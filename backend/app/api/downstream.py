"""Downstream sales: what a distributor sold onward.

Every response that carries a figure also carries its provenance. There is no
endpoint here that returns a combined reported-plus-verified total, and adding
one would defeat the module: a claim and a confirmed fact are different things,
and only the second should decide anything about a distributor.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile,
)
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_admin, require_distribution_access
from app.db import get_session
from app.models import User
from app.services import downstream as svc

router = APIRouter(prefix="/api/downstream", tags=["Downstream sales"])


def _rows(items) -> list[dict]:
    out = []
    for r in items:
        d = dict(r)
        out.append({k: (str(v) if isinstance(v, Decimal) else v)
                    for k, v in d.items()})
    return out


class MarketerIn(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=160)
    phone: Optional[str] = None
    employee_reference: Optional[str] = None
    territory_id: Optional[UUID] = None


class OutletIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    outlet_type: str = "OTHER"
    phone: Optional[str] = None
    address: Optional[str] = None
    state_id: Optional[UUID] = None
    lga_id: Optional[UUID] = None
    town: Optional[str] = None


class SaleLineIn(BaseModel):
    product_id: UUID
    quantity: float = Field(..., gt=0)
    unit: Optional[str] = None
    unit_price: Optional[float] = None
    batch_id: Optional[UUID] = None


class SaleIn(BaseModel):
    distributor_id: UUID
    sold_on: date
    lines: List[SaleLineIn]
    marketer_id: Optional[UUID] = None
    outlet_id: Optional[UUID] = None
    territory_id: Optional[UUID] = None
    notes: Optional[str] = None


class VerifyIn(BaseModel):
    note: Optional[str] = None


class DisputeIn(BaseModel):
    reason: str = Field(..., min_length=3)


class ReasonIn(BaseModel):
    reason: str = Field(..., min_length=3)


# ---- marketers and outlets --------------------------------------------------

@router.post("/{distributor_id}/marketers", status_code=201)
async def add_marketer(
    distributor_id: UUID,
    body: MarketerIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Record one of the distributor's field staff. Creates no login."""
    result = await svc.add_marketer(
        session, distributor_id=distributor_id, full_name=body.full_name,
        phone=body.phone, employee_reference=body.employee_reference,
        territory_id=body.territory_id, actor=user)
    await session.commit()
    return result


@router.get("/{distributor_id}/marketers")
async def list_marketers(
    distributor_id: UUID,
    include_former: bool = False,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    rows = await svc.list_marketers(
        session, distributor_id=distributor_id, include_former=include_former)
    return {"marketers": _rows(rows)}


@router.post("/marketers/{marketer_id}/end")
async def end_marketer(
    marketer_id: UUID,
    body: ReasonIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await svc.end_marketer(
        session, marketer_id=marketer_id, reason=body.reason, actor=user)
    await session.commit()
    return result


@router.post("/{distributor_id}/outlets", status_code=201)
async def add_outlet(
    distributor_id: UUID,
    body: OutletIn,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    result = await svc.add_outlet(
        session, distributor_id=distributor_id, name=body.name,
        outlet_type=body.outlet_type, phone=body.phone, address=body.address,
        state_id=body.state_id, lga_id=body.lga_id, town=body.town, actor=user)
    await session.commit()
    return result


@router.get("/{distributor_id}/outlets")
async def list_outlets(
    distributor_id: UUID,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    rows = await svc.list_outlets(session, distributor_id=distributor_id)
    return {"outlets": _rows(rows)}


# ---- sales ------------------------------------------------------------------

@router.post("/sales", status_code=201)
async def record_sale(
    body: SaleIn,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    """Record a sale the distributor says they made.

    It is stored as REPORTED and counts toward nothing. Verification is a
    separate act by a different person, against evidence.
    """
    result = await svc.record_sale(
        session, distributor_id=body.distributor_id, sold_on=body.sold_on,
        lines=body.lines, marketer_id=body.marketer_id,
        outlet_id=body.outlet_id, territory_id=body.territory_id,
        notes=body.notes, actor=user)
    await session.commit()
    return result


@router.get("/sales")
async def list_sales(
    distributor_id: Optional[UUID] = None,
    provenance: Optional[str] = None,
    discrepancies_only: bool = False,
    limit: int = Query(200, ge=1, le=1000),
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    rows = await svc.list_sales(
        session, distributor_id=distributor_id, provenance=provenance,
        discrepancies_only=discrepancies_only, limit=limit)
    return {"sales": _rows(rows)}


@router.get("/sales/{sale_id}")
async def sale_detail(
    sale_id: UUID,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    return await svc.sale_detail(session, sale_id)


@router.post("/sales/{sale_id}/evidence", status_code=201)
async def upload_evidence(
    sale_id: UUID,
    file: UploadFile = File(...),
    evidence_type: str = Form("OTHER"),
    note: Optional[str] = Form(None),
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    """Attach the invoice, receipt or photograph a verification rests on."""
    content = await file.read()
    result = await svc.attach_evidence(
        session, sale_id=sale_id, evidence_type=evidence_type,
        filename=file.filename or "evidence", content_type=file.content_type or "",
        content=content, note=note, actor=user)
    await session.commit()
    return result


@router.get("/evidence/{evidence_id}")
async def fetch_evidence(
    evidence_id: UUID,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    row = (await session.execute(
        text("""SELECT filename, content_type, content
                  FROM distributor_sale_evidence WHERE id = :e"""),
        {"e": str(evidence_id)})).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Evidence not found.")
    return Response(
        content=bytes(row["content"]), media_type=row["content_type"],
        headers={"Content-Disposition":
                 f'inline; filename="{row["filename"]}"'})


@router.post("/sales/{sale_id}/verify")
async def verify_sale(
    sale_id: UUID,
    body: VerifyIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Confirm a reported sale.

    Refused without evidence on the record, and refused if you are the person
    who reported it.
    """
    result = await svc.verify_sale(
        session, sale_id=sale_id, note=body.note, actor=user)
    await session.commit()
    return result


@router.post("/sales/{sale_id}/dispute")
async def dispute_sale(
    sale_id: UUID,
    body: DisputeIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Record that a reported sale was checked and found wrong. Never deleted."""
    result = await svc.dispute_sale(
        session, sale_id=sale_id, reason=body.reason, actor=user)
    await session.commit()
    return result


# ---- reporting --------------------------------------------------------------

@router.get("/sell-through")
async def sell_through(
    distributor_id: Optional[UUID] = None,
    territory_id: Optional[UUID] = None,
    since: Optional[date] = None,
    until: Optional[date] = None,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    """Downstream sales with REPORTED and VERIFIED kept apart.

    Read `verified_amount` for any decision. `reported_amount` is what the
    distributor claims and nobody has checked. They are deliberately not added
    together.
    """
    return await svc.sell_through(
        session, distributor_id=distributor_id, territory_id=territory_id,
        since=since, until=until)


@router.get("/{distributor_id}/by-marketer")
async def by_marketer(
    distributor_id: UUID,
    since: Optional[date] = None,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    """Per-marketer totals, provenance kept apart.

    Ranking marketers on unverified self-reported figures rewards optimistic
    paperwork, so the verified column is the one sorted on.
    """
    rows = await svc.by_marketer(
        session, distributor_id=distributor_id, since=since)
    return {"marketers": _rows(rows)}
