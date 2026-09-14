"""The command centre, and the difference between zero and unknown.

The claims defended here:

  * a state with no LGAs loaded reports NULL sales and NO_COVERAGE_DATA, never
    zero -- the two say opposite things about what to do next;
  * the national LGA caveat travels with every geographic figure;
  * there is no overall health score anywhere in the response;
  * territories with no target are counted as not measurable, never as failing;
  * verified and reported sales stay apart, including in the CSV export, which
    has two amount columns rather than one total;
  * a batch export says whether stock may actually be despatched;
  * every export is recorded in the audit trail, because contact details and
    trading history leave the building in it.
"""
import csv
import importlib.util
import io as _io
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

from app.services import command_centre as svc
from app.services import distributors as dsvc
from app.services import downstream as down
from app.services import geography as geo

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")
SYNC_DB = (TEST_DB or "").replace("+asyncpg", "")

BASE_SCHEMA = """
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
]


def _load(filename):
    path = (Path(__file__).resolve().parents[1] / "alembic" / "versions"
            / filename)
    spec = importlib.util.spec_from_file_location(
        f"m10_{uuid.uuid4().hex[:6]}", path)
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
    def __init__(self, user_id, full_name="Managing Director"):
        self.id = user_id
        self.role = "admin"
        self.full_name = full_name


class Line:
    def __init__(self, product_id, quantity, unit_price):
        self.product_id = product_id
        self.quantity = quantity
        self.unit_price = unit_price
        self.unit = None
        self.batch_id = None


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


async def _user(db, name="Managing Director"):
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
         "n": f"Item {uuid.uuid4().hex[:5]}"})
    await db.commit()
    return pid


async def _active_distributor(db, admin):
    r = await dsvc.create_distributor(
        db, legal_name=f"CC Test {uuid.uuid4().hex[:6]}", actor=admin,
        acknowledge_duplicates=True)
    await db.commit()
    did = uuid.UUID(r["id"])
    for status, reason in (("APPLIED", "Submitted"),
                           ("UNDER_REVIEW", "Reviewing"),
                           ("APPROVED", "Approved"), ("ACTIVE", "Trading")):
        await dsvc.set_status(db, distributor_id=did, new_status=status,
                              reason=reason, actor=admin)
    await db.commit()
    return did


# ---------------------------------------------------------------------------
# Zero is not unknown
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_state_with_no_lgas_reports_unknown_not_zero(db):
    """Otherwise the map says "nobody sells in Kano" when nothing could be."""
    result = await svc.coverage_map(db)
    by_code = {s["code"]: s for s in result["states"]}

    # Lagos has LGAs from the seed; most states do not.
    lagos = by_code["LA"]
    assert lagos["lgas_loaded"] > 0
    assert lagos["verified_amount"] is not None
    assert lagos["status"] in ("COVERED", "NO_DISTRIBUTOR")

    dark = [s for s in result["states"] if s["lgas_loaded"] == 0]
    assert dark, "the seed loads only a few states; the rest must be visible"
    for state in dark:
        assert state["status"] == "NO_COVERAGE_DATA"
        assert state["verified_amount"] is None, (
            "a state where nothing could be recorded must not report 0.00")

    assert "NO_COVERAGE_DATA" in result["legend"]
    assert "not as zero" in result["legend"]["NO_COVERAGE_DATA"]


@pytest.mark.asyncio
async def test_the_national_lga_caveat_travels_with_the_figures(db):
    summary = await svc.coverage_summary(db)
    assert summary["lgas_nationally"] == 774
    assert summary["lgas_loaded"] < 774
    assert summary["lgas_not_loaded"] == 774 - summary["lgas_loaded"]
    assert "NOT KNOWN rather than zero" in summary["caveat"]

    # And it is carried into the map and the overview, not left behind.
    assert "caveat" in (await svc.coverage_map(db))["coverage"]
    assert "caveat" in (await svc.command_centre(db))["coverage"]


@pytest.mark.asyncio
async def test_there_is_no_overall_health_score(db):
    """A score would have to average a compliance failure against good sales."""
    result = await svc.command_centre(db)
    flat = str(result).lower()

    for forbidden in ("health_score", "overall_score", "composite",
                      "index_score"):
        assert forbidden not in flat

    assert set(result) >= {"distributors", "territories", "this_month",
                           "attention", "coverage"}
    assert "no overall health score" in result["note"]


@pytest.mark.asyncio
async def test_a_territory_with_no_target_is_not_measurable_not_failing(db):
    admin = await _user(db)
    state = (await db.execute(
        text("SELECT id FROM states WHERE code = 'LA'"))).scalar()
    lga = (await db.execute(
        text("SELECT id FROM lgas WHERE state_id = :s LIMIT 1"),
        {"s": str(state)})).scalar()

    await geo.create_territory(
        db, code=f"T{uuid.uuid4().hex[:6].upper()}", name="Untargeted",
        state_id=state, lga_ids=[lga], actor=admin)
    await db.commit()
    # create_territory opens a target from today, and targets are not deletable
    # (a phase 1 guard worth keeping). So push this one's start into the future:
    # a target scheduled to begin next quarter is a real state, and it is
    # exactly the "no target IN FORCE today" case being tested.
    await db.execute(
        text("""UPDATE territory_targets
                   SET effective_from = CURRENT_DATE + 90
                 WHERE territory_id = (SELECT id FROM territories
                                        WHERE name = 'Untargeted'
                                        ORDER BY created_at DESC LIMIT 1)"""))
    await db.commit()

    result = await svc.command_centre(db)
    assert result["territories"]["not_measurable"] >= 1
    assert "either way" in result["territories"]["not_measurable_note"]

    today = date.today()
    rollup = await svc.territory_rollup(db, year=today.year, month=today.month)
    untargeted = [t for t in rollup["territories"] if t["target_amount"] is None]
    assert untargeted
    assert untargeted[0]["band"] == "NO_TARGET"
    assert untargeted[0]["achievement_pct"] is None, (
        "no target means no achievement figure, not a 0% one")


# ---------------------------------------------------------------------------
# Provenance survives the dashboard and the spreadsheet
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_overview_keeps_verified_and_claimed_apart(db):
    admin = await _user(db)
    checker = await _user(db, name="Auditor")
    product = await _product(db)
    did = await _active_distributor(db, admin)

    verified = await down.record_sale(
        db, distributor_id=did, sold_on=date.today(),
        lines=[Line(product, 1, "100000")], actor=admin)
    await db.commit()
    await down.attach_evidence(
        db, sale_id=uuid.UUID(verified["id"]), evidence_type="INVOICE",
        filename="i.pdf", content_type="application/pdf", content=b"%PDF",
        actor=checker)
    await down.verify_sale(db, sale_id=uuid.UUID(verified["id"]), actor=checker)
    await down.record_sale(
        db, distributor_id=did, sold_on=date.today(),
        lines=[Line(product, 1, "900000")], actor=admin)
    await db.commit()

    month = (await svc.command_centre(db))["this_month"]
    assert month["verified_amount"] == "100000.00"
    assert month["reported_amount"] == "900000.00"
    assert "1000000" not in str(month), "the two must never be summed"
    assert "not added together" in month["note"]


@pytest.mark.asyncio
async def test_the_sales_export_has_two_amount_columns(db):
    """One total column is how a claim becomes a fact inside a spreadsheet."""
    admin = await _user(db)
    checker = await _user(db, name="Auditor")
    product = await _product(db)
    did = await _active_distributor(db, admin)
    legal_name = (await db.execute(
        text("SELECT legal_name FROM distributors WHERE id = :d"),
        {"d": str(did)})).scalar()

    verified = await down.record_sale(
        db, distributor_id=did, sold_on=date.today(),
        lines=[Line(product, 1, "250000")], actor=admin)
    await db.commit()
    await down.attach_evidence(
        db, sale_id=uuid.UUID(verified["id"]), evidence_type="INVOICE",
        filename="i.pdf", content_type="application/pdf", content=b"%PDF",
        actor=checker)
    await down.verify_sale(db, sale_id=uuid.UUID(verified["id"]), actor=checker)
    await down.record_sale(
        db, distributor_id=did, sold_on=date.today(),
        lines=[Line(product, 1, "750000")], actor=admin)
    await db.commit()

    content, filename, rows = await svc.export_csv(
        db, dataset="downstream_sales", actor=admin)
    await db.commit()

    reader = list(csv.DictReader(_io.StringIO(content)))
    assert "verified_amount" in reader[0]
    assert "reported_amount" in reader[0]
    assert "total_amount" not in reader[0], (
        "a single total column would erase the distinction on export")

    # Scoped to this test's own rows: the module shares a database with the
    # tests before it, and asserting on reader[0] would be asserting on
    # whichever test happened to run first.
    mine = [r for r in reader if r["distributor"] == legal_name]
    verified_rows = [r for r in mine if r["provenance"] == "VERIFIED"]
    reported_rows = [r for r in mine if r["provenance"] == "REPORTED"]
    assert [r["verified_amount"] for r in verified_rows] == ["250000.00"]
    assert verified_rows[0]["reported_amount"] == ""
    assert [r["reported_amount"] for r in reported_rows] == ["750000.00"]
    assert reported_rows[0]["verified_amount"] == ""
    assert rows >= 2


@pytest.mark.asyncio
async def test_the_distributor_export_flags_who_has_no_agreement(db):
    admin = await _user(db)
    await _active_distributor(db, admin)

    content, _, _ = await svc.export_csv(
        db, dataset="distributors", actor=admin)
    await db.commit()

    reader = list(csv.DictReader(_io.StringIO(content)))
    assert reader
    assert reader[0]["agreement_in_force"] == "NO", (
        "shouted, because trading unsigned is the finding")


@pytest.mark.asyncio
async def test_the_batch_export_says_whether_stock_may_be_sold(db):
    from app.services import batches as bsvc

    admin = await _user(db)
    product = await _product(db)
    good = await bsvc.create_batch(
        db, product_id=product, batch_number="GOOD-1",
        expiry_date=date.today() + timedelta(days=200), actor=admin)
    bad = await bsvc.create_batch(
        db, product_id=product, batch_number="BAD-1", actor=admin)
    await db.commit()
    await bsvc.set_status(db, batch_id=uuid.UUID(bad["id"]), status="RECALLED",
                          reason="Contamination found", actor=admin)
    await db.commit()

    content, _, _ = await svc.export_csv(db, dataset="batches", actor=admin)
    await db.commit()

    by_number = {r["batch_number"]: r
                 for r in csv.DictReader(_io.StringIO(content))}
    assert by_number["GOOD-1"]["dispatchable"] == "yes"
    assert by_number["BAD-1"]["dispatchable"] == "NO"
    assert by_number["BAD-1"]["status"] == "RECALLED"


# ---------------------------------------------------------------------------
# Exports are recorded
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_every_export_is_recorded_in_the_audit_trail(db):
    """Names, phone numbers and trading history leave on somebody's laptop."""
    admin = await _user(db)
    await _active_distributor(db, admin)

    before = (await db.execute(
        text("""SELECT COUNT(*) FROM distributor_audit_logs
                 WHERE event_type = 'DATA_EXPORTED'"""))).scalar()

    await svc.export_csv(db, dataset="distributors", actor=admin,
                         ip_address="203.0.113.7", user_agent="Firefox")
    await db.commit()

    row = (await db.execute(
        text("""SELECT actor_user_id, new_value, ip_address
                  FROM distributor_audit_logs
                 WHERE event_type = 'DATA_EXPORTED'
                 ORDER BY created_at DESC LIMIT 1"""))).mappings().first()
    after = (await db.execute(
        text("""SELECT COUNT(*) FROM distributor_audit_logs
                 WHERE event_type = 'DATA_EXPORTED'"""))).scalar()

    assert after == before + 1
    assert str(row["actor_user_id"]) == str(admin.id)
    assert row["new_value"]["dataset"] == "distributors"
    assert row["ip_address"] == "203.0.113.7"

    # And that record cannot be quietly removed.
    with pytest.raises(Exception):
        await db.execute(
            text("""DELETE FROM distributor_audit_logs
                     WHERE event_type = 'DATA_EXPORTED'"""))
        await db.commit()
    await db.rollback()


@pytest.mark.asyncio
async def test_an_unknown_export_is_refused_with_the_known_ones(db):
    admin = await _user(db)
    with pytest.raises(HTTPException) as exc:
        await svc.export_csv(db, dataset="everything", actor=admin)
    await db.rollback()
    assert exc.value.status_code == 404
    assert "distributors" in exc.value.detail


@pytest.mark.asyncio
async def test_nothing_in_this_phase_stores_an_aggregate(db):
    """A cached total is wrong for exactly as long as nobody notices."""
    source = (Path(__file__).resolve().parents[1]
              / "app" / "services" / "command_centre.py").read_text(
                  encoding="utf-8")
    for written in ("INSERT INTO", "UPDATE ", "CREATE TABLE"):
        # The one write is the audit row, which goes through geography.audit.
        assert written not in source.upper().replace("AUDIT", ""), (
            f"{written} suggests this module caches something")

    tables = (await db.execute(
        text("""SELECT table_name FROM information_schema.tables
                 WHERE table_schema = 'public'
                   AND (table_name LIKE '%dashboard%'
                        OR table_name LIKE '%summary%'
                        OR table_name LIKE '%rollup%')"""))).scalars().all()
    assert tables == [], f"unexpected cache tables: {tables}"


@pytest.mark.asyncio
async def test_ranking_reuses_the_performance_engine(db):
    """A second implementation of "how did they do" would drift from the first."""
    source = (Path(__file__).resolve().parents[1]
              / "app" / "services" / "command_centre.py").read_text(
                  encoding="utf-8")
    assert "perf.leaderboard" in source

    admin = await _user(db)
    await _active_distributor(db, admin)
    today = date.today()
    result = await svc.ranking(db, year=today.year, month=today.month)
    assert result["ranked_on"] == "verified_amount"
    assert "not bottom of the table" in result["note"]
