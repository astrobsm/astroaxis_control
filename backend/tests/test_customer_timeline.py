"""One customer's whole history, assembled from what already exists.

The property worth protecting is that nothing is stored: a cancelled order or
a paid invoice must change the timeline the moment it changes the source. The
second is that the module reads eleven tables and must not fall over when a
deployment is part-way through the migration chain and some are absent.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.services import customer_timeline as svc

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")

NOW = datetime.now(timezone.utc)

# Only tables this file owns are dropped; the rest are created if absent. A
# test file that drops a table it does not own destroys it for every suite
# running afterwards.
SCHEMA = """
DROP TABLE IF EXISTS payments CASCADE;
DROP TABLE IF EXISTS invoices CASCADE;
DROP TABLE IF EXISTS sales_orders CASCADE;
DROP TABLE IF EXISTS manifest_customers CASCADE;
DROP TABLE IF EXISTS delivery_manifests CASCADE;

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL, full_name VARCHAR(255)
);
CREATE TABLE IF NOT EXISTS staff (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id VARCHAR(32) UNIQUE NOT NULL,
    first_name VARCHAR(100), last_name VARCHAR(100)
);
CREATE TABLE IF NOT EXISTS customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_code VARCHAR(32) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL, phone VARCHAR(40), email VARCHAR(255),
    address TEXT, is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE customers ADD COLUMN IF NOT EXISTS merged_into_id UUID;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS marketing_consent VARCHAR(12) DEFAULT 'UNKNOWN';
ALTER TABLE customers ADD COLUMN IF NOT EXISTS do_not_contact BOOLEAN DEFAULT FALSE;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS whatsapp_number VARCHAR(32);
ALTER TABLE customers ADD COLUMN IF NOT EXISTS customer_type VARCHAR(24);
ALTER TABLE customers ADD COLUMN IF NOT EXISTS assigned_staff_id UUID;

CREATE TABLE sales_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_number VARCHAR(64) UNIQUE NOT NULL,
    customer_id UUID REFERENCES customers(id),
    status VARCHAR(32) DEFAULT 'confirmed',
    order_date TIMESTAMPTZ DEFAULT NOW(),
    total_amount NUMERIC(18,2) DEFAULT 0,
    sales_channel VARCHAR(32)
);
CREATE TABLE invoices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_number VARCHAR(64) UNIQUE NOT NULL,
    customer_id UUID REFERENCES customers(id),
    invoice_date TIMESTAMPTZ DEFAULT NOW(),
    due_date TIMESTAMPTZ,
    total_amount NUMERIC(18,2) DEFAULT 0,
    paid_amount NUMERIC(18,2) DEFAULT 0,
    status VARCHAR(32) DEFAULT 'pending'
);
CREATE TABLE payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id UUID REFERENCES invoices(id),
    payment_method VARCHAR(40), amount NUMERIC(18,2) DEFAULT 0,
    payment_date TIMESTAMPTZ DEFAULT NOW(), reference VARCHAR(160),
    notes TEXT, created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE delivery_manifests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    manifest_number VARCHAR(64) UNIQUE NOT NULL,
    delivery_date DATE, status VARCHAR(24) DEFAULT 'preparing'
);
CREATE TABLE manifest_customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    manifest_id UUID REFERENCES delivery_manifests(id) ON DELETE CASCADE,
    customer_id UUID REFERENCES customers(id),
    customer_name VARCHAR(255), status VARCHAR(24) DEFAULT 'pending',
    receiver_name VARCHAR(255), delivered_at TIMESTAMPTZ,
    failed_at TIMESTAMPTZ, failure_reason TEXT
);
"""


@pytest_asyncio.fixture
async def db():
    seng = create_engine(TEST_DB.replace("+asyncpg", ""), future=True)
    with seng.connect() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        for stmt in SCHEMA.strip().split(";"):
            if stmt.strip():
                c.execute(text(stmt))
        c.commit()
    seng.dispose()
    eng = create_async_engine(TEST_DB, future=True)
    maker = sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await eng.dispose()


async def _customer(db, name="Niger Foundation Hospital"):
    cid = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO customers (id, customer_code, name, phone) "
        "VALUES (:i, :c, :n, '08031234567')"),
        {"i": str(cid), "c": f"CUS{uuid.uuid4().hex[:6].upper()}", "n": name})
    return cid


async def _order(db, customer, *, days_ago=10, amount=50000,
                 status="confirmed"):
    oid = uuid.uuid4()
    await db.execute(text("""
        INSERT INTO sales_orders
            (id, order_number, customer_id, status, order_date, total_amount)
        VALUES (:i, :n, :c, :s, :d, :a)
    """), {"i": str(oid), "n": f"SO{uuid.uuid4().hex[:8].upper()}",
           "c": str(customer), "s": status,
           "d": NOW - timedelta(days=days_ago), "a": amount})
    return oid


async def _invoice(db, customer, *, days_ago=9, total=50000, paid=0,
                   due_days_ago=None, status="pending"):
    iid = uuid.uuid4()
    due = (NOW - timedelta(days=due_days_ago)) if due_days_ago is not None else None
    await db.execute(text("""
        INSERT INTO invoices
            (id, invoice_number, customer_id, invoice_date, due_date,
             total_amount, paid_amount, status)
        VALUES (:i, :n, :c, :d, :due, :t, :p, :s)
    """), {"i": str(iid), "n": f"INV{uuid.uuid4().hex[:8].upper()}",
           "c": str(customer), "d": NOW - timedelta(days=days_ago),
           "due": due, "t": total, "p": paid, "s": status})
    return iid


def _kinds(result):
    return [e["kind"] for e in result["events"]]


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_customer_with_no_history_is_not_an_error(db):
    c = await _customer(db)
    result = await svc.timeline(db, customer_id=c)
    assert result["events"] == []
    assert result["customer"]["name"] == "Niger Foundation Hospital"


@pytest.mark.asyncio
async def test_an_unknown_customer_is_a_404(db):
    with pytest.raises(HTTPException) as e:
        await svc.timeline(db, customer_id=uuid.uuid4())
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_orders_invoices_and_payments_appear_together(db):
    c = await _customer(db)
    await _order(db, c, days_ago=10, amount=50000)
    inv = await _invoice(db, c, days_ago=9, total=50000, paid=50000,
                         status="paid")
    await db.execute(text("""
        INSERT INTO payments (invoice_id, amount, payment_method, payment_date)
        VALUES (:i, 50000, 'bank_transfer', :d)
    """), {"i": str(inv), "d": NOW - timedelta(days=8)})

    result = await svc.timeline(db, customer_id=c)
    assert set(_kinds(result)) == {"ORDER", "INVOICE", "PAYMENT"}
    assert result["total_events"] == 3


@pytest.mark.asyncio
async def test_the_newest_event_comes_first(db):
    c = await _customer(db)
    await _order(db, c, days_ago=30)
    await _order(db, c, days_ago=2)
    result = await svc.timeline(db, customer_id=c)
    assert result["events"][0]["at"] > result["events"][1]["at"]


@pytest.mark.asyncio
async def test_a_cancelled_order_is_shown_as_cancelled(db):
    """It happened, so it stays on the history -- flagged, not hidden."""
    c = await _customer(db)
    await _order(db, c, status="cancelled")
    result = await svc.timeline(db, customer_id=c)
    assert result["events"][0]["tone"] == "danger"
    assert "cancelled" in result["events"][0]["detail"]


@pytest.mark.asyncio
async def test_an_overdue_invoice_is_marked(db):
    c = await _customer(db)
    await _invoice(db, c, total=80000, paid=0, due_days_ago=40)
    result = await svc.timeline(db, customer_id=c)
    invoice = next(e for e in result["events"] if e["kind"] == "INVOICE")
    assert invoice["tone"] == "danger"
    assert "outstanding" in invoice["detail"]


@pytest.mark.asyncio
async def test_a_paid_invoice_is_not_marked_overdue(db):
    c = await _customer(db)
    await _invoice(db, c, total=80000, paid=80000, due_days_ago=40,
                   status="paid")
    result = await svc.timeline(db, customer_id=c)
    invoice = next(e for e in result["events"] if e["kind"] == "INVOICE")
    assert invoice["tone"] == "info"


@pytest.mark.asyncio
async def test_a_failed_delivery_shows_its_reason(db):
    c = await _customer(db)
    m = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO delivery_manifests (id, manifest_number, delivery_date) "
        "VALUES (:i, 'MF1', CURRENT_DATE)"), {"i": str(m)})
    await db.execute(text("""
        INSERT INTO manifest_customers
            (manifest_id, customer_id, customer_name, status, failed_at,
             failure_reason)
        VALUES (:m, :c, 'Hospital', 'failed', :t, 'Customer could not pay')
    """), {"m": str(m), "c": str(c), "t": NOW - timedelta(days=1)})

    result = await svc.timeline(db, customer_id=c)
    event = next(e for e in result["events"] if e["kind"] == "DELIVERY")
    assert event["title"] == "Delivery failed"
    assert event["detail"] == "Customer could not pay"
    assert event["tone"] == "danger"


@pytest.mark.asyncio
async def test_nothing_is_stored_so_a_change_shows_immediately(db):
    """The property the whole design rests on."""
    c = await _customer(db)
    o = await _order(db, c, status="confirmed")
    assert (await svc.timeline(db, customer_id=c))["events"][0]["tone"] == "info"

    await db.execute(text(
        "UPDATE sales_orders SET status = 'cancelled' WHERE id = :i"),
        {"i": str(o)})
    assert (await svc.timeline(db, customer_id=c))["events"][0]["tone"] == "danger"


@pytest.mark.asyncio
async def test_a_source_missing_its_columns_is_skipped_not_fatal(db):
    """A part-applied migration leaves a table without a later column.

    This is not hypothetical: in this suite `call_logs` exists as a two-column
    stub built by another test file, and checking only that the table existed
    made the whole timeline fail on `cl.user_id does not exist`.
    """
    c = await _customer(db)
    await _order(db, c)

    await db.execute(text("DROP TABLE IF EXISTS call_logs CASCADE"))
    await db.execute(text(
        "CREATE TABLE call_logs (id UUID PRIMARY KEY, customer_id UUID)"))

    assert await svc._exists(db, "call_logs") is True
    assert await svc._exists(db, "call_logs", "user_id") is False

    result = await svc.timeline(db, customer_id=c)
    assert result["total_events"] >= 1, "the rest of the history still shows"


@pytest.mark.asyncio
async def test_a_missing_table_is_skipped_rather_than_raising(db):
    c = await _customer(db)
    await _order(db, c)
    assert await svc._exists(db, "a_table_that_does_not_exist") is False
    result = await svc.timeline(db, customer_id=c)
    assert result["total_events"] >= 1


@pytest.mark.asyncio
async def test_the_limit_reports_what_was_left_out(db):
    c = await _customer(db)
    for i in range(6):
        await _order(db, c, days_ago=i + 1)
    result = await svc.timeline(db, customer_id=c, limit=3)
    assert result["shown"] == 3
    assert result["total_events"] == 6


@pytest.mark.asyncio
async def test_events_are_counted_by_kind(db):
    c = await _customer(db)
    await _order(db, c, days_ago=5)
    await _order(db, c, days_ago=3)
    await _invoice(db, c, days_ago=2)
    result = await svc.timeline(db, customer_id=c)
    assert result["counts"]["ORDER"] == 2
    assert result["counts"]["INVOICE"] == 1


# ---------------------------------------------------------------------------
# The summary
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_summary_is_computed_not_stored(db):
    c = await _customer(db)
    await _order(db, c, days_ago=60, amount=100000)
    await _order(db, c, days_ago=30, amount=200000)

    s = await svc.summary(db, customer_id=c)
    assert s["orders"] == 2
    assert s["lifetime_value"] == 300000.0
    assert s["average_order"] == 150000.0
    assert s["average_interval_days"] == 30
    assert s["days_since_last_order"] == 30


@pytest.mark.asyncio
async def test_cancelled_orders_are_not_lifetime_value(db):
    c = await _customer(db)
    await _order(db, c, amount=100000)
    await _order(db, c, amount=500000, status="cancelled")
    s = await svc.summary(db, customer_id=c)
    assert s["orders"] == 1
    assert s["lifetime_value"] == 100000.0


@pytest.mark.asyncio
async def test_outstanding_counts_only_what_is_unpaid(db):
    c = await _customer(db)
    await _invoice(db, c, total=100000, paid=40000, status="partial")
    await _invoice(db, c, total=50000, paid=50000, status="paid")
    s = await svc.summary(db, customer_id=c)
    assert s["outstanding"] == 60000.0


@pytest.mark.asyncio
async def test_a_customer_with_one_order_has_no_interval(db):
    """One order gives no gap, and a made-up interval would be worse."""
    c = await _customer(db)
    await _order(db, c)
    s = await svc.summary(db, customer_id=c)
    assert s["orders"] == 1
    assert s["average_interval_days"] is None


@pytest.mark.asyncio
async def test_a_customer_who_never_ordered_is_all_zeroes(db):
    c = await _customer(db)
    s = await svc.summary(db, customer_id=c)
    assert s["orders"] == 0
    assert s["lifetime_value"] == 0.0
    assert s["last_order"] is None
