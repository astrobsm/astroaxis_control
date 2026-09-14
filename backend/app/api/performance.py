"""Distributor performance: run-rate, periods, reviews, scorecard.

Reads require a distribution role (admin, sales, customer care): a distributor's
performance is commercial information, not something every staff login needs.
Opening and closing a performance review is administrator-only -- it is the
process that can end a distributorship.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_admin, require_distribution_access
from app.db import get_session
from app.models import User
from app.services import performance as svc

router = APIRouter(prefix="/api/performance", tags=["Distributor performance"])


def _rows(items) -> list[dict]:
    out = []
    for r in items:
        d = dict(r)
        out.append({k: (str(v) if isinstance(v, Decimal) else v)
                    for k, v in d.items()})
    return out


class SnapshotIn(BaseModel):
    year: int = Field(..., ge=2000, le=2100)
    month: int = Field(..., ge=1, le=12)
    note: Optional[str] = None


class OpenReviewIn(BaseModel):
    reason: Optional[str] = None


class CloseReviewIn(BaseModel):
    outcome: str
    note: str = Field(..., min_length=3)


@router.get("/{distributor_id}/run-rate")
async def run_rate(
    distributor_id: UUID,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    """Where this month is heading, or a refusal to guess.

    Early in a month this returns band TOO_EARLY and no projection. That is not
    a missing feature: a projection from four days of sales is arithmetic, not
    a forecast, and showing it as a red light gets somebody phoned about noise.
    """
    return await svc.run_rate(session, distributor_id=distributor_id)


@router.get("/{distributor_id}/period")
async def period(
    distributor_id: UUID,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    """One month, measured against the target that was in force THEN."""
    return await svc.period_performance(
        session, distributor_id=distributor_id, year=year, month=month)


@router.get("/{distributor_id}/history")
async def history(
    distributor_id: UUID,
    months: int = Query(12, ge=1, le=36),
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    rows = await svc.history(
        session, distributor_id=distributor_id, months=months)
    return {"periods": rows}


@router.get("/{distributor_id}/scorecard")
async def scorecard(
    distributor_id: UUID,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    """Performance, compliance and evidence quality as three separate readings.

    There is no overall score, deliberately. Averaging them would let a good
    sales month outvote an expired licence.
    """
    return await svc.scorecard(session, distributor_id=distributor_id)


@router.get("/{distributor_id}/snapshots")
async def snapshots(
    distributor_id: UUID,
    limit: int = Query(24, ge=1, le=120),
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    """Frozen month-end figures: what was known when a decision was taken."""
    rows = await svc.snapshots(
        session, distributor_id=distributor_id, limit=limit)
    return {"snapshots": _rows(rows)}


@router.post("/{distributor_id}/snapshots", status_code=201)
async def snapshot(
    distributor_id: UUID,
    body: SnapshotIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Freeze a completed month. Immutable once taken."""
    result = await svc.snapshot_period(
        session, distributor_id=distributor_id, year=body.year,
        month=body.month, note=body.note, actor=user)
    await session.commit()
    return result


@router.get("/{distributor_id}/review-due")
async def review_due(
    distributor_id: UUID,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    """Consecutive months below threshold, and whether that warrants a review.

    A month with no target in force breaks the run rather than counting as a
    failure -- nobody can miss a target that was never set.
    """
    return await svc.review_due(session, distributor_id=distributor_id)


@router.post("/{distributor_id}/reviews", status_code=201)
async def open_review(
    distributor_id: UUID,
    body: OpenReviewIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Open a performance review, recording the figures that triggered it."""
    result = await svc.open_review(
        session, distributor_id=distributor_id, reason=body.reason, actor=user)
    await session.commit()
    return result


@router.post("/reviews/{review_id}/close")
async def close_review(
    review_id: UUID,
    body: CloseReviewIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Close a review with a decision.

    TARGET_RESET is a real outcome: sometimes the honest finding is that the
    target was wrong rather than the distributor.
    """
    result = await svc.close_review(
        session, review_id=review_id, outcome=body.outcome, note=body.note,
        actor=user)
    await session.commit()
    return result


@router.get("/reviews")
async def list_reviews(
    distributor_id: Optional[UUID] = None,
    open_only: bool = False,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    rows = await svc.list_reviews(
        session, distributor_id=distributor_id, open_only=open_only)
    return {"reviews": _rows(rows)}


@router.get("/leaderboard")
async def leaderboard(
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    """Active distributors for one month, ranked on VERIFIED sales only."""
    rows = await svc.leaderboard(session, year=year, month=month)
    return {"month": f"{year:04d}-{month:02d}", "distributors": rows,
            "ranked_on": "verified_amount"}
