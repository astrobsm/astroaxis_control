"""Staff birthdays and work anniversaries.

Separate from everything customer-facing, and admin-only throughout: this is
HR data about named employees, including who has agreed to what.
"""
from __future__ import annotations

from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.api.auth import require_admin
from app.models import User
from app.services import staff_engagement as svc

router = APIRouter(prefix='/api/staff-engagement', tags=['Staff engagement'])


class ConsentIn(BaseModel):
    birthday_messages: Optional[bool] = None
    anniversary_messages: Optional[bool] = None
    visibility: Optional[str] = None
    preferred_name: Optional[str] = None
    preferred_channel: Optional[str] = None
    source: str = 'HR_ENTERED'


@router.get('/calendar')
async def engagement_calendar(
    days: int = Query(30, ge=0, le=365),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_admin),
):
    """Birthdays and work anniversaries coming up.

    Only people who agreed to be listed are named, and no year is shown.
    """
    return await svc.calendar(session, days=days, for_display=True)


@router.post('/prepare')
async def prepare_today(
    on: Optional[date] = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_admin),
):
    """Queue today's greetings. Safe to run repeatedly."""
    result = await svc.prepare(session, on=on, actor=user)
    await session.commit()
    return result


@router.post('/{staff_id}/consent')
async def set_consent(
    staff_id: UUID,
    body: ConsentIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_admin),
):
    """Record what this member of staff has agreed to."""
    result = await svc.set_consent(
        session, staff_id=staff_id,
        birthday_messages=body.birthday_messages,
        anniversary_messages=body.anniversary_messages,
        visibility=body.visibility, preferred_name=body.preferred_name,
        preferred_channel=body.preferred_channel, source=body.source,
        actor=user)
    await session.commit()
    return result


@router.get('/{staff_id}/history')
async def staff_history(
    staff_id: UUID,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_admin),
):
    return await svc.history(session, staff_id=staff_id)
