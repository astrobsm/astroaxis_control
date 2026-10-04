"""Quotations.

Behind require_distribution_access: a quotation states what this company will
sell to a named customer and at what price, which is commercial information.
"""
from __future__ import annotations

from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.api.auth import require_distribution_access
from app.models import User
from app.services import quotations as svc

router = APIRouter(prefix='/api/quotations', tags=['Quotations'])


class LineIn(BaseModel):
    product_id: UUID
    unit: str = 'unit'
    quantity: Decimal = Field(..., gt=0)
    # Omit to take the current list price. Supplied only to override it,
    # which is a deliberate act and is why it is not defaulted on the client.
    unit_price: Optional[Decimal] = Field(None, ge=0)
    note: Optional[str] = None


class QuotationIn(BaseModel):
    customer_id: UUID
    items: List[LineIn] = Field(..., min_items=1)
    customer_type: str = 'retail'
    valid_days: Optional[int] = Field(None, ge=1, le=180)
    discount_percent: Decimal = Field(Decimal('0'), ge=0, le=100)
    delivery_charge: Decimal = Field(Decimal('0'), ge=0)
    terms: Optional[str] = None
    notes: Optional[str] = None


class StatusIn(BaseModel):
    status: str
    note: Optional[str] = None


@router.post('', status_code=201)
async def create_quotation(
    body: QuotationIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_distribution_access),
):
    """Prepare a quotation. Prices are taken now and kept."""
    result = await svc.create(
        session, customer_id=body.customer_id,
        items=[i.model_dump() for i in body.items],
        customer_type=body.customer_type, valid_days=body.valid_days,
        discount_percent=body.discount_percent,
        delivery_charge=body.delivery_charge,
        terms=body.terms, notes=body.notes, actor=user)
    await session.commit()
    return result


@router.get('')
async def list_quotations(
    status: Optional[str] = None,
    customer_id: Optional[UUID] = None,
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_distribution_access),
):
    return await svc.listing(session, status=status, customer_id=customer_id,
                             limit=limit)


@router.get('/{quotation_id}')
async def get_quotation(
    quotation_id: UUID,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_distribution_access),
):
    return await svc.get(session, quotation_id=quotation_id)


@router.put('/{quotation_id}/status')
async def change_status(
    quotation_id: UUID,
    body: StatusIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_distribution_access),
):
    """Send, accept, decline or cancel a quotation."""
    result = await svc.set_status(
        session, quotation_id=quotation_id, status=body.status,
        note=body.note, actor=user)
    await session.commit()
    return result


@router.post('/{quotation_id}/convert', status_code=201)
async def convert_quotation(
    quotation_id: UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_distribution_access),
):
    """Turn an accepted quotation into a pending sales order."""
    result = await svc.convert(session, quotation_id=quotation_id, actor=user)
    await session.commit()
    return result
