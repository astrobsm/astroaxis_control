"""Distributor performance: frozen periods and the reviews they trigger.

WHY A SNAPSHOT TABLE EXISTS AT ALL
==================================
Everything in `performance_periods` could be recomputed from targets and sales.
Storing it would normally be the duplication this module's integration directive
forbids -- so the reason it is here has to be better than "it is faster".

It is this: **a review is judged on what was known when it was raised.** Two
things move underneath a performance figure after the fact. Sales get verified
weeks later, so a month's verified total rises long after the month ends. And
targets are versioned rather than edited, so the figure a distributor was
measured against is a historical fact rather than a current setting.

A distributor told in April that they missed February must be able to see the
February numbers as they stood in April -- not today's recomputation, which may
now show them passing. Recomputing is still the primary view and is always
available live; the snapshot records what a decision was actually taken on.

That is the same reasoning as the eligibility score frozen onto a territory
application and the cost snapshotted onto a sales order line.

Snapshots are therefore IMMUTABLE. A snapshot that can be edited records nothing.

WHAT COUNTS
===========
Only VERIFIED downstream sales count toward achievement. `reported_amount` is
stored alongside so the gap is visible -- a distributor whose claimed figure is
triple their verified one is a finding in itself -- but the band and the review
trigger are computed from verified alone.
"""
from alembic import op
import sqlalchemy as sa

revision = 'd9012345678c'
down_revision = 'c8901234567b'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS performance_periods (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            distributor_id UUID NOT NULL REFERENCES distributors(id),
            territory_id UUID REFERENCES territories(id),

            -- The month being measured. Stored as its first day so it sorts
            -- and compares as a date rather than as two integers.
            period_start DATE NOT NULL,
            period_end DATE NOT NULL,

            -- The target AS IT STOOD during that month, not today's. Copied in
            -- because territory_targets is versioned and the row in force then
            -- may since have been superseded.
            target_amount NUMERIC(18,2) NOT NULL DEFAULT 0,

            -- Only this drives the band and the review trigger.
            verified_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
            -- Kept beside it so the gap between claim and confirmation is
            -- visible. Never added to the figure above.
            reported_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
            disputed_amount NUMERIC(18,2) NOT NULL DEFAULT 0,

            achievement_pct NUMERIC(7,2),
            band VARCHAR(16) NOT NULL,

            -- When this was computed, which is the whole point of the table.
            computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            computed_by UUID REFERENCES users(id),
            note TEXT,

            CONSTRAINT uq_perf_period UNIQUE (distributor_id, period_start),
            CONSTRAINT ck_perf_band CHECK (band IN
                ('ON_TARGET','BEHIND','WELL_BEHIND','NO_TARGET')),
            CONSTRAINT ck_perf_window CHECK (period_end > period_start),
            CONSTRAINT ck_perf_amounts CHECK (
                verified_amount >= 0 AND reported_amount >= 0
                AND target_amount >= 0)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_perf_distributor "
               "ON performance_periods (distributor_id, period_start DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_perf_territory "
               "ON performance_periods (territory_id, period_start DESC) "
               "WHERE territory_id IS NOT NULL")

    op.execute("""
        CREATE OR REPLACE FUNCTION performance_period_immutable()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION USING MESSAGE =
                'Performance periods are immutable. The point of the row is '
                'what was known when it was taken -- a snapshot that can be '
                'edited records nothing. Take a new one, or read the live '
                'figures, which are always available.';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_perf_immutable "
               "ON performance_periods")
    op.execute("""
        CREATE TRIGGER trg_perf_immutable
        BEFORE UPDATE OR DELETE ON performance_periods
        FOR EACH ROW EXECUTE FUNCTION performance_period_immutable()
    """)

    # ---- the review a run of bad months triggers ---------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS distributor_performance_reviews (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            review_reference VARCHAR(40) UNIQUE NOT NULL,
            distributor_id UUID NOT NULL REFERENCES distributors(id),
            territory_id UUID REFERENCES territories(id),

            -- What triggered it, in figures, so nobody has to reconstruct it.
            trigger_reason TEXT NOT NULL,
            periods_considered INTEGER NOT NULL DEFAULT 0,
            worst_achievement_pct NUMERIC(7,2),

            opened_on DATE NOT NULL DEFAULT CURRENT_DATE,
            opened_by UUID REFERENCES users(id),

            status VARCHAR(16) NOT NULL DEFAULT 'OPEN',
            -- SUPPORTED     help offered, relationship continues
            -- TARGET_RESET  the target was wrong, not the distributor
            -- WARNED        formal warning issued
            -- TERMINATED    relationship ended
            outcome VARCHAR(20),
            outcome_note TEXT,
            closed_on DATE,
            closed_by UUID REFERENCES users(id),

            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT ck_review_status CHECK (status IN
                ('OPEN','IN_PROGRESS','CLOSED')),
            CONSTRAINT ck_review_outcome CHECK (outcome IS NULL OR outcome IN
                ('SUPPORTED','TARGET_RESET','WARNED','TERMINATED')),
            -- Closing a performance review without saying what was decided
            -- leaves a distributor with no record of why they were warned.
            CONSTRAINT ck_review_closed_recorded CHECK (
                status <> 'CLOSED'
                OR (outcome IS NOT NULL AND closed_on IS NOT NULL
                    AND outcome_note IS NOT NULL AND outcome_note <> ''))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_review_distributor "
               "ON distributor_performance_reviews "
               "(distributor_id, opened_on DESC)")
    # One open review per distributor. A second would split the conversation
    # and let each be closed by pointing at the other.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_review_one_open
            ON distributor_performance_reviews (distributor_id)
         WHERE status IN ('OPEN','IN_PROGRESS')
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION performance_review_guard()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Performance reviews are not deletable. Close one with an '
                    'outcome -- the fact that a distributor was reviewed is '
                    'part of their record either way.';
            END IF;
            IF OLD.status = 'CLOSED' THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Review ' || OLD.review_reference || ' is closed. Open a '
                    'new review rather than reopening this one, so the '
                    'sequence of decisions stays readable.';
            END IF;
            IF NEW.trigger_reason IS DISTINCT FROM OLD.trigger_reason THEN
                RAISE EXCEPTION USING MESSAGE =
                    'What triggered a review cannot be rewritten after the '
                    'fact.';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_review_guard "
               "ON distributor_performance_reviews")
    op.execute("""
        CREATE TRIGGER trg_review_guard
        BEFORE UPDATE OR DELETE ON distributor_performance_reviews
        FOR EACH ROW EXECUTE FUNCTION performance_review_guard()
    """)


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS trg_review_guard "
               "ON distributor_performance_reviews")
    op.execute("DROP TRIGGER IF EXISTS trg_perf_immutable "
               "ON performance_periods")
    for fn in ("performance_review_guard", "performance_period_immutable"):
        op.execute(f"DROP FUNCTION IF EXISTS {fn}()")
    op.execute("DROP TABLE IF EXISTS distributor_performance_reviews CASCADE")
    op.execute("DROP TABLE IF EXISTS performance_periods CASCADE")
