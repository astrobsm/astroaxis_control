"""Batches, quarantine and recall.

Reads are open to any authenticated user, because a picker needs to see which
batch to take. Anything that changes a batch's status is administrator-only: a
quarantine decision stops goods leaving the building and a recall commits the
company to chasing what already left.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_admin, require_authenticated_user
from app.db import get_session
from app.models import User
from app.services import batches as svc
from app.services import inventory as inv

router = APIRouter(prefix="/api/batches", tags=["Batches & traceability"])


def _rows(items) -> list[dict]:
    out = []
    for r in items:
        d = dict(r)
        out.append({k: (str(v) if isinstance(v, Decimal) else v)
                    for k, v in d.items()})
    return out


class BatchIn(BaseModel):
    product_id: UUID
    batch_number: str = Field(..., min_length=2, max_length=64)
    expiry_date: Optional[date] = None
    manufactured_on: Optional[date] = None
    origin: str = "PRODUCTION"
    origin_reference: Optional[str] = None
    supplier_name: Optional[str] = None
    quantity_produced: Optional[float] = None
    notes: Optional[str] = None


class StatusIn(BaseModel):
    status: str
    reason: str = Field(..., min_length=3)
    evidence_document_id: Optional[UUID] = None


class ReceiptIn(BaseModel):
    warehouse_id: UUID
    quantity: float = Field(..., gt=0)
    unit_cost: Optional[float] = None
    reference: Optional[str] = None
    notes: Optional[str] = None


@router.get("")
async def list_batches(
    product_id: Optional[UUID] = None,
    status: Optional[str] = None,
    include_empty: bool = False,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    rows = await svc.list_batches(
        session, product_id=product_id, status=status,
        include_empty=include_empty)
    return {"batches": _rows(rows)}


@router.post("", status_code=201)
async def create_batch(
    body: BatchIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Register a batch. This puts no stock anywhere -- receive it separately."""
    result = await svc.create_batch(
        session, product_id=body.product_id, batch_number=body.batch_number,
        expiry_date=body.expiry_date, manufactured_on=body.manufactured_on,
        origin=body.origin, origin_reference=body.origin_reference,
        supplier_name=body.supplier_name,
        quantity_produced=body.quantity_produced, notes=body.notes, actor=user)
    await session.commit()
    return result


@router.get("/expiring")
async def expiring(
    within_days: int = Query(90, ge=1, le=730),
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """What is about to expire, and what already has -- reported separately."""
    return await svc.expiring_batches(session, within_days=within_days)


@router.get("/traceability")
async def traceability(
    product_id: Optional[UUID] = None,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """How much stock can be traced, in quantities rather than a percentage.

    Read `untraceable_quantity`. It is stock that moved before batch recording
    began; nothing has invented a batch number for it, and a recall cannot
    reach it.
    """
    return await svc.traceability_report(session, product_id=product_id)


@router.get("/available")
async def available(
    product_id: UUID,
    warehouse_id: UUID,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """What can be picked here, soonest expiry first."""
    rows = await svc.available_batches(
        session, product_id=product_id, warehouse_id=warehouse_id)
    return {"batches": _rows(rows)}


@router.get("/{batch_id}")
async def batch_detail(
    batch_id: UUID,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    return await svc.batch_detail(session, batch_id)


@router.post("/{batch_id}/status")
async def set_status(
    batch_id: UUID,
    body: StatusIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Quarantine, release, recall or withdraw a batch.

    Recalling returns the trace with the response: the point of a recall is the
    list of who has the goods, not the flag on the record.
    """
    result = await svc.set_status(
        session, batch_id=batch_id, status=body.status, reason=body.reason,
        evidence_document_id=body.evidence_document_id, actor=user)
    await session.commit()
    return result


@router.get("/{batch_id}/trace")
async def trace(
    batch_id: UUID,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Who holds this batch, and who was already sent it.

    Two lists, because they need two different actions: stock still held can be
    stopped, stock despatched has to be chased.
    """
    return await svc.recall_trace(session, batch_id=batch_id)


@router.post("/{batch_id}/receipts", status_code=201)
async def receive_into_stock(
    batch_id: UUID,
    body: ReceiptIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Book batched goods into a warehouse.

    Goes through inventory.apply_stock_movement like every other stock change,
    so the balance, the ledger row and the batch attribution are written
    together or not at all.
    """
    batch = (await session.execute(
        text("SELECT product_id, batch_number FROM product_batches WHERE id = :b"),
        {"b": str(batch_id)})).mappings().first()
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found.")

    movement_id = await inv.apply_stock_movement(
        session, warehouse_id=body.warehouse_id, movement_type="IN",
        quantity=body.quantity, product_id=batch["product_id"],
        reference=body.reference or f"BATCH {batch['batch_number']}",
        notes=body.notes, created_by=user.id, unit_cost=body.unit_cost,
        batch_id=batch_id)
    await session.commit()
    return {"movement_id": str(movement_id),
            "batch_number": batch["batch_number"],
            "on_hand": str(await svc.batch_balance(
                session, batch_id=batch_id, warehouse_id=body.warehouse_id))}
