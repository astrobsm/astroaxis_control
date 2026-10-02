"""Hiding a member of staff from the lists their name appears on.

WHAT THIS IS FOR
================
Staff names are shown in a lot of places that have nothing to do with
employment records: the birthday panel on the dashboard, attendance boards,
pick-lists, the payroll table. There are ordinary reasons to want somebody off
those lists -- they have resigned, they are suspended pending a query, the
record is a duplicate created by a bad import, or a bereavement makes a
birthday reminder cruel.

WHAT IT IS NOT FOR
==================
Hiding is a DISPLAY decision. It is deliberately powerless over anything else:

  * it does not end employment -- that is `is_active`;
  * it does not affect pay. The payroll queries ignore it completely, and the
    payroll screen marks a hidden person rather than dropping them. Somebody
    who is suspended is usually still owed wages, and a person made invisible
    by mistake or by malice must not thereby become unpayable;
  * it is not deletion. The record is untouched and the person can be
    restored in one action.

WHY A REASON IS REQUIRED
========================
The difference between a legitimate hiding and covering something up is
whether anyone can see afterwards which it was. The reason is enforced by a
CHECK constraint in the database, not by this module and not by the form, and
every hiding and every restoration appends a row to an append-only log naming
the person who did it. Nobody can quietly remove a colleague from view.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MIN_REASON = 3


async def set_visibility(
    session: AsyncSession,
    *,
    staff_id: UUID,
    hidden: bool,
    reason: str,
    actor_id: Optional[UUID] = None,
    actor_name: Optional[str] = None,
) -> dict:
    """Hide a member of staff from displays, or restore them.

    A reason is required in both directions. Restoring somebody is as much a
    decision as hiding them, and 'why is this person back on the list?' is a
    question that gets asked.
    """
    reason = (reason or "").strip()
    if len(reason) < MIN_REASON:
        raise HTTPException(
            status_code=400,
            detail=("A reason is required, and it is kept on the record. "
                    "Say why this person is being "
                    + ("hidden." if hidden else "restored.")))

    staff = (await session.execute(
        text("""SELECT id, employee_id, first_name, last_name, display_hidden
                  FROM staff WHERE id = :i FOR UPDATE"""),
        {"i": str(staff_id)},
    )).first()
    if staff is None:
        raise HTTPException(status_code=404, detail="Staff member not found.")

    if bool(staff.display_hidden) == bool(hidden):
        raise HTTPException(
            status_code=400,
            detail=(f"{staff.first_name} {staff.last_name} is already "
                    f"{'hidden' if hidden else 'visible'}."))

    await session.execute(
        text("""
            UPDATE staff
               SET display_hidden = :h,
                   hidden_reason  = CASE WHEN :h THEN :r ELSE NULL END,
                   hidden_at      = CASE WHEN :h THEN NOW() ELSE NULL END,
                   hidden_by      = CASE WHEN :h THEN CAST(:a AS uuid) ELSE NULL END
             WHERE id = :i
        """),
        {"h": hidden, "r": reason,
         "a": str(actor_id) if actor_id else None, "i": str(staff_id)},
    )

    # Appended whichever way it went, so a hide-then-restore leaves two rows
    # rather than cancelling itself out.
    await session.execute(
        text("""
            INSERT INTO staff_visibility_log
                (staff_id, hidden, reason, actor_id, actor_name)
            VALUES (:s, :h, :r, :a, :an)
        """),
        {"s": str(staff_id), "h": hidden, "r": reason,
         "a": str(actor_id) if actor_id else None, "an": actor_name},
    )

    return {
        "staff_id": str(staff_id),
        "employee_id": staff.employee_id,
        "name": f"{staff.first_name} {staff.last_name}",
        "display_hidden": hidden,
        "reason": reason,
        "note": ("Hidden from staff lists and the dashboard. Pay is "
                 "unaffected: this person still appears on payroll, marked as "
                 "hidden." if hidden
                 else "Restored to staff lists."),
    }


async def hidden_staff(session: AsyncSession) -> list:
    """Everyone currently hidden, with why and who did it.

    This list is the counterweight to the feature. Hiding people is only safe
    while somebody can see the whole set of hidden people in one place.
    """
    rows = (await session.execute(
        text("""
            SELECT s.id, s.employee_id, s.first_name, s.last_name, s.position,
                   s.is_active, s.hidden_reason, s.hidden_at, u.full_name
              FROM staff s
         LEFT JOIN users u ON u.id = s.hidden_by
             WHERE s.display_hidden = TRUE
             ORDER BY s.hidden_at DESC NULLS LAST
        """))).fetchall()
    return [
        {"staff_id": str(r.id), "employee_id": r.employee_id,
         "name": f"{r.first_name} {r.last_name}",
         "position": r.position or "", "is_active": bool(r.is_active),
         "reason": r.hidden_reason,
         "hidden_at": r.hidden_at.isoformat() if r.hidden_at else None,
         "hidden_by": r.full_name}
        for r in rows
    ]


async def visibility_history(session: AsyncSession, *, staff_id: UUID) -> list:
    """Every time this person was hidden or restored, and by whom."""
    rows = (await session.execute(
        text("""SELECT hidden, reason, actor_name, created_at
                  FROM staff_visibility_log
                 WHERE staff_id = :i ORDER BY created_at DESC"""),
        {"i": str(staff_id)},
    )).fetchall()
    return [
        {"hidden": bool(r.hidden), "reason": r.reason,
         "actor_name": r.actor_name,
         "at": r.created_at.isoformat() if r.created_at else None}
        for r in rows
    ]
