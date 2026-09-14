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

from app.api.auth import require_admin, require_distribution_access
from app.db import get_session
from app.models import User
from app.services import compliance as csvc
from app.services import portal
from app.services import registration as reg
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
    user: User = Depends(require_distribution_access),
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
    user: User = Depends(require_distribution_access),
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
    user: User = Depends(require_distribution_access),
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
    user: User = Depends(require_distribution_access),
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
    user: User = Depends(require_distribution_access),
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
    user: User = Depends(require_distribution_access),
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


# ---------------------------------------------------------------------------
# Compliance: storage facilities, assessments, corrective actions, agreements
#
# The checklist is DATA, not code, and every item declares whether it is a
# regulatory requirement, a company policy or a commercial expectation --
# because specification section 3 forbids presenting the second as the first.
# A regulatory item must name the authority that imposes it, and the database
# refuses one that does not.
# ---------------------------------------------------------------------------



class FacilityIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    address: Optional[str] = None
    state_id: Optional[UUID] = None
    lga_id: Optional[UUID] = None
    town: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    floor_area_sqm: Optional[Decimal] = Field(None, ge=0)
    capacity_note: Optional[str] = None
    responsible_person: Optional[str] = None
    responsible_phone: Optional[str] = None


class AssessmentStartIn(BaseModel):
    assessed_on: Optional[date] = None


class AnswerIn(BaseModel):
    checklist_item_id: UUID
    result: str
    note: Optional[str] = None
    evidence_document_id: Optional[UUID] = None


class SubmitAssessmentIn(BaseModel):
    summary: Optional[str] = None


class CorrectiveActionIn(BaseModel):
    status: Optional[str] = None
    severity: Optional[str] = None
    responsible_person: Optional[str] = None
    deadline: Optional[date] = None
    corrective_action: Optional[str] = None
    evidence_document_id: Optional[UUID] = None
    note: Optional[str] = None


class ChecklistItemIn(BaseModel):
    code: str = Field(..., min_length=2, max_length=48)
    section: str = Field(..., min_length=2, max_length=64)
    requirement: str = Field(..., min_length=5)
    requirement_kind: str = Field("COMPANY")
    # Mandatory when the kind is REGULATORY; the database enforces it too.
    authority: Optional[str] = None
    weight: int = Field(1, ge=1, le=10)
    is_critical: bool = False
    requires_evidence: bool = False
    sort_order: int = 100


class AgreementIn(BaseModel):
    title: str = Field(..., min_length=3, max_length=255)
    body: str = Field(..., min_length=20)
    terms: Optional[dict] = None
    territory_ids: list[UUID] = Field(default_factory=list)
    template_document_id: Optional[str] = None
    template_version: Optional[str] = None
    effective_from: Optional[date] = None
    expires_on: Optional[date] = None


class SignIn(BaseModel):
    signer_name: str = Field(..., min_length=2, max_length=255)
    signer_role: str
    meaning: str = Field(..., min_length=10)
    # The hash of the text actually displayed to the signer. Checked against
    # the stored agreement, so a signature is tied to specific words.
    body_sha256: str = Field(..., min_length=64, max_length=64)


class EndAgreementIn(BaseModel):
    status: str
    reason: str = Field(..., min_length=3)


# ---- checklist configuration ----------------------------------------------

@router.get("/compliance/checklist")
async def get_checklist(
    include_inactive: bool = False,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    """The storage checklist, with each item's provenance.

    `requirement_kind` and `authority` are shown so a distributor can see which
    requirements are law and which are this company's policy.
    """
    clause = "" if include_inactive else "WHERE is_active"
    rows = (await session.execute(
        text(f"""SELECT id, code, section, requirement, requirement_kind,
                        authority, weight, is_critical, requires_evidence,
                        sort_order, is_active
                   FROM facility_checklist_items {clause}
                  ORDER BY sort_order"""),
    )).mappings().all()
    kinds = {}
    for r in rows:
        kinds[r["requirement_kind"]] = kinds.get(r["requirement_kind"], 0) + 1
    return {
        "items": _rows(rows),
        "by_kind": kinds,
        "note": ("Items seeded with this module are COMPANY requirements. Mark "
                 "an item REGULATORY only when a law or regulator imposes it, "
                 "and name that authority -- the system will not accept a "
                 "regulatory claim without one."),
    }


@router.post("/compliance/checklist", status_code=201)
async def add_checklist_item(
    body: ChecklistItemIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    if body.requirement_kind not in ("REGULATORY", "COMPANY", "COMMERCIAL"):
        raise HTTPException(
            status_code=400,
            detail="Kind must be REGULATORY, COMPANY or COMMERCIAL.")
    if body.requirement_kind == "REGULATORY" and not (body.authority or "").strip():
        raise HTTPException(
            status_code=400,
            detail=("A regulatory requirement must name the authority that "
                    "imposes it. Company policy must not be recorded as law."))
    await session.execute(
        text("""INSERT INTO facility_checklist_items
                    (id, code, section, requirement, requirement_kind,
                     authority, weight, is_critical, requires_evidence,
                     sort_order)
                VALUES (gen_random_uuid(), :c, :s, :r, :k, :a, :w, :crit,
                        :ev, :ord)"""),
        {"c": body.code.upper(), "s": body.section, "r": body.requirement,
         "k": body.requirement_kind, "a": (body.authority or "").strip() or None,
         "w": body.weight, "crit": body.is_critical,
         "ev": body.requires_evidence, "ord": body.sort_order},
    )
    await session.commit()
    return {"code": body.code.upper(), "requirement_kind": body.requirement_kind}


# ---- facilities -------------------------------------------------------------

@router.post("/{distributor_id}/facilities", status_code=201)
async def add_facility(
    distributor_id: UUID,
    body: FacilityIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await csvc.create_facility(
        session, distributor_id=distributor_id, name=body.name,
        address=body.address, state_id=body.state_id, lga_id=body.lga_id,
        town=body.town, latitude=body.latitude, longitude=body.longitude,
        floor_area_sqm=body.floor_area_sqm, capacity_note=body.capacity_note,
        responsible_person=body.responsible_person,
        responsible_phone=body.responsible_phone, actor=user)
    await session.commit()
    return result


@router.get("/{distributor_id}/facilities")
async def list_facilities(
    distributor_id: UUID,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.execute(
        text("""SELECT f.id, f.name, f.address, f.town, f.status,
                       f.responsible_person, f.floor_area_sqm, f.is_primary,
                       s.name AS state, l.name AS lga,
                       (SELECT fa.assessed_on FROM facility_assessments fa
                         WHERE fa.facility_id = f.id AND fa.status <> 'DRAFT'
                         ORDER BY fa.assessed_on DESC LIMIT 1) AS last_assessed,
                       (SELECT fa.score FROM facility_assessments fa
                         WHERE fa.facility_id = f.id AND fa.status <> 'DRAFT'
                         ORDER BY fa.assessed_on DESC LIMIT 1) AS last_score
                  FROM distributor_facilities f
                  LEFT JOIN states s ON s.id = f.state_id
                  LEFT JOIN lgas l ON l.id = f.lga_id
                 WHERE f.distributor_id = :d
                 ORDER BY f.is_primary DESC, f.created_at"""),
        {"d": str(distributor_id)},
    )).mappings().all()
    return {"facilities": _rows(rows)}


# ---- assessment -------------------------------------------------------------

@router.post("/facilities/{facility_id}/assessments", status_code=201)
async def start_assessment(
    facility_id: UUID,
    body: AssessmentStartIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Open a draft assessment and return the checklist to work through."""
    result = await csvc.start_assessment(
        session, facility_id=facility_id, assessed_on=body.assessed_on,
        actor=user)
    await session.commit()
    return result


@router.put("/assessments/{assessment_id}/items")
async def answer_item(
    assessment_id: UUID,
    body: AnswerIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Record one checklist answer. The question is snapshotted as answered."""
    result = await csvc.answer_item(
        session, assessment_id=assessment_id,
        checklist_item_id=body.checklist_item_id, result=body.result,
        note=body.note, evidence_document_id=body.evidence_document_id)
    await session.commit()
    return result


@router.get("/assessments/{assessment_id}/score")
async def preview_score(
    assessment_id: UUID,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    """The running score while the assessment is still open.

    Read `outcome`, not `score`. A critical or regulatory failure makes the
    outcome FAIL regardless of how good the percentage looks.
    """
    return await csvc.score_assessment(session, assessment_id)


@router.post("/assessments/{assessment_id}/submit")
async def submit_assessment(
    assessment_id: UUID,
    body: SubmitAssessmentIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Freeze the findings and raise a corrective action for every failure."""
    result = await csvc.submit_assessment(
        session, assessment_id=assessment_id, summary=body.summary, actor=user)
    await session.commit()
    return result


@router.get("/assessments/{assessment_id}")
async def assessment_detail(
    assessment_id: UUID,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    head = (await session.execute(
        text("""SELECT fa.*, f.name AS facility_name,
                       d.distributor_code, d.legal_name,
                       u.full_name AS assessor
                  FROM facility_assessments fa
                  JOIN distributor_facilities f ON f.id = fa.facility_id
                  JOIN distributors d ON d.id = fa.distributor_id
                  LEFT JOIN users u ON u.id = fa.assessor_id
                 WHERE fa.id = :a"""),
        {"a": str(assessment_id)},
    )).mappings().first()
    if head is None:
        raise HTTPException(status_code=404, detail="Assessment not found.")

    items = (await session.execute(
        text("""SELECT fai.id, fai.result, fai.note, fai.requirement_snapshot,
                       fai.kind_snapshot, fai.was_critical,
                       fai.evidence_document_id, ci.code, ci.section,
                       ci.authority
                  FROM facility_assessment_items fai
                  JOIN facility_checklist_items ci
                       ON ci.id = fai.checklist_item_id
                 WHERE fai.assessment_id = :a
                 ORDER BY ci.sort_order"""),
        {"a": str(assessment_id)},
    )).mappings().all()

    actions = (await session.execute(
        text("""SELECT id, action_reference, finding, severity, status,
                       responsible_person, deadline, corrective_action,
                       verified_at
                  FROM facility_corrective_actions
                 WHERE assessment_id = :a ORDER BY severity DESC, created_at"""),
        {"a": str(assessment_id)},
    )).mappings().all()

    return {
        "assessment": _rows([head])[0],
        "items": _rows(items),
        "corrective_actions": _rows(actions),
        "live_score": await csvc.score_assessment(session, assessment_id),
    }


# ---- corrective actions -----------------------------------------------------

@router.patch("/corrective-actions/{action_id}")
async def update_corrective_action(
    action_id: UUID,
    body: CorrectiveActionIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Progress a finding. Closing it records who verified the fix."""
    result = await csvc.update_corrective_action(
        session, action_id=action_id, status=body.status,
        severity=body.severity, responsible_person=body.responsible_person,
        deadline=body.deadline, corrective_action=body.corrective_action,
        evidence_document_id=body.evidence_document_id, note=body.note,
        user=user)
    await session.commit()
    return result


@router.get("/compliance/corrective-actions")
async def open_corrective_actions(
    overdue_only: bool = False,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    clause = "AND ca.deadline < CURRENT_DATE" if overdue_only else ""
    rows = (await session.execute(
        text(f"""SELECT ca.id, ca.action_reference, ca.finding, ca.severity,
                        ca.status, ca.responsible_person, ca.deadline,
                        (ca.deadline < CURRENT_DATE) AS overdue,
                        d.distributor_code, d.legal_name
                   FROM facility_corrective_actions ca
                   JOIN distributors d ON d.id = ca.distributor_id
                  WHERE ca.status <> 'CLOSED' {clause}
                  ORDER BY CASE ca.severity WHEN 'CRITICAL' THEN 0
                                            WHEN 'HIGH' THEN 1
                                            WHEN 'MEDIUM' THEN 2 ELSE 3 END,
                           ca.deadline NULLS LAST"""),
    )).mappings().all()
    return {"corrective_actions": _rows(rows)}


# ---- agreements -------------------------------------------------------------

@router.post("/{distributor_id}/agreements", status_code=201)
async def create_agreement(
    distributor_id: UUID,
    body: AgreementIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Draft an agreement from rendered text.

    This endpoint provides the lifecycle, hashing and signature discipline --
    not the contract language. Section 8 is explicit that the template is a
    company document requiring legal review before execution.
    """
    result = await csvc.create_agreement(
        session, distributor_id=distributor_id, title=body.title,
        body=body.body, terms=body.terms, territory_ids=body.territory_ids,
        template_document_id=body.template_document_id,
        template_version=body.template_version,
        effective_from=body.effective_from, expires_on=body.expires_on,
        actor=user)
    await session.commit()
    return result


@router.get("/{distributor_id}/agreements")
async def list_agreements(
    distributor_id: UUID,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.execute(
        text("""SELECT a.id, a.agreement_reference, a.title, a.status,
                       a.effective_from, a.expires_on, a.issued_at,
                       a.accepted_at, a.countersigned_at, a.activated_at,
                       a.ended_at, a.end_reason, a.body_sha256,
                       (SELECT COUNT(*) FROM distributor_agreement_signatures s
                         WHERE s.agreement_id = a.id) AS signature_count
                  FROM distributor_agreements a
                 WHERE a.distributor_id = :d
                 ORDER BY a.created_at DESC"""),
        {"d": str(distributor_id)},
    )).mappings().all()
    return {"agreements": _rows(rows)}


@router.get("/agreements/{agreement_id}")
async def agreement_detail(
    agreement_id: UUID,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    """The agreement as issued, with its signatures.

    `body_sha256` is what a signer must echo back, so a signature is tied to
    the exact text rather than to the act of clicking.
    """
    row = (await session.execute(
        text("""SELECT a.*, d.distributor_code, d.legal_name
                  FROM distributor_agreements a
                  JOIN distributors d ON d.id = a.distributor_id
                 WHERE a.id = :a"""),
        {"a": str(agreement_id)},
    )).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Agreement not found.")

    signatures = (await session.execute(
        text("""SELECT signer_name, signer_role, meaning, content_hash,
                       signed_at, ip_address
                  FROM distributor_agreement_signatures
                 WHERE agreement_id = :a ORDER BY signed_at"""),
        {"a": str(agreement_id)},
    )).mappings().all()

    return {"agreement": _rows([row])[0], "signatures": _rows(signatures)}


@router.post("/agreements/{agreement_id}/issue")
async def issue_agreement(
    agreement_id: UUID,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Present the agreement. Its text freezes at this point."""
    result = await csvc.issue_agreement(
        session, agreement_id=agreement_id, actor=user)
    await session.commit()
    return result


@router.post("/agreements/{agreement_id}/sign")
async def sign_agreement(
    agreement_id: UUID,
    body: SignIn,
    request: Request,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    """Sign the agreement, echoing back the hash of the text you were shown."""
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() or (
        request.client.host if request.client else "")
    result = await csvc.sign_agreement(
        session, agreement_id=agreement_id, signer_name=body.signer_name,
        signer_role=body.signer_role, meaning=body.meaning,
        body_sha256=body.body_sha256, user=user, ip_address=ip,
        user_agent=request.headers.get("user-agent", ""))
    await session.commit()
    return result


@router.post("/agreements/{agreement_id}/activate")
async def activate_agreement(
    agreement_id: UUID,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Bring a countersigned agreement into force. Both signatures required."""
    result = await csvc.activate_agreement(
        session, agreement_id=agreement_id, actor=user)
    await session.commit()
    return result


@router.post("/agreements/{agreement_id}/end")
async def end_agreement(
    agreement_id: UUID,
    body: EndAgreementIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await csvc.end_agreement(
        session, agreement_id=agreement_id, status=body.status,
        reason=body.reason, actor=user)
    await session.commit()
    return result


@router.get("/{distributor_id}/compliance")
async def compliance_summary(
    distributor_id: UUID,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    """Is this distributor fit to trade, and if not, exactly why."""
    return await csvc.compliance_summary(session, distributor_id)


# ---------------------------------------------------------------------------
# Ordering links -- phase 5
#
# Issuing and revoking the credentials the public portal accepts. Admin only,
# and deliberately in this module rather than in app/api/portal.py: that router
# is unauthenticated, and an admin route must never sit beside routes reachable
# by anyone with a URL.
# ---------------------------------------------------------------------------

class OrderLinkIn(BaseModel):
    label: str = Field(..., min_length=3, max_length=160)
    recipient_name: Optional[str] = None
    recipient_phone: Optional[str] = None
    valid_days: int = Field(30, ge=1, le=365)


class RevokeLinkIn(BaseModel):
    reason: str = Field(..., min_length=3)


@router.post("/{distributor_id}/order-links", status_code=201)
async def issue_order_link(
    distributor_id: UUID,
    body: OrderLinkIn,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Issue a shareable ordering link.

    The token is in the response and NOWHERE ELSE. Only its hash is stored, so
    there is no endpoint that can show it again -- copy it now or issue another.
    """
    base = str(request.base_url).rstrip("/")
    result = await portal.issue_link(
        session, distributor_id=distributor_id, label=body.label,
        recipient_name=body.recipient_name,
        recipient_phone=body.recipient_phone, valid_days=body.valid_days,
        base_url=base, actor=user)
    await session.commit()
    return result


@router.get("/{distributor_id}/order-links")
async def list_order_links(
    distributor_id: UUID,
    include_dead: bool = False,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    """Links for this distributor. The tokens are not here and never will be."""
    rows = await portal.list_links(
        session, distributor_id=distributor_id, include_dead=include_dead)
    return {"links": _rows(rows)}


@router.post("/order-links/{link_id}/revoke")
async def revoke_order_link(
    link_id: UUID,
    body: RevokeLinkIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Stop a link working. Immediate, permanent, and recorded."""
    result = await portal.revoke_link(
        session, link_id=link_id, reason=body.reason, actor=user)
    await session.commit()
    return result


@router.get("/order-links/{link_id}/activity")
async def order_link_activity(
    link_id: UUID,
    limit: int = Query(100, ge=1, le=500),
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Every use of this link, append-only.

    What to look at if a link is thought to have leaked: where it was opened
    from, and what was ordered with it.
    """
    rows = await portal.link_activity(session, link_id=link_id, limit=limit)
    return {"activity": _rows(rows)}


@router.get("/{distributor_id}/orders")
async def distributor_orders(
    distributor_id: UUID,
    limit: int = Query(100, ge=1, le=500),
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    """Orders this distributor has placed.

    Read straight from sales_orders. There is no distributor order table: a
    distributor order IS a sales order, so it appears in sales reporting, AR and
    dispatch exactly like every other one.
    """
    rows = await portal.distributor_orders(
        session, distributor_id=distributor_id, limit=limit)
    return {"orders": _rows(rows)}


# ---------------------------------------------------------------------------
# Self-registration: the shareable link, and the queue it feeds
# ---------------------------------------------------------------------------

class RegistrationLinkIn(BaseModel):
    label: str = Field(..., min_length=3, max_length=160)
    campaign: Optional[str] = None
    valid_days: int = Field(90, ge=1, le=730)
    max_submissions: Optional[int] = Field(None, ge=1, le=100000)


class RevokeRegistrationLinkIn(BaseModel):
    reason: str = Field(..., min_length=3)


class RegistrationDecisionIn(BaseModel):
    approve: bool
    note: str = Field(..., min_length=3)
    # The reviewer's decision about who this applicant already is. Separate
    # from whatever the applicant claimed on the form.
    link_customer_id: Optional[UUID] = None
    acknowledge_duplicates: bool = False


@router.post("/registration-links", status_code=201)
async def issue_registration_link(
    body: RegistrationLinkIn,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Create a link prospective distributors can use to apply.

    Unlike an ordering link this is meant to be shared widely -- it lets anyone
    APPLY and nothing more. The token is in the response and nowhere else.
    """
    base = str(request.base_url).rstrip("/")
    result = await reg.issue_link(
        session, label=body.label, campaign=body.campaign,
        valid_days=body.valid_days, max_submissions=body.max_submissions,
        base_url=base, actor=user)
    await session.commit()
    return result


@router.get("/registration-links")
async def list_registration_links(
    include_dead: bool = False,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    rows = await reg.list_links(session, include_dead=include_dead)
    return {"links": _rows(rows)}


@router.post("/registration-links/{link_id}/revoke")
async def revoke_registration_link(
    link_id: UUID,
    body: RevokeRegistrationLinkIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Stop a link accepting applications. Immediate and recorded."""
    result = await reg.revoke_link(
        session, link_id=link_id, reason=body.reason, actor=user)
    await session.commit()
    return result


@router.get("/registrations")
async def list_registrations(
    status: Optional[str] = None,
    pending_only: bool = False,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    """Applications received. Everything here is unverified."""
    rows = await reg.list_registrations(
        session, status=status, pending_only=pending_only)
    return {"registrations": _rows(rows)}


@router.get("/registrations/{registration_id}")
async def registration_detail(
    registration_id: UUID,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    """The application, and who the company thinks this already is.

    The candidate list is built HERE rather than on the public form: matching
    on the public side would mean showing an anonymous visitor who the company
    already trades with. It also catches an applicant who does not realise
    they are already a customer under a slightly different name.
    """
    return await reg.review_packet(session, registration_id=registration_id)


@router.post("/registrations/{registration_id}/decide")
async def decide_registration(
    registration_id: UUID,
    body: RegistrationDecisionIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Approve or refuse. Approving creates the distributor.

    Where `link_customer_id` is given, the applicant's EXISTING customer
    account is attached rather than a new one being created, and the orders
    already recorded against it are attributed to the new distributor. Nothing
    is copied: those orders were always theirs, and the response says how many
    became visible.
    """
    result = await reg.review(
        session, registration_id=registration_id, approve=body.approve,
        note=body.note, link_customer_id=body.link_customer_id,
        acknowledge_duplicates=body.acknowledge_duplicates, actor=user)
    await session.commit()
    return result
