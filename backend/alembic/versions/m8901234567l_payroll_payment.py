"""Paying a payroll run, and hiding a member of staff from displays.

Revision ID: m8901234567l
Revises: l7890123456k
Create Date: 2026-10-02

TWO CHANGES, BOTH ABOUT THE SAME THING: SAYING WHAT HAPPENED
============================================================

WHY A PAYMENT IS NOT THE SAME EVENT AS AN APPROVAL
--------------------------------------------------
`payroll_runs` has carried a PAID status and a `paid_at` column since
o4567890123n, and in all that time nothing has ever set either of them. The
workflow stopped at APPROVED, which posts this entry:

    Dr Salaries & Wages        gross + employer cost
    Cr Staff Salary Payable    net          <- a DEBT, not a payment
    Cr PAYE / Pension / NHF / NHIA Payable

That credit to 2200 is the company saying "we owe our staff this". Nothing in
the system ever said "and we have now paid them", so account 2200 could only
ever grow. A payroll approved in March and paid in March looked identical, in
the ledger, to one approved in March and never paid at all.

This migration adds the second event:

    Dr Staff Salary Payable    net paid
    Cr Bank Accounts / Cash    net paid

and that is why payment is recorded in a table of its own rather than as a
flag. A payment has a date, a method, a bank reference, a person who made it
and a journal entry of its own -- none of which fit in a boolean.

PAYMENT IS PER PAYSLIP, NOT PER RUN
-----------------------------------
Staff are not always paid together. One person's account details are wrong,
another is on suspension pending a query, a third is paid in cash because
their bank is down. If payment were a flag on the run, the first of those
would force the whole month to stay unpaid, and somebody would eventually set
the flag anyway and the ledger would be wrong about three people.

So `payslips` carries `paid_at` and `payment_batch_id`, a run is PAID only
when every one of its payslips is, and a batch posts only what it actually
paid. Partial payment is a normal state here, not an error.

HIDING A MEMBER OF STAFF -- AND WHY A REASON IS COMPULSORY
-----------------------------------------------------------
Staff names appear all over this application: the birthday panel on the
dashboard, the payroll table, attendance, the staff register. There are good
reasons to want somebody off those lists -- they have resigned, they are
suspended, the record is a duplicate, a bereavement makes a birthday card
inappropriate.

There are also bad reasons, and the difference between the two is whether
anyone can see afterwards which it was. So:

  * `hidden_reason` is NOT NULL whenever `display_hidden` is true, enforced by
    a CHECK rather than by the form. A reason the API can skip is a reason
    that will be skipped.
  * `staff_visibility_log` is append-only, protected by the same trigger
    pattern used elsewhere in this schema. Hiding somebody and un-hiding them
    an hour later leaves two rows, not zero.

HIDDEN IS NOT INACTIVE, AND NEVER TOUCHES PAY
---------------------------------------------
`is_active` already exists and means "no longer employed". `display_hidden`
means "do not show this person on lists", which is a presentation decision and
must never become a payroll decision: a suspended employee is usually still
owed money, and somebody made invisible by a bad actor must not also become
unpayable. The payroll queries in this change therefore ignore
`display_hidden` entirely, and the payroll screen shows hidden staff with a
marker rather than dropping them.
"""
from alembic import op

revision = 'm8901234567l'
down_revision = 'l7890123456k'
branch_labels = None
depends_on = None


def upgrade():
    # ---- a payment actually happening ------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS payroll_payment_batches (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            batch_number VARCHAR(64) UNIQUE NOT NULL,
            run_id UUID NOT NULL
                REFERENCES payroll_runs(id) ON DELETE RESTRICT,

            -- The date the money left, which is NOT the date somebody got
            -- round to recording it. The ledger entry uses this one.
            paid_on DATE NOT NULL,
            method VARCHAR(24) NOT NULL DEFAULT 'BANK_TRANSFER',

            -- Whatever ties this to the bank statement. Without it a payment
            -- in this system cannot be reconciled to one in the account.
            bank_reference VARCHAR(160),

            staff_count INTEGER NOT NULL DEFAULT 0,
            total_net NUMERIC(18,2) NOT NULL DEFAULT 0,

            journal_entry_id UUID,
            paid_by UUID REFERENCES users(id),
            paid_by_name VARCHAR(255),
            notes TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT ck_ppb_method CHECK (method IN
                ('BANK_TRANSFER','CASH','CHEQUE','MOBILE_MONEY','OTHER')),
            CONSTRAINT ck_ppb_total CHECK (total_net >= 0)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_ppb_run "
               "ON payroll_payment_batches (run_id, paid_on DESC)")

    # A payment batch is a financial record. Correcting one means reversing
    # it, exactly as with a journal entry -- not editing it afterwards.
    op.execute("""
        CREATE OR REPLACE FUNCTION trg_payment_batch_append_only()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                'payroll_payment_batches is append-only: reverse the batch '
                'instead of altering or deleting it';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS no_edit_payment_batches "
               "ON payroll_payment_batches")
    op.execute("""
        CREATE TRIGGER no_edit_payment_batches
        BEFORE DELETE ON payroll_payment_batches
        FOR EACH ROW EXECUTE FUNCTION trg_payment_batch_append_only()
    """)

    # ---- which payslips that batch settled --------------------------------
    op.execute("ALTER TABLE payslips ADD COLUMN IF NOT EXISTS "
               "paid_at TIMESTAMPTZ")
    op.execute("ALTER TABLE payslips ADD COLUMN IF NOT EXISTS "
               "payment_batch_id UUID REFERENCES payroll_payment_batches(id)")
    # Partial index: the question asked constantly is "who is still owed",
    # and that is the small half of this table in a healthy month.
    op.execute("CREATE INDEX IF NOT EXISTS ix_payslips_unpaid "
               "ON payslips (run_id) WHERE paid_at IS NULL")
    # A payslip is paid if and only if it belongs to a batch. Allowing one
    # without the other would produce a payment with no ledger entry, or a
    # ledger entry nobody can trace to a person.
    op.execute("""
        ALTER TABLE payslips DROP CONSTRAINT IF EXISTS ck_payslip_paid_pair
    """)
    op.execute("""
        ALTER TABLE payslips ADD CONSTRAINT ck_payslip_paid_pair CHECK (
            (paid_at IS NULL AND payment_batch_id IS NULL)
            OR (paid_at IS NOT NULL AND payment_batch_id IS NOT NULL))
    """)

    # ---- hiding somebody from the lists -----------------------------------
    op.execute("ALTER TABLE staff ADD COLUMN IF NOT EXISTS "
               "display_hidden BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE staff ADD COLUMN IF NOT EXISTS hidden_reason TEXT")
    op.execute("ALTER TABLE staff ADD COLUMN IF NOT EXISTS "
               "hidden_at TIMESTAMPTZ")
    op.execute("ALTER TABLE staff ADD COLUMN IF NOT EXISTS hidden_by UUID")

    # The reason is part of the act, not an optional note beside it.
    op.execute("ALTER TABLE staff DROP CONSTRAINT IF EXISTS ck_staff_hidden_reason")
    op.execute("""
        ALTER TABLE staff ADD CONSTRAINT ck_staff_hidden_reason CHECK (
            display_hidden = FALSE
            OR (hidden_reason IS NOT NULL AND length(trim(hidden_reason)) >= 3))
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_staff_visible "
               "ON staff (is_active) WHERE display_hidden = FALSE")

    op.execute("""
        CREATE TABLE IF NOT EXISTS staff_visibility_log (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            staff_id UUID NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
            -- TRUE = hidden, FALSE = restored. Both are recorded: a record of
            -- only the hidings would make every restoration invisible.
            hidden BOOLEAN NOT NULL,
            reason TEXT NOT NULL,
            actor_id UUID REFERENCES users(id),
            actor_name VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_svl_reason CHECK (length(trim(reason)) >= 3)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_svl_staff "
               "ON staff_visibility_log (staff_id, created_at DESC)")

    op.execute("""
        CREATE OR REPLACE FUNCTION trg_visibility_log_append_only()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                'staff_visibility_log is append-only: it is the record of who '
                'hid whom and why';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS no_edit_visibility_log "
               "ON staff_visibility_log")
    op.execute("""
        CREATE TRIGGER no_edit_visibility_log
        BEFORE UPDATE OR DELETE ON staff_visibility_log
        FOR EACH ROW EXECUTE FUNCTION trg_visibility_log_append_only()
    """)


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS no_edit_visibility_log "
               "ON staff_visibility_log")
    op.execute("DROP TABLE IF EXISTS staff_visibility_log CASCADE")
    op.execute("DROP FUNCTION IF EXISTS trg_visibility_log_append_only()")

    op.execute("ALTER TABLE staff DROP CONSTRAINT IF EXISTS ck_staff_hidden_reason")
    for col in ("display_hidden", "hidden_reason", "hidden_at", "hidden_by"):
        op.execute(f"ALTER TABLE staff DROP COLUMN IF EXISTS {col}")

    op.execute("ALTER TABLE payslips DROP CONSTRAINT IF EXISTS ck_payslip_paid_pair")
    op.execute("ALTER TABLE payslips DROP COLUMN IF EXISTS payment_batch_id")
    # paid_at stays. It is the record that a named person was paid on a given
    # date, and a schema downgrade is not a reason to forget that.

    op.execute("DROP TRIGGER IF EXISTS no_edit_payment_batches "
               "ON payroll_payment_batches")
    op.execute("DROP TABLE IF EXISTS payroll_payment_batches CASCADE")
    op.execute("DROP FUNCTION IF EXISTS trg_payment_batch_append_only()")
