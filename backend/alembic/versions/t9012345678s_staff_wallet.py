"""Staff operational wallet & expense accountability.

Revision ID: t9012345678s
Revises: s8901234567r
Create Date: 2026-09-09

WHAT A WALLET IS, AND IS NOT
----------------------------
This module does not hold money and does not move money. The company keeps
using its existing bank account. When the Managing Director hands the Factory
Supervisor N100,000 -- by transfer, by cash, by company card -- that movement
happens OUTSIDE this application. What is recorded here is the consequence:

    "The company has entrusted this employee with N100,000 of its money."

Everything the employee then spends must be evidenced, and whatever is left is
still owed back. The wallet is an accountability ledger, not a bank account.

THE ACCOUNTING DECISION THAT SHAPES THE SCHEMA
---------------------------------------------
Issuing an advance is NOT an expense. It converts cash into a claim on the
employee, so it is an asset swap:

    funding    Dr 1120 Staff Operational Advances / Cr 1100 Cash | 1200 Bank
    expense    Dr <category expense account>      / Cr 1120 Staff Advances
    return     Dr 1100 Cash | 1200 Bank           / Cr 1120 Staff Advances

Which means a wallet balance is exactly that employee's slice of one asset
account, and the wallet can never disagree with the books. Booking the advance
straight to expense -- the common shortcut -- would overstate costs on the day
of funding, understate them for the whole period the money is actually spent,
and leave nothing on the balance sheet saying the employee owes it back.

A reimbursement is the mirror case: the employee spent their OWN money, so the
company owes them. Dr expense / Cr 2160 Staff Reimbursements Payable. It never
touches a wallet balance, because no company money was entrusted.

IMMUTABILITY IS ENFORCED BY THE DATABASE, NOT BY CONVENTION
-----------------------------------------------------------
wallet_ledger and wallet_audit_logs carry triggers that reject UPDATE and
DELETE outright, and wallet_fundings/wallet_expenses carry guards that freeze
the figures which define the event. Application-level immutability is a
promise; a trigger is a guarantee, and these tables are what an auditor reads.
A mistake is corrected by appending a reversal that points at the original, so
the record shows both what was believed at the time and the correction.

This follows the precedent set by the MAPD settlement migration
(s8901234567r) rather than inventing a second style for the same problem.
"""
from alembic import op
import sqlalchemy as sa

revision = 't9012345678s'
down_revision = 's8901234567r'
branch_labels = None
depends_on = None


# Accounts the wallet needs that the base chart does not carry.
NEW_ACCOUNTS = [
    # code, name, type, normal, postable
    ("1120", "Staff Operational Advances", "ASSET", "DEBIT", True),
    ("2160", "Staff Reimbursements Payable", "LIABILITY", "CREDIT", True),
]

# Starting categories, each mapped onto the chart of accounts so an approved
# expense reaches the P&L classified rather than pooled into "sundries".
# Administrators add to these at run time; nothing here is hard-coded in the
# application.
CATEGORIES = [
    # code, name, gl_account_code, requires_receipt, sort
    ("FUEL",         "Fuel",                    "6210", True,  10),
    ("TRANSPORT",    "Transportation",          "6200", True,  20),
    ("MAINTENANCE",  "Maintenance & Repairs",   "5450", True,  30),
    ("FACTORY",      "Factory Supplies",        "5440", True,  40),
    ("OFFICE",       "Office Supplies",         "6600", True,  50),
    ("CUSTOMER",     "Customer Relations",      "6300", True,  60),
    ("MARKETING",    "Marketing & Promotion",   "6300", True,  70),
    ("ADVERTISING",  "Advertising",             "6300", True,  80),
    ("DELIVERY",     "Delivery & Haulage",      "6200", True,  90),
    ("COMMS",        "Communication (Airtime)", "6400", False, 100),
    ("DATA",         "Internet & Data",         "6400", False, 110),
    ("UTILITIES",    "Utilities",               "5410", True,  120),
    ("SECURITY",     "Security",                "6510", True,  130),
    ("CLEANING",     "Cleaning & Sanitation",   "6520", True,  140),
    ("WELFARE",      "Staff Welfare",           "6110", True,  150),
    ("EMERGENCY",    "Emergency",               "6600", True,  160),
    ("OTHER",        "Other",                   "6600", True,  999),
]

# Default approval ladder (section 12 of the specification). These are ROWS,
# not constants in code, precisely so management can change the thresholds
# without a deployment.
APPROVAL_RULES = [
    # min, max, tier
    ("0",     "10000",  "SELF"),
    ("10000", "50000",  "SUPERVISOR"),
    ("50000", None,     "MANAGEMENT"),
]


def upgrade():
    # ---- configuration: expense categories -----------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS wallet_expense_categories (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code VARCHAR(32) UNIQUE NOT NULL,
            name VARCHAR(255) NOT NULL,
            description TEXT,
            -- Where an approved expense in this category lands in the books.
            -- Nullable so a category can be created before finance has
            -- decided its account; posting falls back to 6600 Office Expenses
            -- rather than refusing the expense.
            gl_account_code VARCHAR(20) REFERENCES gl_accounts(code),
            requires_receipt BOOLEAN NOT NULL DEFAULT TRUE,
            default_monthly_cap NUMERIC(18,2),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            sort_order INTEGER NOT NULL DEFAULT 100,
            created_by UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_wal_cat_cap CHECK (
                default_monthly_cap IS NULL OR default_monthly_cap > 0)
        )
    """)

    # ---- the wallets ---------------------------------------------------
    #
    # user_id, not staff_id, is the owner: a wallet holder must be able to log
    # in to record expenses, and `staff` is the payroll/attendance record with
    # no login. staff_id is kept alongside as an optional link so payroll and
    # wallet reporting can be joined for the same person.
    #
    # The cached totals (balance, total_funded, ...) are DERIVED. wallet_ledger
    # is the truth; these columns exist so a dashboard listing 40 wallets does
    # not aggregate the whole ledger 40 times. They are rewritten from the
    # ledger inside the same transaction as every movement, and
    # verify_wallet_integrity() re-derives and compares them on demand.
    op.execute("""
        CREATE TABLE IF NOT EXISTS wallets (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            wallet_number VARCHAR(32) UNIQUE NOT NULL,
            user_id UUID NOT NULL REFERENCES users(id),
            staff_id UUID REFERENCES staff(id),
            wallet_type VARCHAR(24) NOT NULL DEFAULT 'INDIVIDUAL',
            department VARCHAR(100),
            purpose TEXT,
            currency VARCHAR(3) NOT NULL DEFAULT 'NGN',

            -- Spending controls. NULL means "no limit of this kind".
            single_txn_limit NUMERIC(18,2),
            daily_limit NUMERIC(18,2),
            monthly_limit NUMERIC(18,2),
            -- Per-wallet override of the approval ladder's SELF band.
            self_approve_limit NUMERIC(18,2),

            status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',

            -- Derived caches; see the note above.
            balance NUMERIC(18,2) NOT NULL DEFAULT 0,
            total_funded NUMERIC(18,2) NOT NULL DEFAULT 0,
            total_spent NUMERIC(18,2) NOT NULL DEFAULT 0,
            total_returned NUMERIC(18,2) NOT NULL DEFAULT 0,
            total_adjusted NUMERIC(18,2) NOT NULL DEFAULT 0,
            last_transaction_at TIMESTAMPTZ,

            created_by UUID REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT ck_wallet_type CHECK (wallet_type IN
                ('INDIVIDUAL','MARKETING','SALES','FACTORY','LOGISTICS',
                 'PROCUREMENT','ADMIN')),
            CONSTRAINT ck_wallet_status CHECK (status IN
                ('ACTIVE','SUSPENDED','FROZEN','CLOSED')),
            CONSTRAINT ck_wallet_limits CHECK (
                (single_txn_limit IS NULL OR single_txn_limit > 0) AND
                (daily_limit IS NULL OR daily_limit > 0) AND
                (monthly_limit IS NULL OR monthly_limit > 0) AND
                (self_approve_limit IS NULL OR self_approve_limit >= 0)),
            -- One wallet per person per purpose. Without this, "fund John's
            -- factory wallet" becomes ambiguous the moment a second one is
            -- created by accident, and money goes into the wrong ledger.
            CONSTRAINT uq_wallet_user_type UNIQUE (user_id, wallet_type)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_wallets_user ON wallets (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_wallets_status ON wallets (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_wallets_dept ON wallets (department)")

    # ---- the ledger: the single source of truth ------------------------
    #
    # direction is stated in terms of the EMPLOYEE'S ACCOUNTABILITY, not the
    # company's cash:
    #   CREDIT  the employee has been entrusted with more  (funding)
    #   DEBIT   the employee has discharged some of it     (expense, return)
    # so balance = SUM(credits) - SUM(debits) = what they still answer for.
    #
    # balance_after is a snapshot taken under the wallet row lock, so the
    # transaction list renders a running balance without re-summing, and any
    # divergence between the snapshots and the recomputed sum is a detectable
    # integrity failure rather than a silent one.
    op.execute("""
        CREATE TABLE IF NOT EXISTS wallet_ledger (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            sequence BIGSERIAL NOT NULL,
            wallet_id UUID NOT NULL REFERENCES wallets(id),
            entry_type VARCHAR(24) NOT NULL,
            direction VARCHAR(6) NOT NULL,
            amount NUMERIC(18,2) NOT NULL,
            balance_after NUMERIC(18,2) NOT NULL,
            description TEXT NOT NULL,
            occurred_on DATE NOT NULL,
            source_type VARCHAR(32),
            source_id UUID,
            reverses_entry_id UUID REFERENCES wallet_ledger(id),
            journal_entry_id UUID,
            idempotency_key VARCHAR(80),
            created_by UUID REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_wal_led_amount CHECK (amount > 0),
            CONSTRAINT ck_wal_led_type CHECK (entry_type IN
                ('OPENING','FUNDING','EXPENSE','RETURN','ADJUSTMENT',
                 'REVERSAL')),
            CONSTRAINT ck_wal_led_dir CHECK (direction IN ('CREDIT','DEBIT'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_wal_led_wallet "
               "ON wallet_ledger (wallet_id, sequence)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_wal_led_occurred "
               "ON wallet_ledger (occurred_on)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_wal_led_source "
               "ON wallet_ledger (source_type, source_id)")
    # Partial unique index, not a table constraint: most entries carry no
    # idempotency key, and NULLs must not collide with each other.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_wal_led_idem
            ON wallet_ledger (wallet_id, idempotency_key)
         WHERE idempotency_key IS NOT NULL
    """)

    # ---- funding: money the company entrusted to a person --------------
    #
    # disbursement_method records HOW the money actually left the company,
    # outside this application. It is the bridge between the physical transfer
    # and the accountability record, and it is what someone reconciling the
    # bank statement will search on.
    op.execute("""
        CREATE TABLE IF NOT EXISTS wallet_fundings (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            funding_reference VARCHAR(40) UNIQUE NOT NULL,
            wallet_id UUID NOT NULL REFERENCES wallets(id),
            amount NUMERIC(18,2) NOT NULL,
            purpose TEXT NOT NULL,
            disbursement_method VARCHAR(24) NOT NULL,
            disbursement_reference VARCHAR(128),
            -- The GL account the money physically came out of.
            source_account_code VARCHAR(20) NOT NULL DEFAULT '1200'
                REFERENCES gl_accounts(code),
            funded_on DATE NOT NULL,
            -- The date by which the holder must have accounted for it.
            -- Drives the "outstanding" figure on the management dashboard.
            account_by DATE,
            status VARCHAR(16) NOT NULL DEFAULT 'ISSUED',
            ledger_entry_id UUID REFERENCES wallet_ledger(id),
            journal_entry_id UUID,
            fund_request_id UUID,
            issued_by UUID NOT NULL REFERENCES users(id),
            notes TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_wal_fund_amount CHECK (amount > 0),
            CONSTRAINT ck_wal_fund_status CHECK (status IN
                ('ISSUED','REVERSED')),
            CONSTRAINT ck_wal_fund_method CHECK (disbursement_method IN
                ('BANK_TRANSFER','CASH','COMPANY_CARD','CHEQUE','MOBILE_MONEY',
                 'OTHER'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_wal_fund_wallet "
               "ON wallet_fundings (wallet_id, funded_on)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_wal_fund_accountby "
               "ON wallet_fundings (account_by) WHERE status = 'ISSUED'")

    # ---- top-up requests ------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS wallet_fund_requests (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            request_reference VARCHAR(40) UNIQUE NOT NULL,
            wallet_id UUID NOT NULL REFERENCES wallets(id),
            amount_requested NUMERIC(18,2) NOT NULL,
            amount_approved NUMERIC(18,2),
            reason TEXT NOT NULL,
            urgency VARCHAR(12) NOT NULL DEFAULT 'NORMAL',
            -- Balance at the moment of asking, so the approver sees what the
            -- requester saw rather than a figure that moved since.
            balance_at_request NUMERIC(18,2) NOT NULL DEFAULT 0,
            status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
            requested_by UUID NOT NULL REFERENCES users(id),
            requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            decided_by UUID REFERENCES users(id),
            decided_at TIMESTAMPTZ,
            decision_note TEXT,
            funding_id UUID REFERENCES wallet_fundings(id),
            CONSTRAINT ck_wal_req_amount CHECK (amount_requested > 0),
            CONSTRAINT ck_wal_req_approved CHECK (
                amount_approved IS NULL OR amount_approved >= 0),
            CONSTRAINT ck_wal_req_urgency CHECK (urgency IN
                ('NORMAL','URGENT','EMERGENCY')),
            CONSTRAINT ck_wal_req_status CHECK (status IN
                ('PENDING','APPROVED','PARTIALLY_APPROVED','REJECTED',
                 'INFO_REQUESTED','FUNDED','CANCELLED'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_wal_req_status "
               "ON wallet_fund_requests (status, requested_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_wal_req_wallet "
               "ON wallet_fund_requests (wallet_id)")
    op.execute("ALTER TABLE wallet_fundings DROP CONSTRAINT IF EXISTS "
               "fk_wal_fund_request")
    op.execute("""
        ALTER TABLE wallet_fundings ADD CONSTRAINT fk_wal_fund_request
            FOREIGN KEY (fund_request_id) REFERENCES wallet_fund_requests(id)
    """)

    # ---- expenses --------------------------------------------------------
    #
    # An expense is a DOCUMENT with a lifecycle; the ledger entry is its
    # consequence. A PENDING expense has not moved the balance, because the
    # company has not yet accepted it as a discharge of the advance. Only
    # approval writes to wallet_ledger.
    op.execute("""
        CREATE TABLE IF NOT EXISTS wallet_expenses (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            expense_reference VARCHAR(40) UNIQUE NOT NULL,
            wallet_id UUID NOT NULL REFERENCES wallets(id),
            category_id UUID NOT NULL REFERENCES wallet_expense_categories(id),
            amount NUMERIC(18,2) NOT NULL,
            spent_on DATE NOT NULL,
            purpose VARCHAR(255) NOT NULL,
            description TEXT,
            vendor VARCHAR(255),
            location VARCHAR(255),
            payment_method VARCHAR(24) NOT NULL DEFAULT 'CASH',
            department VARCHAR(100),
            project_reference VARCHAR(128),
            customer_id UUID REFERENCES customers(id),
            latitude NUMERIC(10,7),
            longitude NUMERIC(10,7),
            gps_accuracy NUMERIC(10,2),

            status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
            -- The tier the ladder demanded when this was submitted, frozen
            -- here: changing the thresholds later must not retroactively
            -- change who was required to approve a past expense.
            required_tier VARCHAR(20) NOT NULL DEFAULT 'SELF',

            submitted_by UUID NOT NULL REFERENCES users(id),
            submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            decided_by UUID REFERENCES users(id),
            decided_at TIMESTAMPTZ,
            decision_note TEXT,

            ledger_entry_id UUID REFERENCES wallet_ledger(id),
            journal_entry_id UUID,
            reversed_by_expense_id UUID,
            idempotency_key VARCHAR(80),
            notes TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT ck_wal_exp_amount CHECK (amount > 0),
            CONSTRAINT ck_wal_exp_status CHECK (status IN
                ('PENDING','APPROVED','REJECTED','REVERSED')),
            CONSTRAINT ck_wal_exp_tier CHECK (required_tier IN
                ('SELF','SUPERVISOR','MANAGEMENT')),
            -- An approved expense must say who accepted it and when. Without
            -- this, "approved by nobody at no time" is representable.
            CONSTRAINT ck_wal_exp_decided CHECK (
                status = 'PENDING'
                OR (decided_by IS NOT NULL AND decided_at IS NOT NULL))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_wal_exp_wallet "
               "ON wallet_expenses (wallet_id, spent_on)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_wal_exp_status "
               "ON wallet_expenses (status, submitted_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_wal_exp_category "
               "ON wallet_expenses (category_id)")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_wal_exp_idem
            ON wallet_expenses (wallet_id, idempotency_key)
         WHERE idempotency_key IS NOT NULL
    """)

    # ---- returns of unused money ----------------------------------------
    #
    # Two-step by design. The employee DECLARES a return; finance CONFIRMS it
    # has actually arrived. Only confirmation writes to the ledger, because
    # until someone has the cash in hand or sees the credit, the money is
    # still in the employee's possession and they still answer for it.
    op.execute("""
        CREATE TABLE IF NOT EXISTS wallet_returns (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            return_reference VARCHAR(40) UNIQUE NOT NULL,
            wallet_id UUID NOT NULL REFERENCES wallets(id),
            amount NUMERIC(18,2) NOT NULL,
            returned_on DATE NOT NULL,
            method VARCHAR(24) NOT NULL DEFAULT 'CASH',
            reference VARCHAR(128),
            destination_account_code VARCHAR(20) NOT NULL DEFAULT '1100'
                REFERENCES gl_accounts(code),
            status VARCHAR(16) NOT NULL DEFAULT 'DECLARED',
            declared_by UUID NOT NULL REFERENCES users(id),
            declared_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            confirmed_by UUID REFERENCES users(id),
            confirmed_at TIMESTAMPTZ,
            note TEXT,
            ledger_entry_id UUID REFERENCES wallet_ledger(id),
            journal_entry_id UUID,
            reconciliation_id UUID,
            CONSTRAINT ck_wal_ret_amount CHECK (amount > 0),
            CONSTRAINT ck_wal_ret_status CHECK (status IN
                ('DECLARED','CONFIRMED','REJECTED')),
            CONSTRAINT ck_wal_ret_method CHECK (method IN
                ('CASH','BANK_TRANSFER','CHEQUE','MOBILE_MONEY','OTHER'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_wal_ret_wallet "
               "ON wallet_returns (wallet_id, returned_on)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_wal_ret_status "
               "ON wallet_returns (status)")

    # ---- reimbursements --------------------------------------------------
    #
    # Deliberately NOT linked to a wallet balance. The employee spent their own
    # money, so nothing was entrusted and nothing is discharged; the company
    # simply owes them. Folding this into the wallet would inflate both funding
    # and spending by the same amount and make the accountability figure lie.
    op.execute("""
        CREATE TABLE IF NOT EXISTS wallet_reimbursements (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            reimbursement_reference VARCHAR(40) UNIQUE NOT NULL,
            user_id UUID NOT NULL REFERENCES users(id),
            -- Context only (which department's budget it belongs to); the
            -- wallet balance is never touched.
            wallet_id UUID REFERENCES wallets(id),
            category_id UUID NOT NULL REFERENCES wallet_expense_categories(id),
            amount NUMERIC(18,2) NOT NULL,
            spent_on DATE NOT NULL,
            purpose VARCHAR(255) NOT NULL,
            description TEXT,
            vendor VARCHAR(255),
            department VARCHAR(100),
            status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
            submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            decided_by UUID REFERENCES users(id),
            decided_at TIMESTAMPTZ,
            decision_note TEXT,
            amount_approved NUMERIC(18,2),
            paid_on DATE,
            payment_method VARCHAR(24),
            payment_reference VARCHAR(128),
            paid_from_account_code VARCHAR(20) REFERENCES gl_accounts(code),
            paid_by UUID REFERENCES users(id),
            accrual_journal_entry_id UUID,
            payment_journal_entry_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_wal_reim_amount CHECK (amount > 0),
            CONSTRAINT ck_wal_reim_approved CHECK (
                amount_approved IS NULL OR amount_approved > 0),
            CONSTRAINT ck_wal_reim_status CHECK (status IN
                ('PENDING','APPROVED','REJECTED','PAID'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_wal_reim_user "
               "ON wallet_reimbursements (user_id, status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_wal_reim_status "
               "ON wallet_reimbursements (status, submitted_at)")

    # ---- receipts --------------------------------------------------------
    #
    # Stored in the database rather than on the filesystem: on DigitalOcean
    # App Platform the container filesystem is replaced on every deploy, so a
    # receipt written to /app/uploads is evidence with an expiry date. In the
    # database it is covered by the same backups as the transaction it proves.
    #
    # sha256 is indexed because it is how the same receipt photographed once
    # and submitted twice is caught (section 22).
    op.execute("""
        CREATE TABLE IF NOT EXISTS wallet_receipts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            expense_id UUID REFERENCES wallet_expenses(id),
            reimbursement_id UUID REFERENCES wallet_reimbursements(id),
            filename VARCHAR(255) NOT NULL,
            content_type VARCHAR(100) NOT NULL,
            byte_size INTEGER NOT NULL,
            sha256 CHAR(64) NOT NULL,
            content BYTEA NOT NULL,
            uploaded_by UUID REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_wal_rcpt_size CHECK (byte_size > 0),
            CONSTRAINT ck_wal_rcpt_owner CHECK (
                (expense_id IS NOT NULL) <> (reimbursement_id IS NOT NULL))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_wal_rcpt_expense "
               "ON wallet_receipts (expense_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_wal_rcpt_reim "
               "ON wallet_receipts (reimbursement_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_wal_rcpt_sha "
               "ON wallet_receipts (sha256)")

    # ---- reconciliation --------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS wallet_reconciliations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            reconciliation_reference VARCHAR(40) UNIQUE NOT NULL,
            wallet_id UUID NOT NULL REFERENCES wallets(id),
            period_start DATE NOT NULL,
            period_end DATE NOT NULL,
            opening_balance NUMERIC(18,2) NOT NULL,
            total_funded NUMERIC(18,2) NOT NULL,
            total_expenses NUMERIC(18,2) NOT NULL,
            total_returned NUMERIC(18,2) NOT NULL,
            closing_balance NUMERIC(18,2) NOT NULL,
            -- What the holder says they physically still hold. The gap
            -- between this and closing_balance is the variance management
            -- actually has to explain.
            declared_cash_on_hand NUMERIC(18,2),
            variance NUMERIC(18,2),
            status VARCHAR(16) NOT NULL DEFAULT 'SUBMITTED',
            submitted_by UUID NOT NULL REFERENCES users(id),
            submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            reviewed_by UUID REFERENCES users(id),
            reviewed_at TIMESTAMPTZ,
            review_note TEXT,
            note TEXT,
            CONSTRAINT ck_wal_rec_period CHECK (period_end >= period_start),
            CONSTRAINT ck_wal_rec_status CHECK (status IN
                ('SUBMITTED','ACCEPTED','REJECTED'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_wal_rec_wallet "
               "ON wallet_reconciliations (wallet_id, period_end)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_wal_rec_status "
               "ON wallet_reconciliations (status)")
    op.execute("ALTER TABLE wallet_returns DROP CONSTRAINT IF EXISTS "
               "fk_wal_ret_reconciliation")
    op.execute("""
        ALTER TABLE wallet_returns ADD CONSTRAINT fk_wal_ret_reconciliation
            FOREIGN KEY (reconciliation_id)
            REFERENCES wallet_reconciliations(id)
    """)

    # ---- approval configuration -----------------------------------------
    #
    # Thresholds live in rows so administrators can change them without a
    # deployment (section 12 is explicit that they must not be hard-coded).
    op.execute("""
        CREATE TABLE IF NOT EXISTS wallet_approval_rules (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            scope VARCHAR(20) NOT NULL DEFAULT 'GLOBAL',
            wallet_type VARCHAR(24),
            wallet_id UUID REFERENCES wallets(id),
            min_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
            max_amount NUMERIC(18,2),
            tier VARCHAR(20) NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            priority INTEGER NOT NULL DEFAULT 100,
            created_by UUID REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_wal_rule_scope CHECK (scope IN
                ('GLOBAL','WALLET_TYPE','WALLET')),
            CONSTRAINT ck_wal_rule_tier CHECK (tier IN
                ('SELF','SUPERVISOR','MANAGEMENT')),
            CONSTRAINT ck_wal_rule_band CHECK (
                min_amount >= 0 AND (max_amount IS NULL OR max_amount > min_amount)),
            -- A WALLET-scoped rule that names no wallet, or a WALLET_TYPE rule
            -- that names no type, would silently never match.
            CONSTRAINT ck_wal_rule_target CHECK (
                (scope = 'GLOBAL')
                OR (scope = 'WALLET_TYPE' AND wallet_type IS NOT NULL)
                OR (scope = 'WALLET' AND wallet_id IS NOT NULL))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_wal_rule_lookup "
               "ON wallet_approval_rules (scope, is_active, min_amount)")

    # Approval authority is granted here rather than by adding global roles,
    # so nothing outside this module's tables changes. `users.role = 'admin'`
    # is always treated as MANAGEMENT with no ceiling.
    op.execute("""
        CREATE TABLE IF NOT EXISTS wallet_approvers (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            tier VARCHAR(20) NOT NULL,
            -- NULL means no ceiling within the tier.
            max_amount NUMERIC(18,2),
            -- NULL means every department / every wallet.
            department VARCHAR(100),
            wallet_id UUID REFERENCES wallets(id),
            can_fund BOOLEAN NOT NULL DEFAULT FALSE,
            can_reconcile BOOLEAN NOT NULL DEFAULT FALSE,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            granted_by UUID REFERENCES users(id),
            granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_wal_appr_tier CHECK (tier IN
                ('SUPERVISOR','MANAGEMENT','FINANCE')),
            CONSTRAINT ck_wal_appr_max CHECK (
                max_amount IS NULL OR max_amount > 0)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_wal_appr_user "
               "ON wallet_approvers (user_id, is_active)")

    # Per-wallet category caps (section 13).
    op.execute("""
        CREATE TABLE IF NOT EXISTS wallet_category_limits (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            wallet_id UUID NOT NULL REFERENCES wallets(id) ON DELETE CASCADE,
            category_id UUID NOT NULL
                REFERENCES wallet_expense_categories(id) ON DELETE CASCADE,
            monthly_cap NUMERIC(18,2) NOT NULL,
            created_by UUID REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_wal_catlim_cap CHECK (monthly_cap > 0),
            CONSTRAINT uq_wal_catlim UNIQUE (wallet_id, category_id)
        )
    """)

    # ---- anomaly flags ---------------------------------------------------
    #
    # A flag is a QUESTION for a human, never a verdict. The specification is
    # explicit that the system must not accuse the employee, so nothing here
    # blocks a transaction or changes a status by itself.
    op.execute("""
        CREATE TABLE IF NOT EXISTS wallet_flags (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            wallet_id UUID NOT NULL REFERENCES wallets(id),
            expense_id UUID REFERENCES wallet_expenses(id),
            fund_request_id UUID REFERENCES wallet_fund_requests(id),
            flag_type VARCHAR(40) NOT NULL,
            severity VARCHAR(10) NOT NULL DEFAULT 'MEDIUM',
            message TEXT NOT NULL,
            detail JSONB,
            status VARCHAR(16) NOT NULL DEFAULT 'OPEN',
            reviewed_by UUID REFERENCES users(id),
            reviewed_at TIMESTAMPTZ,
            review_note TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_wal_flag_sev CHECK (severity IN
                ('LOW','MEDIUM','HIGH')),
            CONSTRAINT ck_wal_flag_status CHECK (status IN
                ('OPEN','REVIEWED','DISMISSED'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_wal_flag_wallet "
               "ON wallet_flags (wallet_id, status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_wal_flag_open "
               "ON wallet_flags (status, created_at)")
    # One flag per (expense, type): re-running detection on the same expense
    # must not produce a growing pile of identical warnings.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_wal_flag_expense_type
            ON wallet_flags (expense_id, flag_type)
         WHERE expense_id IS NOT NULL
    """)

    # ---- audit log -------------------------------------------------------
    #
    # Separate from `audit_logs` for the same reason mapd_audit_logs is: this
    # one cannot be updated or deleted by anyone, including an administrator
    # with a database client, and it is scoped to money.
    op.execute("""
        CREATE TABLE IF NOT EXISTS wallet_audit_logs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            event_type VARCHAR(50) NOT NULL,
            entity_type VARCHAR(50),
            entity_id UUID,
            wallet_id UUID,
            amount NUMERIC(18,2),
            actor_user_id UUID,
            actor_label VARCHAR(255),
            ip_address VARCHAR(64),
            user_agent VARCHAR(500),
            result VARCHAR(40),
            detail JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_wal_audit_wallet "
               "ON wallet_audit_logs (wallet_id, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_wal_audit_event "
               "ON wallet_audit_logs (event_type, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_wal_audit_actor "
               "ON wallet_audit_logs (actor_user_id, created_at)")

    # ---- immutability, enforced by the database --------------------------
    #
    # RAISE ... USING MESSAGE rather than RAISE 'text %', arg: a literal % in
    # migration SQL is ambiguous under drivers that use pyformat parameters.
    op.execute("""
        CREATE OR REPLACE FUNCTION wallet_reject_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION USING MESSAGE =
                'Table ' || TG_TABLE_NAME || ' is append-only: ' || TG_OP ||
                ' is not permitted. Correct a wallet entry by posting a '
                'reversal, never by editing the record of where money went.';
        END;
        $$ LANGUAGE plpgsql
    """)
    for table in ("wallet_ledger", "wallet_audit_logs"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {table}")
        op.execute(f"""
            CREATE TRIGGER trg_{table}_immutable
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION wallet_reject_mutation()
        """)

    # Fundings must stay updatable (ISSUED -> REVERSED, and the ledger link is
    # written after the entry exists), but the figures that define the event
    # may never change and the row may never be deleted.
    op.execute("""
        CREATE OR REPLACE FUNCTION wallet_funding_guard()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Wallet fundings cannot be deleted; reverse the funding '
                    'so the record of the original advance remains.';
            END IF;
            IF NEW.wallet_id <> OLD.wallet_id
               OR NEW.amount <> OLD.amount
               OR NEW.funding_reference <> OLD.funding_reference
               OR NEW.issued_by <> OLD.issued_by THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Funding ' || OLD.funding_reference || ' is immutable in '
                    'wallet, amount, reference and issuer.';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_wallet_fundings_guard "
               "ON wallet_fundings")
    op.execute("""
        CREATE TRIGGER trg_wallet_fundings_guard
        BEFORE UPDATE OR DELETE ON wallet_fundings
        FOR EACH ROW EXECUTE FUNCTION wallet_funding_guard()
    """)

    # Expenses move PENDING -> APPROVED/REJECTED/REVERSED, so UPDATE is
    # allowed, but what was claimed and by whom is frozen at submission. This
    # is what stops a rejected N80,000 claim being quietly edited to N8,000
    # and re-approved.
    op.execute("""
        CREATE OR REPLACE FUNCTION wallet_expense_guard()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Wallet expenses cannot be deleted; reverse the expense '
                    'so both the original and the correction are on record.';
            END IF;
            IF NEW.wallet_id <> OLD.wallet_id
               OR NEW.amount <> OLD.amount
               OR NEW.expense_reference <> OLD.expense_reference
               OR NEW.submitted_by <> OLD.submitted_by
               OR NEW.spent_on <> OLD.spent_on THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Expense ' || OLD.expense_reference || ' is immutable in '
                    'wallet, amount, date, reference and submitter. Reverse '
                    'it and submit a corrected expense instead.';
            END IF;
            -- A decided expense is settled. Re-deciding it would move the
            -- balance a second time.
            IF OLD.status <> 'PENDING' AND NEW.status = 'PENDING' THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Expense ' || OLD.expense_reference || ' has already been '
                    'decided and cannot be returned to PENDING.';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_wallet_expenses_guard "
               "ON wallet_expenses")
    op.execute("""
        CREATE TRIGGER trg_wallet_expenses_guard
        BEFORE UPDATE OR DELETE ON wallet_expenses
        FOR EACH ROW EXECUTE FUNCTION wallet_expense_guard()
    """)

    # Receipts are evidence: they may be added, never altered or removed.
    op.execute("DROP TRIGGER IF EXISTS trg_wallet_receipts_immutable "
               "ON wallet_receipts")
    op.execute("""
        CREATE TRIGGER trg_wallet_receipts_immutable
        BEFORE UPDATE OR DELETE ON wallet_receipts
        FOR EACH ROW EXECUTE FUNCTION wallet_reject_mutation()
    """)

    # ---- seed ------------------------------------------------------------
    for code, name, atype, normal, postable in NEW_ACCOUNTS:
        op.execute(sa.text("""
            INSERT INTO gl_accounts
                (id, code, name, account_type, normal_balance, is_postable)
            VALUES (gen_random_uuid(), :c, :n, :t, :nb, :p)
            ON CONFLICT (code) DO NOTHING
        """).bindparams(c=code, n=name, t=atype, nb=normal, p=postable))

    for code, name, gl, receipt, sort in CATEGORIES:
        op.execute(sa.text("""
            INSERT INTO wallet_expense_categories
                (id, code, name, gl_account_code, requires_receipt, sort_order)
            VALUES (gen_random_uuid(), :c, :n, :g, :r, :s)
            ON CONFLICT (code) DO NOTHING
        """).bindparams(c=code, n=name, g=gl, r=receipt, s=sort))

    # Only seed the ladder if none exists, so re-running the migration on a
    # database where management has already retuned the thresholds does not
    # resurrect the defaults alongside them.
    existing = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM wallet_approval_rules")).scalar()
    if not existing:
        for lo, hi, tier in APPROVAL_RULES:
            op.execute(sa.text("""
                INSERT INTO wallet_approval_rules
                    (id, scope, min_amount, max_amount, tier, priority)
                VALUES (gen_random_uuid(), 'GLOBAL', :lo, :hi, :t, 100)
            """).bindparams(lo=lo, hi=hi, t=tier))


def downgrade():
    for table in ("wallet_ledger", "wallet_audit_logs", "wallet_receipts"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {table}")
    op.execute("DROP TRIGGER IF EXISTS trg_wallet_fundings_guard "
               "ON wallet_fundings")
    op.execute("DROP TRIGGER IF EXISTS trg_wallet_expenses_guard "
               "ON wallet_expenses")
    op.execute("DROP FUNCTION IF EXISTS wallet_expense_guard()")
    op.execute("DROP FUNCTION IF EXISTS wallet_funding_guard()")
    op.execute("DROP FUNCTION IF EXISTS wallet_reject_mutation()")

    for table in ("wallet_flags", "wallet_audit_logs", "wallet_category_limits",
                  "wallet_approvers", "wallet_approval_rules",
                  "wallet_receipts", "wallet_reconciliations",
                  "wallet_reimbursements", "wallet_returns",
                  "wallet_expenses", "wallet_fund_requests",
                  "wallet_fundings", "wallet_ledger", "wallets",
                  "wallet_expense_categories"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    # The two GL accounts are deliberately left in place: dropping an account
    # that journal lines still reference would break the ledger.
