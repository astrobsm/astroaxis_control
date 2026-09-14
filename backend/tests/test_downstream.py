"""Downstream sales: provenance, and the ledger this must never touch.

The claims defended here:

  * a downstream sale posts NO journal entry -- the company is not a party to
    it and already recognised revenue when it shipped to the distributor;
  * a reported sale is stored as REPORTED and counts toward nothing;
  * verification requires evidence AND somebody other than the reporter;
  * a verified sale's figures freeze;
  * a disputed sale is kept, never deleted -- the claim must stay visible;
  * every total is returned split by provenance, and no combined figure exists;
  * a sale moves stock in the DISTRIBUTOR's warehouse only;
  * a distributor reporting more than they received is recorded AND flagged,
    not refused and not allowed to drive stock negative;
  * the batch follows the goods to the outlet, so a recall reaches the end.
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
from app.services import downstream as svc
from app.services import inventory as inv

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")
SYNC_DB = (TEST_DB or "").replace("+asyncpg", "")

BASE_SCHEMA = """
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
DROP TABLE IF EXISTS gl_journal_lines CASCADE;
DROP TABLE IF EXISTS gl_journal_entries CASCADE;
DROP TABLE IF EXISTS sales_order_lines CASCADE;
DROP TABLE IF EXISTS sales_orders CASCADE;
DROP TABLE IF EXISTS stock_movements CASCADE;
DROP TABLE IF EXISTS stock_levels CASCADE;
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
-- The REAL general ledger table names (app/services/ledger.py writes these).
-- Named exactly so this test asserts nothing is written to the tables the
-- application would actually post to, rather than to invented stand-ins.
CREATE TABLE gl_journal_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entry_date DATE NOT NULL, description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE gl_journal_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entry_id UUID REFERENCES gl_journal_entries(id),
    debit NUMERIC(18,2) DEFAULT 0, credit NUMERIC(18,2) DEFAULT 0
);
"""

MIGRATIONS = [
    ("m7_dist", "x3456789012w_distributor_foundation.py"),
    ("m7_comp", "y4567890123x_distributor_compliance.py"),
    ("m7_apps", "z5678901234y_territory_applications.py"),
    ("m7_portal", "a6789012345z_distributor_portal.py"),
    ("m7_batch", "b7890123456a_product_batches.py"),
    ("m7_down", "c8901234567b_downstream_sales.py"),
]


def _load(name, filename):
    path = (Path(__file__).resolve().parents[1] / "alembic" / "versions"
            / filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeUser:
    def __init__(self, user_id, full_name="Sales Manager"):
        self.id = user_id
        self.role = "admin"
        self.full_name = full_name


class Line:
    def __init__(self, product_id, quantity, unit_price=None, unit=None,
                 batch_id=None):
        self.product_id = product_id
        self.quantity = quantity
        self.unit_price = unit_price
        self.unit = unit
        self.batch_id = batch_id


def _apply_migrations():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = create_engine(SYNC_DB, future=True)
    for name, fn in MIGRATIONS:
        mod = _load(f"{name}_{uuid.uuid4().hex[:4]}", fn)
        with engine.begin() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                mod.upgrade()
    engine.dispose()


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
    """Re-apply the chain before every test in this module.

    Other modules legitimately drop and recreate shared tables like
    stock_movements for their own fixtures, which takes this phase's columns
    and triggers with it. Every migration here is idempotent, so re-running is
    cheap and removes the collection-order dependence that has bitten this
    suite before.
    """
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

async def _user(db, name="Sales Manager"):
    uid = uuid.uuid4()
    await db.execute(
        text("INSERT INTO users (id, email, full_name, role) "
             "VALUES (:i, :e, :n, 'admin')"),
        {"i": str(uid), "e": f"{uid}@t.test", "n": name})
    await db.commit()
    return FakeUser(uid, full_name=name)


async def _product(db, name=None):
    pid = uuid.uuid4()
    await db.execute(
        text("""INSERT INTO products (id, sku, name, unit)
                VALUES (:i, :s, :n, 'carton')"""),
        {"i": str(pid), "s": f"SKU{uuid.uuid4().hex[:8].upper()}",
         "n": name or f"Gauze {uuid.uuid4().hex[:5]}"})
    await db.commit()
    return pid


async def _distributor(db, admin, *, stocked=None):
    """An ACTIVE distributor, with its own warehouse provisioned."""
    r = await dsvc.create_distributor(
        db, legal_name=f"Downstream Test {uuid.uuid4().hex[:6]}", actor=admin,
        acknowledge_duplicates=True)
    await db.commit()
    did = uuid.UUID(r["id"])
    for status, reason in (("APPLIED", "Submitted"),
                           ("UNDER_REVIEW", "Reviewing"),
                           ("APPROVED", "Approved"), ("ACTIVE", "Trading")):
        await dsvc.set_status(db, distributor_id=did, new_status=status,
                              reason=reason, actor=admin)
    await db.commit()

    warehouse_id = (await db.execute(
        text("SELECT warehouse_id FROM distributors WHERE id = :d"),
        {"d": str(did)})).scalar()

    if stocked:
        for product_id, quantity in stocked.items():
            await inv.apply_stock_movement(
                db, warehouse_id=warehouse_id, movement_type="IN",
                quantity=quantity, product_id=product_id,
                created_by=admin.id, reference="Shipped to distributor")
        await db.commit()
    return did, warehouse_id


async def _evidence(db, sale_id, actor):
    return await svc.attach_evidence(
        db, sale_id=sale_id, evidence_type="INVOICE", filename="invoice.pdf",
        content_type="application/pdf",
        content=b"%PDF-1.4 invoice from the pharmacy", actor=actor)


# ---------------------------------------------------------------------------
# The accounting rule
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_downstream_sale_posts_no_journal_entry(db):
    """The company is not a party and already recognised the revenue."""
    admin = await _user(db)
    product = await _product(db)
    did, _ = await _distributor(db, admin, stocked={product: 500})

    before = (await db.execute(
        text("SELECT COUNT(*) FROM gl_journal_entries"))).scalar()

    await svc.record_sale(
        db, distributor_id=did, sold_on=date.today(),
        lines=[Line(product, 20, unit_price="1500.00")], actor=admin)
    await db.commit()

    after = (await db.execute(
        text("SELECT COUNT(*) FROM gl_journal_entries"))).scalar()
    assert after == before, (
        "posting here would double-count revenue in a live general ledger")
    assert (await db.execute(
        text("SELECT COUNT(*) FROM gl_journal_lines"))).scalar() == 0


@pytest.mark.asyncio
async def test_the_module_does_not_import_the_ledger_at_all(db):
    """Load-bearing, not incidental: no code path can post from here."""
    source = (Path(__file__).resolve().parents[1]
              / "app" / "services" / "downstream.py").read_text(encoding="utf-8")
    assert "from app.services.ledger" not in source
    assert "post_entry" not in source


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_new_sale_is_reported_and_counts_toward_nothing(db):
    admin = await _user(db)
    product = await _product(db)
    did, _ = await _distributor(db, admin, stocked={product: 500})

    result = await svc.record_sale(
        db, distributor_id=did, sold_on=date.today(),
        lines=[Line(product, 10, unit_price="2000.00")], actor=admin)
    await db.commit()

    assert result["provenance"] == "REPORTED"
    assert "Nobody has checked it" in result["provenance_note"]
    assert result["total_amount"] == "20000.00"

    figures = await svc.sell_through(db, distributor_id=did)
    assert figures["reported_amount"] == "20000.00"
    assert figures["verified_amount"] == "0.00"
    assert figures["counts_toward_performance"] == "verified_amount"


@pytest.mark.asyncio
async def test_verification_needs_evidence(db):
    admin = await _user(db)
    checker = await _user(db, name="Regional Auditor")
    product = await _product(db)
    did, _ = await _distributor(db, admin, stocked={product: 100})

    sale = await svc.record_sale(
        db, distributor_id=did, sold_on=date.today(),
        lines=[Line(product, 5, unit_price="1000.00")], actor=admin)
    await db.commit()
    sid = uuid.UUID(sale["id"])

    with pytest.raises(HTTPException) as exc:
        await svc.verify_sale(db, sale_id=sid, actor=checker)
    await db.rollback()
    assert "Attach the invoice" in exc.value.detail

    await _evidence(db, sid, checker)
    await db.commit()
    result = await svc.verify_sale(db, sale_id=sid, note="Matched the invoice",
                                   actor=checker)
    await db.commit()
    assert result["provenance"] == "VERIFIED"

    figures = await svc.sell_through(db, distributor_id=did)
    assert figures["verified_amount"] == "5000.00"
    assert figures["reported_amount"] == "0.00"


@pytest.mark.asyncio
async def test_the_reporter_cannot_verify_their_own_sale(db):
    admin = await _user(db)
    product = await _product(db)
    did, _ = await _distributor(db, admin, stocked={product: 100})

    sale = await svc.record_sale(
        db, distributor_id=did, sold_on=date.today(),
        lines=[Line(product, 5, unit_price="1000.00")], actor=admin)
    await db.commit()
    sid = uuid.UUID(sale["id"])
    await _evidence(db, sid, admin)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.verify_sale(db, sale_id=sid, actor=admin)
    await db.rollback()
    assert exc.value.status_code == 403
    assert "second pair of eyes" in exc.value.detail

    # And the database refuses it too.
    with pytest.raises(Exception) as exc:
        await db.execute(
            text("""UPDATE distributor_sales
                       SET provenance = 'VERIFIED', verified_by = reported_by,
                           verified_at = NOW()
                     WHERE id = :s"""), {"s": str(sid)})
        await db.commit()
    await db.rollback()
    assert "cannot be the person who verifies" in str(exc.value)


@pytest.mark.asyncio
async def test_a_verified_sale_freezes(db):
    admin = await _user(db)
    checker = await _user(db, name="Auditor")
    product = await _product(db)
    did, _ = await _distributor(db, admin, stocked={product: 100})

    sale = await svc.record_sale(
        db, distributor_id=did, sold_on=date.today(),
        lines=[Line(product, 5, unit_price="1000.00")], actor=admin)
    await db.commit()
    sid = uuid.UUID(sale["id"])
    await _evidence(db, sid, checker)
    await svc.verify_sale(db, sale_id=sid, actor=checker)
    await db.commit()

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("UPDATE distributor_sales SET total_amount = 999999 "
                 "WHERE id = :s"), {"s": str(sid)})
        await db.commit()
    await db.rollback()
    assert "cannot be changed afterwards" in str(exc.value)


@pytest.mark.asyncio
async def test_a_disputed_sale_is_kept_not_deleted(db):
    """Deleting it removes the evidence that a claim was ever made."""
    admin = await _user(db)
    checker = await _user(db, name="Auditor")
    product = await _product(db)
    did, _ = await _distributor(db, admin, stocked={product: 100})

    sale = await svc.record_sale(
        db, distributor_id=did, sold_on=date.today(),
        lines=[Line(product, 50, unit_price="1000.00")], actor=admin)
    await db.commit()
    sid = uuid.UUID(sale["id"])

    await svc.dispute_sale(
        db, sale_id=sid, reason="The outlet has no record of this purchase",
        actor=checker)
    await db.commit()

    detail = await svc.sale_detail(db, sid)
    assert detail["sale"]["provenance"] == "DISPUTED"
    assert "found wrong" in detail["provenance_note"]

    figures = await svc.sell_through(db, distributor_id=did)
    assert figures["disputed_amount"] == "50000.00"
    assert figures["verified_amount"] == "0.00"
    assert figures["reported_amount"] == "0.00"

    with pytest.raises(Exception) as exc:
        await db.execute(text("DELETE FROM distributor_sales WHERE id = :s"),
                         {"s": str(sid)})
        await db.commit()
    await db.rollback()
    assert "not deletable" in str(exc.value)


@pytest.mark.asyncio
async def test_no_report_ever_returns_a_combined_total(db):
    """The moment such a figure exists, a screen will show it as fact."""
    admin = await _user(db)
    checker = await _user(db, name="Auditor")
    product = await _product(db)
    did, _ = await _distributor(db, admin, stocked={product: 1000})

    verified = await svc.record_sale(
        db, distributor_id=did, sold_on=date.today(),
        lines=[Line(product, 10, unit_price="1000.00")], actor=admin)
    await db.commit()
    await _evidence(db, uuid.UUID(verified["id"]), checker)
    await svc.verify_sale(db, sale_id=uuid.UUID(verified["id"]), actor=checker)
    await svc.record_sale(
        db, distributor_id=did, sold_on=date.today(),
        lines=[Line(product, 90, unit_price="1000.00")], actor=admin)
    await db.commit()

    figures = await svc.sell_through(db, distributor_id=did)
    assert figures["verified_amount"] == "10000.00"
    assert figures["reported_amount"] == "90000.00"
    # 100000 must appear nowhere: that is the number that would be wrong.
    assert "100000" not in str(figures)
    assert not any("total" in k for k in figures), (
        "a combined total is exactly what this module refuses to produce")


@pytest.mark.asyncio
async def test_marketers_are_ranked_on_verified_figures(db):
    """Ranking on self-reported numbers rewards optimistic paperwork."""
    admin = await _user(db)
    checker = await _user(db, name="Auditor")
    product = await _product(db)
    did, _ = await _distributor(db, admin, stocked={product: 1000})

    honest = await svc.add_marketer(db, distributor_id=did,
                                    full_name="Adaeze Honest", actor=admin)
    loud = await svc.add_marketer(db, distributor_id=did,
                                  full_name="Emeka Optimistic", actor=admin)
    await db.commit()

    # Adaeze: 10 verified. Emeka: 200 reported, unverified.
    sale = await svc.record_sale(
        db, distributor_id=did, sold_on=date.today(),
        marketer_id=uuid.UUID(honest["id"]),
        lines=[Line(product, 10, unit_price="1000.00")], actor=admin)
    await db.commit()
    await _evidence(db, uuid.UUID(sale["id"]), checker)
    await svc.verify_sale(db, sale_id=uuid.UUID(sale["id"]), actor=checker)
    await svc.record_sale(
        db, distributor_id=did, sold_on=date.today(),
        marketer_id=uuid.UUID(loud["id"]),
        lines=[Line(product, 200, unit_price="1000.00")], actor=admin)
    await db.commit()

    ranked = await svc.by_marketer(db, distributor_id=did)
    assert ranked[0]["full_name"] == "Adaeze Honest"
    assert ranked[0]["verified_amount"] == "10000.00"
    assert ranked[1]["verified_amount"] == "0.00"
    assert ranked[1]["reported_amount"] == "200000.00"


# ---------------------------------------------------------------------------
# Stock
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_sale_moves_the_distributors_stock_only(db):
    admin = await _user(db)
    product = await _product(db)
    did, distributor_warehouse = await _distributor(
        db, admin, stocked={product: 500})

    company = uuid.uuid4()
    await db.execute(
        text("""INSERT INTO warehouses (id, code, name, is_active)
                VALUES (:i, :c, 'Company Store', TRUE)"""),
        {"i": str(company), "c": f"CW{uuid.uuid4().hex[:6].upper()}"})
    await inv.apply_stock_movement(
        db, warehouse_id=company, movement_type="IN", quantity=1000,
        product_id=product, created_by=admin.id)
    await db.commit()

    await svc.record_sale(
        db, distributor_id=did, sold_on=date.today(),
        lines=[Line(product, 120, unit_price="500.00")], actor=admin)
    await db.commit()

    assert await inv.get_available_stock(
        db, warehouse_id=distributor_warehouse,
        product_id=product) == Decimal("380")
    assert await inv.get_available_stock(
        db, warehouse_id=company, product_id=product) == Decimal("1000"), (
        "company stock left the building when the goods were shipped")


@pytest.mark.asyncio
async def test_reporting_more_than_was_received_is_recorded_and_flagged(db):
    """Refusing would discard a fact; allowing negative would corrupt a number."""
    admin = await _user(db)
    product = await _product(db)
    did, warehouse = await _distributor(db, admin, stocked={product: 40})

    result = await svc.record_sale(
        db, distributor_id=did, sold_on=date.today(),
        lines=[Line(product, 500, unit_price="100.00")], actor=admin)
    await db.commit()

    # The claim is on the record.
    assert result["sale_reference"]
    assert result["total_amount"] == "50000.00"
    # And the mismatch is the finding.
    assert result["stock_discrepancy"] is True
    assert "Insufficient stock" in result["discrepancy_note"]

    # Stock was not driven negative to make it balance.
    assert await inv.get_available_stock(
        db, warehouse_id=warehouse, product_id=product) == Decimal("40")

    flagged = await svc.list_sales(db, distributor_id=did,
                                   discrepancies_only=True)
    assert len(flagged) == 1
    figures = await svc.sell_through(db, distributor_id=did)
    assert figures["stock_discrepancies"] == 1


@pytest.mark.asyncio
async def test_the_batch_follows_the_goods_to_the_outlet(db):
    """This is what lets a recall name the pharmacy that bought the batch."""
    admin = await _user(db)
    checker = await _user(db, name="Auditor")
    product = await _product(db)
    did, warehouse = await _distributor(db, admin)

    batch = await bsvc.create_batch(
        db, product_id=product, batch_number="LOT-TRACE-1",
        expiry_date=date.today() + timedelta(days=200), actor=admin)
    await db.commit()
    bid = uuid.UUID(batch["id"])

    await inv.apply_stock_movement(
        db, warehouse_id=warehouse, movement_type="IN", quantity=300,
        product_id=product, batch_id=bid, created_by=admin.id)
    await db.commit()

    outlet = await svc.add_outlet(
        db, distributor_id=did, name="St Luke Pharmacy",
        outlet_type="PHARMACY", phone="+2348050000000", actor=admin)
    await db.commit()

    await svc.record_sale(
        db, distributor_id=did, sold_on=date.today(),
        outlet_id=uuid.UUID(outlet["id"]),
        lines=[Line(product, 75, unit_price="800.00", batch_id=bid)],
        actor=admin)
    await db.commit()

    assert await inv.batch_balance(
        db, batch_id=bid, warehouse_id=warehouse) == Decimal("225")

    # The batch is attached to the line, so the outlet is reachable from it.
    reached = (await db.execute(
        text("""SELECT o.name, o.phone, l.quantity
                  FROM distributor_sale_lines l
                  JOIN distributor_sales s ON s.id = l.sale_id
                  JOIN distributor_outlets o ON o.id = s.outlet_id
                 WHERE l.batch_id = :b"""),
        {"b": str(bid)})).mappings().all()
    assert len(reached) == 1
    assert reached[0]["name"] == "St Luke Pharmacy"
    assert reached[0]["phone"] == "+2348050000000"


@pytest.mark.asyncio
async def test_a_recalled_batch_cannot_be_sold_onward(db):
    admin = await _user(db)
    product = await _product(db)
    did, warehouse = await _distributor(db, admin)

    batch = await bsvc.create_batch(
        db, product_id=product, batch_number="LOT-BAD-1", actor=admin)
    await db.commit()
    bid = uuid.UUID(batch["id"])
    await inv.apply_stock_movement(
        db, warehouse_id=warehouse, movement_type="IN", quantity=100,
        product_id=product, batch_id=bid, created_by=admin.id)
    await db.commit()
    await bsvc.set_status(db, batch_id=bid, status="RECALLED",
                          reason="Sterility failure", actor=admin)
    await db.commit()

    result = await svc.record_sale(
        db, distributor_id=did, sold_on=date.today(),
        lines=[Line(product, 10, unit_price="500.00", batch_id=bid)],
        actor=admin)
    await db.commit()

    # The claim is recorded -- it may already have happened -- but no stock
    # moved, and the reason says exactly why.
    assert result["stock_discrepancy"] is True
    assert "RECALLED" in result["discrepancy_note"]
    assert await inv.batch_balance(
        db, batch_id=bid, warehouse_id=warehouse) == Decimal("100")


# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evidence_cannot_be_swapped_after_the_fact(db):
    admin = await _user(db)
    product = await _product(db)
    did, _ = await _distributor(db, admin, stocked={product: 100})
    sale = await svc.record_sale(
        db, distributor_id=did, sold_on=date.today(),
        lines=[Line(product, 5, unit_price="100.00")], actor=admin)
    await db.commit()
    await _evidence(db, uuid.UUID(sale["id"]), admin)
    await db.commit()

    for stmt in ("UPDATE distributor_sale_evidence SET filename = 'other.pdf'",
                 "DELETE FROM distributor_sale_evidence"):
        with pytest.raises(Exception) as exc:
            await db.execute(text(stmt))
            await db.commit()
        await db.rollback()
        assert "append-only" in str(exc.value)


@pytest.mark.asyncio
async def test_a_sale_cannot_be_dated_in_the_future(db):
    admin = await _user(db)
    product = await _product(db)
    did, _ = await _distributor(db, admin, stocked={product: 100})

    with pytest.raises(HTTPException) as exc:
        await svc.record_sale(
            db, distributor_id=did, sold_on=date.today() + timedelta(days=1),
            lines=[Line(product, 1, unit_price="100.00")], actor=admin)
    await db.rollback()
    assert "future" in exc.value.detail


@pytest.mark.asyncio
async def test_a_marketer_is_ended_not_deleted(db):
    admin = await _user(db)
    product = await _product(db)
    did, _ = await _distributor(db, admin, stocked={product: 100})
    marketer = await svc.add_marketer(db, distributor_id=did,
                                      full_name="Chidi Leaving", actor=admin)
    await db.commit()
    mid = uuid.UUID(marketer["id"])

    await svc.record_sale(
        db, distributor_id=did, sold_on=date.today(), marketer_id=mid,
        lines=[Line(product, 5, unit_price="100.00")], actor=admin)
    await db.commit()

    await svc.end_marketer(db, marketer_id=mid,
                           reason="Left the distributor's employment",
                           actor=admin)
    await db.commit()

    active = await svc.list_marketers(db, distributor_id=did)
    assert not any(m["id"] == marketer["id"] for m in active)

    everyone = await svc.list_marketers(db, distributor_id=did,
                                        include_former=True)
    former = [m for m in everyone if m["id"] == marketer["id"]][0]
    assert former["is_active"] is False
    assert former["sales_reported"] == 1, (
        "sales stay attached to whoever reported them")
