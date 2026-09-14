"""Recalls, returns and complaints.

The claims defended here:

  * raising a recall blocks despatch AND produces the list of who has the goods
    -- the flag alone is a batch nobody is chasing;
  * the quantity at risk is frozen at the moment the recall is raised, because
    it is the denominator every later figure is measured against;
  * UNACCOUNTED is computed and named, and a recall carrying unaccounted units
    cannot be closed until somebody says what became of them;
  * recovered comes from the EXISTING returned_stock table, not a second
    counter that could disagree with it;
  * a notification records whether the person actually acknowledged it, and an
    unanswered attempt still leaves them on the list;
  * the outstanding list is built from who holds the batch, so somebody nobody
    has tried yet appears;
  * a complaint's description cannot be edited after the fact;
  * a possible adverse event returns a warning saying the app has notified
    nobody -- every time, not once.
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

from app.services import batches as bsvc
from app.services import distributors as dsvc
from app.services import inventory as inv
from app.services import recalls as svc

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")
SYNC_DB = (TEST_DB or "").replace("+asyncpg", "")

BASE_SCHEMA = """
DROP TABLE IF EXISTS product_complaints CASCADE;
DROP TABLE IF EXISTS recall_notifications CASCADE;
DROP TABLE IF EXISTS product_recalls CASCADE;
DROP TABLE IF EXISTS returned_stock CASCADE;
DROP TABLE IF EXISTS scheduled_job_runs CASCADE;
DROP TABLE IF EXISTS attention_acknowledgements CASCADE;
DROP TABLE IF EXISTS distributor_performance_reviews CASCADE;
DROP TABLE IF EXISTS performance_periods CASCADE;
DROP TABLE IF EXISTS distributor_sale_evidence CASCADE;
DROP TABLE IF EXISTS distributor_sale_lines CASCADE;
DROP TABLE IF EXISTS distributor_sales CASCADE;
DROP TABLE IF EXISTS distributor_outlets CASCADE;
DROP TABLE IF EXISTS distributor_marketers CASCADE;
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
DROP TABLE IF EXISTS warehouses CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS staff CASCADE;
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
CREATE TABLE staff (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id VARCHAR(32) UNIQUE NOT NULL,
    first_name VARCHAR(100) NOT NULL, last_name VARCHAR(100) NOT NULL
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
    min_stock NUMERIC(18,6) DEFAULT 0, max_stock NUMERIC(18,6) DEFAULT 0,
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
    quantity NUMERIC(18,6) NOT NULL, unit_cost NUMERIC(18,6),
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
-- The EXISTING returns table (app/api/returns.py writes this). Phase 11 adds
-- two columns to it rather than building a second returns path.
CREATE TABLE returned_stock (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    warehouse_id UUID NOT NULL REFERENCES warehouses(id),
    product_id UUID NOT NULL REFERENCES products(id),
    sales_order_id UUID REFERENCES sales_orders(id),
    customer_id UUID REFERENCES customers(id),
    quantity NUMERIC(18,6) NOT NULL,
    return_reason TEXT NOT NULL,
    return_condition VARCHAR(50) NOT NULL,
    return_date DATE NOT NULL DEFAULT CURRENT_DATE,
    refund_status VARCHAR(50) NOT NULL DEFAULT 'pending',
    refund_amount NUMERIC(18,2),
    processed_by UUID REFERENCES staff(id),
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
"""

MIGRATIONS = [
    "x3456789012w_distributor_foundation.py",
    "y4567890123x_distributor_compliance.py",
    "z5678901234y_territory_applications.py",
    "a6789012345z_distributor_portal.py",
    "b7890123456a_product_batches.py",
    "c8901234567b_downstream_sales.py",
    "d9012345678c_performance.py",
    "e0123456789d_inbox_jobs.py",
    "f1234567890e_recall_workflow.py",
]


def _load(filename):
    path = (Path(__file__).resolve().parents[1] / "alembic" / "versions"
            / filename)
    spec = importlib.util.spec_from_file_location(
        f"m11_{uuid.uuid4().hex[:6]}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _apply_migrations():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = create_engine(SYNC_DB, future=True)
    for fn in MIGRATIONS:
        mod = _load(fn)
        with engine.begin() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                mod.upgrade()
    engine.dispose()


class FakeUser:
    def __init__(self, user_id, full_name="Quality Manager"):
        self.id = user_id
        self.role = "admin"
        self.full_name = full_name


@pytest.fixture(scope="module")
def schema():
    engine = create_engine(SYNC_DB, future=True)
    with engine.begin() as conn:
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        for stmt in BASE_SCHEMA.split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
    engine.dispose()
    _apply_migrations()
    yield


@pytest.fixture(autouse=True)
def migrations_intact(schema):
    _apply_migrations()
    yield


@pytest_asyncio.fixture
async def db(migrations_intact):
    engine = create_async_engine(TEST_DB, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

async def _user(db, name="Quality Manager"):
    uid = uuid.uuid4()
    await db.execute(
        text("INSERT INTO users (id, email, full_name, role) "
             "VALUES (:i, :e, :n, 'admin')"),
        {"i": str(uid), "e": f"{uid}@t.test", "n": name})
    await db.commit()
    return FakeUser(uid, full_name=name)


async def _product(db):
    pid = uuid.uuid4()
    await db.execute(
        text("""INSERT INTO products (id, sku, name, unit)
                VALUES (:i, :s, :n, 'carton')"""),
        {"i": str(pid), "s": f"SKU{uuid.uuid4().hex[:8].upper()}",
         "n": f"Dressing {uuid.uuid4().hex[:5]}"})
    await db.commit()
    return pid


async def _warehouse(db, name=None):
    wid = uuid.uuid4()
    await db.execute(
        text("""INSERT INTO warehouses (id, code, name, is_active)
                VALUES (:i, :c, :n, TRUE)"""),
        {"i": str(wid), "c": f"W{uuid.uuid4().hex[:8].upper()}",
         "n": name or f"Store {uuid.uuid4().hex[:5]}"})
    await db.commit()
    return wid


async def _batch_with_stock(db, admin, *, on_hand=500, warehouse=None):
    product = await _product(db)
    warehouse = warehouse or await _warehouse(db)
    batch = await bsvc.create_batch(
        db, product_id=product,
        batch_number=f"LOT{uuid.uuid4().hex[:6].upper()}",
        expiry_date=date.today() + timedelta(days=300), actor=admin)
    await db.commit()
    bid = uuid.UUID(batch["id"])
    await inv.apply_stock_movement(
        db, warehouse_id=warehouse, movement_type="IN", quantity=on_hand,
        product_id=product, batch_id=bid, created_by=admin.id)
    await db.commit()
    return product, bid, warehouse


async def _ship_to_customer(db, admin, product, batch_id, warehouse, quantity,
                            name="St Jude Hospital"):
    cid = uuid.uuid4()
    await db.execute(
        text("""INSERT INTO customers (id, customer_code, name, phone)
                VALUES (:i, :c, :n, '+2348051111111')"""),
        {"i": str(cid), "c": f"C{uuid.uuid4().hex[:8].upper()}", "n": name})
    oid = uuid.uuid4()
    await db.execute(
        text("""INSERT INTO sales_orders (id, order_number, customer_id,
                                          warehouse_id, status)
                VALUES (:i, :n, :c, :w, 'delivered')"""),
        {"i": str(oid), "n": f"SO-{uuid.uuid4().hex[:6].upper()}",
         "c": str(cid), "w": str(warehouse)})
    await db.execute(
        text("""INSERT INTO sales_order_lines
                    (id, sales_order_id, product_id, unit, quantity,
                     unit_price, line_total, batch_id)
                VALUES (gen_random_uuid(), :o, :p, 'carton', :q, 100, 100,
                        :b)"""),
        {"o": str(oid), "p": str(product), "q": quantity, "b": str(batch_id)})
    await inv.apply_stock_movement(
        db, warehouse_id=warehouse, movement_type="OUT", quantity=quantity,
        product_id=product, batch_id=batch_id, created_by=admin.id,
        reference="Shipped")
    await db.commit()
    return cid, oid


async def _return_through_the_existing_path(db, admin, *, product, warehouse,
                                            batch_id, recall_id, quantity,
                                            customer_id=None):
    """Write a returned_stock row the way app/api/returns.py does."""
    await db.execute(
        text("""INSERT INTO returned_stock
                    (id, warehouse_id, product_id, customer_id, quantity,
                     return_reason, return_condition, batch_id, recall_id)
                VALUES (gen_random_uuid(), :w, :p, CAST(:c AS uuid), :q,
                        'Recall collection', 'damaged', :b, :r)"""),
        {"w": str(warehouse), "p": str(product),
         "c": str(customer_id) if customer_id else None, "q": quantity,
         "b": str(batch_id), "r": str(recall_id)})
    await inv.apply_stock_movement(
        db, warehouse_id=warehouse, movement_type="RETURN", quantity=quantity,
        product_id=product, batch_id=batch_id, created_by=admin.id,
        reference="Recall collection")
    await db.commit()


# ---------------------------------------------------------------------------
# Raising
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_raising_a_recall_blocks_despatch_and_names_who_has_it(db):
    admin = await _user(db)
    product, bid, warehouse = await _batch_with_stock(db, admin, on_hand=500)
    await _ship_to_customer(db, admin, product, bid, warehouse, 120)

    result = await svc.raise_recall(
        db, batch_id=bid,
        reason="Sterility failure confirmed by the laboratory on 12 March",
        actor=admin)
    await db.commit()

    # The flag alone would be a batch nobody is chasing.
    assert result["recipients_to_contact"] == 1
    assert result["despatched_to"][0]["customer"] == "St Jude Hospital"
    assert Decimal(result["at_risk_quantity"]) == Decimal("500")

    status = (await db.execute(
        text("SELECT status FROM product_batches WHERE id = :b"),
        {"b": str(bid)})).scalar()
    assert status == "RECALLED"

    with pytest.raises(HTTPException):
        await inv.apply_stock_movement(
            db, warehouse_id=warehouse, movement_type="OUT", quantity=1,
            product_id=product, batch_id=bid, created_by=admin.id)
    await db.rollback()


@pytest.mark.asyncio
async def test_the_quantity_at_risk_is_frozen_when_the_recall_is_raised(db):
    """It is the denominator every later recovery figure is measured against."""
    admin = await _user(db)
    product, bid, warehouse = await _batch_with_stock(db, admin, on_hand=300)

    raised = await svc.raise_recall(
        db, batch_id=bid, reason="Packaging integrity failure found in transit",
        actor=admin)
    await db.commit()
    rid = uuid.UUID(raised["id"])
    assert Decimal(raised["at_risk_quantity"]) == Decimal("300")

    # Stock keeps moving afterwards. The denominator must not.
    await inv.apply_stock_movement(
        db, warehouse_id=warehouse, movement_type="DAMAGE", quantity=100,
        product_id=product, batch_id=bid, created_by=admin.id,
        reference="Destroyed")
    await db.commit()

    figures = await svc.reconciliation(db, recall_id=rid)
    assert figures["at_risk_quantity"] == "300"

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("UPDATE product_recalls SET at_risk_quantity = 1 "
                 "WHERE id = :r"), {"r": str(rid)})
        await db.commit()
    await db.rollback()
    assert "denominator" in str(exc.value)


@pytest.mark.asyncio
async def test_two_recalls_cannot_run_for_one_batch(db):
    admin = await _user(db)
    _, bid, _ = await _batch_with_stock(db, admin, on_hand=50)
    await svc.raise_recall(db, batch_id=bid,
                           reason="Contamination found during routine testing",
                           actor=admin)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.raise_recall(db, batch_id=bid,
                               reason="Somebody raised it again by mistake",
                               actor=admin)
    await db.rollback()
    assert exc.value.status_code == 409
    assert "split the reconciliation" in exc.value.detail


@pytest.mark.asyncio
async def test_a_recall_needs_a_real_reason(db):
    admin = await _user(db)
    _, bid, _ = await _batch_with_stock(db, admin, on_hand=10)
    with pytest.raises(HTTPException) as exc:
        await svc.raise_recall(db, batch_id=bid, reason="bad", actor=admin)
    await db.rollback()
    assert "two years" in exc.value.detail


# ---------------------------------------------------------------------------
# The unaccounted figure
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unaccounted_units_are_computed_and_named(db):
    """A recall that looks fully recovered usually had this rounded away."""
    admin = await _user(db)
    product, bid, warehouse = await _batch_with_stock(db, admin, on_hand=1000)
    customer_id, _ = await _ship_to_customer(
        db, admin, product, bid, warehouse, 400)

    raised = await svc.raise_recall(
        db, batch_id=bid, reason="Sterility failure confirmed by laboratory",
        actor=admin)
    await db.commit()
    rid = uuid.UUID(raised["id"])
    assert Decimal(raised["at_risk_quantity"]) == Decimal("1000")

    # 150 come back through the EXISTING returns path.
    await _return_through_the_existing_path(
        db, admin, product=product, warehouse=warehouse, batch_id=bid,
        recall_id=rid, quantity=150, customer_id=customer_id)

    # 600 on the shelf are destroyed.
    await inv.apply_stock_movement(
        db, warehouse_id=warehouse, movement_type="DAMAGE", quantity=600,
        product_id=product, batch_id=bid, created_by=admin.id,
        reference="Recall destruction")
    await db.commit()

    figures = await svc.reconciliation(db, recall_id=rid)
    assert figures["recovered_quantity"] == "150"
    assert figures["destroyed_quantity"] == "600"
    assert figures["still_on_our_shelves"] == "150"   # 600 - 400 + 150 - 600 + 400... derived
    # 1000 at risk - 150 returned - 600 destroyed - what is still on the shelf.
    assert Decimal(figures["unaccounted_quantity"]) == (
        Decimal("1000") - Decimal("150") - Decimal("600")
        - Decimal(figures["still_on_our_shelves"]))
    assert "not accounted for" in figures["note"]


@pytest.mark.asyncio
async def test_a_recall_with_unaccounted_units_cannot_be_quietly_closed(db):
    admin = await _user(db)
    product, bid, warehouse = await _batch_with_stock(db, admin, on_hand=200)
    await _ship_to_customer(db, admin, product, bid, warehouse, 200)

    raised = await svc.raise_recall(
        db, batch_id=bid, reason="Foreign matter reported by two hospitals",
        actor=admin)
    await db.commit()
    rid = uuid.UUID(raised["id"])

    figures = await svc.reconciliation(db, recall_id=rid)
    assert Decimal(figures["unaccounted_quantity"]) > 0

    with pytest.raises(HTTPException) as exc:
        await svc.close_recall(
            db, recall_id=rid,
            closure_note="Everything has been dealt with satisfactorily.",
            actor=admin)
    await db.rollback()
    assert "unaccounted for" in exc.value.detail
    assert "still in use" in exc.value.detail

    closed = await svc.close_recall(
        db, recall_id=rid,
        closure_note="All recipients contacted; collection completed.",
        unaccounted_explanation=(
            "The hospital confirms 200 units were used on patients before "
            "we reached them. They cannot be recovered."),
        actor=admin)
    await db.commit()
    assert closed["status"] == "CLOSED"
    assert "used on patients" in closed["unaccounted_explanation"]


@pytest.mark.asyncio
async def test_a_fully_recovered_recall_closes_without_an_explanation(db):
    admin = await _user(db)
    product, bid, warehouse = await _batch_with_stock(db, admin, on_hand=100)

    raised = await svc.raise_recall(
        db, batch_id=bid, reason="Labelling error found before despatch",
        actor=admin)
    await db.commit()
    rid = uuid.UUID(raised["id"])

    figures = await svc.reconciliation(db, recall_id=rid)
    assert figures["unaccounted_quantity"] == "0"
    assert "Every unit at risk" in figures["note"]

    closed = await svc.close_recall(
        db, recall_id=rid,
        closure_note="Nothing had left the warehouse; all 100 units held.",
        actor=admin)
    await db.commit()
    assert closed["status"] == "CLOSED"


@pytest.mark.asyncio
async def test_recovered_comes_from_the_existing_returns_table(db):
    """Not a second counter that could disagree with what returns wrote."""
    source = (Path(__file__).resolve().parents[1]
              / "app" / "services" / "recalls.py").read_text(encoding="utf-8")
    assert "FROM returned_stock" in source

    columns = (await db.execute(
        text("""SELECT column_name FROM information_schema.columns
                 WHERE table_name = 'product_recalls'"""))).scalars().all()
    assert not any("recovered" in c for c in columns), (
        "a stored recovered total would be a second answer to the same question")


@pytest.mark.asyncio
async def test_a_closed_recall_cannot_be_reopened_or_deleted(db):
    admin = await _user(db)
    _, bid, _ = await _batch_with_stock(db, admin, on_hand=10)
    raised = await svc.raise_recall(
        db, batch_id=bid, reason="Precautionary withdrawal pending testing",
        severity="PRECAUTIONARY", actor=admin)
    await db.commit()
    rid = uuid.UUID(raised["id"])
    await svc.close_recall(db, recall_id=rid,
                           closure_note="All stock held and destroyed on site.",
                           actor=admin)
    await db.commit()

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("UPDATE product_recalls SET status = 'OPEN' WHERE id = :r"),
            {"r": str(rid)})
        await db.commit()
    await db.rollback()
    assert "Raise a new recall" in str(exc.value)

    with pytest.raises(Exception) as exc:
        await db.execute(text("DELETE FROM product_recalls WHERE id = :r"),
                         {"r": str(rid)})
        await db.commit()
    await db.rollback()
    assert "not deletable" in str(exc.value)


# ---------------------------------------------------------------------------
# Contacting people
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_unanswered_attempt_is_not_a_notification(db):
    admin = await _user(db)
    product, bid, warehouse = await _batch_with_stock(db, admin, on_hand=300)
    customer_id, _ = await _ship_to_customer(
        db, admin, product, bid, warehouse, 100)
    raised = await svc.raise_recall(
        db, batch_id=bid, reason="Sterility failure confirmed in testing",
        actor=admin)
    await db.commit()
    rid = uuid.UUID(raised["id"])

    attempt = await svc.record_notification(
        db, recall_id=rid, channel="PHONE", customer_id=customer_id,
        acknowledged=False, response="No answer, left voicemail", actor=admin)
    await db.commit()
    assert attempt["acknowledged"] is False
    assert "still needs reaching" in attempt["note"]

    figures = await svc.reconciliation(db, recall_id=rid)
    assert figures["contacts_attempted"] == 1
    assert figures["contacts_acknowledged"] == 0

    confirmed = await svc.record_notification(
        db, recall_id=rid, channel="PHONE", customer_id=customer_id,
        acknowledged=True, response="Spoke to the matron; 40 units still held",
        quantity_reported_held=40, actor=admin)
    await db.commit()
    assert confirmed["acknowledged"] is True

    figures = await svc.reconciliation(db, recall_id=rid)
    assert figures["contacts_attempted"] == 2
    assert figures["contacts_acknowledged"] == 1


@pytest.mark.asyncio
async def test_the_outstanding_list_includes_people_nobody_has_tried(db):
    """A list of unacknowledged notifications would omit them entirely."""
    admin = await _user(db)
    product, bid, warehouse = await _batch_with_stock(db, admin, on_hand=400)
    await _ship_to_customer(db, admin, product, bid, warehouse, 100,
                            name="Never Contacted Clinic")
    raised = await svc.raise_recall(
        db, batch_id=bid, reason="Contamination confirmed by the laboratory",
        actor=admin)
    await db.commit()
    rid = uuid.UUID(raised["id"])

    outstanding = await svc.outstanding_contacts(db, recall_id=rid)
    names = {c["name"] for c in outstanding["still_to_contact"]}
    assert "Never Contacted Clinic" in names
    assert "nobody has tried yet" in outstanding["note"]


@pytest.mark.asyncio
async def test_a_notification_cannot_be_rewritten_or_deleted(db):
    admin = await _user(db)
    _, bid, _ = await _batch_with_stock(db, admin, on_hand=50)
    raised = await svc.raise_recall(
        db, batch_id=bid, reason="Precautionary hold pending investigation",
        actor=admin)
    await db.commit()
    await svc.record_notification(
        db, recall_id=uuid.UUID(raised["id"]), channel="WHATSAPP",
        contact_name="Depot manager", acknowledged=True, actor=admin)
    await db.commit()

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("UPDATE recall_notifications SET channel = 'LETTER'"))
        await db.commit()
    await db.rollback()
    assert "cannot be rewritten" in str(exc.value)

    with pytest.raises(Exception) as exc:
        await db.execute(text("DELETE FROM recall_notifications"))
        await db.commit()
    await db.rollback()
    assert "not deletable" in str(exc.value)


# ---------------------------------------------------------------------------
# Complaints
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_possible_adverse_event_warns_that_nobody_was_notified(db):
    admin = await _user(db)
    product = await _product(db)

    result = await svc.record_complaint(
        db, description="Patient developed a severe reaction after the dressing "
                        "was applied; hospital reports possible contamination.",
        product_id=product, severity="CRITICAL",
        potential_adverse_event=True, actor=admin)
    await db.commit()

    assert result["potential_adverse_event"] is True
    assert result["warning"]
    assert "HAS NOTIFIED NOBODY AND CANNOT" in result["warning"]

    # And it keeps saying so while no regulator record exists.
    listing = await svc.list_complaints(db, adverse_only=True)
    assert listing["adverse_events_with_no_regulator_record"] >= 1
    assert "NOTIFIED NOBODY" in listing["warning"]


@pytest.mark.asyncio
async def test_recording_what_a_person_did_clears_the_warning(db):
    admin = await _user(db)
    result = await svc.record_complaint(
        db, description="Hospital reports packaging was open on arrival, "
                        "possible sterility compromise.",
        potential_adverse_event=True, actor=admin)
    await db.commit()
    cid = uuid.UUID(result["id"])

    await svc.update_complaint(
        db, complaint_id=cid, regulator_notified_on=date.today(),
        regulator_reference="NAFDAC/AE/2026/0142",
        investigation="Reported by the QA manager on the day of receipt.",
        actor=admin)
    await db.commit()

    listing = await svc.list_complaints(db, adverse_only=True)
    mine = [c for c in listing["complaints"] if c["id"] == result["id"]][0]
    assert mine["regulator_reference"] == "NAFDAC/AE/2026/0142"
    assert mine["regulator_notified_on"] is not None


@pytest.mark.asyncio
async def test_a_complaint_description_cannot_be_edited(db):
    """What the complainant said is the thing being investigated."""
    admin = await _user(db)
    result = await svc.record_complaint(
        db, description="The carton contained 11 units instead of 12.",
        actor=admin)
    await db.commit()

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("""UPDATE product_complaints
                       SET description = 'Nothing was wrong, actually.'
                     WHERE id = :c"""), {"c": result["id"]})
        await db.commit()
    await db.rollback()
    assert "cannot be edited" in str(exc.value)

    with pytest.raises(Exception) as exc:
        await db.execute(text("DELETE FROM product_complaints WHERE id = :c"),
                         {"c": result["id"]})
        await db.commit()
    await db.rollback()
    assert "not deletable" in str(exc.value)


@pytest.mark.asyncio
async def test_dropping_the_adverse_event_flag_needs_reasoning_recorded(db):
    admin = await _user(db)
    result = await svc.record_complaint(
        db, description="Reported skin irritation after use of the product.",
        potential_adverse_event=True, actor=admin)
    await db.commit()

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("""UPDATE product_complaints
                       SET potential_adverse_event = FALSE WHERE id = :c"""),
            {"c": result["id"]})
        await db.commit()
    await db.rollback()
    assert "reasoning to be recorded" in str(exc.value)

    await svc.update_complaint(
        db, complaint_id=uuid.UUID(result["id"]),
        investigation="Reviewed with the clinician: irritation predates use of "
                      "this product and is unrelated.",
        potential_adverse_event=False, actor=admin)
    await db.commit()


@pytest.mark.asyncio
async def test_a_complaint_cannot_be_closed_without_an_outcome(db):
    admin = await _user(db)
    result = await svc.record_complaint(
        db, description="Delivery arrived with two damaged cartons.",
        actor=admin)
    await db.commit()
    cid = uuid.UUID(result["id"])

    with pytest.raises(HTTPException) as exc:
        await svc.update_complaint(db, complaint_id=cid, status="CLOSED",
                                    actor=admin)
    await db.rollback()
    assert "entitled to know it was" in exc.value.detail

    await svc.update_complaint(
        db, complaint_id=cid, status="CLOSED",
        outcome="Replaced the two cartons; damage occurred in transit.",
        actor=admin)
    await db.commit()


@pytest.mark.asyncio
async def test_returns_use_the_existing_table_not_a_new_one(db):
    """Two returns paths would mean two answers to "how much came back"."""
    tables = (await db.execute(
        text("""SELECT table_name FROM information_schema.tables
                 WHERE table_schema = 'public'
                   AND table_name LIKE '%return%'"""))).scalars().all()
    assert "returned_stock" in tables
    assert not any(t.startswith("recall_return") or t == "distributor_returns"
                   for t in tables), f"a second returns table exists: {tables}"

    columns = (await db.execute(
        text("""SELECT column_name FROM information_schema.columns
                 WHERE table_name = 'returned_stock'"""))).scalars().all()
    assert "batch_id" in columns and "recall_id" in columns
