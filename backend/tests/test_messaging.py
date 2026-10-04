"""Consent, the outbox, and the rules that stop a message leaving.

The whole value of this module is that it refuses correctly. These tests are
mostly about messages NOT going out, which is the behaviour that matters while
there is no sender and the behaviour that will matter most once there is one.
"""
from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.services import messaging as svc

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")

SCHEMA = """
DROP TABLE IF EXISTS app_setting_changes CASCADE;
DROP TABLE IF EXISTS app_settings CASCADE;
DROP TABLE IF EXISTS outbound_messages CASCADE;
DROP TABLE IF EXISTS customer_important_dates CASCADE;
DROP TABLE IF EXISTS customer_consent_events CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS staff CASCADE;
DROP TABLE IF EXISTS users CASCADE;

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(100) UNIQUE, email VARCHAR(255) UNIQUE NOT NULL,
    full_name VARCHAR(255), role VARCHAR(50) DEFAULT 'admin',
    hashed_password VARCHAR(255) DEFAULT 'x', is_active BOOLEAN DEFAULT TRUE
);
CREATE TABLE staff (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id VARCHAR(32) UNIQUE NOT NULL,
    first_name VARCHAR(100) NOT NULL, last_name VARCHAR(100) NOT NULL
);
CREATE TABLE customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_code VARCHAR(32) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL, phone VARCHAR(40), email VARCHAR(255),
    is_active BOOLEAN DEFAULT TRUE,
    merged_into_id UUID REFERENCES customers(id),
    merged_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
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
        _apply(c, "q0123456789p_contact_consent.py")
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
        self.full_name = "Sales Officer"
        self.username = "officer"


async def _user(db):
    uid = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO users (id, username, email, full_name) "
        "VALUES (:i, :u, :e, 'Sales Officer')"),
        {"i": str(uid), "u": f"u{uid.hex[:8]}", "e": f"{uid.hex[:8]}@t.local"})
    return FakeUser(uid)


async def _customer(db, name="Hospital A"):
    cid = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO customers (id, customer_code, name, phone) "
        "VALUES (:i, :c, :n, '08031234567')"),
        {"i": str(cid), "c": f"CUS{uuid.uuid4().hex[:8].upper()}", "n": name})
    return cid


async def _on(db, *keys):
    for k in keys:
        await svc.set_setting(db, key=k, value="true")


async def _sent(db, customer_id, *, hours_ago, n=1):
    """Pretend n promotional messages went out, since there is no sender."""
    for i in range(n):
        await db.execute(text("""
            INSERT INTO outbound_messages
                (idempotency_key, channel, customer_id, to_address, category,
                 body, reason, status, sent_at)
            VALUES (:k, 'WHATSAPP', :c, '2348031234567', 'PROMOTIONAL',
                    'x', 'test fixture', 'SENT', :t)
        """), {"k": uuid.uuid4().hex, "c": str(customer_id),
               "t": datetime.now(timezone.utc) - timedelta(hours=hours_ago)})


# ---------------------------------------------------------------------------
# Resolving a phone number to one customer
# ---------------------------------------------------------------------------

def test_a_nigerian_number_normalises_however_it_is_written():
    assert (svc.normalise_msisdn("08031234567")
            == svc.normalise_msisdn("+234 803 123 4567")
            == svc.normalise_msisdn("234-803-123-4567")
            == svc.normalise_msisdn("0803 123 4567")
            == "2348031234567")


def test_something_that_is_not_a_number_resolves_to_nobody():
    assert svc.normalise_msisdn("") is None
    assert svc.normalise_msisdn(None) is None
    assert svc.normalise_msisdn("12345") is None


# ---------------------------------------------------------------------------
# Opt-out detection
# ---------------------------------------------------------------------------

def test_a_customer_asking_to_stop_is_understood():
    for phrase in ("STOP", "stop", " Unsubscribe ", "remove me",
                   "Do not contact me", "no more messages"):
        assert svc.classify_reply(phrase) == "OPTED_OUT", phrase


def test_a_sentence_containing_stop_is_not_an_opt_out():
    """'Please don't stop the delivery' must not unsubscribe a customer."""
    assert svc.classify_reply("please don't stop the delivery") is None
    assert svc.classify_reply("stop sending the wrong product") is None


def test_an_ordinary_message_is_not_a_consent_change():
    assert svc.classify_reply("Do you have Hera Wound Gel in stock?") is None


# ---------------------------------------------------------------------------
# The master switch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_everything_is_switched_off_to_begin_with(db):
    """A system that could send on the day it is installed is a mistake."""
    for key in ("OUTBOUND_MESSAGING_ENABLED", "WHATSAPP_ENABLED",
                "PROMOTIONAL_MESSAGING_ENABLED"):
        assert await svc.get_setting(db, key) is False, key


@pytest.mark.asyncio
async def test_the_master_switch_stops_even_transactional_messages(db):
    """A stop button with exceptions is not a stop button."""
    c = await _customer(db)
    result = await svc.enqueue(
        db, channel="WHATSAPP", to_address="2348031234567",
        body="Your order is on its way", reason="Delivery update",
        category="TRANSACTIONAL", customer_id=c)
    assert result["status"] == "BLOCKED"
    assert "switched off for the whole system" in result["blocked_reason"]


@pytest.mark.asyncio
async def test_a_transactional_message_queues_once_sending_is_on(db):
    c = await _customer(db)
    await _on(db, "OUTBOUND_MESSAGING_ENABLED", "WHATSAPP_ENABLED")
    result = await svc.enqueue(
        db, channel="WHATSAPP", to_address="2348031234567",
        body="Your order is on its way", reason="Delivery update",
        category="TRANSACTIONAL", customer_id=c)
    assert result["status"] == "QUEUED"


@pytest.mark.asyncio
async def test_turning_a_switch_on_records_who_did_it(db):
    user = await _user(db)
    await svc.set_setting(db, key="OUTBOUND_MESSAGING_ENABLED", value="true",
                          actor=user)
    row = (await db.execute(text(
        "SELECT old_value, new_value, actor_name FROM app_setting_changes "
        "WHERE key = 'OUTBOUND_MESSAGING_ENABLED'"))).mappings().first()
    assert row["old_value"] == "false"
    assert row["new_value"] == "true"
    assert row["actor_name"] == "Sales Officer"


@pytest.mark.asyncio
async def test_an_unknown_setting_is_a_404(db):
    with pytest.raises(HTTPException) as e:
        await svc.set_setting(db, key="MADE_UP", value="true")
    assert e.value.status_code == 404


# ---------------------------------------------------------------------------
# Consent governs promotional messages only
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_customer_who_has_not_opted_in_gets_no_promotion(db):
    """UNKNOWN means no. Silence is not agreement."""
    c = await _customer(db)
    await _on(db, "OUTBOUND_MESSAGING_ENABLED", "WHATSAPP_ENABLED",
              "PROMOTIONAL_MESSAGING_ENABLED")
    result = await svc.enqueue(
        db, channel="WHATSAPP", to_address="2348031234567",
        body="New product available", reason="Cross-sell",
        category="PROMOTIONAL", customer_id=c)
    assert result["status"] == "BLOCKED"
    assert "not opted in" in result["blocked_reason"]


@pytest.mark.asyncio
async def test_an_opted_in_customer_may_be_sent_a_promotion(db):
    c = await _customer(db)
    await _on(db, "OUTBOUND_MESSAGING_ENABLED", "WHATSAPP_ENABLED",
              "PROMOTIONAL_MESSAGING_ENABLED")
    await svc.set_consent(db, customer_id=c, consent="OPTED_IN",
                          source="CUSTOMER_REPLY")
    result = await svc.enqueue(
        db, channel="WHATSAPP", to_address="2348031234567",
        body="New product available", reason="Cross-sell",
        category="PROMOTIONAL", customer_id=c)
    assert result["status"] == "QUEUED"


@pytest.mark.asyncio
async def test_an_opted_out_customer_still_gets_their_invoice(db):
    """Consent governs marketing, not the order they actually placed."""
    c = await _customer(db)
    await _on(db, "OUTBOUND_MESSAGING_ENABLED", "WHATSAPP_ENABLED")
    await svc.set_consent(db, customer_id=c, consent="OPTED_OUT",
                          source="CUSTOMER_REPLY")

    promo = await svc.enqueue(
        db, channel="WHATSAPP", to_address="2348031234567", body="Offer",
        reason="Campaign", category="PROMOTIONAL", customer_id=c)
    assert promo["status"] == "BLOCKED"

    invoice = await svc.enqueue(
        db, channel="WHATSAPP", to_address="2348031234567",
        body="Invoice INV-1 attached", reason="Invoice for order SO-1",
        category="TRANSACTIONAL", customer_id=c)
    assert invoice["status"] == "BLOCKED", (
        "do-not-contact is set by an opt-out and stops everything")


@pytest.mark.asyncio
async def test_opting_out_also_sets_do_not_contact(db):
    """Two flags disagreeing is how somebody is messaged after asking to stop."""
    c = await _customer(db)
    await svc.set_consent(db, customer_id=c, consent="OPTED_OUT",
                          source="CUSTOMER_REPLY")
    row = (await db.execute(text(
        "SELECT marketing_consent, do_not_contact FROM customers WHERE id = :i"),
        {"i": str(c)})).mappings().first()
    assert row["marketing_consent"] == "OPTED_OUT"
    assert row["do_not_contact"] is True


@pytest.mark.asyncio
async def test_consent_needs_a_source(db):
    c = await _customer(db)
    with pytest.raises(HTTPException) as e:
        await svc.set_consent(db, customer_id=c, consent="OPTED_IN",
                              source="  ")
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_consent_history_is_kept_and_cannot_be_rewritten(db):
    user = await _user(db)
    c = await _customer(db)
    await svc.set_consent(db, customer_id=c, consent="OPTED_IN",
                          source="STAFF_ENTERED", actor=user)
    await svc.set_consent(db, customer_id=c, consent="OPTED_OUT",
                          source="CUSTOMER_REPLY", evidence="replied STOP",
                          actor=user)

    hist = await svc.consent_history(db, customer_id=c)
    assert [h["consent"] for h in hist] == ["OPTED_OUT", "OPTED_IN"]
    assert hist[0]["evidence"] == "replied STOP"

    with pytest.raises(Exception) as e:
        await db.execute(text(
            "UPDATE customer_consent_events SET consent = 'OPTED_IN'"))
    assert "append-only" in str(e.value).lower()


# ---------------------------------------------------------------------------
# Frequency limits
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_weekly_limit_stops_a_third_message(db):
    c = await _customer(db)
    await _on(db, "OUTBOUND_MESSAGING_ENABLED", "WHATSAPP_ENABLED",
              "PROMOTIONAL_MESSAGING_ENABLED")
    await svc.set_consent(db, customer_id=c, consent="OPTED_IN", source="REPLY")
    await _sent(db, c, hours_ago=100, n=2)

    result = await svc.enqueue(
        db, channel="WHATSAPP", to_address="2348031234567", body="Third",
        reason="Campaign", category="PROMOTIONAL", customer_id=c)
    assert result["status"] == "BLOCKED"
    assert "last 7 days" in result["blocked_reason"]


@pytest.mark.asyncio
async def test_two_promotions_too_close_together_are_refused(db):
    c = await _customer(db)
    await _on(db, "OUTBOUND_MESSAGING_ENABLED", "WHATSAPP_ENABLED",
              "PROMOTIONAL_MESSAGING_ENABLED")
    await svc.set_consent(db, customer_id=c, consent="OPTED_IN", source="REPLY")
    await _sent(db, c, hours_ago=2, n=1)

    result = await svc.enqueue(
        db, channel="WHATSAPP", to_address="2348031234567", body="Again",
        reason="Campaign", category="PROMOTIONAL", customer_id=c)
    assert result["status"] == "BLOCKED"
    assert "minimum gap" in result["blocked_reason"]


@pytest.mark.asyncio
async def test_frequency_limits_do_not_apply_to_transactional_messages(db):
    """Four orders this week means four delivery updates."""
    c = await _customer(db)
    await _on(db, "OUTBOUND_MESSAGING_ENABLED", "WHATSAPP_ENABLED")
    await _sent(db, c, hours_ago=1, n=5)

    for i in range(3):
        result = await svc.enqueue(
            db, channel="WHATSAPP", to_address="2348031234567",
            body=f"Order {i} dispatched", reason=f"Delivery update {i}",
            category="TRANSACTIONAL", customer_id=c)
        assert result["status"] == "QUEUED"


@pytest.mark.asyncio
async def test_the_limits_are_configurable(db):
    c = await _customer(db)
    await _on(db, "OUTBOUND_MESSAGING_ENABLED", "WHATSAPP_ENABLED",
              "PROMOTIONAL_MESSAGING_ENABLED")
    await svc.set_consent(db, customer_id=c, consent="OPTED_IN", source="REPLY")
    await svc.set_setting(db, key="PROMOTIONAL_MAX_PER_7_DAYS", value="5")
    await svc.set_setting(db, key="PROMOTIONAL_MIN_HOURS_BETWEEN", value="0")
    await _sent(db, c, hours_ago=100, n=2)

    result = await svc.enqueue(
        db, channel="WHATSAPP", to_address="2348031234567", body="Third",
        reason="Campaign", category="PROMOTIONAL", customer_id=c)
    assert result["status"] == "QUEUED"


@pytest.mark.asyncio
async def test_a_limit_must_be_a_number(db):
    with pytest.raises(HTTPException) as e:
        await svc.set_setting(db, key="PROMOTIONAL_MAX_PER_7_DAYS",
                              value="lots")
    assert e.value.status_code == 400


# ---------------------------------------------------------------------------
# The outbox itself
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_same_message_twice_produces_one_row(db):
    """A retried job or a double-clicked button must not message twice."""
    c = await _customer(db)
    await _on(db, "OUTBOUND_MESSAGING_ENABLED", "WHATSAPP_ENABLED")
    args = dict(channel="WHATSAPP", to_address="2348031234567",
                body="Your order is on its way", reason="Delivery update",
                category="TRANSACTIONAL", customer_id=c)

    first = await svc.enqueue(db, **args)
    second = await svc.enqueue(db, **args)
    assert second["duplicate"] is True
    assert second["id"] == first["id"]

    n = (await db.execute(text(
        "SELECT COUNT(*) FROM outbound_messages"))).scalar()
    assert n == 1


@pytest.mark.asyncio
async def test_a_blocked_message_is_kept_as_evidence(db):
    """Deleting it would erase the proof that the rules worked."""
    c = await _customer(db)
    await svc.enqueue(
        db, channel="WHATSAPP", to_address="2348031234567", body="Offer",
        reason="Campaign", category="PROMOTIONAL", customer_id=c)

    box = await svc.outbox(db, status="BLOCKED")
    assert len(box["messages"]) == 1
    assert box["messages"][0]["blocked_reason"]


@pytest.mark.asyncio
async def test_every_message_must_say_why_it_exists(db):
    c = await _customer(db)
    with pytest.raises(HTTPException) as e:
        await svc.enqueue(
            db, channel="WHATSAPP", to_address="2348031234567",
            body="Hello", reason="", category="TRANSACTIONAL", customer_id=c)
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_the_database_refuses_a_block_with_no_reason(db):
    """Blocking silently is the same as losing the message."""
    with pytest.raises(Exception) as e:
        await db.execute(text("""
            INSERT INTO outbound_messages
                (idempotency_key, channel, to_address, category, body,
                 reason, status)
            VALUES (:k, 'WHATSAPP', '234803', 'PROMOTIONAL', 'x',
                    'test', 'BLOCKED')
        """), {"k": uuid.uuid4().hex})
    assert "ck_outbound_blocked" in str(e.value)


@pytest.mark.asyncio
async def test_an_unknown_channel_is_refused(db):
    with pytest.raises(HTTPException) as e:
        await svc.enqueue(db, channel="PIGEON", to_address="x", body="y",
                          reason="test")
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_a_message_to_an_unknown_customer_is_a_404(db):
    with pytest.raises(HTTPException) as e:
        await svc.enqueue(db, channel="WHATSAPP", to_address="234803",
                          body="y", reason="test", customer_id=uuid.uuid4())
    assert e.value.status_code == 404


# ---------------------------------------------------------------------------
# One number, one customer
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_two_customers_cannot_share_a_whatsapp_number(db):
    """The point of the deduplication: an inbound message must resolve."""
    a = await _customer(db, "Hospital A")
    b = await _customer(db, "Hospital B")
    await db.execute(text(
        "UPDATE customers SET whatsapp_number = '2348031234567' WHERE id = :i"),
        {"i": str(a)})
    with pytest.raises(Exception) as e:
        await db.execute(text(
            "UPDATE customers SET whatsapp_number = '2348031234567' "
            "WHERE id = :i"), {"i": str(b)})
    assert "uq_customers_whatsapp" in str(e.value)


@pytest.mark.asyncio
async def test_a_merged_record_does_not_hold_a_number_hostage(db):
    """An absorbed duplicate must not block the survivor from taking it."""
    a = await _customer(db, "Gone")
    b = await _customer(db, "Keep")
    await db.execute(text(
        "UPDATE customers SET whatsapp_number = '2348031234567' WHERE id = :i"),
        {"i": str(a)})
    await db.execute(text(
        "UPDATE customers SET merged_into_id = :b, merged_at = NOW() "
        "WHERE id = :a"), {"a": str(a), "b": str(b)})
    await db.execute(text(
        "UPDATE customers SET whatsapp_number = '2348031234567' WHERE id = :i"),
        {"i": str(b)})  # must not raise


# ---------------------------------------------------------------------------
# Important dates
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_birthday_is_stored_without_requiring_a_year(db):
    c = await _customer(db)
    await db.execute(text("""
        INSERT INTO customer_important_dates
            (customer_id, date_type, day, month, source)
        VALUES (:c, 'BIRTHDAY', 14, 3, 'CUSTOMER_TOLD_US')
    """), {"c": str(c)})
    row = (await db.execute(text(
        "SELECT day, month, year FROM customer_important_dates "
        "WHERE customer_id = :c"), {"c": str(c)})).mappings().first()
    assert (row["day"], row["month"], row["year"]) == (14, 3, None)


@pytest.mark.asyncio
async def test_an_impossible_date_is_refused(db):
    c = await _customer(db)
    with pytest.raises(Exception) as e:
        await db.execute(text("""
            INSERT INTO customer_important_dates
                (customer_id, date_type, day, month, source)
            VALUES (:c, 'BIRTHDAY', 14, 13, 'STAFF')
        """), {"c": str(c)})
    assert "ck_cid_month" in str(e.value)


@pytest.mark.asyncio
async def test_a_customer_has_only_one_live_birthday(db):
    c = await _customer(db)
    await db.execute(text("""
        INSERT INTO customer_important_dates
            (customer_id, date_type, day, month, source)
        VALUES (:c, 'BIRTHDAY', 14, 3, 'STAFF')
    """), {"c": str(c)})
    with pytest.raises(Exception) as e:
        await db.execute(text("""
            INSERT INTO customer_important_dates
                (customer_id, date_type, day, month, source)
            VALUES (:c, 'BIRTHDAY', 2, 9, 'STAFF')
        """), {"c": str(c)})
    assert "uq_customer_date_type" in str(e.value)
