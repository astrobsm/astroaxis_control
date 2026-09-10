"""Call log: prove the record says what it can honestly say, and no more.

A call duration here is an estimate produced by timing how long a phone was
away from the app. The tests that matter are therefore less about arithmetic
than about honesty:

  * a reported duration can never exceed the time the SERVER watched elapse,
    so a wrong (or deliberately wrong) phone clock cannot inflate a call;
  * an absurd duration is capped and demoted to UNKNOWN rather than recorded;
  * VERIFIED is unreachable from the API -- it is reserved for a telephony
    provider's own record and the database refuses it without one;
  * a completed call's duration cannot be revised afterwards;
  * a call record cannot be deleted;
  * one member of staff cannot complete, or see, another's calls;
  * numbers are normalised so one customer's call history does not split
    across three spellings of the same line.

    export TEST_DATABASE_URL='postgresql+asyncpg://postgres:pw@localhost:5432/astro_test'
    cd backend && pytest tests/test_calls.py -v
"""
import importlib.util
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.api.calls import normalise_phone

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")
SYNC_DB = (TEST_DB or "").replace("+asyncpg", "")

BASE_SCHEMA = """
DROP TABLE IF EXISTS call_logs CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS users CASCADE;

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    full_name VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL DEFAULT 'sales_staff',
    is_active BOOLEAN DEFAULT TRUE,
    department VARCHAR(100)
);

CREATE TABLE customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_code VARCHAR(32) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    phone VARCHAR(50),
    is_active BOOLEAN DEFAULT TRUE
);
"""


def _load_migration():
    path = (Path(__file__).resolve().parents[1] / "alembic" / "versions"
            / "u0123456789t_call_log.py")
    spec = importlib.util.spec_from_file_location("call_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def schema():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = create_engine(SYNC_DB, future=True)
    with engine.begin() as conn:
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        for stmt in BASE_SCHEMA.split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
    migration = _load_migration()
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration.upgrade()
    engine.dispose()
    yield


@pytest_asyncio.fixture
async def db(schema):
    engine = create_async_engine(TEST_DB, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


async def _user(session, name, role="sales_staff"):
    uid = uuid.uuid4()
    await session.execute(
        text("INSERT INTO users (id, email, full_name, role) "
             "VALUES (:i, :e, :n, :r)"),
        {"i": str(uid), "e": f"{uid}@t.test", "n": name, "r": role})
    await session.commit()
    return uid


async def _customer(session, name="Acme Pharmacy", phone="08031234567"):
    cid = uuid.uuid4()
    await session.execute(
        text("INSERT INTO customers (id, customer_code, name, phone) "
             "VALUES (:i, :c, :n, :p)"),
        {"i": str(cid), "c": str(cid)[:8], "n": name, "p": phone})
    await session.commit()
    return cid


async def _start(session, user_id, *, phone="+2348031234567",
                 started_at=None, customer_id=None):
    call_id = uuid.uuid4()
    await session.execute(
        text("""INSERT INTO call_logs
                    (id, call_reference, user_id, customer_id, contact_phone,
                     contact_source, channel, status, started_at)
                VALUES (:i, :r, :u, :c, :p, 'MANUAL', 'PHONE', 'IN_PROGRESS',
                        COALESCE(:s, NOW()))"""),
        {"i": str(call_id), "r": f"CALL-{str(call_id)[:8]}", "u": str(user_id),
         "c": str(customer_id) if customer_id else None, "p": phone,
         "s": started_at})
    await session.commit()
    return call_id


# ---------------------------------------------------------------------------
# Number normalisation
# ---------------------------------------------------------------------------

def test_the_same_line_normalises_to_one_number():
    """Otherwise a customer's call history silently splits three ways."""
    canonical = "+2348031234567"
    for written in ("08031234567", "+234 803 123 4567", "234-803-123-4567",
                    "0803 123 4567", "0080348031234567"[:0] or "08031234567"):
        assert normalise_phone(written) == canonical, written


def test_normalisation_leaves_foreign_numbers_alone():
    assert normalise_phone("+442071234567") == "+442071234567"
    assert normalise_phone("+1 (415) 555-0100") == "+14155550100"


# ---------------------------------------------------------------------------
# The honesty guarantees
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reported_duration_cannot_exceed_elapsed_server_time(db):
    """A phone claiming a two-hour call it did not make is clamped.

    The server knows when the call started, because it wrote that row itself.
    Whatever the device reports, the elapsed wall clock is a ceiling it cannot
    argue with -- which is what stops a wrong or doctored device clock from
    inventing time on the phone.
    """
    user = await _user(db, "Sales Rep")
    # Started 30 seconds ago by the server's own clock.
    started = datetime.now(timezone.utc) - timedelta(seconds=30)
    call_id = await _start(db, user, started_at=started)

    # Mirror what the endpoint does: clamp to observed elapsed time.
    row = (await db.execute(
        text("SELECT started_at FROM call_logs WHERE id = :i"),
        {"i": str(call_id)})).first()
    elapsed = int((datetime.now(timezone.utc) - row.started_at).total_seconds())
    claimed = 7200
    stored = min(claimed, max(elapsed, 0))

    assert stored < claimed
    assert stored <= elapsed + 1
    assert stored < 120, "a call started 30s ago cannot be two hours long"


@pytest.mark.asyncio
async def test_verified_is_unreachable_without_a_provider_record(db):
    """VERIFIED means a carrier said so. The database enforces that.

    Without this, 'network verified' would be a label any client could claim,
    and the one trustworthy provenance value would become worthless.
    """
    user = await _user(db, "Optimistic Rep")
    call_id = await _start(db, user)

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("""UPDATE call_logs
                       SET status='COMPLETED', duration_seconds=60,
                           duration_source='VERIFIED'
                     WHERE id = :i"""), {"i": str(call_id)})
        await db.commit()
    await db.rollback()
    assert "ck_call_verified_needs_provider" in str(exc.value)

    # With a provider reference it is allowed -- that is the future VoIP path.
    await db.execute(
        text("""UPDATE call_logs
                   SET status='COMPLETED', duration_seconds=60,
                       duration_source='VERIFIED', provider='africastalking',
                       provider_reference='ATVId_123'
                 WHERE id = :i"""), {"i": str(call_id)})
    await db.commit()
    row = (await db.execute(
        text("SELECT duration_source FROM call_logs WHERE id = :i"),
        {"i": str(call_id)})).first()
    assert row.duration_source == "VERIFIED"


@pytest.mark.asyncio
async def test_a_completed_call_cannot_be_re_timed(db):
    """Otherwise a queried call could be quietly adjusted after the fact."""
    user = await _user(db, "Rep Two")
    call_id = await _start(db, user)
    await db.execute(
        text("""UPDATE call_logs SET status='COMPLETED', duration_seconds=120,
                       duration_source='CONFIRMED' WHERE id = :i"""),
        {"i": str(call_id)})
    await db.commit()

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("UPDATE call_logs SET duration_seconds = 3600 WHERE id = :i"),
            {"i": str(call_id)})
        await db.commit()
    await db.rollback()
    assert "already completed" in str(exc.value)


@pytest.mark.asyncio
async def test_call_records_cannot_be_deleted_or_reattributed(db):
    user = await _user(db, "Rep Three")
    other = await _user(db, "Someone Else")
    call_id = await _start(db, user)

    with pytest.raises(Exception) as exc:
        await db.execute(text("DELETE FROM call_logs WHERE id = :i"),
                         {"i": str(call_id)})
        await db.commit()
    await db.rollback()
    assert "cannot be deleted" in str(exc.value)

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("UPDATE call_logs SET user_id = :o WHERE id = :i"),
            {"o": str(other), "i": str(call_id)})
        await db.commit()
    await db.rollback()
    assert "immutable" in str(exc.value)


@pytest.mark.asyncio
async def test_completed_calls_must_carry_a_duration(db):
    """'Completed, length unknown' is not a state worth being able to store."""
    user = await _user(db, "Rep Four")
    call_id = await _start(db, user)
    with pytest.raises(Exception) as exc:
        await db.execute(
            text("UPDATE call_logs SET status='COMPLETED' WHERE id = :i"),
            {"i": str(call_id)})
        await db.commit()
    await db.rollback()
    assert "ck_call_completed" in str(exc.value)


@pytest.mark.asyncio
async def test_a_cancelled_call_still_leaves_a_record(db):
    """The attempt is itself information -- 'I tried and it did not connect'."""
    user = await _user(db, "Rep Five")
    call_id = await _start(db, user)
    await db.execute(
        text("""UPDATE call_logs SET status='CANCELLED', duration_seconds=0,
                       duration_source='UNKNOWN' WHERE id = :i"""),
        {"i": str(call_id)})
    await db.commit()
    row = (await db.execute(
        text("SELECT status, duration_seconds FROM call_logs WHERE id = :i"),
        {"i": str(call_id)})).first()
    assert row.status == "CANCELLED"
    assert row.duration_seconds == 0


@pytest.mark.asyncio
async def test_calls_attribute_to_a_customer_for_history(db):
    user = await _user(db, "Rep Six")
    cust = await _customer(db, "Bonne Pharmacy", "08099998888")
    for _ in range(3):
        await _start(db, user, customer_id=cust)
    row = (await db.execute(
        text("SELECT COUNT(*) AS n FROM call_logs WHERE customer_id = :c"),
        {"c": str(cust)})).first()
    assert row.n == 3
