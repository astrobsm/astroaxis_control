"""Event-to-journal translation, and reconciliation back to the source data.

The point of these tests is not that the ledger balances internally (that is
test_ledger.py) but that what it says AGREES with inventory, sales and
payments. A ledger that balances beautifully while disagreeing with the
warehouse is worse than no ledger at all.
"""
import os
import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.services.ledger import trial_balance, profit_and_loss, balance_sheet
from app.services.posting import (
    post_sale, post_customer_payment, post_production_completion,
    post_inventory_writeoff, post_purchase, post_customer_return)

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")

SCHEMA = """
DROP TABLE IF EXISTS payments CASCADE;
DROP TABLE IF EXISTS invoices CASCADE;
DROP TABLE IF EXISTS sales_order_lines CASCADE;
DROP TABLE IF EXISTS sales_orders CASCADE;
CREATE TABLE sales_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_number VARCHAR(64) UNIQUE NOT NULL,
    customer_id UUID, warehouse_id UUID,
    order_date TIMESTAMPTZ DEFAULT NOW(),
    total_amount NUMERIC(18,2) DEFAULT 0
);
CREATE TABLE sales_order_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sales_order_id UUID NOT NULL REFERENCES sales_orders(id),
    product_id UUID NOT NULL, unit VARCHAR(50),
    quantity NUMERIC(18,6) NOT NULL,
    unit_price NUMERIC(18,6) NOT NULL,
    line_total NUMERIC(18,2) NOT NULL,
    unit_cost NUMERIC(18,6), cost_total NUMERIC(18,2),
    cost_source VARCHAR(32)
);
CREATE TABLE invoices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_number VARCHAR(64) UNIQUE NOT NULL,
    customer_id UUID, sales_order_id UUID REFERENCES sales_orders(id),
    total_amount NUMERIC(18,2) DEFAULT 0,
    paid_amount NUMERIC(18,2) DEFAULT 0,
    status VARCHAR(32) DEFAULT 'pending'
);
CREATE TABLE payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id UUID NOT NULL REFERENCES invoices(id),
    payment_method VARCHAR(50) NOT NULL,
    amount NUMERIC(18,2) NOT NULL,
    payment_date TIMESTAMPTZ DEFAULT NOW(),
    reference VARCHAR(255), notes TEXT
);
"""


def _apply_gl_migration(conn):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / \
        "m2345678901l_general_ledger.py"
    spec = importlib.util.spec_from_file_location("mig_m2", path)
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
        _apply_gl_migration(c)
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


TODAY = date(2026, 7, 20)


@pytest.fixture(autouse=True)
def _enable_posting(monkeypatch):
    """These tests exercise the event->journal translation itself, which is
    gated OFF by default for safe roll-out. Enable it for the whole module,
    and clear any cutover so historical test dates still post."""
    monkeypatch.setenv("ACCOUNTING_POSTING_ENABLED", "true")
    monkeypatch.delenv("ACCOUNTING_CUTOVER_DATE", raising=False)


async def make_order(session, *, revenue="2500.00", cost="1000.00",
                     cost_source="wac_global"):
    oid = uuid.uuid4()
    num = f"SO-{uuid.uuid4().hex[:8].upper()}"
    await session.execute(text("""
        INSERT INTO sales_orders (id, order_number, order_date, total_amount)
        VALUES (:i, :n, :d, :t)
    """), {"i": str(oid), "n": num, "d": TODAY, "t": revenue})
    await session.execute(text("""
        INSERT INTO sales_order_lines
            (id, sales_order_id, product_id, quantity, unit_price,
             line_total, unit_cost, cost_total, cost_source)
        VALUES (gen_random_uuid(), :o, :p, 10, :up, :lt, :uc, :ct, :cs)
    """), {"o": str(oid), "p": str(uuid.uuid4()),
           "up": str(Decimal(revenue) / 10), "lt": revenue,
           "uc": str(Decimal(cost) / 10), "ct": cost, "cs": cost_source})
    await session.commit()
    return oid, num


async def make_payment(session, order_id, order_number, amount, method="bank"):
    iid, pid = uuid.uuid4(), uuid.uuid4()
    await session.execute(text("""
        INSERT INTO invoices (id, invoice_number, sales_order_id, total_amount)
        VALUES (:i, :n, :o, :t)
    """), {"i": str(iid), "n": f"INV-{order_number}", "o": str(order_id),
           "t": amount})
    await session.execute(text("""
        INSERT INTO payments (id, invoice_id, payment_method, amount,
                              payment_date)
        VALUES (:p, :i, :m, :a, :d)
    """), {"p": str(pid), "i": str(iid), "m": method, "a": amount, "d": TODAY})
    await session.commit()
    return pid


# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sale_posts_revenue_and_cogs(session):
    oid, num = await make_order(session, revenue="2500.00", cost="1000.00")
    await post_sale(session, order_id=oid, on=TODAY)
    await session.commit()

    tb = await trial_balance(session)
    assert tb["balanced"]
    by_code = {a["code"]: a for a in tb["accounts"]}
    assert by_code["1300"]["balance"] == 2500.00   # receivable
    assert by_code["4100"]["balance"] == 2500.00   # sales
    assert by_code["5100"]["balance"] == 1000.00   # COGS
    assert by_code["1430"]["balance"] == -1000.00  # finished goods relieved

    pl = await profit_and_loss(session)
    assert pl["net_profit"] == 1500.00


@pytest.mark.asyncio
async def test_sale_with_unknown_cost_flags_itself(session):
    """A sale whose cost is unknown must not silently book 100% margin -- the
    entry says so in its description so a reviewer can see it."""
    oid, num = await make_order(session, revenue="900.00", cost="0.00",
                                cost_source="unknown")
    eid = await post_sale(session, order_id=oid, on=TODAY)
    await session.commit()

    desc = (await session.execute(text(
        "SELECT description FROM gl_journal_entries WHERE id = :i"),
        {"i": str(eid)})).scalar()
    assert "no known cost" in desc
    assert "COGS understated" in desc

    tb = await trial_balance(session)
    assert tb["balanced"]
    by_code = {a["code"]: a for a in tb["accounts"]}
    assert "5100" not in by_code, "must not invent a COGS figure"


@pytest.mark.asyncio
async def test_payment_clears_receivable(session):
    oid, num = await make_order(session, revenue="2500.00", cost="1000.00")
    await post_sale(session, order_id=oid, on=TODAY)
    pid = await make_payment(session, oid, num, "2500.00")
    await post_customer_payment(session, payment_id=pid)
    await session.commit()

    tb = await trial_balance(session)
    by_code = {a["code"]: a for a in tb["accounts"]}
    assert by_code["1300"]["balance"] == 0, "receivable should be cleared"
    assert by_code["1200"]["balance"] == 2500.00
    assert tb["balanced"]


@pytest.mark.asyncio
async def test_cash_payment_hits_cash_not_bank(session):
    oid, num = await make_order(session, revenue="100.00", cost="40.00")
    await post_sale(session, order_id=oid, on=TODAY)
    pid = await make_payment(session, oid, num, "100.00", method="cash")
    await post_customer_payment(session, payment_id=pid)
    await session.commit()

    tb = await trial_balance(session)
    by_code = {a["code"]: a for a in tb["accounts"]}
    assert by_code["1100"]["balance"] == 100.00
    assert "1200" not in by_code


@pytest.mark.asyncio
async def test_production_variance_is_explicit(session):
    """Finished goods worth more than the materials consumed (labour and
    overhead) must land in a named variance account, not vanish."""
    await post_production_completion(
        session, completion_id=uuid.uuid4(),
        raw_material_cost="1000.00", finished_goods_value="1350.00",
        reference="PC-1", on=TODAY)
    await session.commit()

    tb = await trial_balance(session)
    assert tb["balanced"]
    by_code = {a["code"]: a for a in tb["accounts"]}
    assert by_code["1430"]["balance"] == 1350.00
    assert by_code["1410"]["balance"] == -1000.00
    assert by_code["5600"]["balance"] == -350.00  # credit = absorbed


@pytest.mark.asyncio
async def test_writeoff_reduces_inventory_and_hits_expense(session):
    await post_purchase(session, value="500.00", reference="PO-1",
                        is_raw_material=True, on=TODAY)
    await post_inventory_writeoff(session, value="120.00", reference="DMG-1",
                                  is_raw_material=True, on=TODAY)
    await session.commit()

    tb = await trial_balance(session)
    by_code = {a["code"]: a for a in tb["accounts"]}
    assert by_code["1410"]["balance"] == 380.00
    assert by_code["5500"]["balance"] == 120.00
    assert by_code["2100"]["balance"] == 500.00
    assert tb["balanced"]


@pytest.mark.asyncio
async def test_return_restocked_reverses_both_sides(session):
    oid, num = await make_order(session, revenue="1000.00", cost="400.00")
    await post_sale(session, order_id=oid, on=TODAY)
    await post_customer_return(
        session, revenue_value="1000.00", cost_value="400.00",
        reference=num, restocked=True, on=TODAY)
    await session.commit()

    tb = await trial_balance(session)
    assert tb["balanced"]
    pl = await profit_and_loss(session)
    # Revenue 1000 less returns 1000 = 0; COGS 400 less 400 restocked = 0.
    assert pl["total_income"] == 0
    assert pl["total_expenses"] == 0
    assert pl["net_profit"] == 0


@pytest.mark.asyncio
async def test_return_not_restocked_keeps_cost_in_cogs(session):
    """Goods returned damaged never came back to the shelf. Crediting
    inventory for them would overstate assets."""
    oid, num = await make_order(session, revenue="1000.00", cost="400.00")
    await post_sale(session, order_id=oid, on=TODAY)
    await post_customer_return(
        session, revenue_value="1000.00", cost_value="400.00",
        reference=num, restocked=False, on=TODAY)
    await session.commit()

    tb = await trial_balance(session)
    assert tb["balanced"]
    by_code = {a["code"]: a for a in tb["accounts"]}
    assert by_code["5100"]["balance"] == 400.00, "cost must stay expensed"
    assert by_code["1430"]["balance"] == -400.00
    pl = await profit_and_loss(session)
    assert pl["net_profit"] == -400.00  # a real loss, correctly shown


# ---------------------------------------------------------------------------
# Reconciliation: does the ledger AGREE with the source systems?
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ledger_revenue_reconciles_to_sales_orders(session):
    total = Decimal("0")
    for amt in ("1200.00", "3400.50", "875.25"):
        oid, _ = await make_order(session, revenue=amt, cost="100.00")
        await post_sale(session, order_id=oid, on=TODAY)
        total += Decimal(amt)
    await session.commit()

    source = (await session.execute(text(
        "SELECT COALESCE(SUM(line_total),0) FROM sales_order_lines"))).scalar()
    pl = await profit_and_loss(session)

    assert Decimal(str(source)) == total
    assert Decimal(str(pl["total_income"])) == total, \
        "ledger revenue disagrees with the sales data it was posted from"


@pytest.mark.asyncio
async def test_ledger_cash_reconciles_to_payments(session):
    collected = Decimal("0")
    for amt in ("500.00", "1250.75"):
        oid, num = await make_order(session, revenue=amt, cost="100.00")
        await post_sale(session, order_id=oid, on=TODAY)
        pid = await make_payment(session, oid, num, amt)
        await post_customer_payment(session, payment_id=pid)
        collected += Decimal(amt)
    await session.commit()

    source = (await session.execute(text(
        "SELECT COALESCE(SUM(amount),0) FROM payments"))).scalar()
    tb = await trial_balance(session)
    bank = {a["code"]: a for a in tb["accounts"]}["1200"]["balance"]

    assert Decimal(str(source)) == collected
    assert Decimal(str(bank)) == collected, \
        "ledger cash disagrees with the payments it was posted from"


@pytest.mark.asyncio
async def test_full_cycle_balance_sheet_holds(session):
    """Purchase -> produce -> sell -> collect. The equation must hold."""
    await post_purchase(session, value="1000.00", reference="PO-1", on=TODAY)
    await post_production_completion(
        session, completion_id=uuid.uuid4(),
        raw_material_cost="1000.00", finished_goods_value="1000.00",
        reference="PC-1", on=TODAY)
    oid, num = await make_order(session, revenue="2500.00", cost="1000.00")
    await post_sale(session, order_id=oid, on=TODAY)
    pid = await make_payment(session, oid, num, "2500.00")
    await post_customer_payment(session, payment_id=pid)
    await session.commit()

    tb = await trial_balance(session)
    bs = await balance_sheet(session)
    pl = await profit_and_loss(session)

    assert tb["balanced"], tb["difference"]
    assert bs["balanced"], bs["difference"]
    assert bs["assets"] == 2500.00        # cash collected, inventory consumed
    assert bs["liabilities"] == 1000.00   # still owe the supplier
    assert pl["net_profit"] == 1500.00
    assert bs["current_period_earnings"] == 1500.00
