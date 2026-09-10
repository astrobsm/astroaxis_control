"""Click-to-call bridging: place a call the network can vouch for.

THE FLOW
--------
    staff taps Call
        -> we ask the provider to ring the STAFF member's own phone
        -> staff answers
        -> the provider asks us what to do; we say "dial the customer"
        -> the provider rings the customer and bridges the two legs
        -> when it ends the provider tells us the duration and the cost

The staff member's phone rings first on purpose. It costs a leg, but it means
the customer's phone only rings once someone is actually there to talk -- the
alternative rings the customer into silence while the staff member's phone is
still connecting, which is how a "spam call" reputation gets built.

WHY THIS IS WORTH THE MONEY
---------------------------
The duration comes from the carrier. Not from a browser timing how long a
phone was face-down, not from what someone typed. It is the only figure in the
call log that nobody in the company can influence, which is precisely why the
database refuses `duration_source = 'VERIFIED'` unless a provider reference is
attached to it.

OFF UNLESS CONFIGURED
---------------------
`configured()` is false until the environment carries a provider's credentials,
and every screen asks before offering the option. An unconfigured deployment
keeps the existing tel: hand-off and its honest estimate. Turning bridging on
is a deliberate act with a per-minute bill attached, never a side effect of
deploying this code.

A NOTE ON THE PROVIDER'S FIELD NAMES
------------------------------------
Callback payloads are parsed leniently -- several plausible spellings are
accepted for each field -- and the raw body is stored in call_provider_events
whether or not it parsed. Provider APIs change, and a callback we failed to
understand is still a fact about a billed call. Verify the field names against
the provider's current documentation when you switch this on; the stored raw
payloads are how you check, and adjusting the parser afterwards loses nothing.
"""
from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROVIDER = (os.getenv("TELEPHONY_PROVIDER", "") or "").strip().lower()

AT_USERNAME = os.getenv("AT_USERNAME", "")
AT_API_KEY = os.getenv("AT_API_KEY", "")
# The number the customer sees. Must be a voice number bought from the provider.
AT_CALLER_ID = os.getenv("AT_CALLER_ID", "")
AT_BASE_URL = os.getenv(
    "AT_VOICE_BASE_URL", "https://voice.africastalking.com")

# Shared secret that must appear in the callback URL. The provider cannot hold
# a bearer token, so the URL itself carries the credential -- which means it
# must be long, random, and treated as a password.
WEBHOOK_SECRET = os.getenv("TELEPHONY_WEBHOOK_SECRET", "")

# Public base URL of THIS application, so the provider can reach the callbacks.
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL", "") or "").rstrip("/")

_SUPPORTED = {"africastalking"}


def configured() -> tuple[bool, Optional[str]]:
    """Is bridging usable? Returns (ok, reason_if_not).

    The reason is surfaced to administrators verbatim, because "call bridging
    is unavailable" with no explanation sends someone reading source code.
    """
    if not PROVIDER:
        return False, ("No telephony provider is configured. Set "
                       "TELEPHONY_PROVIDER to enable call bridging.")
    if PROVIDER not in _SUPPORTED:
        return False, (f"Telephony provider {PROVIDER!r} is not supported. "
                       f"Supported: {', '.join(sorted(_SUPPORTED))}.")
    missing = [n for n, v in (
        ("AT_USERNAME", AT_USERNAME), ("AT_API_KEY", AT_API_KEY),
        ("AT_CALLER_ID", AT_CALLER_ID)) if not v]
    if missing:
        return False, f"Missing configuration: {', '.join(missing)}."
    if not WEBHOOK_SECRET or len(WEBHOOK_SECRET) < 24:
        return False, ("TELEPHONY_WEBHOOK_SECRET must be set and at least 24 "
                       "characters. It is the only thing protecting the "
                       "callback endpoint; generate it with "
                       "`python -c \"import secrets; "
                       "print(secrets.token_urlsafe(32))\"`.")
    if not PUBLIC_BASE_URL.startswith("https://"):
        return False, ("PUBLIC_BASE_URL must be set to this application's "
                       "public https:// address so the provider can deliver "
                       "call callbacks.")
    return True, None


def callback_url() -> str:
    return f"{PUBLIC_BASE_URL}/api/telephony/{WEBHOOK_SECRET}/voice"


# ---------------------------------------------------------------------------
# Placing the call
# ---------------------------------------------------------------------------

class TelephonyError(RuntimeError):
    """The provider refused or could not be reached. Carries a usable message."""


async def place_bridged_call(*, staff_phone: str, customer_phone: str) -> dict:
    """Ring the staff member; the answer callback bridges them to the customer.

    Returns {'session_id': str, 'state': str, 'raw': dict}. Raises
    TelephonyError with a message fit to show a user.
    """
    ok, reason = configured()
    if not ok:
        raise TelephonyError(reason)
    if PROVIDER == "africastalking":
        return await _at_place(staff_phone, customer_phone)
    raise TelephonyError(f"Unsupported provider {PROVIDER!r}.")


async def _at_place(staff_phone: str, customer_phone: str) -> dict:
    payload = {
        "username": AT_USERNAME,
        "from": AT_CALLER_ID,
        "to": staff_phone,
        # Echoed back on the answer callback so we know which customer this
        # leg is for without a second lookup racing the callback.
        "clientRequestId": customer_phone,
    }
    headers = {"apiKey": AT_API_KEY, "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(f"{AT_BASE_URL}/call", data=payload,
                                     headers=headers)
    except httpx.HTTPError as exc:
        raise TelephonyError(
            f"Could not reach the telephony provider: {exc}") from exc

    if resp.status_code >= 400:
        raise TelephonyError(
            f"The telephony provider rejected the call "
            f"(HTTP {resp.status_code}): {resp.text[:300]}")

    try:
        body = resp.json()
    except ValueError:
        raise TelephonyError(
            f"The telephony provider returned an unreadable response: "
            f"{resp.text[:300]}")

    entries = body.get("entries") or []
    if not entries:
        raise TelephonyError(
            f"The provider accepted the request but queued no call: "
            f"{body.get('errorMessage') or body}")

    entry = entries[0]
    status = str(entry.get("status") or "").strip()
    session = entry.get("sessionId") or entry.get("sessionID")
    # AT reports per-destination failures inside a 200 response, so a
    # successful HTTP status is not a successful call.
    if not session or status.lower() not in ("queued", "success", "ok"):
        raise TelephonyError(
            f"The provider could not start the call: "
            f"{entry.get('errorMessage') or status or entry}")
    return {"session_id": str(session), "state": "QUEUED", "raw": body}


def bridge_instruction(customer_phone: str) -> str:
    """XML telling the provider to dial the customer and join the legs.

    Returned when the provider calls back on answer. `record` is deliberately
    off: recording a conversation raises consent obligations under the NDPA
    that a duration figure does not, and this module exists to measure length,
    not to listen.
    """
    safe = "".join(c for c in (customer_phone or "") if c.isdigit() or c == "+")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response>'
        f'<Dial phoneNumbers="{safe}" record="false" sequential="true"/>'
        '</Response>'
    )


def reject_instruction() -> str:
    """Hang up politely when we cannot work out who to bridge to."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Response><Reject/></Response>'
    )


# ---------------------------------------------------------------------------
# Reading callbacks
# ---------------------------------------------------------------------------

def _first(payload: dict, *names: str) -> Optional[Any]:
    for n in names:
        if n in payload and payload[n] not in (None, ""):
            return payload[n]
    return None


def _as_int(value) -> Optional[int]:
    try:
        return max(0, int(float(str(value).strip())))
    except (TypeError, ValueError):
        return None


def _as_decimal(value) -> Optional[Decimal]:
    """Parse a money figure, tolerating 'NGN 1.5000' as well as '1.5'."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    parts = text.split()
    if len(parts) == 2:
        text = parts[1]
    try:
        return Decimal(text).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def parse_callback(payload: dict) -> dict:
    """Normalise a provider callback into the fields the call log needs.

    Deliberately lenient about names: providers rename fields between API
    versions, and the alternative to accepting several spellings is silently
    losing billed minutes when they do. Whatever this returns, the raw payload
    is stored alongside it.
    """
    session = _first(payload, "sessionId", "sessionID", "session_id",
                     "CallSessionId")
    state = str(_first(payload, "callSessionState", "status", "state",
                       "CallSessionState") or "").strip()
    duration = _as_int(_first(payload, "durationInSeconds", "duration",
                              "callDuration", "DurationInSeconds"))
    cost = _as_decimal(_first(payload, "amount", "cost", "Amount"))
    currency = _first(payload, "currencyCode", "currency", "CurrencyCode")
    is_active = str(_first(payload, "isActive", "IsActive") or "").strip()
    hangup = _first(payload, "hangupCause", "callHangupCause", "reason")

    # A call is finished when the provider says the session ended, or says the
    # leg is no longer active. Different providers use different signals, and
    # treating a live call as finished would lock in a duration mid-call.
    finished = (
        state.lower() in ("completed", "ended", "finished", "terminated")
        or is_active in ("0", "false", "False")
    )

    return {
        "session_id": str(session) if session else None,
        "state": state or None,
        "duration_seconds": duration,
        "cost": cost,
        "currency": str(currency) if currency else None,
        "finished": finished,
        "hangup_cause": str(hangup) if hangup else None,
        "client_request_id": _first(payload, "clientRequestId",
                                    "clientDialedNumber"),
        "direction": _first(payload, "direction", "Direction"),
        "is_answer_request": bool(
            _first(payload, "isActive", "IsActive") in ("1", "true", "True")
            and duration is None),
    }
