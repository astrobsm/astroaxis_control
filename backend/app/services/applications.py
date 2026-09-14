"""Territory applications: request, review, decide, assign.

WHY AN APPLICATION EXISTS AT ALL
--------------------------------
`geography.assign_territory` already grants a territory. This module does not
replace it -- it is what a grant should normally come FROM. The difference is
that an assignment records WHAT was decided and an application records WHY: who
asked, what the reviewer was shown, what the conflicts were at that moment, and
who signed it off. When a distributor disputes a territory two years later, the
assignment says only that someone had the right to it from a date.

WHAT THE REVIEWER IS SHOWN
--------------------------
`review_packet` gathers eligibility, compliance and conflicts into one answer,
and `decide` stores the conflicts as they stood at the moment of the decision.
A reviewer who granted a territory over a known overlap cannot later say the
system never told them -- and one who was never shown a conflict can prove it.

WHAT THIS MODULE REFUSES TO DECIDE
----------------------------------
It does not score an applicant out of the running. `assess_eligibility` returns
`may_be_approved` separately from `score`, and this module reports both and lets
a person decide. A low score is an argument, not a verdict, and the specification
is explicit that a weighted average must never absorb a mandatory failure.

Exclusivity itself is NOT enforced here. It is enforced by the database triggers
in migration z5678901234y, which see every path including the ones that do not
come through this module. What this module does is ask the same question BEFORE
the decision, so a reviewer sees the conflict as advice rather than meeting it
as an error after clicking approve.
"""
from __future__ import annotations

import json
import secrets
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import distributors as dsvc
from app.services import geography as geo
from app.services.geography import audit

OPEN_STATUSES = ("DRAFT", "SUBMITTED", "UNDER_REVIEW")


def _reference() -> str:
    return f"TA-{date.today():%Y%m}-{secrets.token_hex(3).upper()}"


# ---------------------------------------------------------------------------
# Conflicts
# ---------------------------------------------------------------------------

async def territory_conflicts(
    session: AsyncSession, *, territory_id: UUID,
    distributor_id: Optional[UUID] = None,
) -> list[dict]:
    """Who else already holds ground this territory covers.

    Overlap with a territory held by the SAME distributor is not a conflict --
    it is one promise made twice, which is untidy but not a contradiction. So
    `distributor_id` is excluded when given.

    Each conflict is marked `blocking` or not, and the distinction is the whole
    point. A conflict is BLOCKING when both territories are exclusive: the
    database will refuse that assignment, so no amount of acknowledging makes
    it possible, and offering a reviewer an override would be a lie. Where
    either side is non-exclusive the overlap is legal -- two distributors may
    genuinely both sell into an LGA -- and the reviewer should be told before
    they decide rather than discover it afterwards.

    This is the same question the database trigger asks. It is asked here too
    so that a reviewer sees it before deciding rather than after.
    """
    rows = (await session.execute(
        text("""
            SELECT DISTINCT
                   l.name AS lga, s.name AS state,
                   t.code AS territory_code, t.name AS territory_name,
                   t.is_exclusive,
                   d.id AS distributor_id, d.distributor_code, d.legal_name,
                   ta.assigned_from
              FROM territory_lgas mine
              JOIN territory_lgas theirs ON theirs.lga_id = mine.lga_id
                   AND theirs.territory_id <> mine.territory_id
              JOIN territories t ON t.id = theirs.territory_id
              JOIN lgas l ON l.id = mine.lga_id
              JOIN states s ON s.id = l.state_id
              JOIN territory_assignments ta ON ta.territory_id = t.id
                   AND ta.assigned_to IS NULL AND ta.status = 'ACTIVE'
              JOIN distributors d ON d.id = ta.distributor_id
             WHERE mine.territory_id = :t
               AND (CAST(:d AS uuid) IS NULL OR d.id <> CAST(:d AS uuid))
             ORDER BY l.name
        """),
        {"t": str(territory_id),
         "d": str(distributor_id) if distributor_id else None},
    )).mappings().all()

    mine_exclusive = (await session.execute(
        text("SELECT is_exclusive FROM territories WHERE id = :t"),
        {"t": str(territory_id)})).scalar()

    return [
        dict(r) | {
            "distributor_id": str(r["distributor_id"]),
            "blocking": bool(mine_exclusive and r["is_exclusive"]),
        }
        for r in rows
    ]


async def review_packet(session: AsyncSession, *, application_id: UUID) -> dict:
    """Everything a reviewer needs, in one answer.

    Assembled rather than summarised: eligibility, compliance and conflicts are
    reported as the three separate judgements they are. Collapsing them into a
    single number would let a good score hide a missing licence, which is the
    failure mode section 6 of the specification exists to prevent.
    """
    app = await get_application(session, application_id)

    eligibility = await dsvc.assess_eligibility(
        session, distributor_id=UUID(app["distributor_id"]))

    # Compliance is optional context: a distributor may legitimately be
    # applying for ground before their facility has been assessed.
    try:
        from app.services import compliance as csvc
        compliance = await csvc.compliance_summary(
            session, UUID(app["distributor_id"]))
    except Exception:  # pragma: no cover - compliance is additive context
        compliance = None

    conflicts = []
    if app["territory_id"]:
        conflicts = await territory_conflicts(
            session, territory_id=UUID(app["territory_id"]),
            distributor_id=UUID(app["distributor_id"]))

    blocking = list(eligibility["blocking_conditions"])
    if compliance and not compliance["fit_to_trade"]:
        blocking += compliance["blocking_conditions"]

    hard = [c for c in conflicts if c["blocking"]]
    advisory = [c for c in conflicts if not c["blocking"]]

    return {
        "application": app,
        "eligibility": eligibility,
        "compliance": compliance,
        "conflicts": conflicts,
        # Two different things. A blocking conflict cannot be granted at all --
        # the database refuses it. An advisory one is legal and is shown so the
        # reviewer is not surprised by it later.
        "blocking_conflicts": hard,
        "advisory_conflicts": advisory,
        "can_be_granted": not hard,
        "blocking_conditions": blocking,
        "recommendation": (
            "CANNOT_GRANT" if hard else
            "REVIEW" if blocking or advisory else
            "GRANT"),
    }


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

async def apply_for_territory(
    session: AsyncSession, *, distributor_id: UUID, territory_id: UUID,
    requested_from: Optional[date] = None, requested_exclusive: bool = True,
    statement: Optional[str] = None, actor=None,
) -> dict:
    """Record a request for a territory. Nothing is granted here."""
    dist = (await session.execute(
        text("""SELECT id, distributor_code, legal_name, status
                  FROM distributors WHERE id = :d"""),
        {"d": str(distributor_id)})).mappings().first()
    if dist is None:
        raise HTTPException(status_code=404, detail="Distributor not found.")
    if dist["status"] in ("TERMINATED", "REJECTED"):
        raise HTTPException(
            status_code=400,
            detail=(f"{dist['legal_name']} is {dist['status'].lower()} and "
                    f"cannot apply for territory."))

    territory = (await session.execute(
        text("""SELECT id, code, name, status, is_exclusive
                  FROM territories WHERE id = :t"""),
        {"t": str(territory_id)})).mappings().first()
    if territory is None:
        raise HTTPException(status_code=404, detail="Territory not found.")
    if territory["status"] == "RETIRED":
        raise HTTPException(
            status_code=400,
            detail=(f"Territory {territory['code']} is retired. Its history is "
                    f"kept, but it is no longer traded."))

    existing = (await session.execute(
        text(f"""SELECT application_number, status
                   FROM distributor_applications
                  WHERE distributor_id = :d AND territory_id = :t
                    AND kind = 'TERRITORY'
                    AND status IN {OPEN_STATUSES}"""),
        {"d": str(distributor_id), "t": str(territory_id)})).mappings().first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=(f"{existing['application_number']} is already open for "
                    f"{territory['code']} ({existing['status'].lower()}). "
                    f"A second request for the same ground is a duplicate, not "
                    f"a stronger case."))

    held = (await session.execute(
        text("""SELECT 1 FROM territory_assignments
                 WHERE territory_id = :t AND distributor_id = :d
                   AND assigned_to IS NULL AND status = 'ACTIVE'"""),
        {"t": str(territory_id), "d": str(distributor_id)})).first()
    if held:
        raise HTTPException(
            status_code=409,
            detail=(f"{dist['legal_name']} already holds {territory['code']}."))

    application_id = uuid4()
    number = _reference()
    await session.execute(
        text("""
            INSERT INTO distributor_applications
                (id, application_number, distributor_id, kind, territory_id,
                 requested_exclusive, requested_from, applicant_statement,
                 status, submitted_at, created_by)
            VALUES (:id, :num, :d, 'TERRITORY', :t, :x, :from, :stmt,
                    'SUBMITTED', NOW(), :by)
        """),
        {"id": str(application_id), "num": number, "d": str(distributor_id),
         "t": str(territory_id),
         "x": requested_exclusive and territory["is_exclusive"],
         "from": requested_from, "stmt": statement,
         "by": str(actor.id) if actor else None},
    )
    await audit(session, event_type="TERRITORY_APPLIED",
                entity_type="distributor_application", entity_id=application_id,
                distributor_id=distributor_id, territory_id=territory_id,
                actor=actor,
                new_value={"application_number": number,
                           "territory": territory["code"]})
    return {"id": str(application_id), "application_number": number,
            "status": "SUBMITTED", "territory": territory["code"],
            "distributor": dist["distributor_code"]}


async def get_application(session: AsyncSession, application_id: UUID) -> dict:
    row = (await session.execute(
        text("""SELECT a.*, d.distributor_code, d.legal_name, d.status
                           AS distributor_status,
                       t.code AS territory_code, t.name AS territory_name,
                       t.is_exclusive AS territory_exclusive,
                       t.status AS territory_status,
                       dec.full_name AS decided_by_name,
                       rev.full_name AS reviewed_by_name
                  FROM distributor_applications a
                  JOIN distributors d ON d.id = a.distributor_id
                  LEFT JOIN territories t ON t.id = a.territory_id
                  LEFT JOIN users dec ON dec.id = a.decided_by
                  LEFT JOIN users rev ON rev.id = a.reviewed_by
                 WHERE a.id = :a"""),
        {"a": str(application_id)})).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Application not found.")
    out = dict(row)
    for key in ("id", "distributor_id", "territory_id", "assignment_id",
                "decided_by", "reviewed_by", "created_by"):
        if out.get(key) is not None:
            out[key] = str(out[key])
    return out


async def begin_review(
    session: AsyncSession, *, application_id: UUID, actor=None,
) -> dict:
    """Take an application off the queue.

    The eligibility score is snapshotted HERE, at the start of review, so the
    figure the reviewer worked from is the figure on the record -- even if a
    document is verified or a weight changes while they are reading.
    """
    app = await get_application(session, application_id)
    if app["status"] != "SUBMITTED":
        raise HTTPException(
            status_code=400,
            detail=(f"Only a submitted application can be taken up for review; "
                    f"{app['application_number']} is "
                    f"{app['status'].lower()}."))

    eligibility = await dsvc.assess_eligibility(
        session, distributor_id=UUID(app["distributor_id"]))

    await session.execute(
        text("""UPDATE distributor_applications
                   SET status = 'UNDER_REVIEW', reviewed_by = :by,
                       reviewed_at = NOW(), eligibility_score = :score,
                       eligibility_band = :band,
                       score_breakdown = CAST(:bd AS JSONB)
                 WHERE id = :a"""),
        {"by": str(actor.id) if actor else None,
         "score": eligibility["score"], "band": eligibility["band"],
         "bd": json.dumps(eligibility, default=str), "a": str(application_id)},
    )
    await audit(session, event_type="TERRITORY_APPLICATION_REVIEW_STARTED",
                entity_type="distributor_application", entity_id=application_id,
                distributor_id=UUID(app["distributor_id"]),
                territory_id=(UUID(app["territory_id"])
                              if app["territory_id"] else None),
                actor=actor,
                new_value={"eligibility_score": eligibility["score"],
                           "band": eligibility["band"]})
    return {"id": str(application_id), "status": "UNDER_REVIEW",
            "eligibility": eligibility}


async def decide(
    session: AsyncSession, *, application_id: UUID, approve: bool,
    note: str, assigned_from: Optional[date] = None,
    acknowledge_conflicts: bool = False, actor=None,
) -> dict:
    """Approve or refuse. Approval creates the assignment in the same breath.

    The two are one act: an approved application that did not produce an
    assignment, or an assignment with no application behind it, are both ways of
    losing the connection between a decision and its effect. They are written in
    the same transaction, and the application records which assignment it
    produced.

    Conflicts are re-checked at decision time rather than trusted from the
    review, because the ground can be granted to someone else while an
    application sits in the queue. Approving over one requires saying so
    explicitly -- the database will refuse it anyway, but a reviewer should
    meet that as a decision they are making, not as an error message.
    """
    if not note or len(note.strip()) < 3:
        raise HTTPException(
            status_code=400,
            detail="A decision needs a reason that someone can read later.")

    app = await get_application(session, application_id)
    if app["status"] not in ("SUBMITTED", "UNDER_REVIEW"):
        raise HTTPException(
            status_code=400,
            detail=(f"{app['application_number']} is {app['status'].lower()} "
                    f"and has already been decided."))
    if app["kind"] != "TERRITORY":
        raise HTTPException(
            status_code=400,
            detail="This endpoint decides territory applications.")

    conflicts = await territory_conflicts(
        session, territory_id=UUID(app["territory_id"]),
        distributor_id=UUID(app["distributor_id"]))

    hard = [c for c in conflicts if c["blocking"]]
    advisory = [c for c in conflicts if not c["blocking"]]

    if approve and hard:
        # Not overridable, and not offered as overridable. The database refuses
        # this assignment, so an "approve anyway" here would be a button that
        # cannot work.
        names = ", ".join(sorted({c["lga"] for c in hard}))
        holders = ", ".join(sorted({c["legal_name"] for c in hard}))
        raise HTTPException(
            status_code=409,
            detail=(f"This cannot be granted: it would promise {names} to two "
                    f"distributors at once, and {holders} already holds that "
                    f"ground exclusively. End the other assignment or narrow "
                    f"one territory's coverage first."))

    if approve and advisory and not acknowledge_conflicts:
        names = ", ".join(sorted({c["lga"] for c in advisory}))
        holders = ", ".join(sorted({c["legal_name"] for c in advisory}))
        raise HTTPException(
            status_code=409,
            detail=(f"{holders} also sells into {names} through an overlapping "
                    f"territory. That is permitted because the overlap is not "
                    f"exclusive on both sides, but confirm you intend it "
                    f"before granting."))

    assignment = None
    if approve:
        # The exclusivity triggers are the real guarantee; this call is where
        # they fire. A conflict raises before anything is written.
        assignment = await geo.assign_territory(
            session, territory_id=UUID(app["territory_id"]),
            distributor_id=UUID(app["distributor_id"]),
            assigned_from=assigned_from or app["requested_from"],
            reason=f"{app['application_number']}: {note.strip()}", actor=actor)

    await session.execute(
        text("""UPDATE distributor_applications
                   SET status = :s, decided_by = :by, decided_at = NOW(),
                       decision_note = :note,
                       assignment_id = CAST(:asg AS uuid),
                       conflicts_at_decision = CAST(:cf AS JSONB)
                 WHERE id = :a"""),
        {"s": "APPROVED" if approve else "REJECTED",
         "by": str(actor.id) if actor else None, "note": note.strip(),
         "asg": assignment["id"] if assignment else None,
         "cf": json.dumps(conflicts, default=str),
         "a": str(application_id)},
    )
    await audit(session,
                event_type=("TERRITORY_APPLICATION_APPROVED" if approve
                            else "TERRITORY_APPLICATION_REJECTED"),
                entity_type="distributor_application", entity_id=application_id,
                distributor_id=UUID(app["distributor_id"]),
                territory_id=UUID(app["territory_id"]), actor=actor,
                reason=note.strip(),
                old_value={"status": app["status"]},
                new_value={"status": "APPROVED" if approve else "REJECTED",
                           "assignment_id": (assignment["id"] if assignment
                                             else None),
                           "conflicts_acknowledged": bool(
                               advisory and acknowledge_conflicts)})
    return {
        "id": str(application_id),
        "application_number": app["application_number"],
        "status": "APPROVED" if approve else "REJECTED",
        "assignment": assignment,
        "conflicts_acknowledged": advisory if acknowledge_conflicts else [],
    }


async def withdraw(
    session: AsyncSession, *, application_id: UUID, reason: str, actor=None,
) -> dict:
    """Take an application back. The row stays; it is not deleted."""
    if not reason or len(reason.strip()) < 3:
        raise HTTPException(status_code=400, detail="A reason is required.")
    app = await get_application(session, application_id)
    if app["status"] not in OPEN_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"{app['application_number']} is already "
                   f"{app['status'].lower()}.")

    await session.execute(
        text("""UPDATE distributor_applications
                   SET status = 'WITHDRAWN', decision_note = :r,
                       decided_at = NOW(), decided_by = :by
                 WHERE id = :a"""),
        {"r": reason.strip(), "by": str(actor.id) if actor else None,
         "a": str(application_id)},
    )
    await audit(session, event_type="TERRITORY_APPLICATION_WITHDRAWN",
                entity_type="distributor_application", entity_id=application_id,
                distributor_id=UUID(app["distributor_id"]),
                territory_id=(UUID(app["territory_id"])
                              if app["territory_id"] else None),
                actor=actor, reason=reason.strip(),
                old_value={"status": app["status"]},
                new_value={"status": "WITHDRAWN"})
    return {"id": str(application_id), "status": "WITHDRAWN"}


async def list_applications(
    session: AsyncSession, *, status: Optional[str] = None,
    distributor_id: Optional[UUID] = None,
    territory_id: Optional[UUID] = None, open_only: bool = False,
) -> list[dict]:
    clauses = ["a.kind = 'TERRITORY'"]
    params: dict = {}
    if open_only:
        clauses.append(f"a.status IN {OPEN_STATUSES}")
    elif status:
        clauses.append("a.status = :status")
        params["status"] = status
    if distributor_id:
        clauses.append("a.distributor_id = :d")
        params["d"] = str(distributor_id)
    if territory_id:
        clauses.append("a.territory_id = :t")
        params["t"] = str(territory_id)

    rows = (await session.execute(
        text(f"""SELECT a.id, a.application_number, a.status, a.submitted_at,
                        a.decided_at, a.eligibility_score, a.eligibility_band,
                        a.requested_from, a.requested_exclusive,
                        d.distributor_code, d.legal_name,
                        t.code AS territory_code, t.name AS territory_name,
                        s.name AS state
                   FROM distributor_applications a
                   JOIN distributors d ON d.id = a.distributor_id
                   LEFT JOIN territories t ON t.id = a.territory_id
                   LEFT JOIN states s ON s.id = t.state_id
                  WHERE {' AND '.join(clauses)}
                  ORDER BY a.submitted_at DESC NULLS LAST"""),
        params)).mappings().all()
    return [dict(r) | {"id": str(r["id"])} for r in rows]


async def distributor_territories(
    session: AsyncSession, distributor_id: UUID,
) -> dict:
    """What this distributor holds now, and what it has held before."""
    rows = (await session.execute(
        text("""SELECT ta.id, ta.assigned_from, ta.assigned_to, ta.status,
                       ta.is_exclusive, ta.reason, ta.end_reason,
                       t.id AS territory_id, t.code AS territory_code,
                       t.name AS territory_name, s.name AS state,
                       (SELECT COUNT(*) FROM territory_lgas tl
                         WHERE tl.territory_id = t.id) AS lga_count
                  FROM territory_assignments ta
                  JOIN territories t ON t.id = ta.territory_id
                  LEFT JOIN states s ON s.id = t.state_id
                 WHERE ta.distributor_id = :d
                 ORDER BY ta.assigned_to NULLS FIRST, ta.assigned_from DESC"""),
        {"d": str(distributor_id)})).mappings().all()
    current = [dict(r) | {"id": str(r["id"]),
                          "territory_id": str(r["territory_id"])}
               for r in rows if r["assigned_to"] is None
               and r["status"] == "ACTIVE"]
    past = [dict(r) | {"id": str(r["id"]),
                       "territory_id": str(r["territory_id"])}
            for r in rows if r["assigned_to"] is not None
            or r["status"] != "ACTIVE"]
    return {"current": current, "past": past}
