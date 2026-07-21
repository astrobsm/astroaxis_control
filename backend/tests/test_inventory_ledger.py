"""Invariant tests for the stock ledger.

These require a real PostgreSQL database: the guarantees under test are
SELECT ... FOR UPDATE row locking, INSERT ... ON CONFLICT against a partial
unique index, and CHECK constraints. SQLite cannot express any of them, so a
SQLite run would pass while proving nothing.

Run against a THROWAWAY database -- the fixtures create and drop schema:

    export TEST_DATABASE_URL='postgresql+asyncpg://postgres:pw@localhost:5432/astro_test'
    cd backend && pytest tests/test_inventory_ledger.py -v

Each test names the specific production defect it prevents from returning.
"""
import asyncio
import os
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.services.inventory import (
    apply_stock_movement, transfer_stock, get_available_stock)

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(TEST_DB, future=True)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await conn.execute(text("DROP TABLE IF EXISTS stock_movements CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS stock_levels CASCADE"))
        await conn.execute(text("""
            CREATE TABLE stock_levels (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                warehouse_id UUID NOT NULL,
                product_id UUID,
                raw_material_id UUID,
                current_stock NUMERIC(18,6) NOT NULL DEFAULT 0,
                reserved_stock NUMERIC(18,6) DEFAULT 0,
                min_stock NUMERIC(18,6) DEFAULT 0,
                max_stock NUMERIC(18,6) DEFAULT 0,
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                CONSTRAINT ck_stock_levels_one_item_type
                    CHECK ((product_id IS NULL) <> (raw_material_id IS NULL))
            )
        """))
        await conn.execute(text("""
            CREATE UNIQUE INDEX uq_stock_levels_wh_product
                ON stock_levels (warehouse_id, product_id)
             WHERE product_id IS NOT NULL
        """))
        await conn.execute(text("""
            CREATE UNIQUE INDEX uq_stock_levels_wh_raw_material
                ON stock_levels (warehouse_id, raw_material_id)
             WHERE raw_material_id IS NOT NULL
        """))
        await conn.execute(text("""
            CREATE TABLE stock_movements (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                warehouse_id UUID NOT NULL,
                product_id UUID,
                raw_material_id UUID,
                movement_type VARCHAR(32) NOT NULL,
                quantity NUMERIC(18,6) NOT NULL,
                unit_cost NUMERIC(18,6),
                reference VARCHAR(255),
                notes TEXT,
                created_by UUID,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                CONSTRAINT ck_stock_movements_one_item_type
                    CHECK ((product_id IS NULL) <> (raw_material_id IS NULL)),
                CONSTRAINT ck_stock_movements_positive_qty
                    CHECK (quantity >= 0)
            )
        """))
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine):
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s


@pytest.fixture
def wh():
    return uuid.uuid4()


@pytest.fixture
def prod():
    return uuid.uuid4()


async def _count_movements(session, warehouse_id):
    return (await session.execute(
        text("SELECT COUNT(*) FROM stock_movements WHERE warehouse_id = :w"),
        {"w": str(warehouse_id)})).scalar()


# ---------------------------------------------------------------------------
# Invariant 1: a balance never changes without a matching ledger row
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_movement_and_balance_are_written_together(session, wh, prod):
    await apply_stock_movement(
        session, warehouse_id=wh, product_id=prod,
        movement_type="IN", quantity=100)
    await session.commit()

    assert await get_available_stock(
        session, warehouse_id=wh, product_id=prod) == Decimal("100")
    assert await _count_movements(session, wh) == 1


@pytest.mark.asyncio
async def test_failed_movement_leaves_no_ledger_row(session, wh, prod):
    """Regression: stock_management wrote a RETURN movement even when it did
    not update the balance, permanently inflating any ledger replay."""
    await apply_stock_movement(
        session, warehouse_id=wh, product_id=prod,
        movement_type="IN", quantity=10)
    await session.commit()

    with pytest.raises(HTTPException):
        await apply_stock_movement(
            session, warehouse_id=wh, product_id=prod,
            movement_type="OUT", quantity=999)
    await session.rollback()

    assert await get_available_stock(
        session, warehouse_id=wh, product_id=prod) == Decimal("10")
    assert await _count_movements(session, wh) == 1


# ---------------------------------------------------------------------------
# Invariant 2: no negative stock
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cannot_oversell(session, wh, prod):
    """Regression: /damaged-product subtracted unconditionally and happily
    reported "remaining_stock": -90."""
    await apply_stock_movement(
        session, warehouse_id=wh, product_id=prod,
        movement_type="IN", quantity=10)
    await session.commit()

    with pytest.raises(HTTPException) as exc:
        await apply_stock_movement(
            session, warehouse_id=wh, product_id=prod,
            movement_type="DAMAGE", quantity=100)
    assert exc.value.status_code == 400
    assert "Insufficient stock" in exc.value.detail


@pytest.mark.asyncio
async def test_negative_allowed_only_when_explicit(session, wh, prod):
    await apply_stock_movement(
        session, warehouse_id=wh, product_id=prod,
        movement_type="OUT", quantity=5, allow_negative=True)
    await session.commit()
    assert await get_available_stock(
        session, warehouse_id=wh, product_id=prod) == Decimal("-5")


# ---------------------------------------------------------------------------
# Invariant 3: positive magnitudes only, known movement types only
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_negative_quantity_rejected(session, wh, prod):
    """Regression: production.py stored OUT as quantity=-required_qty while
    every other writer stored a positive magnitude, so SUM() over a
    movement_type returned near-zero instead of the true total."""
    with pytest.raises(HTTPException) as exc:
        await apply_stock_movement(
            session, warehouse_id=wh, product_id=prod,
            movement_type="OUT", quantity=-50)
    assert "positive magnitude" in exc.value.detail


@pytest.mark.asyncio
async def test_unknown_movement_type_rejected(session, wh, prod):
    """The old if/elif chain silently changed nothing and returned success."""
    with pytest.raises(HTTPException) as exc:
        await apply_stock_movement(
            session, warehouse_id=wh, product_id=prod,
            movement_type="TELEPORT", quantity=5)
    assert "Unknown movement_type" in exc.value.detail


@pytest.mark.asyncio
async def test_exactly_one_item_type_required(session, wh, prod):
    with pytest.raises(HTTPException):
        await apply_stock_movement(
            session, warehouse_id=wh, movement_type="IN", quantity=1)
    with pytest.raises(HTTPException):
        await apply_stock_movement(
            session, warehouse_id=wh, product_id=prod,
            raw_material_id=uuid.uuid4(), movement_type="IN", quantity=1)


# ---------------------------------------------------------------------------
# Invariant 4: concurrency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_deductions_do_not_oversell(engine, wh, prod):
    """Regression: read-modify-write with no row lock lost updates, so
    concurrent deductions oversold.

    NOTE ON TEST STRENGTH: an earlier version of this test used only two
    concurrent deductions and passed against the ORIGINAL broken code -- the
    two tasks never actually interleaved, so it proved nothing. This version
    uses 20 concurrent deductions of 10 against 100 units, which was verified
    to make the old implementation sell 200 units from 100 available (leaving
    a nonsensical final balance of 30). Do not weaken the concurrency here.
    """
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        await apply_stock_movement(
            s, warehouse_id=wh, product_id=prod,
            movement_type="IN", quantity=100)
        await s.commit()

    async def deduct():
        async with maker() as s:
            try:
                await apply_stock_movement(
                    s, warehouse_id=wh, product_id=prod,
                    movement_type="OUT", quantity=10)
                await s.commit()
                return 1
            except HTTPException:
                await s.rollback()
                return 0

    succeeded = sum(await asyncio.gather(*[deduct() for _ in range(20)]))

    async with maker() as s:
        final = await get_available_stock(s, warehouse_id=wh, product_id=prod)
        movements = await _count_movements(s, wh)

    # At most 10 deductions of 10 can come out of 100 units.
    assert succeeded == 10, f"{succeeded} deductions succeeded; expected 10"
    assert final == Decimal("0"), f"final balance {final}, expected 0"
    assert final >= 0, "stock went negative under concurrency"
    # Ledger must contain exactly the movements that really happened.
    assert movements == 1 + succeeded


@pytest.mark.asyncio
async def test_row_lock_actually_blocks_concurrent_writer(engine, wh, prod):
    """Directly assert the FOR UPDATE lock, rather than inferring it.

    While transaction A holds the balance row, B must block. If B completed
    immediately, the lock is not being taken and every other concurrency
    guarantee here is accidental.
    """
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        await apply_stock_movement(
            s, warehouse_id=wh, product_id=prod,
            movement_type="IN", quantity=100)
        await s.commit()

    a = maker()
    await a.begin()
    # A takes the lock and holds it (no commit yet).
    await apply_stock_movement(
        a, warehouse_id=wh, product_id=prod,
        movement_type="OUT", quantity=10)

    async def b_writes():
        async with maker() as s:
            await apply_stock_movement(
                s, warehouse_id=wh, product_id=prod,
                movement_type="OUT", quantity=10)
            await s.commit()

    task = asyncio.create_task(b_writes())
    done, _ = await asyncio.wait({task}, timeout=1.5)
    assert not done, "second writer did not block; FOR UPDATE is not engaged"

    await a.commit()
    await a.close()
    await asyncio.wait_for(task, timeout=10)

    async with maker() as s:
        assert await get_available_stock(
            s, warehouse_id=wh, product_id=prod) == Decimal("80")


@pytest.mark.asyncio
async def test_concurrent_first_writes_create_one_balance_row(engine, wh, prod):
    """Regression: check-then-insert with no unique constraint let two
    concurrent first-time writes each create a stock_levels row. Reads then
    used .first() with no ORDER BY, so reads and writes hit different rows."""
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def add(qty):
        async with maker() as s:
            await apply_stock_movement(
                s, warehouse_id=wh, product_id=prod,
                movement_type="IN", quantity=qty)
            await s.commit()

    await asyncio.gather(add(100), add(100))

    async with maker() as s:
        rows = (await s.execute(
            text("""SELECT COUNT(*) FROM stock_levels
                     WHERE warehouse_id = :w AND product_id = :p"""),
            {"w": str(wh), "p": str(prod)})).scalar()
        total = await get_available_stock(
            s, warehouse_id=wh, product_id=prod)

    assert rows == 1, f"expected one balance row, found {rows}"
    assert total == Decimal("200")


# ---------------------------------------------------------------------------
# Invariant 5: transfers move both legs or neither
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transfer_moves_both_legs(session, prod):
    src, dst = uuid.uuid4(), uuid.uuid4()
    await apply_stock_movement(
        session, warehouse_id=src, product_id=prod,
        movement_type="IN", quantity=100)
    await session.commit()

    await transfer_stock(
        session, from_warehouse_id=src, to_warehouse_id=dst,
        product_id=prod, quantity=30, reference="TRF-1")
    await session.commit()

    assert await get_available_stock(
        session, warehouse_id=src, product_id=prod) == Decimal("70")
    assert await get_available_stock(
        session, warehouse_id=dst, product_id=prod) == Decimal("30")


@pytest.mark.asyncio
async def test_transfer_rolls_back_entirely_when_source_short(session, prod):
    """Regression: stock.py accepted movement_type='TRANSFER' with a single
    warehouse_id -- it decremented the source and incremented nothing, so
    total inventory silently shrank."""
    src, dst = uuid.uuid4(), uuid.uuid4()
    await apply_stock_movement(
        session, warehouse_id=src, product_id=prod,
        movement_type="IN", quantity=10)
    await session.commit()

    with pytest.raises(HTTPException):
        await transfer_stock(
            session, from_warehouse_id=src, to_warehouse_id=dst,
            product_id=prod, quantity=999)
    await session.rollback()

    assert await get_available_stock(
        session, warehouse_id=src, product_id=prod) == Decimal("10")
    assert await get_available_stock(
        session, warehouse_id=dst, product_id=prod) == Decimal("0")


@pytest.mark.asyncio
async def test_transfer_to_same_warehouse_rejected(session, prod):
    wh = uuid.uuid4()
    with pytest.raises(HTTPException):
        await transfer_stock(
            session, from_warehouse_id=wh, to_warehouse_id=wh,
            product_id=prod, quantity=1)


# ---------------------------------------------------------------------------
# Ledger replay must reconcile to the balance -- the property the whole
# traceability requirement rests on.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ledger_replay_reconciles_to_balance(session, wh, prod):
    for mt, q in [("IN", 500), ("OUT", 120), ("RETURN", 20),
                  ("DAMAGE", 15), ("PRODUCTION_IN", 60), ("OUT", 45)]:
        await apply_stock_movement(
            session, warehouse_id=wh, product_id=prod,
            movement_type=mt, quantity=q)
    await session.commit()

    replayed = (await session.execute(text("""
        SELECT COALESCE(SUM(
            CASE WHEN movement_type IN
                ('IN','RETURN','TRANSFER_IN','PRODUCTION_IN',
                 'DAMAGE_TRANSFER_IN','ADJUST_IN')
                 THEN quantity ELSE -quantity END), 0)
          FROM stock_movements
         WHERE warehouse_id = :w AND product_id = :p
    """), {"w": str(wh), "p": str(prod)})).scalar()

    balance = await get_available_stock(
        session, warehouse_id=wh, product_id=prod)

    assert Decimal(str(replayed)) == balance == Decimal("400")
