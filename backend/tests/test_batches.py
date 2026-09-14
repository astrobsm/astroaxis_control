"""Batch traceability, quarantine and recall.

The claims defended here:

  * a recalled or quarantined batch cannot be despatched, by ANY route in --
    the service and the database both refuse it;
  * stock can still come BACK IN for a recalled batch, or the goods could never
    be collected;
  * a batch balance is derived, never stored, so it cannot drift from the
    movements it is made of;
  * a batch cannot be issued for more than it holds;
  * a transfer carries the batch on both legs, so moving goods between shelves
    does not move them out of traceability;
  * a recall produces two lists -- still held, and already despatched;
  * a recalled batch can never be returned to sale;
  * status history is append-only;
  * and the traceability report states what CANNOT be traced, in quantities,
    rather than a reassuring percentage.
"""
import importlib.util
import os
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.services import batches as svc
from app.services import inventory as inv

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")
SYNC_DB = (TEST_DB or "").replace("+asyncpg", "")

BASE_SCHEMA = """
DROP TABLE IF EXISTS batch_status_events CASCADE;
DROP TABLE IF EXISTS product_batches CASCADE;
DROP TABLE IF EXISTS distributor_order_link_events CASCADE;
DROP TABLE IF EXISTS distributor_order_links CASCADE;
DROP TABLE IF EXISTS distributor_agreement_signatures CASCADE;
DROP TABLE IF EXISTS distributor_agreements CASCADE;
DROP TABLE IF EXISTS facility_corrective_actions CASCADE;
DROP TABLE IF EXISTS facility_assessment_items CASCADE;
DROP TABLE IF EXISTS facility_assessments CASCADE;
DROP TABLE IF EXISTS distributor_facilities CASCADE;
DROP TABLE IF EXISTS facility_checklist_items CASCADE;
DROP TABLE IF EXISTS distributor_audit_logs CASCADE;
DROP TABLE IF EXISTS distributor_qualifications CASCADE;
DROP TABLE IF EXISTS distributor_documents CASCADE;
DROP TABLE IF EXISTS distributor_applications CASCADE;
DROP TABLE IF EXISTS territory_assignments CASCADE;
DROP TABLE IF EXISTS territory_targets CASCADE;
DROP TABLE IF EXISTS territory_lgas CASCADE;
DROP TABLE IF EXISTS territories CASCADE;
DROP TABLE IF EXISTS distributors CASCADE;
DROP TABLE IF EXISTS regions CASCADE;
DROP TABLE IF EXISTS lgas CASCADE;
DROP TABLE IF EXISTS states CASCADE;
DROP TABLE IF EXISTS countries CASCADE;
DROP TABLE IF EXISTS sales_order_lines CASCADE;
DROP TABLE IF EXISTS sales_orders CASCADE;
DROP TABLE IF EXISTS stock_movements CASCADE;
DROP TABLE IF EXISTS stock_levels CASCADE;
DROP TABLE IF EXISTS product_pricing CASCADE;
DROP TABLE IF EXISTS warehouses CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS users CASCADE;

CREATE TABLE users (
    -- Mirrors app.models.User in full. A cut-down users table here poisons the
    -- shared test database for every later test that goes through the ORM, and
    -- which test that is depends on collection order, so it is spelled out.
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    full_name VARCHAR(255) NOT NULL,
    hashed_password VARCHAR(255) NOT NULL DEFAULT 'x',
    role VARCHAR(50) NOT NULL DEFAULT 'admin',
    is_active BOOLEAN DEFAULT TRUE,
    is_locked BOOLEAN DEFAULT FALSE,
    failed_login_attempts INTEGER DEFAULT 0,
    last_login TIMESTAMPTZ,
    two_factor_enabled BOOLEAN DEFAULT FALSE,
    two_factor_secret VARCHAR(255),
    phone VARCHAR(20),
    department VARCHAR(100),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ
);
CREATE TABLE customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_code VARCHAR(32) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL, email VARCHAR(255), phone VARCHAR(50),
    address TEXT, credit_limit NUMERIC(12,2) DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sku VARCHAR(64) UNIQUE NOT NULL, name VARCHAR(255) NOT NULL,
    description TEXT, unit VARCHAR(32) NOT NULL DEFAULT 'each',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE warehouses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(32) UNIQUE NOT NULL, name VARCHAR(255) NOT NULL,
    location VARCHAR(255), manager_id UUID, is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE stock_levels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    warehouse_id UUID NOT NULL REFERENCES warehouses(id),
    product_id UUID REFERENCES products(id), raw_material_id UUID,
    current_stock NUMERIC(18,6) NOT NULL DEFAULT 0,
    reserved_stock NUMERIC(18,6) DEFAULT 0,
    -- inventory._lock_or_create_level writes these, so the harness must have
    -- them or every movement fails for a reason unrelated to batches.
    min_stock NUMERIC(18,6) DEFAULT 0,
    max_stock NUMERIC(18,6) DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX uq_stock_levels_wh_product
    ON stock_levels (warehouse_id, product_id) WHERE product_id IS NOT NULL;
CREATE UNIQUE INDEX uq_stock_levels_wh_raw_material
    ON stock_levels (warehouse_id, raw_material_id)
 WHERE raw_material_id IS NOT NULL;
CREATE TABLE stock_movements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    warehouse_id UUID NOT NULL REFERENCES warehouses(id),
    product_id UUID REFERENCES products(id), raw_material_id UUID,
    movement_type VARCHAR(32) NOT NULL,
    quantity NUMERIC(18,6) NOT NULL,
    unit_cost NUMERIC(18,6),
    reference VARCHAR(255), notes TEXT,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE sales_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_number VARCHAR(64) UNIQUE NOT NULL,
    customer_id UUID NOT NULL REFERENCES customers(id),
    warehouse_id UUID REFERENCES warehouses(id),
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    payment_status VARCHAR(32) NOT NULL DEFAULT 'unpaid',
    order_date TIMESTAMPTZ DEFAULT NOW(),
    total_amount NUMERIC(18,2) DEFAULT 0,
    notes TEXT, created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE sales_order_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sales_order_id UUID NOT NULL REFERENCES sales_orders(id),
    product_id UUID NOT NULL REFERENCES products(id),
    unit VARCHAR(50), quantity NUMERIC(18,6) NOT NULL,
    unit_price NUMERIC(18,6) NOT NULL, line_total NUMERIC(18,2) NOT NULL
);
"""

MIGRATIONS = [
    ("m6_dist", "x3456789012w_distributor_foundation.py"),
    ("m6_comp", "y4567890123x_distributor_compliance.py"),
    ("m6_apps", "z5678901234y_territory_applications.py"),
    ("m6_portal", "a6789012345z_distributor_portal.py"),
    ("m6_batch", "b7890123456a_product_batches.py"),
]


def _load(name, filename):
    path = (Path(__file__).resolve().parents[1] / "alembic" / "versions"
            / filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeUser:
    def __init__(self, user_id, full_name="QA Manager"):
        self.id = user_id
        self.role = "admin"
        self.full_name = full_name


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
    for name, fn in MIGRATIONS:
        mod = _load(name, fn)
        with engine.begin() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                mod.upgrade()
    engine.dispose()
    yield


@pytest.fixture(autouse=True)
def batch_objects_intact(schema):
    """Re-apply the batch migration before every test in this module.

    test_inventory_ledger.py legitimately drops and recreates `stock_movements`
    for its own fixtures, and that takes `batch_id` and the dispatch-guard
    trigger with it. Whichever module ran last would win, so half these tests
    would pass or fail purely on collection order -- which is the failure mode
    this suite has already been bitten by once.

    Migration b7890123456a is idempotent (CREATE TABLE IF NOT EXISTS, ADD COLUMN
    IF NOT EXISTS, CREATE OR REPLACE FUNCTION, DROP then CREATE TRIGGER), so
    re-running it costs almost nothing and restores whatever another module
    removed. Re-running the migration rather than copying its DDL here matters:
    a second copy of a trigger definition would drift from the real one, and
    these tests would then be asserting against something production does not
    have.
    """
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = create_engine(SYNC_DB, future=True)
    mod = _load("m6_batch_reapply", "b7890123456a_product_batches.py")
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            mod.upgrade()
    engine.dispose()
    yield


@pytest_asyncio.fixture
async def db(batch_objects_intact):
    engine = create_async_engine(TEST_DB, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

async def _admin(db, name="QA Manager"):
    uid = uuid.uuid4()
    await db.execute(
        text("INSERT INTO users (id, email, full_name, role) "
             "VALUES (:i, :e, :n, 'admin')"),
        {"i": str(uid), "e": f"{uid}@t.test", "n": name})
    await db.commit()
    return FakeUser(uid, full_name=name)


async def _warehouse(db, name=None, kind="COMPANY", distributor_id=None):
    wid = uuid.uuid4()
    await db.execute(
        text("""INSERT INTO warehouses (id, code, name, is_active,
                                        warehouse_kind, distributor_id)
                VALUES (:i, :c, :n, TRUE, :k, CAST(:d AS uuid))"""),
        {"i": str(wid), "c": f"W{uuid.uuid4().hex[:8].upper()}",
         "n": name or f"Store {uuid.uuid4().hex[:5]}", "k": kind,
         "d": str(distributor_id) if distributor_id else None})
    await db.commit()
    return wid


async def _product(db, name=None):
    pid = uuid.uuid4()
    await db.execute(
        text("""INSERT INTO products (id, sku, name, unit)
                VALUES (:i, :s, :n, 'carton')"""),
        {"i": str(pid), "s": f"SKU{uuid.uuid4().hex[:8].upper()}",
         "n": name or f"Dressing {uuid.uuid4().hex[:5]}"})
    await db.commit()
    return pid


async def _batch(db, admin, product_id=None, *, number=None, expiry_days=365,
                 receive=0, warehouse_id=None):
    pid = product_id or await _product(db)
    expiry = (date.today() + timedelta(days=expiry_days)
              if expiry_days is not None else None)
    r = await svc.create_batch(
        db, product_id=pid, batch_number=number or f"LOT{uuid.uuid4().hex[:6].upper()}",
        expiry_date=expiry, manufactured_on=date.today() - timedelta(days=5),
        actor=admin)
    await db.commit()
    bid = uuid.UUID(r["id"])
    if receive:
        wid = warehouse_id or await _warehouse(db)
        await inv.apply_stock_movement(
            db, warehouse_id=wid, movement_type="IN", quantity=receive,
            product_id=pid, batch_id=bid, created_by=admin.id,
            reference="Goods received")
        await db.commit()
        return pid, bid, wid
    return pid, bid, warehouse_id


# ---------------------------------------------------------------------------
# The balance is derived, never stored
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_there_is_no_stored_batch_balance(db):
    """A second copy of a quantity drifts, and drift surfaces during a recall."""
    tables = (await db.execute(
        text("""SELECT table_name FROM information_schema.tables
                 WHERE table_schema = 'public'
                   AND table_name ILIKE '%batch%'"""))).scalars().all()
    assert set(tables) == {"product_batches", "batch_status_events"}, (
        "no batch balance table may exist")

    columns = (await db.execute(
        text("""SELECT column_name FROM information_schema.columns
                 WHERE table_name = 'product_batches'"""))).scalars().all()
    assert not any("stock" in c or "on_hand" in c or "balance" in c
                   for c in columns), (
        "a batch must not carry a cached quantity on hand")


@pytest.mark.asyncio
async def test_the_balance_follows_the_movements(db):
    admin = await _admin(db)
    pid, bid, wid = await _batch(db, admin, receive=100)

    assert await inv.batch_balance(db, batch_id=bid) == Decimal("100")

    await inv.apply_stock_movement(
        db, warehouse_id=wid, movement_type="OUT", quantity=30,
        product_id=pid, batch_id=bid, created_by=admin.id)
    await db.commit()
    assert await inv.batch_balance(db, batch_id=bid) == Decimal("70")

    await inv.apply_stock_movement(
        db, warehouse_id=wid, movement_type="RETURN", quantity=5,
        product_id=pid, batch_id=bid, created_by=admin.id)
    await db.commit()
    assert await inv.batch_balance(db, batch_id=bid) == Decimal("75")


@pytest.mark.asyncio
async def test_a_batch_cannot_be_issued_for_more_than_it_holds(db):
    admin = await _admin(db)
    pid, bid, wid = await _batch(db, admin, receive=10)

    # There is plenty of the PRODUCT in the warehouse...
    other_pid, other_bid, _ = await _batch(db, admin, product_id=pid,
                                           receive=500, warehouse_id=wid)
    assert await inv.get_available_stock(
        db, warehouse_id=wid, product_id=pid) >= Decimal("510")

    # ...but not of this batch, and that is what a trace depends on.
    with pytest.raises(HTTPException) as exc:
        await inv.apply_stock_movement(
            db, warehouse_id=wid, movement_type="OUT", quantity=50,
            product_id=pid, batch_id=bid, created_by=admin.id)
    await db.rollback()
    assert "holds 10" in exc.value.detail


@pytest.mark.asyncio
async def test_a_batch_belongs_to_one_product(db):
    admin = await _admin(db)
    _, bid, wid = await _batch(db, admin, receive=50)
    other_product = await _product(db)

    with pytest.raises(HTTPException) as exc:
        await inv.apply_stock_movement(
            db, warehouse_id=wid, movement_type="IN", quantity=5,
            product_id=other_product, batch_id=bid, created_by=admin.id)
    await db.rollback()
    assert "different product" in exc.value.detail


# ---------------------------------------------------------------------------
# Quarantine and recall
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_recalled_batch_cannot_be_despatched(db):
    admin = await _admin(db)
    pid, bid, wid = await _batch(db, admin, receive=200)

    await svc.set_status(db, batch_id=bid, status="RECALLED",
                         reason="Sterility failure reported by a hospital",
                         actor=admin)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await inv.apply_stock_movement(
            db, warehouse_id=wid, movement_type="OUT", quantity=1,
            product_id=pid, batch_id=bid, created_by=admin.id)
    await db.rollback()
    assert exc.value.status_code == 409
    assert "RECALLED" in exc.value.detail


@pytest.mark.asyncio
async def test_the_database_refuses_it_too_not_just_the_service(db):
    """The service is today's single write path. This survives the next one."""
    admin = await _admin(db)
    pid, bid, wid = await _batch(db, admin, receive=200)
    await svc.set_status(db, batch_id=bid, status="RECALLED",
                         reason="Contamination found during testing",
                         actor=admin)
    await db.commit()

    # Straight at the table, bypassing every check in the service layer.
    with pytest.raises(Exception) as exc:
        await db.execute(
            text("""INSERT INTO stock_movements
                        (id, warehouse_id, product_id, movement_type, quantity,
                         batch_id)
                    VALUES (gen_random_uuid(), :w, :p, 'OUT', 5, :b)"""),
            {"w": str(wid), "p": str(pid), "b": str(bid)})
        await db.commit()
    await db.rollback()
    assert "RECALLED" in str(exc.value)


@pytest.mark.asyncio
async def test_recalled_stock_can_still_come_back_in(db):
    """A rule that blocked returns would make a recall impossible to carry out."""
    admin = await _admin(db)
    pid, bid, wid = await _batch(db, admin, receive=100)
    await svc.set_status(db, batch_id=bid, status="RECALLED",
                         reason="Packaging defect found in the field",
                         actor=admin)
    await db.commit()

    await inv.apply_stock_movement(
        db, warehouse_id=wid, movement_type="RETURN", quantity=40,
        product_id=pid, batch_id=bid, created_by=admin.id,
        reference="Recall collection")
    await db.commit()
    assert await inv.batch_balance(db, batch_id=bid) == Decimal("140")


@pytest.mark.asyncio
async def test_a_quarantined_batch_is_held_until_released(db):
    admin = await _admin(db)
    pid, bid, wid = await _batch(db, admin, receive=60)

    await svc.set_status(db, batch_id=bid, status="QUARANTINED",
                         reason="Awaiting sterility test results", actor=admin)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await inv.apply_stock_movement(
            db, warehouse_id=wid, movement_type="OUT", quantity=1,
            product_id=pid, batch_id=bid, created_by=admin.id)
    await db.rollback()
    assert "QUARANTINED" in exc.value.detail

    # Released, with a reason, it moves again.
    await svc.set_status(db, batch_id=bid, status="AVAILABLE",
                         reason="Sterility test passed, certificate on file",
                         actor=admin)
    await db.commit()
    await inv.apply_stock_movement(
        db, warehouse_id=wid, movement_type="OUT", quantity=10,
        product_id=pid, batch_id=bid, created_by=admin.id)
    await db.commit()
    assert await inv.batch_balance(db, batch_id=bid) == Decimal("50")


@pytest.mark.asyncio
async def test_a_recalled_batch_never_returns_to_sale(db):
    admin = await _admin(db)
    _, bid, _ = await _batch(db, admin, receive=10)
    await svc.set_status(db, batch_id=bid, status="RECALLED",
                         reason="Recall raised by the quality committee",
                         actor=admin)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.set_status(db, batch_id=bid, status="AVAILABLE",
                             reason="We think it was fine after all",
                             actor=admin)
    await db.rollback()
    assert "cannot be returned to sale" in exc.value.detail

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("""UPDATE product_batches SET status = 'AVAILABLE'
                     WHERE id = :b"""), {"b": str(bid)})
        await db.commit()
    await db.rollback()
    assert "cannot be returned to sale" in str(exc.value)


@pytest.mark.asyncio
async def test_an_expired_batch_cannot_be_despatched(db):
    admin = await _admin(db)
    pid, bid, wid = await _batch(db, admin, receive=25)

    # Expire it where it stands; create_batch rightly refuses a past date.
    await db.execute(
        text("""UPDATE product_batches
                   SET expiry_date = CURRENT_DATE - 1,
                       manufactured_on = CURRENT_DATE - 400
                 WHERE id = :b"""), {"b": str(bid)})
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await inv.apply_stock_movement(
            db, warehouse_id=wid, movement_type="OUT", quantity=1,
            product_id=pid, batch_id=bid, created_by=admin.id)
    await db.rollback()
    assert "expired" in exc.value.detail


@pytest.mark.asyncio
async def test_taking_a_batch_off_sale_requires_a_reason(db):
    admin = await _admin(db)
    _, bid, _ = await _batch(db, admin, receive=5)

    with pytest.raises(HTTPException):
        await svc.set_status(db, batch_id=bid, status="QUARANTINED",
                             reason="x", actor=admin)
    await db.rollback()

    # And the database refuses it too.
    with pytest.raises(Exception) as exc:
        await db.execute(
            text("""UPDATE product_batches
                       SET status = 'QUARANTINED', status_reason = NULL
                     WHERE id = :b"""), {"b": str(bid)})
        await db.commit()
    await db.rollback()
    assert "ck_batch_block_reason" in str(exc.value)


@pytest.mark.asyncio
async def test_status_history_is_append_only(db):
    admin = await _admin(db)
    _, bid, _ = await _batch(db, admin, receive=5)
    await svc.set_status(db, batch_id=bid, status="QUARANTINED",
                         reason="Held pending investigation", actor=admin)
    await db.commit()

    history = await svc.status_history(db, bid)
    assert [h["to_status"] for h in history] == ["AVAILABLE", "QUARANTINED"]
    assert history[-1]["decided_by"] == "QA Manager", (
        "who decided is part of the record")

    for stmt in ("UPDATE batch_status_events SET reason = 'something else'",
                 "DELETE FROM batch_status_events"):
        with pytest.raises(Exception) as exc:
            await db.execute(text(stmt))
            await db.commit()
        await db.rollback()
        assert "append-only" in str(exc.value)


# ---------------------------------------------------------------------------
# Traceability
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_transfer_carries_the_batch_to_the_destination(db):
    """Moving goods between shelves must not move them out of traceability."""
    admin = await _admin(db)
    pid, bid, source = await _batch(db, admin, receive=100)
    destination = await _warehouse(db, name="Distributor Store")

    await inv.transfer_stock(
        db, from_warehouse_id=source, to_warehouse_id=destination,
        quantity=40, product_id=pid, batch_id=bid, created_by=admin.id,
        reference="Despatch to distributor")
    await db.commit()

    assert await inv.batch_balance(db, batch_id=bid) == Decimal("100"), (
        "a transfer moves stock, it does not create or destroy it")
    assert await inv.batch_balance(
        db, batch_id=bid, warehouse_id=source) == Decimal("60")
    assert await inv.batch_balance(
        db, batch_id=bid, warehouse_id=destination) == Decimal("40")

    locations = await svc.batch_locations(db, batch_id=bid)
    assert {loc["name"] for loc in locations} >= {"Distributor Store"}


@pytest.mark.asyncio
async def test_a_recall_produces_two_lists(db):
    """Stock still held can be stopped; stock despatched has to be chased."""
    admin = await _admin(db)
    pid, bid, company = await _batch(db, admin, receive=500)
    depot = await _warehouse(db, name="Aba Depot")

    await inv.transfer_stock(
        db, from_warehouse_id=company, to_warehouse_id=depot, quantity=200,
        product_id=pid, batch_id=bid, created_by=admin.id)
    await db.commit()

    # And some went out to a hospital on an order.
    cid = uuid.uuid4()
    await db.execute(
        text("""INSERT INTO customers (id, customer_code, name, phone)
                VALUES (:i, :c, 'St Mary Hospital', '+2348030000000')"""),
        {"i": str(cid), "c": f"C{uuid.uuid4().hex[:8].upper()}"})
    oid = uuid.uuid4()
    await db.execute(
        text("""INSERT INTO sales_orders (id, order_number, customer_id,
                                          warehouse_id, status)
                VALUES (:i, :n, :c, :w, 'delivered')"""),
        {"i": str(oid), "n": f"SO-{uuid.uuid4().hex[:6].upper()}",
         "c": str(cid), "w": str(company)})
    await db.execute(
        text("""INSERT INTO sales_order_lines
                    (id, sales_order_id, product_id, unit, quantity,
                     unit_price, line_total, batch_id)
                VALUES (gen_random_uuid(), :o, :p, 'carton', 60, 100, 6000,
                        :b)"""),
        {"o": str(oid), "p": str(pid), "b": str(bid)})
    await inv.apply_stock_movement(
        db, warehouse_id=company, movement_type="OUT", quantity=60,
        product_id=pid, batch_id=bid, created_by=admin.id, reference="SO")
    await db.commit()

    result = await svc.set_status(
        db, batch_id=bid, status="RECALLED",
        reason="Sterility failure confirmed by the laboratory", actor=admin)
    await db.commit()

    trace = result["trace"]
    assert Decimal(trace["quantity_still_held"]) == Decimal("440")
    assert {loc["name"] for loc in trace["still_held"]} == {
        "Aba Depot"} | {loc["name"] for loc in trace["still_held"]
                        if loc["name"] != "Aba Depot"}
    assert Decimal(trace["quantity_despatched"]) == Decimal("60")
    assert trace["recipients"] == 1
    assert trace["despatched_to"][0]["customer"] == "St Mary Hospital"
    assert trace["despatched_to"][0]["phone"] == "+2348030000000"


@pytest.mark.asyncio
async def test_the_report_says_what_cannot_be_traced(db):
    """No reassuring percentage. Absolute quantities, both sides."""
    admin = await _admin(db)
    pid = await _product(db)
    wid = await _warehouse(db)

    # Stock that predates batches: no batch_id, and nothing invents one.
    await inv.apply_stock_movement(
        db, warehouse_id=wid, movement_type="IN", quantity=300,
        product_id=pid, created_by=admin.id, reference="Opening stock")
    await db.commit()

    _, bid, _ = await _batch(db, admin, product_id=pid, receive=700,
                             warehouse_id=wid)

    report = await svc.traceability_report(db, product_id=pid)
    assert Decimal(report["traceable_quantity"]) == Decimal("700")
    assert Decimal(report["untraceable_quantity"]) == Decimal("300")
    assert report["reconciles"] is True
    assert "moved before batch recording began" in report["note"]

    # The one thing it must never do.
    assert "percent" not in str(report).lower()
    assert "%" not in str(report)


@pytest.mark.asyncio
async def test_the_report_flags_a_disagreement_rather_than_hiding_it(db):
    admin = await _admin(db)
    pid = await _product(db)
    wid = await _warehouse(db)
    await inv.apply_stock_movement(
        db, warehouse_id=wid, movement_type="IN", quantity=100,
        product_id=pid, created_by=admin.id)
    await db.commit()

    # Corrupt the balance behind the ledger's back, as a stray UPDATE would.
    await db.execute(
        text("""UPDATE stock_levels SET current_stock = 999
                 WHERE warehouse_id = :w AND product_id = :p"""),
        {"w": str(wid), "p": str(pid)})
    await db.commit()

    report = await svc.traceability_report(db, product_id=pid)
    assert report["reconciles"] is False
    assert report["reconciliation_note"]
    assert "will miss goods" in report["reconciliation_note"]


@pytest.mark.asyncio
async def test_picking_is_offered_soonest_expiry_first(db):
    """FEFO, not FIFO. They differ exactly when it matters."""
    admin = await _admin(db)
    pid = await _product(db)
    wid = await _warehouse(db)

    # Received FIRST, but expires LATER.
    _, old_arrival, _ = await _batch(db, admin, product_id=pid,
                                     number="ARRIVED-FIRST", expiry_days=300,
                                     receive=50, warehouse_id=wid)
    # Received SECOND, expires SOONER — this is the one to pick.
    _, short_dated, _ = await _batch(db, admin, product_id=pid,
                                     number="EXPIRES-FIRST", expiry_days=30,
                                     receive=50, warehouse_id=wid)
    # No expiry recorded: sorts last, because unknown is not "distant".
    _, undated, _ = await _batch(db, admin, product_id=pid, number="NO-DATE",
                                 expiry_days=None, receive=50,
                                 warehouse_id=wid)

    offered = await svc.available_batches(db, product_id=pid, warehouse_id=wid)
    assert [b["batch_number"] for b in offered] == [
        "EXPIRES-FIRST", "ARRIVED-FIRST", "NO-DATE"]
    assert offered[0]["days_to_expiry"] == 30
    assert offered[-1]["days_to_expiry"] is None


@pytest.mark.asyncio
async def test_a_blocked_batch_is_not_offered_for_picking(db):
    admin = await _admin(db)
    pid = await _product(db)
    wid = await _warehouse(db)
    _, good, _ = await _batch(db, admin, product_id=pid, number="GOOD",
                              receive=10, warehouse_id=wid)
    _, held, _ = await _batch(db, admin, product_id=pid, number="HELD",
                              receive=10, warehouse_id=wid)
    await svc.set_status(db, batch_id=held, status="QUARANTINED",
                         reason="Awaiting laboratory results", actor=admin)
    await db.commit()

    offered = await svc.available_batches(db, product_id=pid, warehouse_id=wid)
    assert [b["batch_number"] for b in offered] == ["GOOD"]


@pytest.mark.asyncio
async def test_two_batches_cannot_share_a_number_on_one_product(db):
    admin = await _admin(db)
    pid = await _product(db)
    await svc.create_batch(db, product_id=pid, batch_number="LOT-001",
                           actor=admin)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.create_batch(db, product_id=pid, batch_number="LOT-001",
                               actor=admin)
    await db.rollback()
    assert exc.value.status_code == 409
    assert "cannot be told apart" in exc.value.detail

    # But a different product may legitimately use the same lot number.
    other = await _product(db)
    await svc.create_batch(db, product_id=other, batch_number="LOT-001",
                           actor=admin)
    await db.commit()


@pytest.mark.asyncio
async def test_a_batch_is_not_deletable_and_keeps_its_identity(db):
    admin = await _admin(db)
    pid, bid, _ = await _batch(db, admin, receive=5)

    with pytest.raises(Exception) as exc:
        await db.execute(text("DELETE FROM product_batches WHERE id = :b"),
                         {"b": str(bid)})
        await db.commit()
    await db.rollback()
    assert "not deletable" in str(exc.value)

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("UPDATE product_batches SET batch_number = 'RENAMED' "
                 "WHERE id = :b"), {"b": str(bid)})
        await db.commit()
    await db.rollback()
    assert "identify the physical goods" in str(exc.value)


@pytest.mark.asyncio
async def test_registering_a_batch_creates_no_stock(db):
    """Otherwise inventory could be conjured by filling in a form."""
    admin = await _admin(db)
    pid = await _product(db)
    r = await svc.create_batch(db, product_id=pid, batch_number="EMPTY-001",
                               actor=admin)
    await db.commit()
    assert await inv.batch_balance(
        db, batch_id=uuid.UUID(r["id"])) == Decimal("0")


@pytest.mark.asyncio
async def test_expiring_separates_what_has_expired_from_what_will(db):
    admin = await _admin(db)
    pid = await _product(db)
    wid = await _warehouse(db)
    _, soon, _ = await _batch(db, admin, product_id=pid, number="SOON",
                              expiry_days=20, receive=10, warehouse_id=wid)
    _, gone, _ = await _batch(db, admin, product_id=pid, number="GONE",
                              expiry_days=10, receive=10, warehouse_id=wid)
    await db.execute(
        text("""UPDATE product_batches SET expiry_date = CURRENT_DATE - 2,
                       manufactured_on = CURRENT_DATE - 400 WHERE id = :b"""),
        {"b": str(gone)})
    await db.commit()

    result = await svc.expiring_batches(db, within_days=60)
    assert "GONE" in {b["batch_number"] for b in result["expired"]}
    assert "SOON" in {b["batch_number"] for b in result["expiring_soon"]}
