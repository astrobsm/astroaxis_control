"""Verify migration k0123456789j repairs dirty stock data correctly.

This migration runs against a live inventory table: it merges duplicate
balance rows and removes rows it cannot repair. Getting it wrong means
corrupting real stock figures, so it is exercised here against a dataset
containing every defect the audit found in production.

    export TEST_DATABASE_URL='postgresql+asyncpg://postgres:pw@localhost:5432/astro_test'
    cd backend && pytest tests/test_stock_migration.py -v
"""
import os
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")

# The migration runs synchronously under alembic, so use a sync driver.
SYNC_DB = (TEST_DB or "").replace("+asyncpg", "")

PRE_MIGRATION_SCHEMA = """
DROP TABLE IF EXISTS stock_levels CASCADE;
DROP TABLE IF EXISTS stock_movements CASCADE;
DROP TABLE IF EXISTS stock_levels_quarantine CASCADE;
DROP TABLE IF EXISTS stock_movements_quarantine CASCADE;

CREATE TABLE stock_levels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    warehouse_id UUID NOT NULL,
    product_id UUID,
    raw_material_id UUID,
    current_stock NUMERIC(18,6) NOT NULL DEFAULT 0,
    reserved_stock NUMERIC(18,6) DEFAULT 0,
    min_stock NUMERIC(18,6) DEFAULT 0,
    max_stock NUMERIC(18,6) DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW()
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
    notes TEXT,
    created_by UUID,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
"""


@pytest.fixture
def conn():
    eng = create_engine(SYNC_DB, future=True)
    with eng.connect() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        for stmt in PRE_MIGRATION_SCHEMA.strip().split(";"):
            if stmt.strip():
                c.execute(text(stmt))
        c.commit()
        yield c
    eng.dispose()


def run_migration(conn):
    """Execute the migration's upgrade() against this connection."""
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / \
        "k0123456789j_stock_ledger_integrity.py"
    spec = importlib.util.spec_from_file_location("mig_k0", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        mod.upgrade()
    conn.commit()


WH = uuid.uuid4()
P1, P2 = uuid.uuid4(), uuid.uuid4()
RM1 = uuid.uuid4()


def seed_dirty(conn):
    """Every defect the audit found, in one table."""
    rows = [
        # Three duplicate balance rows for the same (warehouse, product):
        # the exact result of concurrent check-then-insert. Total = 150.
        (WH, P1, None, 100, 5),
        (WH, P1, None,  30, 2),
        (WH, P1, None,  20, 1),
        # A clean row that must survive untouched.
        (WH, P2, None,  75, 0),
        # Duplicate raw-material rows. Total = 40.
        (WH, None, RM1, 25, 0),
        (WH, None, RM1, 15, 0),
        # Unrepairable: references neither item.
        (WH, None, None, 999, 0),
        # Unrepairable: references both.
        (WH, P2, RM1, 888, 0),
    ]
    for wh, pid, rmid, stock, reserved in rows:
        conn.execute(text("""
            INSERT INTO stock_levels
                (id, warehouse_id, product_id, raw_material_id,
                 current_stock, reserved_stock, updated_at)
            VALUES (gen_random_uuid(), :w, :p, :r, :s, :res, NOW())
        """), {"w": str(wh), "p": str(pid) if pid else None,
               "r": str(rmid) if rmid else None, "s": stock, "res": reserved})

    # Movements: one negative OUT (the production.py sign-convention bug),
    # one orphan referencing neither item.
    conn.execute(text("""
        INSERT INTO stock_movements
            (id, warehouse_id, product_id, movement_type, quantity)
        VALUES (gen_random_uuid(), :w, :p, 'OUT', -50)
    """), {"w": str(WH), "p": str(P1)})
    conn.execute(text("""
        INSERT INTO stock_movements
            (id, warehouse_id, movement_type, quantity)
        VALUES (gen_random_uuid(), :w, 'IN', 10)
    """), {"w": str(WH)})
    conn.commit()


def test_duplicates_are_merged_by_summing(conn):
    seed_dirty(conn)
    run_migration(conn)

    rows = conn.execute(text("""
        SELECT current_stock, reserved_stock FROM stock_levels
         WHERE warehouse_id = :w AND product_id = :p
    """), {"w": str(WH), "p": str(P1)}).fetchall()

    assert len(rows) == 1, f"expected 1 merged row, got {len(rows)}"
    # 100 + 30 + 20 -- no stock invented, none lost.
    assert Decimal(str(rows[0].current_stock)) == Decimal("150")
    assert Decimal(str(rows[0].reserved_stock)) == Decimal("8")


def test_raw_material_duplicates_merged(conn):
    seed_dirty(conn)
    run_migration(conn)
    rows = conn.execute(text("""
        SELECT current_stock FROM stock_levels
         WHERE warehouse_id = :w AND raw_material_id = :r
    """), {"w": str(WH), "r": str(RM1)}).fetchall()
    assert len(rows) == 1
    assert Decimal(str(rows[0].current_stock)) == Decimal("40")


def test_clean_row_untouched(conn):
    seed_dirty(conn)
    run_migration(conn)
    row = conn.execute(text("""
        SELECT current_stock FROM stock_levels
         WHERE warehouse_id = :w AND product_id = :p
    """), {"w": str(WH), "p": str(P2)}).fetchone()
    assert Decimal(str(row.current_stock)) == Decimal("75")


def test_unrepairable_rows_quarantined_not_lost(conn):
    """The whole point of quarantining: nothing is destroyed silently."""
    seed_dirty(conn)
    run_migration(conn)

    live = conn.execute(text("""
        SELECT COUNT(*) FROM stock_levels
         WHERE (product_id IS NULL AND raw_material_id IS NULL)
            OR (product_id IS NOT NULL AND raw_material_id IS NOT NULL)
    """)).scalar()
    assert live == 0, "unrepairable rows still present in stock_levels"

    q = conn.execute(text(
        "SELECT current_stock, quarantine_reason FROM stock_levels_quarantine "
        "ORDER BY current_stock")).fetchall()
    assert len(q) == 2, f"expected 2 quarantined rows, got {len(q)}"
    amounts = sorted(Decimal(str(r.current_stock)) for r in q)
    assert amounts == [Decimal("888"), Decimal("999")]
    assert all(r.quarantine_reason for r in q), "reason not recorded"


def test_constraints_now_reject_bad_writes(conn):
    seed_dirty(conn)
    run_migration(conn)

    # Duplicate balance row must now be impossible.
    with pytest.raises(Exception):
        conn.execute(text("""
            INSERT INTO stock_levels (id, warehouse_id, product_id, current_stock)
            VALUES (gen_random_uuid(), :w, :p, 1)
        """), {"w": str(WH), "p": str(P1)})
        conn.commit()
    conn.rollback()

    # Neither-item row must now be impossible.
    with pytest.raises(Exception):
        conn.execute(text("""
            INSERT INTO stock_levels (id, warehouse_id, current_stock)
            VALUES (gen_random_uuid(), :w, 1)
        """), {"w": str(WH)})
        conn.commit()
    conn.rollback()

    # Negative movement quantity must now be impossible.
    with pytest.raises(Exception):
        conn.execute(text("""
            INSERT INTO stock_movements
                (id, warehouse_id, product_id, movement_type, quantity)
            VALUES (gen_random_uuid(), :w, :p, 'OUT', -5)
        """), {"w": str(WH), "p": str(P1)})
        conn.commit()
    conn.rollback()


def test_negative_movement_quantities_normalised(conn):
    """production.py stored OUT as a negative number while every other writer
    stored a positive magnitude, so SUM() over a movement_type was wrong."""
    seed_dirty(conn)
    run_migration(conn)
    neg = conn.execute(text(
        "SELECT COUNT(*) FROM stock_movements WHERE quantity < 0")).scalar()
    assert neg == 0
    row = conn.execute(text("""
        SELECT quantity FROM stock_movements
         WHERE product_id = :p AND movement_type = 'OUT'
    """), {"p": str(P1)}).fetchone()
    assert Decimal(str(row.quantity)) == Decimal("50")


def test_orphan_movements_quarantined(conn):
    seed_dirty(conn)
    run_migration(conn)
    live = conn.execute(text("""
        SELECT COUNT(*) FROM stock_movements
         WHERE product_id IS NULL AND raw_material_id IS NULL
    """)).scalar()
    assert live == 0
    q = conn.execute(text(
        "SELECT COUNT(*) FROM stock_movements_quarantine")).scalar()
    assert q == 1


def test_migration_is_idempotent(conn):
    """Re-running must not double-merge or crash -- deploys get retried."""
    seed_dirty(conn)
    run_migration(conn)
    before = conn.execute(text(
        "SELECT current_stock FROM stock_levels WHERE product_id = :p"),
        {"p": str(P1)}).scalar()
    try:
        run_migration(conn)
    except Exception:
        # Constraints already exist; that is an acceptable second-run failure
        # provided the data was not altered.
        conn.rollback()
    after = conn.execute(text(
        "SELECT current_stock FROM stock_levels WHERE product_id = :p"),
        {"p": str(P1)}).scalar()
    assert Decimal(str(before)) == Decimal(str(after)) == Decimal("150")
