"""Click-to-call bridging: the network's own record of a call.

Revision ID: v1234567890u
Revises: u0123456789t
Create Date: 2026-09-11

WHY THIS EXISTS
---------------
The call log's durations are estimates: the browser times how long a phone was
away and the staff member confirms or corrects it. Useful, but soft, and a
person who wants a longer number need only wait before coming back.

Bridging removes the guesswork. The staff member taps Call, the telephony
provider rings THEIR phone, they answer, the provider then rings the customer
and joins the two legs. The provider timed the bridge, so the duration and the
cost come from the network, not from anyone's phone -- and neither the caller
nor this application can alter them.

That is what makes duration_source = 'VERIFIED' meaningful, and why the check
constraint added in u0123456789t refuses VERIFIED unless a provider reference
is present.

WHY RAW CALLBACKS ARE STORED
----------------------------
call_provider_events keeps every callback exactly as it arrived, before any
interpretation. Three reasons, in order of how much they hurt when missing:

  * Providers change field names and add states. A callback we failed to parse
    is still a fact about a call that happened; discarding it would silently
    lose billed minutes.
  * A callback can arrive before, after, or instead of the one we expect, and
    can arrive twice. Keeping the raw record makes reconciling that possible
    afterwards rather than requiring us to get it right first time.
  * The endpoint is UNAUTHENTICATED by necessity -- a provider cannot hold a
    bearer token. Recording the source address and whether the shared secret
    matched turns "someone posted something" into evidence.

The table is append-only, enforced by a trigger, for the same reason the wallet
ledger is: it is the record that says what the network told us.
"""
from alembic import op
import sqlalchemy as sa

revision = 'v1234567890u'
down_revision = 'u0123456789t'
branch_labels = None
depends_on = None


def upgrade():
    # ---- what a bridged call needs to remember -------------------------
    for ddl in (
        # The staff member's own line -- the first leg the provider rings.
        # Stored per call because a person's number can change and the record
        # must say which number was actually used at the time.
        "ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS "
        "staff_phone VARCHAR(64)",
        "ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS "
        "cost_currency VARCHAR(8)",
        # QUEUED -> RINGING_STAFF -> BRIDGED -> COMPLETED/FAILED, as reported
        # by the provider. Distinct from `status`, which is this application's
        # own lifecycle and must not be driven directly by a remote caller.
        "ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS "
        "bridge_state VARCHAR(24)",
        "ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS "
        "bridge_failure_reason VARCHAR(255)",
    ):
        op.execute(ddl)

    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_call_provider_ref
            ON call_logs (provider, provider_reference)
         WHERE provider_reference IS NOT NULL
    """)

    # ---- every callback, exactly as it arrived --------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS call_provider_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            -- Nullable: a callback can arrive for a session we have not
            -- matched yet, and losing it would be worse than storing it
            -- unattached.
            call_id UUID REFERENCES call_logs(id),
            provider VARCHAR(32) NOT NULL,
            session_id VARCHAR(128),
            event_type VARCHAR(64),
            raw JSONB NOT NULL,
            -- Did the shared secret match? Recorded rather than enforced-only,
            -- so a stream of failures is visible instead of silently dropped.
            secret_ok BOOLEAN NOT NULL DEFAULT FALSE,
            remote_ip VARCHAR(64),
            applied BOOLEAN NOT NULL DEFAULT FALSE,
            note VARCHAR(255),
            received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_cpe_session "
               "ON call_provider_events (session_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_cpe_call "
               "ON call_provider_events (call_id, received_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_cpe_received "
               "ON call_provider_events (received_at DESC)")

    op.execute("""
        CREATE OR REPLACE FUNCTION call_provider_events_immutable()
        RETURNS TRIGGER AS $$
        BEGIN
            -- `applied` is the one field the application sets after the fact,
            -- so UPDATE is allowed only to flip it. Everything the provider
            -- said is frozen.
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Provider call events are append-only: they are the record '
                    'of what the network reported.';
            END IF;
            IF NEW.raw::text <> OLD.raw::text
               OR NEW.session_id IS DISTINCT FROM OLD.session_id
               OR NEW.provider <> OLD.provider
               OR NEW.received_at <> OLD.received_at
               OR NEW.secret_ok <> OLD.secret_ok THEN
                RAISE EXCEPTION USING MESSAGE =
                    'A provider event cannot be rewritten; only its applied '
                    'flag and note may change.';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_cpe_immutable "
               "ON call_provider_events")
    op.execute("""
        CREATE TRIGGER trg_cpe_immutable
        BEFORE UPDATE OR DELETE ON call_provider_events
        FOR EACH ROW EXECUTE FUNCTION call_provider_events_immutable()
    """)

    # ---- let a bridged call be completed by the provider ----------------
    #
    # u0123456789t froze a COMPLETED call's duration so nobody could re-time it
    # after a manager queried it. That guard has to make one exception: a
    # provider's VERIFIED record may overwrite an estimate, because it is the
    # authoritative figure arriving late. The reverse is never allowed -- an
    # estimate cannot overwrite a verified duration.
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
            IF OLD.duration_source = 'VERIFIED'
               AND NEW.duration_source <> 'VERIFIED' THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Call ' || OLD.call_reference || ' carries the network''s '
                    'own duration; it cannot be replaced by an estimate.';
            END IF;
            IF OLD.status = 'COMPLETED'
               AND NEW.duration_source <> 'VERIFIED'
               AND (NEW.duration_seconds IS DISTINCT FROM OLD.duration_seconds
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

    # BRIDGE joins PHONE and WHATSAPP as a way a call can be placed.
    op.execute("ALTER TABLE call_logs DROP CONSTRAINT IF EXISTS ck_call_channel")
    op.execute("""
        ALTER TABLE call_logs ADD CONSTRAINT ck_call_channel
            CHECK (channel IN ('PHONE','WHATSAPP','BRIDGE'))
    """)


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS trg_cpe_immutable "
               "ON call_provider_events")
    op.execute("DROP FUNCTION IF EXISTS call_provider_events_immutable()")
    op.execute("DROP TABLE IF EXISTS call_provider_events CASCADE")
    op.execute("DROP INDEX IF EXISTS uq_call_provider_ref")
    op.execute("ALTER TABLE call_logs DROP CONSTRAINT IF EXISTS ck_call_channel")
    op.execute("""
        ALTER TABLE call_logs ADD CONSTRAINT ck_call_channel
            CHECK (channel IN ('PHONE','WHATSAPP'))
    """)
    for col in ("staff_phone", "cost_currency", "bridge_state",
                "bridge_failure_reason"):
        op.execute(f"ALTER TABLE call_logs DROP COLUMN IF EXISTS {col}")
