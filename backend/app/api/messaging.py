"""Consent, contact details, the outbox and the switches that govern it.

Reading is open to the distribution roles. Changing a switch is admin-only:
turning outbound messaging on is the single most consequential change anybody
can make here, and the change is recorded against a name.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.api.auth import require_admin, require_distribution_access
from app.models import User
from app.services import messaging as svc

router = APIRouter(prefix='/api/messaging', tags=['Messaging'])


class ConsentIn(BaseModel):
    consent: str
    source: str = Field(..., min_length=2, max_length=40)
    channel: Optional[str] = None
    evidence: Optional[str] = None


class SettingIn(BaseModel):
    value: str


class EnqueueIn(BaseModel):
    channel: str
    to_address: str = Field(..., min_length=3, max_length=160)
    body: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=3)
    category: str = 'TRANSACTIONAL'
    customer_id: Optional[UUID] = None
    template_code: Optional[str] = None
    opportunity_key: Optional[str] = None


@router.get('/settings')
async def list_settings(
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_distribution_access),
):
    """Every switch, and who last changed it."""
    return {'settings': await svc.all_settings(session)}


@router.put('/settings/{key}')
async def change_setting(
    key: str,
    body: SettingIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_admin),
):
    """Change a switch. Recorded against your name, permanently."""
    result = await svc.set_setting(session, key=key, value=body.value,
                                   actor=user)
    await session.commit()
    return result


@router.post('/customers/{customer_id}/consent')
async def set_consent(
    customer_id: UUID,
    body: ConsentIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_distribution_access),
):
    """Record what a customer has agreed to, and how we know."""
    result = await svc.set_consent(
        session, customer_id=customer_id, consent=body.consent,
        source=body.source, channel=body.channel, evidence=body.evidence,
        actor=user)
    await session.commit()
    return result


@router.get('/customers/{customer_id}/consent')
async def consent_history(
    customer_id: UUID,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_distribution_access),
):
    return {'history': await svc.consent_history(session,
                                                 customer_id=customer_id)}


@router.post('/outbox', status_code=201)
async def enqueue_message(
    body: EnqueueIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_distribution_access),
):
    """Put a message in the outbox, or record why it was refused.

    Nothing sends it. There is no sender in this system yet, deliberately.
    """
    result = await svc.enqueue(
        session, channel=body.channel, to_address=body.to_address,
        body=body.body, reason=body.reason, category=body.category,
        customer_id=body.customer_id, template_code=body.template_code,
        opportunity_key=body.opportunity_key, actor=user)
    await session.commit()
    return result


@router.get('/outbox')
async def read_outbox(
    status: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_distribution_access),
):
    return await svc.outbox(session, status=status, limit=limit)
