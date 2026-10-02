"""Who may see whose marketing activity.

Every list, update and delete in the marketing module used to take the staff
id from the caller and trust it. This module is what replaces that trust.

THE RULE
========
    supervisor  -> everyone's records, and may filter by staff_id
    marketer    -> their own records only, and staff_id is ignored if sent
    unlinked    -> refused, with an explanation and who can fix it

`scope_for` is the only place that decides which of those a caller is, and
`assert_owns` is the only place that decides whether a particular row is
theirs. Spreading either decision across twenty route handlers is how one of
them ends up missing it.

WHY THE FILTER IS FORCED RATHER THAN VALIDATED
==============================================
A validated filter -- "if staff_id was supplied, check it matches" -- fails
open the moment a route forgets to pass it on, because the absent filter means
"everybody". Forcing the value means a route that forgets still returns the
caller's own rows. The failure mode of a mistake here should be seeing too
little, never too much.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Who supervises marketers and may therefore read the whole team's activity.
#
# Deliberately not every authenticated login. A marketer's daily log names the
# customers they visited, what was discussed and what it was worth; that is
# commercially sensitive and it is also a record of an individual's
# performance. Production staff and warehouse staff have no reason to read it.
SUPERVISOR_ROLES = ("admin", "sales_staff")

UNLINKED_DETAIL = (
    "Your login is not yet linked to a staff record, so the system cannot "
    "tell which marketing activity is yours. An administrator can link it "
    "under Staff Management. Until then this module has nothing to show you "
    "-- which is not the same as you having done no work."
)


class Scope:
    """What this caller may reach."""

    __slots__ = ("supervisor", "staff_id", "user_id")

    def __init__(self, *, supervisor: bool, staff_id: Optional[str],
                 user_id: Optional[str]):
        self.supervisor = supervisor
        self.staff_id = staff_id
        self.user_id = user_id

    def filter_staff_id(self, requested: Optional[str]) -> Optional[str]:
        """The staff id a query must actually use.

        A supervisor gets what they asked for (None meaning everybody). A
        marketer gets their own, whatever they asked for.
        """
        return requested if self.supervisor else self.staff_id

    def may_write_as(self, requested: Optional[str]) -> Optional[str]:
        """The staff id a new record may be filed under."""
        if self.supervisor:
            return requested or self.staff_id
        if requested and str(requested) != str(self.staff_id):
            raise HTTPException(
                status_code=403,
                detail=("You can only record activity against your own name."))
        return self.staff_id


async def scope_for(session: AsyncSession, user) -> Scope:
    """Resolve what this user may see. Refuses an unlinked non-supervisor."""
    role = getattr(user, "role", None)
    if role in SUPERVISOR_ROLES:
        linked = (await session.execute(
            text("SELECT staff_id FROM users WHERE id = :i"),
            {"i": str(user.id)})).scalar()
        return Scope(supervisor=True,
                     staff_id=str(linked) if linked else None,
                     user_id=str(user.id))

    linked = (await session.execute(
        text("SELECT staff_id FROM users WHERE id = :i"),
        {"i": str(user.id)})).scalar()
    if not linked:
        raise HTTPException(status_code=403, detail=UNLINKED_DETAIL)
    return Scope(supervisor=False, staff_id=str(linked), user_id=str(user.id))


# Tables in this module that carry a marketer, and the column that holds it.
# Named explicitly so a new table cannot be added to the module and silently
# skip the ownership check by not appearing in a generic helper.
OWNED_TABLES = {
    "marketing_plans": "marketer_staff_id",
    "marketing_daily_logs": "marketer_staff_id",
    "marketing_proposals": "marketer_staff_id",
    "marketing_facilities": "marketer_staff_id",
}


async def assert_owns(session: AsyncSession, scope: Scope, *,
                      table: str, row_id) -> None:
    """Refuse unless this row belongs to the caller (or they supervise).

    A missing row is a 404 and a row belonging to somebody else is a 403, so
    the two are distinguishable to a legitimate user. That distinction leaks
    only the existence of an id the caller already guessed, and the
    alternative -- reporting every refusal as 404 -- makes a genuine
    permissions problem impossible to diagnose.
    """
    if table not in OWNED_TABLES:
        raise HTTPException(
            status_code=500,
            detail=f"No ownership rule defined for {table}.")
    if scope.supervisor:
        return

    owner = (await session.execute(
        text(f"SELECT {OWNED_TABLES[table]} FROM {table} WHERE id = :i"),
        {"i": str(row_id)})).first()
    if owner is None:
        raise HTTPException(status_code=404, detail="Record not found.")
    if owner[0] is None or str(owner[0]) != str(scope.staff_id):
        raise HTTPException(
            status_code=403,
            detail="That record belongs to another marketer.")


async def link_user_to_staff(
    session: AsyncSession, *, user_id: UUID, staff_id: Optional[UUID],
    actor_id: Optional[UUID] = None, actor_name: Optional[str] = None,
) -> dict:
    """Point a login at a staff record, or clear the link.

    Recorded in an append-only log, because this is the act that decides whose
    records a login can read.
    """
    user = (await session.execute(
        text("SELECT id, full_name, email, role, staff_id FROM users "
             "WHERE id = :i FOR UPDATE"),
        {"i": str(user_id)})).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")

    staff = None
    if staff_id is not None:
        staff = (await session.execute(
            text("SELECT id, employee_id, first_name, last_name FROM staff "
                 "WHERE id = :i"), {"i": str(staff_id)})).first()
        if staff is None:
            raise HTTPException(status_code=404,
                                detail="Staff member not found.")
        clash = (await session.execute(
            text("SELECT email FROM users WHERE staff_id = :s AND id <> :u"),
            {"s": str(staff_id), "u": str(user_id)})).scalar()
        if clash:
            raise HTTPException(
                status_code=400,
                detail=(f"{staff.first_name} {staff.last_name} is already "
                        f"linked to the login {clash}. One staff record, one "
                        f"login -- unlink that one first."))

    await session.execute(
        text("UPDATE users SET staff_id = :s WHERE id = :u"),
        {"s": str(staff_id) if staff_id else None, "u": str(user_id)})

    await session.execute(
        text("""INSERT INTO user_staff_link_log
                    (user_id, staff_id, previous_staff_id, actor_id, actor_name)
                VALUES (:u, :s, :p, :a, :an)"""),
        {"u": str(user_id), "s": str(staff_id) if staff_id else None,
         "p": str(user.staff_id) if user.staff_id else None,
         "a": str(actor_id) if actor_id else None, "an": actor_name})

    return {
        "user_id": str(user_id),
        "user": user.full_name or user.email,
        "role": user.role,
        "staff_id": str(staff_id) if staff_id else None,
        "staff": (f"{staff.employee_id} {staff.first_name} {staff.last_name}"
                  if staff else None),
        "note": ("This login now sees only that person's marketing activity."
                 if staff_id else
                 "Link cleared. This login can no longer reach the marketing "
                 "module unless it holds a supervisor role."),
    }


async def link_overview(session: AsyncSession) -> dict:
    """Every login, and the staff record it points at.

    Shown to an administrator so the unlinked accounts are visible as a set,
    rather than discovered one complaint at a time.
    """
    users = (await session.execute(text("""
        SELECT u.id, u.full_name, u.email, u.role, u.staff_id,
               s.employee_id, s.first_name, s.last_name
          FROM users u LEFT JOIN staff s ON s.id = u.staff_id
         WHERE u.is_active
         ORDER BY (u.staff_id IS NOT NULL), u.role, u.full_name
    """))).fetchall()

    staff = (await session.execute(text("""
        SELECT s.id, s.employee_id, s.first_name, s.last_name, s.position,
               (u.id IS NOT NULL) AS has_login
          FROM staff s LEFT JOIN users u ON u.staff_id = s.id
         WHERE s.is_active
         ORDER BY s.employee_id
    """))).fetchall()

    return {
        "users": [
            {"user_id": str(u.id), "name": u.full_name or u.email,
             "email": u.email, "role": u.role,
             "staff_id": str(u.staff_id) if u.staff_id else None,
             "staff": (f"{u.employee_id} {u.first_name} {u.last_name}"
                       if u.staff_id else None),
             "needs_link": (u.staff_id is None
                            and u.role not in SUPERVISOR_ROLES)}
            for u in users],
        "staff": [
            {"staff_id": str(s.id), "employee_id": s.employee_id,
             "name": f"{s.first_name} {s.last_name}",
             "position": s.position or "", "has_login": bool(s.has_login)}
            for s in staff],
        "supervisor_roles": list(SUPERVISOR_ROLES),
        "note": ("A login with a supervisor role sees the whole team's "
                 "marketing activity. Any other login sees only the staff "
                 "record it is linked to, and nothing at all until it is "
                 "linked."),
    }
