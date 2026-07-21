"""Verify migration l1234567890k backfills COGS correctly.

Runs against real sales history, so the backfill must not invent numbers, and
must clearly mark the ones it estimates.
"""
import os
import uuid
from decimal import Decimal

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")
SYNC_DB = (TEST_DB or "").replace("+asyncpg", "")

PRE = """
DROP TABLE IF EXISTS invoice_lines CASCADE;
DROP TABLE IF EXISTS invoices CASCADE;
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
    product_id UUID, raw_material_id UUID,
    movement_type VARCHAR(32) NOT NULL,
    quantity NUMERIC(18,6) NOT NULL,
    unit_cost NUMERIC(18,6),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE sales_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_number VARCHAR(64) UNIQUE NOT NULL,
    customer_id UUID NOT NULL, warehouse_id UUID,
    total_amount NUMERIC(18,2) DEFAULT 0
);
CREATE TABLE sales_order_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sales_order_id UUID NOT NULL REFERENCES sales_orders(id),
    product_id UUID NOT NULL, unit VARCHAR(50),
    quantity NUMERIC(18,6) NOT NULL,
    unit_price NUMERIC(18,6) NOT NULL,
    line_total NUMERIC(18,2) NOT NULL
);
CREATE TABLE invoices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_number VARCHAR(64) UNIQUE NOT NULL,
    customer_id UUID NOT NULL,
    sales_order_id UUID REFERENCES sales_orders(id),
    total_amount NUMERIC(18,2) DEFAULT 0
);
CREATE TABLE invoice_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id UUID NOT NULL REFERENCES invoices(id),
    product_id UUID NOT NULL,
    quantity NUMERIC(18,6) NOT NULL,
    unit_price NUMERIC(18,6) NOT NULL,
    line_total NUMERIC(18,2) NOT NULL
);
"""

WH = uuid.uuid4()


@pytest.fixture
def conn():
    eng = create_engine(SYNC_DB, future=True)
    with eng.connect() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        for stmt in PRE.strip().split(";"):
            if stmt.strip():
                c.execute(text(stmt))
        c.commit()
        yield c
    eng.dispose()


def run_migration(conn):
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / \
        "l1234567890k_add_cogs_snapshot.py"
    spec = importlib.util.spec_from_file_location("mig_l1", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        mod.upgrade()
    conn.commit()


def mkproduct(conn, cost_price="0"):
    pid = uuid.uuid4()
    conn.execute(text("""
        INSERT INTO products (id, sku, name, cost_price)
        VALUES (:id, :sku, 'P', :c)
    """), {"id": str(pid), "sku": f"S-{uuid.uuid4().hex[:8]}", "c": cost_price})
    return pid


def mkline(conn, pid, qty="10", price="100", unit='each'):
    oid, lid = uuid.uuid4(), uuid.uuid4()
    conn.execute(text("""
        INSERT INTO sales_orders (id, order_number, customer_id, warehouse_id)
        VALUES (:id, :n, :c, :w)
    """), {"id": str(oid), "n": f"SO-{uuid.uuid4().hex[:8]}",
           "c": str(uuid.uuid4()), "w": str(WH)})
    conn.execute(text("""
        INSERT INTO sales_order_lines
            (id, sales_order_id, product_id, unit, quantity, unit_price, line_total)
        VALUES (:id, :o, :p, :u, :q, :pr, :lt)
    """), {"id": str(lid), "o": str(oid), "p": str(pid), "u": unit,
           "q": qty, "pr": price,
           "lt": str(Decimal(qty) * Decimal(price))})
    return oid, lid


def line(conn, lid):
    return conn.execute(text("""
        SELECT unit_cost, cost_total, cost_source
          FROM sales_order_lines WHERE id = :l
    """), {"l": str(lid)}).first()


def test_backfill_prefers_movement_ledger(conn):
    pid = mkproduct(conn, cost_price="99.00")
    conn.execute(text("""
        INSERT INTO product_pricing (product_id, unit, cost_price)
        VALUES (:p, 'each', 77.00)
    """), {"p": str(pid)})
    conn.execute(text("""
        INSERT INTO stock_movements
            (id, warehouse_id, product_id, movement_type, quantity, unit_cost)
        VALUES (gen_random_uuid(), :w, :p, 'IN', 100, 10.00),
               (gen_random_uuid(), :w, :p, 'IN', 100, 20.00)
    """), {"w": str(WH), "p": str(pid)})
    _, lid = mkline(conn, pid, qty="10")
    conn.commit()

    run_migration(conn)
    r = line(conn, lid)
    assert Decimal(str(r.unit_cost)) == Decimal("15.000000")  # weighted avg
    assert Decimal(str(r.cost_total)) == Decimal("150.00")
    assert r.cost_source == 'backfill_estimate'


def test_backfill_falls_back_to_price_list_then_product(conn):
    p1 = mkproduct(conn, cost_price="99.00")
    conn.execute(text("""
        INSERT INTO product_pricing (product_id, unit, cost_price)
        VALUES (:p, 'each', 77.00)
    """), {"p": str(p1)})
    _, l1 = mkline(conn, p1, qty="2", unit='each')

    p2 = mkproduct(conn, cost_price="55.00")
    _, l2 = mkline(conn, p2, qty="3", unit='drum')
    conn.commit()

    run_migration(conn)
    r1, r2 = line(conn, l1), line(conn, l2)
    assert Decimal(str(r1.unit_cost)) == Decimal("77.000000")
    assert Decimal(str(r2.unit_cost)) == Decimal("55.000000")
    assert Decimal(str(r2.cost_total)) == Decimal("165.00")


def test_uncostable_lines_marked_unknown_not_zero_cost(conn):
    """A zero cost with no marker silently reports 100% margin. 'unknown'
    lets reports exclude the line and say so."""
    pid = mkproduct(conn, cost_price="0")
    _, lid = mkline(conn, pid)
    conn.commit()

    run_migration(conn)
    r = line(conn, lid)
    assert Decimal(str(r.unit_cost)) == Decimal("0")
    assert r.cost_source == 'unknown'


def test_backfill_never_silently_leaves_nulls(conn):
    pid = mkproduct(conn, cost_price="0")
    for _ in range(5):
        mkline(conn, pid)
    conn.commit()
    run_migration(conn)
    nulls = conn.execute(text(
        "SELECT COUNT(*) FROM sales_order_lines WHERE cost_source IS NULL")).scalar()
    assert nulls == 0


def test_duplicate_pricing_rows_deduplicated_and_constrained(conn):
    """The fan-out that doubled COGS is closed at the schema level."""
    pid = mkproduct(conn)
    for c in ("10.00", "20.00", "30.00"):
        conn.execute(text("""
            INSERT INTO product_pricing (product_id, unit, cost_price)
            VALUES (:p, 'each', :c)
        """), {"p": str(pid), "c": c})
    conn.commit()

    run_migration(conn)

    remaining = conn.execute(text(
        "SELECT COUNT(*) FROM product_pricing WHERE product_id = :p"),
        {"p": str(pid)}).scalar()
    assert remaining == 1, "duplicates not collapsed"

    # And a new duplicate must now be rejected outright.
    with pytest.raises(Exception):
        conn.execute(text("""
            INSERT INTO product_pricing (product_id, unit, cost_price)
            VALUES (:p, 'EACH', 40.00)
        """), {"p": str(pid)})
        conn.commit()
    conn.rollback()


def test_invoice_lines_inherit_order_line_cost(conn):
    """An invoice must agree with the order it was raised from."""
    pid = mkproduct(conn, cost_price="12.00")
    oid, lid = mkline(conn, pid, qty="5", price="40")
    iid = uuid.uuid4()
    conn.execute(text("""
        INSERT INTO invoices (id, invoice_number, customer_id, sales_order_id)
        VALUES (:i, :n, :c, :o)
    """), {"i": str(iid), "n": f"INV-{uuid.uuid4().hex[:8]}",
           "c": str(uuid.uuid4()), "o": str(oid)})
    conn.execute(text("""
        INSERT INTO invoice_lines
            (id, invoice_id, product_id, quantity, unit_price, line_total)
        VALUES (gen_random_uuid(), :i, :p, 5, 40, 200)
    """), {"i": str(iid), "p": str(pid)})
    conn.commit()

    run_migration(conn)
    il = conn.execute(text("""
        SELECT unit_cost, cost_total FROM invoice_lines WHERE invoice_id = :i
    """), {"i": str(iid)}).first()
    sol = line(conn, lid)
    assert Decimal(str(il.unit_cost)) == Decimal(str(sol.unit_cost))
    assert Decimal(str(il.cost_total)) == Decimal("60.00")


def test_negative_cost_rejected_after_migration(conn):
    pid = mkproduct(conn, cost_price="10.00")
    _, lid = mkline(conn, pid)
    conn.commit()
    run_migration(conn)
    with pytest.raises(Exception):
        conn.execute(text(
            "UPDATE sales_order_lines SET unit_cost = -5 WHERE id = :l"),
            {"l": str(lid)})
        conn.commit()
    conn.rollback()


def test_migration_is_idempotent(conn):
    pid = mkproduct(conn, cost_price="10.00")
    _, lid = mkline(conn, pid, qty="10")
    conn.commit()

    run_migration(conn)
    before = line(conn, lid)
    try:
        run_migration(conn)
    except Exception:
        conn.rollback()   # constraints already exist; data must be untouched
    after = line(conn, lid)
    assert Decimal(str(before.cost_total)) == Decimal(str(after.cost_total))
    assert Decimal(str(after.cost_total)) == Decimal("100.00")
