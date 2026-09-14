"""Geography and territories.

Administrative geography (country / state / LGA) is reference data. Territories
are commercial decisions laid on top of it. The two are separate on purpose --
see app/services/geography.py.

PERMISSIONS
-----------
Reads require a distribution role -- admin, sales_staff or customer_care. A
territory names the distributor holding it and the target they are measured
against, which is commercial information rather than something every staff login
needs. Everything that changes a territory, a target or an assignment is
administrator-only.

This uses the EXISTING role enum through require_roles rather than adding a new
global role, because fifty routers depend on that enum and widening it is a
change to all of them. See app/api/auth.py for who is deliberately excluded.
"""
from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import (
    APIRouter, Depends, File, HTTPException, Query, Request, UploadFile,
)
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_admin, require_distribution_access
from app.db import get_session
from app.models import User
from app.services import applications as apps
from app.services import geography as geo

router = APIRouter(prefix="/api/geography", tags=["Geography & Territories"])


def _rows(items) -> list[dict]:
    out = []
    for r in items:
        d = dict(r)
        out.append({k: (str(v) if isinstance(v, Decimal) else v)
                    for k, v in d.items()})
    return out


def _client(request: Request) -> dict:
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() or (
        request.client.host if request.client else "")
    return {"ip_address": ip,
            "user_agent": request.headers.get("user-agent", "")}


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class TerritoryIn(BaseModel):
    code: str = Field(..., min_length=2, max_length=32)
    name: str = Field(..., min_length=2, max_length=160)
    state_id: UUID
    region_id: Optional[UUID] = None
    lga_ids: list[UUID] = Field(default_factory=list)
    description: Optional[str] = None
    towns: Optional[str] = None
    boundary_note: Optional[str] = None
    is_exclusive: bool = True
    monthly_target: Optional[Decimal] = Field(None, ge=0)


class CoverageIn(BaseModel):
    lga_ids: list[UUID]


class TargetIn(BaseModel):
    monthly_target: Decimal = Field(..., ge=0)
    effective_from: date
    reason: str = Field(..., min_length=3)


class AssignIn(BaseModel):
    distributor_id: UUID
    assigned_from: Optional[date] = None
    reason: Optional[str] = None


class EndAssignmentIn(BaseModel):
    ended_on: Optional[date] = None
    reason: str = Field(..., min_length=3)


class TerritoryStatusIn(BaseModel):
    status: str
    reason: str = Field(..., min_length=3)


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

@router.get("/states")
async def list_states(
    country: str = "NG",
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.execute(
        text("""SELECT s.id, s.code, s.name, s.capital, s.geopolitical_zone,
                       (SELECT COUNT(*) FROM lgas l WHERE l.state_id = s.id)
                           AS lga_count,
                       (SELECT COUNT(*) FROM territories t
                         WHERE t.state_id = s.id AND t.status <> 'RETIRED')
                           AS territory_count
                  FROM states s JOIN countries c ON c.id = s.country_id
                 WHERE c.iso2 = :c AND s.is_active
                 ORDER BY s.name"""),
        {"c": country.upper()},
    )).mappings().all()
    return {"states": _rows(rows)}


@router.get("/lgas")
async def list_lgas(
    state_id: Optional[UUID] = None,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    clause = "WHERE l.state_id = :s" if state_id else ""
    params = {"s": str(state_id)} if state_id else {}
    rows = (await session.execute(
        text(f"""SELECT l.id, l.name, l.code, l.state_id, s.name AS state
                   FROM lgas l JOIN states s ON s.id = l.state_id
                   {clause}
                  ORDER BY s.name, l.name"""), params,
    )).mappings().all()
    return {"lgas": _rows(rows)}


@router.post("/lgas/import")
async def import_lgas(
    file: UploadFile = File(...),
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Bulk-load LGAs from a CSV of `state_code,lga_name`.

    Nigeria has 774 LGAs. Only Lagos and the FCT area councils were seeded by
    the migration, because a misspelt or missing LGA silently corrupts every
    territory report built on it -- so the rest must come from an authoritative
    source (NBS or INEC) rather than be reproduced from memory.

    Idempotent: re-importing the same file adds nothing.
    """
    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(raw))

    added, skipped, errors = 0, 0, []
    for line_no, row in enumerate(reader, start=1):
        if not row or not row[0].strip():
            continue
        if row[0].strip().lower() in ("state_code", "state"):
            continue          # header
        if len(row) < 2:
            errors.append(f"line {line_no}: expected state_code,lga_name")
            continue
        state_code, lga_name = row[0].strip().upper(), row[1].strip()
        if not lga_name:
            errors.append(f"line {line_no}: empty LGA name")
            continue

        result = await session.execute(
            text("""INSERT INTO lgas (id, state_id, name)
                    SELECT gen_random_uuid(), s.id, :n
                      FROM states s JOIN countries c ON c.id = s.country_id
                     WHERE c.iso2 = 'NG' AND s.code = :sc
                    ON CONFLICT (state_id, name) DO NOTHING"""),
            {"n": lga_name, "sc": state_code})
        if result.rowcount:
            added += 1
        else:
            # Either it already exists, or the state code is unknown. Those are
            # very different problems, so they are distinguished.
            known = (await session.execute(
                text("""SELECT 1 FROM states s JOIN countries c
                               ON c.id = s.country_id
                         WHERE c.iso2 = 'NG' AND s.code = :sc"""),
                {"sc": state_code})).first()
            if known is None:
                errors.append(f"line {line_no}: unknown state code {state_code!r}")
            else:
                skipped += 1

    await session.commit()
    total = (await session.execute(text("SELECT COUNT(*) AS n FROM lgas"))).first()
    return {
        "added": added, "already_present": skipped,
        "errors": errors[:50], "error_count": len(errors),
        "total_lgas_now": total.n,
        "note": ("Nigeria has 774 LGAs in total. Verify against an "
                 "authoritative source before relying on territory coverage."),
    }


@router.get("/regions")
async def list_regions(
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.execute(
        text("SELECT id, code, name FROM regions WHERE is_active ORDER BY name"),
    )).mappings().all()
    return {"regions": _rows(rows)}


# ---------------------------------------------------------------------------
# Territories
# ---------------------------------------------------------------------------

@router.get("/territories")
async def list_territories(
    state_id: Optional[UUID] = None,
    status: Optional[str] = None,
    unassigned_only: bool = False,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    where = ["1 = 1"]
    params: dict = {}
    if state_id:
        where.append("t.state_id = :s")
        params["s"] = str(state_id)
    if status:
        where.append("t.status = :st")
        params["st"] = status
    if unassigned_only:
        where.append("""NOT EXISTS (SELECT 1 FROM territory_assignments ta
                                     WHERE ta.territory_id = t.id
                                       AND ta.assigned_to IS NULL
                                       AND ta.status = 'ACTIVE')""")

    rows = (await session.execute(
        text(f"""
            SELECT t.id, t.code, t.name, t.status, t.is_exclusive,
                   t.description, t.towns,
                   s.name AS state, s.geopolitical_zone AS zone,
                   r.name AS region,
                   (SELECT COUNT(*) FROM territory_lgas tl
                     WHERE tl.territory_id = t.id) AS lga_count,
                   (SELECT tt.monthly_target FROM territory_targets tt
                     WHERE tt.territory_id = t.id AND tt.effective_to IS NULL
                     LIMIT 1) AS monthly_target,
                   (SELECT d.legal_name FROM territory_assignments ta
                      JOIN distributors d ON d.id = ta.distributor_id
                     WHERE ta.territory_id = t.id AND ta.assigned_to IS NULL
                       AND ta.status = 'ACTIVE'
                     ORDER BY ta.assigned_from DESC LIMIT 1) AS holder,
                   (SELECT d.distributor_code FROM territory_assignments ta
                      JOIN distributors d ON d.id = ta.distributor_id
                     WHERE ta.territory_id = t.id AND ta.assigned_to IS NULL
                       AND ta.status = 'ACTIVE'
                     ORDER BY ta.assigned_from DESC LIMIT 1) AS holder_code
              FROM territories t
              JOIN states s ON s.id = t.state_id
              LEFT JOIN regions r ON r.id = t.region_id
             WHERE {' AND '.join(where)}
             ORDER BY s.name, t.code
        """), params,
    )).mappings().all()
    return {"territories": _rows(rows)}


@router.post("/territories", status_code=201)
async def create_territory(
    body: TerritoryIn,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await geo.create_territory(
        session, code=body.code, name=body.name, state_id=body.state_id,
        lga_ids=body.lga_ids, region_id=body.region_id,
        description=body.description, towns=body.towns,
        boundary_note=body.boundary_note, is_exclusive=body.is_exclusive,
        monthly_target=body.monthly_target, actor=user)
    await session.commit()
    return result


@router.get("/territories/{territory_id}")
async def territory_detail(
    territory_id: UUID,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    """One territory, with its coverage, current target and full history."""
    territory = await geo.get_territory(session, territory_id)
    lgas = (await session.execute(
        text("""SELECT l.id, l.name, tl.is_whole_lga
                  FROM territory_lgas tl JOIN lgas l ON l.id = tl.lga_id
                 WHERE tl.territory_id = :t ORDER BY l.name"""),
        {"t": str(territory_id)},
    )).mappings().all()
    return {
        "territory": {k: (str(v) if isinstance(v, Decimal) else v)
                      for k, v in territory.items()},
        "lgas": _rows(lgas),
        "current_target": str(await geo.target_on(session, territory_id)),
        "target_history": await geo.target_history(session, territory_id),
        "current_holder": await geo.current_holder(session, territory_id),
        "assignment_history": await geo.assignment_history(session, territory_id),
    }


@router.put("/territories/{territory_id}/coverage")
async def set_coverage(
    territory_id: UUID,
    body: CoverageIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await geo.set_territory_coverage(
        session, territory_id=territory_id, lga_ids=body.lga_ids, actor=user)
    await session.commit()
    return result


@router.post("/territories/{territory_id}/status")
async def set_territory_status(
    territory_id: UUID,
    body: TerritoryStatusIn,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Suspend, review or retire a territory. Never deletes it.

    A retired territory keeps its history: the sales made in it, the targets it
    carried and the distributors who held it all remain attached.
    """
    valid = {"AVAILABLE", "RESERVED", "SUSPENDED", "UNDER_REVIEW", "RETIRED"}
    if body.status not in valid:
        raise HTTPException(
            status_code=400,
            detail=f"Status must be one of: {', '.join(sorted(valid))}.")

    territory = await geo.get_territory(session, territory_id)
    if body.status in ("AVAILABLE", "RETIRED"):
        holder = await geo.current_holder(session, territory_id)
        if holder:
            raise HTTPException(
                status_code=409,
                detail=(f"{holder['legal_name']} still holds this territory. "
                        f"End the assignment before marking it "
                        f"{body.status.lower()}."))

    await session.execute(
        text("UPDATE territories SET status = :s, updated_at = NOW() "
             "WHERE id = :t"),
        {"s": body.status, "t": str(territory_id)})
    await geo.audit(
        session, event_type="TERRITORY_STATUS_CHANGED",
        entity_type="territory", entity_id=territory_id,
        territory_id=territory_id, actor=user, reason=body.reason,
        old_value={"status": territory["status"]},
        new_value={"status": body.status}, **_client(request))
    await session.commit()
    return {"id": str(territory_id), "status": body.status}


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------

@router.post("/territories/{territory_id}/targets")
async def set_target(
    territory_id: UUID,
    body: TargetIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Set a new monthly target, superseding the current one.

    The previous target is closed, not replaced: a report for a past month
    keeps the figure it was actually measured against.
    """
    result = await geo.set_target(
        session, territory_id=territory_id, monthly_target=body.monthly_target,
        effective_from=body.effective_from, reason=body.reason, actor=user,
        approved_by=user.id)
    await session.commit()
    return result


@router.get("/targets/rollup")
async def target_rollup(
    on: Optional[date] = None,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    """Territory targets summed to state, geopolitical zone and national."""
    return await geo.target_rollup(session, on=on)


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------

@router.post("/territories/{territory_id}/assign")
async def assign(
    territory_id: UUID,
    body: AssignIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await geo.assign_territory(
        session, territory_id=territory_id, distributor_id=body.distributor_id,
        assigned_from=body.assigned_from, reason=body.reason, actor=user)
    await session.commit()
    return result


@router.post("/assignments/{assignment_id}/end")
async def end_assignment(
    assignment_id: UUID,
    body: EndAssignmentIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await geo.end_assignment(
        session, assignment_id=assignment_id, ended_on=body.ended_on,
        reason=body.reason, actor=user)
    await session.commit()
    return result


@router.get("/audit")
async def audit_trail(
    territory_id: Optional[UUID] = None,
    distributor_id: Optional[UUID] = None,
    limit: int = Query(200, ge=1, le=1000),
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """The append-only trail for this domain. Nobody can edit it."""
    where = ["1 = 1"]
    params: dict = {"lim": limit}
    if territory_id:
        where.append("a.territory_id = :t")
        params["t"] = str(territory_id)
    if distributor_id:
        where.append("a.distributor_id = :d")
        params["d"] = str(distributor_id)
    rows = (await session.execute(
        text(f"""SELECT a.event_type, a.entity_type, a.actor_label, a.reason,
                        a.old_value, a.new_value, a.created_at,
                        u.full_name AS actor, t.code AS territory,
                        d.distributor_code AS distributor
                   FROM distributor_audit_logs a
                   LEFT JOIN users u ON u.id = a.actor_user_id
                   LEFT JOIN territories t ON t.id = a.territory_id
                   LEFT JOIN distributors d ON d.id = a.distributor_id
                  WHERE {' AND '.join(where)}
                  ORDER BY a.created_at DESC LIMIT :lim"""), params,
    )).mappings().all()
    return {"events": _rows(rows)}


# ---------------------------------------------------------------------------
# Territory applications -- phase 4
#
# A territory is normally granted BY DECIDING AN APPLICATION rather than by the
# direct /assign call above. Both write the same assignment row; the difference
# is that the application carries why, who reviewed it, and what conflicts were
# on the screen at the moment of the decision.
# ---------------------------------------------------------------------------

class ApplyIn(BaseModel):
    distributor_id: UUID
    requested_from: Optional[date] = None
    requested_exclusive: bool = True
    statement: Optional[str] = None


class DecisionIn(BaseModel):
    approve: bool
    note: str = Field(..., min_length=3)
    assigned_from: Optional[date] = None
    # Approving over a known conflict has to be said out loud. The database
    # refuses it regardless; this makes it a decision rather than an error.
    acknowledge_conflicts: bool = False


class WithdrawIn(BaseModel):
    reason: str = Field(..., min_length=3)


@router.post("/territories/{territory_id}/applications", status_code=201)
async def apply_for_territory(
    territory_id: UUID,
    body: ApplyIn,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    """Request a territory. Nothing is granted here."""
    result = await apps.apply_for_territory(
        session, distributor_id=body.distributor_id, territory_id=territory_id,
        requested_from=body.requested_from,
        requested_exclusive=body.requested_exclusive,
        statement=body.statement, actor=user)
    await session.commit()
    return result


@router.get("/applications")
async def list_applications(
    status: Optional[str] = None,
    distributor_id: Optional[UUID] = None,
    territory_id: Optional[UUID] = None,
    open_only: bool = False,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    rows = await apps.list_applications(
        session, status=status, distributor_id=distributor_id,
        territory_id=territory_id, open_only=open_only)
    return {"applications": _rows(rows)}


@router.get("/applications/{application_id}")
async def application_detail(
    application_id: UUID,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    """Everything a reviewer needs, as three separate judgements.

    `eligibility`, `compliance` and `conflicts` are reported side by side and
    never combined into one number -- a good score must not be able to hide a
    missing licence or an overlapping grant.
    """
    return await apps.review_packet(session, application_id=application_id)


@router.post("/applications/{application_id}/review")
async def begin_review(
    application_id: UUID,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Take an application up. Snapshots the score the reviewer works from."""
    result = await apps.begin_review(
        session, application_id=application_id, actor=user)
    await session.commit()
    return result


@router.post("/applications/{application_id}/decide")
async def decide_application(
    application_id: UUID,
    body: DecisionIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Approve or refuse. Approval creates the assignment in the same act."""
    result = await apps.decide(
        session, application_id=application_id, approve=body.approve,
        note=body.note, assigned_from=body.assigned_from,
        acknowledge_conflicts=body.acknowledge_conflicts, actor=user)
    await session.commit()
    return result


@router.post("/applications/{application_id}/withdraw")
async def withdraw_application(
    application_id: UUID,
    body: WithdrawIn,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    result = await apps.withdraw(
        session, application_id=application_id, reason=body.reason, actor=user)
    await session.commit()
    return result


@router.get("/territories/{territory_id}/conflicts")
async def territory_conflicts(
    territory_id: UUID,
    distributor_id: Optional[UUID] = None,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    """Who else already holds ground this territory covers.

    Exclusivity is enforced per LGA, not per territory, because two territories
    can cover the same LGA -- and each would satisfy a per-territory check while
    together promising the same ground twice.
    """
    rows = await apps.territory_conflicts(
        session, territory_id=territory_id, distributor_id=distributor_id)
    return {"conflicts": _rows(rows), "has_conflicts": bool(rows)}


@router.get("/distributors/{distributor_id}/territories")
async def distributor_territories(
    distributor_id: UUID,
    user: User = Depends(require_distribution_access),
    session: AsyncSession = Depends(get_session),
):
    """What this distributor holds now, and what it held before."""
    result = await apps.distributor_territories(session, distributor_id)
    return {"current": _rows(result["current"]), "past": _rows(result["past"])}
