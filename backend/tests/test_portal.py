"""The distributor ordering portal.

A shareable link is an unauthenticated credential on the public internet, so the
claims worth defending are mostly about what it CANNOT do:

  * the token is never stored -- a dump of the table hands out no working links;
  * it expires, it can be revoked, and a revoked one can never be reinstated;
  * a miss is logged, because a run of misses is what guessing looks like;
  * the catalogue carries no prices, and the quote returns one total and no
    per-line figures;
  * the order is priced from the database, so a client that sends its own total
    changes nothing;
  * the order it produces is a real sales_order -- there is no second table.
"""
import hashlib
import importlib.util
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.services import distributors as dsvc
from app.services import portal as svc

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")
SYNC_DB = (TEST_DB or "").replace("+asyncpg", "")

BASE_SCHEMA = """
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
DROP TABLE IF EXISTS payments CASCADE;
DROP TABLE IF EXISTS invoices CASCADE;
DROP TABLE IF EXISTS sales_order_lines CASCADE;
DROP TABLE IF EXISTS sales_orders CASCADE;
DROP TABLE IF EXISTS product_pricing CASCADE;
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
    description TEXT, manufacturer VARCHAR(255),
    unit VARCHAR(32) NOT NULL DEFAULT 'each',
    cost_price NUMERIC(18,2) DEFAULT 0, selling_price NUMERIC(18,2) DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE product_pricing (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID NOT NULL REFERENCES products(id),
    unit VARCHAR(32) NOT NULL,
    retail_price NUMERIC(18,2), wholesale_price NUMERIC(18,2)
);
CREATE TABLE warehouses (
    -- manager_id included because _get_default_warehouse goes through the ORM
    -- Warehouse model, which selects every mapped column.
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
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE sales_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_number VARCHAR(64) UNIQUE NOT NULL,
    customer_id UUID NOT NULL REFERENCES customers(id),
    warehouse_id UUID REFERENCES warehouses(id),
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    payment_status VARCHAR(32) NOT NULL DEFAULT 'unpaid',
    payment_date TIMESTAMPTZ,
    order_date TIMESTAMPTZ DEFAULT NOW(),
    required_date TIMESTAMPTZ,
    total_amount NUMERIC(18,2) DEFAULT 0,
    notes TEXT,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE sales_order_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sales_order_id UUID NOT NULL REFERENCES sales_orders(id),
    product_id UUID NOT NULL REFERENCES products(id),
    unit VARCHAR(50), quantity NUMERIC(18,6) NOT NULL,
    unit_price NUMERIC(18,6) NOT NULL, line_total NUMERIC(18,2) NOT NULL,
    unit_cost NUMERIC(18,6), cost_total NUMERIC(18,2),
    cost_source VARCHAR(32)
);
-- The AR tables customer_debt.outstanding_for_customer reads. Present so the
-- credit position is exercised for real rather than asserted in its degraded
-- "unavailable" form, which would test nothing.
CREATE TABLE invoices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_number VARCHAR(64) UNIQUE NOT NULL,
    sales_order_id UUID REFERENCES sales_orders(id),
    customer_id UUID REFERENCES customers(id),
    total_amount NUMERIC(18,2) DEFAULT 0,
    amount_paid NUMERIC(18,2) DEFAULT 0,
    status VARCHAR(32) DEFAULT 'open',
    issue_date DATE DEFAULT CURRENT_DATE,
    due_date DATE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id UUID REFERENCES invoices(id),
    amount NUMERIC(18,2) NOT NULL,
    paid_on DATE DEFAULT CURRENT_DATE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
"""

MIGRATIONS = [
    ("m5_dist", "x3456789012w_distributor_foundation.py"),
    ("m5_comp", "y4567890123x_distributor_compliance.py"),
    ("m5_apps", "z5678901234y_territory_applications.py"),
    ("m5_portal", "a6789012345z_distributor_portal.py"),
]


def _load(name, filename):
    path = (Path(__file__).resolve().parents[1] / "alembic" / "versions"
            / filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeUser:
    def __init__(self, user_id, role="admin", full_name="Admin"):
        self.id = user_id
        self.role = role
        self.full_name = full_name


class Item:
    """What the API layer hands the service."""
    def __init__(self, product_id, unit, quantity):
        self.product_id = str(product_id)
        self.unit = unit
        self.quantity = quantity


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


@pytest_asyncio.fixture
async def db(schema):
    engine = create_async_engine(TEST_DB, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

async def _admin(db, name="Sales Admin"):
    uid = uuid.uuid4()
    await db.execute(
        text("INSERT INTO users (id, email, full_name, role) "
             "VALUES (:i, :e, :n, 'admin')"),
        {"i": str(uid), "e": f"{uid}@t.test", "n": name})
    await db.commit()
    return FakeUser(uid, full_name=name)


async def _warehouse(db):
    existing = (await db.execute(
        text("SELECT id FROM warehouses WHERE UPPER(name) = 'SALES WAREHOUSE'")
    )).first()
    if existing:
        return existing.id
    wid = uuid.uuid4()
    await db.execute(
        text("""INSERT INTO warehouses (id, code, name, is_active)
                VALUES (:i, :c, 'Sales Warehouse', TRUE)"""),
        {"i": str(wid), "c": f"SW{uuid.uuid4().hex[:6].upper()}"})
    await db.commit()
    return wid


async def _product(db, *, name=None, unit="carton", wholesale="12000.00",
                   retail="15000.00", stock=100):
    pid = uuid.uuid4()
    name = name or f"Product {uuid.uuid4().hex[:6]}"
    await db.execute(
        text("""INSERT INTO products (id, sku, name, description, unit)
                VALUES (:i, :s, :n, 'A test product', :u)"""),
        {"i": str(pid), "s": f"SKU{uuid.uuid4().hex[:8].upper()}", "n": name,
         "u": unit})
    await db.execute(
        text("""INSERT INTO product_pricing
                    (id, product_id, unit, retail_price, wholesale_price)
                VALUES (gen_random_uuid(), :p, :u, :r, :w)"""),
        {"p": str(pid), "u": unit, "r": retail, "w": wholesale})
    wid = await _warehouse(db)
    await db.execute(
        text("""INSERT INTO stock_levels (id, warehouse_id, product_id,
                                          current_stock)
                VALUES (gen_random_uuid(), :w, :p, :s)"""),
        {"w": str(wid), "p": str(pid), "s": stock})
    await db.commit()
    return pid, name, unit


async def _active_distributor(db, admin, *, credit_limit=None):
    r = await dsvc.create_distributor(
        db, legal_name=f"Portal Test {uuid.uuid4().hex[:6]}", actor=admin,
        acknowledge_duplicates=True)
    await db.commit()
    did = uuid.UUID(r["id"])
    for status, reason in (("APPLIED", "Submitted"),
                           ("UNDER_REVIEW", "Reviewing"),
                           ("APPROVED", "Approved"),
                           ("ACTIVE", "Trading")):
        await dsvc.set_status(db, distributor_id=did, new_status=status,
                              reason=reason, actor=admin)
    await db.commit()
    if credit_limit is not None:
        await db.execute(
            text("""UPDATE customers SET credit_limit = :c
                     WHERE id = (SELECT customer_id FROM distributors
                                  WHERE id = :d)"""),
            {"c": credit_limit, "d": str(did)})
        await db.commit()
    return did


async def _link(db, admin, distributor_id=None, **kw):
    did = distributor_id or await _active_distributor(db, admin)
    result = await svc.issue_link(
        db, distributor_id=did, label=kw.pop("label", "Chinedu, Aba depot"),
        base_url="https://erp.example.test", actor=admin, **kw)
    await db.commit()
    token = result["url"].rsplit("/", 1)[-1]
    return did, result, token


# ---------------------------------------------------------------------------
# The token is a credential
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_token_is_never_stored(db):
    """A dump of this table must hand out no working links."""
    admin = await _admin(db)
    _, result, token = await _link(db, admin)

    row = (await db.execute(
        text("""SELECT token_sha256, token_hint FROM distributor_order_links
                 WHERE id = :l"""), {"l": result["id"]})).mappings().first()

    assert row["token_sha256"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in row["token_sha256"]
    assert row["token_hint"] == token[-6:]

    # And nothing anywhere in the row holds the token itself.
    everything = (await db.execute(
        text("SELECT * FROM distributor_order_links WHERE id = :l"),
        {"l": result["id"]})).mappings().first()
    assert not any(token in str(v) for v in everything.values())

    # Long enough that guessing is not a strategy.
    assert len(token) >= 40


@pytest.mark.asyncio
async def test_a_link_always_expires(db):
    admin = await _admin(db)
    _, result, _ = await _link(db, admin, valid_days=7)
    expires = (await db.execute(
        text("SELECT expires_at FROM distributor_order_links WHERE id = :l"),
        {"l": result["id"]})).scalar()
    assert expires is not None
    assert expires < datetime.now(timezone.utc) + timedelta(days=8)

    # There is no way to ask for a link that never expires.
    for bad in (0, -1, 366, 100000):
        with pytest.raises(HTTPException):
            await svc.issue_link(db, distributor_id=uuid.uuid4(),
                                 label="Forever please", valid_days=bad)
        await db.rollback()


@pytest.mark.asyncio
async def test_an_expired_link_stops_working(db):
    admin = await _admin(db)
    _, result, token = await _link(db, admin)

    # Age the whole row, not just its expiry: ck_link_expiry_after_issue
    # rightly refuses a link that expired before it was created, and that
    # constraint is worth keeping.
    await db.execute(
        text("""UPDATE distributor_order_links
                   SET created_at = NOW() - INTERVAL '40 days',
                       expires_at = NOW() - INTERVAL '1 day'
                 WHERE id = :l"""),
        {"l": result["id"]})
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.resolve(db, token=token)
    await db.rollback()
    assert exc.value.status_code == 403
    assert "expired" in exc.value.detail


@pytest.mark.asyncio
async def test_a_revoked_link_stops_working_and_stays_revoked(db):
    admin = await _admin(db)
    _, result, token = await _link(db, admin)
    link_id = uuid.UUID(result["id"])

    await svc.revoke_link(db, link_id=link_id,
                          reason="Phone lost by the depot manager", actor=admin)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.resolve(db, token=token)
    await db.rollback()
    assert exc.value.status_code == 403
    assert "withdrawn" in exc.value.detail

    # Un-revoking would make the audit trail lie about the window in which a
    # leaked credential worked.
    with pytest.raises(Exception) as exc:
        await db.execute(
            text("""UPDATE distributor_order_links SET revoked_at = NULL
                     WHERE id = :l"""), {"l": str(link_id)})
        await db.commit()
    await db.rollback()
    assert "cannot be reinstated" in str(exc.value)

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("DELETE FROM distributor_order_links WHERE id = :l"),
            {"l": str(link_id)})
        await db.commit()
    await db.rollback()
    assert "not deletable" in str(exc.value)


@pytest.mark.asyncio
async def test_revocation_must_say_why(db):
    admin = await _admin(db)
    _, result, _ = await _link(db, admin)
    with pytest.raises(HTTPException):
        await svc.revoke_link(db, link_id=uuid.UUID(result["id"]), reason="x",
                              actor=admin)
    await db.rollback()


@pytest.mark.asyncio
async def test_a_token_that_matches_nothing_is_logged(db):
    """A run of these is what someone guessing at links looks like."""
    before = (await db.execute(
        text("""SELECT COUNT(*) FROM distributor_order_link_events
                 WHERE event_type = 'NOT_FOUND'"""))).scalar()

    with pytest.raises(HTTPException) as exc:
        await svc.resolve(db, token="x" * 43, ip="203.0.113.9",
                          user_agent="curl/8.0")
    await db.commit()
    assert exc.value.status_code == 404

    after = (await db.execute(
        text("""SELECT COUNT(*) FROM distributor_order_link_events
                 WHERE event_type = 'NOT_FOUND'"""))).scalar()
    assert after == before + 1

    row = (await db.execute(
        text("""SELECT ip_address, user_agent FROM distributor_order_link_events
                 WHERE event_type = 'NOT_FOUND'
                 ORDER BY created_at DESC LIMIT 1"""))).mappings().first()
    assert row["ip_address"] == "203.0.113.9"


@pytest.mark.asyncio
async def test_usage_events_cannot_be_rewritten(db):
    admin = await _admin(db)
    _, result, token = await _link(db, admin)
    await svc.resolve(db, token=token, log_open=True, ip="198.51.100.4")
    await db.commit()

    for stmt in ("UPDATE distributor_order_link_events SET ip_address = '0.0.0.0'",
                 "DELETE FROM distributor_order_link_events"):
        with pytest.raises(Exception) as exc:
            await db.execute(text(stmt))
            await db.commit()
        await db.rollback()
        assert "append-only" in str(exc.value)


@pytest.mark.asyncio
async def test_only_an_active_distributor_gets_a_link(db):
    admin = await _admin(db)
    r = await dsvc.create_distributor(
        db, legal_name=f"Not Active {uuid.uuid4().hex[:6]}", actor=admin,
        acknowledge_duplicates=True)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.issue_link(db, distributor_id=uuid.UUID(r["id"]),
                             label="Too early", actor=admin)
    await db.rollback()
    assert "draft" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_suspending_a_distributor_closes_the_portal(db):
    admin = await _admin(db)
    did, _, token = await _link(db, admin)

    await svc.resolve(db, token=token)  # works while active
    await dsvc.set_status(db, distributor_id=did, new_status="SUSPENDED",
                          reason="Payment overdue beyond terms", actor=admin)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.resolve(db, token=token)
    await db.rollback()
    assert exc.value.status_code == 403
    assert "paused" in exc.value.detail


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_catalogue_carries_no_price_at_all(db):
    """Not zeroed, not masked -- absent. The query never selects them."""
    await _product(db, wholesale="12000.00", retail="15000.00")
    items = await svc.catalogue(db)
    assert items

    for item in items:
        for key, value in item.items():
            assert "price" not in key.lower(), f"{key} leaks pricing"
        # And no field happens to carry the number either.
        assert "12000" not in str(item)
        assert "15000" not in str(item)


@pytest.mark.asyncio
async def test_an_unpriced_variant_is_not_offered(db):
    """Better absent than in a basket that fails at checkout."""
    pid = uuid.uuid4()
    await db.execute(
        text("""INSERT INTO products (id, sku, name, unit)
                VALUES (:i, :s, 'Unpriced Item', 'carton')"""),
        {"i": str(pid), "s": f"SKU{uuid.uuid4().hex[:8].upper()}"})
    await db.execute(
        text("""INSERT INTO product_pricing
                    (id, product_id, unit, retail_price, wholesale_price)
                VALUES (gen_random_uuid(), :p, 'carton', 9000, NULL)"""),
        {"p": str(pid)})
    await db.commit()

    items = await svc.catalogue(db)
    assert not any(i["product_id"] == str(pid) for i in items)


@pytest.mark.asyncio
async def test_a_quote_returns_one_total_and_no_line_prices(db):
    admin = await _admin(db)
    _, _, token = await _link(db, admin)
    pid_a, _, unit = await _product(db, wholesale="12000.00")
    pid_b, _, _ = await _product(db, wholesale="2500.00")

    result = await svc.quote(db, token=token, items=[
        Item(pid_a, unit, 2), Item(pid_b, unit, 4)])
    await db.commit()

    assert result["total"] == "34000.00"  # 2*12000 + 4*2500
    assert result["line_count"] == 2

    flat = str(result)
    assert "12000" not in flat and "2500" not in flat, (
        "no per-unit figure may reach the caller")
    for item in result["items"]:
        assert set(item) == {"product_id", "name", "unit", "quantity"}


@pytest.mark.asyncio
async def test_the_order_is_priced_from_the_database_not_the_client(db):
    """A total that arrived from the client is a price the customer chose."""
    admin = await _admin(db)
    did, _, token = await _link(db, admin)
    pid, _, unit = await _product(db, wholesale="12000.00")

    class Hostile(Item):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.unit_price = Decimal("1.00")
            self.line_total = Decimal("1.00")
            self.total = Decimal("1.00")

    result = await svc.place_order(db, token=token,
                                   items=[Hostile(pid, unit, 3)])
    await db.commit()

    assert result["total"] == "36000.00"

    stored = (await db.execute(
        text("""SELECT o.total_amount, l.unit_price, l.line_total
                  FROM sales_orders o
                  JOIN sales_order_lines l ON l.sales_order_id = o.id
                 WHERE o.order_number = :n"""),
        {"n": result["order_number"]})).mappings().first()
    assert Decimal(stored["total_amount"]) == Decimal("36000.00")
    assert Decimal(stored["unit_price"]) == Decimal("12000")


# ---------------------------------------------------------------------------
# The order is a real sales order
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_order_is_an_ordinary_sales_order(db):
    """No distributor order table. It goes straight into sales_orders."""
    admin = await _admin(db)
    did, link, token = await _link(db, admin)
    pid, _, unit = await _product(db, wholesale="5000.00")

    result = await svc.place_order(
        db, token=token, items=[Item(pid, unit, 2)],
        notes="Please deliver before Friday.")
    await db.commit()

    row = (await db.execute(
        text("""SELECT o.*, c.customer_code
                  FROM sales_orders o JOIN customers c ON c.id = o.customer_id
                 WHERE o.order_number = :n"""),
        {"n": result["order_number"]})).mappings().first()

    assert row["sales_channel"] == "DISTRIBUTOR"
    assert str(row["distributor_id"]) == str(did)
    assert str(row["order_link_id"]) == link["id"]
    assert row["status"] == "pending"
    assert row["payment_status"] == "unpaid"
    assert row["warehouse_id"] is not None, "despatched from a company warehouse"
    assert "Please deliver before Friday." in row["notes"]

    # It bills the distributor's existing customer account -- not a new one.
    customer_id = (await db.execute(
        text("SELECT customer_id FROM distributors WHERE id = :d"),
        {"d": str(did)})).scalar()
    assert str(row["customer_id"]) == str(customer_id)


@pytest.mark.asyncio
async def test_the_order_records_which_link_placed_it(db):
    """If a link leaks, what it ordered must be listable, not guessed at."""
    admin = await _admin(db)
    did, link, token = await _link(db, admin)
    pid, _, unit = await _product(db, wholesale="1000.00")

    placed = await svc.place_order(db, token=token, items=[Item(pid, unit, 5)])
    await db.commit()

    activity = await svc.link_activity(db, link_id=uuid.UUID(link["id"]))
    ordered = [e for e in activity if e["event_type"] == "ORDERED"]
    assert len(ordered) == 1
    assert ordered[0]["order_number"] == placed["order_number"]

    orders = await svc.distributor_orders(db, distributor_id=did)
    assert orders[0]["order_number"] == placed["order_number"]
    assert orders[0]["line_count"] == 1
    assert orders[0]["token_hint"] == link["token_hint"]


@pytest.mark.asyncio
async def test_minimum_order_quantities_are_enforced(db):
    admin = await _admin(db)
    _, _, token = await _link(db, admin)
    pid, name, unit = await _product(db, unit="carton", wholesale="12000.00")

    minimum = svc._wholesale_min_for(svc._norm_unit(unit))
    if minimum > 1:
        with pytest.raises(HTTPException) as exc:
            await svc.quote(db, token=token,
                            items=[Item(pid, unit, minimum - 1)])
        await db.rollback()
        assert name in exc.value.detail

    result = await svc.quote(db, token=token, items=[Item(pid, unit, minimum)])
    await db.commit()
    assert Decimal(result["total"]) > 0


@pytest.mark.asyncio
async def test_nonsense_baskets_are_refused(db):
    admin = await _admin(db)
    _, _, token = await _link(db, admin)
    pid, _, unit = await _product(db, wholesale="1000.00")

    for items, expected in (
        ([], "Nothing has been selected"),
        ([Item(pid, "drum", 10)], "not sold in"),
        ([Item(uuid.uuid4(), unit, 10)], "not found"),
        ([Item(pid, unit, -5)], "more than zero"),
    ):
        with pytest.raises(HTTPException) as exc:
            await svc.quote(db, token=token, items=items)
        await db.rollback()
        assert expected in exc.value.detail


@pytest.mark.asyncio
async def test_the_credit_position_is_reported_for_the_staff_who_confirm(db):
    """Reported, not enforced at the portal -- see the service docstring."""
    admin = await _admin(db)
    did = await _active_distributor(db, admin, credit_limit="10000.00")
    _, _, token = await _link(db, admin, distributor_id=did)
    pid, _, unit = await _product(db, wholesale="30000.00")

    result = await svc.place_order(db, token=token, items=[Item(pid, unit, 1)])
    await db.commit()

    # The order is accepted; the company decides, not the portal.
    assert result["order_number"]
    credit = result["credit"]
    assert credit["checked"] is True
    assert credit["limit_set"] is True
    assert credit["over_limit"] is True

    stored = (await db.execute(
        text("SELECT status FROM sales_orders WHERE order_number = :n"),
        {"n": result["order_number"]})).scalar()
    assert stored == "pending"


@pytest.mark.asyncio
async def test_opening_the_portal_records_the_use(db):
    admin = await _admin(db)
    _, link, token = await _link(db, admin)

    await svc.resolve(db, token=token, log_open=True, ip="192.0.2.10")
    await db.commit()

    row = (await db.execute(
        text("""SELECT use_count, last_used_at FROM distributor_order_links
                 WHERE id = :l"""), {"l": link["id"]})).mappings().first()
    assert row["use_count"] == 1
    assert row["last_used_at"] is not None

    rows = await svc.list_links(db)
    mine = [r for r in rows if str(r["id"]) == link["id"]]
    assert mine and mine[0]["is_live"] is True
    # The listing must never carry a token.
    assert all("token_sha256" not in r for r in rows)
