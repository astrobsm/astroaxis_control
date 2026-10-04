"""Everything that has ever happened with one customer.

Behind require_distribution_access: this is the whole commercial relationship
with a named customer in one response -- what they bought, what they owe, what
they were told and what they asked us to stop sending.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.api.auth import require_distribution_access
from app.models import User
from app.services import customer_timeline as svc

router = APIRouter(prefix='/api/customers', tags=['Customer timeline'])


@router.get('/{customer_id}/timeline')
async def timeline(
    customer_id: UUID,
    limit: int = Query(200, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_distribution_access),
):
    """Orders, invoices, payments, quotations, deliveries, calls, visits,
    messages and consent changes, in order. Nothing is stored."""
    return await svc.timeline(session, customer_id=customer_id, limit=limit)


@router.get('/{customer_id}/summary')
async def summary(
    customer_id: UUID,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_distribution_access),
):
    """The figures somebody wants before picking up the phone."""
    return await svc.summary(session, customer_id=customer_id)
