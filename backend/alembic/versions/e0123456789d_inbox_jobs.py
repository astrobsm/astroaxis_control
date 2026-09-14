"""A derived attention list, and jobs that cannot run twice.

WHY THERE IS NO NOTIFICATIONS TABLE
===================================
The obvious build is a `notifications` table: something happens, a row is
written, the user reads it. It is also the wrong build here, and the reason is
worth stating because it will look like an omission otherwise.

A stored notification is a COPY of a fact that lives somewhere else. The moment
it is written it starts to rot: the corrective action gets closed, the document
gets renewed, the batch gets released -- and the notification still says
otherwise. Users learn within a fortnight that the list lies, and then they stop
reading it, which is worse than not having had it.

So the attention list is DERIVED. `app/services/inbox.py` computes it live from
the tables that already hold the truth -- expiring documents, open corrective
actions, applications awaiting review, recalled batches still in the field,
unverified sales, overdue reviews. Nothing can be stale, because nothing is
stored.

WHAT IS STORED, AND WHY IT IS NOT A COPY
========================================
`attention_acknowledgements` holds one thing a derived list cannot derive:
whether a person has already seen an item and asked not to be shown it for a
while. That is a fact about the PERSON, not about the item, so it does not
duplicate anything.

**Snoozes always expire.** `snoozed_until` is NOT NULL and there is no "dismiss
forever". An item that is still true must come back, or the list becomes a way
of hiding problems rather than surfacing them -- and the one person who dismissed
it is the only person who ever had to think about it. The service additionally
refuses to snooze CRITICAL items at all; the database guarantees only that no
snooze is permanent.

WHY PUSH NOTIFICATIONS ARE NOT USED
===================================
`app/api/notifications.py` exists, but: subscriptions live in a JSON file on the
container filesystem, which is replaced on every deploy; they are keyed by
browser endpoint with a standing TODO for user association; and the only send
path broadcasts to EVERY subscriber. So a message saying "distributor X is
behind target" would reach whoever happened to be subscribed, or nobody.

Sending one distributor's performance to every subscribed browser is worse than
sending nothing. This phase therefore surfaces things in the app rather than
pushing them, and says so plainly instead of shipping a feature that quietly
does not work.

IDEMPOTENT JOBS
===============
`scheduled_job_runs` carries UNIQUE (job_name, run_key). A weekly digest for the
week of 2026-09-07 has run_key '2026-W37', so running it five times inserts
once. Idempotency is a database constraint rather than a code convention,
because a retry loop or a double-registered cron is exactly the situation where
the convention is not holding.
"""
from alembic import op
import sqlalchemy as sa

revision = 'e0123456789d'
down_revision = 'd9012345678c'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS attention_acknowledgements (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            -- Identifies the derived item, e.g.
            -- 'document_expiring:<uuid>' or 'review_due:<uuid>'. Stable across
            -- recomputation so a snooze survives the item being recalculated.
            item_key VARCHAR(160) NOT NULL,
            user_id UUID NOT NULL REFERENCES users(id),

            -- NOT NULL on purpose. There is no dismiss-forever: an item that
            -- is still true has to come back.
            snoozed_until TIMESTAMPTZ NOT NULL,
            reason TEXT,
            -- What the item looked like when it was snoozed, so a snooze taken
            -- on a minor item is visibly not a snooze of the critical one it
            -- later became.
            severity_at_snooze VARCHAR(16),

            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT uq_ack_per_user UNIQUE (item_key, user_id),
            CONSTRAINT ck_ack_future CHECK (snoozed_until > created_at)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_ack_live "
               "ON attention_acknowledgements (user_id, snoozed_until)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_job_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            job_name VARCHAR(64) NOT NULL,

            -- The period this run covers: '2026-09-14', '2026-W37',
            -- '2026-09'. Two runs for the same key are the same run.
            run_key VARCHAR(32) NOT NULL,

            status VARCHAR(16) NOT NULL DEFAULT 'RUNNING',
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at TIMESTAMPTZ,

            -- What it found, as JSON. Findings, never deliveries: nothing in
            -- this system sends anything to anyone (see the module docstring).
            summary JSONB,
            error TEXT,
            triggered_by UUID REFERENCES users(id),

            -- THE idempotency guarantee, in the database rather than in a
            -- convention some retry loop will not be following.
            CONSTRAINT uq_job_run UNIQUE (job_name, run_key),
            CONSTRAINT ck_job_status CHECK (status IN
                ('RUNNING','COMPLETED','FAILED')),
            CONSTRAINT ck_job_failed_explained CHECK (
                status <> 'FAILED' OR (error IS NOT NULL AND error <> ''))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_job_runs "
               "ON scheduled_job_runs (job_name, started_at DESC)")

    op.execute("""
        CREATE OR REPLACE FUNCTION scheduled_job_run_guard()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Job runs are not deletable. Deleting one would let the '
                    'job run again for a period it has already covered, which '
                    'is the whole thing this table prevents.';
            END IF;
            IF OLD.status IN ('COMPLETED','FAILED')
               AND NEW.status IS DISTINCT FROM OLD.status THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Job ' || OLD.job_name || ' for ' || OLD.run_key ||
                    ' has already finished. Record a new run against a new '
                    'key rather than rewriting this one.';
            END IF;
            IF NEW.job_name <> OLD.job_name OR NEW.run_key <> OLD.run_key THEN
                RAISE EXCEPTION USING MESSAGE =
                    'A job run''s name and period identify it and cannot '
                    'change.';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_job_run_guard ON scheduled_job_runs")
    op.execute("""
        CREATE TRIGGER trg_job_run_guard
        BEFORE UPDATE OR DELETE ON scheduled_job_runs
        FOR EACH ROW EXECUTE FUNCTION scheduled_job_run_guard()
    """)


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS trg_job_run_guard ON scheduled_job_runs")
    op.execute("DROP FUNCTION IF EXISTS scheduled_job_run_guard()")
    op.execute("DROP TABLE IF EXISTS scheduled_job_runs CASCADE")
    op.execute("DROP TABLE IF EXISTS attention_acknowledgements CASCADE")
