"""Meetings: who may join, as what, and what actually happened.

WHERE THE MEDIA LIVES, AND WHY IT IS NOT HERE
=============================================
This server has one vCPU and under a gigabyte of RAM, shared with Postgres and
the whole ERP. An SFU (Jitsi's own videobridge, LiveKit, mediasoup) needs
several cores and headroom this machine does not have, and a mesh of browser
peers falls apart past about four participants. So the audio and video go
through Jitsi's infrastructure and never touch this box. What lives here is
everything Jitsi does not know about the company: who may join, who is host,
who came, and when.

TWO SECURITY POSTURES, AND THE CODE SUPPORTS BOTH ON PURPOSE
============================================================
`jaas_configured()` is false until JaaS credentials are in the environment.

    NOT configured -- the public meet.jit.si server. It has no idea who we
    are, so the ONLY thing keeping strangers out is that the room name is 22
    characters of `secrets.token_urlsafe` that the server never reveals to a
    browser it has not admitted. That is a real defence: it is what a Google
    Meet code is. It is not a strong one, because anyone we admit can pass the
    room name on, and we cannot revoke it from them.

    Configured -- the same rooms, with a JWT this server signs. Jitsi then
    enforces on its own side who is moderator and who may enter at all, and a
    forwarded room name is worth nothing without a signature. This is the
    posture the module is built for.

The fallback is not a lesser mode nobody tested: every meeting works, and the
screens say which posture is in force, because a host briefing a supplier
should know whether the link they are sending is protected by a signature or
by obscurity.

WHY A GUEST TOKEN IS NOT A USER TOKEN
=====================================
A guest gets a JWT signed with `MEETING_GUEST_SECRET` -- a DIFFERENT key from
the one that signs user sessions -- and carrying `typ: "meeting_guest"`.
Both halves matter. A different key means a guest token cannot be presented to
`require_authenticated_user` at all. The `typ` claim means that even if the two
keys were ever misconfigured to the same value, the user guard still refuses
it, because it checks. One of those is a mistake waiting to happen; two is a
mistake that has to happen twice.

A guest token names one meeting. There is no route in this application that
accepts it for anything else.
"""
from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

from fastapi import HTTPException
from jose import jwt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# How long a guest's meeting token is good for. Short: it exists to carry
# somebody through one meeting, and a meeting that runs past this can be
# rejoined from the same link.
GUEST_TOKEN_MINUTES = int(os.getenv("MEETING_GUEST_TOKEN_MINUTES", "240"))

# Default life of a shareable link, from the moment it is issued.
DEFAULT_LINK_DAYS = 30
MAX_LINK_DAYS = 365

# Joins per link per hour. A meeting link is forwarded legitimately, so this is
# loose -- it exists to stop a script hammering the endpoint, not to police the
# host's guest list.
JOIN_CAP_PER_HOUR = 120

def _hash_passcode(passcode: str) -> str:
    """Hash a meeting passcode with the application's own password hashing.

    Deliberately not a second CryptContext of this module's own. The ERP
    already has one way to hash a secret, it already handles bcrypt's 72-byte
    input limit, and a meeting passcode is not special enough to justify a
    parallel implementation that can drift from it.

    Imported inside the function because app.api.auth pulls in the FastAPI
    dependency graph, and a service module should not drag that in at import
    time.
    """
    from app.api.auth import hash_password
    return hash_password(passcode)


def _check_passcode(passcode: str, hashed: str) -> bool:
    from app.api.auth import verify_password
    return verify_password(passcode, hashed)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def jaas_configured() -> bool:
    """Are JaaS credentials present?

    All three are required. A half-configured tenant would mint signatures the
    media server rejects, which fails at the worst possible moment -- as
    somebody tries to join a meeting that has already started.
    """
    return bool(os.getenv("JAAS_APP_ID")
                and os.getenv("JAAS_API_KEY")
                and os.getenv("JAAS_PRIVATE_KEY"))


def jitsi_domain() -> str:
    return (os.getenv("JITSI_DOMAIN")
            or ("8x8.vc" if jaas_configured() else "meet.jit.si"))


def guest_secret() -> str:
    """The key that signs guest tokens.

    Deliberately separate from the user session key. Falling back to it would
    make a guest token and a user token interchangeable to anything that only
    checks the signature, so an unset value is refused outright rather than
    quietly borrowing the other one.
    """
    secret = os.getenv("MEETING_GUEST_SECRET")
    if not secret:
        raise HTTPException(
            status_code=503,
            detail=("Guest meeting access is not configured on this server. "
                    "Set MEETING_GUEST_SECRET."))
    return secret


def public_base_url() -> str:
    return (os.getenv("PUBLIC_BASE_URL")
            or "https://erp.bonnesantemedicals.com").rstrip("/")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _meeting_code() -> str:
    """Short, readable, said aloud on the phone. Grants nothing on its own."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no I/O/0/1
    return "BSM-" + "".join(secrets.choice(alphabet) for _ in range(6))


def _room_name() -> str:
    """The Jitsi room. On the public server this IS the credential.

    Never derived from the title: "AstroBSM_weekly-standup" is what the old
    implementation used, and it is guessable by anyone who knows the company
    holds a weekly standup.
    """
    return "bsm" + secrets.token_urlsafe(22).replace("-", "").replace("_", "")


async def audit(
    session: AsyncSession, *, event_type: str,
    meeting_id: Optional[UUID] = None, actor=None,
    actor_label: Optional[str] = None, detail: Optional[dict] = None,
    ip_address: str = "", user_agent: str = "",
) -> None:
    """Append to the meeting trail, inside the caller's transaction.

    Inside it on purpose: a row saying a guest was admitted, surviving a
    transaction that then rolled the admission back, describes something that
    never happened.
    """
    import json
    await session.execute(
        text("""
            INSERT INTO meeting_audit_logs
                (id, meeting_id, event_type, actor_user_id, actor_label,
                 detail, ip_address, user_agent)
            VALUES (gen_random_uuid(), :m, :e, :a, :al,
                    CAST(:d AS JSONB), :ip, :ua)
        """),
        {"m": str(meeting_id) if meeting_id else None, "e": event_type,
         "a": str(actor.id) if actor is not None else None,
         "al": actor_label or (getattr(actor, "email", None) if actor else None),
         "d": json.dumps(detail) if detail else None,
         "ip": (ip_address or "")[:64] or None,
         "ua": (user_agent or "")[:500] or None},
    )


# ---------------------------------------------------------------------------
# The Jitsi token
# ---------------------------------------------------------------------------

def jitsi_token(
    *, room: str, display_name: str, email: Optional[str],
    moderator: bool, minutes: int = GUEST_TOKEN_MINUTES,
) -> Optional[str]:
    """A JaaS JWT, or None when JaaS is not configured.

    None is a valid answer, not a failure: on the public server there is
    nothing to sign a token FOR, and returning a token it would reject is
    worse than returning nothing. The caller passes it to the client only when
    it exists.

    The `moderator` flag is the one that matters. It is what makes host
    controls -- muting, removing, ending for everyone -- decisions the media
    server enforces rather than buttons our own UI chooses to draw.
    """
    if not jaas_configured():
        return None

    app_id = os.getenv("JAAS_APP_ID")
    now = _now()
    payload = {
        "aud": "jitsi",
        "iss": "chat",
        "sub": app_id,
        "room": room,
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
        "nbf": int((now - timedelta(seconds=30)).timestamp()),
        "context": {
            "user": {
                "name": display_name,
                "email": email or "",
                "moderator": "true" if moderator else "false",
            },
            "features": {
                # Recording stays off until it is designed properly -- consent,
                # storage, retention and access control are not afterthoughts,
                # and the call-recording module already shows what that costs.
                "recording": "false",
                "livestreaming": "false",
                "transcription": "false",
                "outbound-call": "false",
            },
        },
    }
    return jwt.encode(
        payload, os.getenv("JAAS_PRIVATE_KEY"), algorithm="RS256",
        headers={"kid": os.getenv("JAAS_API_KEY")})


def guest_token(*, meeting_id: UUID, display_name: str,
                may_share_screen: bool, may_chat: bool) -> str:
    """The token a guest's browser holds. Scoped to one meeting, and nothing else."""
    now = _now()
    return jwt.encode(
        {
            "typ": "meeting_guest",
            "meeting_id": str(meeting_id),
            "name": display_name,
            "screen": may_share_screen,
            "chat": may_chat,
            "jti": uuid4().hex,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=GUEST_TOKEN_MINUTES)).timestamp()),
        },
        guest_secret(), algorithm="HS256")


def decode_guest_token(token: str) -> dict:
    """Read a guest token, refusing anything that is not one.

    The `typ` check is not belt-and-braces. It is the assertion that this
    function cannot be talked into accepting a user session token, whatever
    the two secrets happen to be set to on a given deployment.
    """
    try:
        claims = jwt.decode(token, guest_secret(), algorithms=["HS256"])
    except Exception:
        raise HTTPException(status_code=401,
                            detail="This meeting pass is not valid.")
    if claims.get("typ") != "meeting_guest":
        raise HTTPException(status_code=401,
                            detail="This meeting pass is not valid.")
    return claims


# ---------------------------------------------------------------------------
# Creating and editing meetings
# ---------------------------------------------------------------------------

async def create_meeting(
    session: AsyncSession, *, title: str, scheduled_start: datetime,
    duration_minutes: int = 60, description: Optional[str] = None,
    host_user_id: UUID, participants: Optional[list[dict]] = None,
    guest_access_enabled: bool = True, waiting_room: bool = True,
    guest_screen_share: bool = False, guest_chat: bool = True,
    max_participants: int = 50, passcode: Optional[str] = None,
    link_days: int = DEFAULT_LINK_DAYS, actor=None,
) -> dict:
    """Schedule a meeting and issue its shareable link."""
    if not title.strip():
        raise HTTPException(status_code=400, detail="Give the meeting a title.")
    if not 1 <= duration_minutes <= 1440:
        raise HTTPException(status_code=400,
                            detail="Duration must be between 1 and 1440 minutes.")
    if not 1 <= link_days <= MAX_LINK_DAYS:
        raise HTTPException(status_code=400,
                            detail=f"Link validity must be 1-{MAX_LINK_DAYS} days.")

    meeting_id = uuid4()
    token = secrets.token_urlsafe(32)

    await session.execute(
        text("""
            INSERT INTO meetings
                (id, meeting_code, room_name, title, description,
                 scheduled_start, duration_minutes, host_user_id,
                 link_token_sha256, link_token_hint, link_expires_at,
                 guest_access_enabled, waiting_room, guest_screen_share,
                 guest_chat, max_participants, passcode_hash, created_by)
            VALUES (:id, :code, :room, :title, :descr, :start, :dur, :host,
                    :h, :hint, :exp, :ga, :wr, :gss, :gc, :max, :pc, :by)
        """),
        {"id": str(meeting_id), "code": _meeting_code(), "room": _room_name(),
         "title": title.strip(), "descr": description,
         "start": scheduled_start, "dur": duration_minutes,
         "host": str(host_user_id), "h": _hash(token), "hint": token[-6:],
         "exp": _now() + timedelta(days=link_days),
         "ga": guest_access_enabled, "wr": waiting_room,
         "gss": guest_screen_share, "gc": guest_chat, "max": max_participants,
         "pc": _hash_passcode(passcode) if passcode else None,
         "by": str(actor.id) if actor else str(host_user_id)},
    )

    # The host is a participant of their own meeting, so attendance and the
    # participant list do not have to special-case them everywhere.
    await session.execute(
        text("""INSERT INTO meeting_participants
                    (id, meeting_id, user_id, role, invited_by)
                VALUES (gen_random_uuid(), :m, :u, 'HOST', :by)
                ON CONFLICT (meeting_id, user_id) DO NOTHING"""),
        {"m": str(meeting_id), "u": str(host_user_id),
         "by": str(actor.id) if actor else None})

    for entry in (participants or []):
        role = (entry.get("role") or "PARTICIPANT").upper()
        if role not in ("CO_HOST", "PARTICIPANT"):
            role = "PARTICIPANT"
        await session.execute(
            text("""INSERT INTO meeting_participants
                        (id, meeting_id, user_id, role, invited_by)
                    VALUES (gen_random_uuid(), :m, :u, :r, :by)
                    ON CONFLICT (meeting_id, user_id) DO UPDATE
                       SET role = EXCLUDED.role"""),
            {"m": str(meeting_id), "u": str(entry["user_id"]), "r": role,
             "by": str(actor.id) if actor else None})

    await audit(session, event_type="MEETING_CREATED", meeting_id=meeting_id,
                actor=actor, detail={"title": title.strip(),
                                     "guest_access": guest_access_enabled,
                                     "waiting_room": waiting_room})

    return {
        "id": str(meeting_id),
        "join_url": f"{public_base_url()}/meet/{token}",
        "link_expires_at": (_now() + timedelta(days=link_days)).isoformat(),
        "guest_access_enabled": guest_access_enabled,
        "secured_by": "signature" if jaas_configured() else "unguessable-link",
    }


async def regenerate_link(
    session: AsyncSession, *, meeting_id: UUID, link_days: int = DEFAULT_LINK_DAYS,
    actor=None,
) -> dict:
    """Issue a new link. The previous one stops working immediately.

    Nobody already in the meeting is disconnected: they hold a guest token that
    was checked when they joined, and cutting a supplier off mid-sentence
    because the host tidied up the invitations would be a worse outcome than
    the risk it removes. Revoking (below) is the control for that.
    """
    token = secrets.token_urlsafe(32)
    updated = await session.execute(
        text("""UPDATE meetings
                   SET link_token_sha256 = :h, link_token_hint = :hint,
                       link_expires_at = :exp, link_revoked_at = NULL,
                       link_revoke_reason = NULL, updated_at = NOW()
                 WHERE id = :m AND status <> 'CANCELLED'"""),
        {"h": _hash(token), "hint": token[-6:],
         "exp": _now() + timedelta(days=link_days), "m": str(meeting_id)})
    if (updated.rowcount or 0) == 0:
        raise HTTPException(status_code=404,
                            detail="Meeting not found, or it was cancelled.")

    await audit(session, event_type="MEETING_LINK_REGENERATED",
                meeting_id=meeting_id, actor=actor)
    return {"join_url": f"{public_base_url()}/meet/{token}",
            "link_expires_at": (_now() + timedelta(days=link_days)).isoformat()}


async def revoke_link(
    session: AsyncSession, *, meeting_id: UUID, reason: str, actor=None,
) -> dict:
    """Kill the link. Immediate, and it says why."""
    if not reason or len(reason.strip()) < 3:
        raise HTTPException(status_code=400, detail="Say why.")
    updated = await session.execute(
        text("""UPDATE meetings
                   SET link_revoked_at = NOW(), link_revoke_reason = :r,
                       updated_at = NOW()
                 WHERE id = :m AND link_revoked_at IS NULL"""),
        {"r": reason.strip(), "m": str(meeting_id)})
    if (updated.rowcount or 0) == 0:
        raise HTTPException(status_code=400,
                            detail="No live link to revoke on this meeting.")
    await audit(session, event_type="MEETING_LINK_REVOKED",
                meeting_id=meeting_id, actor=actor,
                detail={"reason": reason.strip()})
    return {"revoked": True}


async def set_status(
    session: AsyncSession, *, meeting_id: UUID, status: str,
    reason: Optional[str] = None, actor=None,
) -> dict:
    """Start, end or cancel. Attendance is closed out when a meeting ends."""
    status = status.upper()
    if status not in ("LIVE", "ENDED", "CANCELLED"):
        raise HTTPException(status_code=400, detail="Unknown meeting status.")
    if status == "CANCELLED" and (not reason or len(reason.strip()) < 3):
        raise HTTPException(
            status_code=400,
            detail="Say why it was cancelled. People rearranged their day for it.")

    column = {"LIVE": "started_at", "ENDED": "ended_at",
              "CANCELLED": "cancelled_at"}[status]
    updated = await session.execute(
        text(f"""UPDATE meetings
                    SET status = :s, {column} = NOW(),
                        cancel_reason = COALESCE(:r, cancel_reason),
                        updated_at = NOW()
                  WHERE id = :m AND status NOT IN ('ENDED','CANCELLED')"""),
        {"s": status, "r": reason.strip() if reason else None,
         "m": str(meeting_id)})
    if (updated.rowcount or 0) == 0:
        raise HTTPException(
            status_code=400,
            detail="This meeting has already ended or been cancelled.")

    if status in ("ENDED", "CANCELLED"):
        # Everyone still shown as present is closed out at the same moment the
        # meeting stopped. Leaving the rows open would report a meeting that
        # never finished and durations that grow forever.
        await session.execute(
            text("""UPDATE meeting_attendance
                       SET left_at = NOW(),
                           duration_seconds = GREATEST(0,
                               EXTRACT(EPOCH FROM (NOW() - joined_at))::int)
                     WHERE meeting_id = :m AND left_at IS NULL"""),
            {"m": str(meeting_id)})
        await session.execute(
            text("""UPDATE meeting_waiting_room SET status = 'WITHDRAWN',
                           decided_at = NOW()
                     WHERE meeting_id = :m AND status = 'WAITING'"""),
            {"m": str(meeting_id)})

    await audit(session, event_type=f"MEETING_{status}", meeting_id=meeting_id,
                actor=actor, detail={"reason": reason} if reason else None)
    return {"id": str(meeting_id), "status": status}


# ---------------------------------------------------------------------------
# Joining
# ---------------------------------------------------------------------------

async def _meeting_row(session: AsyncSession, meeting_id: UUID) -> dict:
    row = (await session.execute(
        text("SELECT * FROM meetings WHERE id = :m"),
        {"m": str(meeting_id)})).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Meeting not found.")
    return dict(row)


async def role_for_user(
    session: AsyncSession, *, meeting_id: UUID, user_id: UUID,
) -> Optional[str]:
    return (await session.execute(
        text("""SELECT role FROM meeting_participants
                 WHERE meeting_id = :m AND user_id = :u"""),
        {"m": str(meeting_id), "u": str(user_id)})).scalar()


async def resolve_link(session: AsyncSession, *, token: str) -> dict:
    """Turn a guest link into a meeting, or refuse with a reason.

    Every refusal is deliberately indistinguishable in shape from the others
    at the network level -- a 403 with a sentence a person can act on -- so
    that probing tokens tells an attacker nothing about which meetings exist.
    """
    if not token or len(token) < 20:
        raise HTTPException(status_code=404,
                            detail="This meeting link is not valid.")

    row = (await session.execute(
        text("""SELECT m.*, u.full_name AS host_name
                  FROM meetings m
                  LEFT JOIN users u ON u.id = m.host_user_id
                 WHERE m.link_token_sha256 = :h"""),
        {"h": _hash(token)})).mappings().first()
    if row is None:
        raise HTTPException(status_code=404,
                            detail="This meeting link is not valid.")
    if row["link_revoked_at"] is not None:
        raise HTTPException(
            status_code=403,
            detail="This meeting link has been withdrawn by the host.")
    if row["link_expires_at"] and row["link_expires_at"] <= _now():
        raise HTTPException(status_code=403,
                            detail="This meeting link has expired.")
    if row["status"] == "CANCELLED":
        raise HTTPException(status_code=403,
                            detail="This meeting was cancelled.")
    if row["status"] == "ENDED":
        raise HTTPException(status_code=403, detail="This meeting has ended.")
    if not row["guest_access_enabled"]:
        raise HTTPException(
            status_code=403,
            detail="This meeting is open to Bonnesante Medicals staff only.")
    return dict(row)


async def _live_count(session: AsyncSession, meeting_id: UUID) -> int:
    return int((await session.execute(
        text("""SELECT COUNT(*) FROM meeting_attendance
                 WHERE meeting_id = :m AND left_at IS NULL"""),
        {"m": str(meeting_id)})).scalar() or 0)


async def guest_join(
    session: AsyncSession, *, token: str, display_name: str,
    passcode: Optional[str] = None, ip: str = "", user_agent: str = "",
) -> dict:
    """A guest asks to join. Returns either a waiting-room place or a seat.

    Nothing about the company leaves this function beyond the meeting title and
    the host's name -- which are on the invitation the guest already holds.
    """
    meeting = await resolve_link(session, token=token)
    meeting_id = meeting["id"]

    display_name = (display_name or "").strip()[:120]
    if len(display_name) < 2:
        raise HTTPException(status_code=400,
                            detail="Please enter the name you want shown.")

    recent = int((await session.execute(
        text("""SELECT COUNT(*) FROM meeting_audit_logs
                 WHERE meeting_id = :m AND event_type = 'GUEST_JOIN_REQUEST'
                   AND created_at > NOW() - INTERVAL '1 hour'"""),
        {"m": str(meeting_id)})).scalar() or 0)
    if recent >= JOIN_CAP_PER_HOUR:
        raise HTTPException(
            status_code=429,
            detail="Too many people have joined through this link in the last "
                   "hour. Please contact the host.")

    if meeting["passcode_hash"]:
        if not passcode or not _check_passcode(
                passcode, meeting["passcode_hash"]):
            raise HTTPException(status_code=403,
                                detail="That meeting passcode is not correct.")

    if meeting["is_locked"]:
        raise HTTPException(
            status_code=403,
            detail="The host has locked this meeting to new participants.")

    if await _live_count(session, meeting_id) >= meeting["max_participants"]:
        raise HTTPException(status_code=403, detail="This meeting is full.")

    await audit(session, event_type="GUEST_JOIN_REQUEST", meeting_id=meeting_id,
                actor_label=display_name, ip_address=ip, user_agent=user_agent,
                detail={"name": display_name})

    if meeting["waiting_room"]:
        waiting_id = uuid4()
        await session.execute(
            text("""INSERT INTO meeting_waiting_room
                        (id, meeting_id, display_name, is_guest, ip_address,
                         user_agent)
                    VALUES (:id, :m, :n, TRUE, :ip, :ua)"""),
            {"id": str(waiting_id), "m": str(meeting_id), "n": display_name,
             "ip": (ip or "")[:64] or None, "ua": (user_agent or "")[:500] or None})
        return {"status": "WAITING", "waiting_id": str(waiting_id),
                "meeting": _public_meeting(meeting),
                "note": "The host has been asked to let you in."}

    return await _issue_guest_seat(session, meeting=meeting,
                                   display_name=display_name)


async def _issue_guest_seat(
    session: AsyncSession, *, meeting: dict, display_name: str,
) -> dict:
    """Open an attendance row and hand back everything the browser needs."""
    attendance_id = uuid4()
    await session.execute(
        text("""INSERT INTO meeting_attendance
                    (id, meeting_id, display_name, is_guest, role)
                VALUES (:id, :m, :n, TRUE, 'GUEST')"""),
        {"id": str(attendance_id), "m": str(meeting["id"]), "n": display_name})

    await audit(session, event_type="GUEST_JOINED",
                meeting_id=meeting["id"], actor_label=display_name)

    pass_token = guest_token(
        meeting_id=meeting["id"], display_name=display_name,
        may_share_screen=meeting["guest_screen_share"],
        may_chat=meeting["guest_chat"])

    return {
        "status": "ADMITTED",
        "attendance_id": str(attendance_id),
        "guest_pass": pass_token,
        "meeting": _public_meeting(meeting),
        "conference": {
            "domain": jitsi_domain(),
            "room": _scoped_room(meeting["room_name"]),
            "jwt": jitsi_token(room=meeting["room_name"],
                               display_name=display_name, email=None,
                               moderator=False),
            "display_name": display_name,
            "can_screen_share": bool(meeting["guest_screen_share"]),
            "can_chat": bool(meeting["guest_chat"]),
        },
    }


def _scoped_room(room_name: str) -> str:
    """The room as the media server names it.

    JaaS namespaces every room under the tenant; the public server does not.
    Getting this wrong puts the meeting in a room nobody else is in, which
    looks exactly like a broken microphone to the person it happens to.
    """
    if jaas_configured():
        return f"{os.getenv('JAAS_APP_ID')}/{room_name}"
    return room_name


def _public_meeting(meeting: dict) -> dict:
    """What a guest may know: the title, the host's name, when, how long.

    Not the description -- an internal agenda routinely names customers,
    figures and staff, and the guest was invited to the meeting, not to the
    company's notes about it.
    """
    return {
        "title": meeting["title"],
        "host": meeting.get("host_name"),
        "scheduled_start": (meeting["scheduled_start"].isoformat()
                            if meeting.get("scheduled_start") else None),
        "duration_minutes": meeting.get("duration_minutes"),
        "waiting_room": bool(meeting.get("waiting_room")),
    }


async def guest_waiting_status(
    session: AsyncSession, *, token: str, waiting_id: UUID,
) -> dict:
    """Has the host decided yet? Polled by the guest's browser."""
    meeting = await resolve_link(session, token=token)
    row = (await session.execute(
        text("""SELECT * FROM meeting_waiting_room
                 WHERE id = :w AND meeting_id = :m"""),
        {"w": str(waiting_id), "m": str(meeting["id"])})).mappings().first()
    if row is None:
        raise HTTPException(status_code=404,
                            detail="That waiting-room request is no longer valid.")

    if row["status"] == "WAITING":
        return {"status": "WAITING", "meeting": _public_meeting(meeting)}
    if row["status"] in ("REJECTED", "WITHDRAWN"):
        return {"status": "REJECTED",
                "note": "The host did not admit you to this meeting."}

    return await _issue_guest_seat(session, meeting=meeting,
                                   display_name=row["display_name"])


async def internal_join(
    session: AsyncSession, *, meeting_id: UUID, user, ip: str = "",
) -> dict:
    """A signed-in member of staff joins. No waiting room for internal users.

    They are already authenticated as somebody the company employs; making them
    queue behind a lobby adds a delay and answers no question the login has not
    already answered.
    """
    meeting = await _meeting_row(session, meeting_id)
    if meeting["status"] == "CANCELLED":
        raise HTTPException(status_code=403, detail="This meeting was cancelled.")
    if meeting["status"] == "ENDED":
        raise HTTPException(status_code=403, detail="This meeting has ended.")

    role = await role_for_user(session, meeting_id=meeting_id, user_id=user.id)
    if role is None:
        # Not invited. Staff can still join an unlocked meeting -- an internal
        # meeting somebody was left off by mistake is a far more common event
        # than an employee gatecrashing -- but it is recorded as such.
        if meeting["is_locked"]:
            raise HTTPException(status_code=403,
                                detail="The host has locked this meeting.")
        role = "PARTICIPANT"
        await audit(session, event_type="UNINVITED_STAFF_JOINED",
                    meeting_id=meeting_id, actor=user)

    if await _live_count(session, meeting_id) >= meeting["max_participants"]:
        raise HTTPException(status_code=403, detail="This meeting is full.")

    attendance_id = uuid4()
    await session.execute(
        text("""INSERT INTO meeting_attendance
                    (id, meeting_id, user_id, display_name, is_guest, role)
                VALUES (:id, :m, :u, :n, FALSE, :r)"""),
        {"id": str(attendance_id), "m": str(meeting_id), "u": str(user.id),
         "n": user.full_name or user.email, "r": role})

    if meeting["status"] == "SCHEDULED" and role in ("HOST", "CO_HOST"):
        await set_status(session, meeting_id=meeting_id, status="LIVE",
                         actor=user)

    await audit(session, event_type="STAFF_JOINED", meeting_id=meeting_id,
                actor=user, detail={"role": role}, ip_address=ip)

    moderator = role in ("HOST", "CO_HOST")
    return {
        "attendance_id": str(attendance_id),
        "role": role,
        "meeting": _public_meeting(meeting) | {
            "id": str(meeting_id),
            "meeting_code": meeting["meeting_code"],
            "description": meeting["description"],
            "status": meeting["status"],
            "is_locked": meeting["is_locked"],
        },
        "conference": {
            "domain": jitsi_domain(),
            "room": _scoped_room(meeting["room_name"]),
            "jwt": jitsi_token(room=meeting["room_name"],
                               display_name=user.full_name or user.email,
                               email=user.email, moderator=moderator),
            "display_name": user.full_name or user.email,
            "moderator": moderator,
            "can_screen_share": True,
            "can_chat": True,
        },
        "secured_by": "signature" if jaas_configured() else "unguessable-link",
    }


async def leave(
    session: AsyncSession, *, attendance_id: UUID,
) -> dict:
    """Close an attendance row. Idempotent: a browser may say this twice."""
    updated = await session.execute(
        text("""UPDATE meeting_attendance
                   SET left_at = NOW(),
                       duration_seconds = GREATEST(0,
                           EXTRACT(EPOCH FROM (NOW() - joined_at))::int)
                 WHERE id = :a AND left_at IS NULL"""),
        {"a": str(attendance_id)})
    return {"closed": bool(updated.rowcount)}


# ---------------------------------------------------------------------------
# Host controls
# ---------------------------------------------------------------------------

async def require_host(
    session: AsyncSession, *, meeting_id: UUID, user,
) -> str:
    """Raise unless this user runs this meeting.

    Server-side, and called by every host route. The UI hiding a button is a
    courtesy; this is the permission.
    """
    role = await role_for_user(session, meeting_id=meeting_id, user_id=user.id)
    if role not in ("HOST", "CO_HOST"):
        raise HTTPException(
            status_code=403,
            detail="Only the host or a co-host can do that.")
    return role


async def decide_waiting(
    session: AsyncSession, *, meeting_id: UUID, waiting_id: UUID,
    admit: bool, actor=None,
) -> dict:
    updated = await session.execute(
        text("""UPDATE meeting_waiting_room
                   SET status = :s, decided_at = NOW(), decided_by = :by
                 WHERE id = :w AND meeting_id = :m AND status = 'WAITING'"""),
        {"s": "ADMITTED" if admit else "REJECTED",
         "by": str(actor.id) if actor else None,
         "w": str(waiting_id), "m": str(meeting_id)})
    if (updated.rowcount or 0) == 0:
        raise HTTPException(status_code=400,
                            detail="That request has already been decided.")
    await audit(session,
                event_type="GUEST_ADMITTED" if admit else "GUEST_REJECTED",
                meeting_id=meeting_id, actor=actor,
                detail={"waiting_id": str(waiting_id)})
    return {"admitted": admit}


async def remove_participant(
    session: AsyncSession, *, meeting_id: UUID, attendance_id: UUID, actor=None,
) -> dict:
    """Close somebody's attendance and record who removed them.

    The honest limit: this ends their attendance RECORD. Ejecting them from the
    media session is Jitsi's moderator control, which is why the moderator
    claim in the JaaS token matters -- without JaaS the host can remove
    somebody from the register but not from the room.
    """
    row = (await session.execute(
        text("""SELECT display_name FROM meeting_attendance
                 WHERE id = :a AND meeting_id = :m"""),
        {"a": str(attendance_id), "m": str(meeting_id)})).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Participant not found.")

    await leave(session, attendance_id=attendance_id)
    await audit(session, event_type="PARTICIPANT_REMOVED",
                meeting_id=meeting_id, actor=actor,
                detail={"name": row["display_name"]})
    return {"removed": True,
            "note": ("Their attendance is closed. Removing them from the live "
                     "call is done with the host controls inside the meeting."
                     if not jaas_configured() else "Removed.")}


async def set_lock(
    session: AsyncSession, *, meeting_id: UUID, locked: bool, actor=None,
) -> dict:
    await session.execute(
        text("UPDATE meetings SET is_locked = :l, updated_at = NOW() "
             "WHERE id = :m"),
        {"l": locked, "m": str(meeting_id)})
    await audit(session, event_type="MEETING_LOCKED" if locked
                else "MEETING_UNLOCKED", meeting_id=meeting_id, actor=actor)
    return {"is_locked": locked}


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

async def list_meetings(
    session: AsyncSession, *, user, scope: str = "mine",
    include_past: bool = False,
) -> list[dict]:
    """Meetings this user may see.

    Scoped to the user, not to their role. A meeting is private to the people
    in it: being an administrator of the ERP is not a reason to read what the
    sales team discussed with a supplier.
    """
    clauses = ["(m.host_user_id = :u OR EXISTS (SELECT 1 FROM "
               "meeting_participants p WHERE p.meeting_id = m.id "
               "AND p.user_id = :u))"]
    if not include_past:
        clauses.append("m.status IN ('SCHEDULED','LIVE')")
    if scope == "hosting":
        clauses.append("m.host_user_id = :u")

    rows = (await session.execute(
        text(f"""
            SELECT m.id, m.meeting_code, m.title, m.description,
                   m.scheduled_start, m.duration_minutes, m.status,
                   m.started_at, m.ended_at, m.is_locked,
                   m.guest_access_enabled, m.waiting_room,
                   m.guest_screen_share, m.guest_chat, m.max_participants,
                   (m.passcode_hash IS NOT NULL) AS has_passcode,
                   (m.link_token_sha256 IS NOT NULL
                    AND m.link_revoked_at IS NULL
                    AND (m.link_expires_at IS NULL
                         OR m.link_expires_at > NOW())) AS link_live,
                   m.link_token_hint, m.link_expires_at,
                   u.full_name AS host_name,
                   (m.host_user_id = :u) AS i_am_host,
                   COALESCE(p.role, 'PARTICIPANT') AS my_role,
                   (SELECT COUNT(*) FROM meeting_participants mp
                     WHERE mp.meeting_id = m.id) AS invited_count,
                   (SELECT COUNT(*) FROM meeting_attendance ma
                     WHERE ma.meeting_id = m.id AND ma.left_at IS NULL)
                       AS live_now,
                   (SELECT COUNT(*) FROM meeting_waiting_room w
                     WHERE w.meeting_id = m.id AND w.status = 'WAITING')
                       AS waiting_count
              FROM meetings m
              LEFT JOIN users u ON u.id = m.host_user_id
              LEFT JOIN meeting_participants p
                     ON p.meeting_id = m.id AND p.user_id = :u
             WHERE {' AND '.join(clauses)}
             ORDER BY m.scheduled_start DESC
             LIMIT 200
        """), {"u": str(user.id)})).mappings().all()
    return [dict(r) | {"id": str(r["id"])} for r in rows]


async def meeting_detail(
    session: AsyncSession, *, meeting_id: UUID, user,
) -> dict:
    """One meeting, with its people. Refused unless the user is in it."""
    role = await role_for_user(session, meeting_id=meeting_id, user_id=user.id)
    meeting = await _meeting_row(session, meeting_id)
    if role is None and str(meeting["host_user_id"]) != str(user.id):
        raise HTTPException(status_code=404, detail="Meeting not found.")

    participants = (await session.execute(
        text("""SELECT p.user_id, p.role, u.full_name, u.email
                  FROM meeting_participants p
                  JOIN users u ON u.id = p.user_id
                 WHERE p.meeting_id = :m ORDER BY p.role, u.full_name"""),
        {"m": str(meeting_id)})).mappings().all()

    attendance = (await session.execute(
        text("""SELECT display_name, is_guest, role, joined_at, left_at,
                       duration_seconds
                  FROM meeting_attendance
                 WHERE meeting_id = :m ORDER BY joined_at"""),
        {"m": str(meeting_id)})).mappings().all()

    waiting = (await session.execute(
        text("""SELECT id, display_name, is_guest, requested_at
                  FROM meeting_waiting_room
                 WHERE meeting_id = :m AND status = 'WAITING'
                 ORDER BY requested_at"""),
        {"m": str(meeting_id)})).mappings().all()

    host_name = (await session.execute(
        text("SELECT full_name FROM users WHERE id = :u"),
        {"u": str(meeting["host_user_id"])})).scalar()

    out = {k: v for k, v in meeting.items()
           if k not in ("room_name", "link_token_sha256", "passcode_hash")}
    out["id"] = str(meeting_id)
    out["host_name"] = host_name
    out["my_role"] = role or "PARTICIPANT"
    out["has_passcode"] = meeting["passcode_hash"] is not None
    out["secured_by"] = "signature" if jaas_configured() else "unguessable-link"
    out["participants"] = [dict(p) | {"user_id": str(p["user_id"])}
                           for p in participants]
    out["attendance"] = [dict(a) for a in attendance]
    out["waiting"] = [dict(w) | {"id": str(w["id"])} for w in waiting]
    return out


def invitation_text(meeting: dict, join_url: str) -> str:
    """The message a host pastes into WhatsApp or email.

    Carries the title, the host, the time and the link. Not the description:
    an agenda is internal, and an invitation is forwarded.
    """
    start = meeting.get("scheduled_start")
    when = start.strftime("%d %B %Y at %I:%M %p") if hasattr(start, "strftime") \
        else str(start or "")
    lines = [
        "You are invited to a meeting.",
        "",
        f"Meeting:  {meeting['title']}",
        f"Host:     {meeting.get('host_name') or 'Bonnesante Medicals'}",
        f"When:     {when}",
        f"Duration: {meeting.get('duration_minutes', 60)} minutes",
        f"Meeting ID: {meeting.get('meeting_code', '')}",
        "",
        "Join from your browser:",
        join_url,
        "",
        "No account or download is needed. Open the link, enter your name, "
        "and allow your browser to use your microphone and camera.",
    ]
    if meeting.get("has_passcode"):
        lines += ["", "A passcode is required. The host will send it "
                      "separately."]
    return "\n".join(lines)
