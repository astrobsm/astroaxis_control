"""The derived attention list, and jobs that cannot run twice.

The claims defended here:

  * the list is derived -- an item disappears the moment the underlying
    problem is fixed, with nothing to mark as read;
  * CRITICAL is reserved for what is actively unsafe, and cannot be snoozed;
  * a snooze always expires; there is no dismiss-forever;
  * an item snoozed while minor reappears if it becomes critical;
  * a job runs once per period, guaranteed by the database and not by a check
    that a concurrent worker could race;
  * a failed job records why and does not block a later period;
  * job runs cannot be deleted, which is what would let a job repeat;
  * no job claims to have delivered anything to anybody.
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
from app.services import inbox as svc
from app.services import inventory as inv
from app.services import jobs as jobsvc

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
        f"m9_{uuid.uuid4().hex[:6]}", path)
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
    def __init__(self, user_id, full_name="Operations Lead"):
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
        # Each test reasons about the whole derived list, so it starts empty.
        for table in ("scheduled_job_runs", "attention_acknowledgements"):
            await session.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER USER"))
            await session.execute(text(f"DELETE FROM {table}"))
            await session.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER USER"))
        await session.execute(text("DELETE FROM distributor_order_links"))
        await session.execute(text("DELETE FROM distributor_documents"))
        await session.commit()
        yield session
    await engine.dispose()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

async def _user(db, name="Operations Lead"):
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


async def _warehouse(db):
    wid = uuid.uuid4()
    await db.execute(
        text("""INSERT INTO warehouses (id, code, name, is_active)
                VALUES (:i, :c, :n, TRUE)"""),
        {"i": str(wid), "c": f"W{uuid.uuid4().hex[:8].upper()}",
         "n": f"Store {uuid.uuid4().hex[:5]}"})
    await db.commit()
    return wid


async def _recalled_batch_with_stock(db, admin):
    """The one thing this module treats as CRITICAL."""
    product = await _product(db)
    warehouse = await _warehouse(db)
    batch = await bsvc.create_batch(
        db, product_id=product, batch_number=f"LOT{uuid.uuid4().hex[:6].upper()}",
        actor=admin)
    await db.commit()
    bid = uuid.UUID(batch["id"])
    await inv.apply_stock_movement(
        db, warehouse_id=warehouse, movement_type="IN", quantity=250,
        product_id=product, batch_id=bid, created_by=admin.id)
    await db.commit()
    await bsvc.set_status(db, batch_id=bid, status="RECALLED",
                          reason="Sterility failure confirmed", actor=admin)
    await db.commit()
    return bid


async def _expiring_document(db, admin, days=10):
    r = await dsvc.create_distributor(
        db, legal_name=f"Doc Holder {uuid.uuid4().hex[:6]}", actor=admin,
        acknowledge_duplicates=True)
    await db.commit()
    did = uuid.UUID(r["id"])
    for status, reason in (("APPLIED", "s"), ("UNDER_REVIEW", "r"),
                           ("APPROVED", "a"), ("ACTIVE", "t")):
        await dsvc.set_status(db, distributor_id=did, new_status=status,
                              reason=f"{reason}eason given", actor=admin)
    await db.commit()
    doc = await dsvc.attach_document(
        db, distributor_id=did, doc_type="NAFDAC_LICENCE",
        filename="licence.pdf", content_type="application/pdf",
        content=b"%PDF licence", expiry_date=date.today() + timedelta(days=days),
        actor=admin)
    await db.commit()
    return did, uuid.UUID(doc["id"])


# ---------------------------------------------------------------------------
# The list is derived
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_item_disappears_when_the_problem_is_fixed(db):
    """No mark-as-read, because there is nothing stored to mark."""
    admin = await _user(db)
    bid = await _recalled_batch_with_stock(db, admin)

    before = await svc.attention_items(db, user=admin)
    keys = {i["key"] for i in before["items"]}
    assert f"recalled_stock:{bid}" in keys

    # The goods are collected. Nothing is dismissed; the stock simply goes.
    product, warehouse = (await db.execute(
        text("""SELECT product_id, warehouse_id FROM stock_movements
                 WHERE batch_id = :b LIMIT 1"""), {"b": str(bid)})).first()
    await inv.apply_stock_movement(
        db, warehouse_id=warehouse, movement_type="DAMAGE", quantity=250,
        product_id=product, batch_id=bid, created_by=admin.id,
        reference="Recall destruction")
    await db.commit()

    after = await svc.attention_items(db, user=admin)
    assert f"recalled_stock:{bid}" not in {i["key"] for i in after["items"]}


@pytest.mark.asyncio
async def test_there_is_no_notifications_table(db):
    """A stored copy of a fact starts rotting the moment it is written."""
    tables = (await db.execute(
        text("""SELECT table_name FROM information_schema.tables
                 WHERE table_schema = 'public'
                   AND (table_name LIKE '%notification%'
                        OR table_name LIKE '%alert%'
                        OR table_name LIKE '%inbox%')"""))).scalars().all()
    assert tables == [], f"unexpected stored-notification tables: {tables}"


@pytest.mark.asyncio
async def test_recalled_stock_in_the_field_is_critical(db):
    admin = await _user(db)
    bid = await _recalled_batch_with_stock(db, admin)

    result = await svc.attention_items(db, user=admin)
    item = next(i for i in result["items"] if i["key"] == f"recalled_stock:{bid}")
    assert item["severity"] == "CRITICAL"
    assert item["snoozeable"] is False
    assert item["category"] == "RECALL"
    # Critical items sort first.
    assert result["items"][0]["severity"] == "CRITICAL"


@pytest.mark.asyncio
async def test_an_expiring_licence_is_reported_before_it_lapses(db):
    admin = await _user(db)
    _, doc_id = await _expiring_document(db, admin, days=10)

    result = await svc.attention_items(db, user=admin)
    item = next(i for i in result["items"]
                if i["key"] == f"document_expiry:{doc_id}")
    assert item["severity"] == "MEDIUM"
    assert "expires soon" in item["title"]

    # Once lapsed it escalates.
    await db.execute(
        text("""UPDATE distributor_documents
                   SET expiry_date = CURRENT_DATE - 1 WHERE id = :d"""),
        {"d": str(doc_id)})
    await db.commit()

    after = await svc.attention_items(db, user=admin)
    item = next(i for i in after["items"]
                if i["key"] == f"document_expiry:{doc_id}")
    assert item["severity"] == "HIGH"
    assert "has expired" in item["title"]


# ---------------------------------------------------------------------------
# Snoozing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_critical_item_cannot_be_snoozed(db):
    admin = await _user(db)
    bid = await _recalled_batch_with_stock(db, admin)

    with pytest.raises(HTTPException) as exc:
        await svc.snooze(db, item_key=f"recalled_stock:{bid}", days=7,
                         reason="Dealing with it later", user=admin)
    await db.rollback()
    assert exc.value.status_code == 403
    assert "cannot be snoozed" in exc.value.detail


@pytest.mark.asyncio
async def test_a_snooze_always_expires(db):
    admin = await _user(db)
    _, doc_id = await _expiring_document(db, admin, days=30)
    key = f"document_expiry:{doc_id}"

    result = await svc.snooze(db, item_key=key, days=7,
                              reason="Renewal already requested", user=admin)
    await db.commit()
    assert result["snoozed_until"]

    hidden = await svc.attention_items(db, user=admin)
    assert key not in {i["key"] for i in hidden["items"]}
    assert hidden["snoozed_hidden"] >= 1

    # There is no way to ask for a permanent one.
    for days in (0, -1, 91, 100000):
        with pytest.raises(HTTPException) as exc:
            await svc.snooze(db, item_key=key, days=days, reason="Forever",
                             user=admin)
        await db.rollback()
        assert "no permanent dismissal" in exc.value.detail

    # And the database refuses a snooze that is already in the past.
    with pytest.raises(Exception) as exc:
        await db.execute(
            text("""INSERT INTO attention_acknowledgements
                        (id, item_key, user_id, snoozed_until)
                    VALUES (gen_random_uuid(), 'x', :u,
                            NOW() - INTERVAL '1 day')"""),
            {"u": str(admin.id)})
        await db.commit()
    await db.rollback()
    assert "ck_ack_future" in str(exc.value)


@pytest.mark.asyncio
async def test_a_snooze_is_per_person(db):
    admin = await _user(db)
    colleague = await _user(db, name="Compliance Officer")
    _, doc_id = await _expiring_document(db, admin, days=30)
    key = f"document_expiry:{doc_id}"

    await svc.snooze(db, item_key=key, days=7, reason="Chasing it", user=admin)
    await db.commit()

    mine = await svc.attention_items(db, user=admin)
    theirs = await svc.attention_items(db, user=colleague)
    assert key not in {i["key"] for i in mine["items"]}
    assert key in {i["key"] for i in theirs["items"]}, (
        "one person's snooze must not hide a problem from everybody else")


@pytest.mark.asyncio
async def test_an_item_that_becomes_critical_reappears(db):
    """A decision taken when it was minor cannot bury what it grew into."""
    admin = await _user(db)
    product = await _product(db)
    warehouse = await _warehouse(db)
    batch = await bsvc.create_batch(
        db, product_id=product,
        batch_number=f"LOT{uuid.uuid4().hex[:6].upper()}",
        expiry_date=date.today() + timedelta(days=20), actor=admin)
    await db.commit()
    bid = uuid.UUID(batch["id"])
    await inv.apply_stock_movement(
        db, warehouse_id=warehouse, movement_type="IN", quantity=80,
        product_id=product, batch_id=bid, created_by=admin.id)
    await db.commit()

    key = f"batch_expiry:{bid}"
    await svc.snooze(db, item_key=key, days=60, reason="Will sell through",
                     user=admin)
    await db.commit()
    assert key not in {i["key"] for i in
                       (await svc.attention_items(db, user=admin))["items"]}

    # It is recalled. The old snooze was taken on a MEDIUM item.
    await bsvc.set_status(db, batch_id=bid, status="RECALLED",
                          reason="Contamination found", actor=admin)
    await db.commit()

    after = await svc.attention_items(db, user=admin)
    critical = [i for i in after["items"] if i["severity"] == "CRITICAL"]
    assert any(i["key"] == f"recalled_stock:{bid}" for i in critical)


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_job_runs_once_per_period(db):
    admin = await _user(db)
    on = date(2026, 9, 14)

    first = await jobsvc.run_job(db, name="recall_watch", on=on, actor=admin)
    await db.commit()
    assert first["ran"] is True
    assert first["status"] == "COMPLETED"

    for _ in range(3):
        again = await jobsvc.run_job(db, name="recall_watch", on=on,
                                     actor=admin)
        await db.commit()
        assert again["ran"] is False
        assert "already run" in again["note"]

    runs = (await db.execute(
        text("""SELECT COUNT(*) FROM scheduled_job_runs
                 WHERE job_name = 'recall_watch'"""))).scalar()
    assert runs == 1


@pytest.mark.asyncio
async def test_the_key_is_claimed_before_the_work_starts(db):
    """A check-then-insert leaves a window two workers both walk through."""
    source = (Path(__file__).resolve().parents[1]
              / "app" / "services" / "jobs.py").read_text(encoding="utf-8")
    claim = source.index("INSERT INTO scheduled_job_runs")
    work = source.index('await registered["fn"]')
    assert claim < work, (
        "the run key must be inserted before the job function is called")

    # And the constraint that makes the race safe actually exists.
    index = (await db.execute(
        text("""SELECT indexdef FROM pg_indexes
                 WHERE indexname = 'uq_job_run'"""))).scalar()
    assert index and "UNIQUE" in index
    assert "job_name" in index and "run_key" in index


@pytest.mark.asyncio
async def test_a_duplicate_claim_is_refused_by_the_database(db):
    admin = await _user(db)
    on = date(2026, 9, 14)
    await jobsvc.run_job(db, name="recall_watch", on=on, actor=admin)
    await db.commit()

    with pytest.raises(Exception):
        await db.execute(
            text("""INSERT INTO scheduled_job_runs
                        (id, job_name, run_key, status)
                    VALUES (gen_random_uuid(), 'recall_watch', :k,
                            'RUNNING')"""),
            {"k": jobsvc.daily_key(on)})
        await db.commit()
    await db.rollback()


@pytest.mark.asyncio
async def test_different_periods_run_separately(db):
    admin = await _user(db)
    monday = date(2026, 9, 14)
    next_week = monday + timedelta(days=7)

    a = await jobsvc.run_job(db, name="attention_digest", on=monday,
                             actor=admin)
    await db.commit()
    b = await jobsvc.run_job(db, name="attention_digest", on=next_week,
                             actor=admin)
    await db.commit()

    assert a["ran"] is True and b["ran"] is True
    assert a["run_key"] != b["run_key"]
    assert a["run_key"].startswith("2026-W")


@pytest.mark.asyncio
async def test_a_finished_run_cannot_be_rewritten_or_deleted(db):
    """Deleting one would let the job repeat a period it already covered."""
    admin = await _user(db)
    await jobsvc.run_job(db, name="recall_watch", on=date(2026, 9, 14),
                         actor=admin)
    await db.commit()

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("UPDATE scheduled_job_runs SET status = 'RUNNING'"))
        await db.commit()
    await db.rollback()
    assert "already finished" in str(exc.value)

    with pytest.raises(Exception) as exc:
        await db.execute(text("DELETE FROM scheduled_job_runs"))
        await db.commit()
    await db.rollback()
    assert "not deletable" in str(exc.value)


@pytest.mark.asyncio
async def test_a_failing_job_records_why_and_does_not_block_later_periods(db):
    admin = await _user(db)

    @jobsvc.job("deliberately_broken", jobsvc.daily_key)
    async def broken(session, on):
        """A job that fails, to prove the failure is recorded."""
        raise ValueError("the thing it depends on was not there")

    try:
        with pytest.raises(HTTPException) as exc:
            await jobsvc.run_job(db, name="deliberately_broken",
                                 on=date(2026, 9, 14), actor=admin)
        assert exc.value.status_code == 500

        row = (await db.execute(
            text("""SELECT status, error FROM scheduled_job_runs
                     WHERE job_name = 'deliberately_broken'"""),
        )).mappings().first()
        assert row["status"] == "FAILED"
        assert "was not there" in row["error"]

        # A later period is unaffected.
        later = await jobsvc.run_job(db, name="recall_watch",
                                     on=date(2026, 9, 15), actor=admin)
        await db.commit()
        assert later["ran"] is True
    finally:
        jobsvc.JOBS.pop("deliberately_broken", None)


@pytest.mark.asyncio
async def test_no_job_claims_to_have_delivered_anything(db):
    admin = await _user(db)
    result = await jobsvc.run_job(db, name="attention_digest",
                                  on=date(2026, 9, 14), actor=admin)
    await db.commit()

    summary = result["summary"]
    assert summary["delivered_to"] is None
    assert "Nothing was sent" in summary["delivery_note"]

    source = (Path(__file__).resolve().parents[1]
              / "app" / "services" / "jobs.py").read_text(encoding="utf-8")
    assert "broadcast_push_notification" not in source
    assert "fire_notification" not in source


@pytest.mark.asyncio
async def test_the_review_sweep_reports_but_does_not_open_reviews(db):
    """Ending somebody's livelihood is not a scheduled task."""
    admin = await _user(db)
    before = (await db.execute(
        text("SELECT COUNT(*) FROM distributor_performance_reviews"))).scalar()

    result = await jobsvc.run_job(db, name="review_trigger_sweep",
                                  on=date(2026, 9, 14), actor=admin)
    await db.commit()

    after = (await db.execute(
        text("SELECT COUNT(*) FROM distributor_performance_reviews"))).scalar()
    assert after == before
    assert "not opened" in result["summary"]["note"]


@pytest.mark.asyncio
async def test_an_unknown_job_is_refused_with_the_known_ones(db):
    with pytest.raises(HTTPException) as exc:
        await jobsvc.run_job(db, name="not_a_job", actor=None)
    await db.rollback()
    assert exc.value.status_code == 404
    assert "recall_watch" in exc.value.detail
