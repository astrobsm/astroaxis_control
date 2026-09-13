"""Distributor registry: registration, provisioning, documents, lifecycle.

The endpoint that matters most here is `POST /{id}/provision`, and the one that
matters most to get *right* is `POST /` -- because a duplicate distributor
created on day one becomes two accounting identities, two stock locations and a
reconciliation nobody can finish. So creation refuses to proceed past a strong
duplicate match until a person has seen the candidates and said so.

Permissions follow the geography module: reads for any authenticated user,
changes for administrators, with granular distribution roles arriving in the
compliance phase via per-user grants rather than a wider global role enum.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Query, Request, Response,
    UploadFile,
)
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_admin, require_authenticated_user
from app.db import get_session
from app.models import User
from app.services import distributors as svc

router = APIRouter(prefix="/api/distributors", tags=["Distributors"])


def _rows(items) -> list[dict]:
    out = []
    for r in items:
        d = dict(r)
        out.append({k: (str(v) if isinstance(v, Decimal) else v)
                    for k, v in d.items()})
    return out


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class DistributorIn(BaseModel):
    legal_name: str = Field(..., min_length=2, max_length=255)
    entity_type: str = Field("COMPANY")
    trading_name: Optional[str] = None
    phone: Optional[str] = None
    whatsapp: Optional[str] = None
    email: Optional[str] = None
    business_address: Optional[str] = None
    state_id: Optional[UUID] = None
    lga_id: Optional[UUID] = None
    town: Optional[str] = None
    cac_number: Optional[str] = None
    tin: Optional[str] = None
    years_in_operation: Optional[int] = Field(None, ge=0, le=200)
    business_type: Optional[str] = None
    employee_count: Optional[int] = Field(None, ge=0)
    marketer_count: Optional[int] = Field(None, ge=0)
    # Set only after the caller has been shown the duplicate candidates.
    acknowledge_duplicates: bool = False


class StatusIn(BaseModel):
    status: str
    reason: str = Field(..., min_length=3, max_length=500)


class ProvisionIn(BaseModel):
    # Attach an EXISTING customer rather than creating one -- the right answer
    # when the applicant already trades with the company.
    link_customer_id: Optional[UUID] = None


class EligibilityIn(BaseModel):
    """Percentage scores, 0-100, per factor. Missing factors score zero."""
    regulatory_eligibility: Optional[float] = Field(None, ge=0, le=100)
    professional_qualification: Optional[float] = Field(None, ge=0, le=100)
    storage_facility: Optional[float] = Field(None, ge=0, le=100)
    financial_capacity: Optional[float] = Field(None, ge=0, le=100)
    sales_capability: Optional[float] = Field(None, ge=0, le=100)
    geographic_capability: Optional[float] = Field(None, ge=0, le=100)
    distribution_experience: Optional[float] = Field(None, ge=0, le=100)


class QualificationIn(BaseModel):
    qualification_type: str = Field(..., min_length=2, max_length=96)
    holder_name: Optional[str] = None
    institution: Optional[str] = None
    certificate_number: Optional[str] = None
    registration_number: Optional[str] = None
    issuing_authority: Optional[str] = None
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None
    document_id: Optional[UUID] = None


class VerifyIn(BaseModel):
    verified: bool
    note: Optional[str] = None


# ---------------------------------------------------------------------------
# Duplicate check -- callable before anything is created
# ---------------------------------------------------------------------------

@router.get("/check-duplicate")
async def check_duplicate(
    legal_name: str = "",
    phone: str = "",
    email: str = "",
    cac_number: str = "",
    tin: str = "",
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Search existing distributors AND customers for a probable match.

    Called by the registration form as the name is typed, so a duplicate is
    caught before anyone fills in twenty fields. Returns candidates for a human
    to judge; nothing is ever merged automatically.
    """
    candidates = await svc.find_possible_duplicates(
        session, legal_name=legal_name, phone=phone, email=email,
        cac_number=cac_number, tin=tin)
    return {
        "candidates": candidates,
        "strong_match": any(c["strength"] >= 80 for c in candidates),
    }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

@router.get("")
async def list_distributors(
    status: Optional[str] = None,
    state_id: Optional[UUID] = None,
    q: Optional[str] = None,
    limit: int = Query(200, ge=1, le=1000),
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    where = ["1 = 1"]
    params: dict = {"lim": limit}
    if status:
        where.append("d.status = :st")
        params["st"] = status
    if state_id:
        where.append("d.state_id = :s")
        params["s"] = str(state_id)
    if q and q.strip():
        where.append("(d.legal_name ILIKE :q OR d.trading_name ILIKE :q "
                     "OR d.distributor_code ILIKE :q)")
        params["q"] = f"%{q.strip()}%"

    rows = (await session.execute(
        text(f"""
            SELECT d.id, d.distributor_code, d.legal_name, d.trading_name,
                   d.entity_type, d.status, d.tier, d.phone, d.email,
                   d.town, s.name AS state, l.name AS lga,
                   d.customer_id, d.warehouse_id, d.user_id,
                   d.created_at, d.activated_at,
                   (SELECT COUNT(*) FROM territory_assignments ta
                     WHERE ta.distributor_id = d.id AND ta.assigned_to IS NULL
                       AND ta.status = 'ACTIVE') AS territory_count
              FROM distributors d
              LEFT JOIN states s ON s.id = d.state_id
              LEFT JOIN lgas l ON l.id = d.lga_id
             WHERE {' AND '.join(where)}
             ORDER BY d.created_at DESC LIMIT :lim
        """), params,
    )).mappings().all()
    return {"distributors": _rows(rows)}


@router.post("", status_code=201)
async def create_distributor(
    body: DistributorIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Register a distributor in DRAFT.

    Returns 409 with the candidate list if a strong duplicate exists and
    `acknowledge_duplicates` was not set -- so the check cannot be skipped by
    not looking at it.
    """
    if body.entity_type not in ("INDIVIDUAL", "COMPANY"):
        raise HTTPException(
            status_code=400,
            detail="Entity type must be INDIVIDUAL or COMPANY.")
    result = await svc.create_distributor(
        session, legal_name=body.legal_name, entity_type=body.entity_type,
        trading_name=body.trading_name, phone=body.phone,
        whatsapp=body.whatsapp, email=body.email,
        business_address=body.business_address, state_id=body.state_id,
        lga_id=body.lga_id, town=body.town, cac_number=body.cac_number,
        tin=body.tin, years_in_operation=body.years_in_operation,
        business_type=body.business_type, employee_count=body.employee_count,
        marketer_count=body.marketer_count, actor=user,
        acknowledge_duplicates=body.acknowledge_duplicates)
    await session.commit()
    return result


@router.get("/{distributor_id}")
async def distributor_detail(
    distributor_id: UUID,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """The distributor dossier: identity, linkage, territories, compliance.

    `linkage` is the part worth reading. It shows the customer account and
    stock location this distributor resolves to in the existing systems -- the
    proof that this module is an orchestration layer and not a second ledger.
    """
    dist = await svc.get_distributor(session, distributor_id)

    linkage = (await session.execute(
        text("""SELECT c.customer_code, c.name AS customer_name,
                       c.credit_limit,
                       w.code AS warehouse_code, w.name AS warehouse_name,
                       w.is_active AS warehouse_active,
                       u.email AS portal_email, u.is_active AS portal_active
                  FROM distributors d
                  LEFT JOIN customers c ON c.id = d.customer_id
                  LEFT JOIN warehouses w ON w.id = d.warehouse_id
                  LEFT JOIN users u ON u.id = d.user_id
                 WHERE d.id = :d"""),
        {"d": str(distributor_id)},
    )).mappings().first()

    territories = (await session.execute(
        text("""SELECT t.id, t.code, t.name, s.name AS state,
                       ta.assigned_from, ta.id AS assignment_id,
                       (SELECT tt.monthly_target FROM territory_targets tt
                         WHERE tt.territory_id = t.id
                           AND tt.effective_to IS NULL LIMIT 1) AS monthly_target
                  FROM territory_assignments ta
                  JOIN territories t ON t.id = ta.territory_id
                  JOIN states s ON s.id = t.state_id
                 WHERE ta.distributor_id = :d AND ta.assigned_to IS NULL
                   AND ta.status = 'ACTIVE'
                 ORDER BY t.code"""),
        {"d": str(distributor_id)},
    )).mappings().all()

    documents = (await session.execute(
        text("""SELECT id, doc_type, title, filename, byte_size, issue_date,
                       expiry_date, verification_status, created_at,
                       (expiry_date IS NOT NULL
                        AND expiry_date < CURRENT_DATE) AS expired
                  FROM distributor_documents WHERE distributor_id = :d
                 ORDER BY created_at DESC"""),
        {"d": str(distributor_id)},
    )).mappings().all()

    qualifications = (await session.execute(
        text("""SELECT id, qualification_type, holder_name, institution,
                       certificate_number, issuing_authority, issue_date,
                       expiry_date, verification_status,
                       (expiry_date IS NOT NULL
                        AND expiry_date < CURRENT_DATE) AS expired
                  FROM distributor_qualifications WHERE distributor_id = :d
                 ORDER BY created_at DESC"""),
        {"d": str(distributor_id)},
    )).mappings().all()

    # Stock actually held, read live from the existing inventory. Not a copy.
    stock = []
    if dist["warehouse_id"]:
        stock = (await session.execute(
            text("""SELECT p.sku, p.name, sl.current_stock, sl.reserved_stock,
                           sl.updated_at
                      FROM stock_levels sl
                      JOIN products p ON p.id = sl.product_id
                     WHERE sl.warehouse_id = :w AND sl.current_stock <> 0
                     ORDER BY p.name"""),
            {"w": str(dist["warehouse_id"])},
        )).mappings().all()

    return {
        "distributor": {k: (str(v) if isinstance(v, Decimal) else v)
                        for k, v in dist.items()},
        "linkage": _rows([linkage])[0] if linkage else None,
        "territories": _rows(territories),
        "documents": _rows(documents),
        "qualifications": _rows(qualifications),
        "stock": _rows(stock),
        "stock_source": ("Read live from stock_levels for this distributor's "
                         "warehouse. Not a copy, and never a second balance."),
    }


@router.post("/{distributor_id}/provision")
async def provision(
    distributor_id: UUID,
    body: ProvisionIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Create or link the accounting identity and the stock location.

    This is the integration: after it, the distributor is a customer to
    accounting and a warehouse to inventory, so invoices, payments, AR and
    stock transfers all work through the existing engines.

    Idempotent -- calling it twice reports what exists rather than creating a
    second identity.
    """
    result = await svc.provision_identities(
        session, distributor_id=distributor_id, actor=user,
        link_customer_id=body.link_customer_id)
    await session.commit()
    return result


@router.post("/{distributor_id}/status")
async def set_status(
    distributor_id: UUID,
    body: StatusIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Move a distributor through its lifecycle. Approval provisions identities."""
    result = await svc.set_status(
        session, distributor_id=distributor_id, new_status=body.status,
        reason=body.reason, actor=user)
    await session.commit()
    return result


@router.post("/{distributor_id}/eligibility")
async def assess_eligibility(
    distributor_id: UUID,
    body: EligibilityIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Score an applicant and list anything that blocks approval outright.

    Read `may_be_approved`, not `score`. A high score never carries an
    applicant past a mandatory failure.
    """
    return await svc.assess_eligibility(
        session, distributor_id=distributor_id,
        scores=body.model_dump(exclude_none=True))


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

@router.post("/{distributor_id}/documents", status_code=201)
async def upload_document(
    distributor_id: UUID,
    doc_type: str = Form(...),
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    issue_date: Optional[date] = Form(None),
    expiry_date: Optional[date] = Form(None),
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    buf = bytearray()
    while True:
        chunk = await file.read(256 * 1024)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > svc.MAX_DOCUMENT_BYTES:
            raise HTTPException(
                status_code=400,
                detail=(f"The document is larger than "
                        f"{svc.MAX_DOCUMENT_BYTES // 1_048_576}MB."))

    result = await svc.attach_document(
        session, distributor_id=distributor_id, doc_type=doc_type,
        filename=file.filename or "document",
        content_type=file.content_type or "application/octet-stream",
        content=bytes(buf), title=title, issue_date=issue_date,
        expiry_date=expiry_date, actor=user)
    await session.commit()
    return result


@router.get("/documents/{document_id}")
async def download_document(
    document_id: UUID,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    row = (await session.execute(
        text("""SELECT content, content_type, filename
                  FROM distributor_documents WHERE id = :id"""),
        {"id": str(document_id)},
    )).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    return Response(
        content=row["content"], media_type=row["content_type"],
        headers={
            "Content-Disposition": f'inline; filename="{row["filename"]}"',
            # Compliance documents are private to one distributor; a shared
            # cache must never hand one to the next viewer.
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/documents/{document_id}/verify")
async def verify_document(
    document_id: UUID,
    body: VerifyIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Verify or reject a document. Never the person who uploaded it."""
    result = await svc.verify_document(
        session, document_id=document_id, verified=body.verified, user=user,
        note=body.note)
    await session.commit()
    return result


# ---------------------------------------------------------------------------
# Qualifications
# ---------------------------------------------------------------------------

@router.post("/{distributor_id}/qualifications", status_code=201)
async def add_qualification(
    distributor_id: UUID,
    body: QualificationIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await svc.add_qualification(
        session, distributor_id=distributor_id,
        qualification_type=body.qualification_type,
        holder_name=body.holder_name, institution=body.institution,
        certificate_number=body.certificate_number,
        registration_number=body.registration_number,
        issuing_authority=body.issuing_authority, issue_date=body.issue_date,
        expiry_date=body.expiry_date, document_id=body.document_id, actor=user)
    await session.commit()
    return result


@router.post("/qualifications/{qualification_id}/verify")
async def verify_qualification(
    qualification_id: UUID,
    body: VerifyIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    status = "VERIFIED" if body.verified else "REJECTED"
    result = await session.execute(
        text("""UPDATE distributor_qualifications
                   SET verification_status = :s, verified_by = :by,
                       verified_at = NOW(), verification_note = :n
                 WHERE id = :id"""),
        {"s": status, "by": str(user.id), "n": body.note,
         "id": str(qualification_id)},
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Qualification not found.")
    await session.commit()
    return {"id": str(qualification_id), "verification_status": status}


# ---------------------------------------------------------------------------
# Expiry watch
# ---------------------------------------------------------------------------

@router.get("/compliance/expiring")
async def expiring(
    within_days: int = Query(90, ge=1, le=365),
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Documents and qualifications at or past expiry, soonest first."""
    items = await svc.expiring_documents(session, within_days=within_days)
    return {
        "within_days": within_days,
        "expired": [i for i in items if i["expired"]],
        "expiring_soon": [i for i in items if not i["expired"]],
        "total": len(items),
    }
