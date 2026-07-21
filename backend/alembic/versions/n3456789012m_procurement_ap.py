"""Procurement & Accounts Payable.

Revision ID: n3456789012m
Revises: m2345678901l
Create Date: 2026-07-20

Brings the procurement tables under Alembic control and adds the two things
Accounts Payable was missing:

  * a SUPPLIER MASTER. Vendors were free-text strings on each purchase order,
    so "Bonnesante Ltd", "bonnesante ltd" and "Bonnesante" were three
    different suppliers. Aging, statements and credit limits are impossible
    against a name.

  * SUPPLIER PAYMENT ROWS. `purchase_orders.paid_amount` was a mutable total
    with nothing behind it -- the same defect that made accounts receivable
    unreconcilable. Payments are now events, and paid_amount is a cache
    derived from them.

The procurement tables themselves were created by CREATE TABLE IF NOT EXISTS
inside a request handler, invisible to Alembic. This migration creates them
identically for a fresh database and leaves an existing one untouched, so the
two converge without disturbing live data.
"""
from alembic import op
import sqlalchemy as sa

revision = 'n3456789012m'
down_revision = 'm2345678901l'
branch_labels = None
depends_on = None


# Verbatim from app/api/procurement.py so a fresh database built by Alembic
# matches one built by the old runtime bootstrap.
LEGACY_TABLES = [
    """CREATE TABLE IF NOT EXISTS purchase_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_number VARCHAR(64) UNIQUE NOT NULL,
    requested_by VARCHAR(255) NOT NULL,
    department VARCHAR(100),
    category VARCHAR(50) NOT NULL DEFAULT 'general',
    priority VARCHAR(20) NOT NULL DEFAULT 'normal',
    status VARCHAR(30) NOT NULL DEFAULT 'submitted',
    title VARCHAR(255) NOT NULL,
    description TEXT,
    justification TEXT,
    total_estimated_cost NUMERIC(18,2) DEFAULT 0,
    approved_by VARCHAR(255),
    approved_at TIMESTAMPTZ,
    rejection_reason TEXT,
    vendor_name VARCHAR(255),
    vendor_contact VARCHAR(255),
    vendor_phone VARCHAR(50),
    vendor_email VARCHAR(255),
    expected_delivery_date DATE,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
)""",
    """CREATE TABLE IF NOT EXISTS purchase_request_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id UUID NOT NULL REFERENCES purchase_requests(id) ON DELETE CASCADE,
    item_type VARCHAR(50) NOT NULL DEFAULT 'general',
    item_name VARCHAR(255) NOT NULL,
    item_id UUID,
    specification TEXT,
    quantity NUMERIC(18,6) NOT NULL DEFAULT 1,
    unit VARCHAR(50) DEFAULT 'each',
    estimated_unit_cost NUMERIC(18,2) DEFAULT 0,
    estimated_total NUMERIC(18,2) DEFAULT 0,
    actual_unit_cost NUMERIC(18,2),
    actual_total NUMERIC(18,2),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
)""",
    """CREATE TABLE IF NOT EXISTS purchase_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    po_number VARCHAR(64) UNIQUE NOT NULL,
    request_id UUID REFERENCES purchase_requests(id),
    vendor_name VARCHAR(255) NOT NULL,
    vendor_contact VARCHAR(255),
    vendor_phone VARCHAR(50),
    vendor_email VARCHAR(255),
    vendor_address TEXT,
    status VARCHAR(30) NOT NULL DEFAULT 'draft',
    order_date TIMESTAMPTZ DEFAULT NOW(),
    expected_delivery DATE,
    delivery_date DATE,
    subtotal NUMERIC(18,2) DEFAULT 0,
    tax_amount NUMERIC(18,2) DEFAULT 0,
    shipping_cost NUMERIC(18,2) DEFAULT 0,
    total_amount NUMERIC(18,2) DEFAULT 0,
    paid_amount NUMERIC(18,2) DEFAULT 0,
    payment_status VARCHAR(30) DEFAULT 'unpaid',
    payment_method VARCHAR(50),
    payment_reference VARCHAR(255),
    payment_date TIMESTAMPTZ,
    notes TEXT,
    created_by VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
)""",
    """CREATE TABLE IF NOT EXISTS purchase_order_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    po_id UUID NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
    item_type VARCHAR(50) NOT NULL DEFAULT 'general',
    item_name VARCHAR(255) NOT NULL,
    item_id UUID,
    specification TEXT,
    quantity NUMERIC(18,6) NOT NULL DEFAULT 1,
    unit VARCHAR(50) DEFAULT 'each',
    unit_cost NUMERIC(18,2) DEFAULT 0,
    line_total NUMERIC(18,2) DEFAULT 0,
    received_qty NUMERIC(18,6) DEFAULT 0,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
)""",
    """CREATE TABLE IF NOT EXISTS purchase_invoices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_number VARCHAR(100) NOT NULL,
    po_id UUID REFERENCES purchase_orders(id),
    vendor_name VARCHAR(255) NOT NULL,
    invoice_date DATE NOT NULL DEFAULT CURRENT_DATE,
    due_date DATE,
    subtotal NUMERIC(18,2) DEFAULT 0,
    tax_amount NUMERIC(18,2) DEFAULT 0,
    total_amount NUMERIC(18,2) DEFAULT 0,
    paid_amount NUMERIC(18,2) DEFAULT 0,
    payment_status VARCHAR(30) DEFAULT 'unpaid',
    status VARCHAR(30) DEFAULT 'pending',
    category VARCHAR(50) DEFAULT 'general',
    description TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
)""",
    """CREATE TABLE IF NOT EXISTS expense_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    expense_number VARCHAR(64) UNIQUE NOT NULL,
    category VARCHAR(50) NOT NULL,
    subcategory VARCHAR(100),
    description TEXT NOT NULL,
    amount NUMERIC(18,2) NOT NULL,
    payment_method VARCHAR(50),
    payment_reference VARCHAR(255),
    payment_date DATE NOT NULL DEFAULT CURRENT_DATE,
    recipient VARCHAR(255),
    approved_by VARCHAR(255),
    po_id UUID REFERENCES purchase_orders(id),
    purchase_invoice_id UUID REFERENCES purchase_invoices(id),
    staff_id UUID,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
)""",
]

# Indexes are applied AFTER column reconciliation below: several of them
# reference columns that a drifted, runtime-created table may not have yet,
# and CREATE INDEX on a missing column aborts the migration.
LEGACY_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_pr_status ON purchase_requests(status)",
    "CREATE INDEX IF NOT EXISTS idx_pr_category ON purchase_requests(category)",
    "CREATE INDEX IF NOT EXISTS idx_po_status ON purchase_orders(status)",
    "CREATE INDEX IF NOT EXISTS idx_po_payment ON purchase_orders(payment_status)",
    "CREATE INDEX IF NOT EXISTS idx_pi_payment ON purchase_invoices(payment_status)",
    "CREATE INDEX IF NOT EXISTS idx_exp_category ON expense_records(category)",
    "CREATE INDEX IF NOT EXISTS idx_exp_date ON expense_records(payment_date)",
]


def upgrade():
    for stmt in LEGACY_TABLES:
        op.execute(stmt)

    # These tables were created by CREATE TABLE IF NOT EXISTS inside a request
    # handler, so an existing database has whatever shape the code had when it
    # first ran -- and IF NOT EXISTS will not add columns that were introduced
    # later. Reconcile explicitly before indexing anything, otherwise a single
    # drifted column aborts the whole migration.
    RECONCILE = [
        ("purchase_orders", "paid_amount", "NUMERIC(18,2) DEFAULT 0"),
        ("purchase_orders", "payment_status", "VARCHAR(30) DEFAULT 'unpaid'"),
        ("purchase_orders", "payment_method", "VARCHAR(50)"),
        ("purchase_orders", "payment_reference", "VARCHAR(255)"),
        ("purchase_orders", "payment_date", "TIMESTAMPTZ"),
        ("purchase_orders", "updated_at", "TIMESTAMPTZ DEFAULT NOW()"),
        ("purchase_invoices", "paid_amount", "NUMERIC(18,2) DEFAULT 0"),
        ("purchase_invoices", "payment_status", "VARCHAR(30) DEFAULT 'unpaid'"),
        ("purchase_invoices", "status", "VARCHAR(30) DEFAULT 'pending'"),
        ("purchase_invoices", "category", "VARCHAR(50) DEFAULT 'general'"),
        ("purchase_invoices", "due_date", "DATE"),
        ("purchase_order_items", "received_qty", "NUMERIC(18,6) DEFAULT 0"),
        ("purchase_requests", "updated_at", "TIMESTAMPTZ DEFAULT NOW()"),
        ("expense_records", "subcategory", "VARCHAR(100)"),
    ]
    for table, column, coltype in RECONCILE:
        op.execute(f"ALTER TABLE {table} "
                   f"ADD COLUMN IF NOT EXISTS {column} {coltype}")

    for stmt in LEGACY_INDEXES:
        op.execute(stmt)

    # ---- Supplier master --------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS suppliers (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            supplier_code VARCHAR(32) UNIQUE NOT NULL,
            name VARCHAR(255) NOT NULL,
            classification VARCHAR(50) DEFAULT 'general',
            contact_person VARCHAR(255),
            phone VARCHAR(50),
            email VARCHAR(255),
            address TEXT,
            tax_id VARCHAR(64),
            bank_name VARCHAR(128),
            bank_account_number VARCHAR(64),
            bank_account_name VARCHAR(128),
            credit_limit NUMERIC(18,2) DEFAULT 0,
            payment_terms_days INTEGER DEFAULT 30,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            notes TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT ck_suppliers_credit_limit CHECK (credit_limit >= 0),
            CONSTRAINT ck_suppliers_terms CHECK (payment_terms_days >= 0)
        )
    """)
    # Case-insensitive uniqueness: "Acme Ltd" and "acme ltd" are one supplier.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_suppliers_name_ci
            ON suppliers (LOWER(name))
    """)

    # Link the existing free-text vendor names to real supplier records.
    op.execute("""
        ALTER TABLE purchase_orders
            ADD COLUMN IF NOT EXISTS supplier_id UUID REFERENCES suppliers(id)
    """)
    op.execute("""
        ALTER TABLE purchase_invoices
            ADD COLUMN IF NOT EXISTS supplier_id UUID REFERENCES suppliers(id)
    """)
    op.execute("""
        ALTER TABLE purchase_requests
            ADD COLUMN IF NOT EXISTS supplier_id UUID REFERENCES suppliers(id)
    """)

    # ---- Supplier payments: the source of truth for what has been paid ----
    op.execute("""
        CREATE TABLE IF NOT EXISTS supplier_payments (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            payment_number VARCHAR(64) UNIQUE NOT NULL,
            supplier_id UUID REFERENCES suppliers(id),
            po_id UUID REFERENCES purchase_orders(id),
            purchase_invoice_id UUID REFERENCES purchase_invoices(id),
            amount NUMERIC(18,2) NOT NULL,
            payment_method VARCHAR(50) NOT NULL,
            payment_reference VARCHAR(255),
            payment_date DATE NOT NULL DEFAULT CURRENT_DATE,
            notes TEXT,
            created_by UUID,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT ck_supplier_payments_positive CHECK (amount > 0),
            -- A payment must settle something identifiable, or it cannot be
            -- reconciled against a supplier statement.
            CONSTRAINT ck_supplier_payments_target CHECK (
                po_id IS NOT NULL OR purchase_invoice_id IS NOT NULL
            )
        )
    """)
    for idx, col in [("po", "po_id"), ("inv", "purchase_invoice_id"),
                     ("supplier", "supplier_id"), ("date", "payment_date")]:
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_supplier_payments_{idx} "
                   f"ON supplier_payments ({col})")

    # ---- Backfill ---------------------------------------------------------
    # Create a supplier for every distinct vendor name already in use, then
    # point the existing documents at it. Names are collapsed case- and
    # whitespace-insensitively, which is the whole reason the master exists.
    op.execute("""
        INSERT INTO suppliers (supplier_code, name, notes)
        SELECT 'SUP-' || UPPER(SUBSTRING(MD5(LOWER(TRIM(v.name))), 1, 8)),
               MIN(v.name),
               'Auto-created from existing purchase records'
          FROM (
              SELECT vendor_name AS name FROM purchase_orders
               WHERE vendor_name IS NOT NULL AND TRIM(vendor_name) <> ''
              UNION ALL
              SELECT vendor_name FROM purchase_invoices
               WHERE vendor_name IS NOT NULL AND TRIM(vendor_name) <> ''
          ) v
         GROUP BY LOWER(TRIM(v.name))
         ON CONFLICT DO NOTHING
    """)
    for tbl in ("purchase_orders", "purchase_invoices"):
        op.execute(f"""
            UPDATE {tbl} t
               SET supplier_id = s.id
              FROM suppliers s
             WHERE LOWER(TRIM(t.vendor_name)) = LOWER(TRIM(s.name))
               AND t.supplier_id IS NULL
        """)

    # Turn each historical paid_amount into an actual payment row, so the
    # cache has something behind it and supplier statements are complete.
    op.execute("""
        INSERT INTO supplier_payments
            (payment_number, supplier_id, po_id, amount, payment_method,
             payment_reference, payment_date, notes)
        SELECT 'SP-MIG-' || UPPER(SUBSTRING(MD5(po.id::text), 1, 10)),
               po.supplier_id, po.id, po.paid_amount,
               COALESCE(po.payment_method, 'unspecified'),
               po.payment_reference,
               COALESCE(po.payment_date::date, po.order_date::date,
                        CURRENT_DATE),
               'Migrated from purchase_orders.paid_amount'
          FROM purchase_orders po
         WHERE po.paid_amount > 0
           AND NOT EXISTS (SELECT 1 FROM supplier_payments sp
                            WHERE sp.po_id = po.id)
         ON CONFLICT DO NOTHING
    """)

    # Received quantity cannot exceed what was ordered.
    op.execute("""
        UPDATE purchase_order_items
           SET received_qty = quantity
         WHERE received_qty > quantity
    """)
    op.execute("""
        ALTER TABLE purchase_order_items
            ADD CONSTRAINT ck_poi_received_not_over
            CHECK (received_qty >= 0 AND received_qty <= quantity)
    """)

    # Track which receipts have already moved stock, so a repeated receive
    # cannot book the same goods into inventory twice.
    op.execute("""
        ALTER TABLE purchase_orders
            ADD COLUMN IF NOT EXISTS stock_received_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS receiving_warehouse_id UUID
                REFERENCES warehouses(id)
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS supplier_payments CASCADE")
    op.execute("""
        ALTER TABLE purchase_order_items
            DROP CONSTRAINT IF EXISTS ck_poi_received_not_over
    """)
    for tbl in ("purchase_orders", "purchase_invoices", "purchase_requests"):
        op.execute(f"ALTER TABLE {tbl} DROP COLUMN IF EXISTS supplier_id")
    op.execute("""
        ALTER TABLE purchase_orders
            DROP COLUMN IF EXISTS stock_received_at,
            DROP COLUMN IF EXISTS receiving_warehouse_id
    """)
    op.execute("DROP TABLE IF EXISTS suppliers CASCADE")
