"""Territory applications, and exclusivity enforced on the ground itself.

WHY THIS MIGRATION EXISTS
=========================

Phase 1 gave territories a single exclusivity guarantee: one live exclusive
holder per territory, as a partial unique index. That is correct and it stays.
It is also not enough, for a reason that only shows up once real territories are
drawn.

``territory_lgas`` is the authoritative coverage, and NOTHING STOPS TWO
TERRITORIES COVERING THE SAME LGA. "Lagos Mainland" and "Ikeja Corridor" can
both include Ikeja. Each is exclusive. Each has exactly one holder. The
per-territory index is satisfied in both cases -- and two distributors now hold
exclusive rights over the same ground.

That is not a theoretical edge. It is the precise dispute exclusivity exists to
prevent, it surfaces months later when both distributors are selling into Ikeja
and each has a signed agreement saying the area is theirs, and by then the
company has promised the same thing twice in writing.

So exclusivity is enforced HERE, at the LGA, by a trigger that refuses the
assignment and names the conflicting territory, the LGA, and who already holds
it. A trigger rather than an application check because an application check is
one untested code path away from not running, and this is a promise the company
makes in a contract.

The same trigger closes a narrower hole in the phase 1 index. That index covers
``is_exclusive`` rows only, so an exclusive territory carrying a non-exclusive
active assignment -- which happens when a territory's exclusivity is switched on
after it was assigned -- would accept a second, exclusive holder.

APPLICATIONS
============

``distributor_applications`` already models exactly what a territory request
needs: an applicant, a scored review, and a decision with a note and a decider.
It arrived in phase 1 and nothing has used it. Rather than create a second,
near-identical table, it gains a ``kind`` and a nullable ``territory_id``:
ONBOARDING applications are about becoming a distributor, TERRITORY applications
are about holding specific ground. One table, because they are the same shape
and a reviewer works one queue.

A decided application freezes. The score recorded on it is a SNAPSHOT taken at
decision time and is never recomputed -- rerunning today's weights against last
year's decision would rewrite why that decision was made, which is the one thing
an application record exists to preserve.
"""
from alembic import op
import sqlalchemy as sa

revision = 'z5678901234y'
down_revision = 'y4567890123x'
branch_labels = None
depends_on = None


def upgrade():
    # ---- applications become territory-aware -------------------------------
    for ddl in (
        "ALTER TABLE distributor_applications ADD COLUMN IF NOT EXISTS "
        "kind VARCHAR(16) NOT NULL DEFAULT 'ONBOARDING'",
        "ALTER TABLE distributor_applications ADD COLUMN IF NOT EXISTS "
        "territory_id UUID REFERENCES territories(id)",
        "ALTER TABLE distributor_applications ADD COLUMN IF NOT EXISTS "
        "requested_exclusive BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE distributor_applications ADD COLUMN IF NOT EXISTS "
        "requested_from DATE",
        "ALTER TABLE distributor_applications ADD COLUMN IF NOT EXISTS "
        "applicant_statement TEXT",
        "ALTER TABLE distributor_applications ADD COLUMN IF NOT EXISTS "
        "review_note TEXT",
        "ALTER TABLE distributor_applications ADD COLUMN IF NOT EXISTS "
        "reviewed_by UUID REFERENCES users(id)",
        "ALTER TABLE distributor_applications ADD COLUMN IF NOT EXISTS "
        "reviewed_at TIMESTAMPTZ",
        # The assignment this application produced. Without it an approved
        # application and the assignment it authorised are two unrelated rows,
        # and nobody can show that the grant came from a decision.
        "ALTER TABLE distributor_applications ADD COLUMN IF NOT EXISTS "
        "assignment_id UUID REFERENCES territory_assignments(id)",
        # Conflicts as they stood when the decision was made. A reviewer who
        # approved over a known conflict must not be able to claim later that
        # the system never showed them one.
        "ALTER TABLE distributor_applications ADD COLUMN IF NOT EXISTS "
        "conflicts_at_decision JSONB",
    ):
        op.execute(ddl)

    op.execute("""
        ALTER TABLE distributor_applications
          DROP CONSTRAINT IF EXISTS ck_app_kind
    """)
    op.execute("""
        ALTER TABLE distributor_applications
          ADD CONSTRAINT ck_app_kind CHECK (kind IN ('ONBOARDING','TERRITORY'))
    """)
    # A territory application that does not name a territory is not a
    # territory application.
    op.execute("""
        ALTER TABLE distributor_applications
          DROP CONSTRAINT IF EXISTS ck_app_territory_named
    """)
    op.execute("""
        ALTER TABLE distributor_applications
          ADD CONSTRAINT ck_app_territory_named CHECK (
              kind <> 'TERRITORY' OR territory_id IS NOT NULL)
    """)
    # A decision without a decider and a date is not a decision.
    op.execute("""
        ALTER TABLE distributor_applications
          DROP CONSTRAINT IF EXISTS ck_app_decided_recorded
    """)
    op.execute("""
        ALTER TABLE distributor_applications
          ADD CONSTRAINT ck_app_decided_recorded CHECK (
              status NOT IN ('APPROVED','REJECTED')
              OR (decided_by IS NOT NULL AND decided_at IS NOT NULL))
    """)

    op.execute("CREATE INDEX IF NOT EXISTS ix_dist_app_territory "
               "ON distributor_applications (territory_id) "
               "WHERE territory_id IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_dist_app_open "
               "ON distributor_applications (status, kind) "
               "WHERE status IN ('DRAFT','SUBMITTED','UNDER_REVIEW')")

    # One open request per distributor per territory. Two open requests for the
    # same ground from the same applicant is a duplicate, not a stronger case,
    # and it makes the review queue lie about how much work is in it.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_app_one_open_per_territory
            ON distributor_applications (distributor_id, territory_id)
         WHERE kind = 'TERRITORY'
           AND status IN ('DRAFT','SUBMITTED','UNDER_REVIEW')
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_app_one_open_onboarding
            ON distributor_applications (distributor_id)
         WHERE kind = 'ONBOARDING'
           AND status IN ('DRAFT','SUBMITTED','UNDER_REVIEW')
    """)

    # ---- a decided application is a record, not a working document ---------
    op.execute("""
        CREATE OR REPLACE FUNCTION distributor_application_guard()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Applications are not deletable. Withdraw one instead, so '
                    'the fact that it was made and withdrawn stays readable.';
            END IF;
            IF OLD.status IN ('APPROVED','REJECTED','WITHDRAWN') AND (
                   NEW.status IS DISTINCT FROM OLD.status
                OR NEW.eligibility_score IS DISTINCT FROM OLD.eligibility_score
                OR NEW.decision_note IS DISTINCT FROM OLD.decision_note
                OR NEW.decided_by IS DISTINCT FROM OLD.decided_by
                OR NEW.territory_id IS DISTINCT FROM OLD.territory_id
                OR NEW.distributor_id IS DISTINCT FROM OLD.distributor_id) THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Application ' || OLD.application_number || ' was already '
                    || lower(OLD.status) || '. Its decision and the score it '
                    'was based on are a record of why -- raise a new '
                    'application instead.';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_dist_app_guard "
               "ON distributor_applications")
    op.execute("""
        CREATE TRIGGER trg_dist_app_guard
        BEFORE UPDATE OR DELETE ON distributor_applications
        FOR EACH ROW EXECUTE FUNCTION distributor_application_guard()
    """)

    # ---- exclusivity, enforced on the ground -------------------------------
    #
    # See the module docstring. The per-territory index in phase 1 stays; this
    # catches what it cannot see, which is two DIFFERENT territories promising
    # the same LGA to two different distributors.
    op.execute("""
        CREATE OR REPLACE FUNCTION territory_exclusivity_guard()
        RETURNS TRIGGER AS $$
        DECLARE
            conflict RECORD;
        BEGIN
            -- Only a live assignment can conflict. Ended ones are history.
            IF NEW.assigned_to IS NOT NULL OR NEW.status <> 'ACTIVE' THEN
                RETURN NEW;
            END IF;

            -- A second live holder of an EXCLUSIVE territory, whatever the
            -- new row's own is_exclusive says. The phase 1 index only sees
            -- rows where is_exclusive is true on both sides.
            SELECT d.legal_name, d.distributor_code, t.code AS territory_code,
                   NULL::text AS lga_name
              INTO conflict
              FROM territory_assignments ta
              JOIN territories t ON t.id = ta.territory_id
              JOIN distributors d ON d.id = ta.distributor_id
             WHERE ta.territory_id = NEW.territory_id
               AND ta.id <> NEW.id
               AND ta.assigned_to IS NULL
               AND ta.status = 'ACTIVE'
               AND t.is_exclusive
             LIMIT 1;

            IF FOUND THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Territory ' || conflict.territory_code || ' is exclusive '
                    'and is already held by ' || conflict.legal_name || ' ('
                    || conflict.distributor_code || '). End that assignment '
                    'before granting it to anyone else.';
            END IF;

            -- The real guarantee: no two EXCLUSIVE territories may promise the
            -- same LGA to different distributors. Overlap between territories
            -- held by the SAME distributor is fine -- it is one promise.
            SELECT d.legal_name, d.distributor_code, t.code AS territory_code,
                   l.name AS lga_name
              INTO conflict
              FROM territory_assignments ta
              JOIN territories t ON t.id = ta.territory_id
              JOIN distributors d ON d.id = ta.distributor_id
              JOIN territory_lgas tl_other ON tl_other.territory_id = t.id
              JOIN territory_lgas tl_new
                   ON tl_new.lga_id = tl_other.lga_id
                  AND tl_new.territory_id = NEW.territory_id
              JOIN lgas l ON l.id = tl_other.lga_id
              JOIN territories t_new ON t_new.id = NEW.territory_id
             WHERE ta.id <> NEW.id
               AND ta.assigned_to IS NULL
               AND ta.status = 'ACTIVE'
               AND ta.distributor_id <> NEW.distributor_id
               AND t.is_exclusive
               AND t_new.is_exclusive
             LIMIT 1;

            IF FOUND THEN
                RAISE EXCEPTION USING MESSAGE =
                    'This would promise ' || conflict.lga_name || ' to two '
                    'distributors at once. ' || conflict.legal_name || ' ('
                    || conflict.distributor_code || ') already holds it '
                    'exclusively through territory '
                    || conflict.territory_code || '. Narrow one territory''s '
                    'coverage or end that assignment first.';
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_territory_exclusivity "
               "ON territory_assignments")
    op.execute("""
        CREATE TRIGGER trg_territory_exclusivity
        BEFORE INSERT OR UPDATE ON territory_assignments
        FOR EACH ROW EXECUTE FUNCTION territory_exclusivity_guard()
    """)

    # Changing a territory's coverage can create the same clash by the back
    # door: add Ikeja to a territory that is already assigned and you have
    # promised it twice without touching territory_assignments at all.
    op.execute("""
        CREATE OR REPLACE FUNCTION territory_coverage_guard()
        RETURNS TRIGGER AS $$
        DECLARE
            conflict RECORD;
        BEGIN
            SELECT d.legal_name, d.distributor_code, t.code AS territory_code,
                   l.name AS lga_name
              INTO conflict
              FROM territory_assignments ta_new
              JOIN territories t_new ON t_new.id = ta_new.territory_id
              JOIN territory_assignments ta ON ta.assigned_to IS NULL
                   AND ta.status = 'ACTIVE'
                   AND ta.distributor_id <> ta_new.distributor_id
              JOIN territories t ON t.id = ta.territory_id AND t.is_exclusive
              JOIN territory_lgas tl ON tl.territory_id = t.id
                   AND tl.lga_id = NEW.lga_id
              JOIN distributors d ON d.id = ta.distributor_id
              JOIN lgas l ON l.id = NEW.lga_id
             WHERE ta_new.territory_id = NEW.territory_id
               AND ta_new.assigned_to IS NULL
               AND ta_new.status = 'ACTIVE'
               AND t_new.is_exclusive
             LIMIT 1;

            IF FOUND THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Adding ' || conflict.lga_name || ' here would promise it '
                    'to two distributors at once -- ' || conflict.legal_name
                    || ' already holds it exclusively through territory '
                    || conflict.territory_code || '. End that assignment or '
                    'narrow that territory first.';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_territory_coverage "
               "ON territory_lgas")
    op.execute("""
        CREATE TRIGGER trg_territory_coverage
        BEFORE INSERT ON territory_lgas
        FOR EACH ROW EXECUTE FUNCTION territory_coverage_guard()
    """)


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS trg_territory_coverage ON territory_lgas")
    op.execute("DROP TRIGGER IF EXISTS trg_territory_exclusivity "
               "ON territory_assignments")
    op.execute("DROP TRIGGER IF EXISTS trg_dist_app_guard "
               "ON distributor_applications")
    for fn in ("territory_coverage_guard", "territory_exclusivity_guard",
               "distributor_application_guard"):
        op.execute(f"DROP FUNCTION IF EXISTS {fn}()")
    for idx in ("uq_app_one_open_per_territory", "uq_app_one_open_onboarding",
                "ix_dist_app_territory", "ix_dist_app_open"):
        op.execute(f"DROP INDEX IF EXISTS {idx}")
    for con in ("ck_app_kind", "ck_app_territory_named",
                "ck_app_decided_recorded"):
        op.execute(f"ALTER TABLE distributor_applications "
                   f"DROP CONSTRAINT IF EXISTS {con}")
    for col in ("kind", "territory_id", "requested_exclusive", "requested_from",
                "applicant_statement", "review_note", "reviewed_by",
                "reviewed_at", "assignment_id", "conflicts_at_decision"):
        op.execute(f"ALTER TABLE distributor_applications "
                   f"DROP COLUMN IF EXISTS {col}")
