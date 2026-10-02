"""Who may see whose marketing activity, and the one-month payroll rule.

The IDOR these guard against was live: `staff_id` arrived as a query
parameter the caller chose and the module trusted it, so any authenticated
employee could read, edit or delete any marketer's plans, visits, customers
and order values.
"""
from __future__ import annotations

import os
import uuid
from datetime import date

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.services import marketing_scope as msc

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")

SCHEMA = """
DROP TABLE IF EXISTS user_staff_link_log CASCADE;
DROP TABLE IF EXISTS marketing_facilities CASCADE;
DROP TABLE IF EXISTS marketing_proposals CASCADE;
DROP TABLE IF EXISTS marketing_daily_logs CASCADE;
DROP TABLE IF EXISTS marketing_plans CASCADE;
DROP TABLE IF EXISTS users CASCADE;
DROP TABLE IF EXISTS staff CASCADE;

CREATE TABLE staff (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id VARCHAR(32) UNIQUE NOT NULL,
    first_name VARCHAR(100) NOT NULL, last_name VARCHAR(100) NOT NULL,
    position VARCHAR(120), is_active BOOLEAN DEFAULT TRUE
);
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(100) UNIQUE, email VARCHAR(255) UNIQUE NOT NULL,
    full_name VARCHAR(255), role VARCHAR(50) DEFAULT 'marketer',
    hashed_password VARCHAR(255) DEFAULT 'x', is_active BOOLEAN DEFAULT TRUE
);
CREATE TABLE marketing_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    marketer_staff_id UUID REFERENCES staff(id), title VARCHAR(255)
);
CREATE TABLE marketing_daily_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    marketer_staff_id UUID REFERENCES staff(id), log_date DATE
);
CREATE TABLE marketing_proposals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    marketer_staff_id UUID REFERENCES staff(id), title VARCHAR(255)
);
CREATE TABLE marketing_facilities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    marketer_staff_id UUID REFERENCES staff(id),
    facility_name VARCHAR(255)
);
"""


def _apply(conn, name):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / name
    spec = importlib.util.spec_from_file_location(
        f"mig_{uuid.uuid4().hex[:6]}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with Operations.context(MigrationContext.configure(conn)):
        mod.upgrade()


@pytest_asyncio.fixture
async def db():
    seng = create_engine(TEST_DB.replace("+asyncpg", ""), future=True)
    with seng.connect() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        for stmt in SCHEMA.strip().split(";"):
            if stmt.strip():
                c.execute(text(stmt))
        c.commit()
        _apply(c, "n9012345678m_marketing_scope.py")
        c.commit()
    seng.dispose()
    eng = create_async_engine(TEST_DB, future=True)
    maker = sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await eng.dispose()


class FakeUser:
    def __init__(self, uid, role):
        self.id = uid
        self.role = role


async def _staff(db, name="Ada"):
    sid = uuid.uuid4()
    tag = uuid.uuid4().hex[:6].upper()
    await db.execute(text(
        "INSERT INTO staff (id, employee_id, first_name, last_name) "
        "VALUES (:i, :e, :f, 'Marketer')"),
        {"i": str(sid), "e": f"BSM{tag}", "f": name})
    return sid


async def _user(db, *, role="marketer", staff_id=None):
    uid = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO users (id, username, email, full_name, role, staff_id) "
        "VALUES (:i, :u, :e, 'Test User', :r, :s)"),
        {"i": str(uid), "u": f"u{uid.hex[:8]}", "e": f"{uid.hex[:8]}@t.local",
         "r": role, "s": str(staff_id) if staff_id else None})
    return uid


async def _log(db, staff_id):
    lid = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO marketing_daily_logs (id, marketer_staff_id, log_date) "
        "VALUES (:i, :s, CURRENT_DATE)"),
        {"i": str(lid), "s": str(staff_id)})
    return lid


# ---------------------------------------------------------------------------
# Resolving who somebody is
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_marketer_is_scoped_to_their_own_staff_record(db):
    s = await _staff(db)
    u = await _user(db, role="marketer", staff_id=s)
    scope = await msc.scope_for(db, FakeUser(u, "marketer"))
    assert scope.supervisor is False
    assert scope.staff_id == str(s)


@pytest.mark.asyncio
async def test_an_unlinked_login_is_refused_with_an_explanation(db):
    """Not shown an empty list, which would read as 'you have done no work'."""
    u = await _user(db, role="marketer", staff_id=None)
    with pytest.raises(HTTPException) as e:
        await msc.scope_for(db, FakeUser(u, "marketer"))
    assert e.value.status_code == 403
    assert "not yet linked" in str(e.value.detail)
    assert "administrator" in str(e.value.detail)


@pytest.mark.asyncio
async def test_a_supervisor_sees_everyone(db):
    u = await _user(db, role="admin")
    scope = await msc.scope_for(db, FakeUser(u, "admin"))
    assert scope.supervisor is True
    assert scope.filter_staff_id(None) is None


@pytest.mark.asyncio
async def test_an_unlinked_supervisor_is_still_allowed(db):
    """An admin has no staff record of their own and must still work."""
    u = await _user(db, role="admin", staff_id=None)
    scope = await msc.scope_for(db, FakeUser(u, "admin"))
    assert scope.supervisor is True


# ---------------------------------------------------------------------------
# The IDOR itself
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_marketer_cannot_read_another_marketers_records(db):
    """This is the vulnerability: ?staff_id=<colleague> used to work."""
    mine = await _staff(db, "Ada")
    theirs = await _staff(db, "Bola")
    u = await _user(db, role="marketer", staff_id=mine)
    scope = await msc.scope_for(db, FakeUser(u, "marketer"))

    assert scope.filter_staff_id(str(theirs)) == str(mine)
    assert scope.filter_staff_id(None) == str(mine)


@pytest.mark.asyncio
async def test_a_supervisor_may_filter_by_whoever_they_ask_for(db):
    theirs = await _staff(db, "Bola")
    u = await _user(db, role="admin")
    scope = await msc.scope_for(db, FakeUser(u, "admin"))
    assert scope.filter_staff_id(str(theirs)) == str(theirs)


@pytest.mark.asyncio
async def test_a_marketer_cannot_file_a_record_under_another_name(db):
    mine = await _staff(db, "Ada")
    theirs = await _staff(db, "Bola")
    u = await _user(db, role="marketer", staff_id=mine)
    scope = await msc.scope_for(db, FakeUser(u, "marketer"))

    with pytest.raises(HTTPException) as e:
        scope.may_write_as(str(theirs))
    assert e.value.status_code == 403
    assert scope.may_write_as(None) == str(mine)


@pytest.mark.asyncio
async def test_a_marketer_cannot_edit_another_marketers_log(db):
    mine = await _staff(db, "Ada")
    theirs = await _staff(db, "Bola")
    u = await _user(db, role="marketer", staff_id=mine)
    scope = await msc.scope_for(db, FakeUser(u, "marketer"))

    their_log = await _log(db, theirs)
    with pytest.raises(HTTPException) as e:
        await msc.assert_owns(db, scope, table="marketing_daily_logs",
                              row_id=their_log)
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_a_marketer_may_edit_their_own_log(db):
    mine = await _staff(db, "Ada")
    u = await _user(db, role="marketer", staff_id=mine)
    scope = await msc.scope_for(db, FakeUser(u, "marketer"))
    my_log = await _log(db, mine)
    await msc.assert_owns(db, scope, table="marketing_daily_logs",
                          row_id=my_log)  # must not raise


@pytest.mark.asyncio
async def test_a_supervisor_may_edit_anyones_log(db):
    theirs = await _staff(db, "Bola")
    u = await _user(db, role="admin")
    scope = await msc.scope_for(db, FakeUser(u, "admin"))
    their_log = await _log(db, theirs)
    await msc.assert_owns(db, scope, table="marketing_daily_logs",
                          row_id=their_log)  # must not raise


@pytest.mark.asyncio
async def test_a_missing_row_is_404_not_403(db):
    mine = await _staff(db)
    u = await _user(db, role="marketer", staff_id=mine)
    scope = await msc.scope_for(db, FakeUser(u, "marketer"))
    with pytest.raises(HTTPException) as e:
        await msc.assert_owns(db, scope, table="marketing_daily_logs",
                              row_id=uuid.uuid4())
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_an_unowned_row_is_refused_rather_than_treated_as_mine(db):
    """A log with no marketer must not fall to whoever asks first."""
    mine = await _staff(db)
    u = await _user(db, role="marketer", staff_id=mine)
    scope = await msc.scope_for(db, FakeUser(u, "marketer"))
    orphan = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO marketing_daily_logs (id, marketer_staff_id, log_date) "
        "VALUES (:i, NULL, CURRENT_DATE)"), {"i": str(orphan)})
    with pytest.raises(HTTPException) as e:
        await msc.assert_owns(db, scope, table="marketing_daily_logs",
                              row_id=orphan)
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_every_owned_table_has_an_ownership_rule(db):
    """A new marketing table must not slip past the check by omission."""
    assert set(msc.OWNED_TABLES) == {
        "marketing_plans", "marketing_daily_logs",
        "marketing_proposals", "marketing_facilities"}


@pytest.mark.asyncio
async def test_a_table_with_no_rule_fails_closed(db):
    u = await _user(db, role="marketer", staff_id=await _staff(db))
    scope = await msc.scope_for(db, FakeUser(u, "marketer"))
    with pytest.raises(HTTPException) as e:
        await msc.assert_owns(db, scope, table="customers",
                              row_id=uuid.uuid4())
    assert e.value.status_code == 500


# ---------------------------------------------------------------------------
# Linking logins to staff
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_linking_a_login_lets_it_in(db):
    s = await _staff(db)
    u = await _user(db, role="marketer", staff_id=None)
    with pytest.raises(HTTPException):
        await msc.scope_for(db, FakeUser(u, "marketer"))

    await msc.link_user_to_staff(db, user_id=u, staff_id=s,
                                 actor_name="Admin")
    scope = await msc.scope_for(db, FakeUser(u, "marketer"))
    assert scope.staff_id == str(s)


@pytest.mark.asyncio
async def test_two_logins_cannot_share_one_staff_record(db):
    s = await _staff(db)
    a = await _user(db, staff_id=None)
    b = await _user(db, staff_id=None)
    await msc.link_user_to_staff(db, user_id=a, staff_id=s)
    with pytest.raises(HTTPException) as e:
        await msc.link_user_to_staff(db, user_id=b, staff_id=s)
    assert e.value.status_code == 400
    assert "already linked" in str(e.value.detail)


@pytest.mark.asyncio
async def test_clearing_a_link_locks_the_login_out_again(db):
    s = await _staff(db)
    u = await _user(db, role="marketer", staff_id=s)
    await msc.link_user_to_staff(db, user_id=u, staff_id=None)
    with pytest.raises(HTTPException):
        await msc.scope_for(db, FakeUser(u, "marketer"))


@pytest.mark.asyncio
async def test_linking_is_recorded_and_cannot_be_rewritten(db):
    s = await _staff(db)
    u = await _user(db, staff_id=None)
    await msc.link_user_to_staff(db, user_id=u, staff_id=s,
                                 actor_name="Admin One")
    rows = (await db.execute(text(
        "SELECT actor_name, staff_id FROM user_staff_link_log "
        "WHERE user_id = :u"), {"u": str(u)})).fetchall()
    assert len(rows) == 1
    assert rows[0].actor_name == "Admin One"

    with pytest.raises(Exception) as e:
        await db.execute(text(
            "UPDATE user_staff_link_log SET actor_name = 'someone else' "
            "WHERE user_id = :u"), {"u": str(u)})
    assert "append-only" in str(e.value).lower()


@pytest.mark.asyncio
async def test_the_overview_flags_logins_that_need_linking(db):
    s = await _staff(db)
    await _user(db, role="marketer", staff_id=None)
    await _user(db, role="admin", staff_id=None)
    await _user(db, role="marketer", staff_id=s)

    overview = await msc.link_overview(db)
    needing = [u for u in overview["users"] if u["needs_link"]]
    assert len(needing) == 1, "only the unlinked non-supervisor needs a link"
    assert needing[0]["role"] == "marketer"


@pytest.mark.asyncio
async def test_linking_an_unknown_staff_member_is_a_404(db):
    u = await _user(db, staff_id=None)
    with pytest.raises(HTTPException) as e:
        await msc.link_user_to_staff(db, user_id=u, staff_id=uuid.uuid4())
    assert e.value.status_code == 404
