"""What a customer owes, and what the invoice prints.

The bug these tests lock down: the sales screen summed each unpaid order's FULL
total, so a customer who had paid ₦99,000 of a ₦100,000 order was shown -- and
invoiced -- as still owing the whole ₦100,000. It also read `payment_status`
(a denormalised flag) rather than the payments, and ignored legacy debts.

The other half is arithmetic that must not be got wrong on a document handed to
a customer: the order being invoiced must not appear in its own "previous
balance", and total payable must equal this invoice plus prior debt exactly.

Requires real PostgreSQL:

    export TEST_DATABASE_URL='postgresql+asyncpg://postgres:pw@localhost:5432/astro_test'
    cd backend && pytest tests/test_customer_debt.py -v
"""
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.services.customer_debt import (
    invoice_statement, outstanding_for_customer)
from app.services.ledger import money

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")

SCHEMA = """
DROP TABLE IF EXISTS legacy_debt_payments CASCADE;
DROP TABLE IF EXISTS legacy_debts CASCADE;
DROP TABLE IF EXISTS payments CASCADE;
DROP TABLE IF EXISTS invoice_lines CASCADE;
DROP TABLE IF EXISTS invoices CASCADE;
DROP TABLE IF EXISTS sales_order_lines CASCADE;
DROP TABLE IF EXISTS sales_orders CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS customers CASCADE;

CREATE TABLE customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_code VARCHAR(32),
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255),
    phone VARCHAR(50),
    address TEXT,
    credit_limit NUMERIC(12,2) DEFAULT 0
);
CREATE TABLE products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sku VARCHAR(64) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL
);
CREATE TABLE sales_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_number VARCHAR(64) UNIQUE NOT NULL,
    customer_id UUID NOT NULL REFERENCES customers(id),
    status VARCHAR(32) NOT NULL DEFAULT 'confirmed',
    payment_status VARCHAR(32) NOT NULL DEFAULT 'unpaid',
    payment_date TIMESTAMPTZ,
    order_date TIMESTAMPTZ DEFAULT NOW(),
    required_date TIMESTAMPTZ,
    total_amount NUMERIC(18,2) DEFAULT 0,
    notes TEXT
);
CREATE TABLE sales_order_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sales_order_id UUID NOT NULL REFERENCES sales_orders(id),
    product_id UUID REFERENCES products(id),
    unit VARCHAR(50),
    quantity NUMERIC(18,6) NOT NULL,
    unit_price NUMERIC(18,6) NOT NULL,
    line_total NUMERIC(18,2) NOT NULL
);
CREATE TABLE invoices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_number VARCHAR(64) UNIQUE NOT NULL,
    customer_id UUID REFERENCES customers(id),
    sales_order_id UUID REFERENCES sales_orders(id),
    total_amount NUMERIC(18,2) DEFAULT 0,
    paid_amount NUMERIC(18,2) DEFAULT 0,
    status VARCHAR(32) DEFAULT 'pending'
);
CREATE TABLE invoice_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id UUID NOT NULL REFERENCES invoices(id),
    product_id UUID REFERENCES products(id),
    quantity NUMERIC(18,6) NOT NULL,
    unit_price NUMERIC(18,6) NOT NULL,
    line_total NUMERIC(18,2) NOT NULL
);
CREATE TABLE payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id UUID NOT NULL REFERENCES invoices(id),
    payment_method VARCHAR(50) NOT NULL,
    amount NUMERIC(18,2) NOT NULL,
    payment_date TIMESTAMPTZ DEFAULT NOW(),
    reference VARCHAR(255)
);
CREATE TABLE legacy_debts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    debt_number VARCHAR(64) UNIQUE NOT NULL,
    customer_id UUID NOT NULL REFERENCES customers(id),
    description TEXT NOT NULL,
    original_amount NUMERIC(18,2) NOT NULL,
    paid_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    debt_date DATE NOT NULL,
    due_date DATE,
    notes TEXT
);
"""


@pytest_asyncio.fixture
async def engine():
    # NullPool: every test rebuilds the schema with DROP TABLE, which needs an
    # ACCESS EXCLUSIVE lock. A pooled connection surviving the previous test
    # still holds ACCESS SHARE on those tables, and the two wait on each other.
    #
    # Terminating stray backends was the first attempt and made it worse -- it
    # also kills the connections this engine's own pool is holding, so a test
    # fails with "connection was closed in the middle of operation" depending on
    # which fixture ran last. Not pooling at all removes the race rather than
    # racing to clean up after it.
    eng = create_async_engine(TEST_DB, future=True, poolclass=NullPool)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        for stmt in SCHEMA.strip().split(";"):
            if stmt.strip():
                await conn.execute(text(stmt))
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine):
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
        # Release any transaction the test left open, so the next test's DDL
        # is not blocked by it.
        await s.rollback()


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------

async def make_customer(session, name="Enugu Teaching Hospital", credit_limit=0):
    row = (await session.execute(text("""
        INSERT INTO customers (id, customer_code, name, credit_limit)
        VALUES (gen_random_uuid(), :c, :n, :cl) RETURNING id
    """), {"c": f"C-{uuid.uuid4().hex[:6].upper()}", "n": name,
           "cl": str(credit_limit)})).first()
    await session.commit()
    return row.id


async def make_order(session, customer_id, total, *, days_ago=0,
                     paid=None, status='confirmed', payment_status='unpaid',
                     with_invoice=True, lines=None):
    """Create an order, optionally its invoice and a payment against it."""
    n = uuid.uuid4().hex[:8].upper()
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    order = (await session.execute(text("""
        INSERT INTO sales_orders (id, order_number, customer_id, status,
                                  payment_status, order_date, total_amount)
        VALUES (gen_random_uuid(), :num, :c, :st, :ps, :d, :t) RETURNING id
    """), {"num": f"SO-{n}", "c": str(customer_id), "st": status,
           "ps": payment_status, "d": when, "t": str(total)})).first()

    for product_name, qty, price in (lines or []):
        prod = (await session.execute(text("""
            INSERT INTO products (id, sku, name)
            VALUES (gen_random_uuid(), :s, :n) RETURNING id
        """), {"s": f"SKU-{uuid.uuid4().hex[:8]}", "n": product_name})).first()
        await session.execute(text("""
            INSERT INTO sales_order_lines (id, sales_order_id, product_id,
                                           quantity, unit_price, line_total)
            VALUES (gen_random_uuid(), :o, :p, :q, :u, :lt)
        """), {"o": str(order.id), "p": str(prod.id), "q": str(qty),
               "u": str(price), "lt": str(money(Decimal(str(qty)) * Decimal(str(price))))})

    if with_invoice:
        inv = (await session.execute(text("""
            INSERT INTO invoices (id, invoice_number, customer_id,
                                  sales_order_id, total_amount)
            VALUES (gen_random_uuid(), :num, :c, :o, :t) RETURNING id
        """), {"num": f"INV-{n}", "c": str(customer_id), "o": str(order.id),
               "t": str(total)})).first()
        if paid is not None:
            await session.execute(text("""
                INSERT INTO payments (id, invoice_id, payment_method, amount)
                VALUES (gen_random_uuid(), :i, 'cash', :a)
            """), {"i": str(inv.id), "a": str(paid)})

    await session.commit()
    return order.id


async def make_legacy_debt(session, customer_id, original, paid=0,
                           days_ago=120, status='pending',
                           description="Carried over from ledger book"):
    await session.execute(text("""
        INSERT INTO legacy_debts (id, debt_number, customer_id, description,
                                  original_amount, paid_amount, status, debt_date)
        VALUES (gen_random_uuid(), :num, :c, :d, :o, :p, :st, :dt)
    """), {"num": f"LD-{uuid.uuid4().hex[:8].upper()}", "c": str(customer_id),
           "d": description, "o": str(original), "p": str(paid),
           "st": status, "dt": date.today() - timedelta(days=days_ago)})
    await session.commit()


# ---------------------------------------------------------------------------
# THE regression: a part-paid order owes the remainder, not the whole thing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_part_paid_order_owes_only_the_remainder(session):
    """₦99,000 paid on a ₦100,000 order leaves ₦1,000 owing, not ₦100,000."""
    cid = await make_customer(session)
    await make_order(session, cid, "100000.00", paid="99000.00",
                     payment_status='partial')

    debt = await outstanding_for_customer(session, customer_id=cid)

    assert debt["total_outstanding"] == Decimal("1000.00")
    assert debt["count"] == 1
    item = debt["items"][0]
    assert item["original_amount"] == 100000.00
    assert item["paid_amount"] == 99000.00
    assert item["balance"] == 1000.00
    assert item["status"] == "partial"


@pytest.mark.asyncio
async def test_fully_paid_order_is_not_a_debt(session):
    cid = await make_customer(session)
    await make_order(session, cid, "50000.00", paid="50000.00",
                     payment_status='paid')
    debt = await outstanding_for_customer(session, customer_id=cid)
    assert debt["total_outstanding"] == Decimal("0.00")
    assert debt["count"] == 0


@pytest.mark.asyncio
async def test_stale_payment_status_flag_is_ignored(session):
    """The flag says paid; the payments say otherwise. The payments win.

    `payment_status` is a cache. A row whose flag was never resynced would have
    been dropped from the old flag-based query entirely -- silently forgiving a
    real debt.
    """
    cid = await make_customer(session)
    await make_order(session, cid, "80000.00", paid="20000.00",
                     payment_status='paid')       # <- the lie
    debt = await outstanding_for_customer(session, customer_id=cid)
    assert debt["total_outstanding"] == Decimal("60000.00")


@pytest.mark.asyncio
async def test_order_with_no_invoice_still_counts(session):
    """Invoices are created lazily, so most unpaid orders have no invoice row.

    A purely invoice-based query would report this customer as owing nothing.
    """
    cid = await make_customer(session)
    await make_order(session, cid, "35000.00", with_invoice=False)
    debt = await outstanding_for_customer(session, customer_id=cid)
    assert debt["total_outstanding"] == Decimal("35000.00")


@pytest.mark.asyncio
async def test_cancelled_order_is_not_a_debt(session):
    cid = await make_customer(session)
    await make_order(session, cid, "70000.00", status='cancelled')
    debt = await outstanding_for_customer(session, customer_id=cid)
    assert debt["count"] == 0


# ---------------------------------------------------------------------------
# legacy debts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_legacy_debt_is_included(session):
    cid = await make_customer(session)
    await make_order(session, cid, "10000.00")
    await make_legacy_debt(session, cid, "250000.00", paid="50000.00")

    debt = await outstanding_for_customer(session, customer_id=cid)

    assert debt["orders_outstanding"] == Decimal("10000.00")
    assert debt["legacy_outstanding"] == Decimal("200000.00")
    assert debt["total_outstanding"] == Decimal("210000.00")
    assert debt["order_count"] == 1
    assert debt["legacy_count"] == 1
    kinds = {i["kind"] for i in debt["items"]}
    assert kinds == {"ORDER", "LEGACY"}


@pytest.mark.asyncio
async def test_cancelled_legacy_debt_excluded(session):
    cid = await make_customer(session)
    await make_legacy_debt(session, cid, "99000.00", status='cancelled')
    debt = await outstanding_for_customer(session, customer_id=cid)
    assert debt["total_outstanding"] == Decimal("0.00")


@pytest.mark.asyncio
async def test_missing_legacy_table_is_not_an_error(session):
    """The legacy table is created lazily by its own screen; it may not exist.

    Under-reporting a debt because a table is absent is exactly the failure the
    probe exists to prevent, so this must return the order debt, not raise.
    """
    cid = await make_customer(session)
    await make_order(session, cid, "5000.00")
    await session.execute(text("DROP TABLE IF EXISTS legacy_debt_payments CASCADE"))
    await session.execute(text("DROP TABLE IF EXISTS legacy_debts CASCADE"))
    await session.commit()

    debt = await outstanding_for_customer(session, customer_id=cid)
    assert debt["total_outstanding"] == Decimal("5000.00")
    assert debt["legacy_outstanding"] == Decimal("0.00")


# ---------------------------------------------------------------------------
# the invoice statement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invoice_excludes_itself_from_previous_balance(session):
    """The order being invoiced must not be billed twice on its own invoice."""
    cid = await make_customer(session)
    old = await make_order(session, cid, "40000.00", days_ago=45)
    new = await make_order(session, cid, "150000.00",
                           lines=[("Hera Wound Gel", 10, 8000),
                                  ("Honey Gauze", 14, 5000)])

    stmt = await invoice_statement(session, order_id=new)

    assert stmt["invoice_total"] == Decimal("150000.00")
    assert stmt["invoice_due"] == Decimal("150000.00")
    assert stmt["previous_outstanding"] == Decimal("40000.00")
    assert stmt["total_payable"] == Decimal("190000.00")
    refs = [i["reference"] for i in stmt["previous_items"]]
    assert len(refs) == 1, "the invoiced order appeared in its own statement"
    assert stmt["order_number"] not in refs
    assert old is not None


@pytest.mark.asyncio
async def test_total_payable_is_invoice_plus_prior_exactly(session):
    """Sums a customer will check by hand, on amounts that do not divide well."""
    cid = await make_customer(session)
    await make_order(session, cid, "33333.33", days_ago=10)
    await make_order(session, cid, "66666.67", days_ago=5, paid="0.01")
    new = await make_order(session, cid, "0.03")

    stmt = await invoice_statement(session, order_id=new)

    assert stmt["previous_outstanding"] == Decimal("99999.99")
    assert stmt["total_payable"] == Decimal("100000.02")
    assert stmt["total_payable"] == stmt["invoice_due"] + stmt["previous_outstanding"]


@pytest.mark.asyncio
async def test_reprinted_invoice_shows_remaining_not_face_value(session):
    """A part-paid order re-printed must ask for what is left, not the total."""
    cid = await make_customer(session)
    order = await make_order(session, cid, "200000.00", paid="120000.00",
                             payment_status='partial')

    stmt = await invoice_statement(session, order_id=order)

    assert stmt["invoice_total"] == Decimal("200000.00")
    assert stmt["invoice_paid"] == Decimal("120000.00")
    assert stmt["invoice_due"] == Decimal("80000.00")
    assert stmt["total_payable"] == Decimal("80000.00")


@pytest.mark.asyncio
async def test_clean_customer_has_no_previous_balance(session):
    """Nothing owed means the statement section is suppressed on the invoice."""
    cid = await make_customer(session)
    new = await make_order(session, cid, "25000.00")
    stmt = await invoice_statement(session, order_id=new)
    assert stmt["previous_outstanding"] == Decimal("0.00")
    assert stmt["previous_items"] == []
    assert stmt["total_payable"] == Decimal("25000.00")


@pytest.mark.asyncio
async def test_other_customers_debts_are_not_borrowed(session):
    cid_a = await make_customer(session, "Hospital A")
    cid_b = await make_customer(session, "Hospital B")
    await make_order(session, cid_b, "500000.00", days_ago=90)
    new = await make_order(session, cid_a, "1000.00")

    stmt = await invoice_statement(session, order_id=new)
    assert stmt["previous_outstanding"] == Decimal("0.00")


# ---------------------------------------------------------------------------
# aging and credit limit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_aging_buckets_split_the_balance(session):
    cid = await make_customer(session)
    await make_order(session, cid, "1000.00", days_ago=0)
    await make_order(session, cid, "2000.00", days_ago=15)
    await make_order(session, cid, "4000.00", days_ago=45)
    await make_order(session, cid, "8000.00", days_ago=75)
    await make_order(session, cid, "16000.00", days_ago=200)

    debt = await outstanding_for_customer(session, customer_id=cid)

    assert debt["aging"]["current"] == Decimal("1000.00")
    assert debt["aging"]["days_1_30"] == Decimal("2000.00")
    assert debt["aging"]["days_31_60"] == Decimal("4000.00")
    assert debt["aging"]["days_61_90"] == Decimal("8000.00")
    assert debt["aging"]["days_over_90"] == Decimal("16000.00")
    assert sum(debt["aging"].values()) == debt["total_outstanding"]
    assert debt["oldest_days"] == 200


@pytest.mark.asyncio
async def test_credit_limit_breach_flagged_only_when_a_limit_is_set(session):
    """A 0 limit is this schema's default and means 'not recorded'."""
    no_limit = await make_customer(session, "No Limit", credit_limit=0)
    await make_order(session, no_limit, "900000.00")
    assert (await outstanding_for_customer(
        session, customer_id=no_limit))["credit_limit_exceeded"] is False

    limited = await make_customer(session, "Limited", credit_limit=100000)
    await make_order(session, limited, "150000.00")
    assert (await outstanding_for_customer(
        session, customer_id=limited))["credit_limit_exceeded"] is True


@pytest.mark.asyncio
async def test_statement_orders_oldest_first(session):
    """A statement is read to find what has been outstanding longest."""
    cid = await make_customer(session)
    await make_order(session, cid, "100.00", days_ago=5)
    await make_order(session, cid, "200.00", days_ago=90)
    await make_order(session, cid, "300.00", days_ago=30)

    items = (await outstanding_for_customer(session, customer_id=cid))["items"]
    ages = [i["age_days"] for i in items]
    assert ages == sorted(ages, reverse=True), f"not oldest-first: {ages}"


@pytest.mark.asyncio
async def test_duplicate_invoices_do_not_double_count_an_order(session):
    """One order, two live invoices -- the balance must still be counted once.

    `invoices.sales_order_id` has no unique constraint, so nothing at the
    database level prevents a second invoice row for the same order (a race in
    ensure_invoice_for_order, or historical data). Joining to invoices to read
    the invoice number would return the order twice and DOUBLE what the customer
    is told they owe, on a document handed to them.
    """
    cid = await make_customer(session)
    order = await make_order(session, cid, "80000.00")

    # A second, non-cancelled invoice against the same order.
    await session.execute(text("""
        INSERT INTO invoices (id, invoice_number, customer_id, sales_order_id,
                              total_amount, status)
        VALUES (gen_random_uuid(), :num, :c, :o, 80000.00, 'pending')
    """), {"num": f"INV-DUP-{uuid.uuid4().hex[:6].upper()}",
           "c": str(cid), "o": str(order)})
    await session.commit()

    debt = await outstanding_for_customer(session, customer_id=cid)

    assert debt["count"] == 1, f"order listed {debt['count']} times"
    assert debt["total_outstanding"] == Decimal("80000.00")
