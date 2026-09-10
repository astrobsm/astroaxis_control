"""Click-to-call bridging: the parts that must not be wrong.

Bridging exists to produce one thing the rest of the call log cannot -- a
duration nobody in the company can influence. These tests defend that claim
and the unauthenticated endpoint that carries it:

  * bridging stays OFF until it is fully configured, and refuses to half-work;
  * a weak or missing webhook secret disables it rather than shipping an open
    endpoint;
  * the secret is compared in constant time;
  * callbacks are parsed leniently across field-name spellings, because a
    payload we fail to understand is still a billed call;
  * a VERIFIED duration cannot be overwritten by an estimate, ever;
  * a verified duration CAN land on a call already completed with an estimate,
    because the authoritative figure often arrives late.

No network is touched: place_bridged_call is exercised only through its
configuration guard.
"""
import importlib
import os
import uuid
from decimal import Decimal
from pathlib import Path

import importlib.util
import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")
SYNC_DB = (TEST_DB or "").replace("+asyncpg", "")

BASE_SCHEMA = """
DROP TABLE IF EXISTS call_provider_events CASCADE;
DROP TABLE IF EXISTS call_logs CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS users CASCADE;

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    full_name VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL DEFAULT 'sales_staff',
    is_active BOOLEAN DEFAULT TRUE,
    phone VARCHAR(20),
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


def _load(name, filename):
    path = (Path(__file__).resolve().parents[1] / "alembic" / "versions"
            / filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
    # Both migrations, in order: bridging amends the guard the first one made.
    for name, fn in (("m_calls", "u0123456789t_call_log.py"),
                     ("m_tel", "v1234567890u_call_telephony.py")):
        mod = _load(name, fn)
        with engine.begin() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                mod.upgrade()
    engine.dispose()
    yield


@pytest_asyncio.fixture
async def db(schema):
    engine = create_async_engine(TEST_DB, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


def _reload_telephony(**env):
    """Re-import the module so module-level config is re-read from os.environ."""
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    import app.services.telephony as t
    return importlib.reload(t)


# ---------------------------------------------------------------------------
# Configuration refuses to half-work
# ---------------------------------------------------------------------------

def test_bridging_is_off_until_fully_configured():
    """Deploying this code must not start billing anyone by itself."""
    t = _reload_telephony(TELEPHONY_PROVIDER=None)
    ok, reason = t.configured()
    assert ok is False
    assert "TELEPHONY_PROVIDER" in reason


def test_partial_configuration_is_refused_with_the_missing_piece_named():
    t = _reload_telephony(
        TELEPHONY_PROVIDER="africastalking", AT_USERNAME="acme",
        AT_API_KEY=None, AT_CALLER_ID=None)
    ok, reason = t.configured()
    assert ok is False
    assert "AT_API_KEY" in reason and "AT_CALLER_ID" in reason


def test_a_weak_webhook_secret_disables_bridging(monkeypatch):
    """The secret is the only thing guarding an unauthenticated endpoint.

    Shipping bridging with a short secret would put an open, world-reachable
    write path on the call log. Refusing to enable it is the correct failure.
    """
    t = _reload_telephony(
        TELEPHONY_PROVIDER="africastalking", AT_USERNAME="acme",
        AT_API_KEY="key", AT_CALLER_ID="+2348000000000",
        TELEPHONY_WEBHOOK_SECRET="tooshort",
        PUBLIC_BASE_URL="https://erp.example.com")
    ok, reason = t.configured()
    assert ok is False
    assert "TELEPHONY_WEBHOOK_SECRET" in reason


def test_insecure_public_base_url_is_refused():
    t = _reload_telephony(
        TELEPHONY_PROVIDER="africastalking", AT_USERNAME="acme",
        AT_API_KEY="key", AT_CALLER_ID="+2348000000000",
        TELEPHONY_WEBHOOK_SECRET="x" * 32,
        PUBLIC_BASE_URL="http://erp.example.com")
    ok, reason = t.configured()
    assert ok is False
    assert "https" in reason


def test_full_configuration_enables_bridging_and_builds_the_callback_url():
    secret = "s" * 40
    t = _reload_telephony(
        TELEPHONY_PROVIDER="africastalking", AT_USERNAME="acme",
        AT_API_KEY="key", AT_CALLER_ID="+2348000000000",
        TELEPHONY_WEBHOOK_SECRET=secret,
        PUBLIC_BASE_URL="https://erp.example.com/")
    ok, reason = t.configured()
    assert ok is True and reason is None
    assert t.callback_url() == f"https://erp.example.com/api/telephony/{secret}/voice"


@pytest.mark.asyncio
async def test_placing_a_call_without_configuration_raises_not_silently_passes():
    t = _reload_telephony(TELEPHONY_PROVIDER=None)
    with pytest.raises(t.TelephonyError):
        await t.place_bridged_call(staff_phone="+2348031111111",
                                   customer_phone="+2348032222222")


# ---------------------------------------------------------------------------
# The webhook secret
# ---------------------------------------------------------------------------

def test_secret_comparison_is_constant_time_and_rejects_short_secrets():
    import app.api.telephony_webhook as wh
    _reload_telephony(TELEPHONY_WEBHOOK_SECRET="a" * 32)
    importlib.reload(wh)
    assert wh._secret_ok("a" * 32) is True
    assert wh._secret_ok("b" * 32) is False
    assert wh._secret_ok("") is False
    assert wh._secret_ok(None) is False

    # A short configured secret must never validate, even against itself.
    _reload_telephony(TELEPHONY_WEBHOOK_SECRET="short")
    importlib.reload(wh)
    assert wh._secret_ok("short") is False


# ---------------------------------------------------------------------------
# Callback parsing
# ---------------------------------------------------------------------------

def test_callback_parsing_survives_the_provider_renaming_fields():
    """Two spellings of the same event must both yield the same facts."""
    t = _reload_telephony(TELEPHONY_PROVIDER="africastalking")

    a = t.parse_callback({
        "sessionId": "ATVId_1", "callSessionState": "Completed",
        "durationInSeconds": "125", "amount": "NGN 12.5000",
        "currencyCode": "NGN", "isActive": "0"})
    b = t.parse_callback({
        "session_id": "ATVId_1", "state": "completed",
        "duration": "125", "cost": "12.50", "currency": "NGN",
        "IsActive": "0"})

    for parsed in (a, b):
        assert parsed["session_id"] == "ATVId_1"
        assert parsed["duration_seconds"] == 125
        assert parsed["cost"] == Decimal("12.50")
        assert parsed["currency"] == "NGN"
        assert parsed["finished"] is True


def test_a_live_call_is_not_treated_as_finished():
    """Locking in a duration mid-call would record the wrong number."""
    t = _reload_telephony(TELEPHONY_PROVIDER="africastalking")
    parsed = t.parse_callback({"sessionId": "ATVId_2", "isActive": "1"})
    assert parsed["finished"] is False
    assert parsed["duration_seconds"] is None


def test_unreadable_amounts_do_not_break_the_callback():
    """A cost we cannot parse must not cost us the duration."""
    t = _reload_telephony(TELEPHONY_PROVIDER="africastalking")
    parsed = t.parse_callback({
        "sessionId": "ATVId_3", "isActive": "0",
        "durationInSeconds": "60", "amount": "not a number"})
    assert parsed["duration_seconds"] == 60
    assert parsed["cost"] is None


def test_the_bridge_instruction_cannot_be_injected_into():
    """The customer number is interpolated into XML, so it is filtered."""
    t = _reload_telephony(TELEPHONY_PROVIDER="africastalking")
    xml = t.bridge_instruction('+234803"/><Reject/><Dial phoneNumbers="+1999')
    assert '<Reject/>' not in xml
    assert xml.count('<Dial') == 1
    assert '"' not in xml.split('phoneNumbers="')[1].split('"')[0]


# ---------------------------------------------------------------------------
# VERIFIED beats an estimate, and nothing beats VERIFIED
# ---------------------------------------------------------------------------

async def _call_row(session, user_id, *, session_id, source, duration, status):
    call_id = uuid.uuid4()
    await session.execute(
        text("""INSERT INTO call_logs
                    (id, call_reference, user_id, contact_phone, contact_source,
                     channel, status, duration_seconds, duration_source,
                     provider, provider_reference)
                VALUES (:i, :r, :u, '+2348031234567', 'MANUAL', 'BRIDGE',
                        :st, :d, :src, 'africastalking', :sid)"""),
        {"i": str(call_id), "r": f"CALL-{str(call_id)[:8]}", "u": str(user_id),
         "st": status, "d": duration, "src": source, "sid": session_id})
    await session.commit()
    return call_id


async def _user(session):
    uid = uuid.uuid4()
    await session.execute(
        text("INSERT INTO users (id, email, full_name) VALUES (:i, :e, 'Rep')"),
        {"i": str(uid), "e": f"{uid}@t.test"})
    await session.commit()
    return uid


@pytest.mark.asyncio
async def test_the_network_figure_may_replace_a_confirmed_estimate(db):
    """The authoritative duration often arrives after the staff member's guess.

    u0123456789t froze a completed call's duration so it could not be re-timed
    after a query. The bridging migration carves out exactly one exception --
    a VERIFIED figure -- and this is it.
    """
    user = await _user(db)
    call_id = await _call_row(db, user, session_id="ATVId_late",
                              source="CONFIRMED", duration=300,
                              status="COMPLETED")
    await db.execute(
        text("""UPDATE call_logs
                   SET duration_seconds = 137, duration_source = 'VERIFIED'
                 WHERE id = :i"""), {"i": str(call_id)})
    await db.commit()
    row = (await db.execute(
        text("SELECT duration_seconds, duration_source FROM call_logs "
             "WHERE id = :i"), {"i": str(call_id)})).first()
    assert row.duration_seconds == 137
    assert row.duration_source == "VERIFIED"


@pytest.mark.asyncio
async def test_an_estimate_can_never_overwrite_the_network_figure(db):
    """The whole value of VERIFIED is that it is the last word."""
    user = await _user(db)
    call_id = await _call_row(db, user, session_id="ATVId_final",
                              source="VERIFIED", duration=137,
                              status="COMPLETED")
    for source, dur in (("CONFIRMED", 600), ("MANUAL", 900),
                        ("MEASURED", 20)):
        with pytest.raises(Exception) as exc:
            await db.execute(
                text("""UPDATE call_logs SET duration_seconds = :d,
                               duration_source = :s WHERE id = :i"""),
                {"d": dur, "s": source, "i": str(call_id)})
            await db.commit()
        await db.rollback()
        assert "cannot be replaced by an estimate" in str(exc.value)


@pytest.mark.asyncio
async def test_provider_events_are_append_only(db):
    """They are the record of what the network told us."""
    user = await _user(db)
    call_id = await _call_row(db, user, session_id="ATVId_ev",
                              source="UNKNOWN", duration=None,
                              status="IN_PROGRESS")
    await db.execute(
        text("""INSERT INTO call_provider_events
                    (id, call_id, provider, session_id, event_type, raw,
                     secret_ok)
                VALUES (gen_random_uuid(), :c, 'africastalking', 'ATVId_ev',
                        'COMPLETED', CAST(:r AS JSONB), TRUE)"""),
        {"c": str(call_id), "r": '{"durationInSeconds": "60"}'})
    await db.commit()

    with pytest.raises(Exception) as exc:
        await db.execute(text("DELETE FROM call_provider_events"))
        await db.commit()
    await db.rollback()
    assert "append-only" in str(exc.value)

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("UPDATE call_provider_events SET raw = CAST(:r AS JSONB)"),
            {"r": '{"durationInSeconds": "9999"}'})
        await db.commit()
    await db.rollback()
    assert "cannot be rewritten" in str(exc.value)

    # Flipping `applied` is the one permitted change.
    await db.execute(text("UPDATE call_provider_events SET applied = TRUE"))
    await db.commit()


@pytest.mark.asyncio
async def test_one_session_id_maps_to_one_call(db):
    """A duplicate callback must not be able to find two rows to write to."""
    user = await _user(db)
    await _call_row(db, user, session_id="ATVId_dup", source="UNKNOWN",
                    duration=None, status="IN_PROGRESS")
    with pytest.raises(Exception):
        await _call_row(db, user, session_id="ATVId_dup", source="UNKNOWN",
                        duration=None, status="IN_PROGRESS")
    await db.rollback()
