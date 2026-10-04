"""Staff birthdays and work anniversaries.

The behaviour that matters is all about NOT messaging people: nobody who has
not agreed, nobody who was hidden, nobody twice, and nobody's age on a screen.
"""
from __future__ import annotations

import os
import uuid
from datetime import date, timedelta

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.services import messaging as msg
from app.services import staff_engagement as svc

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")

TODAY = date(2026, 3, 14)

SCHEMA = """
DROP TABLE IF EXISTS staff_engagement_sends CASCADE;
DROP TABLE IF EXISTS staff_engagement_events CASCADE;
DROP TABLE IF EXISTS staff_engagement_consent CASCADE;
DROP TABLE IF EXISTS outbound_messages CASCADE;
DROP TABLE IF EXISTS app_setting_changes CASCADE;
DROP TABLE IF EXISTS app_settings CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS staff CASCADE;
DROP TABLE IF EXISTS users CASCADE;

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(100) UNIQUE, email VARCHAR(255) UNIQUE NOT NULL,
    full_name VARCHAR(255), role VARCHAR(50) DEFAULT 'admin',
    hashed_password VARCHAR(255) DEFAULT 'x', is_active BOOLEAN DEFAULT TRUE
);
CREATE TABLE customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_code VARCHAR(32) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    marketing_consent VARCHAR(12) NOT NULL DEFAULT 'UNKNOWN',
    do_not_contact BOOLEAN NOT NULL DEFAULT FALSE,
    do_not_contact_until DATE,
    merged_into_id UUID REFERENCES customers(id)
);
CREATE TABLE staff (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id VARCHAR(32) UNIQUE NOT NULL,
    first_name VARCHAR(100) NOT NULL, last_name VARCHAR(100) NOT NULL,
    position VARCHAR(120), phone VARCHAR(40),
    date_of_birth DATE, hire_date DATE,
    is_active BOOLEAN DEFAULT TRUE,
    display_hidden BOOLEAN NOT NULL DEFAULT FALSE,
    hidden_reason TEXT
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
        # The outbox and settings come from the messaging migration; staff
        # engagement queues into exactly that outbox rather than its own.
        _apply(c, "q0123456789p_contact_consent.py")
        _apply(c, "s0123456789r_staff_engagement.py")
        c.commit()
    seng.dispose()
    eng = create_async_engine(TEST_DB, future=True)
    maker = sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await eng.dispose()


class FakeUser:
    def __init__(self, uid):
        self.id = uid
        self.full_name = "HR Manager"
        self.username = "hr"


async def _user(db):
    uid = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO users (id, username, email, full_name) "
        "VALUES (:i, :u, :e, 'HR Manager')"),
        {"i": str(uid), "u": f"u{uid.hex[:8]}", "e": f"{uid.hex[:8]}@t.local"})
    return FakeUser(uid)


async def _staff(db, *, name="Chinedu", dob=None, hired=None,
                 phone="08031234567", hidden=False, reason=None):
    sid = uuid.uuid4()
    await db.execute(text("""
        INSERT INTO staff (id, employee_id, first_name, last_name, position,
                           phone, date_of_birth, hire_date, display_hidden,
                           hidden_reason)
        VALUES (:i, :e, :f, 'Staff', 'Production', :p, :dob, :h, :hid, :r)
    """), {"i": str(sid), "e": f"BSM{uuid.uuid4().hex[:5].upper()}", "f": name,
           "p": phone, "dob": dob, "h": hired, "hid": hidden, "r": reason})
    return sid


async def _switches_on(db):
    for k in ("OUTBOUND_MESSAGING_ENABLED", "WHATSAPP_ENABLED",
              "STAFF_ENGAGEMENT_ENABLED"):
        await msg.set_setting(db, key=k, value="true")


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

def test_a_leap_day_birthday_is_marked_in_ordinary_years():
    """Otherwise it is skipped three years in four."""
    assert svc._celebrates_on(29, 2, date(2026, 2, 28)) is True
    assert svc._celebrates_on(29, 2, date(2024, 2, 29)) is True
    assert svc._celebrates_on(29, 2, date(2024, 2, 28)) is False


def test_an_ordinary_birthday_matches_its_own_day():
    assert svc._celebrates_on(14, 3, date(2026, 3, 14)) is True
    assert svc._celebrates_on(14, 3, date(2026, 3, 15)) is False


# ---------------------------------------------------------------------------
# The calendar, and what it refuses to show
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_somebody_who_has_not_agreed_is_counted_but_not_named(db):
    await _staff(db, name="Private Person", dob=date(1990, 3, 14))
    result = await svc.calendar(db, on=TODAY, days=0, for_display=True)
    assert result["birthdays"] == []
    assert result["excluded_count"] == 1


@pytest.mark.asyncio
async def test_somebody_who_agreed_to_be_listed_appears(db):
    user = await _user(db)
    s = await _staff(db, name="Chinedu", dob=date(1990, 3, 14))
    await svc.set_consent(db, staff_id=s, visibility="DEPARTMENT",
                          actor=user)
    result = await svc.calendar(db, on=TODAY, days=0, for_display=True)
    assert len(result["birthdays"]) == 1
    assert result["birthdays"][0]["name"] == "Chinedu Staff"


@pytest.mark.asyncio
async def test_no_year_or_age_is_ever_returned(db):
    """An employer broadcasting ages internally is a different thing."""
    user = await _user(db)
    s = await _staff(db, dob=date(1990, 3, 14))
    await svc.set_consent(db, staff_id=s, visibility="ORGANISATION",
                          actor=user)
    entry = (await svc.calendar(db, on=TODAY, days=0))["birthdays"][0]
    assert "1990" not in str(entry)
    assert "age" not in entry


@pytest.mark.asyncio
async def test_a_hidden_staff_member_never_appears(db):
    """The hide reason offered in the interface is literally about this."""
    user = await _user(db)
    s = await _staff(db, dob=date(1990, 3, 14), hidden=True,
                     reason="Bereavement - no birthday reminders")
    await svc.set_consent(db, staff_id=s, visibility="ORGANISATION",
                          birthday_messages=True, actor=user)
    result = await svc.calendar(db, on=TODAY, days=0, for_display=True)
    assert result["birthdays"] == []
    assert result["excluded_count"] == 1


@pytest.mark.asyncio
async def test_only_milestone_anniversaries_are_marked(db):
    user = await _user(db)
    five = await _staff(db, name="Five Years", hired=date(2021, 3, 14))
    four = await _staff(db, name="Four Years", hired=date(2022, 3, 14))
    for s in (five, four):
        await svc.set_consent(db, staff_id=s, visibility="DEPARTMENT",
                              actor=user)

    result = await svc.calendar(db, on=TODAY, days=0, for_display=True)
    names = {a["name"] for a in result["anniversaries"]}
    assert names == {"Five Years Staff"}
    assert result["anniversaries"][0]["years"] == 5


@pytest.mark.asyncio
async def test_the_day_somebody_was_hired_is_not_an_anniversary(db):
    user = await _user(db)
    s = await _staff(db, hired=TODAY)
    await svc.set_consent(db, staff_id=s, visibility="DEPARTMENT", actor=user)
    result = await svc.calendar(db, on=TODAY, days=0, for_display=True)
    assert result["anniversaries"] == []


# ---------------------------------------------------------------------------
# Preparing greetings
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_nothing_is_prepared_while_the_feature_is_off(db):
    user = await _user(db)
    s = await _staff(db, dob=date(1990, 3, 14))
    await svc.set_consent(db, staff_id=s, birthday_messages=True,
                          visibility="ORGANISATION", actor=user)
    result = await svc.prepare(db, on=TODAY, actor=user)
    assert result["queued"] == 0
    assert "switched off" in result["note"]


@pytest.mark.asyncio
async def test_somebody_who_has_not_agreed_gets_nothing(db):
    user = await _user(db)
    await _switches_on(db)
    s = await _staff(db, name="Did Not Agree", dob=date(1990, 3, 14))
    await svc.set_consent(db, staff_id=s, visibility="ORGANISATION",
                          actor=user)

    result = await svc.prepare(db, on=TODAY, actor=user)
    assert result["queued"] == 0
    assert any("not agreed" in x["why"] for x in result["skipped"])


@pytest.mark.asyncio
async def test_somebody_who_agreed_gets_a_greeting_queued(db):
    user = await _user(db)
    await _switches_on(db)
    s = await _staff(db, name="Chinedu", dob=date(1990, 3, 14))
    await svc.set_consent(db, staff_id=s, birthday_messages=True,
                          visibility="ORGANISATION", actor=user)

    result = await svc.prepare(db, on=TODAY, actor=user)
    assert result["queued"] == 1
    assert result["prepared"][0]["status"] == "QUEUED"

    body = (await db.execute(text(
        "SELECT body, category FROM outbound_messages"))).mappings().first()
    assert "Chinedu" in body["body"]
    assert body["category"] == "SERVICE"


@pytest.mark.asyncio
async def test_a_hidden_staff_member_is_not_greeted(db):
    user = await _user(db)
    await _switches_on(db)
    s = await _staff(db, dob=date(1990, 3, 14), hidden=True,
                     reason="Bereavement - no birthday reminders")
    await svc.set_consent(db, staff_id=s, birthday_messages=True,
                          visibility="ORGANISATION", actor=user)
    result = await svc.prepare(db, on=TODAY, actor=user)
    assert result["queued"] == 0


@pytest.mark.asyncio
async def test_running_twice_greets_nobody_twice(db):
    """A restart at midnight must not wish somebody happy birthday twice."""
    user = await _user(db)
    await _switches_on(db)
    s = await _staff(db, dob=date(1990, 3, 14))
    await svc.set_consent(db, staff_id=s, birthday_messages=True,
                          visibility="ORGANISATION", actor=user)

    first = await svc.prepare(db, on=TODAY, actor=user)
    second = await svc.prepare(db, on=TODAY, actor=user)
    assert first["queued"] == 1
    assert second["queued"] == 0
    assert any("already prepared" in x["why"] for x in second["skipped"])

    n = (await db.execute(text(
        "SELECT COUNT(*) FROM outbound_messages"))).scalar()
    assert n == 1


@pytest.mark.asyncio
async def test_the_database_refuses_a_second_greeting_for_the_year(db):
    s = await _staff(db, dob=date(1990, 3, 14))
    for _ in range(1):
        await db.execute(text("""
            INSERT INTO staff_engagement_sends
                (staff_id, occasion, occasion_year) VALUES (:s, 'BIRTHDAY', 2026)
        """), {"s": str(s)})
    with pytest.raises(Exception) as e:
        await db.execute(text("""
            INSERT INTO staff_engagement_sends
                (staff_id, occasion, occasion_year) VALUES (:s, 'BIRTHDAY', 2026)
        """), {"s": str(s)})
    assert "uq_ses_once" in str(e.value)


@pytest.mark.asyncio
async def test_somebody_with_no_phone_number_is_reported_not_dropped(db):
    user = await _user(db)
    await _switches_on(db)
    s = await _staff(db, dob=date(1990, 3, 14), phone=None)
    await svc.set_consent(db, staff_id=s, birthday_messages=True,
                          visibility="ORGANISATION", actor=user)
    result = await svc.prepare(db, on=TODAY, actor=user)
    assert result["queued"] == 0
    assert any("no phone" in x["why"] for x in result["skipped"])


@pytest.mark.asyncio
async def test_an_anniversary_message_names_the_years(db):
    user = await _user(db)
    await _switches_on(db)
    s = await _staff(db, name="Amaka", hired=date(2021, 3, 14))
    await svc.set_consent(db, staff_id=s, anniversary_messages=True,
                          visibility="ORGANISATION", actor=user)

    result = await svc.prepare(db, on=TODAY, actor=user)
    assert result["queued"] == 1
    body = (await db.execute(text(
        "SELECT body FROM outbound_messages"))).scalar()
    assert "Amaka" in body
    assert "5 years" in body


@pytest.mark.asyncio
async def test_the_master_switch_still_stops_staff_messages(db):
    """Staff engagement on, but outbound off, means nothing leaves."""
    user = await _user(db)
    await msg.set_setting(db, key="STAFF_ENGAGEMENT_ENABLED", value="true")
    s = await _staff(db, dob=date(1990, 3, 14))
    await svc.set_consent(db, staff_id=s, birthday_messages=True,
                          visibility="ORGANISATION", actor=user)

    result = await svc.prepare(db, on=TODAY, actor=user)
    assert result["queued"] == 1
    assert result["prepared"][0]["status"] == "BLOCKED"
    assert "switched off for the whole system" in \
        result["prepared"][0]["blocked_reason"]


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_consent_starts_at_no_for_everybody(db):
    user = await _user(db)
    s = await _staff(db, dob=date(1990, 3, 14))
    await svc.set_consent(db, staff_id=s, preferred_name="Chi", actor=user)
    row = (await db.execute(text(
        "SELECT birthday_messages, anniversary_messages, visibility "
        "FROM staff_engagement_consent WHERE staff_id = :i"),
        {"i": str(s)})).mappings().first()
    assert row["birthday_messages"] is False
    assert row["anniversary_messages"] is False
    assert row["visibility"] == "NOBODY"


@pytest.mark.asyncio
async def test_a_preferred_name_is_used_in_the_greeting(db):
    user = await _user(db)
    await _switches_on(db)
    s = await _staff(db, name="Emmanuel", dob=date(1990, 3, 14))
    await svc.set_consent(db, staff_id=s, birthday_messages=True,
                          visibility="ORGANISATION", preferred_name="Dr Emma",
                          actor=user)
    await svc.prepare(db, on=TODAY, actor=user)
    body = (await db.execute(text(
        "SELECT body FROM outbound_messages"))).scalar()
    assert "Dr Emma" in body


@pytest.mark.asyncio
async def test_consent_changes_are_recorded_and_cannot_be_rewritten(db):
    user = await _user(db)
    s = await _staff(db)
    await svc.set_consent(db, staff_id=s, birthday_messages=True, actor=user)

    hist = await svc.history(db, staff_id=s)
    assert len(hist["consent_changes"]) == 1
    assert hist["consent_changes"][0]["actor"] == "HR Manager"

    with pytest.raises(Exception) as e:
        await db.execute(text(
            "UPDATE staff_engagement_events SET detail = 'changed'"))
    assert "append-only" in str(e.value).lower()


@pytest.mark.asyncio
async def test_an_unknown_visibility_is_refused(db):
    user = await _user(db)
    s = await _staff(db)
    with pytest.raises(HTTPException) as e:
        await svc.set_consent(db, staff_id=s, visibility="EVERYONE",
                              actor=user)
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_an_unknown_staff_member_is_a_404(db):
    with pytest.raises(HTTPException) as e:
        await svc.set_consent(db, staff_id=uuid.uuid4(),
                              birthday_messages=True)
    assert e.value.status_code == 404
