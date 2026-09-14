"""Distributor performance: run-rate, bands, and when to stop guessing.

THE THING THIS MODULE MOST WANTS TO GET RIGHT
=============================================
**A run-rate computed from three days of a month is noise, and a traffic light
built on noise makes people act on it.**

Four days into January a distributor with one slow week looks like a failing
distributor. Extrapolate their sales across 31 days and the projection says they
will miss by 60%. Show that as a red light and somebody phones them about it.
The number was never evidence of anything; it was four days.

So `run_rate` reports a CONFIDENCE derived from how much of the period has
actually elapsed, and below `MIN_CONFIDENT_ELAPSED` it returns the band
`TOO_EARLY` and no projection at all. Not a grey light, not a hedged amber -- it
declines to answer, and says why. A tool that says "I do not know yet" is worth
more than one that guesses confidently.

CALENDAR-AWARE MEANS SELLING DAYS
=================================
Elapsed fraction is computed on selling days rather than calendar days. A month
that is 40% elapsed by the calendar may be 25% elapsed in days anyone could
actually have sold anything, and dividing by the wrong denominator is how a
distributor is marked down for a month with a long public holiday in it.
Weekends are excluded; `NON_SELLING_WEEKDAYS` is the one knob.

This is not a public-holiday calendar. Nigeria's holidays move, some are
declared at short notice, and inventing a fixed list would produce confidently
wrong numbers. Weekends are the part that is certain, and the docstring says so
rather than implying more precision than exists.

ONLY VERIFIED SALES COUNT
=========================
`downstream.sell_through` returns REPORTED and VERIFIED separately and refuses
to add them. This module uses VERIFIED alone for every band, projection and
trigger, and carries the reported figure alongside so the gap stays visible. A
distributor claiming three times what they can evidence is a finding; folding
the claim into their achievement would bury it.

THE SCORECARD DOES NOT AVERAGE AWAY A BLOCKER
=============================================
`scorecard` returns performance, compliance and evidence quality as three
readings plus a list of blocking conditions. There is no single number that a
missing agreement or an expired licence can be outvoted by -- the same rule as
distributor eligibility and facility assessment.
"""
from __future__ import annotations

import calendar
import secrets
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import downstream as down
from app.services import geography as geo
from app.services.geography import audit

# Bands, as a fraction of the target in force during the period.
ON_TARGET_AT = Decimal("0.90")
BEHIND_AT = Decimal("0.70")

# Below this fraction of the period elapsed, no projection is offered.
MIN_CONFIDENT_ELAPSED = Decimal("0.25")

# Saturday and Sunday. Monday is 0 in date.weekday().
NON_SELLING_WEEKDAYS = {5, 6}

# Consecutive months below BEHIND_AT that raise a review.
REVIEW_AFTER_MONTHS = 3


def money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"),
                                             rounding=ROUND_HALF_UP)


def _pct(part: Decimal, whole: Decimal) -> Optional[Decimal]:
    if whole <= 0:
        return None
    return (part / whole * Decimal("100")).quantize(Decimal("0.01"),
                                                    rounding=ROUND_HALF_UP)


def month_window(year: int, month: int) -> tuple:
    start = date(year, month, 1)
    return start, date(year, month, calendar.monthrange(year, month)[1])


def selling_days(start: date, end: date) -> int:
    """Days in the window anyone could have sold on.

    Weekends only. See the module docstring on why this is not a holiday
    calendar: Nigeria's public holidays move and some are declared days ahead,
    so a hard-coded list would be confidently wrong rather than approximately
    right.
    """
    days, cursor = 0, start
    while cursor <= end:
        if cursor.weekday() not in NON_SELLING_WEEKDAYS:
            days += 1
        cursor += timedelta(days=1)
    return days


def band_for(achievement: Optional[Decimal]) -> str:
    if achievement is None:
        return "NO_TARGET"
    fraction = achievement / Decimal("100")
    if fraction >= ON_TARGET_AT:
        return "ON_TARGET"
    if fraction >= BEHIND_AT:
        return "BEHIND"
    return "WELL_BEHIND"


# ---------------------------------------------------------------------------
# One period
# ---------------------------------------------------------------------------

async def _territory_for(
    session: AsyncSession, distributor_id: UUID, on: date,
) -> Optional[UUID]:
    """The territory this distributor held on a given date.

    Held THEN, not now. Measuring a past month against a territory they were
    assigned last week would compare their sales to somebody else's target.
    """
    row = (await session.execute(
        text("""SELECT territory_id FROM territory_assignments
                 WHERE distributor_id = :d
                   AND assigned_from <= :on
                   AND (assigned_to IS NULL OR assigned_to >= :on)
                 ORDER BY assigned_from DESC LIMIT 1"""),
        {"d": str(distributor_id), "on": on})).first()
    return row.territory_id if row else None


async def period_performance(
    session: AsyncSession, *, distributor_id: UUID, year: int, month: int,
) -> dict:
    """What a distributor achieved in one month, against that month's target."""
    start, end = month_window(year, month)

    territory_id = await _territory_for(session, distributor_id, end)
    target = (await geo.target_on(session, territory_id, end)
              if territory_id else Decimal("0.00"))

    sales = await down.sell_through(
        session, distributor_id=distributor_id, since=start, until=end)
    verified = money(sales["verified_amount"])
    reported = money(sales["reported_amount"])

    achievement = _pct(verified, target)
    today = date.today()

    return {
        "period_start": str(start),
        "period_end": str(end),
        "complete": end < today,
        "territory_id": str(territory_id) if territory_id else None,
        # The target in force THEN. territory_targets is versioned, so this is
        # a historical fact rather than a current setting.
        "target_amount": str(target),
        "verified_amount": str(verified),
        "reported_amount": str(reported),
        "disputed_amount": sales["disputed_amount"],
        "unverified_gap": str(money(reported)),
        "achievement_pct": str(achievement) if achievement is not None else None,
        "band": band_for(achievement),
        "counts_toward_band": "verified_amount",
        "note": (
            "No target was in force for this territory in this period, so "
            "there is nothing to measure against."
            if target <= 0 else
            f"Measured against the target in force during {start:%B %Y}, not "
            f"against today's."),
    }


async def run_rate(
    session: AsyncSession, *, distributor_id: UUID, on: Optional[date] = None,
) -> dict:
    """Where the current month is heading -- or a refusal to guess.

    Below MIN_CONFIDENT_ELAPSED of the period's selling days, this returns band
    TOO_EARLY and no projection. See the module docstring: four days of sales
    extrapolated across a month is not a forecast, and colouring it red gets
    somebody phoned about nothing.
    """
    today = on or date.today()
    start, end = month_window(today.year, today.month)

    total_days = selling_days(start, end)
    elapsed_days = selling_days(start, min(today, end))
    elapsed = (Decimal(elapsed_days) / Decimal(total_days)
               if total_days else Decimal("0"))

    territory_id = await _territory_for(session, distributor_id, today)
    target = (await geo.target_on(session, territory_id, today)
              if territory_id else Decimal("0.00"))

    sales = await down.sell_through(
        session, distributor_id=distributor_id, since=start, until=today)
    verified = money(sales["verified_amount"])

    base = {
        "period_start": str(start),
        "period_end": str(end),
        "as_at": str(today),
        "selling_days_total": total_days,
        "selling_days_elapsed": elapsed_days,
        "elapsed_fraction": str(elapsed.quantize(Decimal("0.01"))),
        "target_amount": str(target),
        "verified_to_date": str(verified),
        "reported_to_date": sales["reported_amount"],
        "counts_toward_band": "verified_to_date",
    }

    if elapsed < MIN_CONFIDENT_ELAPSED:
        return {
            **base,
            "band": "TOO_EARLY",
            "projection": None,
            "projected_achievement_pct": None,
            "confidence": "NONE",
            "note": (
                f"Only {elapsed_days} of {total_days} selling days have passed. "
                f"A projection from this little of the month is arithmetic, not "
                f"a forecast, so none is offered. The figures above are what "
                f"has actually been verified so far."),
        }

    if target <= 0:
        return {
            **base, "band": "NO_TARGET", "projection": None,
            "projected_achievement_pct": None, "confidence": "NONE",
            "note": ("No target is in force for this territory, so there is "
                     "nothing to project against."),
        }

    projection = money(verified / elapsed)
    projected_pct = _pct(projection, target)

    # Confidence follows elapsed time and nothing else. It is a statement about
    # how much month there is, not about how good the distributor is.
    if elapsed >= Decimal("0.75"):
        confidence = "HIGH"
    elif elapsed >= Decimal("0.5"):
        confidence = "MODERATE"
    else:
        confidence = "LOW"

    return {
        **base,
        "band": band_for(projected_pct),
        "projection": str(projection),
        "projected_achievement_pct": str(projected_pct),
        "achievement_to_date_pct": str(_pct(verified, target) or 0),
        "confidence": confidence,
        "note": (
            f"Projected from {elapsed_days} of {total_days} selling days. "
            f"Weekends are excluded; public holidays are not, because "
            f"Nigeria's move and a fixed list would be confidently wrong."
            + ("" if confidence == "HIGH" else
               " Treat this as an early indication rather than a result.")),
    }


async def history(
    session: AsyncSession, *, distributor_id: UUID, months: int = 12,
) -> list[dict]:
    """The last N complete months, each against its own target."""
    today = date.today()
    out = []
    year, month = today.year, today.month
    for _ in range(months):
        month -= 1
        if month == 0:
            year, month = year - 1, 12
        out.append(await period_performance(
            session, distributor_id=distributor_id, year=year, month=month))
    return out


# ---------------------------------------------------------------------------
# Freezing a period
# ---------------------------------------------------------------------------

async def snapshot_period(
    session: AsyncSession, *, distributor_id: UUID, year: int, month: int,
    note: Optional[str] = None, actor=None,
) -> dict:
    """Freeze one month's figures as they stand now.

    Refuses to snapshot a month that has not finished: a partial month frozen
    as if complete is the most misleading row this table could hold.
    """
    start, end = month_window(year, month)
    if end >= date.today():
        raise HTTPException(
            status_code=400,
            detail=(f"{start:%B %Y} has not finished. A part-month frozen as "
                    f"though it were complete is worse than no snapshot."))

    existing = (await session.execute(
        text("""SELECT id, computed_at FROM performance_periods
                 WHERE distributor_id = :d AND period_start = :s"""),
        {"d": str(distributor_id), "s": start})).mappings().first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=(f"{start:%B %Y} was already frozen on "
                    f"{existing['computed_at']:%d %b %Y}. Snapshots are "
                    f"immutable; read the live figures for today's position."))

    figures = await period_performance(
        session, distributor_id=distributor_id, year=year, month=month)

    period_id = uuid4()
    await session.execute(
        text("""
            INSERT INTO performance_periods
                (id, distributor_id, territory_id, period_start, period_end,
                 target_amount, verified_amount, reported_amount,
                 disputed_amount, achievement_pct, band, computed_by, note)
            VALUES (:id, :d, CAST(:t AS uuid), :s, :e, :target, :verified,
                    :reported, :disputed, :pct, :band, :by, :note)
        """),
        {"id": str(period_id), "d": str(distributor_id),
         "t": figures["territory_id"], "s": start, "e": end,
         "target": figures["target_amount"],
         "verified": figures["verified_amount"],
         "reported": figures["reported_amount"],
         "disputed": figures["disputed_amount"],
         "pct": figures["achievement_pct"], "band": figures["band"],
         "by": str(actor.id) if actor else None, "note": note},
    )
    return {"id": str(period_id), **figures, "frozen": True}


async def snapshots(
    session: AsyncSession, *, distributor_id: UUID, limit: int = 24,
) -> list[dict]:
    rows = (await session.execute(
        text("""SELECT p.*, t.code AS territory_code,
                       u.full_name AS computed_by_name
                  FROM performance_periods p
                  LEFT JOIN territories t ON t.id = p.territory_id
                  LEFT JOIN users u ON u.id = p.computed_by
                 WHERE p.distributor_id = :d
                 ORDER BY p.period_start DESC LIMIT :lim"""),
        {"d": str(distributor_id), "lim": limit})).mappings().all()
    out = []
    for r in rows:
        row = dict(r)
        for key in ("id", "distributor_id", "territory_id", "computed_by"):
            if row.get(key) is not None:
                row[key] = str(row[key])
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------

async def review_due(
    session: AsyncSession, *, distributor_id: UUID,
) -> dict:
    """Has this distributor been below the threshold for long enough to review?

    Counts CONSECUTIVE complete months below BEHIND_AT, most recent first, and
    stops at the first month that was not. One bad month in a good year is not
    a pattern; three in a row is a conversation worth having.

    Months with no target in force break the run rather than counting as
    failures -- a distributor cannot miss a target that was never set, and
    treating that as a strike would punish them for an administrative gap.
    """
    periods = await history(session, distributor_id=distributor_id,
                            months=REVIEW_AFTER_MONTHS + 3)

    run, considered, worst = 0, [], None
    for period in periods:
        if not period["complete"]:
            continue
        if period["band"] == "NO_TARGET":
            break
        pct = Decimal(period["achievement_pct"] or "0")
        if pct / Decimal("100") >= BEHIND_AT:
            break
        run += 1
        considered.append(period)
        worst = pct if worst is None or pct < worst else worst

    open_review = (await session.execute(
        text("""SELECT review_reference, status FROM
                     distributor_performance_reviews
                 WHERE distributor_id = :d AND status IN ('OPEN','IN_PROGRESS')
                 LIMIT 1"""),
        {"d": str(distributor_id)})).mappings().first()

    return {
        "consecutive_months_below": run,
        "threshold_pct": str(BEHIND_AT * 100),
        "months_before_review": REVIEW_AFTER_MONTHS,
        "due": run >= REVIEW_AFTER_MONTHS and open_review is None,
        "already_open": (open_review["review_reference"] if open_review
                         else None),
        "periods": considered,
        "worst_achievement_pct": str(worst) if worst is not None else None,
    }


async def open_review(
    session: AsyncSession, *, distributor_id: UUID, reason: Optional[str] = None,
    actor=None,
) -> dict:
    """Start a performance review, recording the figures that triggered it."""
    due = await review_due(session, distributor_id=distributor_id)
    if due["already_open"]:
        raise HTTPException(
            status_code=409,
            detail=(f"Review {due['already_open']} is already open for this "
                    f"distributor. Close it before opening another."))

    territory_id = await _territory_for(session, distributor_id, date.today())

    if reason:
        trigger = reason.strip()
    elif due["consecutive_months_below"]:
        months = ", ".join(
            f"{p['period_start'][:7]} at {p['achievement_pct']}%"
            for p in due["periods"])
        trigger = (f"{due['consecutive_months_below']} consecutive month(s) "
                   f"below {due['threshold_pct']}% of target: {months}.")
    else:
        raise HTTPException(
            status_code=400,
            detail=("Nothing triggered a review and no reason was given. Say "
                    "why one is being opened."))

    review_id = uuid4()
    reference = f"PR-{date.today():%Y%m}-{secrets.token_hex(3).upper()}"
    await session.execute(
        text("""
            INSERT INTO distributor_performance_reviews
                (id, review_reference, distributor_id, territory_id,
                 trigger_reason, periods_considered, worst_achievement_pct,
                 opened_by, status)
            VALUES (:id, :ref, :d, CAST(:t AS uuid), :reason, :n,
                    CAST(:worst AS numeric), :by, 'OPEN')
        """),
        {"id": str(review_id), "ref": reference, "d": str(distributor_id),
         "t": str(territory_id) if territory_id else None, "reason": trigger,
         "n": len(due["periods"]), "worst": due["worst_achievement_pct"],
         "by": str(actor.id) if actor else None},
    )
    await audit(session, event_type="PERFORMANCE_REVIEW_OPENED",
                entity_type="distributor_performance_review",
                entity_id=review_id, distributor_id=distributor_id,
                territory_id=territory_id, actor=actor, reason=trigger,
                new_value={"reference": reference,
                           "months_below": due["consecutive_months_below"]})
    return {"id": str(review_id), "review_reference": reference,
            "status": "OPEN", "trigger_reason": trigger}


async def close_review(
    session: AsyncSession, *, review_id: UUID, outcome: str, note: str,
    actor=None,
) -> dict:
    """Close a review with a decision.

    TARGET_RESET exists because the honest outcome is sometimes that the target
    was wrong. A review process whose only outcomes blame the distributor will
    always find the distributor at fault.
    """
    if outcome not in ("SUPPORTED", "TARGET_RESET", "WARNED", "TERMINATED"):
        raise HTTPException(status_code=400, detail="Unknown outcome.")
    if not note or len(note.strip()) < 3:
        raise HTTPException(
            status_code=400,
            detail=("Record what was decided and why. The distributor is "
                    "entitled to know on what basis."))

    row = (await session.execute(
        text("""SELECT id, review_reference, status, distributor_id
                  FROM distributor_performance_reviews
                 WHERE id = :r FOR UPDATE"""),
        {"r": str(review_id)})).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Review not found.")
    if row["status"] == "CLOSED":
        raise HTTPException(
            status_code=400,
            detail=f"{row['review_reference']} is already closed.")

    await session.execute(
        text("""UPDATE distributor_performance_reviews
                   SET status = 'CLOSED', outcome = :o, outcome_note = :n,
                       closed_on = CURRENT_DATE, closed_by = :by
                 WHERE id = :r"""),
        {"o": outcome, "n": note.strip(),
         "by": str(actor.id) if actor else None, "r": str(review_id)})
    await audit(session, event_type=f"PERFORMANCE_REVIEW_{outcome}",
                entity_type="distributor_performance_review",
                entity_id=review_id, distributor_id=row["distributor_id"],
                actor=actor, reason=note.strip(),
                old_value={"status": row["status"]},
                new_value={"status": "CLOSED", "outcome": outcome})
    return {"id": str(review_id), "status": "CLOSED", "outcome": outcome}


async def list_reviews(
    session: AsyncSession, *, distributor_id: Optional[UUID] = None,
    open_only: bool = False,
) -> list[dict]:
    clauses, params = ["1 = 1"], {}
    if distributor_id:
        clauses.append("r.distributor_id = :d")
        params["d"] = str(distributor_id)
    if open_only:
        clauses.append("r.status IN ('OPEN','IN_PROGRESS')")

    rows = (await session.execute(
        text(f"""SELECT r.id, r.review_reference, r.trigger_reason, r.status,
                        r.outcome, r.outcome_note, r.opened_on, r.closed_on,
                        r.periods_considered, r.worst_achievement_pct,
                        d.distributor_code, d.legal_name,
                        t.code AS territory,
                        o.full_name AS opened_by_name,
                        c.full_name AS closed_by_name
                   FROM distributor_performance_reviews r
                   JOIN distributors d ON d.id = r.distributor_id
                   LEFT JOIN territories t ON t.id = r.territory_id
                   LEFT JOIN users o ON o.id = r.opened_by
                   LEFT JOIN users c ON c.id = r.closed_by
                  WHERE {' AND '.join(clauses)}
                  ORDER BY r.opened_on DESC"""),
        params)).mappings().all()
    return [dict(r) | {"id": str(r["id"])} for r in rows]


# ---------------------------------------------------------------------------
# Scorecard
# ---------------------------------------------------------------------------

async def scorecard(
    session: AsyncSession, *, distributor_id: UUID,
) -> dict:
    """Three readings and a list of blockers. No single number.

    Performance, compliance and evidence quality measure different things and a
    weighted average of them would let a good sales month outvote an expired
    licence. The same rule governs distributor eligibility and facility
    assessment, and it is the rule the specification is most insistent about.
    """
    last_month = await _last_complete_period(session, distributor_id)
    current = await run_rate(session, distributor_id=distributor_id)
    due = await review_due(session, distributor_id=distributor_id)

    try:
        from app.services import compliance as csvc
        compliance = await csvc.compliance_summary(session, distributor_id)
    except Exception:
        compliance = None

    sales = await down.sell_through(session, distributor_id=distributor_id)
    verified = money(sales["verified_amount"])
    reported = money(sales["reported_amount"])
    evidenced = _pct(verified, verified + reported)

    blocking = []
    if compliance and not compliance["fit_to_trade"]:
        blocking += compliance["blocking_conditions"]
    if due["already_open"]:
        blocking.append(
            f"Performance review {due['already_open']} is open.")
    elif due["due"]:
        blocking.append(
            f"{due['consecutive_months_below']} consecutive months below "
            f"{due['threshold_pct']}% of target; a review is due.")
    if sales["stock_discrepancies"]:
        blocking.append(
            f"{sales['stock_discrepancies']} sale(s) report more sold than was "
            f"shipped.")

    return {
        "performance": {
            "last_complete_month": last_month,
            "current_month": current,
        },
        "compliance": compliance,
        "evidence": {
            "verified_amount": str(verified),
            "unverified_amount": str(reported),
            "share_evidenced_pct": (str(evidenced) if evidenced is not None
                                    else None),
            "note": ("How much of what this distributor claims has actually "
                     "been checked. A low share is not a performance problem "
                     "-- it is a reason to distrust the performance figures."),
        },
        "review": due,
        "blocking_conditions": blocking,
        # Deliberately absent: an overall score. See the module docstring.
        "note": ("Read these three separately. They measure different things, "
                 "and averaging them would let a good sales month outvote an "
                 "expired licence."),
    }


async def _last_complete_period(
    session: AsyncSession, distributor_id: UUID,
) -> dict:
    today = date.today()
    year, month = (today.year, today.month - 1) if today.month > 1 else (
        today.year - 1, 12)
    return await period_performance(
        session, distributor_id=distributor_id, year=year, month=month)


async def leaderboard(
    session: AsyncSession, *, year: int, month: int,
) -> list[dict]:
    """Every active distributor for one month, ranked on verified sales."""
    rows = (await session.execute(
        text("""SELECT id, distributor_code, legal_name FROM distributors
                 WHERE status = 'ACTIVE' ORDER BY legal_name"""),
    )).mappings().all()

    out = []
    for row in rows:
        figures = await period_performance(
            session, distributor_id=row["id"], year=year, month=month)
        out.append({
            "distributor_id": str(row["id"]),
            "distributor_code": row["distributor_code"],
            "legal_name": row["legal_name"],
            **figures,
        })
    out.sort(key=lambda r: Decimal(r["verified_amount"]), reverse=True)
    return out
