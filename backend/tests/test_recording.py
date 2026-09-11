"""Call recording: prove the promises that make it lawful to do at all.

Recording customers' voices is only defensible if the safeguards are real. The
tests here are therefore about constraint, not capability:

  * recording stays OFF unless explicitly switched on AND storage is set up --
    turning on bridging must never start recording as a side effect;
  * a recording cannot exist without a date it will be destroyed;
  * deletion actually removes the pointer to the audio, and the row survives
    to prove the destruction happened;
  * a destroyed recording cannot be resurrected;
  * retention can be shortened (an erasure request) but never extended;
  * the access log cannot be edited or deleted by anyone, including the
    administrators it names;
  * the S3 signing is correct, checked against AWS's published test vector.
"""
import importlib
import os
import uuid
from datetime import date, timedelta
from pathlib import Path

import importlib.util
import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")
SYNC_DB = (TEST_DB or "").replace("+asyncpg", "")

BASE_SCHEMA = """
DROP TABLE IF EXISTS call_recording_access CASCADE;
DROP TABLE IF EXISTS call_recordings CASCADE;
DROP TABLE IF EXISTS call_provider_events CASCADE;
DROP TABLE IF EXISTS call_logs CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS users CASCADE;

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    full_name VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL DEFAULT 'sales_staff',
    is_active BOOLEAN DEFAULT TRUE,
    phone VARCHAR(20),
    department VARCHAR(100)
);
CREATE TABLE customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_code VARCHAR(32) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    phone VARCHAR(50),
    is_active BOOLEAN DEFAULT TRUE
);
"""

MIGRATIONS = [
    ("m_calls", "u0123456789t_call_log.py"),
    ("m_tel", "v1234567890u_call_telephony.py"),
    ("m_rec", "w2345678901v_call_recording.py"),
]


def _load(name, filename):
    path = (Path(__file__).resolve().parents[1] / "alembic" / "versions"
            / filename)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def schema():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = create_engine(SYNC_DB, future=True)
    with engine.begin() as conn:
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        for stmt in BASE_SCHEMA.split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
    for name, fn in MIGRATIONS:
        mod = _load(name, fn)
        with engine.begin() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                mod.upgrade()
    engine.dispose()
    yield


@pytest_asyncio.fixture
async def db(schema):
    engine = create_async_engine(TEST_DB, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


def _reload(module_path, **env):
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    mod = importlib.import_module(module_path)
    return importlib.reload(mod)


async def _user(session, role="admin"):
    uid = uuid.uuid4()
    await session.execute(
        text("INSERT INTO users (id, email, full_name, role) "
             "VALUES (:i, :e, 'Admin', :r)"),
        {"i": str(uid), "e": f"{uid}@t.test", "r": role})
    await session.commit()
    return uid


async def _call(session, user_id):
    cid = uuid.uuid4()
    await session.execute(
        # Two constraints shape this fixture, and both caught it when it was
        # wrong: ck_call_completed requires a duration on a completed call,
        # and ck_call_verified_needs_provider refuses VERIFIED without a
        # provider reference. CONFIRMED is the honest label here -- these
        # tests are about recordings, not about how the call was timed.
        text("""INSERT INTO call_logs
                    (id, call_reference, user_id, contact_phone,
                     contact_source, channel, status, duration_seconds,
                     duration_source)
                VALUES (:i, :r, :u, '+2348031234567', 'MANUAL', 'BRIDGE',
                        'COMPLETED', 120, 'CONFIRMED')"""),
        {"i": str(cid), "r": f"CALL-{str(cid)[:8]}", "u": str(user_id)})
    await session.commit()
    return cid


async def _recording(session, call_id, *, status="STORED", key="k/audio.mp3",
                     retention=None):
    rid = uuid.uuid4()
    await session.execute(
        text("""INSERT INTO call_recordings
                    (id, call_id, provider, storage_key, status,
                     retention_until, announced, byte_size)
                VALUES (:i, :c, 'africastalking', :k, :s, :ret, TRUE, 1024)"""),
        {"i": str(rid), "c": str(call_id), "k": key, "s": status,
         "ret": retention or (date.today() + timedelta(days=90))})
    await session.commit()
    return rid


# ---------------------------------------------------------------------------
# Recording is off unless deliberately switched on
# ---------------------------------------------------------------------------

def test_recording_is_off_by_default():
    """Bridging must never start recording as a side effect."""
    r = _reload("app.services.recording", CALL_RECORDING_ENABLED=None)
    ok, reason = r.configured()
    assert ok is False
    assert "switched off" in reason


def test_recording_refuses_without_object_storage():
    """Enabled but with nowhere to put the audio is a misconfiguration."""
    _reload("app.services.objectstore", SPACES_KEY=None, SPACES_SECRET=None,
            SPACES_BUCKET=None, SPACES_ENDPOINT=None)
    r = _reload("app.services.recording", CALL_RECORDING_ENABLED="true")
    ok, reason = r.configured()
    assert ok is False
    assert "object storage" in reason.lower()


def test_an_absurd_retention_period_is_refused():
    _reload("app.services.objectstore", SPACES_KEY="k", SPACES_SECRET="s",
            SPACES_BUCKET="b", SPACES_ENDPOINT="https://nyc3.example.com")
    r = _reload("app.services.recording", CALL_RECORDING_ENABLED="true",
                CALL_RECORDING_RETENTION_DAYS="99999")
    ok, reason = r.configured()
    assert ok is False
    assert "RETENTION_DAYS" in reason


def test_full_configuration_enables_recording():
    _reload("app.services.objectstore", SPACES_KEY="k", SPACES_SECRET="s",
            SPACES_BUCKET="b", SPACES_ENDPOINT="https://nyc3.example.com")
    r = _reload("app.services.recording", CALL_RECORDING_ENABLED="true",
                CALL_RECORDING_RETENTION_DAYS="90")
    ok, reason = r.configured()
    assert ok is True and reason is None
    assert r.retention_date() == date.today() + timedelta(days=90)


# ---------------------------------------------------------------------------
# The dial instruction
# ---------------------------------------------------------------------------

def test_the_dial_instruction_does_not_record_unless_asked():
    t = _reload("app.services.telephony", TELEPHONY_PROVIDER="africastalking")
    xml = t.bridge_instruction("+2348031234567")
    assert 'record="false"' in xml
    assert "<Say" not in xml


def test_recording_adds_the_spoken_notice():
    t = _reload("app.services.telephony", TELEPHONY_PROVIDER="africastalking")
    xml = t.bridge_instruction("+2348031234567", record=True,
                               announcement="This call is being recorded.")
    assert 'record="true"' in xml
    assert "<Say" in xml
    assert "This call is being recorded." in xml
    # The notice must come BEFORE the bridge, or it is spoken to nobody.
    assert xml.index("<Say") < xml.index("<Dial")


def test_the_announcement_cannot_break_out_of_the_xml():
    t = _reload("app.services.telephony", TELEPHONY_PROVIDER="africastalking")
    xml = t.bridge_instruction(
        "+2348031234567", record=True,
        announcement='</Say><Dial phoneNumbers="+1999"/><Say>')
    assert xml.count("<Dial") == 1
    assert "&lt;" in xml


def test_the_recording_url_is_read_from_the_completion_callback():
    t = _reload("app.services.telephony", TELEPHONY_PROVIDER="africastalking")
    parsed = t.parse_callback({
        "sessionId": "ATVId_9", "isActive": "0", "durationInSeconds": "90",
        "recordingUrl": "https://provider.example/rec/abc.mp3"})
    assert parsed["recording_url"] == "https://provider.example/rec/abc.mp3"


# ---------------------------------------------------------------------------
# Retention is not optional
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_recording_cannot_exist_without_a_destruction_date(db):
    """'Keep it forever' is not a lawful position, so it is not representable."""
    user = await _user(db)
    call_id = await _call(db, user)
    with pytest.raises(Exception) as exc:
        await db.execute(
            text("""INSERT INTO call_recordings (id, call_id, status)
                    VALUES (gen_random_uuid(), :c, 'PENDING')"""),
            {"c": str(call_id)})
        await db.commit()
    await db.rollback()
    assert "retention_until" in str(exc.value)


@pytest.mark.asyncio
async def test_retention_can_be_shortened_but_never_extended(db):
    """Shortening honours an erasure request. Extending rewrites a promise."""
    user = await _user(db)
    call_id = await _call(db, user)
    rid = await _recording(db, call_id)

    await db.execute(
        text("""UPDATE call_recordings SET retention_until = CURRENT_DATE
                 WHERE id = :i"""), {"i": str(rid)})
    await db.commit()

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("""UPDATE call_recordings
                       SET retention_until = CURRENT_DATE + 365
                     WHERE id = :i"""), {"i": str(rid)})
        await db.commit()
    await db.rollback()
    assert "never extended" in str(exc.value)


@pytest.mark.asyncio
async def test_a_stored_recording_must_say_where_it_is(db):
    user = await _user(db)
    call_id = await _call(db, user)
    with pytest.raises(Exception) as exc:
        await db.execute(
            text("""INSERT INTO call_recordings
                        (id, call_id, status, retention_until, storage_key)
                    VALUES (gen_random_uuid(), :c, 'STORED',
                            CURRENT_DATE + 30, NULL)"""),
            {"c": str(call_id)})
        await db.commit()
    await db.rollback()
    assert "ck_rec_stored_has_key" in str(exc.value)


@pytest.mark.asyncio
async def test_a_deleted_recording_keeps_no_pointer_to_the_audio(db):
    """A DELETED row that still held a key could be used to find the file."""
    user = await _user(db)
    call_id = await _call(db, user)
    rid = await _recording(db, call_id)
    with pytest.raises(Exception) as exc:
        await db.execute(
            text("""UPDATE call_recordings
                       SET status = 'DELETED', deleted_at = NOW()
                     WHERE id = :i"""), {"i": str(rid)})
        await db.commit()
    await db.rollback()
    assert "ck_rec_deleted_has_no_key" in str(exc.value)

    # Done properly it is allowed.
    await db.execute(
        text("""UPDATE call_recordings
                   SET status='DELETED', storage_key=NULL, provider_url=NULL,
                       deleted_at=NOW() WHERE id = :i"""), {"i": str(rid)})
    await db.commit()


@pytest.mark.asyncio
async def test_a_destroyed_recording_cannot_be_resurrected(db):
    user = await _user(db)
    call_id = await _call(db, user)
    rid = await _recording(db, call_id)
    await db.execute(
        text("""UPDATE call_recordings
                   SET status='DELETED', storage_key=NULL, provider_url=NULL,
                       deleted_at=NOW() WHERE id = :i"""), {"i": str(rid)})
    await db.commit()

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("""UPDATE call_recordings SET status='STORED',
                           storage_key='k/again.mp3' WHERE id = :i"""),
            {"i": str(rid)})
        await db.commit()
    await db.rollback()
    assert "resurrected" in str(exc.value)


@pytest.mark.asyncio
async def test_the_row_survives_so_destruction_can_be_proved(db):
    user = await _user(db)
    call_id = await _call(db, user)
    rid = await _recording(db, call_id)
    with pytest.raises(Exception) as exc:
        await db.execute(text("DELETE FROM call_recordings WHERE id = :i"),
                         {"i": str(rid)})
        await db.commit()
    await db.rollback()
    assert "not deletable" in str(exc.value)


# ---------------------------------------------------------------------------
# Who listened
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_access_log_cannot_be_edited_by_the_people_it_names(db):
    user = await _user(db)
    call_id = await _call(db, user)
    rid = await _recording(db, call_id)
    await db.execute(
        text("""INSERT INTO call_recording_access
                    (id, recording_id, call_id, user_id, action)
                VALUES (gen_random_uuid(), :r, :c, :u, 'PLAY')"""),
        {"r": str(rid), "c": str(call_id), "u": str(user)})
    await db.commit()

    for stmt in ("DELETE FROM call_recording_access",
                 "UPDATE call_recording_access SET action = 'SWEEP'"):
        with pytest.raises(Exception) as exc:
            await db.execute(text(stmt))
            await db.commit()
        await db.rollback()
        assert "append-only" in str(exc.value)


@pytest.mark.asyncio
async def test_a_refused_playback_is_recorded_too(db):
    """Who tried to listen matters as much as who succeeded."""
    user = await _user(db, role="sales_staff")
    call_id = await _call(db, user)
    rid = await _recording(db, call_id)
    await db.execute(
        text("""INSERT INTO call_recording_access
                    (id, recording_id, call_id, user_id, action, note)
                VALUES (gen_random_uuid(), :r, :c, :u, 'DENIED',
                        'not an administrator')"""),
        {"r": str(rid), "c": str(call_id), "u": str(user)})
    await db.commit()
    row = (await db.execute(
        text("""SELECT action, note FROM call_recording_access
                 WHERE recording_id = :r"""), {"r": str(rid)})).first()
    assert row.action == "DENIED"


# ---------------------------------------------------------------------------
# The signing that makes storage work at all
# ---------------------------------------------------------------------------

def test_sigv4_signing_key_matches_the_published_aws_vector():
    """If this drifts, every upload and playback silently 403s."""
    o = _reload("app.services.objectstore")
    got = o.signing_key("wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
                        "20150830", "us-east-1", "iam").hex()
    assert got == ("c4afb1cc5771d871763a393e44b703571b55cc28424d1a5e86da6ed3"
                   "c154a4b9")


def test_a_presigned_url_carries_everything_s3_requires():
    o = _reload("app.services.objectstore", SPACES_KEY="AKIAEXAMPLE",
                SPACES_SECRET="secret", SPACES_BUCKET="recordings",
                SPACES_REGION="nyc3",
                SPACES_ENDPOINT="https://nyc3.digitaloceanspaces.com")
    url = o.presigned_get_url("call-recordings/2026/09/CALL-1.mp3")
    for required in ("X-Amz-Algorithm=AWS4-HMAC-SHA256", "X-Amz-Credential=",
                     "X-Amz-Date=", "X-Amz-Expires=300",
                     "X-Amz-SignedHeaders=host", "X-Amz-Signature="):
        assert required in url, required
    assert url.startswith("https://nyc3.digitaloceanspaces.com/recordings/")
    # The secret itself must never appear in a URL handed to a browser.
    assert "secret" not in url


def test_presigned_urls_are_short_lived_and_bounded():
    o = _reload("app.services.objectstore", SPACES_KEY="k", SPACES_SECRET="s",
                SPACES_BUCKET="b", SPACES_REGION="nyc3",
                SPACES_ENDPOINT="https://nyc3.digitaloceanspaces.com")
    with pytest.raises(ValueError):
        o.presigned_get_url("x.mp3", expires_seconds=99999)
    with pytest.raises(ValueError):
        o.presigned_get_url("x.mp3", expires_seconds=0)


def test_signing_changes_when_the_key_changes():
    """A sanity check that the signature actually depends on its inputs."""
    o = _reload("app.services.objectstore", SPACES_KEY="k", SPACES_SECRET="s",
                SPACES_BUCKET="b", SPACES_REGION="nyc3",
                SPACES_ENDPOINT="https://nyc3.digitaloceanspaces.com")
    a = o.presigned_get_url("one.mp3").split("X-Amz-Signature=")[1]
    b = o.presigned_get_url("two.mp3").split("X-Amz-Signature=")[1]
    assert a != b
