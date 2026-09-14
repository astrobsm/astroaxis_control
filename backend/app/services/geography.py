"""Territories, targets and assignments.

Administrative geography (country / state / LGA) is a set of facts. A territory
is a commercial decision laid on top of it, and the two are deliberately
separate: management can re-cut Lagos into six zones or two without anyone
pretending the map of Nigeria changed.

THREE RULES THIS MODULE EXISTS TO KEEP
--------------------------------------
1. **A target is never edited.** Raising one closes the old row and opens a new
   one with an effective date and a reason. A report for August therefore keeps
   August's target after October's was raised -- which is the difference between
   a performance history and a rewritten one.

2. **An assignment is never edited.** Reassigning a territory closes the
   outgoing row and opens an incoming one. Historical sales stay attached to
   the distributor who actually made them.

3. **One live exclusive holder per territory**, enforced by a partial unique
   index in the database. Application-level checks get skipped on the one code
   path nobody tested; an index does not.

WHY THE DEFAULT TARGET IS NOT A CONSTANT IN THIS FILE
-----------------------------------------------------
N1,000,000 per territory per month is a commercial assumption, not a fact, and
territories differ in population, market maturity and product availability. It
is read from configuration, applied only when a territory is created without an
explicit target, and overridable per territory with a recorded reason -- so the
number can be argued with rather than discovered in source code.
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ledger import money

# The starting assumption for a new territory, not a rule. See the docstring.
DEFAULT_MONTHLY_TARGET = Decimal(
    os.getenv("TERRITORY_DEFAULT_MONTHLY_TARGET", "1000000"))


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

async def audit(
    session: AsyncSession, *, event_type: str, entity_type: str,
    entity_id: Optional[UUID] = None, distributor_id: Optional[UUID] = None,
    territory_id: Optional[UUID] = None, actor=None,
    old_value: Optional[dict] = None, new_value: Optional[dict] = None,
    reason: Optional[str] = None, ip_address: str = "", user_agent: str = "",
) -> None:
    """Append to the distributor domain's own immutable trail.

    Lands in the caller's transaction: an audit row that survived a rolled-back
    reassignment would describe something that never happened.
    """
    await session.execute(
        text("""
            INSERT INTO distributor_audit_logs
                (id, event_type, entity_type, entity_id, distributor_id,
                 territory_id, actor_user_id, actor_label, old_value,
                 new_value, reason, ip_address, user_agent)
            VALUES (gen_random_uuid(), :e, :et, :eid, :did, :tid, :a, :al,
                    CAST(:ov AS JSONB), CAST(:nv AS JSONB), :r, :ip, :ua)
        """),
        {"e": event_type, "et": entity_type,
         "eid": str(entity_id) if entity_id else None,
         "did": str(distributor_id) if distributor_id else None,
         "tid": str(territory_id) if territory_id else None,
         "a": str(actor.id) if actor else None,
         "al": getattr(actor, "full_name", None),
         "ov": json.dumps(old_value, default=str) if old_value else None,
         "nv": json.dumps(new_value, default=str) if new_value else None,
         "r": reason, "ip": (ip_address or "")[:64] or None,
         "ua": (user_agent or "")[:500] or None},
    )


# ---------------------------------------------------------------------------
# Territories
# ---------------------------------------------------------------------------

async def create_territory(
    session: AsyncSession, *, code: str, name: str, state_id: UUID,
    lga_ids: Optional[list[UUID]] = None, region_id: Optional[UUID] = None,
    description: Optional[str] = None, towns: Optional[str] = None,
    boundary_note: Optional[str] = None, is_exclusive: bool = True,
    monthly_target: Optional[Decimal] = None, actor=None,
) -> dict:
    """Define a commercial territory and open its first target period."""
    state = (await session.execute(
        text("SELECT id, name FROM states WHERE id = :s"),
        {"s": str(state_id)},
    )).mappings().first()
    if state is None:
        raise HTTPException(status_code=404, detail="That state does not exist.")

    clash = (await session.execute(
        text("SELECT name FROM territories WHERE lower(code) = lower(:c)"),
        {"c": code},
    )).first()
    if clash:
        raise HTTPException(
            status_code=409,
            detail=f"Territory code {code!r} is already used by {clash.name!r}.")

    territory_id = uuid4()
    await session.execute(
        text("""
            INSERT INTO territories
                (id, code, name, state_id, region_id, description, towns,
                 boundary_note, status, is_exclusive, created_by)
            VALUES (:id, :c, :n, :s, :r, :d, :t, :b, 'AVAILABLE', :x, :by)
        """),
        {"id": str(territory_id), "c": code.upper(), "n": name,
         "s": str(state_id), "r": str(region_id) if region_id else None,
         "d": description, "t": towns, "b": boundary_note,
         "x": is_exclusive, "by": str(actor.id) if actor else None},
    )

    if lga_ids:
        await set_territory_coverage(
            session, territory_id=territory_id, lga_ids=lga_ids, actor=actor)

    await set_target(
        session, territory_id=territory_id,
        monthly_target=(monthly_target if monthly_target is not None
                        else DEFAULT_MONTHLY_TARGET),
        effective_from=date.today().replace(day=1),
        reason="Initial target set when the territory was created",
        actor=actor)

    await audit(session, event_type="TERRITORY_CREATED",
                entity_type="territory", entity_id=territory_id,
                territory_id=territory_id, actor=actor,
                new_value={"code": code.upper(), "name": name,
                           "state": state["name"]})
    return {"id": str(territory_id), "code": code.upper(), "name": name}


async def set_territory_coverage(
    session: AsyncSession, *, territory_id: UUID, lga_ids: list[UUID],
    actor=None,
) -> dict:
    """Replace the LGAs a territory covers.

    An LGA may legitimately appear in more than one territory -- a large LGA is
    often split between commercial zones -- so this is not treated as a clash.
    What IS reported is the overlap, because it is nearly always worth a human
    knowing about.
    """
    territory = await get_territory(session, territory_id)

    overlaps = (await session.execute(
        text("""SELECT t.code, t.name, l.name AS lga
                  FROM territory_lgas tl
                  JOIN territories t ON t.id = tl.territory_id
                  JOIN lgas l ON l.id = tl.lga_id
                 WHERE tl.lga_id = ANY(CAST(:ids AS uuid[]))
                   AND tl.territory_id <> :tid
                   AND t.status <> 'RETIRED'"""),
        {"ids": [str(i) for i in lga_ids], "tid": str(territory_id)},
    )).mappings().all()

    await session.execute(
        text("DELETE FROM territory_lgas WHERE territory_id = :t"),
        {"t": str(territory_id)})
    for lga_id in lga_ids:
        await session.execute(
            text("""INSERT INTO territory_lgas (territory_id, lga_id)
                    VALUES (:t, :l) ON CONFLICT DO NOTHING"""),
            {"t": str(territory_id), "l": str(lga_id)})

    await audit(session, event_type="TERRITORY_COVERAGE_SET",
                entity_type="territory", entity_id=territory_id,
                territory_id=territory_id, actor=actor,
                new_value={"lga_count": len(lga_ids),
                           "overlaps": [dict(o) for o in overlaps]})
    return {
        "territory": territory["code"],
        "lga_count": len(lga_ids),
        "overlaps": [
            {"territory": o["code"], "name": o["name"], "lga": o["lga"]}
            for o in overlaps
        ],
    }


async def get_territory(session: AsyncSession, territory_id: UUID) -> dict:
    row = (await session.execute(
        text("SELECT * FROM territories WHERE id = :id"),
        {"id": str(territory_id)},
    )).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Territory not found.")
    return dict(row)


# ---------------------------------------------------------------------------
# Targets -- versioned, never edited
# ---------------------------------------------------------------------------

async def set_target(
    session: AsyncSession, *, territory_id: UUID, monthly_target: Decimal,
    effective_from: date, reason: Optional[str] = None, actor=None,
    approved_by: Optional[UUID] = None,
) -> dict:
    """Open a new target period, closing whatever was in force.

    The old row is closed the day BEFORE the new one starts, never deleted and
    never rewritten, so every past month keeps the target it was actually
    measured against.
    """
    amount = money(monthly_target)
    if amount < 0:
        raise HTTPException(
            status_code=400, detail="A target cannot be negative.")

    await get_territory(session, territory_id)

    current = (await session.execute(
        text("""SELECT id, monthly_target, effective_from
                  FROM territory_targets
                 WHERE territory_id = :t AND effective_to IS NULL
                 FOR UPDATE"""),
        {"t": str(territory_id)},
    )).mappings().first()

    if current is not None:
        if effective_from <= current["effective_from"]:
            raise HTTPException(
                status_code=400,
                detail=(f"A target starting {effective_from} would begin "
                        f"before the one already in force "
                        f"({current['effective_from']}). Targets move "
                        f"forwards; to correct a mistake, supersede it from "
                        f"today rather than back-dating over history."))
        await session.execute(
            text("""UPDATE territory_targets SET effective_to = :d
                     WHERE id = :id"""),
            {"d": effective_from - timedelta(days=1), "id": str(current["id"])})

    target_id = uuid4()
    await session.execute(
        text("""
            INSERT INTO territory_targets
                (id, territory_id, monthly_target, effective_from, reason,
                 approved_by, created_by)
            VALUES (:id, :t, :amt, :from, :r, :ap, :by)
        """),
        {"id": str(target_id), "t": str(territory_id), "amt": str(amount),
         "from": effective_from, "r": reason,
         "ap": str(approved_by) if approved_by else None,
         "by": str(actor.id) if actor else None},
    )

    await audit(
        session, event_type="TERRITORY_TARGET_SET", entity_type="territory",
        entity_id=territory_id, territory_id=territory_id, actor=actor,
        old_value=({"monthly_target": str(money(current["monthly_target"]))}
                   if current else None),
        new_value={"monthly_target": str(amount),
                   "effective_from": str(effective_from)},
        reason=reason)
    return {"id": str(target_id), "monthly_target": str(amount),
            "effective_from": str(effective_from)}


async def target_on(
    session: AsyncSession, territory_id: UUID, on: Optional[date] = None,
) -> Decimal:
    """The target in force on a given date. Zero if none was ever set.

    Date-aware on purpose: performance for a past month must be measured
    against the target that applied then, not against today's.
    """
    row = (await session.execute(
        text("""SELECT monthly_target FROM territory_targets
                 WHERE territory_id = :t AND effective_from <= :d
                   AND (effective_to IS NULL OR effective_to >= :d)
                 ORDER BY effective_from DESC LIMIT 1"""),
        {"t": str(territory_id), "d": on or date.today()},
    )).first()
    return money(row.monthly_target) if row else Decimal("0.00")


async def target_history(
    session: AsyncSession, territory_id: UUID,
) -> list[dict]:
    rows = (await session.execute(
        text("""SELECT tt.monthly_target, tt.effective_from, tt.effective_to,
                       tt.reason, tt.created_at, u.full_name AS set_by
                  FROM territory_targets tt
                  LEFT JOIN users u ON u.id = tt.created_by
                 WHERE tt.territory_id = :t
                 ORDER BY tt.effective_from DESC"""),
        {"t": str(territory_id)},
    )).mappings().all()
    return [{**dict(r), "monthly_target": str(money(r["monthly_target"]))}
            for r in rows]


# ---------------------------------------------------------------------------
# Assignment -- exclusivity is a database guarantee
# ---------------------------------------------------------------------------

async def assign_territory(
    session: AsyncSession, *, territory_id: UUID, distributor_id: UUID,
    assigned_from: Optional[date] = None, reason: Optional[str] = None,
    actor=None,
) -> dict:
    """Give a territory to a distributor.

    The partial unique index does the real work; this raises a message a person
    can act on rather than letting a constraint violation reach the user.
    """
    territory = await get_territory(session, territory_id)
    if territory["status"] in ("RETIRED", "SUSPENDED"):
        raise HTTPException(
            status_code=400,
            detail=(f"Territory {territory['code']} is "
                    f"{territory['status'].lower()} and cannot be assigned."))

    distributor = (await session.execute(
        text("SELECT id, distributor_code, legal_name, status "
             "FROM distributors WHERE id = :d"),
        {"d": str(distributor_id)},
    )).mappings().first()
    if distributor is None:
        raise HTTPException(status_code=404, detail="Distributor not found.")
    if distributor["status"] not in ("APPROVED", "ACTIVE"):
        raise HTTPException(
            status_code=400,
            detail=(f"{distributor['legal_name']} is "
                    f"{distributor['status'].lower()}. Only an approved or "
                    f"active distributor can hold a territory."))

    if territory["is_exclusive"]:
        holder = (await session.execute(
            text("""SELECT d.legal_name, d.distributor_code, ta.assigned_from
                      FROM territory_assignments ta
                      JOIN distributors d ON d.id = ta.distributor_id
                     WHERE ta.territory_id = :t AND ta.assigned_to IS NULL
                       AND ta.status = 'ACTIVE' AND ta.is_exclusive"""),
            {"t": str(territory_id)},
        )).mappings().first()
        if holder is not None:
            raise HTTPException(
                status_code=409,
                detail=(f"Territory {territory['code']} has been held "
                        f"exclusively by {holder['legal_name']} "
                        f"({holder['distributor_code']}) since "
                        f"{holder['assigned_from']}. End that assignment "
                        f"before reassigning it."))

    assignment_id = uuid4()
    start = assigned_from or date.today()
    await session.execute(
        text("""
            INSERT INTO territory_assignments
                (id, territory_id, distributor_id, assigned_from,
                 is_exclusive, status, assigned_by, reason)
            VALUES (:id, :t, :d, :from, :x, 'ACTIVE', :by, :r)
        """),
        {"id": str(assignment_id), "t": str(territory_id),
         "d": str(distributor_id), "from": start,
         "x": territory["is_exclusive"],
         "by": str(actor.id) if actor else None, "r": reason},
    )
    await session.execute(
        text("UPDATE territories SET status = 'ASSIGNED', updated_at = NOW() "
             "WHERE id = :t"),
        {"t": str(territory_id)})

    await audit(session, event_type="TERRITORY_ASSIGNED",
                entity_type="territory_assignment", entity_id=assignment_id,
                distributor_id=distributor_id, territory_id=territory_id,
                actor=actor, reason=reason,
                new_value={"territory": territory["code"],
                           "distributor": distributor["distributor_code"],
                           "from": str(start)})
    return {"id": str(assignment_id), "territory": territory["code"],
            "distributor": distributor["distributor_code"],
            "assigned_from": str(start)}


async def end_assignment(
    session: AsyncSession, *, assignment_id: UUID, ended_on: Optional[date] = None,
    reason: str, actor=None,
) -> dict:
    """Close an assignment. The row stays; historical sales stay attached."""
    row = (await session.execute(
        text("""SELECT ta.*, t.code AS territory_code
                  FROM territory_assignments ta
                  JOIN territories t ON t.id = ta.territory_id
                 WHERE ta.id = :id FOR UPDATE"""),
        {"id": str(assignment_id)},
    )).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Assignment not found.")
    if row["assigned_to"] is not None:
        raise HTTPException(
            status_code=400,
            detail=f"That assignment already ended on {row['assigned_to']}.")

    end = ended_on or date.today()
    if end < row["assigned_from"]:
        raise HTTPException(
            status_code=400,
            detail=(f"An assignment cannot end ({end}) before it began "
                    f"({row['assigned_from']})."))

    await session.execute(
        text("""UPDATE territory_assignments
                   SET assigned_to = :d, status = 'ENDED', ended_by = :by,
                       end_reason = :r
                 WHERE id = :id"""),
        {"d": end, "by": str(actor.id) if actor else None, "r": reason,
         "id": str(assignment_id)},
    )
    # The territory becomes available again only if nobody else still holds it.
    remaining = (await session.execute(
        text("""SELECT COUNT(*) AS n FROM territory_assignments
                 WHERE territory_id = :t AND assigned_to IS NULL
                   AND status = 'ACTIVE'"""),
        {"t": str(row["territory_id"])},
    )).first()
    if not remaining.n:
        await session.execute(
            text("""UPDATE territories SET status = 'AVAILABLE',
                           updated_at = NOW()
                     WHERE id = :t AND status = 'ASSIGNED'"""),
            {"t": str(row["territory_id"])})

    await audit(session, event_type="TERRITORY_ASSIGNMENT_ENDED",
                entity_type="territory_assignment", entity_id=assignment_id,
                distributor_id=row["distributor_id"],
                territory_id=row["territory_id"], actor=actor, reason=reason,
                old_value={"assigned_from": str(row["assigned_from"])},
                new_value={"assigned_to": str(end)})
    return {"id": str(assignment_id), "territory": row["territory_code"],
            "assigned_to": str(end)}


async def release_all_assignments(
    session: AsyncSession, *, distributor_id: UUID, reason: str, actor=None,
) -> list[dict]:
    """End every live assignment a distributor holds, and say which.

    Called when a distributor is terminated. Without this a terminated
    distributor keeps holding its territories forever: the exclusivity index is
    satisfied by its row, so nobody else can be granted that ground, and the
    territory silently stops being sellable with no record of why.

    The assignments are ENDED, never deleted -- historical sales stay attached
    to whoever actually made them.
    """
    rows = (await session.execute(
        text("""SELECT ta.id, t.code AS territory_code
                  FROM territory_assignments ta
                  JOIN territories t ON t.id = ta.territory_id
                 WHERE ta.distributor_id = :d
                   AND ta.assigned_to IS NULL AND ta.status = 'ACTIVE'"""),
        {"d": str(distributor_id)},
    )).mappings().all()

    released = []
    for row in rows:
        await end_assignment(session, assignment_id=row["id"], reason=reason,
                             actor=actor)
        released.append({"assignment_id": str(row["id"]),
                         "territory": row["territory_code"]})
    return released


async def current_holder(
    session: AsyncSession, territory_id: UUID,
) -> Optional[dict]:
    row = (await session.execute(
        text("""SELECT d.id, d.distributor_code, d.legal_name,
                       ta.assigned_from, ta.id AS assignment_id
                  FROM territory_assignments ta
                  JOIN distributors d ON d.id = ta.distributor_id
                 WHERE ta.territory_id = :t AND ta.assigned_to IS NULL
                   AND ta.status = 'ACTIVE'
                 ORDER BY ta.assigned_from DESC LIMIT 1"""),
        {"t": str(territory_id)},
    )).mappings().first()
    return dict(row) if row else None


async def assignment_history(
    session: AsyncSession, territory_id: UUID,
) -> list[dict]:
    """Who held this territory, and when. Never overwritten (spec section 60)."""
    rows = (await session.execute(
        text("""SELECT ta.assigned_from, ta.assigned_to, ta.status,
                       ta.reason, ta.end_reason, d.distributor_code,
                       d.legal_name, u.full_name AS assigned_by
                  FROM territory_assignments ta
                  JOIN distributors d ON d.id = ta.distributor_id
                  LEFT JOIN users u ON u.id = ta.assigned_by
                 WHERE ta.territory_id = :t
                 ORDER BY ta.assigned_from DESC"""),
        {"t": str(territory_id)},
    )).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Roll-ups
# ---------------------------------------------------------------------------

async def target_rollup(
    session: AsyncSession, *, on: Optional[date] = None,
) -> dict:
    """Territory targets summed to state, region and national level.

    Computed from the target in force on the given date, so a roll-up for a
    past month reflects that month's targets rather than today's.
    """
    when = on or date.today()
    rows = (await session.execute(
        text("""
            SELECT t.id, t.code, t.name, t.status,
                   s.name AS state, s.geopolitical_zone AS zone,
                   r.name AS region,
                   (SELECT tt.monthly_target FROM territory_targets tt
                     WHERE tt.territory_id = t.id
                       AND tt.effective_from <= :d
                       AND (tt.effective_to IS NULL OR tt.effective_to >= :d)
                     ORDER BY tt.effective_from DESC LIMIT 1) AS target,
                   (SELECT d.legal_name FROM territory_assignments ta
                      JOIN distributors d ON d.id = ta.distributor_id
                     WHERE ta.territory_id = t.id AND ta.assigned_to IS NULL
                       AND ta.status = 'ACTIVE'
                     ORDER BY ta.assigned_from DESC LIMIT 1) AS holder
              FROM territories t
              JOIN states s ON s.id = t.state_id
              LEFT JOIN regions r ON r.id = t.region_id
             WHERE t.status <> 'RETIRED'
             ORDER BY s.name, t.code
        """), {"d": when},
    )).mappings().all()

    by_state: dict[str, Decimal] = {}
    by_zone: dict[str, Decimal] = {}
    national = Decimal("0.00")
    unassigned = 0
    for r in rows:
        amount = money(r["target"] or 0)
        national += amount
        by_state[r["state"]] = by_state.get(r["state"], Decimal("0.00")) + amount
        if r["zone"]:
            by_zone[r["zone"]] = by_zone.get(r["zone"], Decimal("0.00")) + amount
        if not r["holder"]:
            unassigned += 1

    return {
        "as_of": str(when),
        "national_target": str(national),
        "territory_count": len(rows),
        "unassigned_territories": unassigned,
        "by_state": [{"state": k, "target": str(v)}
                     for k, v in sorted(by_state.items())],
        "by_zone": [{"zone": k, "target": str(v)}
                    for k, v in sorted(by_zone.items())],
        "territories": [
            {"id": str(r["id"]), "code": r["code"], "name": r["name"],
             "state": r["state"], "region": r["region"], "status": r["status"],
             "target": str(money(r["target"] or 0)), "holder": r["holder"]}
            for r in rows
        ],
    }
