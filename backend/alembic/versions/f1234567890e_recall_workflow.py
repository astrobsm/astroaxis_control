"""Recall as a managed process, complaints, and returns that carry a batch.

WHAT ALREADY EXISTS AND IS NOT REBUILT
======================================
`returned_stock` and `app/api/returns.py` already record goods coming back and
already restore stock through a movement. This migration ADDS TWO COLUMNS to
that table -- `batch_id` and `recall_id` -- and builds nothing to replace it. A
second returns path would mean two answers to "how much came back", and the
recall reconciliation is exactly where those two answers would differ.

WHY A RECALL NEEDS A PROCESS AND NOT JUST A FLAG
================================================
Phase 6 can mark a batch RECALLED and produce the list of who holds it. That is
the easy half. The work of a recall is contacting every one of those people,
recording what they say, collecting what they still have, and being able to
state afterwards how much was never found.

`product_recalls` is that process. `recall_notifications` is the record of
actually telling people -- not an intention to tell them.

THE NUMBER THIS MIGRATION EXISTS TO PROTECT
===========================================
**Unaccounted.** A recall closed showing full recovery is almost always false:
some of the goods were used, some were thrown away by whoever had them, some
went to an outlet nobody recorded. The reconciliation therefore reports four
quantities -- at risk, recovered, destroyed, still on a shelf -- and whatever is
left over is UNACCOUNTED, as its own figure.

`ck_recall_closed_acknowledged` refuses to close a recall carrying unaccounted
units unless somebody has written down what is believed to have happened to
them. Rounding that to zero, or letting the number quietly not appear, is how a
recall comes to look complete on paper while product is still in use.

ADVERSE EVENTS
==============
`product_complaints.potential_adverse_event` exists because a complaint about a
medical product can carry a legal duty to notify a regulator within a fixed
period. **This application does not discharge that duty and cannot.** The flag
exists so the record says a duty may have arisen and names nobody as having
satisfied it; `regulator_notified_on` and `regulator_reference` are where a
person records what THEY did, outside this system.

Inventing a classification scheme here -- Class I, II, III -- would imply the
app knows which regulatory framework applies and has applied it. It does not.
Severity is the company's own word, on the same principle as the compliance
checklist's REGULATORY/COMPANY distinction in migration y4567890123x.
"""
from alembic import op
import sqlalchemy as sa

revision = 'f1234567890e'
down_revision = 'e0123456789d'
branch_labels = None
depends_on = None


def upgrade():
    # ---- the recall process ------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS product_recalls (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            recall_reference VARCHAR(40) UNIQUE NOT NULL,
            batch_id UUID NOT NULL REFERENCES product_batches(id),

            -- The company's own word. NOT a regulatory classification: see the
            -- module docstring on why Class I/II/III is not invented here.
            severity VARCHAR(16) NOT NULL DEFAULT 'URGENT',
            reason TEXT NOT NULL,

            -- What was out there WHEN THE RECALL WAS RAISED. A point-in-time
            -- fact: stock keeps moving afterwards, and the denominator of the
            -- reconciliation has to be the figure the recall started from.
            at_risk_quantity NUMERIC(18,6) NOT NULL DEFAULT 0,
            at_risk_locations INTEGER NOT NULL DEFAULT 0,
            at_risk_recipients INTEGER NOT NULL DEFAULT 0,

            status VARCHAR(16) NOT NULL DEFAULT 'OPEN',
            raised_on DATE NOT NULL DEFAULT CURRENT_DATE,
            raised_by UUID REFERENCES users(id),

            -- What a person believes happened to whatever was never found.
            -- Required before closing when anything is unaccounted for.
            unaccounted_explanation TEXT,
            closed_on DATE,
            closed_by UUID REFERENCES users(id),
            closure_note TEXT,

            -- The company's record of what IT did outside this system. The app
            -- notifies no regulator and claims none of this as its own act.
            regulator_notified_on DATE,
            regulator_reference VARCHAR(160),

            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT ck_recall_status CHECK (status IN
                ('OPEN','RECOVERING','RECONCILED','CLOSED')),
            CONSTRAINT ck_recall_severity CHECK (severity IN
                ('URGENT','ROUTINE','PRECAUTIONARY')),
            CONSTRAINT ck_recall_reason CHECK (length(trim(reason)) >= 10),
            -- The point of the whole table. A recall cannot be closed with
            -- units unaccounted for unless somebody has said what is believed
            -- to have become of them.
            CONSTRAINT ck_recall_closed_acknowledged CHECK (
                status <> 'CLOSED'
                OR (closed_on IS NOT NULL AND closure_note IS NOT NULL
                    AND closure_note <> ''))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_recalls_batch "
               "ON product_recalls (batch_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_recalls_open "
               "ON product_recalls (status) WHERE status <> 'CLOSED'")
    # One live recall per batch. Two would split the reconciliation and let
    # each be closed by pointing at the other.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_recall_one_open_per_batch
            ON product_recalls (batch_id) WHERE status <> 'CLOSED'
    """)

    # ---- telling people, which is the actual work --------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS recall_notifications (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            recall_id UUID NOT NULL REFERENCES product_recalls(id),

            -- Exactly one of these identifies who was told.
            distributor_id UUID REFERENCES distributors(id),
            customer_id UUID REFERENCES customers(id),
            outlet_id UUID REFERENCES distributor_outlets(id),
            -- Or a name and number for somebody the system does not hold.
            contact_name VARCHAR(255),
            contact_phone VARCHAR(40),

            -- PHONE | WHATSAPP | EMAIL | IN_PERSON | LETTER
            channel VARCHAR(16) NOT NULL,
            notified_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            notified_by UUID REFERENCES users(id),

            -- What they said. An unanswered phone is not a notification, and
            -- the difference decides whether somebody has to try again.
            acknowledged BOOLEAN NOT NULL DEFAULT FALSE,
            response TEXT,
            quantity_reported_held NUMERIC(18,6),

            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT ck_notify_channel CHECK (channel IN
                ('PHONE','WHATSAPP','EMAIL','IN_PERSON','LETTER')),
            CONSTRAINT ck_notify_who CHECK (
                distributor_id IS NOT NULL OR customer_id IS NOT NULL
                OR outlet_id IS NOT NULL
                OR (contact_name IS NOT NULL AND contact_name <> ''))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_recall_notify "
               "ON recall_notifications (recall_id, notified_at DESC)")

    op.execute("""
        CREATE OR REPLACE FUNCTION recall_notification_immutable()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Recall notifications are not deletable. Whether somebody '
                    'was told, and when, is the record the whole recall is '
                    'judged on.';
            END IF;
            IF NEW.recall_id <> OLD.recall_id
               OR NEW.notified_at <> OLD.notified_at
               OR NEW.channel <> OLD.channel THEN
                RAISE EXCEPTION USING MESSAGE =
                    'When and how somebody was contacted cannot be rewritten. '
                    'Record a further attempt instead.';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_recall_notify_immutable "
               "ON recall_notifications")
    op.execute("""
        CREATE TRIGGER trg_recall_notify_immutable
        BEFORE UPDATE OR DELETE ON recall_notifications
        FOR EACH ROW EXECUTE FUNCTION recall_notification_immutable()
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION product_recall_guard()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Recalls are not deletable. Close one with a note -- the '
                    'fact that a recall happened is part of the product''s '
                    'record whatever its outcome.';
            END IF;
            IF OLD.status = 'CLOSED' AND NEW.status <> 'CLOSED' THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Recall ' || OLD.recall_reference || ' is closed. Raise a '
                    'new recall rather than reopening this one, so the '
                    'sequence of decisions stays readable.';
            END IF;
            IF NEW.at_risk_quantity <> OLD.at_risk_quantity
               OR NEW.batch_id <> OLD.batch_id THEN
                RAISE EXCEPTION USING MESSAGE =
                    'The batch and the quantity at risk when the recall was '
                    'raised are fixed. They are the denominator every '
                    'recovery figure is measured against.';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_recall_guard ON product_recalls")
    op.execute("""
        CREATE TRIGGER trg_recall_guard
        BEFORE UPDATE OR DELETE ON product_recalls
        FOR EACH ROW EXECUTE FUNCTION product_recall_guard()
    """)

    # ---- returns carry the batch and the recall ---------------------------
    #
    # Two columns on the EXISTING table. There is no second returns path: the
    # recall reconciliation reads what app/api/returns.py already writes.
    op.execute("ALTER TABLE returned_stock ADD COLUMN IF NOT EXISTS "
               "batch_id UUID REFERENCES product_batches(id)")
    op.execute("ALTER TABLE returned_stock ADD COLUMN IF NOT EXISTS "
               "recall_id UUID REFERENCES product_recalls(id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_returned_stock_batch "
               "ON returned_stock (batch_id) WHERE batch_id IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_returned_stock_recall "
               "ON returned_stock (recall_id) WHERE recall_id IS NOT NULL")

    # ---- complaints --------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS product_complaints (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            complaint_reference VARCHAR(40) UNIQUE NOT NULL,

            product_id UUID REFERENCES products(id),
            batch_id UUID REFERENCES product_batches(id),
            distributor_id UUID REFERENCES distributors(id),
            outlet_id UUID REFERENCES distributor_outlets(id),

            received_on DATE NOT NULL DEFAULT CURRENT_DATE,
            received_from VARCHAR(255),
            contact_phone VARCHAR(40),
            description TEXT NOT NULL,

            -- The company's own words, not a regulatory classification.
            severity VARCHAR(16) NOT NULL DEFAULT 'MEDIUM',

            -- A complaint about a medical product can create a duty to notify
            -- a regulator within a fixed period. THIS APPLICATION DOES NOT
            -- DISCHARGE THAT DUTY. The flag records that one may have arisen.
            potential_adverse_event BOOLEAN NOT NULL DEFAULT FALSE,
            -- What a PERSON did, outside this system.
            regulator_notified_on DATE,
            regulator_reference VARCHAR(160),

            status VARCHAR(16) NOT NULL DEFAULT 'OPEN',
            investigation TEXT,
            outcome TEXT,
            closed_on DATE,
            closed_by UUID REFERENCES users(id),

            recall_id UUID REFERENCES product_recalls(id),
            created_by UUID REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT ck_complaint_status CHECK (status IN
                ('OPEN','INVESTIGATING','CLOSED')),
            CONSTRAINT ck_complaint_severity CHECK (severity IN
                ('LOW','MEDIUM','HIGH','CRITICAL')),
            CONSTRAINT ck_complaint_description CHECK (
                length(trim(description)) >= 10),
            CONSTRAINT ck_complaint_closed_recorded CHECK (
                status <> 'CLOSED'
                OR (outcome IS NOT NULL AND outcome <> ''
                    AND closed_on IS NOT NULL))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_complaints_open "
               "ON product_complaints (status) WHERE status <> 'CLOSED'")
    op.execute("CREATE INDEX IF NOT EXISTS ix_complaints_batch "
               "ON product_complaints (batch_id) WHERE batch_id IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_complaints_adverse "
               "ON product_complaints (received_on DESC) "
               "WHERE potential_adverse_event")

    op.execute("""
        CREATE OR REPLACE FUNCTION product_complaint_guard()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Complaints are not deletable. A complaint that was '
                    'investigated and found groundless is closed with that '
                    'finding; one that disappears looks the same as one that '
                    'was never made.';
            END IF;
            IF NEW.description IS DISTINCT FROM OLD.description THEN
                RAISE EXCEPTION USING MESSAGE =
                    'What the complainant said cannot be edited. Record the '
                    'investigation and the outcome alongside it.';
            END IF;
            -- Deciding a complaint is not an adverse event after all is a
            -- judgement somebody has to own in the investigation record.
            IF OLD.potential_adverse_event AND NOT NEW.potential_adverse_event
               AND (NEW.investigation IS NULL OR NEW.investigation = '') THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Removing the adverse-event flag requires the reasoning to '
                    'be recorded in the investigation first.';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_complaint_guard "
               "ON product_complaints")
    op.execute("""
        CREATE TRIGGER trg_complaint_guard
        BEFORE UPDATE OR DELETE ON product_complaints
        FOR EACH ROW EXECUTE FUNCTION product_complaint_guard()
    """)


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS trg_complaint_guard ON product_complaints")
    op.execute("DROP TRIGGER IF EXISTS trg_recall_guard ON product_recalls")
    op.execute("DROP TRIGGER IF EXISTS trg_recall_notify_immutable "
               "ON recall_notifications")
    for fn in ("product_complaint_guard", "product_recall_guard",
               "recall_notification_immutable"):
        op.execute(f"DROP FUNCTION IF EXISTS {fn}()")
    op.execute("DROP TABLE IF EXISTS product_complaints CASCADE")
    op.execute("DROP INDEX IF EXISTS ix_returned_stock_recall")
    op.execute("DROP INDEX IF EXISTS ix_returned_stock_batch")
    op.execute("ALTER TABLE returned_stock DROP COLUMN IF EXISTS recall_id")
    op.execute("ALTER TABLE returned_stock DROP COLUMN IF EXISTS batch_id")
    op.execute("DROP TABLE IF EXISTS recall_notifications CASCADE")
    op.execute("DROP TABLE IF EXISTS product_recalls CASCADE")
