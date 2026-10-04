"""Stock reservations.

Reading is open to any authenticated user -- a picker needs to know what is
genuinely free. Reserving and releasing are limited to the distribution roles,
because holding stock back from everybody else is a commercial act.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.api.auth import require_authenticated_user, require_distribution_access
from app.models import User
from app.services import reservations as svc

router = APIRouter(prefix='/api/reservations', tags=['Stock reservations'])


class ReserveIn(BaseModel):
    warehouse_id: UUID
    product_id: Optional[UUID] = None
    raw_material_id: Optional[UUID] = None
    quantity: Decimal = Field(..., gt=0)
    reference_type: str = 'SALES_ORDER'
    reference_id: Optional[UUID] = None
    reference_label: Optional[str] = None
    hold_hours: Optional[int] = Field(None, ge=1, le=720)


class ReleaseIn(BaseModel):
    reason: str = Field(..., min_length=3)


@router.get('/available')
async def available(
    warehouse_id: UUID,
    product_id: Optional[UUID] = None,
    raw_material_id: Optional[UUID] = None,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    """What is there, what is promised, and what may still be sold."""
    result = await svc.available_stock(
        session, warehouse_id=warehouse_id, product_id=product_id,
        raw_material_id=raw_material_id)
    return {k: float(v) for k, v in result.items()}


@router.post('', status_code=201)
async def reserve(
    body: ReserveIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_distribution_access),
):
    """Hold stock. Refuses rather than overselling."""
    result = await svc.reserve(
        session, warehouse_id=body.warehouse_id, product_id=body.product_id,
        raw_material_id=body.raw_material_id, quantity=body.quantity,
        reference_type=body.reference_type, reference_id=body.reference_id,
        reference_label=body.reference_label, hold_hours=body.hold_hours,
        actor=user)
    await session.commit()
    return result


@router.post('/{reservation_id}/release')
async def release(
    reservation_id: UUID,
    body: ReleaseIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_distribution_access),
):
    result = await svc.release(session, reservation_id=reservation_id,
                               reason=body.reason, actor=user)
    await session.commit()
    return result


@router.post('/{reservation_id}/consume')
async def consume(
    reservation_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_distribution_access),
):
    """Ship what was held: the hold ends and the stock actually leaves."""
    result = await svc.consume(session, reservation_id=reservation_id,
                               actor=user)
    await session.commit()
    return result


@router.get('/reconciliation')
async def reconciliation(
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_distribution_access),
):
    """Prove reserved_stock agrees with the reservations behind it.

    Inventory's trial balance. A disagreement means stock is being promised
    twice, or held back from everybody.
    """
    return await svc.reconcile(session)
