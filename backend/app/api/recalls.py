"""Recalls and product complaints.

Raising and closing a recall is administrator-only: it blocks despatch of stock
across the whole company and commits it to chasing what has already gone out.
Recording a complaint or a contact attempt is open to any authenticated user,
because the person who takes the phone call is the person who should write it
down.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_admin, require_authenticated_user
from app.db import get_session
from app.models import User
from app.services import recalls as svc

router = APIRouter(prefix="/api/recalls", tags=["Recalls & complaints"])


def _rows(items) -> list[dict]:
    out = []
    for r in items:
        d = dict(r)
        out.append({k: (str(v) if isinstance(v, Decimal) else v)
                    for k, v in d.items()})
    return out


class RaiseRecallIn(BaseModel):
    batch_id: UUID
    reason: str = Field(..., min_length=10)
    severity: str = "URGENT"


class NotificationIn(BaseModel):
    channel: str
    distributor_id: Optional[UUID] = None
    customer_id: Optional[UUID] = None
    outlet_id: Optional[UUID] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    acknowledged: bool = False
    response: Optional[str] = None
    quantity_reported_held: Optional[float] = None


class CloseRecallIn(BaseModel):
    closure_note: str = Field(..., min_length=10)
    unaccounted_explanation: Optional[str] = None


class ComplaintIn(BaseModel):
    description: str = Field(..., min_length=10)
    product_id: Optional[UUID] = None
    batch_id: Optional[UUID] = None
    distributor_id: Optional[UUID] = None
    outlet_id: Optional[UUID] = None
    received_from: Optional[str] = None
    contact_phone: Optional[str] = None
    severity: str = "MEDIUM"
    potential_adverse_event: bool = False
    received_on: Optional[date] = None


class ComplaintUpdateIn(BaseModel):
    investigation: Optional[str] = None
    outcome: Optional[str] = None
    status: Optional[str] = None
    severity: Optional[str] = None
    potential_adverse_event: Optional[bool] = None
    regulator_notified_on: Optional[date] = None
    regulator_reference: Optional[str] = None


# ---- recalls ----------------------------------------------------------------

@router.get("")
async def list_recalls(
    open_only: bool = False,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    rows = await svc.list_recalls(session, open_only=open_only)
    return {"recalls": _rows(rows)}


@router.post("", status_code=201)
async def raise_recall(
    body: RaiseRecallIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Mark the batch recalled and open the process of getting it back.

    Returns the list of who holds it and who was sent it. That list is the
    recall; the status flag only stops it going out further.
    """
    result = await svc.raise_recall(
        session, batch_id=body.batch_id, reason=body.reason,
        severity=body.severity, actor=user)
    await session.commit()
    return result


@router.get("/{recall_id}/reconciliation")
async def reconciliation(
    recall_id: UUID,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Recovered, destroyed, still held -- and what is unaccounted for.

    Read `unaccounted_quantity`. A recall that appears fully recovered is
    almost always one whose unaccounted units were rounded away.
    """
    return await svc.reconciliation(session, recall_id=recall_id)


@router.get("/{recall_id}/outstanding")
async def outstanding(
    recall_id: UUID,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Who still has to be reached.

    Built from who holds or received the batch, so somebody nobody has tried
    yet appears -- a list of unacknowledged notifications would omit them.
    """
    return await svc.outstanding_contacts(session, recall_id=recall_id)


@router.post("/{recall_id}/notifications", status_code=201)
async def record_notification(
    recall_id: UUID,
    body: NotificationIn,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Record that somebody was contacted, and what they said."""
    result = await svc.record_notification(
        session, recall_id=recall_id, channel=body.channel,
        distributor_id=body.distributor_id, customer_id=body.customer_id,
        outlet_id=body.outlet_id, contact_name=body.contact_name,
        contact_phone=body.contact_phone, acknowledged=body.acknowledged,
        response=body.response,
        quantity_reported_held=body.quantity_reported_held, actor=user)
    await session.commit()
    return result


@router.post("/{recall_id}/close")
async def close_recall(
    recall_id: UUID,
    body: CloseRecallIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Close a recall. Unaccounted units must be explained, not ignored."""
    result = await svc.close_recall(
        session, recall_id=recall_id, closure_note=body.closure_note,
        unaccounted_explanation=body.unaccounted_explanation, actor=user)
    await session.commit()
    return result


# ---- complaints -------------------------------------------------------------

@router.get("/complaints/all")
async def list_complaints(
    open_only: bool = False,
    adverse_only: bool = False,
    batch_id: Optional[UUID] = None,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Complaints, with possible adverse events first.

    `adverse_events_with_no_regulator_record` counts complaints flagged as
    possible adverse events where nobody has recorded notifying a regulator.
    The app notifies nobody; that count is a list of decisions still owed.
    """
    return await svc.list_complaints(
        session, open_only=open_only, adverse_only=adverse_only,
        batch_id=batch_id)


@router.post("/complaints", status_code=201)
async def record_complaint(
    body: ComplaintIn,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Record a complaint as it was made. The description cannot be edited later.

    If `potential_adverse_event` is set, the response carries a warning that
    the application has notified nobody and cannot.
    """
    result = await svc.record_complaint(
        session, description=body.description, product_id=body.product_id,
        batch_id=body.batch_id, distributor_id=body.distributor_id,
        outlet_id=body.outlet_id, received_from=body.received_from,
        contact_phone=body.contact_phone, severity=body.severity,
        potential_adverse_event=body.potential_adverse_event,
        received_on=body.received_on, actor=user)
    await session.commit()
    return result


@router.patch("/complaints/{complaint_id}")
async def update_complaint(
    complaint_id: UUID,
    body: ComplaintUpdateIn,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Record the investigation and outcome alongside the original complaint."""
    result = await svc.update_complaint(
        session, complaint_id=complaint_id, investigation=body.investigation,
        outcome=body.outcome, status=body.status, severity=body.severity,
        potential_adverse_event=body.potential_adverse_event,
        regulator_notified_on=body.regulator_notified_on,
        regulator_reference=body.regulator_reference, actor=user)
    await session.commit()
    return result
