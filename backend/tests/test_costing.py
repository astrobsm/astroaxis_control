"""COGS must be recorded at the time of sale, not re-derived later.

The defect these guard against: profits.py resolved cost by joining
product_pricing live, so changing a cost price today restated the profit of
every period already closed, reported and paid out.

    export TEST_DATABASE_URL='postgresql+asyncpg://postgres:pw@localhost:5432/astro_test'
    cd backend && pytest tests/test_costing.py -v
"""
import os
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.services.costing import (
    weighted_average_cost, resolve_unit_cost, snapshot_line_cost)

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")

SCHEMA = """
DROP TABLE IF EXISTS sales_order_lines CASCADE;
DROP TABLE IF EXISTS sales_orders CASCADE;
DROP TABLE IF EXISTS stock_movements CASCADE;
DROP TABLE IF EXISTS product_pricing CASCADE;
DROP TABLE IF EXISTS products CASCADE;

CREATE TABLE products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sku VARCHAR(64) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    unit VARCHAR(32) DEFAULT 'each',
    cost_price NUMERIC(18,2) DEFAULT 0
);
CREATE TABLE product_pricing (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID NOT NULL REFERENCES products(id),
    unit VARCHAR(50) NOT NULL,
    cost_price NUMERIC(18,2) NOT NULL DEFAULT 0,
    retail_price NUMERIC(18,2) NOT NULL DEFAULT 0,
    wholesale_price NUMERIC(18,2) NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE stock_movements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    warehouse_id UUID NOT NULL,
    product_id UUID,
    raw_material_id UUID,
    movement_type VARCHAR(32) NOT NULL,
    quantity NUMERIC(18,6) NOT NULL,
    unit_cost NUMERIC(18,6),
    reference VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE sales_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_number VARCHAR(64) UNIQUE NOT NULL,
    customer_id UUID NOT NULL,
    warehouse_id UUID,
    total_amount NUMERIC(18,2) DEFAULT 0
);
CREATE TABLE sales_order_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sales_order_id UUID NOT NULL REFERENCES sales_orders(id),
    product_id UUID NOT NULL,
    unit VARCHAR(50),
    quantity NUMERIC(18,6) NOT NULL,
    unit_price NUMERIC(18,6) NOT NULL,
    line_total NUMERIC(18,2) NOT NULL,
    unit_cost NUMERIC(18,6),
    cost_total NUMERIC(18,2),
    cost_source VARCHAR(32)
);
"""

WH = uuid.uuid4()


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(TEST_DB, future=True)
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


async def make_product(session, cost_price="0"):
    pid = uuid.uuid4()
    await session.execute(text("""
        INSERT INTO products (id, sku, name, cost_price)
        VALUES (:id, :sku, 'Widget', :c)
    """), {"id": str(pid), "sku": f"SKU-{uuid.uuid4().hex[:8]}", "c": cost_price})
    await session.commit()
    return pid


async def add_intake(session, product_id, qty, unit_cost, warehouse_id=WH):
    await session.execute(text("""
        INSERT INTO stock_movements
            (id, warehouse_id, product_id, movement_type, quantity, unit_cost)
        VALUES (gen_random_uuid(), :w, :p, 'IN', :q, :c)
    """), {"w": str(warehouse_id), "p": str(product_id),
           "q": qty, "c": unit_cost})
    await session.commit()


async def make_line(session, product_id, qty="10", price="100", unit=None):
    oid, lid = uuid.uuid4(), uuid.uuid4()
    await session.execute(text("""
        INSERT INTO sales_orders (id, order_number, customer_id, warehouse_id)
        VALUES (:id, :n, :c, :w)
    """), {"id": str(oid), "n": f"SO-{uuid.uuid4().hex[:8]}",
           "c": str(uuid.uuid4()), "w": str(WH)})
    await session.execute(text("""
        INSERT INTO sales_order_lines
            (id, sales_order_id, product_id, unit, quantity, unit_price, line_total)
        VALUES (:id, :oid, :pid, :u, :q, :p, :lt)
    """), {"id": str(lid), "oid": str(oid), "pid": str(product_id),
           "u": unit, "q": qty, "p": price,
           "lt": str(Decimal(qty) * Decimal(price))})
    await session.commit()
    return lid


# ---------------------------------------------------------------------------
# Weighted average from the movement ledger
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_weighted_average_from_movements(session):
    pid = await make_product(session)
    await add_intake(session, pid, 100, "10.00")
    await add_intake(session, pid, 100, "20.00")
    # (100*10 + 100*20) / 200 = 15
    assert await weighted_average_cost(
        session, product_id=pid) == Decimal("15.000000")


@pytest.mark.asyncio
async def test_weighted_average_weights_by_quantity(session):
    pid = await make_product(session)
    await add_intake(session, pid, 900, "10.00")
    await add_intake(session, pid, 100, "20.00")
    # (9000 + 2000) / 1000 = 11 -- not the 15 a naive average would give
    assert await weighted_average_cost(
        session, product_id=pid) == Decimal("11.000000")


@pytest.mark.asyncio
async def test_weighted_average_ignores_outbound_and_zero_cost(session):
    pid = await make_product(session)
    await add_intake(session, pid, 100, "10.00")
    # An OUT movement and a zero-cost intake must not drag the average.
    await session.execute(text("""
        INSERT INTO stock_movements
            (id, warehouse_id, product_id, movement_type, quantity, unit_cost)
        VALUES (gen_random_uuid(), :w, :p, 'OUT', 50, 999),
               (gen_random_uuid(), :w, :p, 'IN', 50, 0)
    """), {"w": str(WH), "p": str(pid)})
    await session.commit()
    assert await weighted_average_cost(
        session, product_id=pid) == Decimal("10.000000")


@pytest.mark.asyncio
async def test_no_costed_movements_returns_none(session):
    """None, not zero -- callers must fall back explicitly rather than
    silently costing goods at nothing and reporting 100% margin."""
    pid = await make_product(session)
    assert await weighted_average_cost(session, product_id=pid) is None


# ---------------------------------------------------------------------------
# Fallback chain, and its audit trail
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_prefers_ledger_over_price_list(session):
    pid = await make_product(session, cost_price="99.00")
    await session.execute(text("""
        INSERT INTO product_pricing (product_id, unit, cost_price,
                                     retail_price, wholesale_price)
        VALUES (:p, 'each', 77.00, 120, 110)
    """), {"p": str(pid)})
    await add_intake(session, pid, 100, "10.00")
    await session.commit()

    cost, source = await resolve_unit_cost(
        session, product_id=pid, unit='each', warehouse_id=WH)
    assert cost == Decimal("10.000000")
    assert source == "wac_warehouse"


@pytest.mark.asyncio
async def test_resolve_falls_back_to_price_list_then_product(session):
    pid = await make_product(session, cost_price="99.00")
    await session.execute(text("""
        INSERT INTO product_pricing (product_id, unit, cost_price,
                                     retail_price, wholesale_price)
        VALUES (:p, 'carton', 77.00, 120, 110)
    """), {"p": str(pid)})
    await session.commit()

    cost, source = await resolve_unit_cost(
        session, product_id=pid, unit='carton')
    assert (cost, source) == (Decimal("77.000000"), "price_list_unit")

    # No matching unit -> product-level cost
    cost, source = await resolve_unit_cost(session, product_id=pid, unit='drum')
    assert (cost, source) == (Decimal("99.000000"), "product_cost_price")


@pytest.mark.asyncio
async def test_unknown_cost_is_labelled_not_silently_zero(session):
    pid = await make_product(session, cost_price="0")
    cost, source = await resolve_unit_cost(session, product_id=pid, unit='each')
    assert cost == Decimal("0")
    assert source == "unknown", "a zero cost must be distinguishable from a real one"


# ---------------------------------------------------------------------------
# The whole point: the snapshot must not move
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_snapshot_survives_price_list_change(session):
    """THE regression. Raising a cost price must not restate a closed sale."""
    pid = await make_product(session, cost_price="10.00")
    lid = await make_line(session, pid, qty="10", price="100", unit='each')

    cost, source = await snapshot_line_cost(
        session, line_id=lid, table='sales_order_lines',
        product_id=pid, quantity=10, unit='each', warehouse_id=WH)
    await session.commit()
    assert cost == Decimal("10.000000")

    # The cost price rises later -- a new supplier, inflation, a correction.
    await session.execute(text(
        "UPDATE products SET cost_price = 40.00 WHERE id = :p"), {"p": str(pid)})
    await session.commit()

    row = (await session.execute(text(
        "SELECT unit_cost, cost_total FROM sales_order_lines WHERE id = :l"),
        {"l": str(lid)})).first()

    assert Decimal(str(row.unit_cost)) == Decimal("10.000000"), \
        "historical cost was restated by a later price change"
    assert Decimal(str(row.cost_total)) == Decimal("100.00")


@pytest.mark.asyncio
async def test_snapshot_survives_price_row_deletion(session):
    """products.py deletes and recreates all pricing rows on update; the
    recorded cost must not vanish with them."""
    pid = await make_product(session, cost_price="0")
    await session.execute(text("""
        INSERT INTO product_pricing (product_id, unit, cost_price,
                                     retail_price, wholesale_price)
        VALUES (:p, 'each', 25.00, 50, 45)
    """), {"p": str(pid)})
    await session.commit()

    lid = await make_line(session, pid, qty="4", price="50", unit='each')
    await snapshot_line_cost(
        session, line_id=lid, table='sales_order_lines',
        product_id=pid, quantity=4, unit='each')
    await session.commit()

    await session.execute(text("DELETE FROM product_pricing WHERE product_id = :p"),
                          {"p": str(pid)})
    await session.commit()

    row = (await session.execute(text(
        "SELECT unit_cost, cost_total, cost_source FROM sales_order_lines WHERE id = :l"),
        {"l": str(lid)})).first()
    assert Decimal(str(row.unit_cost)) == Decimal("25.000000")
    assert Decimal(str(row.cost_total)) == Decimal("100.00")
    assert row.cost_source == "price_list_unit"


@pytest.mark.asyncio
async def test_duplicate_pricing_rows_do_not_multiply_cost(session):
    """Regression: the live join fanned out on duplicate (product_id, unit)
    rows, doubling invoice_cost while invoice_total stayed put -- so the page
    reported more profit than the cash received."""
    pid = await make_product(session, cost_price="0")
    for _ in range(3):
        await session.execute(text("""
            INSERT INTO product_pricing (product_id, unit, cost_price,
                                         retail_price, wholesale_price)
            VALUES (:p, 'each', 30.00, 50, 45)
        """), {"p": str(pid)})
    await session.commit()

    lid = await make_line(session, pid, qty="2", price="50", unit='each')
    cost, _ = await snapshot_line_cost(
        session, line_id=lid, table='sales_order_lines',
        product_id=pid, quantity=2, unit='each')
    await session.commit()

    row = (await session.execute(text(
        "SELECT cost_total FROM sales_order_lines WHERE id = :l"),
        {"l": str(lid)})).first()
    # 2 x 30 = 60, regardless of how many duplicate pricing rows exist.
    assert Decimal(str(row.cost_total)) == Decimal("60.00")


@pytest.mark.asyncio
async def test_snapshot_is_per_warehouse_when_costs_differ(session):
    """Goods bought cheaply into one warehouse should not cost a sale from
    another warehouse at that price."""
    pid = await make_product(session)
    wh_a, wh_b = uuid.uuid4(), uuid.uuid4()
    await add_intake(session, pid, 100, "10.00", warehouse_id=wh_a)
    await add_intake(session, pid, 100, "50.00", warehouse_id=wh_b)

    cost_a, src_a = await resolve_unit_cost(
        session, product_id=pid, warehouse_id=wh_a)
    cost_b, _ = await resolve_unit_cost(
        session, product_id=pid, warehouse_id=wh_b)

    assert cost_a == Decimal("10.000000")
    assert cost_b == Decimal("50.000000")
    assert src_a == "wac_warehouse"

    # With no warehouse given, fall back to the global average: 30.
    cost_global, src_global = await resolve_unit_cost(session, product_id=pid)
    assert cost_global == Decimal("30.000000")
    assert src_global == "wac_global"
