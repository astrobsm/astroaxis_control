"""The attention list, and the jobs that record what it held.

Nothing here sends anything. See app/services/inbox.py: push subscriptions live
in a file destroyed on every deploy, are not tied to users, and the only send
path broadcasts to everyone -- so a message about one distributor's performance
would reach whoever happened to be subscribed. Surfacing in the app is honest.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_admin, require_authenticated_user
from app.db import get_session
from app.models import User
from app.services import inbox as svc
from app.services import jobs as jobsvc

router = APIRouter(prefix="/api/inbox", tags=["Attention list & jobs"])


class SnoozeIn(BaseModel):
    item_key: str = Field(..., max_length=160)
    days: int = Field(7, ge=1, le=90)
    reason: Optional[str] = None


class RunJobIn(BaseModel):
    on: Optional[date] = None


@router.get("")
async def attention(
    within_days: int = Query(60, ge=1, le=365),
    severity: Optional[str] = None,
    include_snoozed: bool = False,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Everything needing attention, computed live.

    There is no notifications table behind this. Every item is a query against
    the data that already holds the truth, so an item disappears the moment the
    underlying problem is fixed and none of it can go stale.
    """
    return await svc.attention_items(
        session, user=user, within_days=within_days,
        include_snoozed=include_snoozed, severity=severity)


@router.post("/snooze")
async def snooze(
    body: SnoozeIn,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Hide one item from yourself for a bounded time.

    There is no permanent dismissal, and CRITICAL items cannot be snoozed at
    all -- recalled stock in the field is not something one person decides
    nobody else needs to see.
    """
    result = await svc.snooze(
        session, item_key=body.item_key, days=body.days, reason=body.reason,
        user=user)
    await session.commit()
    return result


@router.post("/unsnooze")
async def unsnooze(
    body: SnoozeIn,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    result = await svc.unsnooze(session, item_key=body.item_key, user=user)
    await session.commit()
    return result


@router.get("/jobs")
async def list_jobs(
    user: User = Depends(require_authenticated_user),
):
    """What can be run, and what each one does."""
    return {
        "jobs": [
            {"name": name, "description": (spec["doc"] or "").strip()}
            for name, spec in sorted(jobsvc.JOBS.items())
        ],
        "note": ("Jobs find things and record what they found. None of them "
                 "send anything to anybody."),
    }


@router.get("/jobs/history")
async def job_history(
    name: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    rows = await jobsvc.job_history(session, name=name, limit=limit)
    return {"runs": rows}


@router.post("/jobs/{name}/run")
async def run_job(
    name: str,
    body: RunJobIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Run a job for its period, or return what the last run found.

    Safe to call repeatedly: a job runs once per period by database
    constraint, so a retry or a double-registered schedule cannot repeat work.
    """
    result = await jobsvc.run_job(session, name=name, on=body.on, actor=user)
    await session.commit()
    return result
