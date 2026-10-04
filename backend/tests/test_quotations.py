"""Quotations.

The expensive mistake this module exists to prevent is a quote that
re-prices itself. Most of these tests are about the price staying where it was
put, and about a quote that has expired not being honoured by accident.
"""
from __future__ import annotations

import os
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.services import quotations as svc

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")

SCHEMA = """
DROP TABLE IF EXISTS quotation_events CASCADE;
DROP TABLE IF EXISTS quotation_lines CASCADE;
DROP TABLE IF EXISTS quotations CASCADE;
DROP TABLE IF EXISTS sales_order_lines CASCADE;
DROP TABLE IF EXISTS sales_orders CASCADE;
DROP TABLE IF EXISTS product_pricing CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS warehouses CASCADE;
DROP TABLE IF EXISTS app_setting_changes CASCADE;
DROP TABLE IF EXISTS app_settings CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS users CASCADE;

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(100) UNIQUE, email VARCHAR(255) UNIQUE NOT NULL,
    full_name VARCHAR(255), role VARCHAR(50) DEFAULT 'sales_staff',
    hashed_password VARCHAR(255) DEFAULT 'x', is_active BOOLEAN DEFAULT TRUE
);
CREATE TABLE customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_code VARCHAR(32) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL, phone VARCHAR(40), email VARCHAR(255),
    address TEXT, is_active BOOLEAN DEFAULT TRUE,
    merged_into_id UUID REFERENCES customers(id)
);
CREATE TABLE warehouses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sku VARCHAR(64) UNIQUE NOT NULL, name VARCHAR(255) NOT NULL
);
CREATE TABLE product_pricing (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID REFERENCES products(id),
    unit VARCHAR(32), cost_price NUMERIC(18,2),
    retail_price NUMERIC(18,2), wholesale_price NUMERIC(18,2)
);
CREATE TABLE sales_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_number VARCHAR(64) UNIQUE NOT NULL,
    customer_id UUID REFERENCES customers(id),
    warehouse_id UUID REFERENCES warehouses(id),
    status VARCHAR(32) DEFAULT 'pending',
    payment_status VARCHAR(32) DEFAULT 'unpaid',
    order_date TIMESTAMPTZ DEFAULT NOW(),
    total_amount NUMERIC(18,2) DEFAULT 0,
    notes TEXT, sales_channel VARCHAR(32), created_by UUID
);
CREATE TABLE sales_order_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sales_order_id UUID REFERENCES sales_orders(id) ON DELETE CASCADE,
    product_id UUID REFERENCES products(id),
    unit VARCHAR(32), quantity NUMERIC(18,2),
    unit_price NUMERIC(18,2), line_total NUMERIC(18,2)
);
CREATE TABLE app_settings (
    key VARCHAR(80) PRIMARY KEY, value TEXT,
    value_type VARCHAR(12) NOT NULL DEFAULT 'STRING', description TEXT,
    updated_by UUID, updated_by_name VARCHAR(255),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
        _apply(c, "r0123456789q_quotations.py")
        c.commit()
    seng.dispose()
    eng = create_async_engine(TEST_DB, future=True)
    maker = sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        await s.execute(text(
            "INSERT INTO warehouses (name) VALUES ('Main') "
            "ON CONFLICT DO NOTHING"))
        yield s
    await eng.dispose()


class FakeUser:
    def __init__(self, uid, role="sales_staff"):
        self.id = uid
        self.role = role
        self.full_name = "Sales Officer" if role != "admin" else "Admin One"
        self.username = role


async def _user(db, role="sales_staff"):
    uid = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO users (id, username, email, full_name, role) "
        "VALUES (:i, :u, :e, :n, :r)"),
        {"i": str(uid), "u": f"u{uid.hex[:8]}", "e": f"{uid.hex[:8]}@t.local",
         "n": "Sales Officer", "r": role})
    return FakeUser(uid, role)


async def _customer(db, name="Hospital A"):
    cid = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO customers (id, customer_code, name) VALUES (:i, :c, :n)"),
        {"i": str(cid), "c": f"CUS{uuid.uuid4().hex[:8].upper()}", "n": name})
    return cid


async def _product(db, name="Hera Wound Gel", *, retail=10000, wholesale=8000,
                   unit="unit"):
    pid = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO products (id, sku, name) VALUES (:i, :s, :n)"),
        {"i": str(pid), "s": uuid.uuid4().hex[:8].upper(), "n": name})
    await db.execute(text("""
        INSERT INTO product_pricing
            (product_id, unit, retail_price, wholesale_price)
        VALUES (:p, :u, :r, :w)
    """), {"p": str(pid), "u": unit, "r": retail, "w": wholesale})
    return pid


async def _quote(db, customer, product, *, qty=2, actor=None, **kw):
    return await svc.create(
        db, customer_id=customer,
        items=[{"product_id": product, "unit": "unit", "quantity": qty}],
        actor=actor, **kw)


# ---------------------------------------------------------------------------
# The price is taken once and kept
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_quotation_does_not_follow_the_price_list(db):
    """The single most important behaviour in this module."""
    user = await _user(db)
    c = await _customer(db)
    p = await _product(db, retail=10000)

    q = await _quote(db, c, p, qty=2, actor=user)
    assert q["total_amount"] == 20000.0

    await db.execute(text(
        "UPDATE product_pricing SET retail_price = 25000 WHERE product_id = :p"),
        {"p": str(p)})

    again = await svc.get(db, quotation_id=uuid.UUID(q["id"]))
    assert again["total_amount"] == 20000.0, (
        "a quote the customer is holding must not re-price itself")
    assert again["lines"][0]["unit_price"] == 10000.0


@pytest.mark.asyncio
async def test_the_product_name_is_kept_as_quoted(db):
    user = await _user(db)
    c = await _customer(db)
    p = await _product(db, name="Hera Wound Gel")
    q = await _quote(db, c, p, actor=user)

    await db.execute(text(
        "UPDATE products SET name = 'Hera Wound Gel (discontinued)' "
        "WHERE id = :p"), {"p": str(p)})

    again = await svc.get(db, quotation_id=uuid.UUID(q["id"]))
    assert again["lines"][0]["product_name"] == "Hera Wound Gel"


@pytest.mark.asyncio
async def test_wholesale_customers_are_quoted_the_wholesale_price(db):
    user = await _user(db)
    c = await _customer(db)
    p = await _product(db, retail=10000, wholesale=8000)
    q = await _quote(db, c, p, qty=1, customer_type="wholesale", actor=user)
    assert q["total_amount"] == 8000.0


@pytest.mark.asyncio
async def test_a_product_with_no_price_cannot_be_quoted(db):
    user = await _user(db)
    c = await _customer(db)
    pid = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO products (id, sku, name) VALUES (:i, 'X', 'Unpriced')"),
        {"i": str(pid)})
    with pytest.raises(HTTPException) as e:
        await _quote(db, c, pid, actor=user)
    assert e.value.status_code == 400
    assert "No price is set" in str(e.value.detail)


@pytest.mark.asyncio
async def test_an_overridden_price_is_honoured(db):
    user = await _user(db)
    c = await _customer(db)
    p = await _product(db, retail=10000)
    q = await svc.create(
        db, customer_id=c,
        items=[{"product_id": p, "unit": "unit", "quantity": 1,
                "unit_price": Decimal("9500")}], actor=user)
    assert q["total_amount"] == 9500.0


# ---------------------------------------------------------------------------
# Validity
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_quotation_always_has_an_end_date(db):
    user = await _user(db)
    c = await _customer(db)
    p = await _product(db)
    q = await _quote(db, c, p, actor=user)
    assert q["valid_until"] is not None
    assert date.fromisoformat(q["valid_until"]) > date.today()


@pytest.mark.asyncio
async def test_an_expired_quotation_cannot_be_accepted(db):
    """The price was only promised until the date on it."""
    user = await _user(db)
    c = await _customer(db)
    p = await _product(db)
    q = await _quote(db, c, p, actor=user)
    qid = uuid.UUID(q["id"])
    await svc.set_status(db, quotation_id=qid, status="SENT", actor=user)
    await db.execute(text(
        "UPDATE quotations SET valid_until = CURRENT_DATE - 1 WHERE id = :i"),
        {"i": str(qid)})

    with pytest.raises(HTTPException) as e:
        await svc.set_status(db, quotation_id=qid, status="ACCEPTED",
                             actor=user)
    assert e.value.status_code == 400
    assert "expired" in str(e.value.detail)


@pytest.mark.asyncio
async def test_expiry_is_worked_out_on_reading_not_stored(db):
    """A nightly job that fails leaves dead quotes looking live."""
    user = await _user(db)
    c = await _customer(db)
    p = await _product(db)
    q = await _quote(db, c, p, actor=user)
    qid = uuid.UUID(q["id"])
    await svc.set_status(db, quotation_id=qid, status="SENT", actor=user)

    assert (await svc.get(db, quotation_id=qid))["is_expired"] is False
    await db.execute(text(
        "UPDATE quotations SET valid_until = CURRENT_DATE - 1 WHERE id = :i"),
        {"i": str(qid)})
    fresh = await svc.get(db, quotation_id=qid)
    assert fresh["is_expired"] is True
    assert fresh["status"] == "SENT", "the stored status is untouched"


# ---------------------------------------------------------------------------
# What may happen to a quote
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_draft_cannot_be_accepted_before_it_is_sent(db):
    user = await _user(db)
    c = await _customer(db)
    p = await _product(db)
    q = await _quote(db, c, p, actor=user)
    with pytest.raises(HTTPException) as e:
        await svc.set_status(db, quotation_id=uuid.UUID(q["id"]),
                             status="ACCEPTED", actor=user)
    assert e.value.status_code == 400
    assert "DRAFT" in str(e.value.detail)


@pytest.mark.asyncio
async def test_a_declined_quotation_is_final(db):
    user = await _user(db)
    c = await _customer(db)
    p = await _product(db)
    q = await _quote(db, c, p, actor=user)
    qid = uuid.UUID(q["id"])
    await svc.set_status(db, quotation_id=qid, status="SENT", actor=user)
    await svc.set_status(db, quotation_id=qid, status="DECLINED",
                         note="Bought elsewhere", actor=user)
    with pytest.raises(HTTPException) as e:
        await svc.set_status(db, quotation_id=qid, status="ACCEPTED",
                             actor=user)
    assert "final state" in str(e.value.detail)


@pytest.mark.asyncio
async def test_declining_requires_a_reason(db):
    user = await _user(db)
    c = await _customer(db)
    p = await _product(db)
    q = await _quote(db, c, p, actor=user)
    qid = uuid.UUID(q["id"])
    await svc.set_status(db, quotation_id=qid, status="SENT", actor=user)
    with pytest.raises(HTTPException) as e:
        await svc.set_status(db, quotation_id=qid, status="DECLINED",
                             actor=user)
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_every_change_is_recorded_with_who_made_it(db):
    user = await _user(db)
    c = await _customer(db)
    p = await _product(db)
    q = await _quote(db, c, p, actor=user)
    qid = uuid.UUID(q["id"])
    await svc.set_status(db, quotation_id=qid, status="SENT", actor=user)

    hist = (await svc.get(db, quotation_id=qid))["history"]
    assert [h["to"] for h in hist] == ["SENT", "DRAFT"]
    assert hist[0]["actor"] == "Sales Officer"


@pytest.mark.asyncio
async def test_the_history_cannot_be_rewritten(db):
    user = await _user(db)
    c = await _customer(db)
    p = await _product(db)
    await _quote(db, c, p, actor=user)
    with pytest.raises(Exception) as e:
        await db.execute(text(
            "UPDATE quotation_events SET to_status = 'ACCEPTED'"))
    assert "append-only" in str(e.value).lower()


# ---------------------------------------------------------------------------
# Discounts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_discount_within_the_threshold_needs_nobody(db):
    user = await _user(db)
    c = await _customer(db)
    p = await _product(db, retail=10000)
    q = await _quote(db, c, p, qty=1, discount_percent=Decimal("5"),
                     actor=user)
    assert q["total_amount"] == 9500.0


@pytest.mark.asyncio
async def test_a_large_discount_is_refused_to_a_salesperson(db):
    user = await _user(db)
    c = await _customer(db)
    p = await _product(db)
    with pytest.raises(HTTPException) as e:
        await _quote(db, c, p, discount_percent=Decimal("40"), actor=user)
    assert e.value.status_code == 403
    assert "administrator" in str(e.value.detail)


@pytest.mark.asyncio
async def test_an_administrator_may_give_a_large_discount_and_is_named(db):
    admin = await _user(db, role="admin")
    c = await _customer(db)
    p = await _product(db, retail=10000)
    q = await _quote(db, c, p, qty=1, discount_percent=Decimal("40"),
                     actor=admin)
    assert q["total_amount"] == 6000.0
    full = await svc.get(db, quotation_id=uuid.UUID(q["id"]))
    assert full["discount_approved_by"] == "Admin One"


@pytest.mark.asyncio
async def test_the_threshold_is_configurable(db):
    user = await _user(db)
    c = await _customer(db)
    p = await _product(db)
    await db.execute(text(
        "UPDATE app_settings SET value = '50' "
        "WHERE key = 'QUOTATION_MAX_DISCOUNT_PERCENT'"))
    q = await _quote(db, c, p, discount_percent=Decimal("40"), actor=user)
    assert q["id"]


# ---------------------------------------------------------------------------
# Becoming an order
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_accepted_quotation_becomes_a_pending_order(db):
    user = await _user(db)
    c = await _customer(db)
    p = await _product(db, retail=10000)
    q = await _quote(db, c, p, qty=3, actor=user)
    qid = uuid.UUID(q["id"])
    await svc.set_status(db, quotation_id=qid, status="SENT", actor=user)
    await svc.set_status(db, quotation_id=qid, status="ACCEPTED", actor=user)

    result = await svc.convert(db, quotation_id=qid, actor=user)
    assert result["lines"] == 1
    assert result["total_amount"] == 30000.0

    order = (await db.execute(text(
        "SELECT status, total_amount, sales_channel, quotation_id "
        "FROM sales_orders WHERE order_number = :n"),
        {"n": result["order_number"]})).mappings().first()
    assert order["status"] == "pending", "it joins the normal confirmation flow"
    assert order["sales_channel"] == "QUOTATION"
    assert str(order["quotation_id"]) == str(qid)


@pytest.mark.asyncio
async def test_a_quotation_converts_only_once(db):
    """Converting twice means two orders for one agreement."""
    user = await _user(db)
    c = await _customer(db)
    p = await _product(db)
    q = await _quote(db, c, p, actor=user)
    qid = uuid.UUID(q["id"])
    await svc.set_status(db, quotation_id=qid, status="SENT", actor=user)
    await svc.set_status(db, quotation_id=qid, status="ACCEPTED", actor=user)
    await svc.convert(db, quotation_id=qid, actor=user)

    with pytest.raises(HTTPException) as e:
        await svc.convert(db, quotation_id=qid, actor=user)
    assert e.value.status_code == 400
    assert "CONVERTED" in str(e.value.detail)


@pytest.mark.asyncio
async def test_an_unaccepted_quotation_cannot_become_an_order(db):
    user = await _user(db)
    c = await _customer(db)
    p = await _product(db)
    q = await _quote(db, c, p, actor=user)
    qid = uuid.UUID(q["id"])
    await svc.set_status(db, quotation_id=qid, status="SENT", actor=user)
    with pytest.raises(HTTPException) as e:
        await svc.convert(db, quotation_id=qid, actor=user)
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_the_database_refuses_converted_without_an_order(db):
    user = await _user(db)
    c = await _customer(db)
    p = await _product(db)
    q = await _quote(db, c, p, actor=user)
    with pytest.raises(Exception) as e:
        await db.execute(text(
            "UPDATE quotations SET status = 'CONVERTED' WHERE id = :i"),
            {"i": q["id"]})
    assert "ck_quotation_converted" in str(e.value)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_merged_customer_cannot_be_quoted(db):
    user = await _user(db)
    keep = await _customer(db, "Keep")
    gone = await _customer(db, "Gone")
    p = await _product(db)
    await db.execute(text(
        "UPDATE customers SET merged_into_id = :k WHERE id = :g"),
        {"k": str(keep), "g": str(gone)})
    with pytest.raises(HTTPException) as e:
        await _quote(db, gone, p, actor=user)
    assert e.value.status_code == 400
    assert "merged" in str(e.value.detail)


@pytest.mark.asyncio
async def test_the_listing_totals_only_live_quotations(db):
    user = await _user(db)
    c = await _customer(db)
    p = await _product(db, retail=10000)

    live = await _quote(db, c, p, qty=1, actor=user)
    await svc.set_status(db, quotation_id=uuid.UUID(live["id"]),
                         status="SENT", actor=user)

    dead = await _quote(db, c, p, qty=5, actor=user)
    dqid = uuid.UUID(dead["id"])
    await svc.set_status(db, quotation_id=dqid, status="SENT", actor=user)
    await db.execute(text(
        "UPDATE quotations SET valid_until = CURRENT_DATE - 1 WHERE id = :i"),
        {"i": str(dqid)})

    result = await svc.listing(db)
    assert result["open_value"] == 10000.0, "an expired quote is not pipeline"


@pytest.mark.asyncio
async def test_a_quotation_needs_at_least_one_line(db):
    user = await _user(db)
    c = await _customer(db)
    with pytest.raises(HTTPException) as e:
        await svc.create(db, customer_id=c, items=[], actor=user)
    assert e.value.status_code == 400
