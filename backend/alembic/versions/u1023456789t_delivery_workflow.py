"""A delivery that can fail, and goods that can come back.

Revision ID: u1023456789t
Revises: t0123456789s
Create Date: 2026-10-04

WHAT WAS MISSING
================
`manifest_customers.status` holds exactly two values in the live database:
`pending` and `delivered`. There is no way to record that a drop failed.

That is not a cosmetic gap. When a van returns with goods the customer
refused, was closed for, or could not pay for, three things are true and none
of them can be written down:

  * the delivery did not happen, but the drop still reads `pending` forever
    or gets marked `delivered` because those are the only options;
  * the goods are back in the warehouse, but stock was deducted when the
    invoice was raised, so the system believes they are gone;
  * nobody can tell how often deliveries fail, or why.

The second is the expensive one. Returned stock that is physically on the
shelf and absent from the system is found at the next stock count, months
later, as an unexplained surplus -- and an unexplained surplus is
indistinguishable from a counting error, so it teaches nobody anything.

STATUS IS NOW A CLOSED SET, AND IT WAS NOT
==========================================
`PUT /manifests/{id}/status` read `data.get('status', 'dispatched')` and wrote
it straight into the column. Any string at all was accepted, so a typo set a
manifest to a status nothing else in the system recognises, and no screen
would ever show it again.

The CHECK constraints below close that, and the transition rules in
`app.services.delivery` close the rest: a manifest cannot go from `preparing`
straight to `completed` without being dispatched, because that is a run that
never left the yard and arrived anyway.

LOWERCASE, DELIBERATELY
=======================
Every other status column added recently is upper case. These are lower case
because the 31 manifests and 38 drops already in this database are, and
rewriting live rows to change their case is a data migration whose only
benefit is tidiness. Matching what is there is worth more than matching a
convention.

NO STOCK MOVES ON DISPATCH
==========================
Stock is deducted when the invoice is raised -- see `payment_tracking.py`.
Dispatching a manifest is the physical movement of goods the system has
already written off, so deducting again would halve the warehouse.

The one movement this adds is the opposite: a FAILED drop whose goods come
back puts them back, once, recorded.
"""
from alembic import op

revision = 'u1023456789t'
down_revision = 't0123456789s'
branch_labels = None
depends_on = None


def upgrade():
    # ---- the run -----------------------------------------------------------
    op.execute("ALTER TABLE delivery_manifests ADD COLUMN IF NOT EXISTS "
               "dispatched_at TIMESTAMPTZ")
    op.execute("ALTER TABLE delivery_manifests ADD COLUMN IF NOT EXISTS "
               "completed_at TIMESTAMPTZ")
    op.execute("ALTER TABLE delivery_manifests ADD COLUMN IF NOT EXISTS "
               "cancelled_at TIMESTAMPTZ")
    op.execute("ALTER TABLE delivery_manifests ADD COLUMN IF NOT EXISTS "
               "cancel_reason TEXT")

    # Anything already outside the set is left alone and the constraint is
    # added NOT VALID, so a value nobody anticipated cannot fail this
    # migration on the live database at the worst moment.
    op.execute("ALTER TABLE delivery_manifests "
               "DROP CONSTRAINT IF EXISTS ck_manifest_status")
    op.execute("""
        ALTER TABLE delivery_manifests ADD CONSTRAINT ck_manifest_status CHECK (
            status IN ('preparing','dispatched','in_transit','completed',
                       'cancelled')) NOT VALID
    """)

    # ---- each drop ---------------------------------------------------------
    op.execute("ALTER TABLE manifest_customers ADD COLUMN IF NOT EXISTS "
               "delivered_at TIMESTAMPTZ")
    op.execute("ALTER TABLE manifest_customers ADD COLUMN IF NOT EXISTS "
               "failed_at TIMESTAMPTZ")
    op.execute("ALTER TABLE manifest_customers ADD COLUMN IF NOT EXISTS "
               "failure_reason TEXT")
    op.execute("ALTER TABLE manifest_customers ADD COLUMN IF NOT EXISTS "
               "attempt_count INTEGER NOT NULL DEFAULT 0")
    # Set when the goods from a failed drop are put back on the shelf. NULL
    # means they have not been, which is a question somebody has to answer.
    op.execute("ALTER TABLE manifest_customers ADD COLUMN IF NOT EXISTS "
               "stock_returned_at TIMESTAMPTZ")

    op.execute("ALTER TABLE manifest_customers "
               "DROP CONSTRAINT IF EXISTS ck_drop_status")
    op.execute("""
        ALTER TABLE manifest_customers ADD CONSTRAINT ck_drop_status CHECK (
            status IN ('pending','out_for_delivery','delivered','failed',
                       'returned','cancelled')) NOT VALID
    """)
    # A failed drop says why. A failure with no reason is a fact nobody can
    # act on and the commonest one to leave blank.
    op.execute("ALTER TABLE manifest_customers "
               "DROP CONSTRAINT IF EXISTS ck_drop_failure_reason")
    op.execute("""
        ALTER TABLE manifest_customers ADD CONSTRAINT ck_drop_failure_reason
        CHECK (status <> 'failed'
               OR (failure_reason IS NOT NULL
                   AND length(trim(failure_reason)) >= 3)) NOT VALID
    """)

    op.execute("CREATE INDEX IF NOT EXISTS ix_drops_open "
               "ON manifest_customers (manifest_id) "
               "WHERE status IN ('pending','out_for_delivery')")
    # The question asked after every failed run: whose goods are back in the
    # building and not yet back on the system.
    op.execute("CREATE INDEX IF NOT EXISTS ix_drops_awaiting_return "
               "ON manifest_customers (failed_at) "
               "WHERE status = 'failed' AND stock_returned_at IS NULL")

    # ---- what happened, and who did it -------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS delivery_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            manifest_id UUID REFERENCES delivery_manifests(id)
                ON DELETE CASCADE,
            manifest_customer_id UUID REFERENCES manifest_customers(id)
                ON DELETE CASCADE,
            from_status VARCHAR(24),
            to_status VARCHAR(24) NOT NULL,
            note TEXT,
            actor_id UUID REFERENCES users(id),
            actor_name VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

            CONSTRAINT ck_delivery_event_target CHECK (
                manifest_id IS NOT NULL OR manifest_customer_id IS NOT NULL)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_delivery_events_manifest "
               "ON delivery_events (manifest_id, created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_delivery_events_drop "
               "ON delivery_events (manifest_customer_id, created_at DESC)")

    op.execute("""
        CREATE OR REPLACE FUNCTION trg_delivery_event_append_only()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                'delivery_events is append-only: it is the record of what the '
                'driver reported and when';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS no_edit_delivery_events "
               "ON delivery_events")
    op.execute("""
        CREATE TRIGGER no_edit_delivery_events
        BEFORE UPDATE OR DELETE ON delivery_events
        FOR EACH ROW EXECUTE FUNCTION trg_delivery_event_append_only()
    """)

    op.execute("""
        INSERT INTO app_settings (key, value, value_type, description) VALUES
          ('DELIVERY_NOTIFY_CUSTOMER', 'false', 'BOOLEAN',
           'Queue a message to the customer when their delivery is '
           'dispatched, out for delivery, or delivered. Governed by the '
           'master outbound switch as well.')
        ON CONFLICT (key) DO NOTHING
    """)


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS no_edit_delivery_events "
               "ON delivery_events")
    op.execute("DROP TABLE IF EXISTS delivery_events CASCADE")
    op.execute("DROP FUNCTION IF EXISTS trg_delivery_event_append_only()")
    op.execute("DROP INDEX IF EXISTS ix_drops_open")
    op.execute("DROP INDEX IF EXISTS ix_drops_awaiting_return")
    for c in ("ck_manifest_status",):
        op.execute(f"ALTER TABLE delivery_manifests DROP CONSTRAINT IF EXISTS {c}")
    for c in ("ck_drop_status", "ck_drop_failure_reason"):
        op.execute(f"ALTER TABLE manifest_customers DROP CONSTRAINT IF EXISTS {c}")
    for col in ("dispatched_at", "completed_at", "cancelled_at",
                "cancel_reason"):
        op.execute(f"ALTER TABLE delivery_manifests DROP COLUMN IF EXISTS {col}")
    for col in ("delivered_at", "failed_at", "failure_reason",
                "attempt_count"):
        op.execute(f"ALTER TABLE manifest_customers DROP COLUMN IF EXISTS {col}")
    # stock_returned_at stays. Dropping it would lose the record of which
    # returned goods have already been put back, and the only way to recover
    # that is a stock count.
    op.execute("DELETE FROM app_settings WHERE key = 'DELIVERY_NOTIFY_CUSTOMER'")
