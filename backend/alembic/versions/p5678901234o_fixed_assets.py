"""Fixed assets and depreciation.

Revision ID: p5678901234o
Revises: o4567890123n
Create Date: 2026-07-20

The existing depreciation lived on GET /api/machines/{id}/depreciation, which:
  * MUTATED on a read -- it wrote current_value back to the row, so merely
    viewing an asset changed it;
  * recomputed the entire history from acquisition on every call using
    fractional years, so the answer drifted with the date it was asked;
  * recorded no periodic charge, so there was nothing to post, audit or
    reconcile;
  * used float, ignored residual value, and could depreciate an asset below
    zero.

Depreciation is a periodic EVENT, not a derived number. Each period's charge
is recorded once as an immutable row and posted to the ledger. The carrying
amount is then the sum of those charges subtracted from cost -- reproducible,
auditable, and identical no matter when it is asked.
"""
from alembic import op
import sqlalchemy as sa

revision = 'p5678901234o'
down_revision = 'o4567890123n'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS fixed_assets (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            asset_number VARCHAR(64) UNIQUE NOT NULL,
            name VARCHAR(255) NOT NULL,
            description TEXT,
            category VARCHAR(50) NOT NULL DEFAULT 'equipment',
            acquisition_date DATE NOT NULL,
            cost NUMERIC(18,2) NOT NULL,
            residual_value NUMERIC(18,2) NOT NULL DEFAULT 0,
            useful_life_months INTEGER NOT NULL,
            method VARCHAR(30) NOT NULL DEFAULT 'STRAIGHT_LINE',
            -- Reducing-balance needs an annual rate; straight-line does not.
            annual_rate_percent NUMERIC(6,3),
            -- Which GL accounts this asset's cost and charges belong to, so
            -- vehicles and machinery can report separately.
            asset_account VARCHAR(20) NOT NULL DEFAULT '1520',
            accumulated_account VARCHAR(20) NOT NULL DEFAULT '1590',
            expense_account VARCHAR(20) NOT NULL DEFAULT '6800',
            cost_centre VARCHAR(50),
            location VARCHAR(255),
            serial_number VARCHAR(128),
            supplier_id UUID,
            status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
            -- Depreciation stops at disposal; these record the outcome.
            disposal_date DATE,
            disposal_proceeds NUMERIC(18,2),
            disposal_notes TEXT,
            -- Where this asset came from, if it was created from an existing
            -- machines_equipment row rather than entered directly.
            source_machine_id UUID,
            created_by UUID,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT ck_fa_cost CHECK (cost > 0),
            CONSTRAINT ck_fa_residual CHECK (
                residual_value >= 0 AND residual_value <= cost),
            CONSTRAINT ck_fa_life CHECK (useful_life_months > 0),
            CONSTRAINT ck_fa_method CHECK (method IN
                ('STRAIGHT_LINE','REDUCING_BALANCE')),
            CONSTRAINT ck_fa_status CHECK (status IN
                ('ACTIVE','FULLY_DEPRECIATED','DISPOSED','WRITTEN_OFF')),
            -- Reducing balance is meaningless without a rate.
            CONSTRAINT ck_fa_reducing_needs_rate CHECK (
                method <> 'REDUCING_BALANCE'
                OR (annual_rate_percent IS NOT NULL
                    AND annual_rate_percent > 0)),
            CONSTRAINT ck_fa_disposal CHECK (
                (status <> 'DISPOSED') OR disposal_date IS NOT NULL)
        )
    """)
    for idx, col in [("status", "status"), ("category", "category"),
                     ("acq", "acquisition_date")]:
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_fixed_assets_{idx} "
                   f"ON fixed_assets ({col})")

    # One immutable charge per asset per period. The unique constraint is the
    # idempotency guard: a depreciation run that is retried, or accidentally
    # executed twice for a month, cannot double-charge.
    op.execute("""
        CREATE TABLE IF NOT EXISTS asset_depreciation_charges (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            asset_id UUID NOT NULL REFERENCES fixed_assets(id),
            period_start DATE NOT NULL,
            period_end DATE NOT NULL,
            amount NUMERIC(18,2) NOT NULL,
            opening_carrying_amount NUMERIC(18,2) NOT NULL,
            closing_carrying_amount NUMERIC(18,2) NOT NULL,
            journal_entry_id UUID,
            run_id UUID,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT uq_asset_period UNIQUE (asset_id, period_start),
            CONSTRAINT ck_charge_amount CHECK (amount >= 0),
            CONSTRAINT ck_charge_period CHECK (period_end >= period_start),
            CONSTRAINT ck_charge_carrying CHECK (
                closing_carrying_amount >= 0
                AND closing_carrying_amount <= opening_carrying_amount)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_dep_charges_asset "
               "ON asset_depreciation_charges (asset_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_dep_charges_period "
               "ON asset_depreciation_charges (period_start)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS depreciation_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_number VARCHAR(64) UNIQUE NOT NULL,
            period_start DATE NOT NULL,
            period_end DATE NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'POSTED',
            asset_count INTEGER DEFAULT 0,
            total_charge NUMERIC(18,2) DEFAULT 0,
            journal_entry_id UUID,
            created_by UUID,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT uq_dep_run_period UNIQUE (period_start, period_end),
            CONSTRAINT ck_dep_run_status CHECK (status IN
                ('POSTED','REVERSED'))
        )
    """)

    # Carry across anything already recorded as a machine with a purchase
    # cost. current_value is deliberately NOT used as a starting point -- it
    # was written by a GET handler using drifting fractional-year maths and
    # cannot be reconciled to anything.
    #
    # machines_equipment is a runtime-created table (the app builds it via
    # CREATE TABLE IF NOT EXISTS on first use), so it is absent on a freshly
    # built database. Guard the backfill on its existence -- with no machine
    # rows there is simply nothing to carry across.
    if op.get_bind().execute(sa.text(
            "SELECT to_regclass('public.machines_equipment')"
    )).scalar() is None:
        return

    op.execute("""
        INSERT INTO fixed_assets
            (asset_number, name, category, acquisition_date, cost,
             residual_value, useful_life_months, method, annual_rate_percent,
             asset_account, accumulated_account, expense_account,
             location, serial_number, source_machine_id, status)
        SELECT 'FA-' || UPPER(SUBSTRING(MD5(m.id::text), 1, 8)),
               m.name,
               'equipment',
               COALESCE(m.purchase_date, CURRENT_DATE),
               m.purchase_cost,
               0,
               -- Derive a life from the stated annual rate where there is
               -- one; otherwise default to 5 years and let finance correct
               -- it. Never silently assume the rate is right.
               CASE WHEN COALESCE(m.depreciation_rate, 0) > 0
                    THEN GREATEST(1, ROUND(1200.0 / m.depreciation_rate)::int)
                    ELSE 60 END,
               CASE WHEN LOWER(COALESCE(m.depreciation_method, '')) LIKE '%declin%'
                    THEN 'REDUCING_BALANCE' ELSE 'STRAIGHT_LINE' END,
               CASE WHEN LOWER(COALESCE(m.depreciation_method, '')) LIKE '%declin%'
                    THEN NULLIF(m.depreciation_rate, 0) ELSE NULL END,
               '1520', '1590', '6800',
               m.location, NULLIF(m.serial_number, ''), m.id, 'ACTIVE'
          FROM machines_equipment m
         WHERE COALESCE(m.purchase_cost, 0) > 0
           AND NOT EXISTS (SELECT 1 FROM fixed_assets fa
                            WHERE fa.source_machine_id = m.id)
    """)

    # A reducing-balance asset carried over without a usable rate would
    # violate the CHECK; fall back to straight line rather than fail.
    op.execute("""
        UPDATE fixed_assets
           SET method = 'STRAIGHT_LINE', annual_rate_percent = NULL
         WHERE method = 'REDUCING_BALANCE'
           AND (annual_rate_percent IS NULL OR annual_rate_percent <= 0)
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS asset_depreciation_charges CASCADE")
    op.execute("DROP TABLE IF EXISTS depreciation_runs CASCADE")
    op.execute("DROP TABLE IF EXISTS fixed_assets CASCADE")
