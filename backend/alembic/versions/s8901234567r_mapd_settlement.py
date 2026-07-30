"""MAPD: multi-account intelligent payment distribution.

Revision ID: s8901234567r
Revises: r7890123456q
Create Date: 2026-07-26

One customer payment, many destination accounts. This migration creates the
configuration a distribution needs (business units, revenue centres, financial
accounts, per-product account mapping, settlement rules) and the immutable
record it produces (settlements, settlement details, refunds, audit log).

Two design decisions are worth stating here rather than leaving to be inferred:

  * **A financial account is not a GL account.** `financial_accounts` is the
    operational destination -- a bank account, a wallet, a business unit's
    purse. Each one maps onto exactly one postable account in `gl_accounts`,
    so the ledger stays the single source of accounting truth and the
    distribution never becomes a second, disagreeing set of books.

  * **Settlement details are append-only, enforced by the database.** A
    trigger rejects UPDATE and DELETE. Application-level immutability is a
    convention; a trigger is a guarantee, and this table is what an auditor
    reads to see where a customer's money went. Corrections are reversals
    (see mapd_refunds), never edits.

Allocation types, and why there are two:
  CASH        the money physically moves to that account. Dr destination /
              Cr the account the payment landed in. These MUST sum to the
              payment, or nothing is settled.
  OBLIGATION  the allocation creates a debt to someone else -- a distributor
              commission, a revenue share. Dr expense / Cr liability. These
              are ADDITIONAL to the cash split, not carved out of it, because
              owing a partner 10% does not reduce the cash you banked.
"""
from alembic import op
import sqlalchemy as sa

revision = 's8901234567r'
down_revision = 'r7890123456q'
branch_labels = None
depends_on = None


# Accounts the distribution engine needs that the base chart does not carry.
NEW_ACCOUNTS = [
    # code, name, type, normal, postable
    ("1250", "Settlement Clearing", "ASSET", "DEBIT", True),
    ("2500", "Settlement Obligations Payable", "LIABILITY", "CREDIT", True),
    ("2510", "Distributor Commission Payable", "LIABILITY", "CREDIT", True),
    ("6310", "Distributor & Partner Commissions", "EXPENSE", "DEBIT", True),
    ("6320", "Revenue Share Expense", "EXPENSE", "DEBIT", True),
]

PAYMENT_METHODS = [
    # code, name, category, requires_reference, sort
    ("bank_transfer", "Bank Transfer", "BANK", True, 10),
    ("pos", "POS Terminal", "CARD", True, 20),
    ("card", "Card Payment", "CARD", True, 30),
    ("qr", "QR Code", "DIGITAL", True, 40),
    ("ussd", "USSD", "DIGITAL", True, 50),
    ("wallet", "Wallet", "DIGITAL", True, 60),
    ("mobile_money", "Mobile Money", "DIGITAL", True, 70),
    ("cash", "Cash", "CASH", False, 80),
    ("cheque", "Cheque", "BANK", True, 90),
    ("unspecified", "Unspecified", "OTHER", False, 999),
]


def upgrade():
    # ---- organisational dimensions ------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS business_units (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code VARCHAR(32) UNIQUE NOT NULL,
            name VARCHAR(255) NOT NULL,
            description TEXT,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS revenue_centers (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code VARCHAR(32) UNIQUE NOT NULL,
            name VARCHAR(255) NOT NULL,
            business_unit_id UUID REFERENCES business_units(id),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_revenue_centers_bu "
               "ON revenue_centers (business_unit_id)")

    # ---- destination accounts -----------------------------------------
    #
    # gl_account_code is NOT NULL and references the chart of accounts: an
    # account money can be sent to but that the ledger has never heard of is
    # exactly how a distribution engine ends up disagreeing with the books.
    op.execute("""
        CREATE TABLE IF NOT EXISTS financial_accounts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code VARCHAR(32) UNIQUE NOT NULL,
            name VARCHAR(255) NOT NULL,
            account_kind VARCHAR(20) NOT NULL DEFAULT 'BANK',
            gl_account_code VARCHAR(20) NOT NULL REFERENCES gl_accounts(code),
            -- Only meaningful for OBLIGATION accounts: the liability credited
            -- when the expense above is debited.
            contra_gl_account_code VARCHAR(20) REFERENCES gl_accounts(code),
            bank_name VARCHAR(255),
            account_number_enc TEXT,
            account_name VARCHAR(255),
            currency VARCHAR(3) NOT NULL DEFAULT 'NGN',
            business_unit_id UUID REFERENCES business_units(id),
            status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
            description TEXT,
            created_by UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_fin_acct_kind CHECK (account_kind IN
                ('BANK','CASH','WALLET','VIRTUAL','OBLIGATION')),
            CONSTRAINT ck_fin_acct_status CHECK (status IN
                ('ACTIVE','SUSPENDED','CLOSED')),
            -- An obligation account without the liability side would post a
            -- one-legged entry, which post_entry would reject at run time.
            -- Reject it at configuration time instead.
            CONSTRAINT ck_fin_acct_obligation CHECK (
                account_kind <> 'OBLIGATION'
                OR contra_gl_account_code IS NOT NULL)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_fin_acct_status "
               "ON financial_accounts (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_fin_acct_bu "
               "ON financial_accounts (business_unit_id)")

    # ---- per-product financial configuration --------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS product_accounts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            product_id UUID NOT NULL UNIQUE REFERENCES products(id)
                ON DELETE CASCADE,
            sales_account_code VARCHAR(20) REFERENCES gl_accounts(code),
            cost_account_code VARCHAR(20) REFERENCES gl_accounts(code),
            inventory_account_code VARCHAR(20) REFERENCES gl_accounts(code),
            tax_group VARCHAR(32),
            business_unit_id UUID REFERENCES business_units(id),
            revenue_center_id UUID REFERENCES revenue_centers(id),
            settlement_priority INTEGER NOT NULL DEFAULT 100,
            -- Used when no settlement rule matches: 100% of the product's
            -- share goes here. Keeps a newly registered product settleable
            -- without demanding a full rule be authored first.
            default_financial_account_id UUID REFERENCES financial_accounts(id),
            notes TEXT,
            updated_by UUID,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_product_accounts_bu "
               "ON product_accounts (business_unit_id)")

    # ---- settlement rules ---------------------------------------------
    #
    # scope makes resolution deterministic: PRODUCT beats BUSINESS_UNIT beats
    # GLOBAL, and within a scope the lowest priority number wins. Without a
    # total order, two overlapping rules would settle a payment differently
    # depending on row order.
    op.execute("""
        CREATE TABLE IF NOT EXISTS settlement_rules (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code VARCHAR(64) UNIQUE NOT NULL,
            name VARCHAR(255) NOT NULL,
            scope VARCHAR(20) NOT NULL DEFAULT 'PRODUCT',
            product_id UUID REFERENCES products(id) ON DELETE CASCADE,
            business_unit_id UUID REFERENCES business_units(id),
            basis VARCHAR(20) NOT NULL DEFAULT 'PERCENTAGE',
            priority INTEGER NOT NULL DEFAULT 100,
            effective_from DATE NOT NULL DEFAULT CURRENT_DATE,
            effective_to DATE,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            description TEXT,
            created_by UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_rule_scope CHECK (scope IN
                ('PRODUCT','BUSINESS_UNIT','GLOBAL')),
            CONSTRAINT ck_rule_basis CHECK (basis IN
                ('PERCENTAGE','FIXED','PER_UNIT')),
            CONSTRAINT ck_rule_dates CHECK (
                effective_to IS NULL OR effective_to >= effective_from),
            -- A PRODUCT rule with no product, or a BUSINESS_UNIT rule with no
            -- unit, can never match anything and would look configured.
            CONSTRAINT ck_rule_target CHECK (
                (scope = 'PRODUCT' AND product_id IS NOT NULL)
             OR (scope = 'BUSINESS_UNIT' AND business_unit_id IS NOT NULL)
             OR (scope = 'GLOBAL'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_rules_product "
               "ON settlement_rules (product_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_rules_lookup "
               "ON settlement_rules (scope, is_active, priority)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS settlement_rule_splits (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            rule_id UUID NOT NULL REFERENCES settlement_rules(id)
                ON DELETE CASCADE,
            financial_account_id UUID NOT NULL
                REFERENCES financial_accounts(id),
            allocation_type VARCHAR(16) NOT NULL DEFAULT 'CASH',
            percentage NUMERIC(9,6),
            fixed_amount NUMERIC(18,2),
            rate_per_unit NUMERIC(18,6),
            -- The residual split absorbs whatever the fixed / per-unit splits
            -- did not consume, so a rule always allocates the line exactly.
            is_residual BOOLEAN NOT NULL DEFAULT FALSE,
            sort_order INTEGER NOT NULL DEFAULT 0,
            description TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_split_type CHECK (allocation_type IN
                ('CASH','OBLIGATION')),
            CONSTRAINT ck_split_amounts CHECK (
                percentage IS NULL OR (percentage >= 0 AND percentage <= 100)),
            CONSTRAINT ck_split_nonneg CHECK (
                (fixed_amount IS NULL OR fixed_amount >= 0)
                AND (rate_per_unit IS NULL OR rate_per_unit >= 0)),
            -- Exactly one basis value, or the residual flag. A split with both
            -- a percentage and a fixed amount has no defined meaning.
            CONSTRAINT ck_split_one_basis CHECK (
                (CASE WHEN percentage IS NOT NULL THEN 1 ELSE 0 END
               + CASE WHEN fixed_amount IS NOT NULL THEN 1 ELSE 0 END
               + CASE WHEN rate_per_unit IS NOT NULL THEN 1 ELSE 0 END
               + CASE WHEN is_residual THEN 1 ELSE 0 END) = 1)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_splits_rule "
               "ON settlement_rule_splits (rule_id)")
    # At most one residual per rule -- two would each claim "the rest".
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_split_one_residual
            ON settlement_rule_splits (rule_id, allocation_type)
         WHERE is_residual
    """)

    # ---- payment methods ----------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS payment_methods (
            code VARCHAR(32) PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            category VARCHAR(20) NOT NULL DEFAULT 'OTHER',
            requires_reference BOOLEAN NOT NULL DEFAULT FALSE,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            sort_order INTEGER NOT NULL DEFAULT 100
        )
    """)

    # ---- the settlement record ----------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS settlements (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            settlement_reference VARCHAR(64) UNIQUE NOT NULL,
            payment_id UUID NOT NULL REFERENCES payments(id),
            invoice_id UUID NOT NULL REFERENCES invoices(id),
            sales_order_id UUID REFERENCES sales_orders(id),
            gross_amount NUMERIC(18,2) NOT NULL,
            allocated_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
            obligation_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
            status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
            failure_reason TEXT,
            attempt_number INTEGER NOT NULL DEFAULT 1,
            payment_method VARCHAR(50),
            source_gl_account_code VARCHAR(20),
            journal_entry_id UUID,
            distributed_at TIMESTAMPTZ,
            created_by UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_settlement_status CHECK (status IN
                ('PENDING','COMPLETED','FAILED','SKIPPED','REVERSED')),
            CONSTRAINT ck_settlement_amounts CHECK (
                gross_amount > 0 AND allocated_amount >= 0
                AND obligation_amount >= 0)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_settlements_payment "
               "ON settlements (payment_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_settlements_invoice "
               "ON settlements (invoice_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_settlements_status "
               "ON settlements (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_settlements_created "
               "ON settlements (created_at)")
    # THE idempotency guard: one live settlement per payment, enforced by the
    # database rather than by a check-then-insert two concurrent verifications
    # could both pass. FAILED and REVERSED rows are excluded so a retry can
    # insert a fresh attempt while the failed attempts remain as history.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_settlement_live_per_payment
            ON settlements (payment_id)
         WHERE status IN ('PENDING','COMPLETED','SKIPPED')
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS settlement_details (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            settlement_id UUID NOT NULL REFERENCES settlements(id),
            invoice_line_id UUID REFERENCES invoice_lines(id),
            product_id UUID REFERENCES products(id),
            financial_account_id UUID NOT NULL
                REFERENCES financial_accounts(id),
            rule_id UUID REFERENCES settlement_rules(id),
            split_id UUID REFERENCES settlement_rule_splits(id),
            allocation_type VARCHAR(16) NOT NULL DEFAULT 'CASH',
            basis VARCHAR(20) NOT NULL DEFAULT 'PERCENTAGE',
            amount NUMERIC(18,2) NOT NULL,
            description TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_detail_type CHECK (allocation_type IN
                ('CASH','OBLIGATION')),
            CONSTRAINT ck_detail_amount CHECK (amount > 0)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_details_settlement "
               "ON settlement_details (settlement_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_details_account "
               "ON settlement_details (financial_account_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_details_product "
               "ON settlement_details (product_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_details_line "
               "ON settlement_details (invoice_line_id)")

    # ---- refunds -------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS mapd_refunds (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            refund_reference VARCHAR(64) UNIQUE NOT NULL,
            settlement_id UUID NOT NULL REFERENCES settlements(id),
            payment_id UUID REFERENCES payments(id),
            invoice_id UUID REFERENCES invoices(id),
            amount NUMERIC(18,2) NOT NULL,
            is_full_reversal BOOLEAN NOT NULL DEFAULT TRUE,
            reason TEXT NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'COMPLETED',
            journal_entry_id UUID,
            approved_by UUID,
            created_by UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_refund_status CHECK (status IN
                ('PENDING','COMPLETED','FAILED')),
            CONSTRAINT ck_refund_amount CHECK (amount > 0)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_refunds_settlement "
               "ON mapd_refunds (settlement_id)")

    # ---- audit log -----------------------------------------------------
    #
    # Separate from the generic audit_logs table on purpose: this one is
    # append-only at the database level and is the record an auditor reads to
    # follow one customer's money from receipt to destination account.
    op.execute("""
        CREATE TABLE IF NOT EXISTS mapd_audit_logs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            event_type VARCHAR(50) NOT NULL,
            entity_type VARCHAR(50),
            entity_id UUID,
            payment_id UUID,
            settlement_id UUID,
            actor_user_id UUID,
            actor_label VARCHAR(255),
            detail JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_mapd_audit_event "
               "ON mapd_audit_logs (event_type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_mapd_audit_payment "
               "ON mapd_audit_logs (payment_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_mapd_audit_settlement "
               "ON mapd_audit_logs (settlement_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_mapd_audit_created "
               "ON mapd_audit_logs (created_at)")

    # ---- immutability, enforced by the database ------------------------
    # RAISE ... USING MESSAGE rather than RAISE 'text %', arg: a literal % in
    # migration SQL is ambiguous under drivers that use pyformat parameters,
    # and a migration that fails to apply is worse than a terser message.
    op.execute("""
        CREATE OR REPLACE FUNCTION mapd_reject_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION USING MESSAGE =
                'Table ' || TG_TABLE_NAME || ' is append-only: ' || TG_OP ||
                ' is not permitted. Correct a settlement by reversing it '
                '(see mapd_refunds), never by editing the record of where '
                'money went.';
        END;
        $$ LANGUAGE plpgsql
    """)
    for table in ("settlement_details", "mapd_audit_logs"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {table}")
        op.execute(f"""
            CREATE TRIGGER trg_{table}_immutable
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION mapd_reject_mutation()
        """)

    # Settlements themselves must stay updatable (a PENDING row becomes
    # COMPLETED), but the figures that define the event may never change and
    # the row may never be deleted.
    op.execute("""
        CREATE OR REPLACE FUNCTION mapd_settlement_guard()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Settlements cannot be deleted; reverse the settlement '
                    'instead so the record of the original remains.';
            END IF;
            IF NEW.payment_id <> OLD.payment_id
               OR NEW.gross_amount <> OLD.gross_amount
               OR NEW.settlement_reference <> OLD.settlement_reference THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Settlement ' || OLD.settlement_reference ||
                    ' is immutable in payment_id, gross_amount and reference.';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_settlements_guard ON settlements")
    op.execute("""
        CREATE TRIGGER trg_settlements_guard
        BEFORE UPDATE OR DELETE ON settlements
        FOR EACH ROW EXECUTE FUNCTION mapd_settlement_guard()
    """)

    # ---- seed ----------------------------------------------------------
    for code, name, atype, normal, postable in NEW_ACCOUNTS:
        op.execute(sa.text("""
            INSERT INTO gl_accounts
                (id, code, name, account_type, normal_balance, is_postable)
            VALUES (gen_random_uuid(), :c, :n, :t, :nb, :p)
            ON CONFLICT (code) DO NOTHING
        """).bindparams(c=code, n=name, t=atype, nb=normal, p=postable))

    for code, name, category, needs_ref, sort in PAYMENT_METHODS:
        op.execute(sa.text("""
            INSERT INTO payment_methods
                (code, name, category, requires_reference, sort_order)
            VALUES (:c, :n, :cat, :r, :s)
            ON CONFLICT (code) DO NOTHING
        """).bindparams(c=code, n=name, cat=category, r=needs_ref, s=sort))


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS trg_settlements_guard ON settlements")
    op.execute("DROP TRIGGER IF EXISTS trg_settlement_details_immutable "
               "ON settlement_details")
    op.execute("DROP TRIGGER IF EXISTS trg_mapd_audit_logs_immutable "
               "ON mapd_audit_logs")
    op.execute("DROP FUNCTION IF EXISTS mapd_settlement_guard()")
    op.execute("DROP FUNCTION IF EXISTS mapd_reject_mutation()")

    op.execute("DROP TABLE IF EXISTS mapd_audit_logs CASCADE")
    op.execute("DROP TABLE IF EXISTS mapd_refunds CASCADE")
    op.execute("DROP TABLE IF EXISTS settlement_details CASCADE")
    op.execute("DROP TABLE IF EXISTS settlements CASCADE")
    op.execute("DROP TABLE IF EXISTS settlement_rule_splits CASCADE")
    op.execute("DROP TABLE IF EXISTS settlement_rules CASCADE")
    op.execute("DROP TABLE IF EXISTS product_accounts CASCADE")
    op.execute("DROP TABLE IF EXISTS financial_accounts CASCADE")
    op.execute("DROP TABLE IF EXISTS revenue_centers CASCADE")
    op.execute("DROP TABLE IF EXISTS business_units CASCADE")
    op.execute("DROP TABLE IF EXISTS payment_methods CASCADE")
