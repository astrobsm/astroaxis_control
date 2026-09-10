"""Company call log: who called which customer, when, and for how long.

Revision ID: u0123456789t
Revises: t9012345678s
Create Date: 2026-09-11

WHAT THIS CAN AND CANNOT KNOW
-----------------------------
A web application cannot read a phone's call log. There is no browser API for
it on Android or iOS, and there will not be one. When the app hands off to the
dialer or to WhatsApp it is backgrounded, and it learns nothing about what
happened next.

What it CAN measure is how long the app was in the background between the staff
member tapping Call and returning. That interval contains the call, but also
the dialling, the ringing, and whatever delay there was before they switched
back -- and a person who wants a longer number need only wait before returning.

So `duration_seconds` is deliberately paired with `duration_source`, and every
screen that shows a duration shows where it came from:

    MEASURED   the app timed the gap; nobody confirmed it
    CONFIRMED  the staff member was shown the measured figure and accepted it
    MANUAL     the staff member typed the figure in
    UNKNOWN    the app never saw them come back

Recording the provenance is the whole point. This module sits next to a wallet
ledger whose figures are exact and enforced by database triggers; presenting a
soft estimate in the same typeface as a hard one would quietly devalue both.
A call duration here is evidence of activity, never evidence of a fact.

WHY THERE IS NO 'VERIFIED' SOURCE
---------------------------------
There would be, if calls were routed through a company telephony provider --
the carrier's own record is authoritative and unforgeable by the employee.
`provider_reference` exists for exactly that: when a VoIP or CDR integration is
added later, it holds the provider's call id and a VERIFIED source becomes
possible without changing this table or anything above it.
"""
from alembic import op
import sqlalchemy as sa

revision = 'u0123456789t'
down_revision = 't9012345678s'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS call_logs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            call_reference VARCHAR(40) UNIQUE NOT NULL,

            -- Who made the call. Always the authenticated user; never accepted
            -- from the client.
            user_id UUID NOT NULL REFERENCES users(id),
            department VARCHAR(100),

            -- Who was called. customer_id is set when the contact came from
            -- the company database; the name and number are stored alongside
            -- regardless, because a contact picked from the phone has no row
            -- here and the record must still say who was called.
            customer_id UUID REFERENCES customers(id),
            contact_name VARCHAR(255),
            contact_phone VARCHAR(64) NOT NULL,
            -- CUSTOMER | PHONE_CONTACT | MANUAL -- where the number came from.
            contact_source VARCHAR(20) NOT NULL DEFAULT 'MANUAL',

            -- PHONE | WHATSAPP
            channel VARCHAR(16) NOT NULL DEFAULT 'PHONE',
            direction VARCHAR(12) NOT NULL DEFAULT 'OUTBOUND',

            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            ended_at TIMESTAMPTZ,
            duration_seconds INTEGER,
            -- See the module docstring. Never render a duration without this.
            duration_source VARCHAR(12) NOT NULL DEFAULT 'UNKNOWN',
            -- What the app actually timed, kept even when the staff member
            -- overrides it: the gap between the two is worth seeing.
            measured_seconds INTEGER,

            -- IN_PROGRESS until the app comes back or the row is completed.
            status VARCHAR(16) NOT NULL DEFAULT 'IN_PROGRESS',
            purpose VARCHAR(255),
            outcome VARCHAR(32),
            notes TEXT,

            -- Set once a telephony provider is integrated; until then NULL and
            -- duration_source can never be VERIFIED.
            provider VARCHAR(32),
            provider_reference VARCHAR(128),
            cost NUMERIC(18,2),

            latitude NUMERIC(10,7),
            longitude NUMERIC(10,7),
            user_agent VARCHAR(500),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT ck_call_channel CHECK (channel IN ('PHONE','WHATSAPP')),
            CONSTRAINT ck_call_direction CHECK (direction IN
                ('OUTBOUND','INBOUND')),
            CONSTRAINT ck_call_status CHECK (status IN
                ('IN_PROGRESS','COMPLETED','CANCELLED')),
            CONSTRAINT ck_call_source CHECK (duration_source IN
                ('MEASURED','CONFIRMED','MANUAL','UNKNOWN','VERIFIED')),
            CONSTRAINT ck_call_contact_source CHECK (contact_source IN
                ('CUSTOMER','PHONE_CONTACT','MANUAL')),
            CONSTRAINT ck_call_duration CHECK (
                duration_seconds IS NULL OR duration_seconds >= 0),
            CONSTRAINT ck_call_measured CHECK (
                measured_seconds IS NULL OR measured_seconds >= 0),
            -- VERIFIED is reserved for a provider's own record. Without a
            -- provider reference it would be a claim dressed up as a fact.
            CONSTRAINT ck_call_verified_needs_provider CHECK (
                duration_source <> 'VERIFIED' OR provider_reference IS NOT NULL),
            CONSTRAINT ck_call_completed CHECK (
                status <> 'COMPLETED' OR duration_seconds IS NOT NULL)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_call_user "
               "ON call_logs (user_id, started_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_call_customer "
               "ON call_logs (customer_id, started_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_call_started "
               "ON call_logs (started_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_call_status "
               "ON call_logs (status) WHERE status = 'IN_PROGRESS'")
    op.execute("CREATE INDEX IF NOT EXISTS ix_call_dept "
               "ON call_logs (department, started_at DESC)")

    # A completed call is a record of something that happened. It may be
    # completed once -- filling in the duration and outcome -- and after that
    # who called whom, when, and how long it was said to take are frozen.
    # Without this, a call could be quietly re-timed after a manager queried it.
    op.execute("""
        CREATE OR REPLACE FUNCTION call_log_guard()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Call records cannot be deleted. Cancel a call that did '
                    'not happen; the record of the attempt remains.';
            END IF;
            IF NEW.user_id <> OLD.user_id
               OR NEW.call_reference <> OLD.call_reference
               OR NEW.started_at <> OLD.started_at
               OR NEW.contact_phone <> OLD.contact_phone THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Call ' || OLD.call_reference || ' is immutable in caller, '
                    'reference, start time and number called.';
            END IF;
            IF OLD.status = 'COMPLETED' AND (
                   NEW.duration_seconds IS DISTINCT FROM OLD.duration_seconds
                OR NEW.duration_source  IS DISTINCT FROM OLD.duration_source
                OR NEW.status <> OLD.status) THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Call ' || OLD.call_reference || ' is already completed; '
                    'its duration cannot be revised.';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_call_logs_guard ON call_logs")
    op.execute("""
        CREATE TRIGGER trg_call_logs_guard
        BEFORE UPDATE OR DELETE ON call_logs
        FOR EACH ROW EXECUTE FUNCTION call_log_guard()
    """)


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS trg_call_logs_guard ON call_logs")
    op.execute("DROP FUNCTION IF EXISTS call_log_guard()")
    op.execute("DROP TABLE IF EXISTS call_logs CASCADE")
