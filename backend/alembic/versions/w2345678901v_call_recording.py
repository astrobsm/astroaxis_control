"""Call recording: the audio, who heard it, and when it must be destroyed.

Revision ID: w2345678901v
Revises: v1234567890u
Create Date: 2026-09-11

WHAT MAKES THIS DIFFERENT FROM EVERY OTHER TABLE HERE
-----------------------------------------------------
A call recording is personal data about two people -- the employee and the
customer -- and neither of them works for this database. Under the Nigeria
Data Protection Act 2023 that carries obligations the rest of this system does
not have: a lawful basis, notice before recording starts, a retention period
after which the data must actually be gone, and an answer to a data subject who
asks what you hold about them.

So the schema is built around deletion rather than preservation, which is the
opposite of the wallet ledger sitting next to it:

  * `retention_until` is NOT NULL. There is no way to store a recording
    without saying when it dies. "Keep it forever" is not a lawful position
    and is therefore not a representable state.
  * Deletion is real. The audio leaves object storage and `storage_key` is
    cleared, so the row cannot be used to find the file again.
  * The ROW survives deletion, marked DELETED. An auditor needs to see that a
    recording existed and was destroyed on schedule; a vanished row proves
    nothing either way.
  * `announced` records whether the notice was actually played. A recording
    made without it is evidence of a compliance failure, and hiding that would
    defeat the purpose of recording it.

WHO LISTENED IS ITSELF PERSONAL DATA
------------------------------------
call_recording_access is append-only, enforced by a trigger. Every playback,
download and deletion is written there with the actor and their address. If a
customer ever asks who has heard their call, that table is the only honest
answer -- and it must not be editable by the people it names.
"""
from alembic import op
import sqlalchemy as sa

revision = 'w2345678901v'
down_revision = 'v1234567890u'
branch_labels = None
depends_on = None


def upgrade():
    for ddl in (
        # Was recording asked for when the call was placed? Kept on the call
        # itself so the intent survives even if the audio never arrives.
        "ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS "
        "recording_requested BOOLEAN NOT NULL DEFAULT FALSE",
        # Did the notice actually play? See the module docstring.
        "ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS "
        "recording_announced BOOLEAN NOT NULL DEFAULT FALSE",
    ):
        op.execute(ddl)

    op.execute("""
        CREATE TABLE IF NOT EXISTS call_recordings (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            -- One recording per call. A second row for the same call would
            -- mean one of them is unaccounted for at deletion time.
            call_id UUID NOT NULL UNIQUE REFERENCES call_logs(id),

            provider VARCHAR(32),
            -- Where the provider kept it. Retained only until we have our own
            -- copy: their retention policy is not ours.
            provider_url TEXT,

            storage_bucket VARCHAR(128),
            -- Cleared on deletion, so a DELETED row cannot be used to find
            -- the audio again.
            storage_key VARCHAR(512),
            content_type VARCHAR(100),
            byte_size BIGINT,
            duration_seconds INTEGER,

            -- PENDING  provider reported a recording; we have not stored it
            -- STORED   our copy is in object storage
            -- FAILED   we could not retrieve or store it
            -- DELETED  destroyed, on schedule or on request
            status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
            failure_reason VARCHAR(255),

            -- Whether the spoken notice played before the call connected.
            announced BOOLEAN NOT NULL DEFAULT FALSE,

            -- NOT NULL on purpose: there is no way to keep a recording
            -- without committing to when it will be destroyed.
            retention_until DATE NOT NULL,
            deleted_at TIMESTAMPTZ,
            deleted_by UUID REFERENCES users(id),
            delete_reason VARCHAR(255),

            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT ck_rec_status CHECK (status IN
                ('PENDING','STORED','FAILED','DELETED')),
            CONSTRAINT ck_rec_size CHECK (byte_size IS NULL OR byte_size >= 0),
            -- A stored recording must say where it is; a deleted one must not.
            CONSTRAINT ck_rec_stored_has_key CHECK (
                status <> 'STORED' OR storage_key IS NOT NULL),
            CONSTRAINT ck_rec_deleted_has_no_key CHECK (
                status <> 'DELETED'
                OR (storage_key IS NULL AND provider_url IS NULL
                    AND deleted_at IS NOT NULL))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_rec_call "
               "ON call_recordings (call_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_rec_status "
               "ON call_recordings (status)")
    # The index the retention sweep runs on. Partial, because a DELETED
    # recording is never swept again.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_rec_retention
            ON call_recordings (retention_until)
         WHERE status IN ('PENDING','STORED','FAILED')
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS call_recording_access (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            recording_id UUID REFERENCES call_recordings(id),
            call_id UUID REFERENCES call_logs(id),
            user_id UUID REFERENCES users(id),
            actor_label VARCHAR(255),
            -- PLAY | DOWNLOAD | DELETE | SWEEP | DENIED
            action VARCHAR(16) NOT NULL,
            ip_address VARCHAR(64),
            user_agent VARCHAR(500),
            note VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_rec_access_action CHECK (action IN
                ('PLAY','DOWNLOAD','DELETE','SWEEP','DENIED'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_rec_access_rec "
               "ON call_recording_access (recording_id, created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_rec_access_user "
               "ON call_recording_access (user_id, created_at DESC)")

    # ---- who listened cannot be edited by the people it names -----------
    op.execute("""
        CREATE OR REPLACE FUNCTION call_recording_access_immutable()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION USING MESSAGE =
                'Recording access is append-only. If a customer asks who has '
                'heard their call, this table is the answer, and it must not '
                'be editable by the people it names.';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_rec_access_immutable "
               "ON call_recording_access")
    op.execute("""
        CREATE TRIGGER trg_rec_access_immutable
        BEFORE UPDATE OR DELETE ON call_recording_access
        FOR EACH ROW EXECUTE FUNCTION call_recording_access_immutable()
    """)

    # ---- a recording row outlives its audio -----------------------------
    op.execute("""
        CREATE OR REPLACE FUNCTION call_recording_guard()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION USING MESSAGE =
                    'A recording row is not deletable. Destroy the audio and '
                    'mark it DELETED -- the row is the proof it was destroyed '
                    'on schedule.';
            END IF;
            IF NEW.call_id <> OLD.call_id THEN
                RAISE EXCEPTION USING MESSAGE =
                    'A recording cannot be moved to a different call.';
            END IF;
            -- Retention may be shortened (an erasure request), never extended.
            -- Otherwise the commitment made when the audio was captured could
            -- be quietly rewritten later.
            IF NEW.retention_until > OLD.retention_until THEN
                RAISE EXCEPTION USING MESSAGE =
                    'Retention can be shortened but never extended; the '
                    'period was fixed when the recording was made.';
            END IF;
            IF OLD.status = 'DELETED' AND NEW.status <> 'DELETED' THEN
                RAISE EXCEPTION USING MESSAGE =
                    'A destroyed recording cannot be resurrected.';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_call_recordings_guard "
               "ON call_recordings")
    op.execute("""
        CREATE TRIGGER trg_call_recordings_guard
        BEFORE UPDATE OR DELETE ON call_recordings
        FOR EACH ROW EXECUTE FUNCTION call_recording_guard()
    """)


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS trg_rec_access_immutable "
               "ON call_recording_access")
    op.execute("DROP TRIGGER IF EXISTS trg_call_recordings_guard "
               "ON call_recordings")
    op.execute("DROP FUNCTION IF EXISTS call_recording_access_immutable()")
    op.execute("DROP FUNCTION IF EXISTS call_recording_guard()")
    op.execute("DROP TABLE IF EXISTS call_recording_access CASCADE")
    op.execute("DROP TABLE IF EXISTS call_recordings CASCADE")
    for col in ("recording_requested", "recording_announced"):
        op.execute(f"ALTER TABLE call_logs DROP COLUMN IF EXISTS {col}")
