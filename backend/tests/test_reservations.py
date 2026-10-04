"""Stock reservations.

Two behaviours carry the whole module:

  * two orders for the last unit must not both succeed;
  * reserved_stock must never disagree with the reservations behind it.

Everything else is detail.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.services import reservations as svc

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")

SCHEMA = """
DROP TABLE IF EXISTS inventory_reservations CASCADE;
DROP TABLE IF EXISTS stock_movements CASCADE;
DROP TABLE IF EXISTS stock_levels CASCADE;
DROP TABLE IF EXISTS app_setting_changes CASCADE;
DROP TABLE IF EXISTS app_settings CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS warehouses CASCADE;
DROP TABLE IF EXISTS users CASCADE;

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(100) UNIQUE, email VARCHAR(255) UNIQUE NOT NULL,
    full_name VARCHAR(255), role VARCHAR(50) DEFAULT 'sales_staff',
    hashed_password VARCHAR(255) DEFAULT 'x'
);
CREATE TABLE warehouses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL
);
CREATE TABLE products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sku VARCHAR(64) UNIQUE NOT NULL, name VARCHAR(255) NOT NULL
);
-- NOT dropped and only created if absent. These suites share one database
-- and no other test file creates raw_materials, so this file replacing it
-- with a three-column stub destroyed the real one for test_bom_cost. A test
-- file should not drop a table it does not own.
CREATE TABLE IF NOT EXISTS raw_materials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sku VARCHAR(64) UNIQUE, name VARCHAR(255) NOT NULL
);
CREATE TABLE stock_levels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    warehouse_id UUID NOT NULL REFERENCES warehouses(id),
    product_id UUID REFERENCES products(id),
    raw_material_id UUID REFERENCES raw_materials(id),
    current_stock NUMERIC(18,6) DEFAULT 0,
    reserved_stock NUMERIC(18,6) DEFAULT 0,
    min_stock NUMERIC(18,6) DEFAULT 0, max_stock NUMERIC(18,6) DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX uq_sl_product ON stock_levels (warehouse_id, product_id)
    WHERE product_id IS NOT NULL;
CREATE UNIQUE INDEX uq_sl_rm ON stock_levels (warehouse_id, raw_material_id)
    WHERE raw_material_id IS NOT NULL;
CREATE TABLE stock_movements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    warehouse_id UUID REFERENCES warehouses(id),
    product_id UUID REFERENCES products(id),
    raw_material_id UUID REFERENCES raw_materials(id),
    movement_type VARCHAR(32) NOT NULL,
    quantity NUMERIC(18,6) NOT NULL,
    reference VARCHAR(255), notes TEXT, batch_id UUID,
    unit_cost NUMERIC(18,6), created_by UUID,
    movement_date TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
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
        _apply(c, "t0123456789s_inventory_reservations.py")
        c.commit()
    seng.dispose()
    eng = create_async_engine(TEST_DB, future=True)
    maker = sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await eng.dispose()


async def _warehouse(db):
    wid = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO warehouses (id, name) VALUES (:i, 'Main')"),
        {"i": str(wid)})
    return wid


async def _product(db, name="Hera Wound Gel"):
    pid = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO products (id, sku, name) VALUES (:i, :s, :n)"),
        {"i": str(pid), "s": uuid.uuid4().hex[:8].upper(), "n": name})
    return pid


async def _stock(db, warehouse, product, qty):
    await db.execute(text("""
        INSERT INTO stock_levels (warehouse_id, product_id, current_stock)
        VALUES (:w, :p, :q)
    """), {"w": str(warehouse), "p": str(product), "q": qty})


async def _level(db, warehouse, product):
    return (await db.execute(text(
        "SELECT current_stock, reserved_stock FROM stock_levels "
        "WHERE warehouse_id = :w AND product_id = :p"),
        {"w": str(warehouse), "p": str(product)})).mappings().first()


# ---------------------------------------------------------------------------
# available = on hand - reserved
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_available_is_on_hand_minus_reserved(db):
    w = await _warehouse(db)
    p = await _product(db)
    await _stock(db, w, p, 100)

    await svc.reserve(db, warehouse_id=w, product_id=p, quantity=30,
                      reference_type="SALES_ORDER")
    result = await svc.available_stock(db, warehouse_id=w, product_id=p)
    assert result["on_hand"] == Decimal("100")
    assert result["reserved"] == Decimal("30")
    assert result["available"] == Decimal("70")


@pytest.mark.asyncio
async def test_an_item_never_stocked_is_zero_not_an_error(db):
    w = await _warehouse(db)
    p = await _product(db)
    result = await svc.available_stock(db, warehouse_id=w, product_id=p)
    assert result["available"] == Decimal("0")


@pytest.mark.asyncio
async def test_reserving_does_not_move_stock(db):
    """The goods are still on the shelf and still on the balance sheet."""
    w = await _warehouse(db)
    p = await _product(db)
    await _stock(db, w, p, 100)
    await svc.reserve(db, warehouse_id=w, product_id=p, quantity=40,
                      reference_type="SALES_ORDER")

    level = await _level(db, w, p)
    assert float(level["current_stock"]) == 100.0
    assert float(level["reserved_stock"]) == 40.0

    moves = (await db.execute(text(
        "SELECT COUNT(*) FROM stock_movements"))).scalar()
    assert moves == 0, "a reservation is not a movement"


# ---------------------------------------------------------------------------
# Not overselling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reserving_more_than_is_free_is_refused(db):
    w = await _warehouse(db)
    p = await _product(db)
    await _stock(db, w, p, 10)
    with pytest.raises(HTTPException) as e:
        await svc.reserve(db, warehouse_id=w, product_id=p, quantity=11,
                          reference_type="SALES_ORDER")
    assert e.value.status_code == 409
    assert "Only 10 available" in str(e.value.detail)


@pytest.mark.asyncio
async def test_two_orders_cannot_both_take_the_last_unit(db):
    """The check and the write are under one lock, so the second loses."""
    w = await _warehouse(db)
    p = await _product(db)
    await _stock(db, w, p, 1)

    await svc.reserve(db, warehouse_id=w, product_id=p, quantity=1,
                      reference_type="SALES_ORDER")
    with pytest.raises(HTTPException) as e:
        await svc.reserve(db, warehouse_id=w, product_id=p, quantity=1,
                          reference_type="SALES_ORDER")
    assert e.value.status_code == 409
    assert "already held for other orders" in str(e.value.detail)


@pytest.mark.asyncio
async def test_a_refused_reservation_changes_nothing(db):
    w = await _warehouse(db)
    p = await _product(db)
    await _stock(db, w, p, 5)
    with pytest.raises(HTTPException):
        await svc.reserve(db, warehouse_id=w, product_id=p, quantity=50,
                          reference_type="SALES_ORDER")
    level = await _level(db, w, p)
    assert float(level["reserved_stock"]) == 0.0
    n = (await db.execute(text(
        "SELECT COUNT(*) FROM inventory_reservations"))).scalar()
    assert n == 0


@pytest.mark.asyncio
async def test_zero_or_negative_quantities_are_refused(db):
    w = await _warehouse(db)
    p = await _product(db)
    await _stock(db, w, p, 10)
    for q in (0, -5):
        with pytest.raises(HTTPException) as e:
            await svc.reserve(db, warehouse_id=w, product_id=p, quantity=q,
                              reference_type="SALES_ORDER")
        assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_a_reservation_is_for_one_kind_of_item(db):
    w = await _warehouse(db)
    p = await _product(db)
    with pytest.raises(HTTPException) as e:
        await svc.reserve(db, warehouse_id=w, product_id=p,
                          raw_material_id=uuid.uuid4(), quantity=1,
                          reference_type="SALES_ORDER")
    assert e.value.status_code == 400


# ---------------------------------------------------------------------------
# Releasing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_releasing_makes_the_stock_available_again(db):
    w = await _warehouse(db)
    p = await _product(db)
    await _stock(db, w, p, 100)
    r = await svc.reserve(db, warehouse_id=w, product_id=p, quantity=40,
                          reference_type="SALES_ORDER")

    await svc.release(db, reservation_id=uuid.UUID(r["reservation_id"]),
                      reason="Order cancelled")
    result = await svc.available_stock(db, warehouse_id=w, product_id=p)
    assert result["available"] == Decimal("100")
    assert result["reserved"] == Decimal("0")


@pytest.mark.asyncio
async def test_releasing_needs_a_reason(db):
    w = await _warehouse(db)
    p = await _product(db)
    await _stock(db, w, p, 10)
    r = await svc.reserve(db, warehouse_id=w, product_id=p, quantity=1,
                          reference_type="SALES_ORDER")
    with pytest.raises(HTTPException) as e:
        await svc.release(db, reservation_id=uuid.UUID(r["reservation_id"]),
                          reason="")
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_releasing_twice_is_refused(db):
    w = await _warehouse(db)
    p = await _product(db)
    await _stock(db, w, p, 10)
    r = await svc.reserve(db, warehouse_id=w, product_id=p, quantity=5,
                          reference_type="SALES_ORDER")
    rid = uuid.UUID(r["reservation_id"])
    await svc.release(db, reservation_id=rid, reason="Cancelled")
    with pytest.raises(HTTPException) as e:
        await svc.release(db, reservation_id=rid, reason="Again")
    assert e.value.status_code == 400
    assert "already released" in str(e.value.detail)


@pytest.mark.asyncio
async def test_cancelling_an_order_frees_all_of_its_lines(db):
    """Not the one somebody happened to click."""
    w = await _warehouse(db)
    a, b = await _product(db, "A"), await _product(db, "B")
    await _stock(db, w, a, 100)
    await _stock(db, w, b, 100)
    order = uuid.uuid4()
    for p in (a, b):
        await svc.reserve(db, warehouse_id=w, product_id=p, quantity=10,
                          reference_type="SALES_ORDER", reference_id=order)

    result = await svc.release_for_reference(
        db, reference_type="SALES_ORDER", reference_id=order,
        reason="Order cancelled")
    assert result["released"] == 2
    for p in (a, b):
        assert (await svc.available_stock(
            db, warehouse_id=w, product_id=p))["reserved"] == Decimal("0")


@pytest.mark.asyncio
async def test_an_expired_hold_is_given_back(db):
    """An abandoned basket must not hold stock forever."""
    w = await _warehouse(db)
    p = await _product(db)
    await _stock(db, w, p, 100)
    r = await svc.reserve(db, warehouse_id=w, product_id=p, quantity=25,
                          reference_type="QUOTATION")
    await db.execute(text(
        "UPDATE inventory_reservations SET expires_at = NOW() - INTERVAL '1 hour' "
        "WHERE id = :i"), {"i": r["reservation_id"]})

    result = await svc.release_expired(db)
    assert result["released"] == 1
    assert (await svc.available_stock(
        db, warehouse_id=w, product_id=p))["available"] == Decimal("100")


@pytest.mark.asyncio
async def test_a_live_hold_is_not_released_early(db):
    w = await _warehouse(db)
    p = await _product(db)
    await _stock(db, w, p, 100)
    await svc.reserve(db, warehouse_id=w, product_id=p, quantity=25,
                      reference_type="SALES_ORDER")
    assert (await svc.release_expired(db))["released"] == 0


@pytest.mark.asyncio
async def test_the_database_refuses_a_reservation_with_no_expiry(db):
    w = await _warehouse(db)
    p = await _product(db)
    with pytest.raises(Exception) as e:
        await db.execute(text("""
            INSERT INTO inventory_reservations
                (warehouse_id, product_id, quantity, reference_type)
            VALUES (:w, :p, 1, 'SALES_ORDER')
        """), {"w": str(w), "p": str(p)})
    assert "expires_at" in str(e.value)


# ---------------------------------------------------------------------------
# Consuming
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_consuming_moves_the_stock_and_clears_the_hold(db):
    w = await _warehouse(db)
    p = await _product(db)
    await _stock(db, w, p, 100)
    r = await svc.reserve(db, warehouse_id=w, product_id=p, quantity=30,
                          reference_type="SALES_ORDER",
                          reference_label="SO-1")

    await svc.consume(db, reservation_id=uuid.UUID(r["reservation_id"]))

    level = await _level(db, w, p)
    assert float(level["current_stock"]) == 70.0, "the stock actually left"
    assert float(level["reserved_stock"]) == 0.0, "and the hold is gone"

    move = (await db.execute(text(
        "SELECT movement_type, quantity FROM stock_movements"))).mappings().first()
    assert move["movement_type"] == "OUT"
    assert float(move["quantity"]) == 30.0


@pytest.mark.asyncio
async def test_consuming_twice_is_refused(db):
    w = await _warehouse(db)
    p = await _product(db)
    await _stock(db, w, p, 100)
    r = await svc.reserve(db, warehouse_id=w, product_id=p, quantity=10,
                          reference_type="SALES_ORDER")
    rid = uuid.UUID(r["reservation_id"])
    await svc.consume(db, reservation_id=rid)
    with pytest.raises(HTTPException) as e:
        await svc.consume(db, reservation_id=rid)
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_a_consumed_reservation_cannot_be_released(db):
    """Releasing after the goods left would make them available again."""
    w = await _warehouse(db)
    p = await _product(db)
    await _stock(db, w, p, 50)
    r = await svc.reserve(db, warehouse_id=w, product_id=p, quantity=10,
                          reference_type="SALES_ORDER")
    rid = uuid.UUID(r["reservation_id"])
    await svc.consume(db, reservation_id=rid)
    with pytest.raises(HTTPException) as e:
        await svc.release(db, reservation_id=rid, reason="Changed my mind")
    assert e.value.status_code == 400
    assert float((await _level(db, w, p))["current_stock"]) == 40.0


# ---------------------------------------------------------------------------
# Inventory's trial balance
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_aggregate_agrees_with_the_reservations(db):
    w = await _warehouse(db)
    a, b = await _product(db, "A"), await _product(db, "B")
    await _stock(db, w, a, 100)
    await _stock(db, w, b, 60)

    r1 = await svc.reserve(db, warehouse_id=w, product_id=a, quantity=10,
                           reference_type="SALES_ORDER")
    await svc.reserve(db, warehouse_id=w, product_id=a, quantity=15,
                      reference_type="SALES_ORDER")
    await svc.reserve(db, warehouse_id=w, product_id=b, quantity=5,
                      reference_type="PRODUCTION_ORDER")
    await svc.release(db, reservation_id=uuid.UUID(r1["reservation_id"]),
                      reason="Cancelled")

    result = await svc.reconcile(db)
    assert result["balanced"] is True, result["discrepancies"]
    assert (await svc.available_stock(
        db, warehouse_id=w, product_id=a))["reserved"] == Decimal("15")


@pytest.mark.asyncio
async def test_the_reconciliation_notices_a_drift(db):
    """If the aggregate is ever corrupted, something has to say so."""
    w = await _warehouse(db)
    p = await _product(db)
    await _stock(db, w, p, 100)
    await svc.reserve(db, warehouse_id=w, product_id=p, quantity=20,
                      reference_type="SALES_ORDER")

    await db.execute(text(
        "UPDATE stock_levels SET reserved_stock = 999 WHERE product_id = :p"),
        {"p": str(p)})

    result = await svc.reconcile(db)
    assert result["balanced"] is False
    assert result["discrepancies"][0]["reserved_stock"] == 999.0
    assert result["discrepancies"][0]["live_holds"] == 20.0


@pytest.mark.asyncio
async def test_consuming_leaves_the_books_balanced(db):
    w = await _warehouse(db)
    p = await _product(db)
    await _stock(db, w, p, 100)
    r = await svc.reserve(db, warehouse_id=w, product_id=p, quantity=30,
                          reference_type="SALES_ORDER")
    await svc.consume(db, reservation_id=uuid.UUID(r["reservation_id"]))
    assert (await svc.reconcile(db))["balanced"] is True
