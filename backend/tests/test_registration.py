"""Self-registration, and linking an applicant to who they already are.

The claims defended here:

  * the public form cannot be used to browse the customer list -- it confirms a
    FULL phone number and masks what it returns;
  * a submission creates no distributor and grants nothing; it lands in a
    queue;
  * what the applicant CLAIMED and what a reviewer DECIDED are separate
    columns, and neither can be edited afterwards;
  * approving creates the distributor through the ordinary path;
  * linking an existing customer ATTRIBUTES their orders rather than copying
    them -- there is one row per order before and after, and the money does not
    move;
  * sales_channel is not rewritten: those were direct sales when they happened;
  * performance is unaffected, because a new distributor does not acquire a
    sell-through history by being linked.
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

from app.services import registration as svc

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")
SYNC_DB = (TEST_DB or "").replace("+asyncpg", "")

BASE_SCHEMA = """
DROP TABLE IF EXISTS distributor_registrations CASCADE;
DROP TABLE IF EXISTS distributor_registration_links CASCADE;
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
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL, full_name VARCHAR(255) NOT NULL,
    hashed_password VARCHAR(255) NOT NULL DEFAULT 'x',
    role VARCHAR(50) NOT NULL DEFAULT 'admin',
    is_active BOOLEAN DEFAULT TRUE, is_locked BOOLEAN DEFAULT FALSE,
    failed_login_attempts INTEGER DEFAULT 0, last_login TIMESTAMPTZ,
    two_factor_enabled BOOLEAN DEFAULT FALSE, two_factor_secret VARCHAR(255),
    phone VARCHAR(20), department VARCHAR(100),
    created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ
);
CREATE TABLE staff (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id VARCHAR(32) UNIQUE NOT NULL,
    first_name VARCHAR(100), last_name VARCHAR(100)
);
CREATE TABLE customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_code VARCHAR(32) UNIQUE NOT NULL, name VARCHAR(255) NOT NULL,
    email VARCHAR(255), phone VARCHAR(50), address TEXT,
    credit_limit NUMERIC(12,2) DEFAULT 0, is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sku VARCHAR(64) UNIQUE NOT NULL, name VARCHAR(255) NOT NULL,
    unit VARCHAR(32) DEFAULT 'each'
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
CREATE TABLE stock_movements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    warehouse_id UUID NOT NULL REFERENCES warehouses(id),
    product_id UUID REFERENCES products(id), raw_material_id UUID,
    movement_type VARCHAR(32) NOT NULL, quantity NUMERIC(18,6) NOT NULL,
    unit_cost NUMERIC(18,6), reference VARCHAR(255), notes TEXT,
    created_by UUID REFERENCES users(id), created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE sales_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_number VARCHAR(64) UNIQUE NOT NULL,
    customer_id UUID NOT NULL REFERENCES customers(id),
    warehouse_id UUID REFERENCES warehouses(id),
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    payment_status VARCHAR(32) NOT NULL DEFAULT 'unpaid',
    order_date TIMESTAMPTZ DEFAULT NOW(),
    total_amount NUMERIC(18,2) DEFAULT 0, notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE sales_order_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sales_order_id UUID NOT NULL REFERENCES sales_orders(id),
    product_id UUID NOT NULL REFERENCES products(id),
    unit VARCHAR(50), quantity NUMERIC(18,6) NOT NULL,
    unit_price NUMERIC(18,6) NOT NULL, line_total NUMERIC(18,2) NOT NULL
);
CREATE TABLE returned_stock (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    warehouse_id UUID NOT NULL REFERENCES warehouses(id),
    product_id UUID NOT NULL REFERENCES products(id),
    sales_order_id UUID REFERENCES sales_orders(id),
    customer_id UUID REFERENCES customers(id),
    quantity NUMERIC(18,6) NOT NULL, return_reason TEXT NOT NULL,
    return_condition VARCHAR(50) NOT NULL,
    return_date DATE NOT NULL DEFAULT CURRENT_DATE,
    refund_status VARCHAR(50) NOT NULL DEFAULT 'pending',
    refund_amount NUMERIC(18,2), processed_by UUID REFERENCES staff(id),
    notes TEXT, created_at TIMESTAMP DEFAULT NOW()
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
    "h3456789012g_registration_links.py",
    "i4567890123h_registration_link_recoverable.py",
]


def _load(filename):
    path = (Path(__file__).resolve().parents[1] / "alembic" / "versions"
            / filename)
    spec = importlib.util.spec_from_file_location(
        f"reg_{uuid.uuid4().hex[:6]}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _apply():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = create_engine(SYNC_DB, future=True)
    for filename in MIGRATIONS:
        mod = _load(filename)
        with engine.begin() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                mod.upgrade()
    engine.dispose()


class FakeUser:
    def __init__(self, user_id, full_name="Sales Manager"):
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
    _apply()
    yield


@pytest.fixture(autouse=True)
def migrations_intact(schema):
    _apply()
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


async def _link(db, admin, **kw):
    result = await svc.issue_link(
        db, label=kw.pop("label", "Trade fair, Aba"),
        base_url="https://erp.example.test", actor=admin, **kw)
    await db.commit()
    return result, result["url"].rsplit("/", 1)[-1]


async def _existing_customer(db, *, name="DIVIDEND PHARMACY",
                             phone="+2348031234567", orders=3, value="500000"):
    """A customer with a trading history, as production has."""
    cid = uuid.uuid4()
    await db.execute(
        text("""INSERT INTO customers (id, customer_code, name, phone)
                VALUES (:i, :c, :n, :p)"""),
        {"i": str(cid), "c": f"C{uuid.uuid4().hex[:8].upper()}", "n": name,
         "p": phone})
    wid = uuid.uuid4()
    await db.execute(
        text("""INSERT INTO warehouses (id, code, name) VALUES (:i, :c, 'W')"""),
        {"i": str(wid), "c": f"W{uuid.uuid4().hex[:8].upper()}"})
    for n in range(orders):
        await db.execute(
            text("""INSERT INTO sales_orders
                        (id, order_number, customer_id, warehouse_id, status,
                         total_amount, order_date)
                    VALUES (gen_random_uuid(), :num, :c, :w, 'delivered',
                            :v, NOW() - (:n || ' days')::interval)"""),
            {"num": f"SO-{uuid.uuid4().hex[:8].upper()}", "c": str(cid),
             "w": str(wid), "v": value, "n": str((n + 1) * 30)})
    await db.commit()
    return cid


def _payload(**kw):
    base = {"legal_name": "New Applicant Ltd", "phone": "+2348090000001",
            "entity_type": "COMPANY"}
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# The public form cannot browse the customer list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_check_confirms_a_full_number_and_masks_the_answer(db):
    admin = await _user(db)
    _, token = await _link(db, admin)
    await _existing_customer(db, name="DIVIDEND PHARMACY",
                             phone="+2348031234567")

    result = await svc.confirm_existing_customer(
        db, token=token, phone="08031234567")
    await db.commit()

    assert result["found"] is True
    # Enough to recognise yourself by, not enough to harvest.
    assert result["masked_name"] != "DIVIDEND PHARMACY"
    assert result["masked_name"].startswith("DIV")
    assert "•" in result["masked_name"]
    assert "PHARMACY" not in result["masked_name"]


@pytest.mark.asyncio
async def test_a_partial_number_is_refused(db):
    """Otherwise it is a prefix search, which is a browsable list."""
    admin = await _user(db)
    _, token = await _link(db, admin)
    await _existing_customer(db, phone="+2348031234567")

    for partial in ("0803", "080312", "123"):
        with pytest.raises(HTTPException) as exc:
            await svc.confirm_existing_customer(db, token=token, phone=partial)
        await db.rollback()
        assert "full phone number" in exc.value.detail


@pytest.mark.asyncio
async def test_no_match_and_several_matches_answer_the_same_way(db):
    """Distinguishing them would leak how many accounts share a number."""
    admin = await _user(db)
    _, token = await _link(db, admin)

    nothing = await svc.confirm_existing_customer(
        db, token=token, phone="+2349999999999")
    await db.commit()
    assert nothing["found"] is False

    await _existing_customer(db, name="First Shop", phone="+2348055555555")
    await _existing_customer(db, name="Second Shop", phone="+2348055555555")
    ambiguous = await svc.confirm_existing_customer(
        db, token=token, phone="+2348055555555")
    await db.commit()

    assert ambiguous["found"] is False
    assert ambiguous["note"] == nothing["note"], (
        "the two answers must be indistinguishable")


@pytest.mark.asyncio
async def test_lookups_are_capped_so_numbers_cannot_be_walked(db):
    admin = await _user(db)
    _, token = await _link(db, admin)

    for n in range(svc.LOOKUP_CAP_PER_HOUR):
        await svc.confirm_existing_customer(
            db, token=token, phone=f"+23480{n:08d}")
        await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.confirm_existing_customer(
            db, token=token, phone="+2348012345678")
    await db.rollback()
    assert exc.value.status_code == 429


# ---------------------------------------------------------------------------
# Submitting
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_submission_creates_no_distributor(db):
    admin = await _user(db)
    _, token = await _link(db, admin)
    before = (await db.execute(
        text("SELECT COUNT(*) FROM distributors"))).scalar()

    result = await svc.submit(db, token=token, payload=_payload(),
                               ip="203.0.113.5")
    await db.commit()

    assert result["status"] == "PENDING"
    assert "does not create an account" in result["note"]
    after = (await db.execute(
        text("SELECT COUNT(*) FROM distributors"))).scalar()
    assert after == before, "a public form must not write to the register"

    queued = (await db.execute(
        text("SELECT COUNT(*) FROM distributor_registrations"))).scalar()
    assert queued == 1


@pytest.mark.asyncio
async def test_what_the_applicant_submitted_cannot_be_edited(db):
    admin = await _user(db)
    _, token = await _link(db, admin)
    await svc.submit(db, token=token, payload=_payload(
        legal_name="Exactly As Typed Ltd"), ip="203.0.113.6")
    await db.commit()

    for column, value in (("legal_name", "'Something Else'"),
                          ("phone", "'+2340000000000'"),
                          ("claims_existing_customer", "TRUE")):
        with pytest.raises(Exception) as exc:
            await db.execute(
                text(f"UPDATE distributor_registrations SET {column} = {value}"))
            await db.commit()
        await db.rollback()
        assert "cannot be edited" in str(exc.value)

    with pytest.raises(Exception) as exc:
        await db.execute(text("DELETE FROM distributor_registrations"))
        await db.commit()
    await db.rollback()
    assert "not deletable" in str(exc.value)


@pytest.mark.asyncio
async def test_submissions_are_rate_limited_per_address(db):
    admin = await _user(db)
    _, token = await _link(db, admin)

    for n in range(svc.SUBMIT_CAP_PER_HOUR):
        await svc.submit(db, token=token,
                          payload=_payload(legal_name=f"Applicant {n}"),
                          ip="203.0.113.99")
        await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.submit(db, token=token, payload=_payload(),
                          ip="203.0.113.99")
    await db.rollback()
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_a_revoked_or_expired_link_stops_accepting(db):
    admin = await _user(db)
    issued, token = await _link(db, admin)

    await svc.revoke_link(db, link_id=uuid.UUID(issued["id"]),
                           reason="Campaign finished", actor=admin)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.submit(db, token=token, payload=_payload())
    await db.rollback()
    assert "withdrawn" in exc.value.detail

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("""UPDATE distributor_registration_links
                       SET revoked_at = NULL WHERE id = :l"""),
            {"l": issued["id"]})
        await db.commit()
    await db.rollback()
    assert "cannot be reinstated" in str(exc.value)


@pytest.mark.asyncio
async def test_a_link_can_be_capped_at_a_number_of_applications(db):
    admin = await _user(db)
    _, token = await _link(db, admin, max_submissions=2)

    for n in range(2):
        await svc.submit(db, token=token,
                          payload=_payload(legal_name=f"Capped {n}"),
                          ip=f"198.51.100.{n}")
        await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.submit(db, token=token, payload=_payload(),
                          ip="198.51.100.9")
    await db.rollback()
    assert "number of" in exc.value.detail


# ---------------------------------------------------------------------------
# Review, and the history question
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_reviewer_sees_candidates_and_what_they_would_bring(db):
    admin = await _user(db)
    _, token = await _link(db, admin)
    await _existing_customer(db, name="MALAKI PHARMACY",
                             phone="+2348037777777", orders=4, value="250000")

    await svc.submit(db, token=token, payload=_payload(
        legal_name="MALAKI PHARMACY", phone="+2348037777777"))
    await db.commit()
    # ORDER BY, because this module shares a database with the tests before it
    # and an unordered LIMIT 1 picks whichever row happens to come back first.
    reg_id = (await db.execute(
        text("""SELECT id FROM distributor_registrations
                 ORDER BY submitted_at DESC LIMIT 1"""))).scalar()

    packet = await svc.review_packet(db, registration_id=reg_id)
    customers = [c for c in packet["candidates"] if c["kind"] == "customer"]
    assert customers, "the existing customer should be matched for staff"
    assert customers[0]["history"]["orders"] == 4
    assert Decimal(customers[0]["history"]["value"]) == Decimal("1000000")
    assert "decision for you, not for the applicant" in packet["note"]


@pytest.mark.asyncio
async def test_linking_attributes_history_rather_than_copying_it(db):
    """A distributor IS a customer. The orders were always theirs."""
    admin = await _user(db)
    _, token = await _link(db, admin)
    customer_id = await _existing_customer(
        db, name="DIVIDEND PHARMACY", phone="+2348031111111",
        orders=5, value="200000")

    orders_before = (await db.execute(
        text("SELECT COUNT(*) FROM sales_orders"))).scalar()
    value_before = (await db.execute(
        text("SELECT COALESCE(SUM(total_amount),0) FROM sales_orders"))).scalar()

    await svc.submit(db, token=token, payload=_payload(
        legal_name="DIVIDEND PHARMACY", phone="+2348031111111",
        claims_existing_customer=True))
    await db.commit()
    reg_id = (await db.execute(
        text("""SELECT id FROM distributor_registrations
                 ORDER BY submitted_at DESC LIMIT 1"""))).scalar()

    result = await svc.review(
        db, registration_id=reg_id, approve=True,
        note="Known customer; approved for distributorship.",
        link_customer_id=customer_id, acknowledge_duplicates=True, actor=admin)
    await db.commit()

    # Nothing was copied.
    orders_after = (await db.execute(
        text("SELECT COUNT(*) FROM sales_orders"))).scalar()
    value_after = (await db.execute(
        text("SELECT COALESCE(SUM(total_amount),0) FROM sales_orders"))).scalar()
    assert orders_after == orders_before, "orders were duplicated"
    assert value_after == value_before, "revenue was recounted"

    # They are simply visible under the distributor now.
    assert result["history"]["orders_attributed"] == 5
    assert Decimal(result["history"]["value"]) == Decimal("1000000")
    assert "Nothing was copied" in result["history"]["note"]

    distributor_id = result["distributor"]["id"]
    attributed = (await db.execute(
        text("SELECT COUNT(*) FROM sales_orders WHERE distributor_id = :d"),
        {"d": distributor_id})).scalar()
    assert attributed == 5

    # And the distributor uses the EXISTING customer account, not a new one.
    linked = (await db.execute(
        text("SELECT customer_id FROM distributors WHERE id = :d"),
        {"d": distributor_id})).scalar()
    assert str(linked) == str(customer_id)


@pytest.mark.asyncio
async def test_historical_orders_keep_the_channel_they_were_placed_on(db):
    """They were direct sales at the time. Rewriting that falsifies history."""
    admin = await _user(db)
    _, token = await _link(db, admin)
    customer_id = await _existing_customer(db, name="Channel Test Ltd",
                                            phone="+2348032222222", orders=3)

    await svc.submit(db, token=token, payload=_payload(
        legal_name="Channel Test Ltd", phone="+2348032222222"))
    await db.commit()
    reg_id = (await db.execute(
        text("""SELECT id FROM distributor_registrations
                 ORDER BY submitted_at DESC LIMIT 1"""))).scalar()

    result = await svc.review(
        db, registration_id=reg_id, approve=True, note="Approved.",
        link_customer_id=customer_id, acknowledge_duplicates=True, actor=admin)
    await db.commit()

    rows = (await db.execute(
        text("""SELECT sales_channel, distributor_attributed_at
                  FROM sales_orders WHERE distributor_id = :d"""),
        {"d": result["distributor"]["id"]})).mappings().all()
    assert rows
    for row in rows:
        assert row["sales_channel"] == "DIRECT", (
            "an order placed before the distributor existed must not be "
            "relabelled as a distributor sale")
        assert row["distributor_attributed_at"] is not None, (
            "an attributed order must be distinguishable from one actually "
            "placed as a distributor order")


@pytest.mark.asyncio
async def test_being_linked_grants_no_sell_through_history(db):
    """Performance measures downstream sales, not what the company sold them."""
    admin = await _user(db)
    _, token = await _link(db, admin)
    customer_id = await _existing_customer(db, name="Perf Neutral Ltd",
                                            phone="+2348033333333", orders=4)
    await svc.submit(db, token=token, payload=_payload(
        legal_name="Perf Neutral Ltd", phone="+2348033333333"))
    await db.commit()
    reg_id = (await db.execute(
        text("""SELECT id FROM distributor_registrations
                 ORDER BY submitted_at DESC LIMIT 1"""))).scalar()
    result = await svc.review(
        db, registration_id=reg_id, approve=True, note="Approved.",
        link_customer_id=customer_id, acknowledge_duplicates=True, actor=admin)
    await db.commit()

    downstream = (await db.execute(
        text("SELECT COUNT(*) FROM distributor_sales WHERE distributor_id = :d"),
        {"d": result["distributor"]["id"]})).scalar()
    assert downstream == 0, (
        "linking must not invent a sell-through record they never reported")


@pytest.mark.asyncio
async def test_what_was_claimed_and_what_was_decided_stay_separate(db):
    admin = await _user(db)
    _, token = await _link(db, admin)
    claimed = await _existing_customer(db, name="Claimed Ltd",
                                        phone="+2348034444444", orders=2)
    actual = await _existing_customer(db, name="Actually This One Ltd",
                                       phone="+2348035555555", orders=6)

    await svc.submit(db, token=token, payload=_payload(
        legal_name="Claimed Ltd", phone="+2348034444444",
        claims_existing_customer=True, claimed_customer_id=str(claimed)))
    await db.commit()
    reg_id = (await db.execute(
        text("""SELECT id FROM distributor_registrations
                 ORDER BY submitted_at DESC LIMIT 1"""))).scalar()

    # The reviewer disagrees with the applicant and links the other account.
    result = await svc.review(
        db, registration_id=reg_id, approve=True,
        note="They trade under the other account; linked that instead.",
        link_customer_id=actual, acknowledge_duplicates=True, actor=admin)
    await db.commit()

    row = (await db.execute(
        text("""SELECT claimed_customer_id, linked_customer_id
                  FROM distributor_registrations WHERE id = :r"""),
        {"r": str(reg_id)})).mappings().first()
    assert str(row["claimed_customer_id"]) == str(claimed)
    assert str(row["linked_customer_id"]) == str(actual)
    assert result["history"]["orders_attributed"] == 6


@pytest.mark.asyncio
async def test_approving_without_a_customer_creates_a_fresh_distributor(db):
    admin = await _user(db)
    _, token = await _link(db, admin)
    await svc.submit(db, token=token,
                      payload=_payload(legal_name="Brand New Trading Ltd"))
    await db.commit()
    reg_id = (await db.execute(
        text("""SELECT id FROM distributor_registrations
                 ORDER BY submitted_at DESC LIMIT 1"""))).scalar()

    result = await svc.review(
        db, registration_id=reg_id, approve=True,
        note="No existing relationship; approved as a new distributor.",
        actor=admin)
    await db.commit()

    assert result["distributor"]["status"] == "DRAFT"
    assert result["history"] is None
    assert "DRAFT" in result["next"]


@pytest.mark.asyncio
async def test_a_decision_needs_a_reason_and_cannot_be_retaken(db):
    admin = await _user(db)
    _, token = await _link(db, admin)
    await svc.submit(db, token=token, payload=_payload())
    await db.commit()
    reg_id = (await db.execute(
        text("""SELECT id FROM distributor_registrations
                 ORDER BY submitted_at DESC LIMIT 1"""))).scalar()

    with pytest.raises(HTTPException) as exc:
        await svc.review(db, registration_id=reg_id, approve=False, note="x",
                          actor=admin)
    await db.rollback()
    assert "entitled to a reason" in exc.value.detail

    await svc.review(db, registration_id=reg_id, approve=False,
                      note="No storage facility and no CAC registration.",
                      actor=admin)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.review(db, registration_id=reg_id, approve=True,
                          note="Changed my mind", actor=admin)
    await db.rollback()
    assert "already rejected" in exc.value.detail


@pytest.mark.asyncio
async def test_a_forged_customer_id_on_the_form_is_discarded(db):
    """Otherwise an applicant could attach themselves to somebody's account."""
    admin = await _user(db)
    _, token = await _link(db, admin)

    await svc.submit(db, token=token, payload=_payload(
        claims_existing_customer=True,
        claimed_customer_id=str(uuid.uuid4())))
    await db.commit()

    claimed = (await db.execute(
        text("""SELECT claimed_customer_id FROM distributor_registrations
                 ORDER BY submitted_at DESC LIMIT 1"""))).scalar()
    assert claimed is None


@pytest.mark.asyncio
async def test_the_link_can_be_copied_again_because_it_is_meant_to_be_public(db):
    """A registration link is printed on flyers, so hiding it bought nothing.

    It was first stored as a hash only, copied from the ordering link. The
    first one issued in production was shown once, the page was reloaded, and
    it was gone -- unrecoverable, for a link whose entire purpose is to be
    published. See i4567890123h for why this is the right trade here and the
    wrong one for an ordering link.
    """
    admin = await _user(db)
    issued, token = await _link(db, admin, label="Trade fair, Aba, March")

    links = await svc.list_links(db, base_url="https://erp.example.test")
    mine = [l for l in links if l["id"] == issued["id"]]
    assert len(mine) == 1
    assert mine[0]["url"] == f"https://erp.example.test/register/{token}"
    assert mine[0]["recoverable"] is True

    # And the copy that comes back still opens the form.
    opened = await svc.resolve(db, token=mine[0]["url"].rsplit("/", 1)[-1])
    assert opened["id"] == uuid.UUID(issued["id"])


@pytest.mark.asyncio
async def test_lookup_still_goes_through_the_hash(db):
    """The plain token is for display. Matching a request is still by hash.

    Two columns that could disagree is how a revoked link keeps working, so
    there is one resolver and it reads token_sha256.
    """
    admin = await _user(db)
    issued, token = await _link(db, admin, label="Hash path check")

    # Corrupt the DISPLAY copy only. Resolution must be unaffected.
    await db.execute(
        text("UPDATE distributor_registration_links SET token = 'rubbish' "
             "WHERE id = :i"), {"i": issued["id"]})
    await db.commit()

    opened = await svc.resolve(db, token=token)
    assert opened["id"] == uuid.UUID(issued["id"])

    with pytest.raises(HTTPException) as exc:
        await svc.resolve(db, token="rubbish-but-long-enough-to-pass-length")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_a_link_issued_before_the_token_was_kept_says_so(db):
    """Old rows have no token and nothing can derive one. Say that, plainly."""
    admin = await _user(db)
    issued, _ = await _link(db, admin, label="Issued before the change")
    await db.execute(
        text("UPDATE distributor_registration_links SET token = NULL "
             "WHERE id = :i"), {"i": issued["id"]})
    await db.commit()

    links = await svc.list_links(db, base_url="https://erp.example.test")
    old = [l for l in links if l["id"] == issued["id"]][0]
    assert old["url"] is None
    assert old["recoverable"] is False
    # The fingerprint survives, so the row can still be told apart when it is
    # time to revoke it.
    assert old["token_hint"]


# ---------------------------------------------------------------------------
# Ground that is already spoken for
# ---------------------------------------------------------------------------

async def _geography(db, *, lga_names=("Alpha", "Beta", "Gamma")):
    """A state of our own, so these tests do not depend on the seeded map."""
    suffix = uuid.uuid4().hex[:6].upper()
    country = (await db.execute(
        text("SELECT id FROM countries WHERE iso2 = 'NG'"))).scalar()
    state_id = uuid.uuid4()
    await db.execute(
        text("""INSERT INTO states (id, country_id, code, name)
                VALUES (:i, :c, :code, :n)"""),
        {"i": str(state_id), "c": str(country), "code": suffix[:4],
         "n": f"Teststate {suffix}"})
    lgas = {}
    for name in lga_names:
        lga_id = uuid.uuid4()
        await db.execute(
            text("""INSERT INTO lgas (id, state_id, name)
                    VALUES (:i, :s, :n)"""),
            {"i": str(lga_id), "s": str(state_id), "n": f"{name} {suffix}"})
        lgas[name] = lga_id
    await db.commit()
    return state_id, lgas


async def _registration_id(db, reference):
    return (await db.execute(
        text("""SELECT id FROM distributor_registrations
                 WHERE registration_reference = :r"""),
        {"r": reference})).scalar()


@pytest.mark.asyncio
async def test_an_area_under_application_is_shown_but_cannot_be_chosen(db):
    """Shown, not hidden. An area that vanishes reads as broken software.

    Worse, the applicant assumes their own area is missing and picks the
    neighbouring one, and now the address on the application is wrong.
    """
    admin = await _user(db)
    _, token = await _link(db, admin)
    state_id, lgas = await _geography(db)

    await svc.submit(db, token=token,
                     payload=_payload(state_id=str(state_id),
                                      lga_id=str(lgas["Alpha"])))
    await db.commit()

    listed = await svc.lgas_with_availability(db, state_id=state_id)
    by_id = {l["id"]: l for l in listed}

    assert len(listed) == 3, "a taken area must still be listed"
    assert by_id[str(lgas["Alpha"])]["available"] is False
    assert by_id[str(lgas["Beta"])]["available"] is True


@pytest.mark.asyncio
async def test_the_dropdown_is_a_courtesy_and_the_server_is_the_rule(db):
    """A disabled option stops nobody who sends the request by hand."""
    admin = await _user(db)
    _, token = await _link(db, admin)
    state_id, lgas = await _geography(db)

    await svc.submit(db, token=token,
                     payload=_payload(state_id=str(state_id),
                                      lga_id=str(lgas["Alpha"])))
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.submit(db, token=token,
                         payload=_payload(legal_name="Second Applicant Ltd",
                                          state_id=str(state_id),
                                          lga_id=str(lgas["Alpha"])))
    assert exc.value.status_code == 409
    assert "already covered" in exc.value.detail


@pytest.mark.asyncio
async def test_rejecting_an_application_frees_the_area_again(db):
    """What makes claiming-by-application safe on a link anyone can forward.

    Somebody could otherwise lock out a state with junk applications. The cure
    is the rejection that was going to happen anyway.
    """
    admin = await _user(db)
    _, token = await _link(db, admin)
    state_id, lgas = await _geography(db)

    result = await svc.submit(
        db, token=token,
        payload=_payload(state_id=str(state_id), lga_id=str(lgas["Alpha"])))
    await db.commit()
    registration_id = await _registration_id(
        db, result["registration_reference"])

    listed = {l["id"]: l for l in
              await svc.lgas_with_availability(db, state_id=state_id)}
    assert listed[str(lgas["Alpha"])]["available"] is False

    await svc.review(db, registration_id=registration_id, approve=False,
                     note="Not a fit at this time.", actor=admin)
    await db.commit()

    listed = {l["id"]: l for l in
              await svc.lgas_with_availability(db, state_id=state_id)}
    assert listed[str(lgas["Alpha"])]["available"] is True, (
        "rejecting an application must release the area it claimed")


@pytest.mark.asyncio
async def test_an_approved_application_keeps_holding_its_area(db):
    """Approval creates the distributor, and the area stays taken through it."""
    admin = await _user(db)
    _, token = await _link(db, admin)
    state_id, lgas = await _geography(db)

    # Its own name and number: approving runs the duplicate check, and the
    # default payload collides with a distributor another test created.
    result = await svc.submit(
        db, token=token,
        payload=_payload(legal_name=f"Holder {uuid.uuid4().hex[:6]} Ltd",
                         phone=f"+23480{uuid.uuid4().int % 10**8:08d}",
                         state_id=str(state_id), lga_id=str(lgas["Alpha"])))
    await db.commit()
    registration_id = await _registration_id(
        db, result["registration_reference"])

    await svc.review(db, registration_id=registration_id, approve=True,
                     note="Approved after visit.", actor=admin)
    await db.commit()

    listed = {l["id"]: l for l in
              await svc.lgas_with_availability(db, state_id=state_id)}
    assert listed[str(lgas["Alpha"])]["available"] is False


@pytest.mark.asyncio
async def test_a_terminated_distributor_releases_its_area(db):
    """Otherwise ground is held for ever by somebody no longer trading."""
    state_id, lgas = await _geography(db)
    await db.execute(
        text("""INSERT INTO distributors
                    (id, distributor_code, legal_name, entity_type, status,
                     lga_id, state_id)
                VALUES (gen_random_uuid(), :c, 'Former Holder Ltd', 'COMPANY',
                        'TERMINATED', :l, :s)"""),
        {"c": f"D{uuid.uuid4().hex[:8].upper()}", "l": str(lgas["Alpha"]),
         "s": str(state_id)})
    await db.commit()

    listed = {l["id"]: l for l in
              await svc.lgas_with_availability(db, state_id=state_id)}
    assert listed[str(lgas["Alpha"])]["available"] is True


@pytest.mark.asyncio
async def test_a_state_closes_only_when_every_area_in_it_is_taken(db):
    """A state is a container, not a grant.

    Closing a whole state because one LGA in it is held would hand one
    distributor a state-wide exclusivity nobody agreed to.
    """
    admin = await _user(db)
    _, token = await _link(db, admin)
    state_id, lgas = await _geography(db, lga_names=("Alpha", "Beta"))

    await svc.submit(db, token=token,
                     payload=_payload(state_id=str(state_id),
                                      lga_id=str(lgas["Alpha"])))
    await db.commit()

    mine = [s for s in await svc.states_with_availability(db)
            if s["id"] == str(state_id)][0]
    assert mine["available"] is True, "one taken area must not close a state"
    assert mine["available_lgas"] == 1
    assert mine["total_lgas"] == 2

    await svc.submit(db, token=token,
                     payload=_payload(legal_name="Second Applicant Ltd",
                                      state_id=str(state_id),
                                      lga_id=str(lgas["Beta"])))
    await db.commit()

    mine = [s for s in await svc.states_with_availability(db)
            if s["id"] == str(state_id)][0]
    assert mine["available"] is False
    assert mine["available_lgas"] == 0


@pytest.mark.asyncio
async def test_the_public_is_never_told_who_holds_an_area(db):
    """The reason would map the distributor network for anyone with the link.

    This is the same mistake as a customer-name type-ahead, in a different
    field: "covered by X Pharmacy, Aba North" repeated across 774 LGAs is the
    whole network, exported by a stranger.
    """
    admin = await _user(db)
    _, token = await _link(db, admin)
    state_id, lgas = await _geography(db)

    await svc.submit(
        db, token=token,
        payload=_payload(legal_name="Very Distinctive Holder Ltd",
                         phone="+2348090001234",
                         state_id=str(state_id), lga_id=str(lgas["Alpha"])))
    await db.commit()

    listed = await svc.lgas_with_availability(db, state_id=state_id)
    blob = repr(listed)
    assert "Very Distinctive Holder" not in blob
    assert "+2348090001234" not in blob

    taken = [l for l in listed if not l["available"]]
    assert {l["note"] for l in taken} == {"Already covered"}, (
        "every taken area must answer identically, or the difference itself "
        "says which kind of holder is there")


@pytest.mark.asyncio
async def test_a_reviewer_can_see_the_application_that_claimed_the_area(db):
    """Excluding its own claim, or the reviewer sees it blocked by itself."""
    admin = await _user(db)
    _, token = await _link(db, admin)
    state_id, lgas = await _geography(db)

    result = await svc.submit(
        db, token=token,
        payload=_payload(state_id=str(state_id), lga_id=str(lgas["Alpha"])))
    await db.commit()
    registration_id = await _registration_id(
        db, result["registration_reference"])

    listed = {l["id"]: l for l in await svc.lgas_with_availability(
        db, state_id=state_id, exclude_registration_id=registration_id)}
    assert listed[str(lgas["Alpha"])]["available"] is True


# ---------------------------------------------------------------------------
# Approving somebody who is already a customer
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_linking_the_matching_customer_is_enough_to_approve(db):
    """The dead end this hit in production, on REG-202609-D2995E.

    The applicant gave the phone number and email of an existing customer
    account -- the ordinary case, because the contact person already buys from
    the company. The reviewer did the right thing and chose to link that
    customer, and approval was refused: create_distributor re-ran the duplicate
    check and found the very customer being linked.

    The only way through the screen was to tick "this is a different business",
    which was untrue. A workflow whose only exit is a false statement is a bug,
    not a safeguard.
    """
    admin = await _user(db)
    _, token = await _link(db, admin)
    shared_phone = f"+23480{uuid.uuid4().int % 10**8:08d}"
    customer_id = await _existing_customer(
        db, name="Ozioko Fabian U", phone=shared_phone, orders=2)

    result = await svc.submit(
        db, token=token,
        payload=_payload(legal_name="Tripleluminance Resources Nig. Ltd",
                         phone=shared_phone))
    await db.commit()
    registration_id = await _registration_id(
        db, result["registration_reference"])

    decided = await svc.review(
        db, registration_id=registration_id, approve=True,
        note="Same business as the existing account; verified by phone.",
        link_customer_id=customer_id, actor=admin)
    await db.commit()

    assert decided["status"] == "APPROVED"
    assert decided["history"]["orders_attributed"] == 2, (
        "linking must still bring the customer's existing orders across")


@pytest.mark.asyncio
async def test_a_match_the_reviewer_has_not_linked_still_blocks(db):
    """Linking one account answers for that account and no other.

    The fix must not turn the duplicate check off; it must narrow it to what
    the reviewer has actually decided.
    """
    admin = await _user(db)
    _, token = await _link(db, admin)
    shared_phone = f"+23480{uuid.uuid4().int % 10**8:08d}"
    await _existing_customer(db, name="Some Existing Buyer",
                             phone=shared_phone, orders=1)

    result = await svc.submit(
        db, token=token,
        payload=_payload(legal_name="Unrelated Applicant Ltd",
                         phone=shared_phone))
    await db.commit()
    registration_id = await _registration_id(
        db, result["registration_reference"])

    with pytest.raises(HTTPException) as exc:
        await svc.review(db, registration_id=registration_id, approve=True,
                         note="Approving without deciding who this is.",
                         actor=admin)
    assert exc.value.status_code == 409
    assert exc.value.detail["candidates"], (
        "a refusal must name what it matched, or the reviewer cannot act on it")


@pytest.mark.asyncio
async def test_a_reviewer_can_still_say_it_is_a_different_business(db):
    """Two businesses genuinely can share a phone number -- a shared line, a
    landlord's number, a family business. The reviewer has to be able to say so.
    """
    admin = await _user(db)
    _, token = await _link(db, admin)
    shared_phone = f"+23480{uuid.uuid4().int % 10**8:08d}"
    await _existing_customer(db, name="Neighbour Enterprises",
                             phone=shared_phone, orders=1)

    result = await svc.submit(
        db, token=token,
        payload=_payload(legal_name="Genuinely Different Ltd",
                         phone=shared_phone))
    await db.commit()
    registration_id = await _registration_id(
        db, result["registration_reference"])

    decided = await svc.review(
        db, registration_id=registration_id, approve=True,
        note="Shared line in the same building; visited both. Different owners.",
        acknowledge_duplicates=True, actor=admin)
    await db.commit()

    assert decided["status"] == "APPROVED"
    assert decided["history"] is None, "nothing should have been attributed"


@pytest.mark.asyncio
async def test_an_existing_distributor_is_never_waved_through_by_a_link(db):
    """Linking a CUSTOMER cannot answer for a matching DISTRIBUTOR.

    That would mean a second distributor record for a company already in the
    register -- two codes, two dossiers, and territory conflicts that only
    surface much later.
    """
    admin = await _user(db)
    _, token = await _link(db, admin)
    shared_phone = f"+23480{uuid.uuid4().int % 10**8:08d}"
    customer_id = await _existing_customer(
        db, name="Same Group Trading", phone=shared_phone, orders=1)
    # APPROVED rather than ACTIVE: ck_dist_active_provisioned requires an
    # ACTIVE distributor to have its customer and warehouse, and this fixture
    # only needs a row that the duplicate matcher will find.
    await db.execute(
        text("""INSERT INTO distributors
                    (id, distributor_code, legal_name, entity_type, status,
                     phone)
                VALUES (gen_random_uuid(), :c, 'Already A Distributor Ltd',
                        'COMPANY', 'APPROVED', :p)"""),
        {"c": f"D{uuid.uuid4().hex[:8].upper()}", "p": shared_phone})
    await db.commit()

    result = await svc.submit(
        db, token=token,
        payload=_payload(legal_name="Same Group Trading", phone=shared_phone))
    await db.commit()
    registration_id = await _registration_id(
        db, result["registration_reference"])

    with pytest.raises(HTTPException) as exc:
        await svc.review(db, registration_id=registration_id, approve=True,
                         note="Linking the customer and hoping for the best.",
                         link_customer_id=customer_id, actor=admin)
    assert exc.value.status_code == 409
    kinds = {c["kind"] for c in exc.value.detail["candidates"]}
    assert "distributor" in kinds
    assert "customer" not in kinds, (
        "the linked customer is resolved; only the distributor should remain")
