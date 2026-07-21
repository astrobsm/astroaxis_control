"""General ledger: chart of accounts, journal, periods.

Revision ID: m2345678901l
Revises: l1234567890k
Create Date: 2026-07-20

Creates the double-entry ledger and seeds a chart of accounts for a Nigerian
wound-care manufacturer (VAT, PAYE, pension, NHF/NHIA).

Constraints are deliberate, not decorative:
  * a journal line may carry a debit OR a credit, never both, never negative
  * account codes are unique and typed
  * periods cannot overlap
Balance-per-entry is enforced in app.services.ledger.post_entry, which is the
only code path that writes journal lines.
"""
from alembic import op
import sqlalchemy as sa

revision = 'm2345678901l'
down_revision = 'l1234567890k'
branch_labels = None
depends_on = None


ACCOUNTS = [
    # code, name, type, normal, postable
    ("1000", "ASSETS", "ASSET", "DEBIT", False),
    ("1100", "Cash", "ASSET", "DEBIT", True),
    ("1110", "Petty Cash", "ASSET", "DEBIT", True),
    ("1200", "Bank Accounts", "ASSET", "DEBIT", True),
    ("1300", "Accounts Receivable", "ASSET", "DEBIT", True),
    ("1400", "INVENTORY", "ASSET", "DEBIT", False),
    ("1410", "Raw Materials", "ASSET", "DEBIT", True),
    ("1420", "Work In Progress", "ASSET", "DEBIT", True),
    ("1430", "Finished Goods", "ASSET", "DEBIT", True),
    ("1440", "Packaging Materials", "ASSET", "DEBIT", True),
    ("1450", "Consumables", "ASSET", "DEBIT", True),
    ("1500", "FIXED ASSETS", "ASSET", "DEBIT", False),
    ("1510", "Factory Building", "ASSET", "DEBIT", True),
    ("1520", "Plant & Machinery", "ASSET", "DEBIT", True),
    ("1530", "Vehicles", "ASSET", "DEBIT", True),
    ("1540", "Office & Laboratory Equipment", "ASSET", "DEBIT", True),
    ("1590", "Accumulated Depreciation", "ASSET", "CREDIT", True),

    ("2000", "LIABILITIES", "LIABILITY", "CREDIT", False),
    ("2100", "Accounts Payable", "LIABILITY", "CREDIT", True),
    ("2150", "Accrued Expenses", "LIABILITY", "CREDIT", True),
    ("2200", "Staff Salary Payable", "LIABILITY", "CREDIT", True),
    ("2210", "PAYE Payable", "LIABILITY", "CREDIT", True),
    ("2220", "Pension Payable", "LIABILITY", "CREDIT", True),
    ("2230", "NHF Payable", "LIABILITY", "CREDIT", True),
    ("2240", "NHIA Payable", "LIABILITY", "CREDIT", True),
    ("2300", "VAT Payable", "LIABILITY", "CREDIT", True),
    ("2310", "Withholding Tax Payable", "LIABILITY", "CREDIT", True),
    ("2320", "Company Income Tax Payable", "LIABILITY", "CREDIT", True),
    ("2400", "Loans Payable", "LIABILITY", "CREDIT", True),

    ("3000", "EQUITY", "EQUITY", "CREDIT", False),
    ("3100", "Share Capital", "EQUITY", "CREDIT", True),
    ("3200", "Capital Contribution", "EQUITY", "CREDIT", True),
    ("3300", "Retained Earnings", "EQUITY", "CREDIT", True),
    ("3400", "Opening Balance Equity", "EQUITY", "CREDIT", True),

    ("4000", "INCOME", "INCOME", "CREDIT", False),
    ("4100", "Product Sales", "INCOME", "CREDIT", True),
    ("4200", "Service Income", "INCOME", "CREDIT", True),
    ("4300", "Other Income", "INCOME", "CREDIT", True),
    ("4400", "Interest Income", "INCOME", "CREDIT", True),
    ("4900", "Sales Returns & Allowances", "INCOME", "DEBIT", True),

    ("5000", "COST OF SALES", "EXPENSE", "DEBIT", False),
    ("5100", "Cost of Goods Sold", "EXPENSE", "DEBIT", True),
    ("5200", "Raw Material Purchases", "EXPENSE", "DEBIT", True),
    ("5210", "Packaging Materials", "EXPENSE", "DEBIT", True),
    ("5300", "Factory Labour", "EXPENSE", "DEBIT", True),
    ("5400", "FACTORY OVERHEADS", "EXPENSE", "DEBIT", False),
    ("5410", "Electricity", "EXPENSE", "DEBIT", True),
    ("5420", "Generator Diesel", "EXPENSE", "DEBIT", True),
    ("5430", "Water", "EXPENSE", "DEBIT", True),
    ("5440", "Factory Maintenance", "EXPENSE", "DEBIT", True),
    ("5450", "Equipment Maintenance", "EXPENSE", "DEBIT", True),
    ("5500", "Inventory Write-off / Damage", "EXPENSE", "DEBIT", True),
    ("5600", "Production Variance", "EXPENSE", "DEBIT", True),

    ("6000", "OPERATING EXPENSES", "EXPENSE", "DEBIT", False),
    ("6100", "Salaries & Wages", "EXPENSE", "DEBIT", True),
    ("6110", "Staff Welfare", "EXPENSE", "DEBIT", True),
    ("6200", "Transportation & Logistics", "EXPENSE", "DEBIT", True),
    ("6210", "Fuel", "EXPENSE", "DEBIT", True),
    ("6300", "Marketing & Advertising", "EXPENSE", "DEBIT", True),
    ("6400", "Internet & Telephone", "EXPENSE", "DEBIT", True),
    ("6410", "Software & Subscriptions", "EXPENSE", "DEBIT", True),
    ("6500", "Insurance", "EXPENSE", "DEBIT", True),
    ("6510", "Security", "EXPENSE", "DEBIT", True),
    ("6520", "Cleaning", "EXPENSE", "DEBIT", True),
    ("6530", "Waste Disposal", "EXPENSE", "DEBIT", True),
    ("6600", "Office Expenses", "EXPENSE", "DEBIT", True),
    ("6700", "Legal & Professional Fees", "EXPENSE", "DEBIT", True),
    ("6800", "Depreciation", "EXPENSE", "DEBIT", True),
    ("6900", "Research & Development", "EXPENSE", "DEBIT", True),
    ("6950", "Quality Control & Laboratory", "EXPENSE", "DEBIT", True),
    ("6990", "Bank Charges", "EXPENSE", "DEBIT", True),
]


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS gl_accounts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code VARCHAR(20) UNIQUE NOT NULL,
            name VARCHAR(255) NOT NULL,
            account_type VARCHAR(20) NOT NULL,
            normal_balance VARCHAR(6) NOT NULL,
            parent_id UUID REFERENCES gl_accounts(id),
            description TEXT,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            is_postable BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT ck_gl_accounts_type CHECK (account_type IN
                ('ASSET','LIABILITY','EQUITY','INCOME','EXPENSE')),
            CONSTRAINT ck_gl_accounts_normal CHECK (normal_balance IN
                ('DEBIT','CREDIT'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_gl_accounts_type "
               "ON gl_accounts (account_type)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS gl_journal_entries (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            -- Monotonic insertion order. posted_at cannot serve this purpose:
            -- NOW() is transaction time in Postgres, so entries written in one
            -- transaction share a timestamp, and entry_number carries a random
            -- collision-safety suffix. Without a real sequence, same-day
            -- ledger lines order arbitrarily and running balances read as
            -- nonsense.
            seq BIGSERIAL NOT NULL,
            entry_number VARCHAR(64) UNIQUE NOT NULL,
            entry_date DATE NOT NULL,
            description TEXT NOT NULL,
            source_module VARCHAR(50) NOT NULL,
            source_reference VARCHAR(128),
            status VARCHAR(16) NOT NULL DEFAULT 'POSTED',
            reverses_entry_id UUID REFERENCES gl_journal_entries(id),
            posted_at TIMESTAMPTZ DEFAULT NOW(),
            created_by UUID,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT ck_gl_entry_status CHECK (status IN
                ('DRAFT','POSTED','REVERSED'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_gl_entries_seq "
               "ON gl_journal_entries (seq)")
    for idx, col in [("date", "entry_date"), ("module", "source_module"),
                     ("ref", "source_reference"), ("status", "status")]:
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_gl_entries_{idx} "
                   f"ON gl_journal_entries ({col})")
    # One reversal per entry, enforced by the database rather than by a
    # check-then-insert that two concurrent requests could both pass.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_gl_entry_reversal
            ON gl_journal_entries (reverses_entry_id)
         WHERE reverses_entry_id IS NOT NULL
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS gl_journal_lines (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            entry_id UUID NOT NULL
                REFERENCES gl_journal_entries(id) ON DELETE CASCADE,
            account_id UUID NOT NULL REFERENCES gl_accounts(id),
            debit NUMERIC(18,2) NOT NULL DEFAULT 0,
            credit NUMERIC(18,2) NOT NULL DEFAULT 0,
            description TEXT,
            cost_centre VARCHAR(50),
            line_number INTEGER NOT NULL DEFAULT 0,
            -- A line is a debit or a credit, never both, never negative.
            CONSTRAINT ck_gl_line_sides CHECK (
                debit >= 0 AND credit >= 0
                AND NOT (debit > 0 AND credit > 0)
                AND (debit > 0 OR credit > 0)
            )
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_gl_lines_entry "
               "ON gl_journal_lines (entry_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_gl_lines_account "
               "ON gl_journal_lines (account_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_gl_lines_cost_centre "
               "ON gl_journal_lines (cost_centre)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS gl_periods (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(50) UNIQUE NOT NULL,
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'OPEN',
            closed_at TIMESTAMPTZ,
            closed_by UUID,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT ck_gl_period_status CHECK (status IN ('OPEN','CLOSED')),
            CONSTRAINT ck_gl_period_order CHECK (end_date >= start_date)
        )
    """)
    # Overlapping periods would make "which period is this date in?"
    # ambiguous, and period locking meaningless.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute("""
        ALTER TABLE gl_periods
            ADD CONSTRAINT ex_gl_periods_no_overlap
            EXCLUDE USING gist (
                daterange(start_date, end_date, '[]') WITH &&
            )
    """)

    # ---- seed the chart of accounts (idempotent) ----
    for code, name, atype, normal, postable in ACCOUNTS:
        op.execute(sa.text("""
            INSERT INTO gl_accounts
                (id, code, name, account_type, normal_balance, is_postable)
            VALUES (gen_random_uuid(), :c, :n, :t, :nb, :p)
            ON CONFLICT (code) DO NOTHING
        """).bindparams(c=code, n=name, t=atype, nb=normal, p=postable))

    # Link children to their header by code prefix, for report grouping.
    op.execute("""
        UPDATE gl_accounts child
           SET parent_id = parent.id
          FROM gl_accounts parent
         WHERE parent.is_postable = FALSE
           AND child.id <> parent.id
           AND child.parent_id IS NULL
           AND LEFT(child.code, 2) = LEFT(parent.code, 2)
           AND parent.code LIKE '%00'
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS gl_journal_lines CASCADE")
    op.execute("DROP TABLE IF EXISTS gl_journal_entries CASCADE")
    op.execute("DROP TABLE IF EXISTS gl_periods CASCADE")
    op.execute("DROP TABLE IF EXISTS gl_accounts CASCADE")
