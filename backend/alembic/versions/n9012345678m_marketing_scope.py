"""Link a login to a staff record, so "my records" can mean something.

Revision ID: n9012345678m
Revises: m8901234567l
Create Date: 2026-10-02

THE PROBLEM THIS EXISTS TO SOLVE
================================
Every route in the marketing module takes `staff_id` (or `marketer_staff_id`)
as a value chosen by the caller, and checks nothing:

    GET  /api/marketing/logs?staff_id=<anyone>     -> their activity
    PUT  /api/marketing/logs/{log_id}              -> edit anyone's log
    POST /api/marketing/logs {marketer_staff_id}   -> file a log as anyone

Any authenticated employee could therefore read every marketer's visits,
customers, outcomes and order values, alter somebody else's record of what
they did, or create one in their name. That is an insecure direct object
reference, and it has been live.

WHY A SCHEMA CHANGE WAS NEEDED TO FIX IT
========================================
The obvious fix -- "a marketer sees only their own records" -- could not be
written, because nothing in this database connected a login to a person.
`users` holds email, full_name, role and department. `staff` holds
employee_id, first_name and last_name. There was no join between them, so the
application had no way to answer "which staff record is this user?" and
therefore no way to tell one marketer's data from another's.

This adds that link. It is nullable, because the mapping has to be made by a
human who knows which login belongs to which employee, and guessing it from
matching names would be worse than leaving it empty -- two people share a name
far more often than is comfortable, and the cost of being wrong here is one
person reading another's records under the system's assurance that they
cannot.

WHAT HAPPENS WHILE IT IS NULL
=============================
The application refuses rather than guesses. A non-supervisor whose login is
not linked is told, in those words, that their account is not yet linked to a
staff record and who can link it. They are not shown an empty list, which
would read as "you have done no work", and they are certainly not shown
everybody's.

UNIQUE, AND WHY
===============
One login, one staff record. Two logins pointing at the same employee would
make "their own records" ambiguous and would let a shared account accumulate
one person's activity under another's name.
"""
from alembic import op

revision = 'n9012345678m'
down_revision = 'm8901234567l'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE users ADD COLUMN IF NOT EXISTS staff_id UUID
            REFERENCES staff(id) ON DELETE SET NULL
    """)
    # Partial unique: many logins may legitimately have no staff record (a
    # system account, an integration), so only the non-null values are
    # constrained.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_users_staff
            ON users (staff_id) WHERE staff_id IS NOT NULL
    """)

    # Who linked whom, and when. Changing which employee a login belongs to
    # changes what that login can read, so it is not a silent edit.
    op.execute("""
        CREATE TABLE IF NOT EXISTS user_staff_link_log (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            staff_id UUID REFERENCES staff(id) ON DELETE SET NULL,
            previous_staff_id UUID REFERENCES staff(id) ON DELETE SET NULL,
            actor_id UUID REFERENCES users(id),
            actor_name VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_usll_user "
               "ON user_staff_link_log (user_id, created_at DESC)")

    op.execute("""
        CREATE OR REPLACE FUNCTION trg_link_log_append_only()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                'user_staff_link_log is append-only: it records who gave which '
                'login access to whose records';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS no_edit_link_log ON user_staff_link_log")
    op.execute("""
        CREATE TRIGGER no_edit_link_log
        BEFORE UPDATE OR DELETE ON user_staff_link_log
        FOR EACH ROW EXECUTE FUNCTION trg_link_log_append_only()
    """)


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS no_edit_link_log ON user_staff_link_log")
    op.execute("DROP TABLE IF EXISTS user_staff_link_log CASCADE")
    op.execute("DROP FUNCTION IF EXISTS trg_link_log_append_only()")
    op.execute("DROP INDEX IF EXISTS uq_users_staff")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS staff_id")
