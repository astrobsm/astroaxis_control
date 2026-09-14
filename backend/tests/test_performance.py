"""Distributor performance: run-rate, bands, snapshots and reviews.

The claims defended here:

  * early in a month no projection is offered at all -- four days of sales
    extrapolated across a month is arithmetic, not a forecast;
  * elapsed time is counted in selling days, not calendar days;
  * a past month is measured against the target IN FORCE THEN, not today's;
  * only VERIFIED sales count toward a band;
  * a month with no target breaks the review run rather than counting as a
    failure -- nobody misses a target that was never set;
  * a snapshot is immutable, and a part-month cannot be frozen;
  * a closed review cannot be reopened or its trigger rewritten;
  * the scorecard returns no single number that a blocker could be averaged
    away by.
"""
import calendar
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

from app.services import distributors as dsvc
from app.services import downstream as down
from app.services import geography as geo
from app.services import performance as svc

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")
SYNC_DB = (TEST_DB or "").replace("+asyncpg", "")

BASE_SCHEMA = """
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
]


def _load(filename):
    path = (Path(__file__).resolve().parents[1] / "alembic" / "versions"
            / filename)
    spec = importlib.util.spec_from_file_location(
        f"m8_{uuid.uuid4().hex[:6]}", path)
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
    def __init__(self, user_id, full_name="Regional Director"):
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
    """Re-apply before every test; other modules recreate shared tables."""
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

async def _user(db, name="Regional Director"):
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
         "n": f"Product {uuid.uuid4().hex[:5]}"})
    await db.commit()
    return pid


async def _ground(db):
    state = (await db.execute(
        text("SELECT id FROM states WHERE code = 'LA'"))).scalar()
    lid = uuid.uuid4()
    tag = uuid.uuid4().hex[:8]
    await db.execute(
        text("""INSERT INTO lgas (id, state_id, name, code)
                VALUES (:i, :s, :n, :c)"""),
        {"i": str(lid), "s": str(state), "n": f"Perfland {tag}",
         "c": f"PF{tag[:5]}".upper()})
    await db.commit()
    return state, lid


async def _distributor_with_territory(db, admin, *, monthly_target="1000000"):
    state, lga = await _ground(db)
    territory = await geo.create_territory(
        db, code=f"T{uuid.uuid4().hex[:6].upper()}",
        name=f"Territory {uuid.uuid4().hex[:4]}", state_id=state,
        lga_ids=[lga], monthly_target=Decimal(monthly_target), actor=admin)
    await db.commit()
    tid = uuid.UUID(territory["id"])

    # create_territory opens the target from today, and set_target rightly
    # refuses to back-date over history -- "targets move forwards" is a phase 1
    # guard worth keeping. These tests measure historical months, so the
    # fixture simulates a target that has simply been in force for a long time,
    # which is what the data would look like for a real territory.
    await db.execute(
        text("""UPDATE territory_targets SET effective_from = :from
                 WHERE territory_id = :t AND effective_to IS NULL"""),
        {"from": date.today() - timedelta(days=900), "t": str(tid)})
    await db.commit()

    r = await dsvc.create_distributor(
        db, legal_name=f"Perf Test {uuid.uuid4().hex[:6]}", actor=admin,
        acknowledge_duplicates=True)
    await db.commit()
    did = uuid.UUID(r["id"])
    for status, reason in (("APPLIED", "Submitted"),
                           ("UNDER_REVIEW", "Reviewing"),
                           ("APPROVED", "Approved"), ("ACTIVE", "Trading")):
        await dsvc.set_status(db, distributor_id=did, new_status=status,
                              reason=reason, actor=admin)
    await db.commit()

    # Assigned from well in the past, so historical months are measurable.
    await geo.assign_territory(
        db, territory_id=tid, distributor_id=did,
        assigned_from=date.today() - timedelta(days=900),
        reason="Initial grant", actor=admin)
    await db.commit()
    return did, tid


async def _verified_sale(db, admin, checker, did, product, amount, on):
    """A sale that actually counts: reported then verified with evidence."""
    sale = await down.record_sale(
        db, distributor_id=did, sold_on=on,
        lines=[Line(product, 1, str(amount))], actor=admin)
    await db.commit()
    sid = uuid.UUID(sale["id"])
    await down.attach_evidence(
        db, sale_id=sid, evidence_type="INVOICE", filename="inv.pdf",
        content_type="application/pdf", content=b"%PDF invoice", actor=checker)
    await down.verify_sale(db, sale_id=sid, actor=checker)
    await db.commit()
    return sid


def _months_ago(n):
    today = date.today()
    year, month = today.year, today.month
    for _ in range(n):
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return year, month


def _mid_month(year, month):
    return date(year, month, min(15, calendar.monthrange(year, month)[1]))


# ---------------------------------------------------------------------------
# The run-rate refusal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_projection_is_offered_early_in_a_month(db):
    """Four days of sales extrapolated across a month is not a forecast."""
    admin = await _user(db)
    did, _ = await _distributor_with_territory(db, admin)

    start = date.today().replace(day=1)
    # Two selling days in: far below the confidence floor.
    early = start
    while svc.selling_days(start, early) < 2:
        early += timedelta(days=1)

    result = await svc.run_rate(db, distributor_id=did, on=early)

    assert result["band"] == "TOO_EARLY"
    assert result["projection"] is None
    assert result["projected_achievement_pct"] is None
    assert result["confidence"] == "NONE"
    assert "arithmetic, not" in result["note"]


@pytest.mark.asyncio
async def test_a_projection_appears_once_enough_of_the_month_has_passed(db):
    admin = await _user(db)
    checker = await _user(db, name="Auditor")
    product = await _product(db)
    did, _ = await _distributor_with_territory(db, admin,
                                               monthly_target="1000000")

    start = date.today().replace(day=1)
    end = date(start.year, start.month,
               calendar.monthrange(start.year, start.month)[1])
    total = svc.selling_days(start, end)

    # Three quarters through the month.
    late, elapsed = start, 0
    while elapsed < int(total * 0.75):
        late += timedelta(days=1)
        elapsed = svc.selling_days(start, late)
    late = min(late, date.today())

    await _verified_sale(db, admin, checker, did, product, "600000", start)

    result = await svc.run_rate(db, distributor_id=did, on=late)
    if Decimal(result["elapsed_fraction"]) >= svc.MIN_CONFIDENT_ELAPSED:
        assert result["projection"] is not None
        assert result["confidence"] in ("LOW", "MODERATE", "HIGH")
        assert Decimal(result["projection"]) >= Decimal("600000")


@pytest.mark.asyncio
async def test_elapsed_time_is_counted_in_selling_days(db):
    """A weekend is not a day anybody could have sold on."""
    # A known week: Monday 5th to Sunday 11th January 2026.
    monday, sunday = date(2026, 1, 5), date(2026, 1, 11)
    assert (sunday - monday).days + 1 == 7
    assert svc.selling_days(monday, sunday) == 5

    saturday, sunday_only = date(2026, 1, 10), date(2026, 1, 11)
    assert svc.selling_days(saturday, sunday_only) == 0


# ---------------------------------------------------------------------------
# Targets as they were
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_past_month_is_measured_against_the_target_in_force_then(db):
    """Raising a target today must not retrospectively fail a past month."""
    admin = await _user(db)
    checker = await _user(db, name="Auditor")
    product = await _product(db)
    did, tid = await _distributor_with_territory(db, admin,
                                                 monthly_target="1000000")

    year, month = _months_ago(2)
    sold_on = _mid_month(year, month)
    await _verified_sale(db, admin, checker, did, product, "950000", sold_on)

    before = await svc.period_performance(
        db, distributor_id=did, year=year, month=month)
    assert before["target_amount"] == "1000000.00"
    assert before["band"] == "ON_TARGET"

    # Today the target is tripled. Targets are superseded, never edited.
    await geo.set_target(
        db, territory_id=tid, monthly_target=Decimal("3000000"),
        effective_from=date.today(), reason="Expanded coverage", actor=admin)
    await db.commit()

    after = await svc.period_performance(
        db, distributor_id=did, year=year, month=month)
    assert after["target_amount"] == "1000000.00", (
        "the past month keeps the figure it was actually measured against")
    assert after["band"] == "ON_TARGET"

    current = await svc.period_performance(
        db, distributor_id=did, year=date.today().year,
        month=date.today().month)
    assert current["target_amount"] == "3000000.00"


@pytest.mark.asyncio
async def test_only_verified_sales_count_toward_a_band(db):
    admin = await _user(db)
    checker = await _user(db, name="Auditor")
    product = await _product(db)
    did, _ = await _distributor_with_territory(db, admin,
                                               monthly_target="1000000")

    year, month = _months_ago(1)
    sold_on = _mid_month(year, month)

    # A large claim, unverified.
    await down.record_sale(
        db, distributor_id=did, sold_on=sold_on,
        lines=[Line(product, 1, "5000000")], actor=admin)
    await db.commit()

    result = await svc.period_performance(
        db, distributor_id=did, year=year, month=month)
    assert result["verified_amount"] == "0.00"
    assert result["reported_amount"] == "5000000.00"
    assert result["band"] == "WELL_BEHIND", (
        "a claim nobody checked cannot carry a distributor to target")

    # Verified, it counts.
    await _verified_sale(db, admin, checker, did, product, "950000", sold_on)
    after = await svc.period_performance(
        db, distributor_id=did, year=year, month=month)
    assert after["verified_amount"] == "950000.00"
    assert after["band"] == "ON_TARGET"


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_three_bad_months_in_a_row_raise_a_review(db):
    admin = await _user(db)
    checker = await _user(db, name="Auditor")
    product = await _product(db)
    did, _ = await _distributor_with_territory(db, admin,
                                               monthly_target="1000000")

    # Three consecutive months at 20% of target.
    for n in (1, 2, 3):
        year, month = _months_ago(n)
        await _verified_sale(db, admin, checker, did, product, "200000",
                             _mid_month(year, month))

    due = await svc.review_due(db, distributor_id=did)
    assert due["consecutive_months_below"] >= 3
    assert due["due"] is True

    review = await svc.open_review(db, distributor_id=did, actor=admin)
    await db.commit()
    assert review["status"] == "OPEN"
    assert "consecutive month" in review["trigger_reason"]
    assert "%" in review["trigger_reason"], (
        "the figures that triggered it are on the record")

    # A second review would split the conversation.
    with pytest.raises(HTTPException) as exc:
        await svc.open_review(db, distributor_id=did, actor=admin)
    await db.rollback()
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_one_good_month_breaks_the_run(db):
    admin = await _user(db)
    checker = await _user(db, name="Auditor")
    product = await _product(db)
    did, _ = await _distributor_with_territory(db, admin,
                                               monthly_target="1000000")

    for n, amount in ((1, "200000"), (2, "980000"), (3, "200000")):
        year, month = _months_ago(n)
        await _verified_sale(db, admin, checker, did, product, amount,
                             _mid_month(year, month))

    due = await svc.review_due(db, distributor_id=did)
    assert due["consecutive_months_below"] == 1
    assert due["due"] is False


@pytest.mark.asyncio
async def test_a_month_with_no_target_does_not_count_as_a_failure(db):
    """Nobody can miss a target that was never set."""
    admin = await _user(db)
    r = await dsvc.create_distributor(
        db, legal_name=f"No Target {uuid.uuid4().hex[:6]}", actor=admin,
        acknowledge_duplicates=True)
    await db.commit()
    did = uuid.UUID(r["id"])
    for status, reason in (("APPLIED", "s"), ("UNDER_REVIEW", "r"),
                           ("APPROVED", "a"), ("ACTIVE", "t")):
        await dsvc.set_status(db, distributor_id=did, new_status=status,
                              reason=f"{reason}eason", actor=admin)
    await db.commit()

    due = await svc.review_due(db, distributor_id=did)
    assert due["consecutive_months_below"] == 0
    assert due["due"] is False

    last = await svc.period_performance(
        db, distributor_id=did, year=_months_ago(1)[0], month=_months_ago(1)[1])
    assert last["band"] == "NO_TARGET"
    assert "nothing to measure against" in last["note"]


@pytest.mark.asyncio
async def test_a_review_can_conclude_the_target_was_wrong(db):
    """A process whose only outcomes blame the distributor always will."""
    admin = await _user(db)
    checker = await _user(db, name="Auditor")
    product = await _product(db)
    did, _ = await _distributor_with_territory(db, admin,
                                               monthly_target="1000000")
    for n in (1, 2, 3):
        year, month = _months_ago(n)
        await _verified_sale(db, admin, checker, did, product, "200000",
                             _mid_month(year, month))

    review = await svc.open_review(db, distributor_id=did, actor=admin)
    await db.commit()
    rid = uuid.UUID(review["id"])

    result = await svc.close_review(
        db, review_id=rid, outcome="TARGET_RESET",
        note="The target assumed two hospitals that never opened.", actor=admin)
    await db.commit()
    assert result["outcome"] == "TARGET_RESET"

    # Closed stays closed.
    with pytest.raises(Exception) as exc:
        await db.execute(
            text("""UPDATE distributor_performance_reviews
                       SET status = 'OPEN' WHERE id = :r"""), {"r": str(rid)})
        await db.commit()
    await db.rollback()
    assert "Open a new review" in str(exc.value)


@pytest.mark.asyncio
async def test_a_review_cannot_be_closed_without_saying_what_was_decided(db):
    admin = await _user(db)
    did, _ = await _distributor_with_territory(db, admin)
    review = await svc.open_review(
        db, distributor_id=did, reason="Concerns raised by the field team",
        actor=admin)
    await db.commit()
    rid = uuid.UUID(review["id"])

    with pytest.raises(HTTPException):
        await svc.close_review(db, review_id=rid, outcome="WARNED", note="x",
                               actor=admin)
    await db.rollback()

    # And the database refuses a closure with no outcome recorded.
    with pytest.raises(Exception) as exc:
        await db.execute(
            text("""UPDATE distributor_performance_reviews
                       SET status = 'CLOSED' WHERE id = :r"""), {"r": str(rid)})
        await db.commit()
    await db.rollback()
    assert "ck_review_closed_recorded" in str(exc.value)


@pytest.mark.asyncio
async def test_what_triggered_a_review_cannot_be_rewritten(db):
    admin = await _user(db)
    did, _ = await _distributor_with_territory(db, admin)
    review = await svc.open_review(
        db, distributor_id=did, reason="Complaints from three outlets",
        actor=admin)
    await db.commit()

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("""UPDATE distributor_performance_reviews
                       SET trigger_reason = 'Something else entirely'
                     WHERE id = :r"""), {"r": review["id"]})
        await db.commit()
    await db.rollback()
    assert "cannot be rewritten" in str(exc.value)

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("DELETE FROM distributor_performance_reviews WHERE id = :r"),
            {"r": review["id"]})
        await db.commit()
    await db.rollback()
    assert "not deletable" in str(exc.value)


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_snapshot_records_what_was_known_then(db):
    admin = await _user(db)
    checker = await _user(db, name="Auditor")
    product = await _product(db)
    did, _ = await _distributor_with_territory(db, admin,
                                               monthly_target="1000000")

    year, month = _months_ago(1)
    sold_on = _mid_month(year, month)
    await _verified_sale(db, admin, checker, did, product, "400000", sold_on)

    frozen = await svc.snapshot_period(
        db, distributor_id=did, year=year, month=month,
        note="Month-end close", actor=admin)
    await db.commit()
    assert frozen["verified_amount"] == "400000.00"
    assert frozen["band"] == "WELL_BEHIND"

    # A sale from that month is verified later. The live view moves...
    await _verified_sale(db, admin, checker, did, product, "550000", sold_on)
    live = await svc.period_performance(
        db, distributor_id=did, year=year, month=month)
    assert live["verified_amount"] == "950000.00"
    assert live["band"] == "ON_TARGET"

    # ...and the snapshot does not, which is the entire point.
    stored = await svc.snapshots(db, distributor_id=did)
    assert stored[0]["verified_amount"] == Decimal("400000.00")
    assert stored[0]["band"] == "WELL_BEHIND"


@pytest.mark.asyncio
async def test_a_snapshot_cannot_be_edited_or_deleted(db):
    admin = await _user(db)
    did, _ = await _distributor_with_territory(db, admin)
    year, month = _months_ago(1)
    frozen = await svc.snapshot_period(
        db, distributor_id=did, year=year, month=month, actor=admin)
    await db.commit()

    for stmt in ("UPDATE performance_periods SET verified_amount = 999999",
                 "DELETE FROM performance_periods"):
        with pytest.raises(Exception) as exc:
            await db.execute(text(stmt))
            await db.commit()
        await db.rollback()
        assert "immutable" in str(exc.value)

    with pytest.raises(HTTPException) as exc:
        await svc.snapshot_period(db, distributor_id=did, year=year,
                                  month=month, actor=admin)
    await db.rollback()
    assert exc.value.status_code == 409
    assert frozen["id"]


@pytest.mark.asyncio
async def test_a_part_month_cannot_be_frozen(db):
    admin = await _user(db)
    did, _ = await _distributor_with_territory(db, admin)
    today = date.today()

    with pytest.raises(HTTPException) as exc:
        await svc.snapshot_period(db, distributor_id=did, year=today.year,
                                  month=today.month, actor=admin)
    await db.rollback()
    assert "has not finished" in exc.value.detail


# ---------------------------------------------------------------------------
# Scorecard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_scorecard_returns_no_single_number(db):
    """A weighted average would let a good month outvote an expired licence."""
    admin = await _user(db)
    did, _ = await _distributor_with_territory(db, admin)

    card = await svc.scorecard(db, distributor_id=did)

    assert set(card) >= {"performance", "compliance", "evidence", "review",
                         "blocking_conditions"}
    for forbidden in ("overall", "score", "total", "rating", "grade"):
        assert forbidden not in card, (
            f"{forbidden!r} would be a number a blocker could be averaged into")

    # Compliance gaps surface as named blockers, not as a deduction.
    assert isinstance(card["blocking_conditions"], list)
    assert any("facility" in b.lower() or "agreement" in b.lower()
               for b in card["blocking_conditions"])


@pytest.mark.asyncio
async def test_the_scorecard_reports_how_much_is_actually_evidenced(db):
    admin = await _user(db)
    checker = await _user(db, name="Auditor")
    product = await _product(db)
    did, _ = await _distributor_with_territory(db, admin)

    sold_on = _mid_month(*_months_ago(1))
    await _verified_sale(db, admin, checker, did, product, "250000", sold_on)
    await down.record_sale(
        db, distributor_id=did, sold_on=sold_on,
        lines=[Line(product, 1, "750000")], actor=admin)
    await db.commit()

    card = await svc.scorecard(db, distributor_id=did)
    evidence = card["evidence"]
    assert evidence["verified_amount"] == "250000.00"
    assert evidence["unverified_amount"] == "750000.00"
    assert evidence["share_evidenced_pct"] == "25.00"
    assert "reason to distrust" in evidence["note"]
