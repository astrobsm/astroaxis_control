"""Verify migration n3456789012m against existing procurement data.

The risky part is the supplier backfill: it collapses free-text vendor names
into a master and converts historical paid_amount totals into payment rows.
Getting either wrong misstates what the business owes.
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

# Pre-migration shape: procurement tables as the runtime bootstrap made them,
# with no suppliers table and no payment rows.
PRE = """
DROP TABLE IF EXISTS supplier_payments CASCADE;
DROP TABLE IF EXISTS expense_records CASCADE;
DROP TABLE IF EXISTS purchase_invoices CASCADE;
DROP TABLE IF EXISTS purchase_order_items CASCADE;
DROP TABLE IF EXISTS purchase_orders CASCADE;
DROP TABLE IF EXISTS purchase_request_items CASCADE;
DROP TABLE IF EXISTS purchase_requests CASCADE;
DROP TABLE IF EXISTS suppliers CASCADE;
DROP TABLE IF EXISTS warehouses CASCADE;

CREATE TABLE warehouses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(32) UNIQUE NOT NULL, name VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE
);
CREATE TABLE purchase_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_number VARCHAR(64) UNIQUE NOT NULL,
    requested_by VARCHAR(255) NOT NULL,
    category VARCHAR(50) NOT NULL DEFAULT 'general',
    priority VARCHAR(20) NOT NULL DEFAULT 'normal',
    status VARCHAR(30) NOT NULL DEFAULT 'submitted',
    title VARCHAR(255) NOT NULL,
    total_estimated_cost NUMERIC(18,2) DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE purchase_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    po_number VARCHAR(64) UNIQUE NOT NULL,
    request_id UUID REFERENCES purchase_requests(id),
    vendor_name VARCHAR(255) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'draft',
    order_date TIMESTAMPTZ DEFAULT NOW(),
    total_amount NUMERIC(18,2) DEFAULT 0,
    paid_amount NUMERIC(18,2) DEFAULT 0,
    payment_status VARCHAR(30) DEFAULT 'unpaid',
    payment_method VARCHAR(50), payment_reference VARCHAR(255),
    payment_date TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE purchase_order_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    po_id UUID NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
    item_type VARCHAR(50) NOT NULL DEFAULT 'general',
    item_name VARCHAR(255) NOT NULL, item_id UUID,
    quantity NUMERIC(18,6) NOT NULL DEFAULT 1,
    unit_cost NUMERIC(18,2) DEFAULT 0,
    line_total NUMERIC(18,2) DEFAULT 0,
    received_qty NUMERIC(18,6) DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE purchase_invoices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_number VARCHAR(100) NOT NULL,
    po_id UUID REFERENCES purchase_orders(id),
    vendor_name VARCHAR(255) NOT NULL,
    invoice_date DATE NOT NULL DEFAULT CURRENT_DATE,
    total_amount NUMERIC(18,2) DEFAULT 0,
    paid_amount NUMERIC(18,2) DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE expense_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    expense_number VARCHAR(64) UNIQUE NOT NULL,
    category VARCHAR(50) NOT NULL, description TEXT NOT NULL,
    amount NUMERIC(18,2) NOT NULL,
    payment_date DATE NOT NULL DEFAULT CURRENT_DATE,
    po_id UUID REFERENCES purchase_orders(id),
    purchase_invoice_id UUID REFERENCES purchase_invoices(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
"""


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
        "n3456789012m_procurement_ap.py"
    spec = importlib.util.spec_from_file_location("mig_n", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with Operations.context(MigrationContext.configure(conn)):
        mod.upgrade()
    conn.commit()


def mkpo(conn, vendor, total="1000.00", paid="0", status="ordered"):
    pid = uuid.uuid4()
    conn.execute(text("""
        INSERT INTO purchase_orders
            (id, po_number, vendor_name, status, total_amount, paid_amount)
        VALUES (:i, :n, :v, :s, :t, :p)
    """), {"i": str(pid), "n": f"PO-{uuid.uuid4().hex[:8].upper()}",
           "v": vendor, "s": status, "t": total, "p": paid})
    return pid


def test_vendor_names_collapse_into_one_supplier(conn):
    """The whole point of the master: three spellings, one supplier."""
    mkpo(conn, "Acme Ltd")
    mkpo(conn, "acme ltd")
    mkpo(conn, "  ACME LTD  ")
    conn.commit()

    run_migration(conn)

    n = conn.execute(text(
        "SELECT COUNT(*) FROM suppliers WHERE LOWER(TRIM(name))='acme ltd'")).scalar()
    assert n == 1, f"expected 1 supplier, got {n}"

    linked = conn.execute(text(
        "SELECT COUNT(*) FROM purchase_orders WHERE supplier_id IS NOT NULL")).scalar()
    assert linked == 3, "all POs should point at the one supplier"

    distinct = conn.execute(text(
        "SELECT COUNT(DISTINCT supplier_id) FROM purchase_orders")).scalar()
    assert distinct == 1


def test_distinct_vendors_stay_distinct(conn):
    """The collapse must not be so aggressive it merges real suppliers."""
    mkpo(conn, "Acme Ltd")
    mkpo(conn, "Acme Chemicals Ltd")
    conn.commit()
    run_migration(conn)
    assert conn.execute(text("SELECT COUNT(*) FROM suppliers")).scalar() == 2


def test_historical_paid_amounts_become_payment_rows(conn):
    """paid_amount had nothing behind it. After migration every naira paid
    is an auditable event that can appear on a supplier statement."""
    p1 = mkpo(conn, "Acme Ltd", total="1000.00", paid="400.00")
    p2 = mkpo(conn, "Acme Ltd", total="2000.00", paid="2000.00")
    mkpo(conn, "Acme Ltd", total="500.00", paid="0")
    conn.commit()

    run_migration(conn)

    rows = conn.execute(text(
        "SELECT po_id, amount FROM supplier_payments ORDER BY amount")).fetchall()
    assert len(rows) == 2, "only POs with paid_amount > 0 should generate rows"
    assert [Decimal(str(r.amount)) for r in rows] == [
        Decimal("400.00"), Decimal("2000.00")]

    total_paid = conn.execute(text(
        "SELECT COALESCE(SUM(amount),0) FROM supplier_payments")).scalar()
    assert Decimal(str(total_paid)) == Decimal("2400.00")


def test_migrated_payments_carry_supplier_link(conn):
    mkpo(conn, "Acme Ltd", total="1000.00", paid="1000.00")
    conn.commit()
    run_migration(conn)
    n = conn.execute(text(
        "SELECT COUNT(*) FROM supplier_payments WHERE supplier_id IS NULL")).scalar()
    assert n == 0, "payments without a supplier cannot appear on a statement"


def test_outstanding_balance_preserved_across_migration(conn):
    """The figure that matters: what we owe must not change."""
    mkpo(conn, "Acme Ltd", total="1000.00", paid="400.00")
    mkpo(conn, "Beta Supplies", total="5000.00", paid="1000.00")
    conn.commit()

    before = conn.execute(text("""
        SELECT COALESCE(SUM(total_amount - paid_amount), 0)
          FROM purchase_orders WHERE status NOT IN ('cancelled','draft')
    """)).scalar()

    run_migration(conn)

    after = conn.execute(text("""
        SELECT COALESCE(SUM(po.total_amount - COALESCE((
                   SELECT SUM(sp.amount) FROM supplier_payments sp
                    WHERE sp.po_id = po.id), 0)), 0)
          FROM purchase_orders po
         WHERE po.status NOT IN ('cancelled','draft')
    """)).scalar()

    assert Decimal(str(before)) == Decimal(str(after)) == Decimal("4600.00")


def test_over_received_quantities_clamped_then_constrained(conn):
    po = mkpo(conn, "Acme Ltd")
    conn.execute(text("""
        INSERT INTO purchase_order_items
            (po_id, item_name, quantity, unit_cost, received_qty)
        VALUES (:p, 'Widget', 10, 5, 25)
    """), {"p": str(po)})
    conn.commit()

    run_migration(conn)

    got = conn.execute(text(
        "SELECT received_qty, quantity FROM purchase_order_items")).first()
    assert Decimal(str(got.received_qty)) == Decimal(str(got.quantity))

    # And over-receipt is now impossible.
    with pytest.raises(Exception):
        conn.execute(text(
            "UPDATE purchase_order_items SET received_qty = 999"))
        conn.commit()
    conn.rollback()


def test_supplier_payment_must_target_something(conn):
    conn.commit()
    run_migration(conn)
    with pytest.raises(Exception):
        conn.execute(text("""
            INSERT INTO supplier_payments
                (payment_number, amount, payment_method)
            VALUES ('SP-ORPHAN', 100, 'cash')
        """))
        conn.commit()
    conn.rollback()


def test_negative_supplier_payment_rejected(conn):
    po = mkpo(conn, "Acme Ltd")
    conn.commit()
    run_migration(conn)
    with pytest.raises(Exception):
        conn.execute(text("""
            INSERT INTO supplier_payments
                (payment_number, po_id, amount, payment_method)
            VALUES ('SP-NEG', :p, -50, 'cash')
        """), {"p": str(po)})
        conn.commit()
    conn.rollback()


def test_migration_is_idempotent(conn):
    mkpo(conn, "Acme Ltd", total="1000.00", paid="400.00")
    conn.commit()

    run_migration(conn)
    before = conn.execute(text(
        "SELECT COALESCE(SUM(amount),0) FROM supplier_payments")).scalar()
    suppliers_before = conn.execute(text(
        "SELECT COUNT(*) FROM suppliers")).scalar()

    try:
        run_migration(conn)
    except Exception:
        conn.rollback()

    after = conn.execute(text(
        "SELECT COALESCE(SUM(amount),0) FROM supplier_payments")).scalar()
    suppliers_after = conn.execute(text(
        "SELECT COUNT(*) FROM suppliers")).scalar()

    assert Decimal(str(before)) == Decimal(str(after)) == Decimal("400.00"), \
        "re-running must not duplicate payments"
    assert suppliers_before == suppliers_after
