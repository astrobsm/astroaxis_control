"""The opportunity queue: what was DONE about a finding, not the finding itself.

Revision ID: o0123456789n
Revises: n9012345678m
Create Date: 2026-10-04

WHY THERE IS NO OPPORTUNITIES TABLE
===================================
The obvious build is a table of opportunities: a nightly job works out who is
due a reorder, writes a row per customer, and the sales screen reads it. It is
also the wrong build, for the reason already written down in e0123456789d and
proved by the attention list:

    A stored notification is a COPY of a fact that lives somewhere else. The
    moment it is written it starts to rot.

An opportunity is exactly that kind of copy. "Hospital A is 37 days overdue"
stops being true the instant Hospital A places an order -- but the stored row
still says it, so a sales officer rings a customer who ordered yesterday and
the list loses their trust in a fortnight.

So the queue is DERIVED. `app/services/opportunities.py` computes it live from
sales_orders, sales_order_lines, invoices and logistics_deliveries -- the
tables that already hold the truth. It cannot be stale because nothing is
stored.

WHAT IS STORED, AND WHY IT IS NOT A COPY
========================================
One thing a derived list cannot derive: what a human DID about it. Whether
somebody rang this customer, what came of it, and whether the opportunity
turned into an order. That is a fact about an action taken by a person, not a
restatement of the order history, so it duplicates nothing.

That record is also the only way to answer the question that decides whether
this whole feature was worth building: of the opportunities we surfaced, how
many were acted on, and how many turned into money.

APPEND-ONLY, AND WHY IT MATTERS HERE
====================================
A row says a named member of staff contacted a named customer on a date and
reported an outcome. Letting that be edited later would make the conversion
figures unauditable, and conversion figures are what this feature will
eventually be judged on -- by the people whose work they measure. The same
reasoning as the payroll payment batches and the staff visibility log.

A snooze is recorded the same way and always expires. There is no
dismiss-forever: an opportunity that is still true must come back, or the
queue becomes a way of making real work invisible.
"""
from alembic import op

revision = 'o0123456789n'
down_revision = 'n9012345678m'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS customer_opportunity_actions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            -- Stable across recomputation: 'REORDER_DUE:<customer id>'. The
            -- same opportunity arising again next month carries the same key,
            -- which is what makes a snooze and a conversion rate meaningful.
            opportunity_key VARCHAR(160) NOT NULL,
            opportunity_type VARCHAR(40) NOT NULL,
            customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,

            outcome VARCHAR(24) NOT NULL,

            -- What the queue believed when the action was taken. Stored
            -- because the finding is derived and will have changed by the
            -- time anyone reads this back: without it, "we called a customer
            -- worth 400,000" cannot be reconstructed.
            potential_value NUMERIC(18,2),
            reason_at_action TEXT,

            note TEXT,
            -- Set only for a SNOOZED row. NOT NULL would be wrong here (most
            -- rows are not snoozes) but a snooze without one is refused by the
            -- CHECK below, so there is still no dismiss-forever.
            snoozed_until TIMESTAMPTZ,

            actor_id UUID REFERENCES users(id),
            actor_name VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT ck_coa_outcome CHECK (outcome IN
                ('CONTACTED','CONVERTED','DECLINED','SNOOZED','NOT_RELEVANT')),
            CONSTRAINT ck_coa_snooze CHECK (
                (outcome = 'SNOOZED' AND snoozed_until IS NOT NULL)
                OR (outcome <> 'SNOOZED' AND snoozed_until IS NULL))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_coa_customer "
               "ON customer_opportunity_actions (customer_id, created_at DESC)")
    # The queue asks "is this key snoozed right now" on every open, for every
    # finding. Partial, because live snoozes are the small half.
    op.execute("CREATE INDEX IF NOT EXISTS ix_coa_live_snooze "
               "ON customer_opportunity_actions (opportunity_key, snoozed_until) "
               "WHERE snoozed_until IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_coa_outcome_date "
               "ON customer_opportunity_actions (outcome, created_at DESC)")

    op.execute("""
        CREATE OR REPLACE FUNCTION trg_opportunity_action_append_only()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                'customer_opportunity_actions is append-only: it is the record '
                'of who contacted whom and what came of it. Add a correcting '
                'row instead of changing one.';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS no_edit_opportunity_actions "
               "ON customer_opportunity_actions")
    op.execute("""
        CREATE TRIGGER no_edit_opportunity_actions
        BEFORE UPDATE OR DELETE ON customer_opportunity_actions
        FOR EACH ROW EXECUTE FUNCTION trg_opportunity_action_append_only()
    """)

    # ---- indexes the derivation itself needs ------------------------------
    # The queue walks every customer's order history on every open. At 242
    # orders that is instant either way, but these are the two scans it does
    # and they are free to add now and awkward to diagnose later.
    op.execute("CREATE INDEX IF NOT EXISTS ix_sales_orders_customer_date "
               "ON sales_orders (customer_id, order_date DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_invoices_customer_status "
               "ON invoices (customer_id, status)")


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS no_edit_opportunity_actions "
               "ON customer_opportunity_actions")
    op.execute("DROP TABLE IF EXISTS customer_opportunity_actions CASCADE")
    op.execute("DROP FUNCTION IF EXISTS trg_opportunity_action_append_only()")
    op.execute("DROP INDEX IF EXISTS ix_sales_orders_customer_date")
    op.execute("DROP INDEX IF EXISTS ix_invoices_customer_status")
