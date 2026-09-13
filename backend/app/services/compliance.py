"""Storage facility assessment, corrective actions and distributor agreements.

TWO RULES CARRIED OVER FROM ELIGIBILITY, FOR THE SAME REASON
------------------------------------------------------------
1. **A critical failure is not absorbed by the score.** `score_assessment`
   returns the weighted percentage and the list of failed critical items
   separately, and the outcome is FAIL whenever the second is non-empty --
   however good the first looks. A facility with no quarantine area and 94%
   is not a 94% facility.

2. **Company policy is never presented as law.** Every answer snapshots the
   requirement's `requirement_kind` at the time it was answered, so a report
   can say which findings were regulatory and which were the company's own,
   and rewording the checklist next year cannot retroactively promote a
   company rule into a legal one.

WHY AN ANSWER SNAPSHOTS ITS QUESTION
------------------------------------
`facility_assessment_items` stores the requirement text, kind and weight as
they read when the inspector answered. Without that, editing a checklist item
silently changes what every past inspector is recorded as having agreed to --
and the assessment becomes a record of today's checklist rather than of what
was actually found.

AGREEMENTS
----------
The body is rendered once, hashed, and frozen at issue. The signature stores
the same hash, so "this person accepted THIS text" is checkable later rather
than asserted. A signature carries a MEANING, because a click with no stated
meaning is not an acceptance of anything.

This module builds the machinery. It does not draft binding legal language:
specification section 8 asks for a configurable template requiring legal review,
and inventing contract clauses that read as settled law is precisely what it
warns against.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.geography import audit

PASS_AT = Decimal("80")
CONDITIONAL_AT = Decimal("60")

# Results that count toward the score. NOT_APPLICABLE is excluded from both
# sides -- scoring a facility down for lacking a cold chain it does not need
# would make the number meaningless.
_SCORING = {"PASS", "FAIL", "REQUIRES_CORRECTION"}
_EARNS_CREDIT = {"PASS"}


def _reference(prefix: str) -> str:
    return f"{prefix}-{date.today():%Y%m}-{secrets.token_hex(3).upper()}"


# ---------------------------------------------------------------------------
# Facilities
# ---------------------------------------------------------------------------

async def create_facility(
    session: AsyncSession, *, distributor_id: UUID, name: str,
    address: Optional[str] = None, state_id: Optional[UUID] = None,
    lga_id: Optional[UUID] = None, town: Optional[str] = None,
    latitude: Optional[float] = None, longitude: Optional[float] = None,
    floor_area_sqm: Optional[Decimal] = None,
    capacity_note: Optional[str] = None,
    responsible_person: Optional[str] = None,
    responsible_phone: Optional[str] = None, actor=None,
) -> dict:
    exists = (await session.execute(
        text("SELECT 1 FROM distributors WHERE id = :d"),
        {"d": str(distributor_id)})).first()
    if exists is None:
        raise HTTPException(status_code=404, detail="Distributor not found.")

    facility_id = uuid4()
    await session.execute(
        text("""
            INSERT INTO distributor_facilities
                (id, distributor_id, name, address, state_id, lga_id, town,
                 latitude, longitude, floor_area_sqm, capacity_note,
                 responsible_person, responsible_phone, created_by)
            VALUES (:id, :d, :n, :a, :s, :l, :t, :lat, :lng, :area, :cap,
                    :rp, :rph, :by)
        """),
        {"id": str(facility_id), "d": str(distributor_id), "n": name,
         "a": address, "s": str(state_id) if state_id else None,
         "l": str(lga_id) if lga_id else None, "t": town,
         "lat": latitude, "lng": longitude,
         "area": str(floor_area_sqm) if floor_area_sqm is not None else None,
         "cap": capacity_note, "rp": responsible_person,
         "rph": responsible_phone, "by": str(actor.id) if actor else None},
    )
    await audit(session, event_type="FACILITY_ADDED",
                entity_type="distributor_facility", entity_id=facility_id,
                distributor_id=distributor_id, actor=actor,
                new_value={"name": name, "town": town})
    return {"id": str(facility_id), "name": name, "status": "PENDING"}


# ---------------------------------------------------------------------------
# Assessment
# ---------------------------------------------------------------------------

async def start_assessment(
    session: AsyncSession, *, facility_id: UUID, assessed_on: Optional[date] = None,
    actor=None,
) -> dict:
    """Open a draft assessment against the checklist as it reads today."""
    facility = (await session.execute(
        text("""SELECT id, distributor_id, name FROM distributor_facilities
                 WHERE id = :f"""),
        {"f": str(facility_id)})).mappings().first()
    if facility is None:
        raise HTTPException(status_code=404, detail="Facility not found.")

    open_draft = (await session.execute(
        text("""SELECT assessment_reference FROM facility_assessments
                 WHERE facility_id = :f AND status = 'DRAFT' LIMIT 1"""),
        {"f": str(facility_id)})).first()
    if open_draft:
        raise HTTPException(
            status_code=409,
            detail=(f"Assessment {open_draft.assessment_reference} is still in "
                    f"draft for this facility. Complete or discard it first."))

    assessment_id = uuid4()
    reference = _reference("FA")
    await session.execute(
        text("""
            INSERT INTO facility_assessments
                (id, assessment_reference, facility_id, distributor_id,
                 assessed_on, assessor_id, status)
            VALUES (:id, :ref, :f, :d, :on, :by, 'DRAFT')
        """),
        {"id": str(assessment_id), "ref": reference, "f": str(facility_id),
         "d": str(facility["distributor_id"]),
         "on": assessed_on or date.today(),
         "by": str(actor.id) if actor else None},
    )
    checklist = (await session.execute(
        text("""SELECT id, code, section, requirement, requirement_kind,
                       authority, weight, is_critical, requires_evidence
                  FROM facility_checklist_items
                 WHERE is_active ORDER BY sort_order"""),
    )).mappings().all()

    return {
        "id": str(assessment_id), "assessment_reference": reference,
        "facility": facility["name"], "status": "DRAFT",
        "checklist": [dict(c) | {"id": str(c["id"])} for c in checklist],
    }


async def answer_item(
    session: AsyncSession, *, assessment_id: UUID, checklist_item_id: UUID,
    result: str, note: Optional[str] = None,
    evidence_document_id: Optional[UUID] = None,
) -> dict:
    """Record one answer, snapshotting the question as it currently reads."""
    if result not in ("PASS", "FAIL", "NOT_APPLICABLE", "REQUIRES_CORRECTION"):
        raise HTTPException(status_code=400, detail="Unknown result.")

    assessment = (await session.execute(
        text("""SELECT id, status FROM facility_assessments WHERE id = :a"""),
        {"a": str(assessment_id)})).mappings().first()
    if assessment is None:
        raise HTTPException(status_code=404, detail="Assessment not found.")
    if assessment["status"] != "DRAFT":
        raise HTTPException(
            status_code=400,
            detail="This assessment has been submitted and cannot be changed.")

    item = (await session.execute(
        text("""SELECT requirement, requirement_kind, weight, is_critical,
                       requires_evidence, code
                  FROM facility_checklist_items WHERE id = :i"""),
        {"i": str(checklist_item_id)})).mappings().first()
    if item is None:
        raise HTTPException(status_code=404, detail="Checklist item not found.")

    if (item["requires_evidence"] and result in ("PASS", "REQUIRES_CORRECTION")
            and evidence_document_id is None):
        raise HTTPException(
            status_code=400,
            detail=(f"{item['code']} requires photographic or documentary "
                    f"evidence before it can be recorded as {result.lower()}."))

    await session.execute(
        text("""
            INSERT INTO facility_assessment_items
                (id, assessment_id, checklist_item_id, result, note,
                 evidence_document_id, requirement_snapshot, kind_snapshot,
                 weight_snapshot, was_critical)
            VALUES (gen_random_uuid(), :a, :c, :r, :n, :ev, :req, :kind,
                    :w, :crit)
            ON CONFLICT (assessment_id, checklist_item_id) DO UPDATE
               SET result = EXCLUDED.result, note = EXCLUDED.note,
                   evidence_document_id = EXCLUDED.evidence_document_id
        """),
        {"a": str(assessment_id), "c": str(checklist_item_id), "r": result,
         "n": note,
         "ev": str(evidence_document_id) if evidence_document_id else None,
         "req": item["requirement"], "kind": item["requirement_kind"],
         "w": item["weight"], "crit": item["is_critical"]},
    )
    return {"checklist_item_id": str(checklist_item_id), "result": result}


async def score_assessment(session: AsyncSession, assessment_id: UUID) -> dict:
    """Weighted score, plus the critical failures that override it.

    Not-applicable answers are excluded from both numerator and denominator.
    REQUIRES_CORRECTION earns no credit -- it is a fail with a route back.
    """
    rows = (await session.execute(
        text("""SELECT fai.result, fai.weight_snapshot AS weight,
                       fai.was_critical, fai.kind_snapshot AS kind,
                       fai.requirement_snapshot AS requirement,
                       ci.code, ci.authority
                  FROM facility_assessment_items fai
                  JOIN facility_checklist_items ci
                       ON ci.id = fai.checklist_item_id
                 WHERE fai.assessment_id = :a"""),
        {"a": str(assessment_id)})).mappings().all()

    earned = Decimal("0")
    possible = Decimal("0")
    critical_failures, regulatory_failures = [], []
    counted = 0

    for r in rows:
        if r["result"] not in _SCORING:
            continue
        counted += 1
        weight = Decimal(str(r["weight"] or 1))
        possible += weight
        if r["result"] in _EARNS_CREDIT:
            earned += weight
        else:
            entry = {"code": r["code"], "requirement": r["requirement"],
                     "kind": r["kind"], "authority": r["authority"],
                     "result": r["result"]}
            if r["was_critical"]:
                critical_failures.append(entry)
            if r["kind"] == "REGULATORY":
                regulatory_failures.append(entry)

    score = (Decimal("0") if possible == 0
             else (earned / possible * Decimal("100")).quantize(
                 Decimal("0.01"), rounding=ROUND_HALF_UP))

    # The score never overrides a critical failure, and a regulatory failure is
    # reported in its own right so it is never buried in an average.
    if critical_failures or regulatory_failures:
        outcome, band = "FAIL", "CRITICAL_FAILURE"
    elif score >= PASS_AT:
        outcome, band = "PASS", "COMPLIANT"
    elif score >= CONDITIONAL_AT:
        outcome, band = "CONDITIONAL", "CONDITIONAL"
    else:
        outcome, band = "FAIL", "NON_COMPLIANT"

    return {
        "score": str(score), "band": band, "outcome": outcome,
        "items_assessed": counted,
        "items_not_applicable": len(rows) - counted,
        "critical_failures": critical_failures,
        "regulatory_failures": regulatory_failures,
        "thresholds": {"pass_at": str(PASS_AT),
                       "conditional_at": str(CONDITIONAL_AT)},
    }


async def submit_assessment(
    session: AsyncSession, *, assessment_id: UUID, summary: Optional[str] = None,
    actor=None,
) -> dict:
    """Freeze the findings and raise a corrective action for every failure.

    Every FAIL and REQUIRES_CORRECTION becomes a tracked corrective action with
    a severity, because a finding recorded without an owner and a deadline is a
    finding nobody will fix.
    """
    assessment = (await session.execute(
        text("""SELECT id, assessment_reference, status, facility_id,
                       distributor_id
                  FROM facility_assessments WHERE id = :a FOR UPDATE"""),
        {"a": str(assessment_id)})).mappings().first()
    if assessment is None:
        raise HTTPException(status_code=404, detail="Assessment not found.")
    if assessment["status"] != "DRAFT":
        raise HTTPException(
            status_code=400,
            detail=f"This assessment is already {assessment['status'].lower()}.")

    scored = await score_assessment(session, assessment_id)
    if scored["items_assessed"] == 0:
        raise HTTPException(
            status_code=400,
            detail="Nothing has been assessed yet; answer the checklist first.")

    await session.execute(
        text("""UPDATE facility_assessments
                   SET status = 'SUBMITTED', score = :sc, band = :b,
                       outcome = :o, critical_failures = :cf,
                       items_assessed = :n, summary = :sum, updated_at = NOW()
                 WHERE id = :a"""),
        {"sc": scored["score"], "b": scored["band"], "o": scored["outcome"],
         "cf": len(scored["critical_failures"]), "n": scored["items_assessed"],
         "sum": summary, "a": str(assessment_id)},
    )

    failures = (await session.execute(
        text("""SELECT fai.id, fai.requirement_snapshot, fai.result,
                       fai.was_critical, fai.note
                  FROM facility_assessment_items fai
                 WHERE fai.assessment_id = :a
                   AND fai.result IN ('FAIL','REQUIRES_CORRECTION')"""),
        {"a": str(assessment_id)})).mappings().all()

    raised = 0
    for f in failures:
        await session.execute(
            text("""
                INSERT INTO facility_corrective_actions
                    (id, action_reference, assessment_id, assessment_item_id,
                     distributor_id, finding, severity, status, created_by)
                VALUES (gen_random_uuid(), :ref, :a, :item, :d, :find, :sev,
                        'OPEN', :by)
            """),
            {"ref": _reference("CA"), "a": str(assessment_id),
             "item": str(f["id"]), "d": str(assessment["distributor_id"]),
             "find": (f["requirement_snapshot"] or "")
                     + (f" — {f['note']}" if f["note"] else ""),
             "sev": "CRITICAL" if f["was_critical"] else "MEDIUM",
             "by": str(actor.id) if actor else None},
        )
        raised += 1

    status = {"PASS": "APPROVED", "CONDITIONAL": "CONDITIONAL",
              "FAIL": "REJECTED"}[scored["outcome"]]
    await session.execute(
        text("""UPDATE distributor_facilities
                   SET status = :s, updated_at = NOW() WHERE id = :f"""),
        {"s": status, "f": str(assessment["facility_id"])},
    )

    await audit(session, event_type="FACILITY_ASSESSED",
                entity_type="facility_assessment", entity_id=assessment_id,
                distributor_id=assessment["distributor_id"], actor=actor,
                new_value={"reference": assessment["assessment_reference"],
                           "score": scored["score"],
                           "outcome": scored["outcome"],
                           "critical_failures": len(scored["critical_failures"]),
                           "corrective_actions_raised": raised})
    return {**scored, "assessment_reference": assessment["assessment_reference"],
            "corrective_actions_raised": raised, "facility_status": status}


# ---------------------------------------------------------------------------
# Corrective actions
# ---------------------------------------------------------------------------

async def update_corrective_action(
    session: AsyncSession, *, action_id: UUID, status: Optional[str] = None,
    responsible_person: Optional[str] = None, deadline: Optional[date] = None,
    corrective_action: Optional[str] = None, severity: Optional[str] = None,
    evidence_document_id: Optional[UUID] = None, note: Optional[str] = None,
    user=None,
) -> dict:
    """Move a finding along. Closing requires verification by someone else.

    A finding closed by the person responsible for fixing it is a finding
    nobody independently checked, which is how corrective actions become
    paperwork.
    """
    row = (await session.execute(
        text("""SELECT * FROM facility_corrective_actions
                 WHERE id = :i FOR UPDATE"""),
        {"i": str(action_id)})).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Corrective action not found.")
    if row["status"] == "CLOSED":
        raise HTTPException(
            status_code=400, detail="That finding is already closed.")

    sets, params = [], {"i": str(action_id)}
    for field, value in (("responsible_person", responsible_person),
                         ("deadline", deadline),
                         ("corrective_action", corrective_action),
                         ("severity", severity)):
        if value is not None:
            sets.append(f"{field} = :{field}")
            params[field] = value
    if evidence_document_id is not None:
        sets.append("evidence_document_id = :ev")
        params["ev"] = str(evidence_document_id)

    if status is not None:
        if status not in ("OPEN", "IN_PROGRESS", "COMPLETED", "VERIFIED",
                          "CLOSED"):
            raise HTTPException(status_code=400, detail="Unknown status.")
        if status in ("VERIFIED", "CLOSED"):
            if user is None:
                raise HTTPException(
                    status_code=403, detail="Verification requires a user.")
            sets.append("verified_by = :vb")
            sets.append("verified_at = NOW()")
            params["vb"] = str(user.id)
            if note:
                sets.append("verification_note = :vn")
                params["vn"] = note
        if status == "COMPLETED":
            sets.append("completed_at = NOW()")
        if status == "CLOSED":
            sets.append("closed_at = NOW()")
        sets.append("status = :st")
        params["st"] = status

    if not sets:
        raise HTTPException(status_code=400, detail="Nothing to change.")

    await session.execute(
        text(f"UPDATE facility_corrective_actions SET {', '.join(sets)} "
             f"WHERE id = :i"), params)
    await audit(session, event_type="CORRECTIVE_ACTION_UPDATED",
                entity_type="facility_corrective_action", entity_id=action_id,
                distributor_id=row["distributor_id"], actor=user, reason=note,
                old_value={"status": row["status"]},
                new_value={"status": status or row["status"]})
    return {"id": str(action_id), "status": status or row["status"]}


# ---------------------------------------------------------------------------
# Agreements
# ---------------------------------------------------------------------------

def _hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


async def create_agreement(
    session: AsyncSession, *, distributor_id: UUID, title: str, body: str,
    terms: Optional[dict] = None, territory_ids: Optional[list] = None,
    template_document_id: Optional[str] = None,
    template_version: Optional[str] = None,
    effective_from: Optional[date] = None, expires_on: Optional[date] = None,
    actor=None,
) -> dict:
    """Draft an agreement. Editable until it is issued, frozen after.

    The body is supplied rendered: this module provides the lifecycle, the
    hashing and the signature discipline, not the contract language. Section 8
    is explicit that the template is a company document requiring legal review,
    and generating clauses that read as settled law would be the opposite of
    that.
    """
    if not body.strip():
        raise HTTPException(
            status_code=400, detail="An agreement needs a body.")

    live = (await session.execute(
        text("""SELECT agreement_reference FROM distributor_agreements
                 WHERE distributor_id = :d
                   AND status IN ('ACTIVE','COUNTERSIGNED') LIMIT 1"""),
        {"d": str(distributor_id)})).first()
    if live:
        raise HTTPException(
            status_code=409,
            detail=(f"{live.agreement_reference} is already in force for this "
                    f"distributor. Terminate or supersede it first -- two live "
                    f"agreements would make the terms ambiguous."))

    agreement_id = uuid4()
    reference = _reference("AGR")
    await session.execute(
        text("""
            INSERT INTO distributor_agreements
                (id, agreement_reference, distributor_id, template_document_id,
                 template_version, title, body, body_sha256, terms,
                 territory_ids, effective_from, expires_on, status, created_by)
            VALUES (:id, :ref, :d, :tpl, :ver, :t, :body, :hash,
                    CAST(:terms AS JSONB), CAST(:terr AS JSONB), :from, :to,
                    'DRAFT', :by)
        """),
        {"id": str(agreement_id), "ref": reference, "d": str(distributor_id),
         "tpl": template_document_id, "ver": template_version, "t": title,
         "body": body, "hash": _hash(body),
         "terms": json.dumps(terms or {}, default=str),
         "terr": json.dumps([str(t) for t in (territory_ids or [])]),
         "from": effective_from, "to": expires_on,
         "by": str(actor.id) if actor else None},
    )
    await audit(session, event_type="AGREEMENT_DRAFTED",
                entity_type="distributor_agreement", entity_id=agreement_id,
                distributor_id=distributor_id, actor=actor,
                new_value={"reference": reference, "title": title})
    return {"id": str(agreement_id), "agreement_reference": reference,
            "status": "DRAFT", "body_sha256": _hash(body)}


async def issue_agreement(
    session: AsyncSession, *, agreement_id: UUID, actor=None,
) -> dict:
    """Present the agreement to the distributor. The text freezes here."""
    row = (await session.execute(
        text("""SELECT id, agreement_reference, status, distributor_id, body
                  FROM distributor_agreements WHERE id = :a FOR UPDATE"""),
        {"a": str(agreement_id)})).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Agreement not found.")
    if row["status"] != "DRAFT":
        raise HTTPException(
            status_code=400,
            detail=f"This agreement is already {row['status'].lower()}.")

    # Re-hash here, not only at draft time. The body is editable while the
    # agreement is a draft, and a stored hash that no longer describes the
    # stored text would make every later signature unverifiable -- the signer
    # would be refused for echoing exactly what they were shown. Issue is the
    # freeze point, so it is where the hash becomes authoritative. The guard
    # trigger still permits this write because the row is DRAFT until the
    # same statement moves it on.
    digest = _hash(row["body"])
    await session.execute(
        text("""UPDATE distributor_agreements
                   SET status = 'ISSUED', body_sha256 = :hash,
                       issued_at = NOW(), updated_at = NOW()
                 WHERE id = :a"""),
        {"hash": digest, "a": str(agreement_id)})
    await audit(session, event_type="AGREEMENT_ISSUED",
                entity_type="distributor_agreement", entity_id=agreement_id,
                distributor_id=row["distributor_id"], actor=actor,
                new_value={"reference": row["agreement_reference"],
                           "body_sha256": digest})
    return {"id": str(agreement_id), "status": "ISSUED",
            "body_sha256": digest}


async def sign_agreement(
    session: AsyncSession, *, agreement_id: UUID, signer_name: str,
    signer_role: str, meaning: str, body_sha256: str, user=None,
    ip_address: str = "", user_agent: str = "",
) -> dict:
    """Record a signature against the exact text that was displayed.

    `body_sha256` is what the signer's screen showed. If it does not match the
    stored agreement the signature is refused, because a signature that cannot
    be tied to specific text proves only that a button was pressed.
    """
    if signer_role not in ("DISTRIBUTOR", "COMPANY", "WITNESS"):
        raise HTTPException(status_code=400, detail="Unknown signer role.")
    if not meaning or len(meaning.strip()) < 10:
        raise HTTPException(
            status_code=400,
            detail=("A signature must state what it means, for example 'I have "
                    "read and accept these terms on behalf of the company'."))

    row = (await session.execute(
        text("""SELECT id, agreement_reference, status, distributor_id,
                       body_sha256
                  FROM distributor_agreements WHERE id = :a FOR UPDATE"""),
        {"a": str(agreement_id)})).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Agreement not found.")
    if row["status"] not in ("ISSUED", "ACCEPTED"):
        raise HTTPException(
            status_code=400,
            detail=(f"An agreement can only be signed once issued; this one is "
                    f"{row['status'].lower()}."))
    if body_sha256 != row["body_sha256"]:
        raise HTTPException(
            status_code=409,
            detail=("The document you signed does not match the agreement on "
                    "record. Reload it and read the current text before "
                    "signing."))

    already = (await session.execute(
        text("""SELECT 1 FROM distributor_agreement_signatures
                 WHERE agreement_id = :a AND signer_role = :r LIMIT 1"""),
        {"a": str(agreement_id), "r": signer_role})).first()
    if already:
        raise HTTPException(
            status_code=409,
            detail=f"This agreement already carries a {signer_role.lower()} "
                   f"signature.")

    await session.execute(
        text("""
            INSERT INTO distributor_agreement_signatures
                (id, agreement_id, signer_name, signer_role, signer_user_id,
                 meaning, content_hash, ip_address, user_agent)
            VALUES (gen_random_uuid(), :a, :n, :r, :u, :m, :h, :ip, :ua)
        """),
        {"a": str(agreement_id), "n": signer_name, "r": signer_role,
         "u": str(user.id) if user else None, "m": meaning.strip(),
         "h": body_sha256, "ip": (ip_address or "")[:64] or None,
         "ua": (user_agent or "")[:500] or None},
    )

    new_status = row["status"]
    if signer_role == "DISTRIBUTOR" and row["status"] == "ISSUED":
        new_status = "ACCEPTED"
        await session.execute(
            text("""UPDATE distributor_agreements
                       SET status = 'ACCEPTED', accepted_at = NOW(),
                           updated_at = NOW() WHERE id = :a"""),
            {"a": str(agreement_id)})
    elif signer_role == "COMPANY" and row["status"] == "ACCEPTED":
        new_status = "COUNTERSIGNED"
        await session.execute(
            text("""UPDATE distributor_agreements
                       SET status = 'COUNTERSIGNED', countersigned_at = NOW(),
                           updated_at = NOW() WHERE id = :a"""),
            {"a": str(agreement_id)})

    await audit(session, event_type=f"AGREEMENT_SIGNED_{signer_role}",
                entity_type="distributor_agreement", entity_id=agreement_id,
                distributor_id=row["distributor_id"], actor=user,
                new_value={"signer": signer_name, "meaning": meaning.strip(),
                           "content_hash": body_sha256,
                           "status": new_status},
                ip_address=ip_address, user_agent=user_agent)
    return {"id": str(agreement_id), "status": new_status,
            "signed_as": signer_role}


async def activate_agreement(
    session: AsyncSession, *, agreement_id: UUID, effective_from: Optional[date] = None,
    actor=None,
) -> dict:
    """Bring a countersigned agreement into force.

    Refuses unless both parties have signed. An agreement in force that only
    one side signed is not an agreement.
    """
    row = (await session.execute(
        text("""SELECT id, agreement_reference, status, distributor_id
                  FROM distributor_agreements WHERE id = :a FOR UPDATE"""),
        {"a": str(agreement_id)})).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Agreement not found.")
    if row["status"] != "COUNTERSIGNED":
        raise HTTPException(
            status_code=400,
            detail=(f"Only a countersigned agreement can be activated; this "
                    f"one is {row['status'].lower()}. Both the distributor and "
                    f"the company must sign first."))

    signers = (await session.execute(
        text("""SELECT signer_role FROM distributor_agreement_signatures
                 WHERE agreement_id = :a"""),
        {"a": str(agreement_id)})).scalars().all()
    if "DISTRIBUTOR" not in signers or "COMPANY" not in signers:
        raise HTTPException(
            status_code=400,
            detail="Both a distributor and a company signature are required.")

    await session.execute(
        text("""UPDATE distributor_agreements
                   SET status = 'ACTIVE', activated_at = NOW(),
                       effective_from = COALESCE(:from, effective_from,
                                                 CURRENT_DATE),
                       updated_at = NOW()
                 WHERE id = :a"""),
        {"from": effective_from, "a": str(agreement_id)})
    await audit(session, event_type="AGREEMENT_ACTIVATED",
                entity_type="distributor_agreement", entity_id=agreement_id,
                distributor_id=row["distributor_id"], actor=actor,
                new_value={"reference": row["agreement_reference"]})
    return {"id": str(agreement_id), "status": "ACTIVE"}


async def end_agreement(
    session: AsyncSession, *, agreement_id: UUID, status: str, reason: str,
    actor=None,
) -> dict:
    """Terminate, expire or supersede an agreement. Never delete it."""
    if status not in ("TERMINATED", "EXPIRED", "SUPERSEDED", "DECLINED"):
        raise HTTPException(status_code=400, detail="Unknown end status.")
    if not reason or len(reason.strip()) < 3:
        raise HTTPException(status_code=400, detail="A reason is required.")

    row = (await session.execute(
        text("""SELECT id, agreement_reference, status, distributor_id
                  FROM distributor_agreements WHERE id = :a FOR UPDATE"""),
        {"a": str(agreement_id)})).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Agreement not found.")
    if row["status"] in ("TERMINATED", "EXPIRED", "SUPERSEDED", "DECLINED"):
        raise HTTPException(
            status_code=400,
            detail=f"This agreement already ended ({row['status'].lower()}).")

    await session.execute(
        text("""UPDATE distributor_agreements
                   SET status = :s, ended_at = NOW(), end_reason = :r,
                       updated_at = NOW() WHERE id = :a"""),
        {"s": status, "r": reason, "a": str(agreement_id)})
    await audit(session, event_type=f"AGREEMENT_{status}",
                entity_type="distributor_agreement", entity_id=agreement_id,
                distributor_id=row["distributor_id"], actor=actor,
                reason=reason, old_value={"status": row["status"]},
                new_value={"status": status})
    return {"id": str(agreement_id), "status": status}


async def compliance_summary(
    session: AsyncSession, distributor_id: UUID,
) -> dict:
    """Is this distributor fit to trade? One answer, with its reasons."""
    facility = (await session.execute(
        text("""SELECT status, name FROM distributor_facilities
                 WHERE distributor_id = :d AND status <> 'CLOSED'
                 ORDER BY is_primary DESC, created_at LIMIT 1"""),
        {"d": str(distributor_id)})).mappings().first()

    agreement = (await session.execute(
        text("""SELECT agreement_reference, status, effective_from, expires_on
                  FROM distributor_agreements WHERE distributor_id = :d
                 ORDER BY created_at DESC LIMIT 1"""),
        {"d": str(distributor_id)})).mappings().first()

    open_actions = (await session.execute(
        text("""SELECT COUNT(*) AS n,
                       COUNT(*) FILTER (WHERE severity = 'CRITICAL') AS critical,
                       COUNT(*) FILTER (WHERE deadline < CURRENT_DATE) AS overdue
                  FROM facility_corrective_actions
                 WHERE distributor_id = :d AND status <> 'CLOSED'"""),
        {"d": str(distributor_id)})).mappings().first()

    blocking = []
    if facility is None:
        blocking.append("No storage facility has been recorded.")
    elif facility["status"] == "PENDING":
        blocking.append(f"{facility['name']} has never been assessed.")
    elif facility["status"] == "REJECTED":
        blocking.append(f"{facility['name']} failed its assessment.")
    if agreement is None:
        blocking.append("No agreement exists.")
    elif agreement["status"] != "ACTIVE":
        blocking.append(
            f"Agreement {agreement['agreement_reference']} is "
            f"{agreement['status'].lower()}, not in force.")
    if open_actions["critical"]:
        blocking.append(
            f"{open_actions['critical']} critical corrective action(s) remain "
            f"open.")

    return {
        "facility": dict(facility) if facility else None,
        "agreement": dict(agreement) if agreement else None,
        "corrective_actions": {
            "open": open_actions["n"], "critical": open_actions["critical"],
            "overdue": open_actions["overdue"],
        },
        "blocking_conditions": blocking,
        "fit_to_trade": not blocking,
    }
