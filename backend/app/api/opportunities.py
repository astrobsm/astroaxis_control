"""The sales opportunity queue.

Behind require_distribution_access, like the rest of the commercial register.
This list states what each customer buys, what they owe, how often they order
and when they last did -- which is the company's commercial position with
every one of its customers in a single screen. Production and warehouse logins
have no reason to read it.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.api.auth import require_distribution_access
from app.models import User
from app.services import opportunities as svc

router = APIRouter(prefix='/api/opportunities', tags=['Opportunities'])


class ActionIn(BaseModel):
    opportunity_key: str = Field(..., min_length=3, max_length=160)
    outcome: str
    note: Optional[str] = None
    snooze_days: Optional[int] = Field(None, ge=1, le=90)


@router.get('')
async def opportunity_queue(
    limit: int = Query(20, ge=1, le=200),
    priority: Optional[str] = Query(
        None, description="COMMERCIAL or RELATIONSHIP"),
    types: Optional[str] = Query(
        None, description="Comma-separated opportunity types"),
    include_snoozed: bool = False,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_distribution_access),
):
    """Who to contact, ranked, with the reason for each.

    Computed live on every call. Nothing is stored, so an item disappears as
    soon as the customer orders or pays.
    """
    wanted = ([t.strip().upper() for t in types.split(',') if t.strip()]
              if types else None)
    return await svc.queue(
        session, limit=limit,
        priority=priority.upper() if priority else None,
        types=wanted, include_snoozed=include_snoozed)


@router.post('/actions', status_code=201)
async def record_action(
    body: ActionIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_distribution_access),
):
    """Record what was done about an opportunity.

    Append-only. A mistake is corrected by recording another action, not by
    editing this one -- the conversion figures are what this feature will be
    judged on, and they have to be auditable by the people they measure.
    """
    result = await svc.record_action(
        session, opportunity_key=body.opportunity_key, outcome=body.outcome,
        note=body.note, snooze_days=body.snooze_days, actor=user)
    await session.commit()
    return result


@router.get('/performance')
async def opportunity_performance(
    days: int = Query(90, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_distribution_access),
):
    """How many opportunities were acted on, and what came of them."""
    return await svc.performance(session, days=days)
