"""Scheduled jobs that cannot run twice for the same period.

HOW IDEMPOTENCY IS GUARANTEED
=============================
`scheduled_job_runs` carries UNIQUE (job_name, run_key). `run_job` claims the
key by INSERTing it BEFORE doing any work: if the row already exists the claim
fails and the job returns the previous result instead of repeating.

Claiming first matters. Checking for an existing run and then inserting leaves a
window in which two workers both see nothing and both proceed -- which is
precisely the situation a double-registered cron or a retry loop creates. The
unique index closes it, because one of the two INSERTs loses.

WHAT A JOB IS ALLOWED TO DO
===========================
Find things and record what it found. Nothing here sends anything to anybody:
see migration e0123456789d on why push notifications would reach the wrong
people. A "digest" is a stored summary somebody can open, not a message anybody
received, and `summary` is named findings rather than deliveries so nobody reads
it as proof that a person was told.

The one job that WRITES is the month-end snapshot, and it writes only
`performance_periods` rows, which are themselves immutable.
"""
from __future__ import annotations

import json
import traceback
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Optional

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import inbox
from app.services import performance as perf

JOBS: dict = {}


def job(name: str, key_fn: Callable[[date], str]):
    """Register a job and how it names the period it covers."""
    def register(fn):
        JOBS[name] = {"fn": fn, "key_fn": key_fn, "doc": fn.__doc__ or ""}
        return fn
    return register


def daily_key(on: date) -> str:
    return on.isoformat()


def weekly_key(on: date) -> str:
    year, week, _ = on.isocalendar()
    return f"{year}-W{week:02d}"


def monthly_key(on: date) -> str:
    """The month that has just FINISHED, not the one in progress."""
    first = on.replace(day=1)
    previous = first - timedelta(days=1)
    return f"{previous.year}-{previous.month:02d}"


def _json(value) -> str:
    def default(o):
        if isinstance(o, (Decimal,)):
            return str(o)
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        return str(o)
    return json.dumps(value, default=default)


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------

async def run_job(
    session: AsyncSession, *, name: str, on: Optional[date] = None, actor=None,
    force_key: Optional[str] = None,
) -> dict:
    """Run a job once for its period, or return what the last run found."""
    registered = JOBS.get(name)
    if registered is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown job {name!r}. Known: {', '.join(sorted(JOBS))}.")

    on = on or date.today()
    run_key = force_key or registered["key_fn"](on)

    existing = (await session.execute(
        text("""SELECT id, status, started_at, finished_at, summary, error
                  FROM scheduled_job_runs
                 WHERE job_name = :n AND run_key = :k"""),
        {"n": name, "k": run_key})).mappings().first()
    if existing:
        return {
            "job": name, "run_key": run_key, "ran": False,
            "status": existing["status"],
            "previous_run": existing["started_at"].isoformat(),
            "summary": existing["summary"],
            "error": existing["error"],
            "note": (f"{name} has already run for {run_key}. Jobs are "
                     f"idempotent per period, so this returned the earlier "
                     f"result instead of repeating the work."),
        }

    # Claim the key BEFORE doing any work. Two workers racing here means one
    # INSERT loses on the unique index, which is the point.
    try:
        async with session.begin_nested():
            await session.execute(
                text("""INSERT INTO scheduled_job_runs
                            (id, job_name, run_key, status, triggered_by)
                        VALUES (gen_random_uuid(), :n, :k, 'RUNNING', :by)"""),
                {"n": name, "k": run_key,
                 "by": str(actor.id) if actor else None},
            )
    except IntegrityError:
        return {
            "job": name, "run_key": run_key, "ran": False,
            "status": "RUNNING",
            "note": (f"Another worker claimed {name} for {run_key} first. "
                     f"Only one run per period happens, by database "
                     f"constraint."),
        }

    try:
        summary = await registered["fn"](session, on)
    except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
        await session.execute(
            text("""UPDATE scheduled_job_runs
                       SET status = 'FAILED', finished_at = NOW(), error = :e
                     WHERE job_name = :n AND run_key = :k"""),
            {"e": f"{type(exc).__name__}: {exc}\n"
                  f"{traceback.format_exc()[-2000:]}",
             "n": name, "k": run_key},
        )
        await session.commit()
        raise HTTPException(
            status_code=500,
            detail=f"{name} failed for {run_key}: {type(exc).__name__}. The "
                   f"failure is recorded; fix the cause and run it against a "
                   f"new key.") from exc

    await session.execute(
        text("""UPDATE scheduled_job_runs
                   SET status = 'COMPLETED', finished_at = NOW(),
                       summary = CAST(:s AS JSONB)
                 WHERE job_name = :n AND run_key = :k"""),
        {"s": _json(summary), "n": name, "k": run_key},
    )
    return {"job": name, "run_key": run_key, "ran": True,
            "status": "COMPLETED", "summary": summary}


async def job_history(
    session: AsyncSession, *, name: Optional[str] = None, limit: int = 50,
) -> list[dict]:
    clause = "WHERE job_name = :n" if name else ""
    params = {"lim": limit}
    if name:
        params["n"] = name
    rows = (await session.execute(
        text(f"""SELECT r.job_name, r.run_key, r.status, r.started_at,
                        r.finished_at, r.summary, r.error,
                        u.full_name AS triggered_by_name
                   FROM scheduled_job_runs r
                   LEFT JOIN users u ON u.id = r.triggered_by
                   {clause}
                  ORDER BY r.started_at DESC LIMIT :lim"""),
        params)).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# The jobs
# ---------------------------------------------------------------------------

@job("attention_digest", weekly_key)
async def attention_digest(session: AsyncSession, on: date) -> dict:
    """Freeze what needed attention this week.

    Records findings. It does not notify anybody -- see the module docstring.
    The value is a weekly record of what the backlog looked like, so a problem
    that has sat unfixed for six weeks is visible as six weeks rather than as
    today's list.
    """
    snapshot = await inbox.attention_items(session, include_snoozed=True)
    return {
        "total": snapshot["total"],
        "counts": snapshot["counts"],
        "by_category": _count_by(snapshot["items"], "category"),
        "critical": [
            {"key": i["key"], "title": i["title"]}
            for i in snapshot["items"] if i["severity"] == "CRITICAL"
        ],
        "delivered_to": None,
        "delivery_note": ("Nothing was sent. This is a record of what the list "
                          "held, readable in the app."),
    }


@job("document_expiry_sweep", daily_key)
async def document_expiry_sweep(session: AsyncSession, on: date) -> dict:
    """Count documents and batches lapsing, so a trend is visible over time."""
    documents = await inbox._expiring_documents(session, 60)
    batches = await inbox._expiring_batches(session, 60)
    return {
        "documents_expiring_or_expired": len(documents),
        "documents_already_expired": sum(
            1 for d in documents if d["severity"] == "HIGH"),
        "batches_expiring_or_expired": len(batches),
        "batches_already_expired": sum(
            1 for b in batches if b["severity"] == "HIGH"),
    }


@job("recall_watch", daily_key)
async def recall_watch(session: AsyncSession, on: date) -> dict:
    """Recalled stock still in the field, counted every day it is still there.

    A recall that is not progressing looks identical to one that is, unless
    somebody is counting. This makes the day count visible.
    """
    items = await inbox._recalled_stock_still_out(session)
    return {
        "recalled_batches_with_stock": len(items),
        "batches": [{"key": i["key"], "title": i["title"]} for i in items],
    }


@job("month_end_performance", monthly_key)
async def month_end_performance(session: AsyncSession, on: date) -> dict:
    """Freeze last month for every active distributor.

    The one job that writes. `performance_periods` rows are immutable, and
    snapshot_period refuses a part-month, so running this on the 1st freezes
    the month that has just ended and nothing else.
    """
    first_of_this = on.replace(day=1)
    last_month_end = first_of_this - timedelta(days=1)
    year, month = last_month_end.year, last_month_end.month

    distributors = (await session.execute(
        text("""SELECT id, distributor_code, legal_name FROM distributors
                 WHERE status = 'ACTIVE'"""))).mappings().all()

    frozen, skipped = [], []
    for d in distributors:
        try:
            async with session.begin_nested():
                result = await perf.snapshot_period(
                    session, distributor_id=d["id"], year=year, month=month,
                    note=f"Automatic month-end close for {year}-{month:02d}")
            frozen.append({"distributor": d["distributor_code"],
                           "band": result["band"],
                           "verified": result["verified_amount"]})
        except HTTPException as exc:
            # Already frozen, or nothing to freeze. Recorded rather than
            # swallowed, so a distributor silently missing from a month-end is
            # visible instead of invisible.
            skipped.append({"distributor": d["distributor_code"],
                            "reason": exc.detail})

    return {"period": f"{year}-{month:02d}", "frozen": len(frozen),
            "skipped": len(skipped), "details": frozen,
            "skipped_details": skipped}


@job("review_trigger_sweep", weekly_key)
async def review_trigger_sweep(session: AsyncSession, on: date) -> dict:
    """Which distributors have earned a performance review.

    Reports; it does not open them. Opening a review is a decision with
    consequences for somebody's livelihood, and a cron job is the wrong thing
    to be taking it.
    """
    distributors = (await session.execute(
        text("""SELECT id, distributor_code, legal_name FROM distributors
                 WHERE status = 'ACTIVE'"""))).mappings().all()

    due = []
    for d in distributors:
        result = await perf.review_due(session, distributor_id=d["id"])
        if result["due"]:
            due.append({
                "distributor": d["distributor_code"],
                "legal_name": d["legal_name"],
                "months_below": result["consecutive_months_below"],
                "worst_pct": result["worst_achievement_pct"],
            })
    return {
        "reviews_due": len(due), "distributors": due,
        "note": ("Reported, not opened. Opening a performance review is a "
                 "decision a person takes, not a scheduled task."),
    }


def _count_by(items: list, field: str) -> dict:
    out: dict = {}
    for item in items:
        out[item[field]] = out.get(item[field], 0) + 1
    return out
