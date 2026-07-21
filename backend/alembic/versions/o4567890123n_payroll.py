"""Payroll: statutory deductions, payslips, and GL posting.

Revision ID: o4567890123n
Revises: n3456789012m
Create Date: 2026-07-20

The existing payroll paid every staff member a hardcoded NGN 425/hour --
ignoring their actual `hourly_rate` and `monthly_salary` -- and set
deductions to zero. No PAYE, no pension, no NHF, no NHIA were withheld from
anyone. Under-deducted PAYE is recoverable from the COMPANY, not the
employee, so that is an accruing liability rather than a cosmetic bug.

RATES ARE DATA, NOT CODE
------------------------
Every tax band and contribution rate lives in these tables, effective-dated,
so they can be corrected without a deploy when the law changes. Nothing is
hardcoded in the engine.

The seeded configuration is marked **is_confirmed = FALSE** and the engine
REFUSES TO RUN against an unconfirmed configuration. This is deliberate: the
seeded figures are a plausible starting point, not legal advice, and they
must be checked against current Nigerian law by someone qualified before a
single payslip is produced. Confirming is a recorded act with a name against
it.
"""
from alembic import op
import sqlalchemy as sa

revision = 'o4567890123n'
down_revision = 'n3456789012m'
branch_labels = None
depends_on = None


def upgrade():
    # ---- Rate configuration (effective-dated, confirmable) ----------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS payroll_rate_configs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(100) NOT NULL,
            effective_from DATE NOT NULL,
            effective_to DATE,
            -- Payroll cannot be run against an unconfirmed configuration.
            is_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
            confirmed_by VARCHAR(255),
            confirmed_at TIMESTAMPTZ,
            source_reference TEXT,
            notes TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT ck_prc_dates CHECK (
                effective_to IS NULL OR effective_to >= effective_from),
            CONSTRAINT ck_prc_confirmed_has_author CHECK (
                is_confirmed = FALSE OR confirmed_by IS NOT NULL)
        )
    """)

    # Progressive PAYE bands. Stored as ANNUAL figures because Nigerian
    # personal income tax is assessed annually and then apportioned; applying
    # monthly bands directly gives a different answer for anyone whose pay
    # varies month to month.
    op.execute("""
        CREATE TABLE IF NOT EXISTS payroll_tax_bands (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            config_id UUID NOT NULL
                REFERENCES payroll_rate_configs(id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL,
            lower_bound NUMERIC(18,2) NOT NULL,
            upper_bound NUMERIC(18,2),          -- NULL = no upper limit
            rate_percent NUMERIC(6,3) NOT NULL,
            CONSTRAINT ck_band_rate CHECK (rate_percent >= 0
                                           AND rate_percent <= 100),
            CONSTRAINT ck_band_bounds CHECK (
                upper_bound IS NULL OR upper_bound > lower_bound),
            CONSTRAINT uq_band_sequence UNIQUE (config_id, sequence)
        )
    """)

    # Everything that is not a progressive band: contribution percentages,
    # relief parameters, thresholds.
    op.execute("""
        CREATE TABLE IF NOT EXISTS payroll_rate_items (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            config_id UUID NOT NULL
                REFERENCES payroll_rate_configs(id) ON DELETE CASCADE,
            code VARCHAR(50) NOT NULL,
            value NUMERIC(18,4) NOT NULL,
            -- What the value is applied to: GROSS, BASIC, PENSIONABLE,
            -- TAXABLE, or FIXED for an absolute naira amount.
            basis VARCHAR(20) NOT NULL DEFAULT 'GROSS',
            description TEXT,
            CONSTRAINT uq_rate_item UNIQUE (config_id, code),
            CONSTRAINT ck_rate_basis CHECK (basis IN
                ('GROSS','BASIC','PENSIONABLE','TAXABLE','FIXED'))
        )
    """)

    # ---- Payroll runs and payslips ---------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS payroll_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_number VARCHAR(64) UNIQUE NOT NULL,
            period_start DATE NOT NULL,
            period_end DATE NOT NULL,
            config_id UUID REFERENCES payroll_rate_configs(id),
            status VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
            gross_total NUMERIC(18,2) DEFAULT 0,
            deductions_total NUMERIC(18,2) DEFAULT 0,
            net_total NUMERIC(18,2) DEFAULT 0,
            employer_cost_total NUMERIC(18,2) DEFAULT 0,
            approved_by VARCHAR(255),
            approved_at TIMESTAMPTZ,
            paid_at TIMESTAMPTZ,
            journal_entry_id UUID,
            notes TEXT,
            created_by UUID,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT ck_run_status CHECK (status IN
                ('DRAFT','APPROVED','PAID','CANCELLED')),
            CONSTRAINT ck_run_period CHECK (period_end >= period_start)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS payslips (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id UUID NOT NULL
                REFERENCES payroll_runs(id) ON DELETE CASCADE,
            staff_id UUID NOT NULL REFERENCES staff(id),
            payslip_number VARCHAR(64) UNIQUE NOT NULL,
            basic_salary NUMERIC(18,2) DEFAULT 0,
            gross_pay NUMERIC(18,2) NOT NULL DEFAULT 0,
            taxable_income NUMERIC(18,2) DEFAULT 0,
            total_deductions NUMERIC(18,2) DEFAULT 0,
            net_pay NUMERIC(18,2) NOT NULL DEFAULT 0,
            employer_contributions NUMERIC(18,2) DEFAULT 0,
            regular_hours NUMERIC(8,2) DEFAULT 0,
            overtime_hours NUMERIC(8,2) DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT uq_payslip_staff_run UNIQUE (run_id, staff_id),
            CONSTRAINT ck_payslip_non_negative CHECK (
                gross_pay >= 0 AND total_deductions >= 0
                AND employer_contributions >= 0)
        )
    """)

    # Every earning and deduction, itemised. A payslip that shows only a
    # total cannot be checked by the employee or defended to a tax authority.
    op.execute("""
        CREATE TABLE IF NOT EXISTS payslip_components (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            payslip_id UUID NOT NULL
                REFERENCES payslips(id) ON DELETE CASCADE,
            -- 30, not 20: 'EMPLOYER_CONTRIBUTION' is 21 characters, so a
            -- narrower column rejects a value the CHECK below permits.
            component_type VARCHAR(30) NOT NULL,
            code VARCHAR(50) NOT NULL,
            label VARCHAR(255) NOT NULL,
            amount NUMERIC(18,2) NOT NULL,
            basis_amount NUMERIC(18,2),
            rate_applied NUMERIC(10,4),
            sequence INTEGER DEFAULT 0,
            CONSTRAINT ck_component_type CHECK (component_type IN
                ('EARNING','DEDUCTION','EMPLOYER_CONTRIBUTION','INFO')),
            CONSTRAINT ck_component_amount CHECK (amount >= 0)
        )
    """)
    for idx, tbl, col in [("run", "payslips", "run_id"),
                          ("staff", "payslips", "staff_id"),
                          ("payslip", "payslip_components", "payslip_id"),
                          ("period", "payroll_runs", "period_start")]:
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_payroll_{idx} "
                   f"ON {tbl} ({col})")

    # Recurring per-staff deductions: loans, salary advances.
    op.execute("""
        CREATE TABLE IF NOT EXISTS staff_deductions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            staff_id UUID NOT NULL REFERENCES staff(id),
            code VARCHAR(50) NOT NULL,
            label VARCHAR(255) NOT NULL,
            total_amount NUMERIC(18,2) NOT NULL,
            amount_per_period NUMERIC(18,2) NOT NULL,
            amount_recovered NUMERIC(18,2) NOT NULL DEFAULT 0,
            start_date DATE NOT NULL DEFAULT CURRENT_DATE,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            notes TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT ck_deduction_amounts CHECK (
                total_amount > 0 AND amount_per_period > 0
                AND amount_recovered >= 0
                AND amount_recovered <= total_amount)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_staff_deductions_staff "
               "ON staff_deductions (staff_id)")

    # Per-staff pay structure. Staff currently carries only a flat
    # monthly_salary / hourly_rate, which cannot express the housing and
    # transport split that Nigerian pensionable pay is calculated from.
    op.execute("""
        ALTER TABLE staff
            ADD COLUMN IF NOT EXISTS basic_salary NUMERIC(18,2),
            ADD COLUMN IF NOT EXISTS housing_allowance NUMERIC(18,2) DEFAULT 0,
            ADD COLUMN IF NOT EXISTS transport_allowance NUMERIC(18,2) DEFAULT 0,
            ADD COLUMN IF NOT EXISTS other_allowances NUMERIC(18,2) DEFAULT 0,
            ADD COLUMN IF NOT EXISTS employment_type VARCHAR(30) DEFAULT 'permanent',
            ADD COLUMN IF NOT EXISTS tax_exempt BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS pension_pin VARCHAR(64),
            ADD COLUMN IF NOT EXISTS tin VARCHAR(64)
    """)
    # Existing staff: treat their current monthly salary as basic, so the
    # engine has something coherent to work from until HR splits it properly.
    op.execute("""
        UPDATE staff SET basic_salary = monthly_salary
         WHERE basic_salary IS NULL AND monthly_salary IS NOT NULL
    """)

    # ---- Seed an UNCONFIRMED starting configuration ----------------------
    #
    # These figures reflect the long-standing PITA structure and are provided
    # ONLY as a starting shape for the tables. They are NOT verified against
    # current law -- the Nigeria Tax Act 2025 took effect in January 2026 and
    # changed both the bands and the relief structure. is_confirmed stays
    # FALSE and the engine refuses to run until an accountant reviews and
    # confirms them.
    op.execute("""
        INSERT INTO payroll_rate_configs
            (id, name, effective_from, is_confirmed, notes)
        SELECT gen_random_uuid(),
               'Nigeria statutory (UNVERIFIED - review before use)',
               DATE '2026-01-01', FALSE,
               'Seeded by migration o4567890123n as a starting shape only. '
               'Figures are NOT verified against current Nigerian tax law. '
               'An accountant must review every band and rate, correct them, '
               'and confirm this configuration before payroll can be run.'
         WHERE NOT EXISTS (SELECT 1 FROM payroll_rate_configs)
    """)

    op.execute("""
        INSERT INTO payroll_tax_bands
            (config_id, sequence, lower_bound, upper_bound, rate_percent)
        SELECT c.id, v.seq, v.lo, v.hi, v.rate
          FROM payroll_rate_configs c
         CROSS JOIN (VALUES
              (1,        0.00,   300000.00,  7.000),
              (2,   300000.00,   600000.00, 11.000),
              (3,   600000.00,  1100000.00, 15.000),
              (4,  1100000.00,  1600000.00, 19.000),
              (5,  1600000.00,  3200000.00, 21.000),
              (6,  3200000.00,        NULL, 24.000)
         ) AS v(seq, lo, hi, rate)
         WHERE NOT EXISTS (SELECT 1 FROM payroll_tax_bands)
    """)

    op.execute("""
        INSERT INTO payroll_rate_items
            (config_id, code, value, basis, description)
        SELECT c.id, v.code, v.val, v.basis, v.descr
          FROM payroll_rate_configs c
         CROSS JOIN (VALUES
              ('PENSION_EMPLOYEE', 8.0000, 'PENSIONABLE',
               'Employee pension contribution'),
              ('PENSION_EMPLOYER', 10.0000, 'PENSIONABLE',
               'Employer pension contribution (a company cost, not a deduction)'),
              ('NHF', 2.5000, 'BASIC',
               'National Housing Fund'),
              ('NHIA_EMPLOYEE', 5.0000, 'BASIC',
               'Health insurance employee portion'),
              ('NHIA_EMPLOYER', 10.0000, 'BASIC',
               'Health insurance employer portion'),
              ('CRA_FIXED', 200000.0000, 'FIXED',
               'Consolidated relief: fixed component (annual)'),
              ('CRA_MIN_PERCENT_GROSS', 1.0000, 'GROSS',
               'Consolidated relief: the fixed component or this %% of gross, '
               'whichever is higher'),
              ('CRA_PERCENT_GROSS', 20.0000, 'GROSS',
               'Consolidated relief: additional %% of gross'),
              ('MINIMUM_TAX_PERCENT', 1.0000, 'GROSS',
               'Minimum tax where computed PAYE falls below this'),
              ('OVERTIME_MULTIPLIER', 1.5000, 'FIXED',
               'Overtime paid at this multiple of the hourly rate'),
              ('STANDARD_MONTHLY_HOURS', 160.0000, 'FIXED',
               'Hours per month before overtime applies')
         ) AS v(code, val, basis, descr)
         WHERE NOT EXISTS (SELECT 1 FROM payroll_rate_items)
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS payslip_components CASCADE")
    op.execute("DROP TABLE IF EXISTS payslips CASCADE")
    op.execute("DROP TABLE IF EXISTS payroll_runs CASCADE")
    op.execute("DROP TABLE IF EXISTS staff_deductions CASCADE")
    op.execute("DROP TABLE IF EXISTS payroll_tax_bands CASCADE")
    op.execute("DROP TABLE IF EXISTS payroll_rate_items CASCADE")
    op.execute("DROP TABLE IF EXISTS payroll_rate_configs CASCADE")
    op.execute("""
        ALTER TABLE staff
            DROP COLUMN IF EXISTS basic_salary,
            DROP COLUMN IF EXISTS housing_allowance,
            DROP COLUMN IF EXISTS transport_allowance,
            DROP COLUMN IF EXISTS other_allowances,
            DROP COLUMN IF EXISTS employment_type,
            DROP COLUMN IF EXISTS tax_exempt,
            DROP COLUMN IF EXISTS pension_pin,
            DROP COLUMN IF EXISTS tin
    """)
