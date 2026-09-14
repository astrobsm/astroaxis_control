"""Batch and lot traceability, quarantine and recall.

THE DECISION THAT SHAPES THIS MIGRATION
=======================================
**There is no batch balance table.** A batch's quantity on hand is DERIVED by
summing `stock_movements` for that batch, exactly the way it is already summed
for everything else.

A `batch_stock_levels` cache would have been faster and is the obvious thing to
build. It is also a second copy of a number the system already knows, and the
integration directive prohibits exactly that for good reason: two stores of the
same quantity drift, and the drift is discovered during a recall -- the one
moment the figure has to be right. `stock_movements` already records every
change with a positive magnitude and a direction, so the derivation is exact and
cannot disagree with itself. `ix_sm_batch` exists to make it fast.

WHAT CANNOT BE TRACED, AND WHY IT IS NOT PRETENDED OTHERWISE
============================================================
`stock_movements.batch_id` is NULLABLE and **nothing is backfilled**. Every unit
that moved before this migration has no batch and never will -- the information
does not exist, and inventing a batch number for it would be fabricating a
traceability record, which is worse than having none. A recall report must
therefore always answer in two parts: how much is traceable, and how much is
not. `app/services/batches.py::traceability_report` is built to say so rather
than to show a reassuring percentage.

EXPIRY IS NULLABLE, DELIBERATELY
================================
Not every product expires, and forcing a date would mean staff typing a fictional
one to get past the form. Where a date IS present it is enforced: an expired
batch cannot be dispatched. A missing expiry is recorded as unknown rather than
as "does not expire", because those are different facts.

QUARANTINE AND RECALL ARE ENFORCED IN THE DATABASE
==================================================
`app/services/inventory.py::apply_stock_movement` is the single write path and
checks this too. The trigger here is deliberate duplication: that module's own
docstring records that ~12 call sites once hand-rolled their own balance updates,
and a recalled batch leaving the building because someone wrote a raw INSERT is
not a failure mode worth leaving open to save one trigger.
"""
from alembic import op
import sqlalchemy as sa

revision = 'b7890123456a'
down_revision = 'a6789012345z'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS product_batches (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            product_id UUID NOT NULL REFERENCES products(id),

            -- As printed on the goods. Unique per product: two products may
            -- legitimately carry the same lot number from different suppliers.
            batch_number VARCHAR(64) NOT NULL,

            manufactured_on DATE,
            -- Nullable on purpose; see the module docstring. Unknown is not
            -- the same fact as "does not expire".
            expiry_date DATE,

            -- AVAILABLE   may be dispatched
            -- QUARANTINED held pending investigation; cannot leave
            -- RECALLED    withdrawn from sale; cannot leave, must be traced
            -- WITHDRAWN   removed from sale for a commercial reason
            -- CONSUMED    fully issued; kept for history
            status VARCHAR(16) NOT NULL DEFAULT 'AVAILABLE',
            status_reason TEXT,

            -- PRODUCTION | PURCHASE | OPENING
            -- OPENING is stock that existed before batches did and has been
            -- given one retrospectively BY A PERSON who knows what it was.
            origin VARCHAR(16) NOT NULL DEFAULT 'PRODUCTION',
            origin_reference VARCHAR(128),
            supplier_name VARCHAR(255),

            quantity_produced NUMERIC(18,6),
            notes TEXT,

            created_by UUID REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT uq_batch_per_product UNIQUE (product_id, batch_number),
            CONSTRAINT ck_batch_status CHECK (status IN
                ('AVAILABLE','QUARANTINED','RECALLED','WITHDRAWN','CONSUMED')),
            CONSTRAINT ck_batch_origin CHECK (origin IN
                ('PRODUCTION','PURCHASE','OPENING')),
            CONSTRAINT ck_batch_dates CHECK (
                manufactured_on IS NULL OR expiry_date IS NULL
                OR expiry_date >= manufactured_on),
            -- Taking a batch off sale is a decision someone has to own.
            CONSTRAINT ck_batch_block_reason CHECK (
                status NOT IN ('QUARANTINED','RECALLED','WITHDRAWN')
                OR (status_reason IS NOT NULL AND status_reason <> ''))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_batches_product "
               "ON product_batches (product_id, expiry_date NULLS LAST)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_batches_expiry "
               "ON product_batches (expiry_date) "
               "WHERE expiry_date IS NOT NULL AND status = 'AVAILABLE'")
    op.execute("CREATE INDEX IF NOT EXISTS ix_batches_blocked "
               "ON product_batches (status) WHERE status <> 'AVAILABLE'")

    # ---- why a batch was quarantined or released, append-only --------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS batch_status_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            batch_id UUID NOT NULL REFERENCES product_batches(id),
            from_status VARCHAR(16),
            to_status VARCHAR(16) NOT NULL,
            reason TEXT NOT NULL,
            -- Who decided, not merely who typed it.
            decided_by UUID REFERENCES users(id),
            decided_by_label VARCHAR(160),
            evidence_document_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_bse_reason CHECK (length(trim(reason)) >= 3)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_bse_batch "
               "ON batch_status_events (batch_id, created_at DESC)")

    op.execute("""
        CREATE OR REPLACE FUNCTION batch_status_event_immutable()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION USING MESSAGE =
                'Batch status history is append-only. Why a batch was '
                'quarantined is the record a recall is judged on; it cannot be '
                'edited afterwards.';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_bse_immutable ON batch_status_events")
    op.execute("""
        CREATE TRIGGER trg_bse_immutable
        BEFORE UPDATE OR DELETE ON batch_status_events
        FOR EACH ROW EXECUTE FUNCTION batch_status_event_immutable()
    """)

    # A batch that has been recalled cannot quietly become available again.
    op.execute("""
        CREATE OR REPLACE FUNCTION product_batch_guard()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Batches are not deletable. The movements that reference '
                    'one are the traceability record.';
            END IF;
            IF OLD.status = 'RECALLED' AND NEW.status = 'AVAILABLE' THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Batch ' || OLD.batch_number || ' was recalled and cannot '
                    'be returned to sale. If the recall was raised in error, '
                    'that is a decision to record on a new batch, not an edit '
                    'to this one.';
            END IF;
            IF NEW.batch_number <> OLD.batch_number
               OR NEW.product_id <> OLD.product_id THEN
                RAISE EXCEPTION USING MESSAGE =
                    'A batch''s number and product identify the physical goods '
                    'and cannot be changed once movements reference them.';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_batch_guard ON product_batches")
    op.execute("""
        CREATE TRIGGER trg_batch_guard
        BEFORE UPDATE OR DELETE ON product_batches
        FOR EACH ROW EXECUTE FUNCTION product_batch_guard()
    """)

    # ---- movements and order lines carry the batch -------------------------
    op.execute("ALTER TABLE stock_movements ADD COLUMN IF NOT EXISTS "
               "batch_id UUID REFERENCES product_batches(id)")
    # The index the derived balance depends on. Without it every batch balance
    # is a sequential scan of the whole movement history.
    op.execute("CREATE INDEX IF NOT EXISTS ix_sm_batch "
               "ON stock_movements (batch_id, warehouse_id) "
               "WHERE batch_id IS NOT NULL")

    op.execute("ALTER TABLE sales_order_lines ADD COLUMN IF NOT EXISTS "
               "batch_id UUID REFERENCES product_batches(id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_sol_batch "
               "ON sales_order_lines (batch_id) WHERE batch_id IS NOT NULL")

    # ---- recalled goods do not leave the building --------------------------
    #
    # Deliberate duplication of the check in inventory.apply_stock_movement.
    # See the module docstring: the service is the single write path today, and
    # this is the guarantee that survives someone writing a raw INSERT.
    op.execute("""
        CREATE OR REPLACE FUNCTION batch_dispatch_guard()
        RETURNS TRIGGER AS $$
        DECLARE
            b RECORD;
        BEGIN
            IF NEW.batch_id IS NULL THEN
                RETURN NEW;
            END IF;

            SELECT batch_number, status, expiry_date, product_id
              INTO b FROM product_batches WHERE id = NEW.batch_id;

            IF b.product_id IS DISTINCT FROM NEW.product_id THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Batch ' || b.batch_number || ' belongs to a different '
                    'product. A movement cannot attribute stock to a batch of '
                    'something else.';
            END IF;

            -- Only outbound movements are blocked. Stock must still be able to
            -- come BACK IN: a recall works by returning goods, and a rule that
            -- stopped that would make recalled stock impossible to collect.
            IF NEW.movement_type IN ('OUT','TRANSFER_OUT','PRODUCTION_OUT') THEN
                IF b.status = 'RECALLED' THEN
                    RAISE EXCEPTION USING MESSAGE =
                        'Batch ' || b.batch_number || ' has been RECALLED and '
                        'cannot be despatched. Goods on hand must be returned '
                        'or destroyed, not sold.';
                END IF;
                IF b.status = 'QUARANTINED' THEN
                    RAISE EXCEPTION USING MESSAGE =
                        'Batch ' || b.batch_number || ' is QUARANTINED pending '
                        'investigation and cannot be despatched. Release it '
                        'first, with a reason.';
                END IF;
                IF b.status = 'WITHDRAWN' THEN
                    RAISE EXCEPTION USING MESSAGE =
                        'Batch ' || b.batch_number || ' has been withdrawn '
                        'from sale and cannot be despatched.';
                END IF;
                IF b.expiry_date IS NOT NULL AND b.expiry_date < CURRENT_DATE THEN
                    RAISE EXCEPTION USING MESSAGE =
                        'Batch ' || b.batch_number || ' expired on '
                        || b.expiry_date || ' and cannot be despatched.';
                END IF;
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_batch_dispatch ON stock_movements")
    op.execute("""
        CREATE TRIGGER trg_batch_dispatch
        BEFORE INSERT ON stock_movements
        FOR EACH ROW EXECUTE FUNCTION batch_dispatch_guard()
    """)


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS trg_batch_dispatch ON stock_movements")
    op.execute("DROP TRIGGER IF EXISTS trg_batch_guard ON product_batches")
    op.execute("DROP TRIGGER IF EXISTS trg_bse_immutable ON batch_status_events")
    for fn in ("batch_dispatch_guard", "product_batch_guard",
               "batch_status_event_immutable"):
        op.execute(f"DROP FUNCTION IF EXISTS {fn}()")
    op.execute("DROP INDEX IF EXISTS ix_sol_batch")
    op.execute("ALTER TABLE sales_order_lines DROP COLUMN IF EXISTS batch_id")
    op.execute("DROP INDEX IF EXISTS ix_sm_batch")
    op.execute("ALTER TABLE stock_movements DROP COLUMN IF EXISTS batch_id")
    op.execute("DROP TABLE IF EXISTS batch_status_events CASCADE")
    op.execute("DROP TABLE IF EXISTS product_batches CASCADE")
