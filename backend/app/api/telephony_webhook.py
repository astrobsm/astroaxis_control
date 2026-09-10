"""Telephony provider callbacks. THIS ROUTER IS UNAUTHENTICATED.

It lives in its own module so that fact is impossible to miss in main.py,
where it is registered without the bearer-token dependency every other router
carries. A telephony provider cannot hold a token, so the credential is a long
random secret embedded in the URL path -- which makes that URL a password. Give
it to the provider and to nobody else, keep it out of logs, and rotate it if it
is ever exposed.

Defences, in the order they matter:

  * The secret is compared in constant time, so the endpoint cannot be probed
    a character at a time by measuring how long a wrong guess takes.
  * A wrong secret is recorded and answered with a flat 404 -- indistinguishable
    from a path that does not exist, so a prober learns nothing.
  * Every request is written to call_provider_events with its source address
    BEFORE it is interpreted, so a stream of failures is visible rather than
    silently dropped.
  * Nothing a caller sends chooses a call. The session id must already appear
    on a row this application created when a real member of staff pressed
    Call; an unrecognised session is stored and refused, never acted on.

WHAT ARRIVES HERE
-----------------
Two different events, on the same URL:

  ANSWER      the staff member picked up, and the provider is asking what to
              do. We reply with XML telling it to dial the customer and bridge
              the legs.
  COMPLETION  the call ended. The provider reports the duration and the cost,
              and we record them as VERIFIED -- the only duration in this
              system that nobody in the company can influence.
"""
from __future__ import annotations

import hmac
import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.services import telephony

router = APIRouter(prefix="/api/telephony", tags=["Telephony"])


def _secret_ok(supplied: str) -> bool:
    expected = telephony.WEBHOOK_SECRET
    if not expected or len(expected) < 24:
        return False
    return hmac.compare_digest(str(supplied or ""), expected)


async def _read_payload(request: Request) -> dict:
    """Providers post form-encoded or JSON depending on the event. Take both.

    Anything unparseable is still returned as a dict so it can be stored: a
    body we cannot read is evidence about a call that may have been billed.
    """
    ctype = (request.headers.get("content-type") or "").lower()
    try:
        if "json" in ctype:
            body = await request.json()
            return body if isinstance(body, dict) else {"_body": body}
        form = await request.form()
        return {k: str(v) for k, v in form.items()}
    except Exception:
        try:
            raw = (await request.body()).decode("utf-8", "replace")
        except Exception:
            raw = ""
        return {"_unparsed": raw[:4000]}


async def _record(session: AsyncSession, *, call_id, session_id, event_type,
                  payload, secret_ok, remote_ip, note=None, applied=False):
    await session.execute(
        text("""
            INSERT INTO call_provider_events
                (id, call_id, provider, session_id, event_type, raw,
                 secret_ok, remote_ip, applied, note)
            VALUES (gen_random_uuid(), :cid, :prov, :sid, :et,
                    CAST(:raw AS JSONB), :ok, :ip, :ap, :note)
        """),
        {"cid": str(call_id) if call_id else None,
         "prov": telephony.PROVIDER or "unknown",
         "sid": session_id, "et": (event_type or "")[:64] or None,
         "raw": json.dumps(payload, default=str)[:200000],
         "ok": secret_ok, "ip": (remote_ip or "")[:64] or None,
         "ap": applied, "note": (note or "")[:255] or None},
    )


@router.post("/{secret}/voice")
async def voice_callback(
    secret: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    forwarded = request.headers.get("x-forwarded-for", "")
    remote_ip = forwarded.split(",")[0].strip() or (
        request.client.host if request.client else "")

    payload = await _read_payload(request)

    if not _secret_ok(secret):
        await _record(session, call_id=None, session_id=None,
                      event_type="REJECTED", payload=payload, secret_ok=False,
                      remote_ip=remote_ip, note="secret mismatch")
        await session.commit()
        raise HTTPException(status_code=404, detail="Not Found")

    parsed = telephony.parse_callback(payload)
    session_id = parsed["session_id"]

    call = None
    if session_id:
        call = (await session.execute(
            text("""SELECT id, status, contact_phone, duration_source
                      FROM call_logs
                     WHERE provider_reference = :sid
                     FOR UPDATE"""),
            {"sid": session_id},
        )).mappings().first()

    if call is None:
        # A billed call we cannot match is exactly what someone will need to
        # investigate later, so it is kept rather than dropped.
        await _record(session, call_id=None, session_id=session_id,
                      event_type=parsed["state"] or "UNMATCHED",
                      payload=payload, secret_ok=True, remote_ip=remote_ip,
                      note="no call row for this session")
        await session.commit()
        return Response(content=telephony.reject_instruction(),
                        media_type="application/xml")

    # --- the staff member answered: dial the customer and bridge -----------
    if not parsed["finished"]:
        await session.execute(
            text("""UPDATE call_logs SET bridge_state = 'BRIDGING',
                           updated_at = NOW() WHERE id = :id"""),
            {"id": str(call["id"])})
        await _record(session, call_id=call["id"], session_id=session_id,
                      event_type="ANSWER", payload=payload, secret_ok=True,
                      remote_ip=remote_ip, applied=True)
        await session.commit()
        return Response(
            content=telephony.bridge_instruction(call["contact_phone"]),
            media_type="application/xml")

    # --- the call ended: record what the network says ----------------------
    duration = parsed["duration_seconds"]
    if duration is None:
        await _record(session, call_id=call["id"], session_id=session_id,
                      event_type=parsed["state"] or "END", payload=payload,
                      secret_ok=True, remote_ip=remote_ip,
                      note="completion carried no duration")
        await session.commit()
        return Response(status_code=200, content="")

    await session.execute(
        text("""
            UPDATE call_logs
               SET status = 'COMPLETED',
                   bridge_state = 'COMPLETED',
                   ended_at = COALESCE(ended_at, NOW()),
                   duration_seconds = :dur,
                   duration_source = 'VERIFIED',
                   cost = :cost,
                   cost_currency = :cur,
                   bridge_failure_reason = :hangup,
                   updated_at = NOW()
             WHERE id = :id
        """),
        {"dur": duration,
         "cost": str(parsed["cost"]) if parsed["cost"] is not None else None,
         "cur": parsed["currency"],
         "hangup": parsed["hangup_cause"],
         "id": str(call["id"])},
    )
    await _record(session, call_id=call["id"], session_id=session_id,
                  event_type=parsed["state"] or "COMPLETED", payload=payload,
                  secret_ok=True, remote_ip=remote_ip, applied=True)
    await session.commit()
    return Response(status_code=200, content="")
