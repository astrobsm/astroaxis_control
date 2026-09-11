"""Call recordings: capture, storage, playback and destruction.

Recording is a SEPARATE switch from bridging. Turning on click-to-call must
never start recording as a side effect -- one is an operational improvement,
the other creates personal data about your customers and carries obligations
under the Nigeria Data Protection Act 2023.

THE ANNOUNCEMENT, AND ITS HONEST LIMIT
--------------------------------------
Before the legs are joined, the provider plays a spoken notice. With Africa's
Talking this is a <Say> in the answer response, which is heard by whoever is on
the call at that moment -- the STAFF MEMBER, because their leg is answered
first and the customer has not been dialled yet.

That is a real control: it reminds the employee, on every single call, that
the call is being recorded and that they must say so. It is NOT the same as
the customer hearing an automated notice, and this module does not pretend
otherwise.

Providers that support a "whisper" to the called party can announce to the
customer directly; RECORDING_CALLEE_ANNOUNCE_URL exists for that and is used
when set. Until it is, the customer notice depends on the staff member saying
the line -- which is why the wording is printed on their screen before the
call, stored in policy, and why `announced` is recorded per call rather than
assumed.

Do not switch recording on until the staff notice is in their contract or
written policy, and the customer notice is actually being given.

DELETION IS THE POINT
---------------------
Every recording is stored with a date after which it must not exist.
`sweep_expired` destroys the audio and marks the row DELETED. If that job stops
running, the retention promise is broken silently -- so it reports what it did
and the admin screen shows how many recordings are overdue.
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import objectstore

# Separate from TELEPHONY_* on purpose. See the module docstring.
RECORDING_ENABLED = (os.getenv("CALL_RECORDING_ENABLED", "") or "").lower() in (
    "1", "true", "yes", "on")

RETENTION_DAYS = int(os.getenv("CALL_RECORDING_RETENTION_DAYS", "90"))

ANNOUNCEMENT = os.getenv(
    "CALL_RECORDING_ANNOUNCEMENT",
    "This call is being recorded for quality and record keeping. "
    "Please inform the customer before you continue.")

# A provider-hosted audio file played to the CALLED party, where the provider
# supports it. Unset by default because Africa's Talking has no such hook.
CALLEE_ANNOUNCE_URL = os.getenv("RECORDING_CALLEE_ANNOUNCE_URL", "")

# What staff are told to say. Shown on the call screen and reproduced in the
# policy documentation, so the wording is the same everywhere.
STAFF_SCRIPT = os.getenv(
    "CALL_RECORDING_STAFF_SCRIPT",
    "Good day, this is {staff} from Bonnesante Medicals. "
    "Please note this call is being recorded for our records.")


def configured() -> tuple[bool, Optional[str]]:
    """Is recording usable? Requires object storage as well as the switch."""
    if not RECORDING_ENABLED:
        return False, ("Call recording is switched off. Set "
                       "CALL_RECORDING_ENABLED=true to enable it -- but put "
                       "the staff notice in writing first.")
    ok, reason = objectstore.configured()
    if not ok:
        return False, (f"Call recording needs object storage. {reason}")
    if RETENTION_DAYS < 1 or RETENTION_DAYS > 3650:
        return False, ("CALL_RECORDING_RETENTION_DAYS must be between 1 and "
                       "3650.")
    return True, None


def retention_date(on: Optional[date] = None) -> date:
    return (on or date.today()) + timedelta(days=RETENTION_DAYS)


def storage_key(call_reference: str, recording_id: UUID) -> str:
    """Keys are partitioned by month so a lifecycle rule can act on them too.

    Belt and braces: if this application ever stops running its sweep, a bucket
    lifecycle policy on the same prefixes is the backstop that still honours
    the retention promise.
    """
    return (f"call-recordings/{date.today():%Y/%m}/"
            f"{call_reference}-{recording_id.hex[:8]}.mp3")


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

async def register(
    session: AsyncSession, *, call_id: UUID, call_reference: str,
    provider: str, provider_url: Optional[str], duration_seconds: Optional[int],
    announced: bool,
) -> Optional[UUID]:
    """Note that a recording exists, before trying to fetch it.

    Written first so a recording the provider made is on record even if the
    download then fails. The alternative loses all trace of audio that exists
    on someone else's server and is still subject to a deletion request.
    """
    rec_id = uuid4()
    await session.execute(
        text("""
            INSERT INTO call_recordings
                (id, call_id, provider, provider_url, duration_seconds,
                 announced, status, retention_until)
            VALUES (:id, :c, :p, :url, :dur, :ann, 'PENDING', :ret)
            ON CONFLICT (call_id) DO NOTHING
        """),
        {"id": str(rec_id), "c": str(call_id), "p": provider,
         "url": provider_url, "dur": duration_seconds, "ann": announced,
         "ret": retention_date()},
    )
    row = (await session.execute(
        text("SELECT id FROM call_recordings WHERE call_id = :c"),
        {"c": str(call_id)},
    )).first()
    return row.id if row else None


async def store(
    session: AsyncSession, *, recording_id: UUID, call_reference: str,
) -> dict:
    """Fetch the audio from the provider and put our own copy in storage.

    Until this succeeds the only copy is the provider's, governed by their
    retention rather than ours -- which is the situation the retention promise
    exists to avoid.
    """
    rec = (await session.execute(
        text("""SELECT id, provider_url, status FROM call_recordings
                 WHERE id = :id"""),
        {"id": str(recording_id)},
    )).mappings().first()
    if rec is None:
        return {"stored": False, "reason": "no such recording"}
    if rec["status"] == "STORED":
        return {"stored": True, "reason": "already stored"}
    if not rec["provider_url"]:
        await _fail(session, recording_id, "the provider gave no audio URL")
        return {"stored": False, "reason": "no provider url"}

    key = storage_key(call_reference, recording_id)
    try:
        audio = await objectstore.fetch(rec["provider_url"])
        await objectstore.put_object(key, audio, content_type="audio/mpeg")
    except Exception as exc:
        await _fail(session, recording_id, str(exc)[:255])
        return {"stored": False, "reason": str(exc)}

    await session.execute(
        text("""
            UPDATE call_recordings
               SET status = 'STORED', storage_key = :k, storage_bucket = :b,
                   byte_size = :sz, content_type = 'audio/mpeg',
                   provider_url = NULL, failure_reason = NULL,
                   updated_at = NOW()
             WHERE id = :id
        """),
        {"k": key, "b": objectstore.SPACES_BUCKET, "sz": len(audio),
         "id": str(recording_id)},
    )
    # provider_url is cleared once we hold the audio: leaving a link to
    # someone else's copy of a customer's voice serves no purpose and is one
    # more place it can leak from.
    return {"stored": True, "key": key, "bytes": len(audio)}


async def _fail(session: AsyncSession, recording_id: UUID, reason: str) -> None:
    await session.execute(
        text("""UPDATE call_recordings SET status = 'FAILED',
                       failure_reason = :r, updated_at = NOW()
                 WHERE id = :id AND status <> 'DELETED'"""),
        {"r": reason, "id": str(recording_id)},
    )


# ---------------------------------------------------------------------------
# Playback -- always logged
# ---------------------------------------------------------------------------

async def log_access(
    session: AsyncSession, *, recording_id: Optional[UUID], call_id,
    user, action: str, ip_address: str = "", user_agent: str = "",
    note: Optional[str] = None,
) -> None:
    await session.execute(
        text("""
            INSERT INTO call_recording_access
                (id, recording_id, call_id, user_id, actor_label, action,
                 ip_address, user_agent, note)
            VALUES (gen_random_uuid(), :r, :c, :u, :al, :a, :ip, :ua, :n)
        """),
        {"r": str(recording_id) if recording_id else None,
         "c": str(call_id) if call_id else None,
         "u": str(user.id) if user else None,
         "al": getattr(user, "full_name", None),
         "a": action, "ip": (ip_address or "")[:64] or None,
         "ua": (user_agent or "")[:500] or None,
         "n": (note or "")[:255] or None},
    )


async def playback_url(
    session: AsyncSession, *, recording_id: UUID, user,
    ip_address: str = "", user_agent: str = "",
) -> str:
    """A short-lived link to one recording. The access is recorded first.

    Written before the URL is handed out, in the same transaction, so a
    playback cannot happen without a corresponding entry in the log. The URL
    itself is never stored anywhere: it is a credential.
    """
    rec = (await session.execute(
        text("""SELECT id, call_id, status, storage_key FROM call_recordings
                 WHERE id = :id"""),
        {"id": str(recording_id)},
    )).mappings().first()
    if rec is None:
        raise LookupError("Recording not found.")
    if rec["status"] == "DELETED":
        await log_access(session, recording_id=recording_id,
                         call_id=rec["call_id"], user=user, action="DENIED",
                         ip_address=ip_address, user_agent=user_agent,
                         note="already destroyed")
        raise LookupError(
            "That recording has been destroyed under the retention policy.")
    if rec["status"] != "STORED" or not rec["storage_key"]:
        raise LookupError(
            "That recording is not available yet; it may still be "
            "transferring from the provider.")

    await log_access(session, recording_id=recording_id, call_id=rec["call_id"],
                     user=user, action="PLAY", ip_address=ip_address,
                     user_agent=user_agent)
    return objectstore.presigned_get_url(rec["storage_key"],
                                         expires_seconds=300)


# ---------------------------------------------------------------------------
# Destruction
# ---------------------------------------------------------------------------

async def destroy(
    session: AsyncSession, *, recording_id: UUID, user, reason: str,
    ip_address: str = "", user_agent: str = "",
) -> dict:
    """Delete the audio for good and mark the row DELETED.

    The row stays. An auditor -- or a customer exercising a right of erasure --
    needs to see that a recording existed and was destroyed, and on what date.
    """
    rec = (await session.execute(
        text("""SELECT id, call_id, status, storage_key FROM call_recordings
                 WHERE id = :id FOR UPDATE"""),
        {"id": str(recording_id)},
    )).mappings().first()
    if rec is None:
        raise LookupError("Recording not found.")
    if rec["status"] == "DELETED":
        return {"deleted": True, "already": True}

    if rec["storage_key"]:
        await objectstore.delete_object(rec["storage_key"])

    await session.execute(
        text("""
            UPDATE call_recordings
               SET status = 'DELETED', storage_key = NULL, provider_url = NULL,
                   deleted_at = NOW(), deleted_by = :u, delete_reason = :r,
                   updated_at = NOW()
             WHERE id = :id
        """),
        {"u": str(user.id) if user else None, "r": reason[:255],
         "id": str(recording_id)},
    )
    await log_access(session, recording_id=recording_id, call_id=rec["call_id"],
                     user=user, action="DELETE", ip_address=ip_address,
                     user_agent=user_agent, note=reason[:255])
    return {"deleted": True, "already": False}


async def sweep_expired(session: AsyncSession, *, limit: int = 200) -> dict:
    """Destroy everything past its retention date. Returns what it did.

    Reports rather than logging quietly, because a retention promise that
    silently stops being kept is worse than never having made one.
    """
    due = (await session.execute(
        text("""SELECT id, call_id, storage_key FROM call_recordings
                 WHERE status IN ('PENDING','STORED','FAILED')
                   AND retention_until < CURRENT_DATE
                 ORDER BY retention_until LIMIT :lim"""),
        {"lim": limit},
    )).mappings().all()

    destroyed, failed = 0, []
    for rec in due:
        try:
            if rec["storage_key"]:
                await objectstore.delete_object(rec["storage_key"])
            await session.execute(
                text("""UPDATE call_recordings
                           SET status='DELETED', storage_key=NULL,
                               provider_url=NULL, deleted_at=NOW(),
                               delete_reason='retention period expired',
                               updated_at=NOW()
                         WHERE id = :id"""),
                {"id": str(rec["id"])})
            await log_access(session, recording_id=rec["id"],
                             call_id=rec["call_id"], user=None, action="SWEEP",
                             note="retention period expired")
            destroyed += 1
        except Exception as exc:
            failed.append({"id": str(rec["id"]), "error": str(exc)[:200]})

    overdue = (await session.execute(
        text("""SELECT COUNT(*) AS n FROM call_recordings
                 WHERE status IN ('PENDING','STORED','FAILED')
                   AND retention_until < CURRENT_DATE"""),
    )).first()

    return {"destroyed": destroyed, "failures": failed,
            "still_overdue": overdue.n}
