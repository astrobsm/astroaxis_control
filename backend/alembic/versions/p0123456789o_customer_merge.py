"""Merging duplicate customers, without losing what they bought.

Revision ID: p0123456789o
Revises: o0123456789n
Create Date: 2026-10-04

WHY THIS IS URGENT RATHER THAN TIDY
===================================
In the live database today, 21 of 102 customer records share a telephone
number with another record. One person -- Anyeneh Gloria -- exists seven
times, under two spellings of her own name, and St Mary's Hospital is on the
same number again.

Three consequences, in order of cost:

  * **Inbound WhatsApp cannot work at all.** A message arrives from a number.
    If that number matches eight customer records, the system cannot say who
    is writing, and an AI that guesses will quote one customer another's
    prices and history. Every later stage of the commerce plan rests on
    resolving a number to exactly one customer.
  * **The opportunity queue already overstates the problem.** Seven records
    for one person produce seven "gone quiet" findings, so a sales officer
    rings the same woman seven times, or more likely stops trusting the list.
  * Totals per customer -- lifetime value, average order, reorder interval --
    are computed per record, so a split customer looks like several small
    ones and never reaches the thresholds that would surface them.

NOTHING IS MERGED AUTOMATICALLY
===============================
Detection is derived and cheap; merging is permanent and expensive. Two
records on one phone number may be a husband and wife, a hospital and the
nurse who orders for it, or a shared office line. Merging those destroys the
distinction and there is no undo that restores it cleanly.

So the candidates are computed live and a person confirms every merge. This is
also what the engagement plan asked for, and it is the right way round.

A MERGE MOVES HISTORY, IT DOES NOT DELETE RECORDS
=================================================
Twelve tables carry a customer_id. A merge repoints all twelve at the
surviving record and then marks the absorbed record `merged_into_id` rather
than deleting it. Keeping the row matters: an invoice PDF, an order
confirmation or a link somebody saved still names the old id, and a dangling
reference is worse than a redundant row.

`customer_merges` records what moved, how much of it, and who decided. It is
append-only, because a merge rewrites commercial history and "who merged these
two customers, and when" is a question that gets asked after somebody notices
an order on the wrong account.
"""
from alembic import op

revision = 'p0123456789o'
down_revision = 'o0123456789n'
branch_labels = None
depends_on = None


def upgrade():
    # ---- the surviving/absorbed relationship ------------------------------
    op.execute("""
        ALTER TABLE customers ADD COLUMN IF NOT EXISTS merged_into_id UUID
            REFERENCES customers(id)
    """)
    op.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS "
               "merged_at TIMESTAMPTZ")
    op.execute("ALTER TABLE customers ADD COLUMN IF NOT EXISTS "
               "merged_by UUID REFERENCES users(id)")

    # A record cannot be merged into itself, and a merged record must say when.
    op.execute("ALTER TABLE customers DROP CONSTRAINT IF EXISTS ck_customer_merge")
    op.execute("""
        ALTER TABLE customers ADD CONSTRAINT ck_customer_merge CHECK (
            merged_into_id IS NULL
            OR (merged_into_id <> id AND merged_at IS NOT NULL))
    """)
    # Every query that lists customers now has to exclude the absorbed ones.
    op.execute("CREATE INDEX IF NOT EXISTS ix_customers_live "
               "ON customers (is_active) WHERE merged_into_id IS NULL")

    # ---- what was merged, and by whom -------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS customer_merges (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            surviving_id UUID NOT NULL REFERENCES customers(id),
            merged_id UUID NOT NULL REFERENCES customers(id),

            -- Denormalised deliberately: the point of this row is to be
            -- readable years later, and the absorbed record's name may be
            -- corrected on the survivor afterwards.
            surviving_name VARCHAR(255),
            merged_name VARCHAR(255),
            merged_code VARCHAR(32),

            -- What actually moved, per table. Without this nobody can tell
            -- whether a merge moved 40 orders or none, which is the first
            -- question asked when one looks wrong.
            moved JSONB,

            reason TEXT NOT NULL,
            actor_id UUID REFERENCES users(id),
            actor_name VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT ck_merge_distinct CHECK (surviving_id <> merged_id),
            CONSTRAINT ck_merge_reason CHECK (length(trim(reason)) >= 3)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_customer_merges_surviving "
               "ON customer_merges (surviving_id, created_at DESC)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_customer_merges_merged "
               "ON customer_merges (merged_id)")

    op.execute("""
        CREATE OR REPLACE FUNCTION trg_customer_merge_append_only()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                'customer_merges is append-only: it records who combined two '
                'customers and what history moved with them';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS no_edit_customer_merges "
               "ON customer_merges")
    op.execute("""
        CREATE TRIGGER no_edit_customer_merges
        BEFORE UPDATE OR DELETE ON customer_merges
        FOR EACH ROW EXECUTE FUNCTION trg_customer_merge_append_only()
    """)

    # ---- detection needs these --------------------------------------------
    # Normalised phone: the last ten digits, which is what matches a Nigerian
    # number however it was typed (+234..., 0803..., spaces, dashes).
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_customers_phone_digits
            ON customers (RIGHT(regexp_replace(COALESCE(phone, ''), '[^0-9]',
                                               '', 'g'), 10))
         WHERE phone IS NOT NULL
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_customers_email_lower "
               "ON customers (LOWER(email)) WHERE email IS NOT NULL")


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS no_edit_customer_merges "
               "ON customer_merges")
    op.execute("DROP TABLE IF EXISTS customer_merges CASCADE")
    op.execute("DROP FUNCTION IF EXISTS trg_customer_merge_append_only()")
    op.execute("DROP INDEX IF EXISTS ix_customers_phone_digits")
    op.execute("DROP INDEX IF EXISTS ix_customers_email_lower")
    op.execute("DROP INDEX IF EXISTS ix_customers_live")
    op.execute("ALTER TABLE customers DROP CONSTRAINT IF EXISTS ck_customer_merge")
    # merged_into_id stays. Dropping it would silently resurrect every
    # absorbed customer as a live duplicate, which is the fault this
    # migration exists to fix.
