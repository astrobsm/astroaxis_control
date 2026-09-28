"""Meetings: who gets in, as what, and what is written down afterwards.

WHAT THESE TESTS ARE FOR
========================
The feature they cover replaced a video conference that mounted the PUBLIC
meet.jit.si in an iframe with a room called "AstroBSM_" plus whatever the user
typed. Anyone who guessed "AstroBSM_weekly-standup" was in the management
meeting. Nothing was recorded: no schedule, no attendance, no invitation.

So the tests that matter here are not the happy path. They are the ones that
fail if the room becomes guessable again, if a guest pass turns into a session,
or if somebody who was not in a meeting can read what happened in it.
"""
import importlib.util
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.services import meetings as svc

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")
SYNC_DB = (TEST_DB or "").replace("+asyncpg", "")

# Dropped in dependency order, then rebuilt. Only `users` is hand-written --
# everything else this module touches comes from the migration under test, so
# the test exercises the real DDL rather than a convenient imitation of it.
DROP_FIRST = (
    "meeting_audit_logs", "meeting_attendance", "meeting_waiting_room",
    "meeting_participants", "meetings", "users",
)

USERS_TABLE = """
    CREATE TABLE users (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        email VARCHAR(255) UNIQUE NOT NULL,
        full_name VARCHAR(255) NOT NULL,
        hashed_password VARCHAR(255) NOT NULL DEFAULT 'x',
        role VARCHAR(50) NOT NULL DEFAULT 'admin',
        is_active BOOLEAN DEFAULT TRUE,
        is_locked BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
"""

MIGRATIONS = ["k6789012345j_meetings.py"]


def _apply():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = create_engine(SYNC_DB, future=True)
    for filename in MIGRATIONS:
        path = (Path(__file__).resolve().parents[1] / "alembic" / "versions"
                / filename)
        spec = importlib.util.spec_from_file_location(
            f"m_{uuid.uuid4().hex[:6]}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with engine.begin() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                mod.upgrade()
    engine.dispose()


@pytest.fixture(scope="module")
def schema():
    engine = create_engine(SYNC_DB, future=True)
    with engine.begin() as conn:
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        for table in DROP_FIRST:
            conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
        conn.execute(text(USERS_TABLE))
    engine.dispose()
    _apply()
    yield


@pytest_asyncio.fixture
async def db(schema, monkeypatch):
    # Guest access needs a signing key. Set here so the suite does not depend
    # on the developer's environment carrying one.
    os.environ.setdefault("MEETING_GUEST_SECRET", "test-guest-secret-not-real")
    engine = create_async_engine(TEST_DB, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


class FakeUser:
    def __init__(self, user_id, full_name="Staff Member", email=None,
                 role="admin"):
        self.id = user_id
        self.full_name = full_name
        self.email = email or f"{user_id}@test.test"
        self.role = role


async def _user(db, name="Dr Emmanuel"):
    uid = uuid.uuid4()
    await db.execute(
        text("INSERT INTO users (id, email, full_name, role) "
             "VALUES (:i, :e, :n, 'admin')"),
        {"i": str(uid), "e": f"{uid}@test.test", "n": name})
    await db.commit()
    return FakeUser(uid, full_name=name, email=f"{uid}@test.test")


async def _meeting(db, host, **kw):
    result = await svc.create_meeting(
        db, title=kw.pop("title", "Weekly Production Meeting"),
        scheduled_start=kw.pop(
            "scheduled_start", datetime.now(timezone.utc) + timedelta(hours=1)),
        host_user_id=host.id, actor=host, **kw)
    await db.commit()
    return result, result["join_url"].rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------
# The room is not guessable
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_room_name_owes_nothing_to_the_title(db):
    """The bug this whole module exists to fix.

    The old implementation used "AstroBSM_" + whatever the user typed, on the
    public Jitsi server. Anyone who knew the company held a weekly standup
    could walk into it.
    """
    host = await _user(db)
    await _meeting(db, host, title="Weekly Production Meeting")

    room = (await db.execute(
        text("SELECT room_name FROM meetings ORDER BY created_at DESC LIMIT 1")
    )).scalar()

    lowered = room.lower()
    for word in ("weekly", "production", "meeting", "astrobsm", "bsm-"):
        assert word not in lowered.replace("bsm", "", 1), (
            f"the room name leaks {word!r}: {room}")
    assert len(room) >= 20, "a short room name is a guessable room name"


@pytest.mark.asyncio
async def test_two_meetings_never_share_a_room(db):
    host = await _user(db)
    rooms = set()
    for i in range(5):
        await _meeting(db, host, title="Standup")
        rooms.add((await db.execute(text(
            "SELECT room_name FROM meetings ORDER BY created_at DESC LIMIT 1"
        ))).scalar())
    assert len(rooms) == 5


@pytest.mark.asyncio
async def test_the_room_never_reaches_a_browser_that_was_not_admitted(db):
    """The room is the credential on the public server, so it is handed over
    only after the server has decided this person may be in the meeting.
    """
    host = await _user(db)
    created, token = await _meeting(db, host, waiting_room=True)

    opened = await svc.resolve_link(db, token=token)
    # What the landing page is allowed to show.
    public = svc._public_meeting(opened)
    assert "room" not in public and "room_name" not in public

    waiting = await svc.guest_join(db, token=token, display_name="Supplier Rep")
    await db.commit()
    assert waiting["status"] == "WAITING"
    assert "conference" not in waiting, (
        "somebody still in the lobby must not be given the room")


# ---------------------------------------------------------------------------
# A guest pass is not a session
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_guest_pass_is_refused_by_the_user_guard(db):
    """The single most important test in this file.

    If a guest pass were ever accepted as a user session, an outsider holding
    a meeting link would be inside the ERP.
    """
    from app.api.auth import decode_token

    host = await _user(db)
    created, token = await _meeting(db, host, waiting_room=False)
    seat = await svc.guest_join(db, token=token, display_name="Outside Guest")
    await db.commit()

    pass_token = seat["guest_pass"]

    # It cannot even be decoded by the user-session machinery: different key.
    with pytest.raises(HTTPException) as exc:
        decode_token(pass_token)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_the_user_guard_refuses_a_guest_pass_even_if_the_keys_matched(db):
    """Defence in depth, and it is tested rather than asserted in a comment.

    A deployment that set MEETING_GUEST_SECRET to the same value as the
    session key would make the signature check pass. The typ claim is what
    still refuses it.
    """
    from jose import jwt as jose_jwt
    from app.api import auth as auth_mod

    forged = jose_jwt.encode(
        {"typ": "meeting_guest", "sub": str(uuid.uuid4()),
         "exp": int((datetime.now(timezone.utc)
                     + timedelta(hours=1)).timestamp())},
        auth_mod.SECRET_KEY, algorithm=auth_mod.ALGORITHM)

    payload = auth_mod.decode_token(forged)      # signature is valid here
    assert payload["typ"] == "meeting_guest"

    # ...and the guard still refuses it.
    import inspect
    source = inspect.getsource(auth_mod.require_authenticated_user)
    assert 'typ") == "meeting_guest"' in source, (
        "require_authenticated_user must reject a meeting pass explicitly")


@pytest.mark.asyncio
async def test_a_guest_pass_names_one_meeting_and_carries_no_privileges(db):
    host = await _user(db)
    created, token = await _meeting(
        db, host, waiting_room=False, guest_screen_share=False, guest_chat=True)
    seat = await svc.guest_join(db, token=token, display_name="Guest")
    await db.commit()

    claims = svc.decode_guest_token(seat["guest_pass"])
    assert claims["meeting_id"] == created["id"]
    assert claims["screen"] is False, "host disallowed guest screen sharing"
    assert claims["chat"] is True
    assert "role" not in claims and "sub" not in claims


# ---------------------------------------------------------------------------
# The link: expiry, revocation, regeneration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_revoked_link_stops_working_at_once(db):
    host = await _user(db)
    created, token = await _meeting(db, host)
    await svc.revoke_link(db, meeting_id=uuid.UUID(created["id"]),
                          reason="Sent to the wrong supplier.", actor=host)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.resolve_link(db, token=token)
    assert exc.value.status_code == 403
    assert "withdrawn" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_regenerating_invalidates_the_old_link(db):
    host = await _user(db)
    created, old_token = await _meeting(db, host)
    fresh = await svc.regenerate_link(
        db, meeting_id=uuid.UUID(created["id"]), actor=host)
    await db.commit()

    new_token = fresh["join_url"].rsplit("/", 1)[-1]
    assert new_token != old_token

    with pytest.raises(HTTPException):
        await svc.resolve_link(db, token=old_token)
    assert await svc.resolve_link(db, token=new_token) is not None


@pytest.mark.asyncio
async def test_an_expired_link_is_refused(db):
    host = await _user(db)
    created, token = await _meeting(db, host)
    await db.execute(
        text("UPDATE meetings SET link_expires_at = NOW() - INTERVAL '1 day' "
             "WHERE id = :m"), {"m": created["id"]})
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.resolve_link(db, token=token)
    assert exc.value.status_code == 403
    assert "expired" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_a_guessed_token_says_nothing_about_what_exists(db):
    """Probing must not tell an attacker which meetings are real."""
    host = await _user(db)
    await _meeting(db, host)

    with pytest.raises(HTTPException) as exc:
        await svc.resolve_link(db, token="a" * 40)
    assert exc.value.status_code == 404
    assert exc.value.detail == "This meeting link is not valid."


# ---------------------------------------------------------------------------
# Waiting room, locks, capacity
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_waiting_room_holds_a_guest_until_the_host_decides(db):
    host = await _user(db)
    created, token = await _meeting(db, host, waiting_room=True)
    meeting_id = uuid.UUID(created["id"])

    waiting = await svc.guest_join(db, token=token, display_name="Mary Guest")
    await db.commit()
    assert waiting["status"] == "WAITING"

    status = await svc.guest_waiting_status(
        db, token=token, waiting_id=uuid.UUID(waiting["waiting_id"]))
    assert status["status"] == "WAITING"

    await svc.decide_waiting(db, meeting_id=meeting_id,
                             waiting_id=uuid.UUID(waiting["waiting_id"]),
                             admit=True, actor=host)
    await db.commit()

    admitted = await svc.guest_waiting_status(
        db, token=token, waiting_id=uuid.UUID(waiting["waiting_id"]))
    await db.commit()
    assert admitted["status"] == "ADMITTED"
    assert admitted["conference"]["room"], "now they get the room"


@pytest.mark.asyncio
async def test_a_rejected_guest_gets_no_room(db):
    host = await _user(db)
    created, token = await _meeting(db, host, waiting_room=True)
    waiting = await svc.guest_join(db, token=token, display_name="Not Invited")
    await db.commit()

    await svc.decide_waiting(
        db, meeting_id=uuid.UUID(created["id"]),
        waiting_id=uuid.UUID(waiting["waiting_id"]), admit=False, actor=host)
    await db.commit()

    result = await svc.guest_waiting_status(
        db, token=token, waiting_id=uuid.UUID(waiting["waiting_id"]))
    assert result["status"] == "REJECTED"
    assert "conference" not in result


@pytest.mark.asyncio
async def test_a_locked_meeting_refuses_new_guests(db):
    host = await _user(db)
    created, token = await _meeting(db, host, waiting_room=False)
    await svc.set_lock(db, meeting_id=uuid.UUID(created["id"]), locked=True,
                       actor=host)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.guest_join(db, token=token, display_name="Late Arrival")
    assert exc.value.status_code == 403
    assert "locked" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_a_full_meeting_refuses_one_more(db):
    host = await _user(db)
    created, token = await _meeting(db, host, waiting_room=False,
                                    max_participants=2)
    await svc.guest_join(db, token=token, display_name="Guest One")
    await svc.guest_join(db, token=token, display_name="Guest Two")
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.guest_join(db, token=token, display_name="Guest Three")
    assert exc.value.status_code == 403
    assert "full" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_a_passcode_is_required_when_the_host_set_one(db):
    host = await _user(db)
    created, token = await _meeting(db, host, waiting_room=False,
                                    passcode="Sup3rSecret")

    with pytest.raises(HTTPException) as exc:
        await svc.guest_join(db, token=token, display_name="Guest")
    assert exc.value.status_code == 403

    with pytest.raises(HTTPException):
        await svc.guest_join(db, token=token, display_name="Guest",
                             passcode="wrong")

    ok = await svc.guest_join(db, token=token, display_name="Guest",
                              passcode="Sup3rSecret")
    await db.commit()
    assert ok["status"] == "ADMITTED"


@pytest.mark.asyncio
async def test_guest_access_can_be_switched_off_entirely(db):
    host = await _user(db)
    created, token = await _meeting(db, host, guest_access_enabled=False)
    with pytest.raises(HTTPException) as exc:
        await svc.resolve_link(db, token=token)
    assert exc.value.status_code == 403
    assert "staff only" in exc.value.detail.lower()


# ---------------------------------------------------------------------------
# Host authority
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_only_a_host_or_co_host_may_control_a_meeting(db):
    host = await _user(db, "Host")
    other = await _user(db, "Somebody Else")
    created, _ = await _meeting(db, host)
    meeting_id = uuid.UUID(created["id"])

    assert await svc.require_host(db, meeting_id=meeting_id, user=host) == "HOST"

    with pytest.raises(HTTPException) as exc:
        await svc.require_host(db, meeting_id=meeting_id, user=other)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_a_co_host_may_control_the_meeting(db):
    host = await _user(db, "Host")
    deputy = await _user(db, "Deputy")
    created, _ = await _meeting(
        db, host, participants=[{"user_id": str(deputy.id), "role": "CO_HOST"}])
    role = await svc.require_host(
        db, meeting_id=uuid.UUID(created["id"]), user=deputy)
    assert role == "CO_HOST"


@pytest.mark.asyncio
async def test_a_meeting_is_private_to_the_people_in_it(db):
    """Not role-gated. An administrator is not entitled to read what the sales
    team discussed with a supplier merely for being an administrator.
    """
    host = await _user(db, "Host")
    outsider = await _user(db, "Uninvolved Admin")
    created, _ = await _meeting(db, host)

    with pytest.raises(HTTPException) as exc:
        await svc.meeting_detail(
            db, meeting_id=uuid.UUID(created["id"]), user=outsider)
    assert exc.value.status_code == 404, (
        "an outsider should not even learn that the meeting exists")

    mine = await svc.list_meetings(db, user=outsider)
    assert all(m["id"] != created["id"] for m in mine)


@pytest.mark.asyncio
async def test_the_host_cannot_be_uninvited_from_their_own_meeting(db):
    host = await _user(db)
    created, _ = await _meeting(db, host)
    removed = await db.execute(
        text("""DELETE FROM meeting_participants
                 WHERE meeting_id = :m AND user_id = :u AND role <> 'HOST'"""),
        {"m": created["id"], "u": str(host.id)})
    assert (removed.rowcount or 0) == 0


# ---------------------------------------------------------------------------
# Attendance
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_attendance_records_who_came_and_for_how_long(db):
    host = await _user(db, "Host")
    created, token = await _meeting(db, host, waiting_room=False)
    meeting_id = uuid.UUID(created["id"])

    joined = await svc.internal_join(db, meeting_id=meeting_id, user=host)
    guest = await svc.guest_join(db, token=token, display_name="Mary Guest")
    await db.commit()

    await svc.leave(db, attendance_id=uuid.UUID(joined["attendance_id"]))
    await db.commit()

    rows = (await db.execute(
        text("""SELECT display_name, is_guest, role, left_at, duration_seconds
                  FROM meeting_attendance WHERE meeting_id = :m
                 ORDER BY joined_at"""), {"m": created["id"]})).mappings().all()

    # Keyed by role, not by position: both rows are inserted in the same
    # transaction and can share a timestamp, so their order is arbitrary.
    by_role = {r["role"]: r for r in rows}
    assert set(by_role) == {"HOST", "GUEST"}

    staff = by_role["HOST"]
    assert staff["is_guest"] is False
    assert staff["left_at"] is not None
    assert staff["duration_seconds"] is not None

    visitor = by_role["GUEST"]
    assert visitor["is_guest"] is True
    assert visitor["display_name"] == "Mary Guest"
    assert visitor["left_at"] is None, "still in the meeting"


@pytest.mark.asyncio
async def test_ending_a_meeting_closes_everybody_still_shown_as_present(db):
    """A browser that crashed never tells us it left. Ending the meeting is
    the moment we can honestly say everyone stopped attending.
    """
    host = await _user(db)
    created, token = await _meeting(db, host, waiting_room=False)
    meeting_id = uuid.UUID(created["id"])

    await svc.internal_join(db, meeting_id=meeting_id, user=host)
    await svc.guest_join(db, token=token, display_name="Guest")
    await db.commit()

    await svc.set_status(db, meeting_id=meeting_id, status="ENDED", actor=host)
    await db.commit()

    open_rows = (await db.execute(
        text("""SELECT COUNT(*) FROM meeting_attendance
                 WHERE meeting_id = :m AND left_at IS NULL"""),
        {"m": created["id"]})).scalar()
    assert open_rows == 0


@pytest.mark.asyncio
async def test_a_host_joining_starts_the_meeting(db):
    host = await _user(db)
    created, _ = await _meeting(db, host)
    await svc.internal_join(db, meeting_id=uuid.UUID(created["id"]), user=host)
    await db.commit()
    status = (await db.execute(
        text("SELECT status FROM meetings WHERE id = :m"),
        {"m": created["id"]})).scalar()
    assert status == "LIVE"


@pytest.mark.asyncio
async def test_an_ended_meeting_cannot_be_joined(db):
    host = await _user(db)
    created, token = await _meeting(db, host, waiting_room=False)
    await svc.set_status(db, meeting_id=uuid.UUID(created["id"]),
                         status="ENDED", actor=host)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.guest_join(db, token=token, display_name="Too Late")
    assert exc.value.status_code == 403
    assert "ended" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_cancelling_demands_a_reason(db):
    host = await _user(db)
    created, _ = await _meeting(db, host)
    with pytest.raises(HTTPException) as exc:
        await svc.set_status(db, meeting_id=uuid.UUID(created["id"]),
                             status="CANCELLED", actor=host)
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# The trail
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_letting_an_outsider_in_is_recorded(db):
    host = await _user(db)
    created, token = await _meeting(db, host, waiting_room=True)
    meeting_id = uuid.UUID(created["id"])
    waiting = await svc.guest_join(db, token=token, display_name="Supplier")
    await db.commit()
    await svc.decide_waiting(db, meeting_id=meeting_id,
                             waiting_id=uuid.UUID(waiting["waiting_id"]),
                             admit=True, actor=host)
    await db.commit()

    events = [r[0] for r in (await db.execute(
        text("""SELECT event_type FROM meeting_audit_logs
                 WHERE meeting_id = :m ORDER BY created_at"""),
        {"m": created["id"]})).all()]
    assert "MEETING_CREATED" in events
    assert "GUEST_JOIN_REQUEST" in events
    assert "GUEST_ADMITTED" in events


@pytest.mark.asyncio
async def test_the_meeting_trail_cannot_be_rewritten(db):
    """A convention that a log is not edited is worth nothing next to a
    trigger that refuses. This table is what somebody reads to find out who
    let an outsider into a meeting.
    """
    host = await _user(db)
    created, _ = await _meeting(db, host)

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("""UPDATE meeting_audit_logs SET event_type = 'NOTHING'
                     WHERE meeting_id = :m"""), {"m": created["id"]})
    assert "append-only" in str(exc.value)
    await db.rollback()

    with pytest.raises(Exception):
        await db.execute(
            text("DELETE FROM meeting_audit_logs WHERE meeting_id = :m"),
            {"m": created["id"]})
    await db.rollback()


# ---------------------------------------------------------------------------
# What a guest is told
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_landing_page_tells_a_guest_nothing_about_the_company(db):
    """An invitation gets forwarded. Everything it carries is public."""
    host = await _user(db, "Dr Emmanuel")
    created, token = await _meeting(
        db, host, title="Weekly Production Meeting",
        description="Discuss the Ozioko account, margins and the Q4 layoffs.")

    meeting = await svc.resolve_link(db, token=token)
    public = svc._public_meeting(meeting)

    assert public["title"] == "Weekly Production Meeting"
    assert public["host"] == "Dr Emmanuel"
    blob = repr(public)
    assert "Ozioko" not in blob and "layoffs" not in blob, (
        "the agenda is internal and must not travel with the invitation")
    assert "room_name" not in public
    assert meeting["room_name"] not in blob, (
        "the room is the credential on the public server; the landing page "
        "must not carry it")


@pytest.mark.asyncio
async def test_the_invitation_carries_the_link_and_not_the_agenda(db):
    host = await _user(db, "Dr Emmanuel")
    created, token = await _meeting(
        db, host, title="Supplier Review",
        description="Internal: our walk-away price is 4.2m.")
    detail = await svc.meeting_detail(
        db, meeting_id=uuid.UUID(created["id"]), user=host)

    text_out = svc.invitation_text(detail, created["join_url"])
    assert created["join_url"] in text_out
    assert "Supplier Review" in text_out
    assert "Dr Emmanuel" in text_out
    assert "walk-away" not in text_out
    assert "No account or download is needed" in text_out


# ---------------------------------------------------------------------------
# Posture
# ---------------------------------------------------------------------------

def test_without_jaas_no_token_is_minted_rather_than_a_rejected_one(monkeypatch):
    """Returning a signature the media server would reject is worse than
    returning none: it fails as somebody tries to join.
    """
    for key in ("JAAS_APP_ID", "JAAS_API_KEY", "JAAS_PRIVATE_KEY"):
        monkeypatch.delenv(key, raising=False)
    assert svc.jaas_configured() is False
    assert svc.jitsi_token(room="r", display_name="X", email=None,
                           moderator=True) is None
    assert svc.jitsi_domain() == "meet.jit.si"


def test_a_half_configured_tenant_counts_as_not_configured(monkeypatch):
    monkeypatch.setenv("JAAS_APP_ID", "vpaas-magic-cookie-test")
    monkeypatch.delenv("JAAS_API_KEY", raising=False)
    monkeypatch.delenv("JAAS_PRIVATE_KEY", raising=False)
    assert svc.jaas_configured() is False, (
        "half a tenant mints signatures the media server rejects")


def test_the_guest_secret_never_falls_back_to_the_session_key(monkeypatch):
    """Falling back would make a guest pass and a user session
    interchangeable to anything that only checks the signature.
    """
    monkeypatch.delenv("MEETING_GUEST_SECRET", raising=False)
    with pytest.raises(HTTPException) as exc:
        svc.guest_secret()
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_a_host_who_invites_themselves_stays_the_host(db):
    """The first real meeting held with this feature locked its own host out.

    The host ticked their own name in the invite list. create_meeting writes
    the HOST row first, then upserts every invitee -- and the upsert overwrote
    HOST with PARTICIPANT. require_host then refused them, so they could not
    admit the three people waiting outside, and the host panel never rendered
    because the client is told its role by the server.
    """
    host = await _user(db, "Self Inviter")
    created, _ = await _meeting(
        db, host,
        participants=[{"user_id": str(host.id), "role": "PARTICIPANT"}])

    role = await svc.role_for_user(
        db, meeting_id=uuid.UUID(created["id"]), user_id=host.id)
    assert role == "HOST", "the host must not be demoted by their own invite"

    assert await svc.require_host(
        db, meeting_id=uuid.UUID(created["id"]), user=host) == "HOST"


@pytest.mark.asyncio
async def test_a_host_cannot_be_demoted_by_a_later_invitation_either(db):
    """Same rule on the add-participant path, not only at creation."""
    host = await _user(db, "Host")
    created, _ = await _meeting(db, host)
    meeting_id = uuid.UUID(created["id"])

    await db.execute(
        text("""INSERT INTO meeting_participants
                    (id, meeting_id, user_id, role, invited_by)
                VALUES (gen_random_uuid(), :m, :u, 'PARTICIPANT', :by)
                ON CONFLICT (meeting_id, user_id) DO UPDATE
                   SET role = EXCLUDED.role
                 WHERE meeting_participants.role <> 'HOST'"""),
        {"m": str(meeting_id), "u": str(host.id), "by": str(host.id)})
    await db.commit()

    assert await svc.role_for_user(
        db, meeting_id=meeting_id, user_id=host.id) == "HOST"


@pytest.mark.asyncio
async def test_the_host_joining_is_told_they_are_the_host(db):
    """The client draws the host panel from this. Wrong here, no panel there."""
    host = await _user(db, "Host")
    created, _ = await _meeting(
        db, host,
        participants=[{"user_id": str(host.id), "role": "PARTICIPANT"}])

    seat = await svc.internal_join(
        db, meeting_id=uuid.UUID(created["id"]), user=host)
    await db.commit()

    assert seat["role"] == "HOST"
    assert seat["conference"]["moderator"] is True
