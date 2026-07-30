"""MAPD: prove one payment reaches the right accounts, to the naira.

The claims worth testing are the ones a finance manager would refuse to take
on trust:

  * the split sums to the payment EXACTLY, including on amounts that do not
    divide evenly;
  * a mixed-product invoice sends each product's money to that product's
    account, not a pooled total someone reconciles by hand later;
  * instalments converge on the same split as a single payment;
  * a retried distribution does not pay a destination account twice;
  * a suspended destination pauses the whole settlement rather than allocating
    part of it -- and the PAYMENT still stands;
  * the record of where money went cannot be edited afterwards.

Requires real PostgreSQL (triggers, partial unique indexes, row locking):

    export TEST_DATABASE_URL='postgresql+asyncpg://postgres:pw@localhost:5432/astro_test'
    cd backend && pytest tests/test_settlement.py -v
"""
import importlib.util
import os
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.services.ledger import money
from app.services.receivables import record_payment
from app.services.settlement import (
    apportion, build_plan, distribute_payment, refund_settlement,
    retry_failed_settlements, settlement_health)

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")

# The base tables MAPD hangs off. Kept minimal and explicit rather than
# importing the whole application schema: a test that needs 60 tables to
# demonstrate a payment split is testing the wrong thing.
SCHEMA = """
DROP TABLE IF EXISTS mapd_audit_logs CASCADE;
DROP TABLE IF EXISTS mapd_refunds CASCADE;
DROP TABLE IF EXISTS settlement_details CASCADE;
DROP TABLE IF EXISTS settlements CASCADE;
DROP TABLE IF EXISTS settlement_rule_splits CASCADE;
DROP TABLE IF EXISTS settlement_rules CASCADE;
DROP TABLE IF EXISTS product_accounts CASCADE;
DROP TABLE IF EXISTS financial_accounts CASCADE;
DROP TABLE IF EXISTS revenue_centers CASCADE;
DROP TABLE IF EXISTS business_units CASCADE;
DROP TABLE IF EXISTS payment_methods CASCADE;
DROP TABLE IF EXISTS payments CASCADE;
DROP TABLE IF EXISTS invoice_lines CASCADE;
DROP TABLE IF EXISTS invoices CASCADE;
DROP TABLE IF EXISTS sales_order_lines CASCADE;
DROP TABLE IF EXISTS sales_orders CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS customers CASCADE;

CREATE TABLE customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL
);
CREATE TABLE products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sku VARCHAR(64) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL
);
CREATE TABLE sales_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_number VARCHAR(64) UNIQUE NOT NULL,
    customer_id UUID REFERENCES customers(id),
    payment_status VARCHAR(32) NOT NULL DEFAULT 'unpaid',
    payment_date TIMESTAMPTZ,
    order_date TIMESTAMPTZ DEFAULT NOW(),
    total_amount NUMERIC(18,2) DEFAULT 0
);
CREATE TABLE sales_order_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sales_order_id UUID NOT NULL REFERENCES sales_orders(id),
    product_id UUID NOT NULL REFERENCES products(id),
    quantity NUMERIC(18,6) NOT NULL,
    unit_price NUMERIC(18,6) NOT NULL,
    line_total NUMERIC(18,2) NOT NULL
);
CREATE TABLE invoices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_number VARCHAR(64) UNIQUE NOT NULL,
    customer_id UUID REFERENCES customers(id),
    sales_order_id UUID REFERENCES sales_orders(id),
    invoice_date TIMESTAMPTZ DEFAULT NOW(),
    due_date TIMESTAMPTZ,
    total_amount NUMERIC(18,2) DEFAULT 0,
    paid_amount NUMERIC(18,2) DEFAULT 0,
    status VARCHAR(32) DEFAULT 'pending',
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE invoice_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id UUID NOT NULL REFERENCES invoices(id),
    product_id UUID NOT NULL REFERENCES products(id),
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
    reference VARCHAR(255),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
"""

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _apply_migration(conn, filename: str):
    """Run a real migration module against the test database.

    The migrations ARE the schema definition -- re-declaring the MAPD tables
    by hand here would let a constraint drift out of the tests silently, which
    is exactly the kind of gap these tests exist to close.
    """
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = VERSIONS / filename
    spec = importlib.util.spec_from_file_location(f"mig_{filename[:12]}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        mod.upgrade()


@pytest_asyncio.fixture
async def engine():
    seng = create_engine(TEST_DB.replace("+asyncpg", ""), future=True)
    with seng.connect() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        for t in ("gl_journal_lines", "gl_journal_entries", "gl_periods",
                  "gl_accounts"):
            c.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        for stmt in SCHEMA.strip().split(";"):
            if stmt.strip():
                c.execute(text(stmt))
        c.commit()
        _apply_migration(c, "m2345678901l_general_ledger.py")
        c.commit()
        _apply_migration(c, "s8901234567r_mapd_settlement.py")
        c.commit()
    seng.dispose()
    eng = create_async_engine(TEST_DB, future=True)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine):
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    monkeypatch.setenv("MAPD_ENABLED", "true")
    monkeypatch.delenv("MAPD_STRICT", raising=False)
    monkeypatch.setenv("ACCOUNTING_POSTING_ENABLED", "true")
    monkeypatch.delenv("ACCOUNTING_CUTOVER_DATE", raising=False)


# ---------------------------------------------------------------------------
# fixtures / builders
# ---------------------------------------------------------------------------

async def make_account(session, code, name, gl="1200", kind="BANK",
                       status="ACTIVE", contra=None):
    row = (await session.execute(text("""
        INSERT INTO financial_accounts
            (id, code, name, account_kind, gl_account_code,
             contra_gl_account_code, status)
        VALUES (gen_random_uuid(), :c, :n, :k, :gl, :contra, :st)
     RETURNING id
    """), {"c": code, "n": name, "k": kind, "gl": gl, "contra": contra,
           "st": status})).first()
    return row.id


async def make_product(session, sku, name):
    row = (await session.execute(text("""
        INSERT INTO products (id, sku, name) VALUES (gen_random_uuid(), :s, :n)
     RETURNING id
    """), {"s": sku, "n": name})).first()
    return row.id


async def map_product(session, product_id, account_id=None, business_unit_id=None):
    await session.execute(text("""
        INSERT INTO product_accounts
            (id, product_id, business_unit_id, default_financial_account_id)
        VALUES (gen_random_uuid(), :p, :bu, :a)
        ON CONFLICT (product_id) DO UPDATE
           SET default_financial_account_id = EXCLUDED.default_financial_account_id,
               business_unit_id = EXCLUDED.business_unit_id
    """), {"p": str(product_id),
           "bu": str(business_unit_id) if business_unit_id else None,
           "a": str(account_id) if account_id else None})


async def make_rule(session, code, product_id, splits, scope='PRODUCT',
                    business_unit_id=None):
    """splits: list of (account_id, allocation_type, percentage|None, residual)."""
    rule = (await session.execute(text("""
        INSERT INTO settlement_rules
            (id, code, name, scope, product_id, business_unit_id, basis,
             priority, effective_from, is_active)
        VALUES (gen_random_uuid(), :c, :c, :scope, :p, :bu, 'PERCENTAGE', 100,
                CURRENT_DATE - 1, TRUE)
     RETURNING id
    """), {"c": code, "scope": scope,
           "p": str(product_id) if product_id else None,
           "bu": str(business_unit_id) if business_unit_id else None})).first()
    for i, (acct, kind, pct, residual) in enumerate(splits):
        await session.execute(text("""
            INSERT INTO settlement_rule_splits
                (id, rule_id, financial_account_id, allocation_type,
                 percentage, is_residual, sort_order)
            VALUES (gen_random_uuid(), :r, :a, :k, :pct, :res, :i)
        """), {"r": str(rule.id), "a": str(acct), "k": kind,
               "pct": pct, "res": residual, "i": i})
    return rule.id


async def make_invoice(session, lines, order_number=None):
    """lines: list of (product_id, quantity, unit_price)."""
    n = uuid.uuid4().hex[:8].upper()
    cust = (await session.execute(text(
        "INSERT INTO customers (id, name) VALUES (gen_random_uuid(), :n) "
        "RETURNING id"), {"n": "Test Hospital"})).first()

    total = sum(money(Decimal(str(q)) * Decimal(str(p))) for _, q, p in lines)
    order = (await session.execute(text("""
        INSERT INTO sales_orders (id, order_number, customer_id, total_amount)
        VALUES (gen_random_uuid(), :num, :c, :t) RETURNING id
    """), {"num": f"SO-{n}", "c": str(cust.id), "t": str(total)})).first()

    inv = (await session.execute(text("""
        INSERT INTO invoices (id, invoice_number, customer_id, sales_order_id,
                              total_amount)
        VALUES (gen_random_uuid(), :num, :c, :o, :t) RETURNING id
    """), {"num": f"INV-{n}", "c": str(cust.id), "o": str(order.id),
           "t": str(total)})).first()

    for pid, qty, price in lines:
        line_total = money(Decimal(str(qty)) * Decimal(str(price)))
        for table, parent in (("sales_order_lines", order.id),
                              ("invoice_lines", inv.id)):
            key = ("sales_order_id" if table == "sales_order_lines"
                   else "invoice_id")
            await session.execute(text(f"""
                INSERT INTO {table} (id, {key}, product_id, quantity,
                                     unit_price, line_total)
                VALUES (gen_random_uuid(), :p, :prod, :q, :u, :lt)
            """), {"p": str(parent), "prod": str(pid), "q": str(qty),
                   "u": str(price), "lt": str(line_total)})
    await session.commit()
    return inv.id, order.id, money(total)


async def allocations_by_account(session, settlement_id):
    rows = (await session.execute(text("""
        SELECT fa.code, d.allocation_type, SUM(d.amount) AS amount
          FROM settlement_details d
          JOIN financial_accounts fa ON fa.id = d.financial_account_id
         WHERE d.settlement_id = :s
         GROUP BY fa.code, d.allocation_type
    """), {"s": str(settlement_id)})).fetchall()
    return {(r.code, r.allocation_type): money(r.amount) for r in rows}


# ---------------------------------------------------------------------------
# apportionment -- no database needed
# ---------------------------------------------------------------------------

def test_apportion_sums_exactly_on_indivisible_amounts():
    """The cent that rounding loses has to come from somebody's account."""
    for total in ("100.00", "0.01", "33.33", "999999.99", "1000000.01"):
        for weights in ([1, 1, 1], [70, 20, 10], [1, 2, 3, 5, 8],
                        [Decimal("0.01")] * 7):
            parts = apportion(total, weights)
            assert sum(parts) == money(total), (
                f"{total} split {weights} summed to {sum(parts)}")


def test_apportion_matches_the_brief_example():
    """The worked example from the specification, to the naira."""
    parts = apportion("330000.00", [160000, 50000, 60000, 60000])
    assert parts == [Decimal("160000.00"), Decimal("50000.00"),
                     Decimal("60000.00"), Decimal("60000.00")]


def test_apportion_is_deterministic():
    """A split that varies run to run cannot be reconciled."""
    runs = {tuple(apportion("100.00", [1, 1, 1])) for _ in range(20)}
    assert len(runs) == 1


def test_apportion_never_exceeds_a_weight_when_total_equals_sum():
    parts = apportion("60.00", [10, 20, 30])
    assert parts == [Decimal("10.00"), Decimal("20.00"), Decimal("30.00")]


def test_apportion_of_zero_is_all_zeros():
    assert apportion("0.00", [1, 2, 3]) == [Decimal("0.00")] * 3


# ---------------------------------------------------------------------------
# the headline case: one payment, four products, four accounts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mixed_invoice_reaches_each_products_account(session):
    """The specification's worked example, end to end.

    ₦330,000 paid once against four products belonging to four business units
    must arrive as ₦160,000 / ₦50,000 / ₦60,000 / ₦60,000 in four accounts,
    with no manual transfer and nothing left in a pooled balance.
    """
    accounts = {}
    products = []
    spec = [
        ("HERA", "Hera Account", "P-HERA", "Hera Wound Gel", 20, 8000),
        ("HONEY", "Honey Account", "P-HONEY", "Honey Gauze", 10, 5000),
        ("CLEX", "Wound Clex Account", "P-CLEX", "Wound Clex Solution", 15, 4000),
        ("PACK", "Dressing Pack Account", "P-PACK", "Sterile Dressing Pack", 8, 7500),
    ]
    for code, name, sku, pname, qty, price in spec:
        accounts[code] = await make_account(session, code, name)
        pid = await make_product(session, sku, pname)
        await map_product(session, pid, accounts[code])
        products.append((pid, qty, price))

    invoice_id, _, total = await make_invoice(session, products)
    assert total == Decimal("330000.00")

    result = await record_payment(
        session, invoice_id=invoice_id, amount=total,
        payment_method="bank_transfer", reference="ONE-PAYMENT")
    await session.commit()

    settlement = result["settlement"]
    assert settlement["status"] == "COMPLETED", settlement.get("reason")
    assert settlement["allocated_amount"] == Decimal("330000.00")

    got = await allocations_by_account(session, settlement["settlement_id"])
    assert got[("HERA", "CASH")] == Decimal("160000.00")
    assert got[("HONEY", "CASH")] == Decimal("50000.00")
    assert got[("CLEX", "CASH")] == Decimal("60000.00")
    assert got[("PACK", "CASH")] == Decimal("60000.00")
    assert sum(got.values()) == total


@pytest.mark.asyncio
async def test_percentage_rule_splits_within_a_product(session):
    """Hera revenue: 70% manufacturing, 20% sales, 10% marketing."""
    mfg = await make_account(session, "MFG", "Manufacturing")
    sales = await make_account(session, "SALES", "Sales Division")
    mkt = await make_account(session, "MKT", "Marketing")
    pid = await make_product(session, "P-HERA", "Hera Wound Gel")
    await map_product(session, pid, mfg)
    await make_rule(session, "HERA-SPLIT", pid, [
        (mfg, 'CASH', 70, False),
        (sales, 'CASH', 20, False),
        (mkt, 'CASH', 10, False),
    ])
    invoice_id, _, total = await make_invoice(session, [(pid, 1, "160000.00")])

    result = await record_payment(
        session, invoice_id=invoice_id, amount=total,
        payment_method="pos", reference="SPLIT-TEST")
    await session.commit()

    got = await allocations_by_account(
        session, result["settlement"]["settlement_id"])
    assert got[("MFG", "CASH")] == Decimal("112000.00")
    assert got[("SALES", "CASH")] == Decimal("32000.00")
    assert got[("MKT", "CASH")] == Decimal("16000.00")
    assert sum(got.values()) == total


@pytest.mark.asyncio
async def test_indivisible_amount_still_sums_to_the_payment(session):
    """A 70/20/10 split of ₦33.33 must not lose or invent a cent."""
    a = await make_account(session, "A", "A")
    b = await make_account(session, "B", "B")
    c = await make_account(session, "C", "C")
    pid = await make_product(session, "P1", "Odd Amount Product")
    await map_product(session, pid, a)
    await make_rule(session, "ODD", pid, [
        (a, 'CASH', 70, False), (b, 'CASH', 20, False), (c, 'CASH', 10, False)])
    invoice_id, _, total = await make_invoice(session, [(pid, 1, "33.33")])

    result = await record_payment(
        session, invoice_id=invoice_id, amount=total,
        payment_method="cash", reference="ODD-1")
    await session.commit()

    got = await allocations_by_account(
        session, result["settlement"]["settlement_id"])
    assert sum(got.values()) == Decimal("33.33")


@pytest.mark.asyncio
async def test_residual_split_absorbs_the_remainder(session):
    """A fixed handling fee plus 'the rest' must still allocate the whole line."""
    fee = await make_account(session, "FEE", "Handling Fees")
    main = await make_account(session, "MAIN", "Main Account")
    pid = await make_product(session, "P1", "Product")
    await map_product(session, pid, main)

    rule = (await session.execute(text("""
        INSERT INTO settlement_rules (id, code, name, scope, product_id, basis,
                                      priority, effective_from, is_active)
        VALUES (gen_random_uuid(), 'FEE-RULE', 'Fee rule', 'PRODUCT', :p,
                'FIXED', 100, CURRENT_DATE - 1, TRUE) RETURNING id
    """), {"p": str(pid)})).first()
    await session.execute(text("""
        INSERT INTO settlement_rule_splits
            (id, rule_id, financial_account_id, allocation_type, fixed_amount,
             sort_order)
        VALUES (gen_random_uuid(), :r, :a, 'CASH', 500.00, 0)
    """), {"r": str(rule.id), "a": str(fee)})
    await session.execute(text("""
        INSERT INTO settlement_rule_splits
            (id, rule_id, financial_account_id, allocation_type, is_residual,
             sort_order)
        VALUES (gen_random_uuid(), :r, :a, 'CASH', TRUE, 1)
    """), {"r": str(rule.id), "a": str(main)})

    invoice_id, _, total = await make_invoice(session, [(pid, 1, "10000.00")])
    result = await record_payment(
        session, invoice_id=invoice_id, amount=total,
        payment_method="bank_transfer", reference="FEE-1")
    await session.commit()

    got = await allocations_by_account(
        session, result["settlement"]["settlement_id"])
    assert got[("FEE", "CASH")] == Decimal("500.00")
    assert got[("MAIN", "CASH")] == Decimal("9500.00")


# ---------------------------------------------------------------------------
# partial payments
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_instalments_converge_on_the_same_split(session):
    """Three instalments must end where one payment would have.

    This is the property that makes partial payment safe: each payment is
    allocated against REMAINING line capacity, so the totals converge exactly
    rather than drifting by a cent per instalment.
    """
    a = await make_account(session, "A", "Account A")
    b = await make_account(session, "B", "Account B")
    p1 = await make_product(session, "P1", "Product One")
    p2 = await make_product(session, "P2", "Product Two")
    await map_product(session, p1, a)
    await map_product(session, p2, b)

    invoice_id, _, total = await make_invoice(
        session, [(p1, 3, "33.33"), (p2, 7, "11.11")])

    for i, amount in enumerate(["50.00", "25.00"]):
        await record_payment(session, invoice_id=invoice_id, amount=amount,
                             payment_method="cash", reference=f"PART-{i}")
        await session.commit()

    remaining = money(total - Decimal("75.00"))
    await record_payment(session, invoice_id=invoice_id, amount=remaining,
                         payment_method="cash", reference="PART-FINAL")
    await session.commit()

    rows = (await session.execute(text("""
        SELECT fa.code, SUM(d.amount) AS total
          FROM settlement_details d
          JOIN settlements s ON s.id = d.settlement_id
          JOIN financial_accounts fa ON fa.id = d.financial_account_id
         WHERE s.invoice_id = :i AND s.status = 'COMPLETED'
           AND d.allocation_type = 'CASH'
         GROUP BY fa.code
    """), {"i": str(invoice_id)})).fetchall()
    got = {r.code: money(r.total) for r in rows}

    assert got["A"] == money(Decimal("3") * Decimal("33.33"))
    assert got["B"] == money(Decimal("7") * Decimal("11.11"))
    assert sum(got.values()) == total


@pytest.mark.asyncio
async def test_a_payment_beyond_the_invoice_is_refused_not_guessed(session):
    """Money with no line to attach to must fail loudly, not land somewhere."""
    a = await make_account(session, "A", "Account A")
    pid = await make_product(session, "P1", "Product")
    await map_product(session, pid, a)
    invoice_id, _, total = await make_invoice(session, [(pid, 1, "100.00")])

    # allow_overpayment bypasses the receivables guard, so the engine's own
    # guard is what is under test here.
    result = await record_payment(
        session, invoice_id=invoice_id, amount="150.00",
        payment_method="cash", reference="OVER", allow_overpayment=True)
    await session.commit()

    assert result["settlement"]["status"] == "FAILED"
    assert "exceeds" in result["settlement"]["reason"]

    detail_count = (await session.execute(text("""
        SELECT COUNT(*) FROM settlement_details d
          JOIN settlements s ON s.id = d.settlement_id
         WHERE s.invoice_id = :i
    """), {"i": str(invoice_id)})).scalar()
    assert detail_count == 0, "a refused settlement must allocate nothing"


# ---------------------------------------------------------------------------
# idempotence and failure handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_distributing_twice_does_not_pay_twice(session):
    a = await make_account(session, "A", "Account A")
    pid = await make_product(session, "P1", "Product")
    await map_product(session, pid, a)
    invoice_id, _, total = await make_invoice(session, [(pid, 1, "1000.00")])

    result = await record_payment(
        session, invoice_id=invoice_id, amount=total,
        payment_method="cash", reference="IDEM")
    await session.commit()
    payment_id = result["payment_id"]

    for _ in range(3):
        again = await distribute_payment(session, payment_id=payment_id)
        await session.commit()
        assert again["idempotent"] is True

    total_allocated = money((await session.execute(text("""
        SELECT COALESCE(SUM(d.amount), 0) FROM settlement_details d
          JOIN settlements s ON s.id = d.settlement_id
         WHERE s.payment_id = :p AND d.allocation_type = 'CASH'
    """), {"p": str(payment_id)})).scalar())
    assert total_allocated == Decimal("1000.00")

    live = (await session.execute(text("""
        SELECT COUNT(*) FROM settlements
         WHERE payment_id = :p AND status IN ('PENDING','COMPLETED','SKIPPED')
    """), {"p": str(payment_id)})).scalar()
    assert live == 1


@pytest.mark.asyncio
async def test_suspended_account_pauses_the_whole_settlement(session):
    """No partial allocation, and the payment still stands."""
    good = await make_account(session, "GOOD", "Good Account")
    bad = await make_account(session, "BAD", "Suspended Account",
                             status="SUSPENDED")
    p1 = await make_product(session, "P1", "Product One")
    p2 = await make_product(session, "P2", "Product Two")
    await map_product(session, p1, good)
    await map_product(session, p2, bad)

    invoice_id, _, total = await make_invoice(
        session, [(p1, 1, "600.00"), (p2, 1, "400.00")])

    result = await record_payment(
        session, invoice_id=invoice_id, amount=total,
        payment_method="cash", reference="SUSPENDED")
    await session.commit()

    assert result["settlement"]["status"] == "FAILED"
    assert "SUSPENDED" in result["settlement"]["reason"]

    # The good half must NOT have been allocated on its own.
    allocated = (await session.execute(text("""
        SELECT COUNT(*) FROM settlement_details d
          JOIN settlements s ON s.id = d.settlement_id
         WHERE s.invoice_id = :i
    """), {"i": str(invoice_id)})).scalar()
    assert allocated == 0

    # ...and the money is still recorded as received.
    paid = money((await session.execute(text(
        "SELECT COALESCE(SUM(amount),0) FROM payments WHERE invoice_id = :i"),
        {"i": str(invoice_id)})).scalar())
    assert paid == total


@pytest.mark.asyncio
async def test_retry_settles_once_the_account_is_reactivated(session):
    good = await make_account(session, "GOOD", "Good Account")
    bad = await make_account(session, "BAD", "Suspended", status="SUSPENDED")
    p1 = await make_product(session, "P1", "Product One")
    p2 = await make_product(session, "P2", "Product Two")
    await map_product(session, p1, good)
    await map_product(session, p2, bad)
    invoice_id, _, total = await make_invoice(
        session, [(p1, 1, "600.00"), (p2, 1, "400.00")])

    await record_payment(session, invoice_id=invoice_id, amount=total,
                         payment_method="cash", reference="RETRY")
    await session.commit()

    health = await settlement_health(session)
    assert health["failed_settlements"] == 1
    assert health["healthy"] is False

    await session.execute(text(
        "UPDATE financial_accounts SET status = 'ACTIVE' WHERE code = 'BAD'"))
    await session.commit()

    outcome = await retry_failed_settlements(session)
    await session.commit()
    assert outcome["settled"] == 1

    got = (await session.execute(text("""
        SELECT fa.code, SUM(d.amount) AS amount
          FROM settlement_details d
          JOIN settlements s ON s.id = d.settlement_id
          JOIN financial_accounts fa ON fa.id = d.financial_account_id
         WHERE s.invoice_id = :i AND s.status = 'COMPLETED'
         GROUP BY fa.code
    """), {"i": str(invoice_id)})).fetchall()
    amounts = {r.code: money(r.amount) for r in got}
    assert amounts == {"GOOD": Decimal("600.00"), "BAD": Decimal("400.00")}

    assert (await settlement_health(session))["healthy"] is True


@pytest.mark.asyncio
async def test_unconfigured_product_skips_rather_than_guessing(session):
    """Nothing is invented for a product nobody has mapped."""
    pid = await make_product(session, "P1", "Unmapped Product")
    invoice_id, _, total = await make_invoice(session, [(pid, 1, "500.00")])

    result = await record_payment(
        session, invoice_id=invoice_id, amount=total,
        payment_method="cash", reference="UNMAPPED")
    await session.commit()

    assert result["settlement"]["status"] == "SKIPPED"
    assert "Unmapped Product" in result["settlement"]["reason"]


@pytest.mark.asyncio
async def test_strict_mode_fails_an_unconfigured_product(session, monkeypatch):
    monkeypatch.setenv("MAPD_STRICT", "true")
    pid = await make_product(session, "P1", "Unmapped Product")
    invoice_id, _, total = await make_invoice(session, [(pid, 1, "500.00")])

    result = await record_payment(
        session, invoice_id=invoice_id, amount=total,
        payment_method="cash", reference="STRICT")
    await session.commit()
    assert result["settlement"]["status"] == "FAILED"


# ---------------------------------------------------------------------------
# ledger agreement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ledger_entry_matches_the_allocations(session):
    """The journal must say the same thing the settlement details do."""
    hera = await make_account(session, "HERA", "Hera Account", gl="1250")
    honey = await make_account(session, "HONEY", "Honey Account", gl="1100")
    p1 = await make_product(session, "P1", "Hera Gel")
    p2 = await make_product(session, "P2", "Honey Gauze")
    await map_product(session, p1, hera)
    await map_product(session, p2, honey)

    invoice_id, _, total = await make_invoice(
        session, [(p1, 1, "160000.00"), (p2, 1, "50000.00")])
    result = await record_payment(
        session, invoice_id=invoice_id, amount=total,
        payment_method="bank_transfer", reference="LEDGER")
    await session.commit()

    entry_id = result["settlement"]["journal_entry_id"]
    assert entry_id is not None, "posting was enabled; an entry was expected"

    lines = (await session.execute(text("""
        SELECT a.code, l.debit, l.credit
          FROM gl_journal_lines l
          JOIN gl_accounts a ON a.id = l.account_id
         WHERE l.entry_id = :e ORDER BY a.code
    """), {"e": str(entry_id)})).fetchall()

    debits = {r.code: money(r.debit) for r in lines if money(r.debit) > 0}
    credits = {r.code: money(r.credit) for r in lines if money(r.credit) > 0}

    assert debits["1250"] == Decimal("160000.00")
    assert debits["1100"] == Decimal("50000.00")
    # Money left the bank account the payment landed in.
    assert credits["1200"] == Decimal("210000.00")
    assert sum(debits.values()) == sum(credits.values())


@pytest.mark.asyncio
async def test_allocation_to_the_source_account_is_netted_out(session):
    """Dr 1200 / Cr 1200 carries no information; it must not be posted."""
    same = await make_account(session, "SAME", "Same As Source", gl="1200")
    pid = await make_product(session, "P1", "Product")
    await map_product(session, pid, same)
    invoice_id, _, total = await make_invoice(session, [(pid, 1, "1000.00")])

    result = await record_payment(
        session, invoice_id=invoice_id, amount=total,
        payment_method="bank_transfer", reference="SELF")
    await session.commit()

    assert result["settlement"]["status"] == "COMPLETED"
    assert result["settlement"]["journal_entry_id"] is None
    # The allocation is still recorded -- only the redundant journal is skipped.
    got = await allocations_by_account(
        session, result["settlement"]["settlement_id"])
    assert got[("SAME", "CASH")] == Decimal("1000.00")


@pytest.mark.asyncio
async def test_obligation_is_additional_to_the_cash_split(session):
    """A 10% distributor commission creates a liability; the cash still banks."""
    bank = await make_account(session, "BANK", "Main Bank", gl="1200")
    commission = await make_account(
        session, "COMM", "Distributor Commission", gl="6310",
        kind="OBLIGATION", contra="2510")
    pid = await make_product(session, "P1", "Product")
    await map_product(session, pid, bank)
    await make_rule(session, "COMM-RULE", pid, [
        (bank, 'CASH', 100, False),
        (commission, 'OBLIGATION', 10, False),
    ])
    invoice_id, _, total = await make_invoice(session, [(pid, 1, "10000.00")])

    result = await record_payment(
        session, invoice_id=invoice_id, amount=total,
        payment_method="cash", reference="COMM")
    await session.commit()
    settlement = result["settlement"]

    got = await allocations_by_account(session, settlement["settlement_id"])
    assert got[("BANK", "CASH")] == Decimal("10000.00")
    assert got[("COMM", "OBLIGATION")] == Decimal("1000.00")
    assert settlement["allocated_amount"] == Decimal("10000.00"), \
        "the obligation must not reduce the cash allocated"

    lines = (await session.execute(text("""
        SELECT a.code, l.debit, l.credit
          FROM gl_journal_lines l JOIN gl_accounts a ON a.id = l.account_id
         WHERE l.entry_id = :e
    """), {"e": str(settlement["journal_entry_id"])})).fetchall()
    by_code = {r.code: (money(r.debit), money(r.credit)) for r in lines}
    assert by_code["6310"][0] == Decimal("1000.00")   # expense debited
    assert by_code["2510"][1] == Decimal("1000.00")   # liability credited


# ---------------------------------------------------------------------------
# immutability and reversal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_settlement_details_cannot_be_edited_or_deleted(session):
    a = await make_account(session, "A", "Account A")
    pid = await make_product(session, "P1", "Product")
    await map_product(session, pid, a)
    invoice_id, _, total = await make_invoice(session, [(pid, 1, "100.00")])
    result = await record_payment(session, invoice_id=invoice_id, amount=total,
                                  payment_method="cash", reference="IMMUT")
    await session.commit()
    sid = result["settlement"]["settlement_id"]

    for statement in (
        "UPDATE settlement_details SET amount = 1 WHERE settlement_id = :s",
        "DELETE FROM settlement_details WHERE settlement_id = :s",
    ):
        with pytest.raises(Exception) as exc:
            await session.execute(text(statement), {"s": str(sid)})
        assert "append-only" in str(exc.value)
        await session.rollback()


@pytest.mark.asyncio
async def test_settlements_cannot_be_deleted(session):
    a = await make_account(session, "A", "Account A")
    pid = await make_product(session, "P1", "Product")
    await map_product(session, pid, a)
    invoice_id, _, total = await make_invoice(session, [(pid, 1, "100.00")])
    result = await record_payment(session, invoice_id=invoice_id, amount=total,
                                  payment_method="cash", reference="NODEL")
    await session.commit()

    with pytest.raises(Exception) as exc:
        await session.execute(
            text("DELETE FROM settlements WHERE id = :s"),
            {"s": str(result["settlement"]["settlement_id"])})
    assert "cannot be deleted" in str(exc.value)
    await session.rollback()


@pytest.mark.asyncio
async def test_full_refund_reverses_every_allocation(session):
    hera = await make_account(session, "HERA", "Hera", gl="1250")
    honey = await make_account(session, "HONEY", "Honey", gl="1100")
    p1 = await make_product(session, "P1", "Hera Gel")
    p2 = await make_product(session, "P2", "Honey Gauze")
    await map_product(session, p1, hera)
    await map_product(session, p2, honey)
    invoice_id, _, total = await make_invoice(
        session, [(p1, 1, "160000.00"), (p2, 1, "50000.00")])
    result = await record_payment(session, invoice_id=invoice_id, amount=total,
                                  payment_method="bank_transfer",
                                  reference="REFUND")
    await session.commit()
    sid = result["settlement"]["settlement_id"]

    refund = await refund_settlement(
        session, settlement_id=sid, reason="Customer returned the whole order")
    await session.commit()

    assert refund["is_full_reversal"] is True
    status = (await session.execute(text(
        "SELECT status FROM settlements WHERE id = :s"), {"s": str(sid)})).scalar()
    assert status == 'REVERSED'

    # The original entry stays; a mirror is posted against it.
    original = (await session.execute(text(
        "SELECT status FROM gl_journal_entries WHERE id = :e"),
        {"e": str(result["settlement"]["journal_entry_id"])})).scalar()
    assert original == 'REVERSED'

    reversal_lines = (await session.execute(text("""
        SELECT a.code, l.debit, l.credit
          FROM gl_journal_lines l JOIN gl_accounts a ON a.id = l.account_id
         WHERE l.entry_id = :e
    """), {"e": str(refund["journal_entry_id"])})).fetchall()
    by_code = {r.code: (money(r.debit), money(r.credit)) for r in reversal_lines}
    assert by_code["1250"][1] == Decimal("160000.00")   # credited back
    assert by_code["1100"][1] == Decimal("50000.00")
    assert by_code["1200"][0] == Decimal("210000.00")   # returned to source


@pytest.mark.asyncio
async def test_partial_refund_takes_a_share_from_every_destination(session):
    """Clawing it all back from one account would overstate the others."""
    a = await make_account(session, "A", "Account A", gl="1250")
    b = await make_account(session, "B", "Account B", gl="1100")
    p1 = await make_product(session, "P1", "One")
    p2 = await make_product(session, "P2", "Two")
    await map_product(session, p1, a)
    await map_product(session, p2, b)
    invoice_id, _, total = await make_invoice(
        session, [(p1, 1, "800.00"), (p2, 1, "200.00")])
    result = await record_payment(session, invoice_id=invoice_id, amount=total,
                                  payment_method="bank_transfer",
                                  reference="PARTIAL-REFUND")
    await session.commit()

    refund = await refund_settlement(
        session, settlement_id=result["settlement"]["settlement_id"],
        amount="100.00", reason="One damaged carton returned")
    await session.commit()
    assert refund["is_full_reversal"] is False

    lines = (await session.execute(text("""
        SELECT a.code, l.debit, l.credit
          FROM gl_journal_lines l JOIN gl_accounts a ON a.id = l.account_id
         WHERE l.entry_id = :e
    """), {"e": str(refund["journal_entry_id"])})).fetchall()
    by_code = {r.code: (money(r.debit), money(r.credit)) for r in lines}
    assert by_code["1250"][1] == Decimal("80.00")
    assert by_code["1100"][1] == Decimal("20.00")
    assert by_code["1200"][0] == Decimal("100.00")

    # The settlement stays COMPLETED -- only part of it came back.
    status = (await session.execute(text(
        "SELECT status FROM settlements WHERE id = :s"),
        {"s": str(result["settlement"]["settlement_id"])})).scalar()
    assert status == 'COMPLETED'


@pytest.mark.asyncio
async def test_refund_cannot_exceed_what_was_settled(session):
    a = await make_account(session, "A", "Account A")
    pid = await make_product(session, "P1", "Product")
    await map_product(session, pid, a)
    invoice_id, _, total = await make_invoice(session, [(pid, 1, "100.00")])
    result = await record_payment(session, invoice_id=invoice_id, amount=total,
                                  payment_method="cash", reference="OVERREF")
    await session.commit()

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await refund_settlement(
            session, settlement_id=result["settlement"]["settlement_id"],
            amount="150.00", reason="Too much")
    assert "exceed" in exc.value.detail


# ---------------------------------------------------------------------------
# planning without side effects
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_payment_survives_a_broken_distribution(session, monkeypatch):
    """A failed SQL statement inside the engine must not take the payment down.

    PostgreSQL poisons the whole transaction after any failed statement, so an
    unexpected error in distribution -- a missing table on a half-migrated
    database, say -- would abort the caller's transaction and lose the record
    of money that has already changed hands. The hook runs inside a savepoint
    precisely so rolling back leaves a usable transaction behind.
    """
    import app.services.settlement as engine

    async def exploding(session_, **kwargs):
        # A real failed statement, not a bare raise: only this poisons the
        # transaction, which is the condition under test.
        await session_.execute(text("SELECT * FROM table_that_does_not_exist"))

    monkeypatch.setattr(engine, "distribute_payment", exploding)

    a = await make_account(session, "A", "Account A")
    pid = await make_product(session, "P1", "Product")
    await map_product(session, pid, a)
    invoice_id, _, total = await make_invoice(session, [(pid, 1, "1000.00")])

    result = await record_payment(session, invoice_id=invoice_id, amount=total,
                                  payment_method="cash", reference="BROKEN")
    await session.commit()

    assert result["settlement"]["status"] == "ERROR"
    paid = money((await session.execute(text(
        "SELECT COALESCE(SUM(amount),0) FROM payments WHERE invoice_id = :i"),
        {"i": str(invoice_id)})).scalar())
    assert paid == total, "the payment must survive a broken distribution"

    status = (await session.execute(text(
        "SELECT status FROM invoices WHERE id = :i"),
        {"i": str(invoice_id)})).scalar()
    assert status == 'paid'


@pytest.mark.asyncio
async def test_plan_writes_nothing(session):
    """The preview an operator sees must not be able to move money."""
    a = await make_account(session, "A", "Account A")
    pid = await make_product(session, "P1", "Product")
    await map_product(session, pid, a)
    invoice_id, _, total = await make_invoice(session, [(pid, 1, "1000.00")])

    payment = (await session.execute(text("""
        INSERT INTO payments (id, invoice_id, payment_method, amount, reference)
        VALUES (gen_random_uuid(), :i, 'cash', :a, 'PLAN') RETURNING id
    """), {"i": str(invoice_id), "a": str(total)})).first()
    await session.commit()

    plan = await build_plan(session, payment_id=payment.id)
    await session.commit()

    assert plan["status"] == "READY"
    assert plan["allocated_amount"] == Decimal("1000.00")
    count = (await session.execute(
        text("SELECT COUNT(*) FROM settlements"))).scalar()
    assert count == 0
