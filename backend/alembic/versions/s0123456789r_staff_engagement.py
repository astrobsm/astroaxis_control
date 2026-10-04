"""Staff birthdays and work anniversaries, with consent that defaults to no.

Revision ID: s0123456789r
Revises: r0123456789q
Create Date: 2026-10-04

SEPARATE FROM THE CUSTOMER AGENT, IN CODE AND NOT BY CONVENTION
===============================================================
This is HR data about employees. It gets its own tables, its own service and
its own routes, and nothing a customer-facing agent can reach will ever touch
them. The compartmentalisation has to be structural, because a shared helper
that "just needs the staff name" is how a customer ends up being told whose
birthday it is.

CONSENT DEFAULTS TO NO, AND THAT IS A DEPARTURE
===============================================
The brief said the default should be the most privacy-preserving setting
permitted by policy. This goes further: no automated message reaches any
member of staff until that person has said yes.

The reason is specific rather than general caution. The dashboard already
shows every employee's birthday AND THEIR AGE to anyone who can open it. Age
is not something an employer should broadcast internally by default, and a
system that begins messaging people about a date they never agreed to share
compounds a problem rather than adding a feature. At fourteen staff, asking
costs almost nothing.

HIDING ALREADY EXISTS, AND THIS MUST RESPECT IT
===============================================
m8901234567l added `staff.display_hidden` with a compulsory reason, and one of
the reasons the interface suggests is "Bereavement -- no birthday reminders".
A staff member hidden for exactly that reason must not then receive an
automated birthday greeting. The service checks both flags; this migration
exists partly so that check has something to read.

WHAT IS STORED AND WHAT IS NOT
==============================
Stored: whether this person agreed, when, and what they agreed to see shared.
Not stored: a second copy of their date of birth. `staff.date_of_birth`
already holds it and that is the only place it lives. A duplicate would drift,
and a wrong birthday is worse than none.

A work anniversary is likewise derived from `staff.hire_date`. Nothing is
copied.
"""
from alembic import op

revision = 's0123456789r'
down_revision = 'r0123456789q'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS staff_engagement_consent (
            staff_id UUID PRIMARY KEY REFERENCES staff(id) ON DELETE CASCADE,

            -- Nothing is sent to anybody while this is FALSE, which is how
            -- every row starts.
            birthday_messages BOOLEAN NOT NULL DEFAULT FALSE,
            anniversary_messages BOOLEAN NOT NULL DEFAULT FALSE,

            -- How much of it other people may see. The default shows the
            -- person's date to nobody: a team that wants to celebrate
            -- together can say so, but it is theirs to say.
            visibility VARCHAR(24) NOT NULL DEFAULT 'NOBODY',

            preferred_name VARCHAR(120),
            preferred_channel VARCHAR(16),

            -- Who recorded the answer and how. 'STAFF_MEMBER' carries more
            -- weight than 'HR_ENTERED', and anyone checking will want to know
            -- which it was.
            consent_source VARCHAR(40),
            consent_at TIMESTAMPTZ,
            updated_by UUID REFERENCES users(id),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT ck_sec_visibility CHECK (visibility IN
                ('NOBODY','MANAGER','DEPARTMENT','ORGANISATION')),
            CONSTRAINT ck_sec_channel CHECK (preferred_channel IS NULL
                OR preferred_channel IN ('WHATSAPP','SMS','EMAIL','IN_APP'))
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS staff_engagement_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            staff_id UUID NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
            change VARCHAR(40) NOT NULL,
            detail TEXT,
            actor_id UUID REFERENCES users(id),
            actor_name VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_staff_engagement_events "
               "ON staff_engagement_events (staff_id, created_at DESC)")

    op.execute("""
        CREATE OR REPLACE FUNCTION trg_staff_engagement_append_only()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                'staff_engagement_events is append-only: it records what each '
                'member of staff agreed to, and who recorded it';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS no_edit_staff_engagement "
               "ON staff_engagement_events")
    op.execute("""
        CREATE TRIGGER no_edit_staff_engagement
        BEFORE UPDATE OR DELETE ON staff_engagement_events
        FOR EACH ROW EXECUTE FUNCTION trg_staff_engagement_append_only()
    """)

    # What was actually sent, per person per occasion. UNIQUE is the whole
    # point: a job that runs twice, or a container restarted at midnight, must
    # not wish somebody a happy birthday twice.
    op.execute("""
        CREATE TABLE IF NOT EXISTS staff_engagement_sends (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            staff_id UUID NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
            occasion VARCHAR(24) NOT NULL,
            -- The year the occasion fell in, not the date it was sent: a
            -- greeting posted a day late is still that year's greeting.
            occasion_year SMALLINT NOT NULL,
            years_of_service SMALLINT,
            outbound_message_id UUID REFERENCES outbound_messages(id),
            status VARCHAR(16) NOT NULL DEFAULT 'QUEUED',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT ck_ses_occasion CHECK (occasion IN
                ('BIRTHDAY','WORK_ANNIVERSARY')),
            CONSTRAINT uq_ses_once UNIQUE (staff_id, occasion, occasion_year)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_staff_sends_year "
               "ON staff_engagement_sends (occasion_year, occasion)")

    op.execute("""
        INSERT INTO app_settings (key, value, value_type, description) VALUES
          ('STAFF_ENGAGEMENT_ENABLED', 'false', 'BOOLEAN',
           'Allow automated birthday and work-anniversary messages to staff. '
           'Each person must also have agreed individually.'),
          ('STAFF_ANNIVERSARY_MILESTONES', '1,3,5,10,15,20', 'STRING',
           'Which years of service are marked. Other years pass unremarked '
           'rather than producing a message nobody wanted.'),
          ('STAFF_BIRTHDAY_TEMPLATE',
           'Happy Birthday, {preferred_name}! On behalf of everyone at '
           'Bonnesante Medicals, we wish you a wonderful day and a year of '
           'good health and happiness. Thank you for all you do.',
           'STRING', 'The birthday message. {preferred_name} is replaced.'),
          ('STAFF_ANNIVERSARY_TEMPLATE',
           'Congratulations, {preferred_name}, on {years} years with '
           'Bonnesante Medicals. Thank you for your dedication and your '
           'contribution to the team.',
           'STRING',
           'The work-anniversary message. {preferred_name} and {years} are '
           'replaced.')
        ON CONFLICT (key) DO NOTHING
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS staff_engagement_sends CASCADE")
    op.execute("DROP TRIGGER IF EXISTS no_edit_staff_engagement "
               "ON staff_engagement_events")
    op.execute("DROP TABLE IF EXISTS staff_engagement_events CASCADE")
    op.execute("DROP FUNCTION IF EXISTS trg_staff_engagement_append_only()")
    # The consent table stays. A member of staff who declined automated
    # messages must not become contactable again because somebody rolled back
    # a schema.
    op.execute("DELETE FROM app_settings WHERE key IN "
               "('STAFF_ENGAGEMENT_ENABLED','STAFF_ANNIVERSARY_MILESTONES',"
               "'STAFF_BIRTHDAY_TEMPLATE','STAFF_ANNIVERSARY_TEMPLATE')")
