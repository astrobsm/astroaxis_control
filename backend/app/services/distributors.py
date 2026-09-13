"""Distributor identity, provisioning, duplicate detection and eligibility.

THE PROVISIONING STEP IS THE WHOLE INTEGRATION
----------------------------------------------
When a distributor is approved, `provision_identities` gives it:

  * a row in `customers` -- its accounting identity. From that moment invoices,
    payments, AR ageing and GL posting work through the existing financial
    code, because to accounting the distributor simply IS a customer. There is
    no distributor ledger to reconcile against the real one.
  * a row in `warehouses` -- its stock location. Shipping to the distributor
    becomes transfer_stock(main -> distributor): an existing, audited,
    balance-checked movement rather than a number decremented somewhere else.

Everything else in the distributor module is workflow and reporting laid on top
of those two links. Get provisioning wrong and the module becomes the parallel
system it exists to avoid, so it is idempotent, transactional, and refuses to
half-complete.

DUPLICATES ARE REPORTED, NEVER MERGED
-------------------------------------
`find_possible_duplicates` searches existing customers and distributors before
anything is created. It returns candidates for a human to judge. Automatic
merging of two business identities is not reversible and not this module's
decision to make.

THE SCORE DOES NOT OUTRANK A MANDATORY REQUIREMENT
--------------------------------------------------
A high eligibility score cannot carry an applicant past a failed mandatory
condition. The score and the blocking conditions are evaluated separately and
reported separately, because a weighted average that can silently absorb a
missing licence is worse than no score at all.
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import date
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.geography import audit

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
ALLOWED_DOCUMENT_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif",
    "application/pdf",
}

# Default weights for section 6. Overridable per deployment; the admin screen
# that edits them arrives with the compliance phase. What is NOT configurable
# is that a mandatory failure blocks regardless of the total -- see
# `assess_eligibility`.
DEFAULT_WEIGHTS = {
    "regulatory_eligibility": Decimal(os.getenv("DIST_W_REGULATORY", "20")),
    "professional_qualification": Decimal(os.getenv("DIST_W_QUALIFICATION", "15")),
    "storage_facility": Decimal(os.getenv("DIST_W_STORAGE", "15")),
    "financial_capacity": Decimal(os.getenv("DIST_W_FINANCIAL", "15")),
    "sales_capability": Decimal(os.getenv("DIST_W_SALES", "15")),
    "geographic_capability": Decimal(os.getenv("DIST_W_GEOGRAPHIC", "10")),
    "distribution_experience": Decimal(os.getenv("DIST_W_EXPERIENCE", "10")),
}
ELIGIBLE_AT = Decimal(os.getenv("DIST_ELIGIBLE_AT", "80"))
CONDITIONAL_AT = Decimal(os.getenv("DIST_CONDITIONAL_AT", "60"))


def _norm(value: Optional[str]) -> str:
    """Fold a name for comparison: case, punctuation and company suffixes.

    'Bonne Stores Ltd.', 'BONNE STORES LIMITED' and 'Bonne  Stores' are the
    same business applying three times, and a duplicate check that misses that
    is a duplicate check that does nothing.
    """
    s = (value or "").lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(
        r"\b(ltd|limited|plc|nig|nigeria|enterprises|enterprise|ventures|"
        r"venture|company|co|and|sons|global|services|resources)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _digits(value: Optional[str]) -> str:
    return re.sub(r"\D", "", value or "")


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

async def find_possible_duplicates(
    session: AsyncSession, *, legal_name: str = "", phone: str = "",
    email: str = "", cac_number: str = "", tin: str = "",
    exclude_distributor_id: Optional[UUID] = None,
) -> list[dict]:
    """Existing distributors and customers that might be this applicant.

    Returns candidates with the reason each matched, ordered strongest first.
    Never merges anything: an identity decision belongs to a person.
    """
    matches: dict[str, dict] = {}

    def add(key: str, kind: str, ident: str, name: str, reason: str,
            strength: int, extra: Optional[dict] = None):
        entry = matches.setdefault(
            key, {"kind": kind, "id": ident, "name": name, "reasons": [],
                  "strength": 0, **(extra or {})})
        entry["reasons"].append(reason)
        entry["strength"] = max(entry["strength"], strength)

    # Registration identifiers are the strongest signal: two businesses cannot
    # share a CAC number or a TIN.
    if cac_number.strip():
        for r in (await session.execute(
            text("""SELECT id, distributor_code, legal_name FROM distributors
                     WHERE lower(cac_number) = lower(:v)
                       AND (CAST(:x AS uuid) IS NULL OR id <> CAST(:x AS uuid))"""),
            {"v": cac_number.strip(),
             "x": str(exclude_distributor_id) if exclude_distributor_id else None},
        )).mappings().all():
            add(f"d:{r['id']}", "distributor", str(r["id"]), r["legal_name"],
                "Same CAC registration number", 100,
                {"code": r["distributor_code"]})

    if tin.strip():
        for r in (await session.execute(
            text("""SELECT id, distributor_code, legal_name FROM distributors
                     WHERE lower(tin) = lower(:v)
                       AND (CAST(:x AS uuid) IS NULL OR id <> CAST(:x AS uuid))"""),
            {"v": tin.strip(),
             "x": str(exclude_distributor_id) if exclude_distributor_id else None},
        )).mappings().all():
            add(f"d:{r['id']}", "distributor", str(r["id"]), r["legal_name"],
                "Same tax identification number", 100,
                {"code": r["distributor_code"]})

    if email.strip():
        for r in (await session.execute(
            text("""SELECT id, distributor_code, legal_name FROM distributors
                     WHERE lower(email) = lower(:v)
                       AND (CAST(:x AS uuid) IS NULL OR id <> CAST(:x AS uuid))"""),
            {"v": email.strip(),
             "x": str(exclude_distributor_id) if exclude_distributor_id else None},
        )).mappings().all():
            add(f"d:{r['id']}", "distributor", str(r["id"]), r["legal_name"],
                "Same email address", 70, {"code": r["distributor_code"]})
        for r in (await session.execute(
            text("""SELECT id, customer_code, name FROM customers
                     WHERE lower(email) = lower(:v)"""),
            {"v": email.strip()},
        )).mappings().all():
            add(f"c:{r['id']}", "customer", str(r["id"]), r["name"],
                "An existing customer uses this email", 70,
                {"code": r["customer_code"]})

    phone_digits = _digits(phone)
    if len(phone_digits) >= 10:
        # Compare the last 10 digits so 0803..., +234803... and 234803... match.
        tail = phone_digits[-10:]
        for r in (await session.execute(
            text("""SELECT id, distributor_code, legal_name, phone
                      FROM distributors
                     WHERE regexp_replace(COALESCE(phone,''), '\\D', '', 'g')
                           LIKE '%' || :t
                       AND (CAST(:x AS uuid) IS NULL OR id <> CAST(:x AS uuid))"""),
            {"t": tail,
             "x": str(exclude_distributor_id) if exclude_distributor_id else None},
        )).mappings().all():
            add(f"d:{r['id']}", "distributor", str(r["id"]), r["legal_name"],
                "Same phone number", 80, {"code": r["distributor_code"]})
        for r in (await session.execute(
            text("""SELECT id, customer_code, name FROM customers
                     WHERE regexp_replace(COALESCE(phone,''), '\\D', '', 'g')
                           LIKE '%' || :t"""),
            {"t": tail},
        )).mappings().all():
            add(f"c:{r['id']}", "customer", str(r["id"]), r["name"],
                "An existing customer uses this phone number", 80,
                {"code": r["customer_code"]})

    folded = _norm(legal_name)
    if len(folded) >= 4:
        for r in (await session.execute(
            text("""SELECT id, distributor_code, legal_name FROM distributors
                     WHERE (CAST(:x AS uuid) IS NULL OR id <> CAST(:x AS uuid))"""),
            {"x": str(exclude_distributor_id) if exclude_distributor_id else None},
        )).mappings().all():
            if _norm(r["legal_name"]) == folded:
                add(f"d:{r['id']}", "distributor", str(r["id"]),
                    r["legal_name"], "Effectively the same business name", 90,
                    {"code": r["distributor_code"]})
        for r in (await session.execute(
            text("SELECT id, customer_code, name FROM customers"),
        )).mappings().all():
            if _norm(r["name"]) == folded:
                add(f"c:{r['id']}", "customer", str(r["id"]), r["name"],
                    "An existing customer has effectively this name", 85,
                    {"code": r["customer_code"]})

    return sorted(matches.values(), key=lambda m: -m["strength"])


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

async def _next_distributor_code(session: AsyncSession) -> str:
    row = (await session.execute(
        text("SELECT COUNT(*) AS n FROM distributors"))).first()
    return f"DIST-{row.n + 1:06d}"


async def create_distributor(
    session: AsyncSession, *, legal_name: str, entity_type: str = "COMPANY",
    trading_name: Optional[str] = None, phone: Optional[str] = None,
    whatsapp: Optional[str] = None, email: Optional[str] = None,
    business_address: Optional[str] = None, state_id: Optional[UUID] = None,
    lga_id: Optional[UUID] = None, town: Optional[str] = None,
    cac_number: Optional[str] = None, tin: Optional[str] = None,
    years_in_operation: Optional[int] = None,
    business_type: Optional[str] = None, employee_count: Optional[int] = None,
    marketer_count: Optional[int] = None, actor=None,
    acknowledge_duplicates: bool = False,
) -> dict:
    """Register a distributor in DRAFT.

    Refuses if a probable duplicate exists unless the caller has been shown the
    candidates and explicitly acknowledged them -- which is how a person stays
    in the loop without the check being a mere suggestion.
    """
    if not legal_name.strip():
        raise HTTPException(status_code=400, detail="A legal name is required.")

    duplicates = await find_possible_duplicates(
        session, legal_name=legal_name, phone=phone or "", email=email or "",
        cac_number=cac_number or "", tin=tin or "")
    strong = [d for d in duplicates if d["strength"] >= 80]
    if strong and not acknowledge_duplicates:
        raise HTTPException(
            status_code=409,
            detail={
                "message": ("This may already exist in the system. Review the "
                            "matches before creating a new record."),
                "candidates": strong,
            })

    distributor_id = uuid4()
    code = await _next_distributor_code(session)
    await session.execute(
        text("""
            INSERT INTO distributors
                (id, distributor_code, legal_name, trading_name, entity_type,
                 phone, whatsapp, email, business_address, state_id, lga_id,
                 town, cac_number, tin, years_in_operation, business_type,
                 employee_count, marketer_count, status, created_by)
            VALUES (:id, :code, :ln, :tn, :et, :ph, :wa, :em, :addr, :st, :lg,
                    :town, :cac, :tin, :yrs, :bt, :emp, :mkt, 'DRAFT', :by)
        """),
        {"id": str(distributor_id), "code": code, "ln": legal_name.strip(),
         "tn": trading_name, "et": entity_type, "ph": phone, "wa": whatsapp,
         "em": email, "addr": business_address,
         "st": str(state_id) if state_id else None,
         "lg": str(lga_id) if lga_id else None, "town": town,
         "cac": (cac_number or "").strip() or None,
         "tin": (tin or "").strip() or None, "yrs": years_in_operation,
         "bt": business_type, "emp": employee_count, "mkt": marketer_count,
         "by": str(actor.id) if actor else None},
    )
    await audit(session, event_type="DISTRIBUTOR_CREATED",
                entity_type="distributor", entity_id=distributor_id,
                distributor_id=distributor_id, actor=actor,
                new_value={"code": code, "legal_name": legal_name},
                reason=("Created despite duplicate warning"
                        if strong else None))
    return {"id": str(distributor_id), "distributor_code": code,
            "legal_name": legal_name, "status": "DRAFT",
            "duplicate_warnings": strong if strong else []}


async def get_distributor(session: AsyncSession, distributor_id: UUID) -> dict:
    row = (await session.execute(
        text("SELECT * FROM distributors WHERE id = :d"),
        {"d": str(distributor_id)},
    )).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Distributor not found.")
    return dict(row)


# ---------------------------------------------------------------------------
# Provisioning -- the integration itself
# ---------------------------------------------------------------------------

async def provision_identities(
    session: AsyncSession, *, distributor_id: UUID, actor=None,
    link_customer_id: Optional[UUID] = None,
) -> dict:
    """Give the distributor its accounting identity and its stock location.

    Idempotent: safe to call twice, and calling it on an already-provisioned
    distributor reports what exists rather than creating a second identity.

    `link_customer_id` attaches an EXISTING customer account -- the right
    outcome when the applicant already trades with the company, since creating
    a second account would split their history and their balance in two.
    """
    dist = await get_distributor(session, distributor_id)
    created: list[str] = []

    # --- accounting identity -------------------------------------------
    customer_id = dist["customer_id"]
    if customer_id is None:
        if link_customer_id is not None:
            existing = (await session.execute(
                text("""SELECT id, name, distributor_id FROM customers
                         WHERE id = :c"""),
                {"c": str(link_customer_id)},
            )).mappings().first()
            if existing is None:
                raise HTTPException(
                    status_code=404, detail="That customer does not exist.")
            if existing["distributor_id"] is not None:
                raise HTTPException(
                    status_code=409,
                    detail=(f"Customer {existing['name']} is already the "
                            f"accounting identity of another distributor."))
            customer_id = existing["id"]
            created.append("linked existing customer")
        else:
            customer_id = uuid4()
            await session.execute(
                text("""
                    INSERT INTO customers
                        (id, customer_code, name, email, phone, address,
                         distributor_id, cac_number, tin, is_active)
                    VALUES (:id, :code, :name, :em, :ph, :addr, :d, :cac,
                            :tin, TRUE)
                """),
                {"id": str(customer_id), "code": dist["distributor_code"],
                 "name": dist["legal_name"], "em": dist["email"],
                 "ph": dist["phone"], "addr": dist["business_address"],
                 "d": str(distributor_id), "cac": dist["cac_number"],
                 "tin": dist["tin"]},
            )
            created.append("customer account")

        await session.execute(
            text("""UPDATE customers SET distributor_id = :d
                     WHERE id = :c AND distributor_id IS NULL"""),
            {"d": str(distributor_id), "c": str(customer_id)})

    # --- stock location -------------------------------------------------
    warehouse_id = dist["warehouse_id"]
    if warehouse_id is None:
        warehouse_id = uuid4()
        await session.execute(
            text("""
                INSERT INTO warehouses
                    (id, code, name, location, is_active, warehouse_kind,
                     distributor_id)
                VALUES (:id, :code, :name, :loc, TRUE, 'DISTRIBUTOR', :d)
            """),
            {"id": str(warehouse_id),
             "code": f"WH-{dist['distributor_code']}",
             "name": f"{dist['legal_name']} (distributor stock)",
             "loc": dist["business_address"] or dist["town"],
             "d": str(distributor_id)},
        )
        created.append("stock location")

    await session.execute(
        text("""UPDATE distributors
                   SET customer_id = :c, warehouse_id = :w, updated_at = NOW()
                 WHERE id = :d"""),
        {"c": str(customer_id), "w": str(warehouse_id),
         "d": str(distributor_id)},
    )

    if created:
        await audit(session, event_type="DISTRIBUTOR_PROVISIONED",
                    entity_type="distributor", entity_id=distributor_id,
                    distributor_id=distributor_id, actor=actor,
                    new_value={"customer_id": str(customer_id),
                               "warehouse_id": str(warehouse_id),
                               "created": created})
    return {
        "distributor_id": str(distributor_id),
        "customer_id": str(customer_id),
        "warehouse_id": str(warehouse_id),
        "created": created or ["already provisioned"],
    }


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------

async def assess_eligibility(
    session: AsyncSession, *, distributor_id: UUID,
    scores: Optional[dict] = None,
) -> dict:
    """Score an applicant, and separately report anything that blocks them.

    The weighted score and the mandatory conditions are two different answers.
    A high score never carries an applicant past a missing licence, because a
    weighted average that can absorb a regulatory failure is a way of hiding
    one (specification section 6).
    """
    dist = await get_distributor(session, distributor_id)
    provided = scores or {}

    breakdown, total = [], Decimal("0")
    for factor, weight in DEFAULT_WEIGHTS.items():
        raw = provided.get(factor)
        if raw is None:
            earned = Decimal("0")
            note = "Not assessed"
        else:
            pct = max(Decimal("0"), min(Decimal("100"), Decimal(str(raw))))
            earned = (weight * pct / Decimal("100")).quantize(Decimal("0.01"))
            note = f"{pct}% of {weight}"
        total += earned
        breakdown.append({"factor": factor, "weight": str(weight),
                          "earned": str(earned), "note": note})

    # Mandatory conditions, evaluated independently of the score.
    blocking: list[str] = []

    if dist["entity_type"] == "COMPANY" and not (dist["cac_number"] or "").strip():
        blocking.append(
            "A company distributor must provide its CAC registration number.")

    docs = (await session.execute(
        text("""SELECT COUNT(*) FILTER (WHERE verification_status = 'VERIFIED')
                           AS verified,
                       COUNT(*) AS total
                  FROM distributor_documents WHERE distributor_id = :d"""),
        {"d": str(distributor_id)},
    )).mappings().first()
    if not docs["verified"]:
        blocking.append(
            "No submitted document has been verified yet.")

    expired = (await session.execute(
        text("""SELECT COUNT(*) AS n FROM distributor_qualifications
                 WHERE distributor_id = :d AND expiry_date IS NOT NULL
                   AND expiry_date < CURRENT_DATE"""),
        {"d": str(distributor_id)},
    )).first()
    if expired.n:
        blocking.append(
            f"{expired.n} qualification(s) have expired and must be renewed.")

    total = total.quantize(Decimal("0.01"))
    if total >= ELIGIBLE_AT:
        band = "ELIGIBLE"
    elif total >= CONDITIONAL_AT:
        band = "CONDITIONALLY_ELIGIBLE"
    else:
        band = "NOT_ELIGIBLE"

    return {
        "score": str(total),
        "band": band,
        "breakdown": breakdown,
        "blocking_conditions": blocking,
        # The score alone never decides. This is the field callers must read.
        "may_be_approved": band != "NOT_ELIGIBLE" and not blocking,
        "thresholds": {"eligible_at": str(ELIGIBLE_AT),
                       "conditional_at": str(CONDITIONAL_AT)},
        "documents": {"verified": docs["verified"], "submitted": docs["total"]},
    }


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

_ALLOWED_TRANSITIONS = {
    "DRAFT": {"APPLIED", "REJECTED"},
    "APPLIED": {"UNDER_REVIEW", "REJECTED", "DRAFT"},
    "UNDER_REVIEW": {"APPROVED", "REJECTED", "APPLIED"},
    "APPROVED": {"ACTIVE", "SUSPENDED", "TERMINATED"},
    "ACTIVE": {"SUSPENDED", "TERMINATED"},
    "SUSPENDED": {"ACTIVE", "TERMINATED"},
    "TERMINATED": set(),
    "REJECTED": {"DRAFT"},
}


async def set_status(
    session: AsyncSession, *, distributor_id: UUID, new_status: str,
    reason: str, actor=None,
) -> dict:
    """Move a distributor through its lifecycle, enforcing legal transitions.

    Activation provisions the accounting identity and stock location if they do
    not exist -- the database constraint refuses an ACTIVE distributor without
    them, so a half-finished activation cannot reach the first order.
    """
    dist = await get_distributor(session, distributor_id)
    old = dist["status"]

    if new_status == old:
        return {"id": str(distributor_id), "status": old, "changed": False}
    allowed = _ALLOWED_TRANSITIONS.get(old, set())
    if new_status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=(f"A {old.lower()} distributor cannot become "
                    f"{new_status.lower()}. Permitted from here: "
                    f"{', '.join(sorted(allowed)) or 'nothing -- this is final'}."))
    if not reason or len(reason.strip()) < 3:
        raise HTTPException(
            status_code=400,
            detail="A reason is required for every status change.")

    if new_status in ("APPROVED", "ACTIVE"):
        await provision_identities(
            session, distributor_id=distributor_id, actor=actor)

    stamps = {
        "APPROVED": "approved_at = NOW(), approved_by = :by,",
        "ACTIVE": "activated_at = NOW(),",
        "SUSPENDED": "suspended_at = NOW(),",
        "TERMINATED": "terminated_at = NOW(),",
    }.get(new_status, "")

    await session.execute(
        text(f"""UPDATE distributors
                    SET status = :s, {stamps} status_reason = :r,
                        updated_at = NOW()
                  WHERE id = :d"""),
        {"s": new_status, "r": reason, "d": str(distributor_id),
         **({"by": str(actor.id) if actor else None} if "approved_by" in stamps
            else {})},
    )

    # A terminated or suspended distributor stops trading, so its stock
    # location is closed to new movement. The balance stays; it has to be
    # reconciled and recovered, not made to vanish.
    if new_status in ("SUSPENDED", "TERMINATED") and dist["warehouse_id"]:
        await session.execute(
            text("UPDATE warehouses SET is_active = FALSE WHERE id = :w"),
            {"w": str(dist["warehouse_id"])})
    elif new_status == "ACTIVE" and dist["warehouse_id"]:
        await session.execute(
            text("UPDATE warehouses SET is_active = TRUE WHERE id = :w"),
            {"w": str(dist["warehouse_id"])})

    await audit(session, event_type=f"DISTRIBUTOR_{new_status}",
                entity_type="distributor", entity_id=distributor_id,
                distributor_id=distributor_id, actor=actor, reason=reason,
                old_value={"status": old}, new_value={"status": new_status})
    return {"id": str(distributor_id), "status": new_status, "changed": True,
            "was": old}


# ---------------------------------------------------------------------------
# Documents and qualifications
# ---------------------------------------------------------------------------

async def attach_document(
    session: AsyncSession, *, distributor_id: UUID, doc_type: str,
    filename: str, content_type: str, content: bytes,
    title: Optional[str] = None, issue_date: Optional[date] = None,
    expiry_date: Optional[date] = None, actor=None,
) -> dict:
    """Store a compliance document in the database.

    In the database rather than on disk for the same reason wallet receipts
    are: the container filesystem is replaced on every deploy, and a compliance
    document that disappears at the next release is not evidence of anything.
    """
    if content_type not in ALLOWED_DOCUMENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(f"Documents must be an image or PDF. "
                    f"{content_type or 'unknown type'} is not accepted."))
    if not content:
        raise HTTPException(status_code=400, detail="The file is empty.")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise HTTPException(
            status_code=400,
            detail=(f"The document is {len(content) / 1_048_576:.1f}MB; the "
                    f"limit is {MAX_DOCUMENT_BYTES // 1_048_576}MB."))

    await get_distributor(session, distributor_id)
    digest = hashlib.sha256(content).hexdigest()
    document_id = uuid4()
    await session.execute(
        text("""
            INSERT INTO distributor_documents
                (id, distributor_id, doc_type, title, filename, content_type,
                 byte_size, sha256, content, issue_date, expiry_date,
                 uploaded_by)
            VALUES (:id, :d, :dt, :t, :fn, :ct, :sz, :h, :blob, :iss, :exp, :by)
        """),
        {"id": str(document_id), "d": str(distributor_id), "dt": doc_type,
         "t": title, "fn": filename[:255], "ct": content_type,
         "sz": len(content), "h": digest, "blob": content,
         "iss": issue_date, "exp": expiry_date,
         "by": str(actor.id) if actor else None},
    )

    # The same file submitted twice is usually an accident, occasionally not.
    # Reported, never blocked.
    prior = (await session.execute(
        text("""SELECT d.distributor_code, dd.doc_type
                  FROM distributor_documents dd
                  JOIN distributors d ON d.id = dd.distributor_id
                 WHERE dd.sha256 = :h AND dd.id <> :self LIMIT 1"""),
        {"h": digest, "self": str(document_id)},
    )).mappings().first()

    await audit(session, event_type="DOCUMENT_UPLOADED",
                entity_type="distributor_document", entity_id=document_id,
                distributor_id=distributor_id, actor=actor,
                new_value={"doc_type": doc_type, "bytes": len(content),
                           "duplicate_of": dict(prior) if prior else None})
    return {"id": str(document_id), "doc_type": doc_type,
            "byte_size": len(content), "sha256": digest,
            "duplicate_of": dict(prior) if prior else None}


async def verify_document(
    session: AsyncSession, *, document_id: UUID, verified: bool, user,
    note: Optional[str] = None,
) -> dict:
    row = (await session.execute(
        text("""SELECT id, distributor_id, doc_type, verification_status,
                       uploaded_by
                  FROM distributor_documents WHERE id = :id FOR UPDATE"""),
        {"id": str(document_id)},
    )).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    if str(row["uploaded_by"] or "") == str(user.id):
        raise HTTPException(
            status_code=403,
            detail=("You cannot verify a document you uploaded yourself. "
                    "Verification is a second pair of eyes or it is nothing."))

    status = "VERIFIED" if verified else "REJECTED"
    await session.execute(
        text("""UPDATE distributor_documents
                   SET verification_status = :s, verified_by = :by,
                       verified_at = NOW(), verification_note = :n
                 WHERE id = :id"""),
        {"s": status, "by": str(user.id), "n": note, "id": str(document_id)},
    )
    await audit(session, event_type=f"DOCUMENT_{status}",
                entity_type="distributor_document", entity_id=document_id,
                distributor_id=row["distributor_id"], actor=user, reason=note,
                old_value={"status": row["verification_status"]},
                new_value={"status": status})
    return {"id": str(document_id), "verification_status": status}


async def add_qualification(
    session: AsyncSession, *, distributor_id: UUID, qualification_type: str,
    holder_name: Optional[str] = None, institution: Optional[str] = None,
    certificate_number: Optional[str] = None,
    registration_number: Optional[str] = None,
    issuing_authority: Optional[str] = None, issue_date: Optional[date] = None,
    expiry_date: Optional[date] = None, document_id: Optional[UUID] = None,
    actor=None,
) -> dict:
    await get_distributor(session, distributor_id)
    qualification_id = uuid4()
    await session.execute(
        text("""
            INSERT INTO distributor_qualifications
                (id, distributor_id, qualification_type, holder_name,
                 institution, certificate_number, registration_number,
                 issuing_authority, issue_date, expiry_date, document_id)
            VALUES (:id, :d, :qt, :hn, :inst, :cn, :rn, :ia, :iss, :exp, :doc)
        """),
        {"id": str(qualification_id), "d": str(distributor_id),
         "qt": qualification_type, "hn": holder_name, "inst": institution,
         "cn": certificate_number, "rn": registration_number,
         "ia": issuing_authority, "iss": issue_date, "exp": expiry_date,
         "doc": str(document_id) if document_id else None},
    )
    await audit(session, event_type="QUALIFICATION_ADDED",
                entity_type="distributor_qualification",
                entity_id=qualification_id, distributor_id=distributor_id,
                actor=actor, new_value={"type": qualification_type})
    return {"id": str(qualification_id),
            "qualification_type": qualification_type}


async def expiring_documents(
    session: AsyncSession, *, within_days: int = 90,
) -> list[dict]:
    """Documents and qualifications approaching or past expiry.

    One query over both, because to whoever has to chase them they are the same
    problem, and two separate lists is how one of them gets forgotten.
    """
    rows = (await session.execute(
        text("""
            SELECT 'document' AS kind, dd.id, dd.doc_type AS label,
                   dd.expiry_date, d.id AS distributor_id,
                   d.distributor_code, d.legal_name,
                   (dd.expiry_date - CURRENT_DATE) AS days_left
              FROM distributor_documents dd
              JOIN distributors d ON d.id = dd.distributor_id
             WHERE dd.expiry_date IS NOT NULL
               AND dd.expiry_date <= CURRENT_DATE + CAST(:w AS INTEGER)
               AND d.status NOT IN ('TERMINATED','REJECTED')
            UNION ALL
            SELECT 'qualification', dq.id, dq.qualification_type,
                   dq.expiry_date, d.id, d.distributor_code, d.legal_name,
                   (dq.expiry_date - CURRENT_DATE)
              FROM distributor_qualifications dq
              JOIN distributors d ON d.id = dq.distributor_id
             WHERE dq.expiry_date IS NOT NULL
               AND dq.expiry_date <= CURRENT_DATE + CAST(:w AS INTEGER)
               AND d.status NOT IN ('TERMINATED','REJECTED')
             ORDER BY expiry_date
        """), {"w": within_days},
    )).mappings().all()
    return [{**dict(r), "id": str(r["id"]),
             "distributor_id": str(r["distributor_id"]),
             "expired": r["days_left"] < 0} for r in rows]
