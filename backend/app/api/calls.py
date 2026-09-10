"""Company call log.

Staff pick a contact -- from the customer database or from their phone -- tap
Call, and the app hands off to the dialer or to WhatsApp. When they come back,
the app offers the time they were away and they confirm or correct it.

THE HONEST LIMIT, RESTATED HERE BECAUSE IT SHAPES EVERY ENDPOINT
---------------------------------------------------------------
The browser cannot see call state. `start` records the intent to call and
`complete` records what the staff member says happened. The duration is
therefore an ESTIMATE with a stated provenance, never a measurement of the
call itself. Every response carries `duration_source` so no screen can render
a number without saying where it came from.

The one thing the server does NOT trust is the clock on the phone. `complete`
takes a duration in seconds, not an end timestamp, and the server clamps it to
the wall-clock interval it observed between start and completion. A device
whose clock is wrong -- or set wrong -- cannot inflate a call beyond the time
that actually elapsed on the server.
"""
from __future__ import annotations

import csv
import io
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_authenticated_user
from app.db import get_session
from app.models import User
from app.services import wallet as wsvc

router = APIRouter(prefix="/api/calls", tags=["Call Log"])

# A call left open this long was almost certainly abandoned -- the staff member
# closed the tab, the phone died, the browser was killed. Completing it with a
# measured duration would invent a very long call, so it is capped and marked.
MAX_REASONABLE_CALL_SECONDS = 4 * 60 * 60


class CallStart(BaseModel):
    contact_phone: str = Field(..., min_length=4, max_length=64)
    contact_name: Optional[str] = Field(None, max_length=255)
    customer_id: Optional[UUID] = None
    contact_source: str = Field("MANUAL")
    channel: str = Field("PHONE")
    purpose: Optional[str] = Field(None, max_length=255)
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class CallComplete(BaseModel):
    # Seconds, not an end timestamp: see the module docstring.
    duration_seconds: int = Field(..., ge=0)
    duration_source: str = Field("CONFIRMED")
    measured_seconds: Optional[int] = Field(None, ge=0)
    outcome: Optional[str] = Field(None, max_length=32)
    purpose: Optional[str] = Field(None, max_length=255)
    notes: Optional[str] = None


def _reference() -> str:
    return f"CALL-{date.today().strftime('%Y%m')}-{secrets.token_hex(4).upper()}"


_DIGITS = re.compile(r"[^0-9+]")


def normalise_phone(raw: str) -> str:
    """Strip formatting so the same number is one number.

    Contacts arrive from three places -- the customer table, the phone's
    address book, and typing -- and the same line shows up as
    '0803 123 4567', '+2348031234567' and '234-803-123-4567'. Without
    normalising, per-customer call history silently splits three ways.

    Nigerian local format (0XXXXXXXXXX) is promoted to +234 because that is
    what the phone book and WhatsApp both use.
    """
    s = _DIGITS.sub("", (raw or "").strip())
    if s.startswith("00"):
        s = "+" + s[2:]
    if s.startswith("0") and len(s) == 11:
        s = "+234" + s[1:]
    elif s.startswith("234") and len(s) == 13:
        s = "+" + s
    return s


def _rows(items) -> list[dict]:
    from decimal import Decimal
    return [{k: (str(v) if isinstance(v, Decimal) else v)
             for k, v in dict(r).items()} for r in items]


@router.get("/contacts")
async def search_contacts(
    q: str = Query("", max_length=100),
    limit: int = Query(25, ge=1, le=100),
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Customers with a phone number, for the in-app contact picker.

    Only customers who can actually be called are returned -- offering a name
    with no number just produces a dead tap.
    """
    where = ["c.is_active IS NOT FALSE", "c.phone IS NOT NULL", "c.phone <> ''"]
    params: dict = {"lim": limit}
    if q.strip():
        where.append("(c.name ILIKE :q OR c.phone ILIKE :q "
                     "OR c.customer_code ILIKE :q)")
        params["q"] = f"%{q.strip()}%"

    rows = (await session.execute(
        text(f"""
            SELECT c.id, c.customer_code, c.name, c.phone,
                   (SELECT MAX(l.started_at) FROM call_logs l
                     WHERE l.customer_id = c.id) AS last_called
              FROM customers c
             WHERE {' AND '.join(where)}
             ORDER BY last_called DESC NULLS LAST, c.name
             LIMIT :lim
        """), params,
    )).mappings().all()
    return {"contacts": _rows(rows)}


@router.post("", status_code=201)
async def start_call(
    body: CallStart,
    request: Request,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Record that a call is being placed. Returns the id to complete with.

    The caller is always the authenticated user. A client cannot log a call on
    someone else's behalf -- that would make the whole log unattributable.
    """
    if body.channel not in ("PHONE", "WHATSAPP"):
        raise HTTPException(status_code=400, detail="Unknown call channel.")
    if body.contact_source not in ("CUSTOMER", "PHONE_CONTACT", "MANUAL"):
        raise HTTPException(status_code=400, detail="Unknown contact source.")

    phone = normalise_phone(body.contact_phone)
    if len(phone) < 4:
        raise HTTPException(
            status_code=400, detail="That does not look like a phone number.")

    name = body.contact_name
    customer_id = body.customer_id
    if customer_id is not None:
        row = (await session.execute(
            text("SELECT name, phone FROM customers WHERE id = :c"),
            {"c": str(customer_id)},
        )).mappings().first()
        if row is None:
            raise HTTPException(status_code=404, detail="Customer not found.")
        name = name or row["name"]

    call_id = uuid4()
    reference = _reference()
    await session.execute(
        text("""
            INSERT INTO call_logs
                (id, call_reference, user_id, department, customer_id,
                 contact_name, contact_phone, contact_source, channel,
                 direction, status, purpose, latitude, longitude, user_agent)
            VALUES (:id, :ref, :u, :dept, :cust, :nm, :ph, :src, :ch,
                    'OUTBOUND', 'IN_PROGRESS', :p, :lat, :lng, :ua)
        """),
        {"id": str(call_id), "ref": reference, "u": str(user.id),
         "dept": user.department, "cust": str(customer_id) if customer_id else None,
         "nm": name, "ph": phone, "src": body.contact_source,
         "ch": body.channel, "p": body.purpose,
         "lat": body.latitude, "lng": body.longitude,
         "ua": (request.headers.get("user-agent") or "")[:500] or None},
    )
    await session.commit()
    return {
        "id": str(call_id),
        "call_reference": reference,
        "contact_phone": phone,
        "contact_name": name,
        "channel": body.channel,
        "status": "IN_PROGRESS",
    }


@router.post("/{call_id}/complete")
async def complete_call(
    call_id: UUID,
    body: CallComplete,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Close a call with what the staff member says its duration was.

    The reported duration is clamped to the interval the SERVER observed
    between start and completion. The phone's clock is not trusted: it can be
    wrong by accident or on purpose, and the server's own elapsed time is a
    ceiling no client can argue with.
    """
    if body.duration_source not in ("MEASURED", "CONFIRMED", "MANUAL",
                                    "UNKNOWN"):
        # VERIFIED is deliberately absent: it belongs to a telephony provider's
        # record, not to anything a browser can assert.
        raise HTTPException(
            status_code=400,
            detail="A duration may only be MEASURED, CONFIRMED, MANUAL or "
                   "UNKNOWN.")

    call = (await session.execute(
        text("SELECT * FROM call_logs WHERE id = :id FOR UPDATE"),
        {"id": str(call_id)},
    )).mappings().first()
    if call is None:
        raise HTTPException(status_code=404, detail="Call not found.")
    if str(call["user_id"]) != str(user.id):
        raise HTTPException(
            status_code=403, detail="You can only complete your own calls.")
    if call["status"] != "IN_PROGRESS":
        raise HTTPException(
            status_code=400,
            detail=f"This call is already {call['status'].lower()}.")

    now = datetime.now(timezone.utc)
    elapsed = int((now - call["started_at"]).total_seconds())
    duration = min(int(body.duration_seconds), max(elapsed, 0))
    source = body.duration_source
    if duration > MAX_REASONABLE_CALL_SECONDS:
        duration = MAX_REASONABLE_CALL_SECONDS
        source = "UNKNOWN"

    measured = body.measured_seconds
    if measured is not None:
        measured = min(int(measured), max(elapsed, 0))

    await session.execute(
        text("""
            UPDATE call_logs
               SET status = 'COMPLETED', ended_at = :now,
                   duration_seconds = :dur, duration_source = :src,
                   measured_seconds = :meas,
                   outcome = :out, notes = :notes,
                   purpose = COALESCE(:p, purpose), updated_at = NOW()
             WHERE id = :id
        """),
        {"now": now, "dur": duration, "src": source, "meas": measured,
         "out": body.outcome, "notes": body.notes, "p": body.purpose,
         "id": str(call_id)},
    )
    await session.commit()
    return {
        "id": str(call_id),
        "call_reference": call["call_reference"],
        "status": "COMPLETED",
        "duration_seconds": duration,
        "duration_source": source,
        "clamped": duration < int(body.duration_seconds),
    }


@router.post("/{call_id}/cancel")
async def cancel_call(
    call_id: UUID,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """The call was not placed after all. The attempt stays on the record."""
    result = await session.execute(
        text("""UPDATE call_logs SET status = 'CANCELLED', ended_at = NOW(),
                       duration_seconds = 0, duration_source = 'UNKNOWN',
                       updated_at = NOW()
                 WHERE id = :id AND user_id = :u AND status = 'IN_PROGRESS'"""),
        {"id": str(call_id), "u": str(user.id)},
    )
    if result.rowcount == 0:
        raise HTTPException(
            status_code=404,
            detail="No call of yours is in progress with that id.")
    await session.commit()
    return {"id": str(call_id), "status": "CANCELLED"}


@router.get("/me")
async def my_calls(
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.execute(
        text("""
            SELECT l.id, l.call_reference, l.contact_name, l.contact_phone,
                   l.channel, l.started_at, l.duration_seconds,
                   l.duration_source, l.status, l.purpose, l.outcome,
                   c.name AS customer_name
              FROM call_logs l
              LEFT JOIN customers c ON c.id = l.customer_id
             WHERE l.user_id = :u
             ORDER BY l.started_at DESC
             LIMIT :lim
        """), {"u": str(user.id), "lim": limit},
    )).mappings().all()

    today = (await session.execute(
        text("""SELECT COUNT(*) AS calls,
                       COALESCE(SUM(duration_seconds), 0) AS seconds
                  FROM call_logs
                 WHERE user_id = :u AND status = 'COMPLETED'
                   AND started_at >= date_trunc('day', NOW())"""),
        {"u": str(user.id)},
    )).mappings().first()

    return {
        "calls": _rows(rows),
        "today": {"calls": today["calls"], "seconds": int(today["seconds"])},
    }


@router.get("")
async def list_calls(
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    user_id: Optional[UUID] = None,
    customer_id: Optional[UUID] = None,
    department: Optional[str] = None,
    limit: int = Query(200, ge=1, le=1000),
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Company-wide call log. Approvers and administrators only.

    Reuses the wallet module's capability check rather than inventing a second
    notion of "management", so who may see the call log and who may approve
    spending stay one decision.
    """
    caps = await wsvc.user_capabilities(session, user)
    if not (caps["is_admin"] or caps["can_approve"] or caps["can_fund"]):
        raise HTTPException(
            status_code=403,
            detail="The company call log is for approvers and administrators. "
                   "Your own calls are at /api/calls/me.")

    where = ["1 = 1"]
    params: dict = {"lim": limit}
    if date_from:
        where.append("l.started_at >= :df")
        params["df"] = date_from
    if date_to:
        where.append("l.started_at < :dt")
        params["dt"] = date_to + timedelta(days=1)
    if user_id:
        where.append("l.user_id = :uid")
        params["uid"] = str(user_id)
    if customer_id:
        where.append("l.customer_id = :cid")
        params["cid"] = str(customer_id)
    if department:
        where.append("l.department = :dept")
        params["dept"] = department

    rows = (await session.execute(
        text(f"""
            SELECT l.id, l.call_reference, l.contact_name, l.contact_phone,
                   l.channel, l.started_at, l.ended_at, l.duration_seconds,
                   l.duration_source, l.measured_seconds, l.status, l.purpose,
                   l.outcome, l.department, u.full_name AS caller,
                   c.name AS customer_name, c.customer_code
              FROM call_logs l
              JOIN users u ON u.id = l.user_id
              LEFT JOIN customers c ON c.id = l.customer_id
             WHERE {' AND '.join(where)}
             ORDER BY l.started_at DESC
             LIMIT :lim
        """), params,
    )).mappings().all()
    return {"calls": _rows(rows)}


@router.get("/dashboard")
async def dashboard(
    days: int = Query(30, ge=1, le=365),
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    caps = await wsvc.user_capabilities(session, user)
    if not (caps["is_admin"] or caps["can_approve"] or caps["can_fund"]):
        raise HTTPException(
            status_code=403,
            detail="The call dashboard is for approvers and administrators.")

    since = datetime.now(timezone.utc) - timedelta(days=days)

    totals = (await session.execute(
        text("""
            SELECT COUNT(*) FILTER (WHERE status = 'COMPLETED') AS completed,
                   COUNT(*) FILTER (WHERE status = 'CANCELLED') AS cancelled,
                   COUNT(*) FILTER (WHERE status = 'IN_PROGRESS') AS open,
                   COALESCE(SUM(duration_seconds) FILTER (
                       WHERE status = 'COMPLETED'), 0) AS seconds,
                   COUNT(DISTINCT customer_id) FILTER (
                       WHERE customer_id IS NOT NULL) AS customers
              FROM call_logs WHERE started_at >= :s
        """), {"s": since},
    )).mappings().first()

    # How much of the reported time is a soft figure. If most durations are
    # MANUAL, the totals above are opinion, and whoever reads this dashboard
    # should be told that rather than left to assume otherwise.
    provenance = (await session.execute(
        text("""SELECT duration_source, COUNT(*) AS calls,
                       COALESCE(SUM(duration_seconds), 0) AS seconds
                  FROM call_logs
                 WHERE started_at >= :s AND status = 'COMPLETED'
                 GROUP BY duration_source"""), {"s": since},
    )).mappings().all()

    by_staff = (await session.execute(
        text("""
            SELECT u.full_name AS caller, l.department,
                   COUNT(*) AS calls,
                   COALESCE(SUM(l.duration_seconds), 0) AS seconds,
                   COUNT(DISTINCT l.customer_id) AS customers
              FROM call_logs l JOIN users u ON u.id = l.user_id
             WHERE l.started_at >= :s AND l.status = 'COMPLETED'
             GROUP BY u.full_name, l.department
             ORDER BY seconds DESC
        """), {"s": since},
    )).mappings().all()

    by_customer = (await session.execute(
        text("""
            SELECT c.name AS customer, c.customer_code,
                   COUNT(*) AS calls,
                   COALESCE(SUM(l.duration_seconds), 0) AS seconds,
                   MAX(l.started_at) AS last_called
              FROM call_logs l JOIN customers c ON c.id = l.customer_id
             WHERE l.started_at >= :s AND l.status = 'COMPLETED'
             GROUP BY c.name, c.customer_code
             ORDER BY seconds DESC LIMIT 25
        """), {"s": since},
    )).mappings().all()

    return {
        "period_days": days,
        "totals": {
            "completed": totals["completed"],
            "cancelled": totals["cancelled"],
            "in_progress": totals["open"],
            "seconds": int(totals["seconds"]),
            "customers_reached": totals["customers"],
        },
        "provenance": _rows(provenance),
        "by_staff": _rows(by_staff),
        "by_customer": _rows(by_customer),
    }


@router.get("/reports/calls.csv")
async def export_calls(
    date_from: date,
    date_to: date,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    caps = await wsvc.user_capabilities(session, user)
    if not (caps["is_admin"] or caps["can_approve"] or caps["can_fund"]):
        raise HTTPException(
            status_code=403, detail="You cannot export the company call log.")
    if date_to < date_from:
        raise HTTPException(
            status_code=400, detail="The end date precedes the start date.")

    rows = (await session.execute(
        text("""
            SELECT l.call_reference, l.started_at, u.full_name AS caller,
                   l.department, COALESCE(c.name, l.contact_name) AS contact,
                   l.contact_phone, l.channel, l.duration_seconds,
                   l.duration_source, l.status, l.purpose, l.outcome
              FROM call_logs l
              JOIN users u ON u.id = l.user_id
              LEFT JOIN customers c ON c.id = l.customer_id
             WHERE l.started_at >= :s AND l.started_at < :e
             ORDER BY l.started_at
        """), {"s": date_from, "e": date_to + timedelta(days=1)},
    )).mappings().all()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Reference", "Started", "Caller", "Department", "Contact",
                "Number", "Channel", "Duration (s)", "Duration source",
                "Status", "Purpose", "Outcome"])
    for r in rows:
        w.writerow([r["call_reference"], r["started_at"], r["caller"],
                    r["department"], r["contact"], r["contact_phone"],
                    r["channel"], r["duration_seconds"], r["duration_source"],
                    r["status"], r["purpose"], r["outcome"]])
    name = f"calls-{date_from}-to-{date_to}.csv"
    return Response(
        content=buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---------------------------------------------------------------------------
# Click-to-call bridging
#
# The provider rings the staff member, then dials the customer and joins the
# legs. The duration and cost come back from the network, so they are the only
# figures in this module nobody in the company can influence.
# ---------------------------------------------------------------------------

from app.services import telephony  # noqa: E402  (kept beside its endpoints)


class BridgeStart(BaseModel):
    contact_phone: str = Field(..., min_length=4, max_length=64)
    contact_name: Optional[str] = Field(None, max_length=255)
    customer_id: Optional[UUID] = None
    contact_source: str = Field("MANUAL")
    purpose: Optional[str] = Field(None, max_length=255)
    # Overrides the number on the user's profile for this call only -- for a
    # staff member on a temporary line. Never stored back to the profile.
    staff_phone: Optional[str] = Field(None, max_length=64)


@router.get("/config")
async def call_config(
    user: User = Depends(require_authenticated_user),
):
    """What the client may offer. Asked before any Call button is drawn.

    Without this the UI would show a 'company line' option that fails on tap
    wherever bridging is not configured, which is worse than not offering it.
    """
    ok, reason = telephony.configured()
    return {
        "bridging_available": ok,
        "bridging_unavailable_reason": None if ok else reason,
        "provider": telephony.PROVIDER or None,
        "your_phone": user.phone,
    }


@router.post("/bridge", status_code=201)
async def start_bridged_call(
    body: BridgeStart,
    request: Request,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Place a bridged call. The network times it; nobody here does."""
    ok, reason = telephony.configured()
    if not ok:
        raise HTTPException(status_code=503, detail=reason)

    staff_phone = normalise_phone(body.staff_phone or user.phone or "")
    if len(staff_phone) < 8:
        raise HTTPException(
            status_code=400,
            detail=("Your profile has no usable phone number, so there is "
                    "nothing for the system to ring. Add one under Settings, "
                    "or type the line you are on."))

    customer_phone = normalise_phone(body.contact_phone)
    if len(customer_phone) < 8:
        raise HTTPException(
            status_code=400, detail="That does not look like a phone number.")
    if customer_phone == staff_phone:
        raise HTTPException(
            status_code=400,
            detail="That is your own number -- the call would bridge to "
                   "itself.")

    name = body.contact_name
    if body.customer_id is not None:
        row = (await session.execute(
            text("SELECT name FROM customers WHERE id = :c"),
            {"c": str(body.customer_id)},
        )).mappings().first()
        if row is None:
            raise HTTPException(status_code=404, detail="Customer not found.")
        name = name or row["name"]

    # The row is written BEFORE the provider is asked, so a call that connects
    # while our response is in flight still has somewhere for the callback to
    # land. An orphaned QUEUED row is a far better failure than a billed call
    # with no record.
    call_id = uuid4()
    reference = _reference()
    await session.execute(
        text("""
            INSERT INTO call_logs
                (id, call_reference, user_id, department, customer_id,
                 contact_name, contact_phone, contact_source, channel,
                 direction, status, purpose, staff_phone, bridge_state,
                 provider, user_agent)
            VALUES (:id, :ref, :u, :dept, :cust, :nm, :ph, :src, 'BRIDGE',
                    'OUTBOUND', 'IN_PROGRESS', :p, :sp, 'QUEUED', :prov, :ua)
        """),
        {"id": str(call_id), "ref": reference, "u": str(user.id),
         "dept": user.department,
         "cust": str(body.customer_id) if body.customer_id else None,
         "nm": name, "ph": customer_phone, "src": body.contact_source,
         "p": body.purpose, "sp": staff_phone,
         "prov": telephony.PROVIDER,
         "ua": (request.headers.get("user-agent") or "")[:500] or None},
    )
    await session.commit()

    try:
        placed = await telephony.place_bridged_call(
            staff_phone=staff_phone, customer_phone=customer_phone)
    except telephony.TelephonyError as exc:
        await session.execute(
            text("""UPDATE call_logs
                       SET status='CANCELLED', bridge_state='FAILED',
                           bridge_failure_reason=:r, duration_seconds=0,
                           duration_source='UNKNOWN', ended_at=NOW(),
                           updated_at=NOW()
                     WHERE id = :id"""),
            {"r": str(exc)[:255], "id": str(call_id)})
        await session.commit()
        raise HTTPException(status_code=502, detail=str(exc))

    await session.execute(
        text("""UPDATE call_logs
                   SET provider_reference = :ref, bridge_state = 'RINGING_STAFF',
                       updated_at = NOW()
                 WHERE id = :id"""),
        {"ref": placed["session_id"], "id": str(call_id)})
    await session.commit()

    return {
        "id": str(call_id),
        "call_reference": reference,
        "contact_name": name,
        "contact_phone": customer_phone,
        "staff_phone": staff_phone,
        "channel": "BRIDGE",
        "status": "IN_PROGRESS",
        "message": ("Your phone is ringing. Answer it and you will be "
                    "connected to the customer."),
    }


@router.get("/{call_id}/status")
async def call_status(
    call_id: UUID,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Poll a bridged call while waiting for the network's record."""
    row = (await session.execute(
        text("""SELECT user_id, status, bridge_state, duration_seconds,
                       duration_source, bridge_failure_reason, cost,
                       cost_currency
                  FROM call_logs WHERE id = :id"""),
        {"id": str(call_id)},
    )).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Call not found.")
    if str(row["user_id"]) != str(user.id):
        raise HTTPException(status_code=403, detail="That is not your call.")
    return {
        "status": row["status"],
        "bridge_state": row["bridge_state"],
        "duration_seconds": row["duration_seconds"],
        "duration_source": row["duration_source"],
        "failure_reason": row["bridge_failure_reason"],
        "cost": str(row["cost"]) if row["cost"] is not None else None,
        "cost_currency": row["cost_currency"],
    }


@router.get("/provider/events")
async def provider_events(
    limit: int = Query(100, ge=1, le=500),
    unmatched_only: bool = False,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Raw provider callbacks, for reconciling billed minutes. Admins only.

    This is where you look when the provider billed a call that shows no
    duration here, or when they renamed a field and the parser quietly stopped
    recognising it. The raw bodies are kept precisely so that is recoverable.
    """
    if user.role != "admin":
        raise HTTPException(
            status_code=403, detail="Provider events are for administrators.")
    where = "WHERE call_id IS NULL" if unmatched_only else ""
    rows = (await session.execute(
        text(f"""SELECT id, call_id, provider, session_id, event_type,
                        secret_ok, remote_ip, applied, note, received_at, raw
                   FROM call_provider_events {where}
                  ORDER BY received_at DESC LIMIT :lim"""),
        {"lim": limit},
    )).mappings().all()
    return {"events": _rows(rows)}
