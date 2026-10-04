"""The delivery workflow.

The behaviour that matters is the failure path. A successful delivery needs
almost no logic; a failed one is where stock goes missing, and these tests are
mostly about that.
"""
from __future__ import annotations

import os
import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.services import delivery as svc
from app.services import messaging as msg

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")

# Only tables this module owns are dropped. raw_materials, products and the
# rest are created IF NOT EXISTS -- a test file that drops a table it does not
# own destroys it for every suite running afterwards, which has already
# happened three times here.
SCHEMA = """
DROP TABLE IF EXISTS delivery_events CASCADE;
DROP TABLE IF EXISTS manifest_items CASCADE;
DROP TABLE IF EXISTS manifest_customers CASCADE;
DROP TABLE IF EXISTS delivery_manifests CASCADE;
DROP TABLE IF EXISTS outbound_messages CASCADE;
DROP TABLE IF EXISTS app_setting_changes CASCADE;
DROP TABLE IF EXISTS app_settings CASCADE;

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL, full_name VARCHAR(255),
    role VARCHAR(50) DEFAULT 'admin', hashed_password VARCHAR(255) DEFAULT 'x'
);
CREATE TABLE IF NOT EXISTS warehouses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(32), name VARCHAR(255) NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sku VARCHAR(64) UNIQUE NOT NULL, name VARCHAR(255) NOT NULL
);
CREATE TABLE IF NOT EXISTS customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_code VARCHAR(32) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL
);
-- customers may already exist from another suite, in which case the CREATE
-- above is a no-op and these columns may be missing. The consent migration
-- builds an index over merged_into_id, so it has to be there either way.
ALTER TABLE customers ADD COLUMN IF NOT EXISTS merged_into_id UUID;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS marketing_consent VARCHAR(12) NOT NULL DEFAULT 'UNKNOWN';
ALTER TABLE customers ADD COLUMN IF NOT EXISTS do_not_contact BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS do_not_contact_until DATE;

CREATE TABLE IF NOT EXISTS stock_levels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    warehouse_id UUID NOT NULL REFERENCES warehouses(id),
    product_id UUID REFERENCES products(id),
    raw_material_id UUID,
    current_stock NUMERIC(18,6) DEFAULT 0,
    reserved_stock NUMERIC(18,6) DEFAULT 0,
    min_stock NUMERIC(18,6) DEFAULT 0, max_stock NUMERIC(18,6) DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS stock_movements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    warehouse_id UUID, product_id UUID, raw_material_id UUID,
    movement_type VARCHAR(32) NOT NULL, quantity NUMERIC(18,6) NOT NULL,
    reference VARCHAR(255), notes TEXT, batch_id UUID,
    unit_cost NUMERIC(18,6), created_by UUID,
    movement_date TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE delivery_manifests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    manifest_number VARCHAR(64) UNIQUE NOT NULL,
    delivery_date DATE, logistics_officer VARCHAR(255),
    vehicle_details VARCHAR(255), driver_name VARCHAR(255),
    driver_phone VARCHAR(40), transport_mode VARCHAR(40),
    transport_cost NUMERIC(18,2) DEFAULT 0,
    additional_charges NUMERIC(18,2) DEFAULT 0,
    total_cost NUMERIC(18,2) DEFAULT 0,
    status VARCHAR(24) DEFAULT 'preparing', notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE manifest_customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    manifest_id UUID REFERENCES delivery_manifests(id) ON DELETE CASCADE,
    customer_id UUID REFERENCES customers(id),
    customer_name VARCHAR(255), customer_phone VARCHAR(40),
    delivery_address TEXT, city VARCHAR(120), state VARCHAR(120),
    receiver_name VARCHAR(255), receiver_phone VARCHAR(40),
    physical_invoice_number VARCHAR(64), delivery_time TIMESTAMPTZ,
    signature_collected BOOLEAN DEFAULT FALSE, delivery_notes TEXT,
    status VARCHAR(24) DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE manifest_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    manifest_customer_id UUID REFERENCES manifest_customers(id) ON DELETE CASCADE,
    product_id UUID REFERENCES products(id),
    product_name VARCHAR(255), sku VARCHAR(64),
    quantity NUMERIC(18,6), unit VARCHAR(32),
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
        _apply(c, "u1023456789t_delivery_workflow.py")
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
        self.full_name = "Logistics Officer"
        self.username = "logistics"


async def _user(db):
    # Only id, email and full_name: different suites build `users` with
    # different columns, and this file does not own that table.
    uid = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO users (id, email, full_name) "
        "VALUES (:i, :e, 'Logistics Officer')"),
        {"i": str(uid), "e": f"{uid.hex[:8]}@t.local"})
    return FakeUser(uid)


async def _setup(db, *, stock=100, qty=10, drops=1):
    """A warehouse with stock, a product, a manifest and its drops."""
    w = uuid.uuid4()
    # The real warehouses table requires a code; this file does not own it and
    # therefore takes it as it finds it.
    await db.execute(text(
        "INSERT INTO warehouses (id, code, name) VALUES (:i, :c, 'Main')"),
        {"i": str(w), "c": uuid.uuid4().hex[:8].upper()})
    p = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO products (id, sku, name) VALUES (:i, :s, 'Hera Gel')"),
        {"i": str(p), "s": uuid.uuid4().hex[:8].upper()})
    await db.execute(text(
        "INSERT INTO stock_levels (warehouse_id, product_id, current_stock) "
        "VALUES (:w, :p, :q)"), {"w": str(w), "p": str(p), "q": stock})

    m = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO delivery_manifests (id, manifest_number, delivery_date) "
        "VALUES (:i, :n, CURRENT_DATE)"),
        {"i": str(m), "n": f"MF{uuid.uuid4().hex[:6].upper()}"})

    drop_ids = []
    for i in range(drops):
        c = uuid.uuid4()
        await db.execute(text(
            "INSERT INTO customers (id, customer_code, name) "
            "VALUES (:i, :c, :n)"),
            {"i": str(c), "c": f"CUS{uuid.uuid4().hex[:6].upper()}",
             "n": f"Hospital {i}"})
        d = uuid.uuid4()
        await db.execute(text("""
            INSERT INTO manifest_customers
                (id, manifest_id, customer_id, customer_name, customer_phone)
            VALUES (:i, :m, :c, :n, '08031234567')
        """), {"i": str(d), "m": str(m), "c": str(c), "n": f"Hospital {i}"})
        await db.execute(text("""
            INSERT INTO manifest_items
                (manifest_customer_id, product_id, product_name, quantity, unit)
            VALUES (:d, :p, 'Hera Gel', :q, 'unit')
        """), {"d": str(d), "p": str(p), "q": qty})
        drop_ids.append(d)

    return {"warehouse": w, "product": p, "manifest": m, "drops": drop_ids}


async def _stock(db, warehouse, product):
    return float((await db.execute(text(
        "SELECT current_stock FROM stock_levels "
        "WHERE warehouse_id = :w AND product_id = :p"),
        {"w": str(warehouse), "p": str(product)})).scalar())


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_run_cannot_arrive_without_leaving(db):
    """preparing -> completed is a van that never left the yard."""
    s = await _setup(db)
    with pytest.raises(HTTPException) as e:
        await svc.set_manifest_status(db, manifest_id=s["manifest"],
                                      status="completed")
    assert e.value.status_code == 400
    assert "cannot become completed" in str(e.value.detail)


@pytest.mark.asyncio
async def test_an_unrecognised_status_is_refused(db):
    """The old endpoint wrote any string it was handed."""
    s = await _setup(db)
    with pytest.raises(HTTPException) as e:
        await svc.set_manifest_status(db, manifest_id=s["manifest"],
                                      status="dispached")
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_dispatching_puts_every_drop_out_for_delivery(db):
    user = await _user(db)
    s = await _setup(db, drops=3)
    await svc.set_manifest_status(db, manifest_id=s["manifest"],
                                  status="dispatched", actor=user)
    statuses = [r[0] for r in (await db.execute(text(
        "SELECT status FROM manifest_customers WHERE manifest_id = :m"),
        {"m": str(s["manifest"])})).fetchall()]
    assert statuses == ["out_for_delivery"] * 3


@pytest.mark.asyncio
async def test_a_run_cannot_be_closed_with_drops_unaccounted_for(db):
    """Closing it would record a delivery nobody made."""
    user = await _user(db)
    s = await _setup(db, drops=2)
    await svc.set_manifest_status(db, manifest_id=s["manifest"],
                                  status="dispatched", actor=user)
    await svc.set_drop_status(db, drop_id=s["drops"][0], status="delivered",
                              actor=user)

    with pytest.raises(HTTPException) as e:
        await svc.set_manifest_status(db, manifest_id=s["manifest"],
                                      status="completed", actor=user)
    assert "1 drop(s)" in str(e.value.detail)
    assert "no outcome recorded" in str(e.value.detail)


@pytest.mark.asyncio
async def test_a_run_closes_once_every_drop_has_an_outcome(db):
    user = await _user(db)
    s = await _setup(db, drops=2)
    await svc.set_manifest_status(db, manifest_id=s["manifest"],
                                  status="dispatched", actor=user)
    await svc.set_drop_status(db, drop_id=s["drops"][0], status="delivered",
                              actor=user)
    await svc.set_drop_status(db, drop_id=s["drops"][1], status="failed",
                              reason="Customer closed", actor=user)
    result = await svc.set_manifest_status(db, manifest_id=s["manifest"],
                                           status="completed", actor=user)
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_cancelling_a_run_needs_a_reason(db):
    s = await _setup(db)
    with pytest.raises(HTTPException) as e:
        await svc.set_manifest_status(db, manifest_id=s["manifest"],
                                      status="cancelled")
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_a_completed_run_is_final(db):
    user = await _user(db)
    s = await _setup(db, drops=1)
    await svc.set_manifest_status(db, manifest_id=s["manifest"],
                                  status="dispatched", actor=user)
    await svc.set_drop_status(db, drop_id=s["drops"][0], status="delivered",
                              actor=user)
    await svc.set_manifest_status(db, manifest_id=s["manifest"],
                                  status="completed", actor=user)
    with pytest.raises(HTTPException) as e:
        await svc.set_manifest_status(db, manifest_id=s["manifest"],
                                      status="in_transit", actor=user)
    assert "final state" in str(e.value.detail)


# ---------------------------------------------------------------------------
# Failure, which is the point
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_failed_delivery_needs_a_reason(db):
    user = await _user(db)
    s = await _setup(db)
    with pytest.raises(HTTPException) as e:
        await svc.set_drop_status(db, drop_id=s["drops"][0], status="failed",
                                  actor=user)
    assert e.value.status_code == 400
    assert "why the delivery failed" in str(e.value.detail)


@pytest.mark.asyncio
async def test_the_database_refuses_a_failure_with_no_reason(db):
    """Enforced by CHECK, so an API that forgets cannot bypass it."""
    s = await _setup(db)
    with pytest.raises(Exception) as e:
        await db.execute(text(
            "UPDATE manifest_customers SET status = 'failed' WHERE id = :i"),
            {"i": str(s["drops"][0])})
    assert "ck_drop_failure_reason" in str(e.value)


@pytest.mark.asyncio
async def test_a_failure_records_the_reason_and_counts_the_attempt(db):
    user = await _user(db)
    s = await _setup(db)
    await svc.set_drop_status(db, drop_id=s["drops"][0], status="failed",
                              reason="Customer could not pay", actor=user)
    row = (await db.execute(text(
        "SELECT status, failure_reason, failed_at, attempt_count "
        "FROM manifest_customers WHERE id = :i"),
        {"i": str(s["drops"][0])})).mappings().first()
    assert row["status"] == "failed"
    assert row["failure_reason"] == "Customer could not pay"
    assert row["failed_at"] is not None
    assert row["attempt_count"] == 1


@pytest.mark.asyncio
async def test_a_failed_drop_can_be_attempted_again(db):
    """A failure is not final; the goods are still on the van."""
    user = await _user(db)
    s = await _setup(db)
    await svc.set_drop_status(db, drop_id=s["drops"][0], status="failed",
                              reason="Nobody there", actor=user)
    await svc.set_drop_status(db, drop_id=s["drops"][0],
                              status="out_for_delivery", actor=user)
    await svc.set_drop_status(db, drop_id=s["drops"][0], status="delivered",
                              actor=user)
    row = (await db.execute(text(
        "SELECT status, attempt_count FROM manifest_customers WHERE id = :i"),
        {"i": str(s["drops"][0])})).mappings().first()
    assert row["status"] == "delivered"
    assert row["attempt_count"] == 2


# ---------------------------------------------------------------------------
# Returning stock, which is the half that matters
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_returning_a_failed_delivery_puts_the_stock_back(db):
    """Stock was deducted at invoicing; after a failure it is on the shelf
    and missing from the system until this runs."""
    user = await _user(db)
    s = await _setup(db, stock=100, qty=10)
    before = await _stock(db, s["warehouse"], s["product"])

    await svc.set_drop_status(db, drop_id=s["drops"][0], status="failed",
                              reason="Customer refused", actor=user)
    result = await svc.return_to_stock(
        db, drop_id=s["drops"][0], warehouse_id=s["warehouse"], actor=user)

    assert result["returned"][0]["quantity"] == 10.0
    assert await _stock(db, s["warehouse"], s["product"]) == before + 10

    # Selected by reference: this suite does not own stock_movements and the
    # table carries rows from other tests.
    move = (await db.execute(text(
        "SELECT movement_type, quantity FROM stock_movements "
        "WHERE reference = :r"),
        {"r": f"Failed delivery {s['drops'][0]}"})).mappings().first()
    assert move["movement_type"] == "RETURN"
    assert float(move["quantity"]) == 10.0


@pytest.mark.asyncio
async def test_returning_twice_is_refused(db):
    """Returning twice would invent inventory."""
    user = await _user(db)
    s = await _setup(db, stock=100, qty=10)
    await svc.set_drop_status(db, drop_id=s["drops"][0], status="failed",
                              reason="Refused", actor=user)
    await svc.return_to_stock(db, drop_id=s["drops"][0],
                              warehouse_id=s["warehouse"], actor=user)

    with pytest.raises(HTTPException) as e:
        await svc.return_to_stock(db, drop_id=s["drops"][0],
                                  warehouse_id=s["warehouse"], actor=user)
    assert e.value.status_code == 400
    # What matters is the stock, not which of the two guards caught it: the
    # status check fires first, and the stock_returned_at guard stands behind
    # it for anything that changes status another way.
    assert await _stock(db, s["warehouse"], s["product"]) == 110.0


@pytest.mark.asyncio
async def test_only_a_failed_delivery_has_goods_to_return(db):
    user = await _user(db)
    s = await _setup(db)
    await svc.set_drop_status(db, drop_id=s["drops"][0], status="delivered",
                              actor=user)
    with pytest.raises(HTTPException) as e:
        await svc.return_to_stock(db, drop_id=s["drops"][0],
                                  warehouse_id=s["warehouse"], actor=user)
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_goods_awaiting_return_are_listed(db):
    """Whose goods are in the building and missing from stock."""
    user = await _user(db)
    s = await _setup(db, drops=2)
    await svc.set_drop_status(db, drop_id=s["drops"][0], status="failed",
                              reason="Customer closed", actor=user)
    await svc.set_drop_status(db, drop_id=s["drops"][1], status="delivered",
                              actor=user)

    waiting = await svc.awaiting_return(db)
    assert waiting["count"] == 1
    assert waiting["drops"][0]["reason"] == "Customer closed"

    await svc.return_to_stock(db, drop_id=s["drops"][0],
                              warehouse_id=s["warehouse"], actor=user)
    assert (await svc.awaiting_return(db))["count"] == 0


@pytest.mark.asyncio
async def test_a_returned_drop_is_final(db):
    user = await _user(db)
    s = await _setup(db)
    await svc.set_drop_status(db, drop_id=s["drops"][0], status="failed",
                              reason="Refused", actor=user)
    await svc.return_to_stock(db, drop_id=s["drops"][0],
                              warehouse_id=s["warehouse"], actor=user)
    with pytest.raises(HTTPException) as e:
        await svc.set_drop_status(db, drop_id=s["drops"][0],
                                  status="delivered", actor=user)
    assert "final state" in str(e.value.detail)


# ---------------------------------------------------------------------------
# History and notifications
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_every_change_is_recorded_with_who_made_it(db):
    user = await _user(db)
    s = await _setup(db)
    await svc.set_manifest_status(db, manifest_id=s["manifest"],
                                  status="dispatched", actor=user)
    await svc.set_drop_status(db, drop_id=s["drops"][0], status="failed",
                              reason="Nobody there", actor=user)

    events = await svc.history(db, manifest_id=s["manifest"])
    assert [e["to"] for e in events] == ["failed", "out_for_delivery",
                                         "dispatched"]
    assert all(e["actor"] == "Logistics Officer" for e in events)


@pytest.mark.asyncio
async def test_the_history_cannot_be_rewritten(db):
    user = await _user(db)
    s = await _setup(db)
    await svc.set_manifest_status(db, manifest_id=s["manifest"],
                                  status="dispatched", actor=user)
    with pytest.raises(Exception) as e:
        await db.execute(text(
            "UPDATE delivery_events SET to_status = 'completed'"))
    assert "append-only" in str(e.value).lower()


@pytest.mark.asyncio
async def test_no_customer_is_messaged_while_the_setting_is_off(db):
    user = await _user(db)
    s = await _setup(db)
    result = await svc.set_manifest_status(db, manifest_id=s["manifest"],
                                           status="dispatched", actor=user)
    assert result["messages_queued"] == 0
    n = (await db.execute(text(
        "SELECT COUNT(*) FROM outbound_messages"))).scalar()
    assert n == 0


@pytest.mark.asyncio
async def test_a_delivery_message_is_queued_but_still_not_sent(db):
    user = await _user(db)
    s = await _setup(db)
    for k in ("OUTBOUND_MESSAGING_ENABLED", "WHATSAPP_ENABLED",
              "DELIVERY_NOTIFY_CUSTOMER"):
        await msg.set_setting(db, key=k, value="true")

    result = await svc.set_manifest_status(db, manifest_id=s["manifest"],
                                           status="dispatched", actor=user)
    assert result["messages_queued"] == 1

    row = (await db.execute(text(
        "SELECT body, category, status FROM outbound_messages"))).mappings().first()
    assert "out for delivery" in row["body"]
    assert row["category"] == "TRANSACTIONAL"
    assert row["status"] == "QUEUED", "queued, and nothing drains the outbox"


@pytest.mark.asyncio
async def test_an_internal_status_is_not_news_to_the_customer(db):
    """in_transit tells the person waiting nothing they did not know."""
    user = await _user(db)
    s = await _setup(db)
    for k in ("OUTBOUND_MESSAGING_ENABLED", "WHATSAPP_ENABLED",
              "DELIVERY_NOTIFY_CUSTOMER"):
        await msg.set_setting(db, key=k, value="true")
    await svc.set_manifest_status(db, manifest_id=s["manifest"],
                                  status="dispatched", actor=user)
    before = (await db.execute(text(
        "SELECT COUNT(*) FROM outbound_messages"))).scalar()
    await svc.set_manifest_status(db, manifest_id=s["manifest"],
                                  status="in_transit", actor=user)
    after = (await db.execute(text(
        "SELECT COUNT(*) FROM outbound_messages"))).scalar()
    assert after == before


@pytest.mark.asyncio
async def test_a_run_that_went_out_and_was_never_closed_is_surfaced(db):
    """Sixteen of these exist in the live database, up to five months old."""
    user = await _user(db)
    s = await _setup(db, drops=2)
    await svc.set_manifest_status(db, manifest_id=s["manifest"],
                                  status="dispatched", actor=user)
    await db.execute(text(
        "UPDATE delivery_manifests SET delivery_date = CURRENT_DATE - 30 "
        "WHERE id = :i"), {"i": str(s["manifest"])})

    result = await svc.unclosed_runs(db)
    assert result["count"] == 1
    assert result["open_drops"] == 2
    assert result["runs"][0]["days_old"] == 30


@pytest.mark.asyncio
async def test_a_run_closed_properly_is_not_surfaced(db):
    user = await _user(db)
    s = await _setup(db, drops=1)
    await svc.set_manifest_status(db, manifest_id=s["manifest"],
                                  status="dispatched", actor=user)
    await db.execute(text(
        "UPDATE delivery_manifests SET delivery_date = CURRENT_DATE - 30 "
        "WHERE id = :i"), {"i": str(s["manifest"])})
    await svc.set_drop_status(db, drop_id=s["drops"][0], status="delivered",
                              actor=user)
    await svc.set_manifest_status(db, manifest_id=s["manifest"],
                                  status="completed", actor=user)
    assert (await svc.unclosed_runs(db))["count"] == 0


@pytest.mark.asyncio
async def test_todays_run_is_not_yet_overdue(db):
    user = await _user(db)
    s = await _setup(db, drops=1)
    await svc.set_manifest_status(db, manifest_id=s["manifest"],
                                  status="dispatched", actor=user)
    assert (await svc.unclosed_runs(db))["count"] == 0
