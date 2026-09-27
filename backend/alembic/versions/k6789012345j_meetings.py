"""Meetings: scheduling, secure guest links, roles and attendance.

Revision ID: k6789012345j
Revises: j5678901234i
Create Date: 2026-09-27

WHAT THIS REPLACES
==================
The video conference that existed before this was ninety lines inside
AppMain.js. It asked for a room name, prefixed it with "AstroBSM_", and mounted
the PUBLIC meet.jit.si in an iframe. Anyone on the internet who guessed
"AstroBSM_weekly-standup" was in the management meeting, and nothing about the
meeting was ever written down: no schedule, no attendance, no invitation, no
record that it happened.

So this migration is not an enhancement of a data model. There was no data
model. It is the first one.

THE ROOM NAME IS A SECRET, AND THAT IS THE WHOLE SECURITY MODEL UNTIL JaaS
==========================================================================
Jitsi's public server has no notion of who we are, so on that server the only
thing standing between an outsider and the meeting is whether they can guess
the room. `room_name` is therefore 22 characters of `secrets.token_urlsafe`,
never derived from the title, and never shown anywhere a person might paste it
carelessly -- it reaches a browser only after the server has decided that
browser may join.

`meeting_code` is the short human-facing identifier ("say meeting BSM-4K2P on
the phone"). It is deliberately NOT the room: knowing the code gets you nothing
without a link, so it can be read aloud safely.

When JaaS credentials are configured the room name stops being the only
defence -- the media server then checks a signature we produce -- but the room
stays random, because a defence that costs nothing should not be removed when a
second one arrives.

WHY GUEST ACCESS IS A TOKEN ON THE MEETING, NOT AN ACCOUNT
==========================================================
`link_token_sha256` follows the pattern the distributor ordering link
established: the token lives in the URL, only its hash is stored, it expires,
it is revocable, and regenerating it invalidates the old one immediately. A
guest who holds it can do exactly one thing -- ask to join one meeting. There
is no user row, no session, and no path from it to any other part of the ERP.

ATTENDANCE IS AN OBSERVATION, NOT A CLAIM
=========================================
`meeting_attendance` rows are opened when somebody joins and closed when they
leave or when the meeting ends. `left_at` is nullable because a browser that
crashes never tells us it left; a row with no `left_at` is an unfinished
observation and the reports say so rather than inventing a departure time.
This is the same discipline `call_logs.duration_source` applies to call length.
"""
from alembic import op

revision = 'k6789012345j'
down_revision = 'j5678901234i'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS meetings (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            -- Said aloud on the phone. Knowing it grants nothing.
            meeting_code VARCHAR(24) UNIQUE NOT NULL,
            -- The actual Jitsi room. High entropy, server-issued, and on the
            -- public Jitsi server this IS the credential.
            room_name VARCHAR(64) UNIQUE NOT NULL,

            title VARCHAR(255) NOT NULL,
            description TEXT,
            scheduled_start TIMESTAMPTZ NOT NULL,
            duration_minutes INTEGER NOT NULL DEFAULT 60,

            host_user_id UUID NOT NULL REFERENCES users(id),

            -- SCHEDULED -> LIVE -> ENDED, or CANCELLED from either of the
            -- first two. Never deleted: a meeting that happened is a record.
            status VARCHAR(16) NOT NULL DEFAULT 'SCHEDULED',
            started_at TIMESTAMPTZ,
            ended_at TIMESTAMPTZ,
            cancelled_at TIMESTAMPTZ,
            cancel_reason TEXT,

            -- The guest link. Only the hash is stored, exactly as the
            -- distributor ordering link does.
            link_token_sha256 VARCHAR(64) UNIQUE,
            link_token_hint VARCHAR(12),
            link_expires_at TIMESTAMPTZ,
            link_revoked_at TIMESTAMPTZ,
            link_revoke_reason TEXT,

            -- What a guest is allowed to do. Enforced server-side when the
            -- guest's token is minted, not by hiding buttons.
            guest_access_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            waiting_room BOOLEAN NOT NULL DEFAULT TRUE,
            guest_screen_share BOOLEAN NOT NULL DEFAULT FALSE,
            guest_chat BOOLEAN NOT NULL DEFAULT TRUE,
            max_participants INTEGER NOT NULL DEFAULT 50,

            -- Optional second factor on the link. Hashed; never recoverable.
            passcode_hash VARCHAR(255),

            is_locked BOOLEAN NOT NULL DEFAULT FALSE,

            created_by UUID REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT ck_meeting_status CHECK (status IN
                ('SCHEDULED','LIVE','ENDED','CANCELLED')),
            CONSTRAINT ck_meeting_duration CHECK (
                duration_minutes BETWEEN 1 AND 1440),
            CONSTRAINT ck_meeting_capacity CHECK (
                max_participants BETWEEN 2 AND 500),
            -- A cancelled meeting must say why. "Cancelled" with no reason is
            -- the row somebody argues about a month later.
            CONSTRAINT ck_meeting_cancel_reason CHECK (
                status <> 'CANCELLED' OR cancel_reason IS NOT NULL)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_meetings_host "
               "ON meetings (host_user_id, scheduled_start DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_meetings_upcoming "
               "ON meetings (scheduled_start) "
               "WHERE status IN ('SCHEDULED','LIVE')")

    # ---- who was invited ---------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS meeting_participants (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            meeting_id UUID NOT NULL REFERENCES meetings(id)
                ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role VARCHAR(16) NOT NULL DEFAULT 'PARTICIPANT',
            invited_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            invited_by UUID REFERENCES users(id),
            CONSTRAINT ck_mp_role CHECK (role IN
                ('HOST','CO_HOST','PARTICIPANT'))
        )
    """)
    # One invitation per person per meeting. Inviting somebody twice is a
    # mis-click, not two invitations.
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_meeting_participant "
               "ON meeting_participants (meeting_id, user_id)")

    # ---- who is waiting to be let in --------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS meeting_waiting_room (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            meeting_id UUID NOT NULL REFERENCES meetings(id)
                ON DELETE CASCADE,
            display_name VARCHAR(120) NOT NULL,
            -- Set when an INTERNAL user is waiting; null for a guest.
            user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            is_guest BOOLEAN NOT NULL DEFAULT TRUE,
            status VARCHAR(16) NOT NULL DEFAULT 'WAITING',
            requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            decided_at TIMESTAMPTZ,
            decided_by UUID REFERENCES users(id),
            ip_address VARCHAR(64),
            user_agent VARCHAR(500),
            CONSTRAINT ck_mwr_status CHECK (status IN
                ('WAITING','ADMITTED','REJECTED','WITHDRAWN'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_mwr_pending "
               "ON meeting_waiting_room (meeting_id, requested_at) "
               "WHERE status = 'WAITING'")

    # ---- who actually attended --------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS meeting_attendance (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            meeting_id UUID NOT NULL REFERENCES meetings(id)
                ON DELETE CASCADE,
            -- Null for a guest: there is no account to point at, which is the
            -- whole point of a guest.
            user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            display_name VARCHAR(120) NOT NULL,
            is_guest BOOLEAN NOT NULL DEFAULT FALSE,
            role VARCHAR(16) NOT NULL DEFAULT 'PARTICIPANT',
            joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            -- Nullable on purpose: a browser that crashed never told us it
            -- left. A row with no left_at is an unfinished observation, and
            -- the reports say so rather than inventing a departure.
            left_at TIMESTAMPTZ,
            duration_seconds INTEGER,
            CONSTRAINT ck_ma_role CHECK (role IN
                ('HOST','CO_HOST','PARTICIPANT','GUEST')),
            CONSTRAINT ck_ma_interval CHECK (
                left_at IS NULL OR left_at >= joined_at)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_ma_meeting "
               "ON meeting_attendance (meeting_id, joined_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_ma_open "
               "ON meeting_attendance (meeting_id) WHERE left_at IS NULL")

    # ---- what was done, and by whom ---------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS meeting_audit_logs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            meeting_id UUID REFERENCES meetings(id) ON DELETE SET NULL,
            event_type VARCHAR(48) NOT NULL,
            actor_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            actor_label VARCHAR(255),
            detail JSONB,
            ip_address VARCHAR(64),
            user_agent VARCHAR(500),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_meeting_audit "
               "ON meeting_audit_logs (meeting_id, created_at DESC)")

    # Append-only, enforced by the database. This table is what somebody reads
    # to find out who let an outsider into a meeting; a convention that it is
    # not edited is worth nothing next to a trigger that refuses.
    op.execute("""
        CREATE OR REPLACE FUNCTION meeting_audit_immutable()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                'meeting_audit_logs is append-only; % is not permitted',
                TG_OP;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_meeting_audit_immutable "
               "ON meeting_audit_logs")
    op.execute("""
        CREATE TRIGGER trg_meeting_audit_immutable
        BEFORE UPDATE OR DELETE ON meeting_audit_logs
        FOR EACH ROW EXECUTE FUNCTION meeting_audit_immutable()
    """)


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS trg_meeting_audit_immutable "
               "ON meeting_audit_logs")
    op.execute("DROP FUNCTION IF EXISTS meeting_audit_immutable()")
    for table in ("meeting_audit_logs", "meeting_attendance",
                  "meeting_waiting_room", "meeting_participants", "meetings"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
