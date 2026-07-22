"""Tax/VAT returns and QC inspection costing.

Revision ID: r7890123456q
Revises: q6789012345p
Create Date: 2026-07-21

Completes the accounting spec with the three subsystems that were still
outstanding, of which two need schema:

  * **VAT returns.** A filing register over the ledger. Output VAT is whatever
    has been posted to VAT Payable (2300); input VAT is the recoverable VAT on
    purchases. This adds `vat_returns` to hold a FILED snapshot -- the figures
    as they were declared to FIRS -- so a later ledger correction cannot
    silently rewrite a return that has already been submitted. It also adds a
    dedicated **Input VAT Recoverable** asset account (1360) so recoverable VAT
    has a home distinct from the expense it sat inside before.

  * **QC inspection costing.** `qc_inspections` records the cost of a quality
    check and, once posted, carries the id of the journal entry that put that
    cost into the ledger against the QC cost centre -- so lab spend stops being
    invisible to the P&L and the cost-centre report.

(The third subsystem, the executive dashboard, is pure read-model over the
ledger and needs no tables.)

Maintenance costing needs no new table: a machine_maintenance row already
carries its cost, and the posting is keyed on that row's id through the
ledger's own idempotency guard.
"""
from alembic import op
import sqlalchemy as sa

revision = 'r7890123456q'
down_revision = 'q6789012345p'
branch_labels = None
depends_on = None


def upgrade():
    # -- Input VAT recoverable account -------------------------------------
    # An asset: VAT paid on purchases that reduces what is remitted to FIRS.
    # Kept separate from the expense it used to be buried in so the VAT return
    # can read it directly.
    op.execute(sa.text("""
        INSERT INTO gl_accounts
            (id, code, name, account_type, normal_balance, is_postable)
        VALUES (gen_random_uuid(), :c, :n, 'ASSET', 'DEBIT', TRUE)
        ON CONFLICT (code) DO NOTHING
    """).bindparams(c='1360', n='Input VAT Recoverable'))

    # -- VAT returns -------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS vat_returns (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            period_start DATE NOT NULL,
            period_end DATE NOT NULL,
            -- The three figures a VAT return declares, SNAPSHOTTED at filing.
            -- They are computed from the ledger when the return is prepared,
            -- but frozen here on filing: the return is what was declared, and
            -- a later back-dated correction must not rewrite history.
            output_vat NUMERIC(18,2) NOT NULL DEFAULT 0,
            input_vat  NUMERIC(18,2) NOT NULL DEFAULT 0,
            -- output - input. Positive is remittable to FIRS; negative is a
            -- credit carried forward. Stored rather than derived so the return
            -- reads the same even if the sign convention ever changes.
            net_payable NUMERIC(18,2) NOT NULL DEFAULT 0,
            status VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
            firs_reference VARCHAR(100),
            filed_by VARCHAR(255),
            filed_at TIMESTAMPTZ,
            paid_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
            -- The journal entry that remitted the VAT (Dr VAT Payable/Cr Bank).
            payment_entry_id UUID REFERENCES gl_journal_entries(id),
            notes TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT ck_vat_return_status CHECK (status IN
                ('DRAFT','FILED','PAID')),
            CONSTRAINT ck_vat_return_period CHECK (period_end >= period_start),
            -- A FILED return must record who filed it: an unsigned declaration
            -- is not a declaration.
            CONSTRAINT ck_vat_return_filed_has_author CHECK (
                status = 'DRAFT' OR filed_by IS NOT NULL)
        )
    """)
    # One filed (or paid) return per period -- a period cannot be declared to
    # FIRS twice. DRAFTs are working documents and may coexist.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_vat_return_period_filed
            ON vat_returns (period_start, period_end)
         WHERE status <> 'DRAFT'
    """)

    # -- QC inspections ----------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS qc_inspections (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            inspection_number VARCHAR(64) UNIQUE NOT NULL,
            inspection_date DATE NOT NULL DEFAULT CURRENT_DATE,
            -- What was inspected: a batch number, product, or incoming lot.
            subject VARCHAR(255) NOT NULL,
            inspection_type VARCHAR(50) NOT NULL DEFAULT 'in_process',
            result VARCHAR(20) NOT NULL DEFAULT 'pending',
            cost NUMERIC(18,2) NOT NULL DEFAULT 0,
            cost_centre VARCHAR(50) NOT NULL DEFAULT 'QC',
            inspector VARCHAR(255),
            product_id UUID,
            batch_number VARCHAR(64),
            -- Set when the cost is posted; its presence is what tells the API
            -- the cost has already reached the ledger (idempotency).
            gl_entry_id UUID REFERENCES gl_journal_entries(id),
            notes TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT ck_qc_result CHECK (result IN
                ('pass','fail','pending','conditional')),
            CONSTRAINT ck_qc_cost CHECK (cost >= 0)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_qc_inspections_date "
               "ON qc_inspections (inspection_date)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_qc_inspections_result "
               "ON qc_inspections (result)")


def downgrade():
    op.execute("DROP TABLE IF EXISTS qc_inspections CASCADE")
    op.execute("DROP TABLE IF EXISTS vat_returns CASCADE")
    op.execute("DELETE FROM gl_accounts WHERE code = '1360'")
