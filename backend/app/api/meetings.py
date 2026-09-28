"""Meetings, for people who are signed in.

Every route here requires a user session. The guest side of the same feature
lives in app/api/portal.py with the rest of the application's public surface,
so that one file holds everything reachable without a login and the hardening
test finds it all in one place.

AUTHORISATION IS PER MEETING, NOT PER ROLE
==========================================
The ERP's roles answer "what part of the business is this person in". They do
not answer "was this person in that meeting", and using them here would be
wrong in both directions: an administrator would be able to read a meeting they
were not in, and a marketer invited to a supplier call could not open it.

So these routes check membership of the meeting itself -- `require_host` for
anything that controls it, and a participant check for anything that reads it.
A meeting is private to the people in it.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_authenticated_user
from app.db import get_session
from app.models import User
from app.services import meetings as svc

router = APIRouter(prefix="/api/meetings", tags=["Meetings"])


def _rows(rows):
    return [
        {k: (str(v) if hasattr(v, "hex") else v) for k, v in row.items()}
        for row in rows
    ]


class ParticipantIn(BaseModel):
    user_id: UUID
    role: str = "PARTICIPANT"


class MeetingIn(BaseModel):
    title: str = Field(..., min_length=2, max_length=255)
    scheduled_start: datetime
    duration_minutes: int = Field(60, ge=1, le=1440)
    description: Optional[str] = None
    participants: List[ParticipantIn] = []
    guest_access_enabled: bool = True
    waiting_room: bool = True
    guest_screen_share: bool = False
    guest_chat: bool = True
    max_participants: int = Field(50, ge=2, le=500)
    passcode: Optional[str] = Field(None, min_length=4, max_length=64)
    link_days: int = Field(svc.DEFAULT_LINK_DAYS, ge=1, le=svc.MAX_LINK_DAYS)


class MeetingUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=2, max_length=255)
    scheduled_start: Optional[datetime] = None
    duration_minutes: Optional[int] = Field(None, ge=1, le=1440)
    description: Optional[str] = None
    guest_access_enabled: Optional[bool] = None
    waiting_room: Optional[bool] = None
    guest_screen_share: Optional[bool] = None
    guest_chat: Optional[bool] = None
    max_participants: Optional[int] = Field(None, ge=2, le=500)


class ReasonIn(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500)


class LinkIn(BaseModel):
    link_days: int = Field(svc.DEFAULT_LINK_DAYS, ge=1, le=svc.MAX_LINK_DAYS)


class LockIn(BaseModel):
    locked: bool


class DecideIn(BaseModel):
    admit: bool


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

@router.get("/config")
async def config(user: User = Depends(require_authenticated_user)):
    """What this deployment can do, so the screens can stop guessing.

    `secured_by` is surfaced deliberately. A host sending a link to a supplier
    should know whether that link is protected by a signature the media server
    checks, or only by being unguessable.
    """
    return {
        "jaas_configured": svc.jaas_configured(),
        "domain": svc.jitsi_domain(),
        "secured_by": "signature" if svc.jaas_configured()
        else "unguessable-link",
        "guest_access_available": bool(
            __import__("os").getenv("MEETING_GUEST_SECRET")),
        "note": (
            "Meeting media runs on Jitsi. This server stores who may join, "
            "who hosted and who attended."),
    }


@router.get("")
async def list_meetings(
    scope: str = "mine",
    include_past: bool = False,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Meetings this user is in. Never anybody else's."""
    return {"meetings": _rows(
        await svc.list_meetings(session, user=user, scope=scope,
                                include_past=include_past))}


@router.get("/{meeting_id}")
async def meeting_detail(
    meeting_id: UUID,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    return await svc.meeting_detail(session, meeting_id=meeting_id, user=user)


@router.get("/{meeting_id}/invitation")
async def invitation(
    meeting_id: UUID,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """The invitation text. Host only -- it contains the live join link.

    The link itself cannot be rebuilt here: only its hash is stored. A host who
    has lost it regenerates, which is the same trade the distributor ordering
    link makes and for the same reason.
    """
    await svc.require_host(session, meeting_id=meeting_id, user=user)
    detail = await svc.meeting_detail(session, meeting_id=meeting_id, user=user)
    return {
        "invitation": svc.invitation_text(detail, "<join link>"),
        "note": ("The join link is shown once, when it is created or "
                 "regenerated. Only its fingerprint is stored here."),
    }


# ---------------------------------------------------------------------------
# Creating and changing
# ---------------------------------------------------------------------------

@router.post("", status_code=201)
async def create_meeting(
    body: MeetingIn,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Schedule a meeting. Any signed-in member of staff may hold one."""
    result = await svc.create_meeting(
        session, title=body.title, scheduled_start=body.scheduled_start,
        duration_minutes=body.duration_minutes, description=body.description,
        host_user_id=user.id,
        participants=[p.model_dump(mode="json") for p in body.participants],
        guest_access_enabled=body.guest_access_enabled,
        waiting_room=body.waiting_room,
        guest_screen_share=body.guest_screen_share,
        guest_chat=body.guest_chat, max_participants=body.max_participants,
        passcode=body.passcode, link_days=body.link_days, actor=user)
    await session.commit()

    detail = await svc.meeting_detail(
        session, meeting_id=UUID(result["id"]), user=user)
    result["invitation"] = svc.invitation_text(detail, result["join_url"])
    return result


@router.patch("/{meeting_id}")
async def update_meeting(
    meeting_id: UUID,
    body: MeetingUpdate,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    await svc.require_host(session, meeting_id=meeting_id, user=user)
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items()
              if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="Nothing to change.")

    sets = ", ".join(f"{k} = :{k}" for k in fields)
    await session.execute(
        text(f"UPDATE meetings SET {sets}, updated_at = NOW() WHERE id = :m"),
        {**fields, "m": str(meeting_id)})
    await svc.audit(session, event_type="MEETING_UPDATED",
                    meeting_id=meeting_id, actor=user,
                    detail={"changed": sorted(fields)})
    await session.commit()
    return await svc.meeting_detail(session, meeting_id=meeting_id, user=user)


@router.post("/{meeting_id}/status")
async def change_status(
    meeting_id: UUID,
    body: dict,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Start, end, or cancel. Ending closes everyone's attendance."""
    await svc.require_host(session, meeting_id=meeting_id, user=user)
    result = await svc.set_status(
        session, meeting_id=meeting_id, status=body.get("status", ""),
        reason=body.get("reason"), actor=user)
    await session.commit()
    return result


@router.post("/{meeting_id}/participants", status_code=201)
async def add_participant(
    meeting_id: UUID,
    body: ParticipantIn,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    await svc.require_host(session, meeting_id=meeting_id, user=user)
    role = body.role.upper()
    if role not in ("CO_HOST", "PARTICIPANT"):
        role = "PARTICIPANT"
    await session.execute(
        text("""INSERT INTO meeting_participants
                    (id, meeting_id, user_id, role, invited_by)
                VALUES (gen_random_uuid(), :m, :u, :r, :by)
                ON CONFLICT (meeting_id, user_id)
                DO UPDATE SET role = EXCLUDED.role
                 WHERE meeting_participants.role <> 'HOST'"""),
        {"m": str(meeting_id), "u": str(body.user_id), "r": role,
         "by": str(user.id)})
    await svc.audit(session, event_type="PARTICIPANT_INVITED",
                    meeting_id=meeting_id, actor=user,
                    detail={"user_id": str(body.user_id), "role": role})
    await session.commit()
    return {"added": True, "role": role}


@router.delete("/{meeting_id}/participants/{user_id}")
async def remove_invitation(
    meeting_id: UUID,
    user_id: UUID,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Uninvite somebody. The host cannot be uninvited from their own meeting."""
    await svc.require_host(session, meeting_id=meeting_id, user=user)
    removed = await session.execute(
        text("""DELETE FROM meeting_participants
                 WHERE meeting_id = :m AND user_id = :u AND role <> 'HOST'"""),
        {"m": str(meeting_id), "u": str(user_id)})
    if (removed.rowcount or 0) == 0:
        raise HTTPException(
            status_code=400,
            detail="Not invited, or this is the host of the meeting.")
    await svc.audit(session, event_type="PARTICIPANT_UNINVITED",
                    meeting_id=meeting_id, actor=user,
                    detail={"user_id": str(user_id)})
    await session.commit()
    return {"removed": True}


# ---------------------------------------------------------------------------
# The link
# ---------------------------------------------------------------------------

@router.post("/{meeting_id}/link/regenerate")
async def regenerate_link(
    meeting_id: UUID,
    body: LinkIn,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """A new link, shown once. The old one stops working immediately."""
    await svc.require_host(session, meeting_id=meeting_id, user=user)
    result = await svc.regenerate_link(
        session, meeting_id=meeting_id, link_days=body.link_days, actor=user)
    detail = await svc.meeting_detail(session, meeting_id=meeting_id, user=user)
    result["invitation"] = svc.invitation_text(detail, result["join_url"])
    await session.commit()
    return result


@router.post("/{meeting_id}/link/revoke")
async def revoke_link(
    meeting_id: UUID,
    body: ReasonIn,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    await svc.require_host(session, meeting_id=meeting_id, user=user)
    result = await svc.revoke_link(
        session, meeting_id=meeting_id, reason=body.reason, actor=user)
    await session.commit()
    return result


# ---------------------------------------------------------------------------
# In the meeting
# ---------------------------------------------------------------------------

@router.post("/{meeting_id}/join")
async def join(
    meeting_id: UUID,
    request: Request,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Join as staff. Returns everything the browser needs and nothing more.

    The room name reaches the client only here, after the server has decided
    this person may be in this meeting.
    """
    result = await svc.internal_join(
        session, meeting_id=meeting_id, user=user,
        ip=(request.client.host if request.client else ""))
    await session.commit()
    return result


@router.post("/attendance/{attendance_id}/leave")
async def leave(
    attendance_id: UUID,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Close an attendance row.

    Deliberately not checked against the caller: the worst a wrong id does is
    close a row early, the ids are unguessable, and a browser being closed is
    the common case this has to survive.
    """
    result = await svc.leave(session, attendance_id=attendance_id)
    await session.commit()
    return result


@router.get("/{meeting_id}/waiting")
async def waiting_room(
    meeting_id: UUID,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Who is asking to come in. Host only -- it names people outside."""
    await svc.require_host(session, meeting_id=meeting_id, user=user)
    rows = (await session.execute(
        text("""SELECT id, display_name, is_guest, requested_at
                  FROM meeting_waiting_room
                 WHERE meeting_id = :m AND status = 'WAITING'
                 ORDER BY requested_at"""),
        {"m": str(meeting_id)})).mappings().all()
    live = (await session.execute(
        text("""SELECT id, display_name, is_guest, role, joined_at
                  FROM meeting_attendance
                 WHERE meeting_id = :m AND left_at IS NULL
                 ORDER BY joined_at"""),
        {"m": str(meeting_id)})).mappings().all()
    return {"waiting": _rows(rows), "in_meeting": _rows(live)}


@router.post("/{meeting_id}/waiting/{waiting_id}")
async def decide_waiting(
    meeting_id: UUID,
    waiting_id: UUID,
    body: DecideIn,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    await svc.require_host(session, meeting_id=meeting_id, user=user)
    result = await svc.decide_waiting(
        session, meeting_id=meeting_id, waiting_id=waiting_id,
        admit=body.admit, actor=user)
    await session.commit()
    return result


@router.post("/{meeting_id}/participants/{attendance_id}/remove")
async def remove_participant(
    meeting_id: UUID,
    attendance_id: UUID,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    await svc.require_host(session, meeting_id=meeting_id, user=user)
    result = await svc.remove_participant(
        session, meeting_id=meeting_id, attendance_id=attendance_id, actor=user)
    await session.commit()
    return result


@router.post("/{meeting_id}/lock")
async def lock(
    meeting_id: UUID,
    body: LockIn,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    await svc.require_host(session, meeting_id=meeting_id, user=user)
    result = await svc.set_lock(
        session, meeting_id=meeting_id, locked=body.locked, actor=user)
    await session.commit()
    return result


@router.get("/{meeting_id}/attendance")
async def attendance(
    meeting_id: UUID,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Who came, and for how long.

    Anyone who was in the meeting may read it. A row still open is reported as
    open rather than given an invented leaving time -- a browser that crashed
    never told us it left.
    """
    role = await svc.role_for_user(session, meeting_id=meeting_id,
                                   user_id=user.id)
    if role is None:
        raise HTTPException(status_code=404, detail="Meeting not found.")
    rows = (await session.execute(
        text("""SELECT display_name, is_guest, role, joined_at, left_at,
                       duration_seconds
                  FROM meeting_attendance
                 WHERE meeting_id = :m ORDER BY joined_at"""),
        {"m": str(meeting_id)})).mappings().all()
    return {
        "attendance": _rows(rows),
        "note": ("A blank leaving time means that person's browser closed "
                 "without telling us; their attendance is closed when the "
                 "meeting ends."),
    }


@router.get("/{meeting_id}/audit")
async def meeting_audit(
    meeting_id: UUID,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """What was done in this meeting, and by whom. Host only."""
    await svc.require_host(session, meeting_id=meeting_id, user=user)
    rows = (await session.execute(
        text("""SELECT a.event_type, a.actor_label, a.detail, a.created_at,
                       u.full_name AS actor_name
                  FROM meeting_audit_logs a
                  LEFT JOIN users u ON u.id = a.actor_user_id
                 WHERE a.meeting_id = :m
                 ORDER BY a.created_at DESC LIMIT 500"""),
        {"m": str(meeting_id)})).mappings().all()
    return {"events": _rows(rows)}
