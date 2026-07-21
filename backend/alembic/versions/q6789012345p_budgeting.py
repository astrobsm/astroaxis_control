"""Cost centres and budgeting.

Revision ID: q6789012345p
Revises: p5678901234o
Create Date: 2026-07-20

`gl_journal_lines.cost_centre` was a free-text VARCHAR with nothing behind it
-- the same defect supplier names had. "Production", "production" and
"PRODUCTION " would report as three separate cost centres, so any departmental
analysis silently split costs across near-duplicate rows.

This adds:
  * a cost centre master with case-insensitive codes, backfilled from
    whatever free text already exists;
  * budgets, versioned and approvable, holding one figure per
    (account, cost centre, period) so variance can be reported at whatever
    granularity finance needs.

Budgets are deliberately NOT enforced -- nothing here blocks spending over
budget. A budget is a plan to compare against, and a system that refuses a
legitimate emergency purchase because a number in a table says no is a system
people route around.
"""
from alembic import op
import sqlalchemy as sa

revision = 'q6789012345p'
down_revision = 'p5678901234o'
branch_labels = None
depends_on = None


COST_CENTRES = [
    ("PROD", "Production", "operations"),
    ("QC", "Quality Control", "operations"),
    ("WH", "Warehouse", "operations"),
    ("MAINT", "Maintenance", "operations"),
    ("SALES", "Sales", "commercial"),
    ("MKT", "Marketing", "commercial"),
    ("ADMIN", "Administration", "support"),
    ("FIN", "Finance", "support"),
    ("HR", "Human Resources", "support"),
    ("RND", "Research & Development", "support"),
    ("MGMT", "Management", "support"),
]


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS cost_centres (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code VARCHAR(50) NOT NULL,
            name VARCHAR(255) NOT NULL,
            category VARCHAR(50) DEFAULT 'operations',
            parent_id UUID REFERENCES cost_centres(id),
            manager_name VARCHAR(255),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            notes TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    # Case-insensitive uniqueness: the whole reason the master exists.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_cost_centres_code_ci
            ON cost_centres (UPPER(TRIM(code)))
    """)

    for code, name, category in COST_CENTRES:
        op.execute(sa.text("""
            INSERT INTO cost_centres (code, name, category)
            SELECT :c, :n, :cat
             WHERE NOT EXISTS (
                 SELECT 1 FROM cost_centres
                  WHERE UPPER(TRIM(code)) = UPPER(TRIM(:c)))
        """).bindparams(c=code, n=name, cat=category))

    # Adopt whatever free text is already on journal lines, so historical
    # postings are not orphaned from the master.
    op.execute("""
        INSERT INTO cost_centres (code, name, category, notes)
        SELECT DISTINCT UPPER(TRIM(l.cost_centre)),
               TRIM(l.cost_centre),
               'unclassified',
               'Adopted from existing journal lines by migration q6789012345p'
          FROM gl_journal_lines l
         WHERE l.cost_centre IS NOT NULL
           AND TRIM(l.cost_centre) <> ''
           AND NOT EXISTS (
               SELECT 1 FROM cost_centres c
                WHERE UPPER(TRIM(c.code)) = UPPER(TRIM(l.cost_centre)))
    """)

    # Normalise existing journal lines onto the canonical code, so the
    # near-duplicates collapse.
    op.execute("""
        UPDATE gl_journal_lines l
           SET cost_centre = c.code
          FROM cost_centres c
         WHERE l.cost_centre IS NOT NULL
           AND UPPER(TRIM(l.cost_centre)) = UPPER(TRIM(c.code))
           AND l.cost_centre <> c.code
    """)

    # ---- Budgets ----------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(255) NOT NULL,
            fiscal_year INTEGER NOT NULL,
            period_start DATE NOT NULL,
            period_end DATE NOT NULL,
            -- DRAFT budgets are working documents; only an APPROVED budget
            -- is reported against, so variance cannot shift under a reader.
            status VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
            version INTEGER NOT NULL DEFAULT 1,
            supersedes_id UUID REFERENCES budgets(id),
            approved_by VARCHAR(255),
            approved_at TIMESTAMPTZ,
            notes TEXT,
            created_by UUID,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT ck_budget_status CHECK (status IN
                ('DRAFT','APPROVED','SUPERSEDED','CLOSED')),
            CONSTRAINT ck_budget_period CHECK (period_end >= period_start),
            CONSTRAINT ck_budget_approved_has_author CHECK (
                status <> 'APPROVED' OR approved_by IS NOT NULL)
        )
    """)
    # Only one approved budget per fiscal year -- two would make "over
    # budget?" unanswerable.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_budget_approved_year
            ON budgets (fiscal_year) WHERE status = 'APPROVED'
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS budget_lines (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            budget_id UUID NOT NULL REFERENCES budgets(id) ON DELETE CASCADE,
            account_code VARCHAR(20) NOT NULL,
            cost_centre VARCHAR(50),
            -- The month this figure applies to. Annual budgets are stored as
            -- twelve rows rather than one, so month-to-date variance is a
            -- filter rather than an apportionment guess.
            period_month DATE NOT NULL,
            amount NUMERIC(18,2) NOT NULL,
            notes TEXT,
            CONSTRAINT uq_budget_line UNIQUE
                (budget_id, account_code, cost_centre, period_month),
            CONSTRAINT ck_budget_line_amount CHECK (amount >= 0)
        )
    """)
    for idx, col in [("budget", "budget_id"), ("account", "account_code"),
                     ("cc", "cost_centre"), ("month", "period_month")]:
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_budget_lines_{idx} "
                   f"ON budget_lines ({col})")

    op.execute("CREATE INDEX IF NOT EXISTS ix_gl_lines_cc_lookup "
               "ON gl_journal_lines (cost_centre) "
               "WHERE cost_centre IS NOT NULL")


def downgrade():
    op.execute("DROP TABLE IF EXISTS budget_lines CASCADE")
    op.execute("DROP TABLE IF EXISTS budgets CASCADE")
    op.execute("DROP TABLE IF EXISTS cost_centres CASCADE")
    op.execute("DROP INDEX IF EXISTS ix_gl_lines_cc_lookup")
