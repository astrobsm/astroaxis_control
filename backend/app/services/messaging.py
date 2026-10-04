"""The outbox, and the rules that decide whether a message may leave.

THERE IS NO SENDER IN THIS MODULE
=================================
`enqueue` puts a row in a table. Nothing in this codebase reads that table and
transmits anything, and nothing will until a provider is chosen and the
question in the audit document is answered: does this company accept that an
automated system will message customers' personal phones without a member of
staff reading each one first?

That is deliberate. The rules below are easier to get right while nothing can
actually go out, and a half-built sender is the most dangerous object in a
system like this.

WHAT ENQUEUE ACTUALLY DOES
==========================
It decides. Every message is checked against the master switch, the channel
switch, the customer's consent, the do-not-contact flag and the frequency
caps, and the outcome is written down either way:

    QUEUED   may be sent when a sender exists
    BLOCKED  refused, with the reason recorded

A blocked message is KEPT. Deleting it would erase the evidence that the rules
worked, which is exactly what anyone auditing this would want to see -- and
the first question after an opt-out complaint is "what did the system do when
it was asked to message them?"

TRANSACTIONAL IS NOT PROMOTIONAL
================================
An invoice, a delivery update, an answer to a question the customer asked:
these are about something the customer already did, and marketing consent does
not govern them. Someone who places an order has asked to hear about it.
Frequency caps do not apply either -- a customer with four orders this week
gets four delivery updates.

The master switch still stops everything, including transactional messages,
because a stop button with exceptions is not a stop button.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

CHANNELS = ("WHATSAPP", "SMS", "EMAIL")
CATEGORIES = ("TRANSACTIONAL", "PROMOTIONAL", "SERVICE")
CONSENTS = ("OPTED_IN", "OPTED_OUT", "UNKNOWN")

# What a customer may write to stop hearing from us. Matched on the whole
# message, trimmed: a message that merely CONTAINS "stop" ("please don't stop
# the delivery") is not an opt-out, and treating it as one loses a customer.
OPT_OUT_PHRASES = {
    "stop", "stop all", "unsubscribe", "no more messages", "remove me",
    "do not contact me", "dont contact me", "don't contact me", "opt out",
    "optout", "cancel subscription",
}
OPT_IN_PHRASES = {"start", "subscribe", "opt in", "optin", "yes please"}


def normalise_msisdn(value: Optional[str]) -> Optional[str]:
    """A phone number as a WhatsApp id: digits, no leading zero, country code.

    Nigerian numbers arrive as 08031234567, +2348031234567, 234 803 123 4567
    and worse. All of those are one person, and an inbound message has to
    resolve to exactly one customer.
    """
    digits = re.sub(r"[^0-9]", "", value or "")
    if not digits:
        return None
    if digits.startswith("234"):
        digits = digits[3:]
    digits = digits.lstrip("0")
    if len(digits) < 7:
        return None
    return f"234{digits[-10:]}" if len(digits) >= 10 else None


def classify_reply(body: str) -> Optional[str]:
    """OPTED_OUT, OPTED_IN, or None for an ordinary message."""
    cleaned = re.sub(r"[^a-z' ]", "", (body or "").strip().lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned in OPT_OUT_PHRASES:
        return "OPTED_OUT"
    if cleaned in OPT_IN_PHRASES:
        return "OPTED_IN"
    return None


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

async def get_setting(session: AsyncSession, key: str, default=None):
    row = (await session.execute(
        text("SELECT value, value_type FROM app_settings WHERE key = :k"),
        {"k": key})).mappings().first()
    if row is None:
        return default
    value, kind = row["value"], row["value_type"]
    if value is None:
        return default
    if kind == "BOOLEAN":
        return str(value).strip().lower() in ("true", "1", "yes", "on")
    if kind == "INTEGER":
        try:
            return int(value)
        except ValueError:
            return default
    if kind == "DECIMAL":
        try:
            return Decimal(value)
        except Exception:
            return default
    return value


async def all_settings(session: AsyncSession) -> list:
    rows = (await session.execute(text(
        "SELECT key, value, value_type, description, updated_by_name, "
        "updated_at FROM app_settings ORDER BY key"))).mappings().all()
    return [{"key": r["key"], "value": r["value"], "type": r["value_type"],
             "description": r["description"],
             "updated_by": r["updated_by_name"],
             "updated_at": (r["updated_at"].isoformat()
                            if r["updated_at"] else None)}
            for r in rows]


async def set_setting(session: AsyncSession, *, key: str, value: str,
                      actor=None) -> dict:
    """Change a setting, recording who did it.

    Turning sending on is the single most consequential change anybody can
    make in this module, so it is not a silent UPDATE.
    """
    current = (await session.execute(
        text("SELECT value, value_type FROM app_settings WHERE key = :k "
             "FOR UPDATE"), {"k": key})).mappings().first()
    if current is None:
        raise HTTPException(status_code=404, detail=f"No setting named {key}.")

    if current["value_type"] == "BOOLEAN":
        value = "true" if str(value).strip().lower() in (
            "true", "1", "yes", "on") else "false"
    elif current["value_type"] == "INTEGER":
        try:
            value = str(int(value))
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400, detail=f"{key} must be a whole number.")

    name = ((getattr(actor, "full_name", None)
             or getattr(actor, "username", None)) if actor else None)
    await session.execute(
        text("""UPDATE app_settings
                   SET value = :v, updated_by = :a, updated_by_name = :an,
                       updated_at = NOW()
                 WHERE key = :k"""),
        {"v": value, "k": key,
         "a": str(actor.id) if actor is not None else None, "an": name})
    await session.execute(
        text("""INSERT INTO app_setting_changes
                    (key, old_value, new_value, actor_id, actor_name)
                VALUES (:k, :o, :n, :a, :an)"""),
        {"k": key, "o": current["value"], "n": value,
         "a": str(actor.id) if actor is not None else None, "an": name})

    return {"key": key, "value": value, "previous": current["value"]}


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------

async def set_consent(
    session: AsyncSession, *, customer_id: UUID, consent: str,
    source: str, channel: Optional[str] = None,
    evidence: Optional[str] = None, actor=None,
) -> dict:
    """Record what a customer has agreed to, and how we know."""
    consent = (consent or "").upper()
    if consent not in CONSENTS:
        raise HTTPException(
            status_code=400,
            detail=f"Consent must be one of {', '.join(CONSENTS)}.")
    if not (source or "").strip():
        raise HTTPException(
            status_code=400,
            detail=("A source is required. If nobody can say how we came to "
                    "believe this, it is not evidence of anything."))

    customer = (await session.execute(
        text("SELECT id, name FROM customers WHERE id = :i FOR UPDATE"),
        {"i": str(customer_id)})).mappings().first()
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found.")

    # An opt-out sets do_not_contact as well. Two flags saying different
    # things is how somebody gets messaged after asking us to stop.
    # Decided here rather than in a CASE: binding `consent` both as a column
    # value and inside a comparison leaves asyncpg unable to deduce one type
    # for the parameter, and it refuses the statement.
    await session.execute(
        text("""UPDATE customers
                   SET marketing_consent = :c, consent_at = NOW(),
                       consent_source = :s,
                       do_not_contact = (do_not_contact OR :stop)
                 WHERE id = :i"""),
        {"c": consent, "s": source[:40], "i": str(customer_id),
         "stop": consent == "OPTED_OUT"})

    await session.execute(
        text("""INSERT INTO customer_consent_events
                    (customer_id, consent, source, channel, evidence,
                     actor_id, actor_name)
                VALUES (:i, :c, :s, :ch, :e, :a, :an)"""),
        {"i": str(customer_id), "c": consent, "s": source[:40],
         "ch": channel, "e": evidence,
         "a": str(actor.id) if actor is not None else None,
         "an": ((getattr(actor, "full_name", None)
                 or getattr(actor, "username", None)) if actor else None)})

    return {
        "customer_id": str(customer_id),
        "customer": customer["name"],
        "marketing_consent": consent,
        "note": ("Promotional messages will not be sent to this customer."
                 if consent != "OPTED_IN"
                 else "This customer may receive promotional messages, "
                      "subject to the frequency limits."),
    }


async def consent_history(session: AsyncSession, *, customer_id: UUID) -> list:
    rows = (await session.execute(
        text("""SELECT consent, source, channel, evidence, actor_name,
                       created_at
                  FROM customer_consent_events WHERE customer_id = :i
                 ORDER BY created_at DESC"""),
        {"i": str(customer_id)})).mappings().all()
    return [{"consent": r["consent"], "source": r["source"],
             "channel": r["channel"], "evidence": r["evidence"],
             "actor_name": r["actor_name"],
             "at": r["created_at"].isoformat() if r["created_at"] else None}
            for r in rows]


# ---------------------------------------------------------------------------
# The outbox
# ---------------------------------------------------------------------------

def _key(*parts) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


async def _why_blocked(
    session: AsyncSession, *, customer: dict, category: str, channel: str,
) -> Optional[str]:
    """The first rule this message fails, or None.

    Checked in order of how absolute each rule is, so the reason recorded is
    the most fundamental one rather than whichever happened to be tested last.
    """
    if not await get_setting(session, "OUTBOUND_MESSAGING_ENABLED", False):
        return ("Outbound messaging is switched off for the whole system. "
                "No message is sent on any channel while this is off.")

    if channel == "WHATSAPP" and not await get_setting(
            session, "WHATSAPP_ENABLED", False):
        return "WhatsApp sending is switched off."

    if customer is None:
        return None  # not addressed to a known customer; rules below need one

    if customer["do_not_contact"]:
        return "This customer is marked do-not-contact."

    until = customer["do_not_contact_until"]
    if until and until >= datetime.now(timezone.utc).date():
        return f"This customer asked not to be contacted until {until}."

    # Everything below governs promotional messages only.
    if category != "PROMOTIONAL":
        return None

    if not await get_setting(session, "PROMOTIONAL_MESSAGING_ENABLED", False):
        return "Promotional messaging is switched off."

    if customer["marketing_consent"] != "OPTED_IN":
        return (f"This customer has not opted in to promotional messages "
                f"(consent is {customer['marketing_consent']}).")

    counts = (await session.execute(text("""
        SELECT
          COUNT(*) FILTER (WHERE sent_at > NOW() - INTERVAL '7 days')  AS week,
          COUNT(*) FILTER (WHERE sent_at > NOW() - INTERVAL '30 days') AS month,
          MAX(sent_at) AS last_sent
          FROM outbound_messages
         WHERE customer_id = :c AND category = 'PROMOTIONAL'
           AND status = 'SENT'
    """), {"c": str(customer["id"])})).mappings().first()

    week_cap = await get_setting(session, "PROMOTIONAL_MAX_PER_7_DAYS", 2)
    month_cap = await get_setting(session, "PROMOTIONAL_MAX_PER_30_DAYS", 4)
    min_hours = await get_setting(session, "PROMOTIONAL_MIN_HOURS_BETWEEN", 48)

    if counts["week"] >= week_cap:
        return (f"Frequency limit: {counts['week']} promotional message(s) "
                f"already sent in the last 7 days (limit {week_cap}).")
    if counts["month"] >= month_cap:
        return (f"Frequency limit: {counts['month']} promotional message(s) "
                f"already sent in the last 30 days (limit {month_cap}).")

    if counts["last_sent"] is not None and min_hours:
        hours = ((datetime.now(timezone.utc) - counts["last_sent"])
                 .total_seconds() / 3600)
        if hours < min_hours:
            return (f"Frequency limit: the last promotional message was "
                    f"{int(hours)} hours ago (minimum gap {min_hours}).")

    return None


async def enqueue(
    session: AsyncSession, *, channel: str, to_address: str, body: str,
    reason: str, category: str = "TRANSACTIONAL",
    customer_id: Optional[UUID] = None, template_code: Optional[str] = None,
    opportunity_key: Optional[str] = None,
    idempotency_key: Optional[str] = None, actor=None,
) -> dict:
    """Put a message in the outbox, or record why it was refused.

    Returns the row either way. A caller that wants to know whether the
    message will go reads `status`.
    """
    channel = (channel or "").upper()
    category = (category or "").upper()
    if channel not in CHANNELS:
        raise HTTPException(status_code=400,
                            detail=f"Channel must be one of {', '.join(CHANNELS)}.")
    if category not in CATEGORIES:
        raise HTTPException(status_code=400,
                            detail=f"Category must be one of {', '.join(CATEGORIES)}.")
    if not (body or "").strip():
        raise HTTPException(status_code=400, detail="A message needs a body.")
    if len((reason or "").strip()) < 3:
        raise HTTPException(
            status_code=400,
            detail=("Every message records why it was created. A message "
                    "nobody can explain afterwards should not be sent."))

    customer = None
    if customer_id is not None:
        customer = (await session.execute(text("""
            SELECT id, name, marketing_consent, do_not_contact,
                   do_not_contact_until
              FROM customers WHERE id = :i
        """), {"i": str(customer_id)})).mappings().first()
        if customer is None:
            raise HTTPException(status_code=404, detail="Customer not found.")

    key = idempotency_key or _key(
        channel, to_address, template_code or "", body, opportunity_key or "")

    existing = (await session.execute(
        text("SELECT id, status FROM outbound_messages "
             "WHERE idempotency_key = :k"), {"k": key})).mappings().first()
    if existing:
        return {"id": str(existing["id"]), "status": existing["status"],
                "duplicate": True,
                "note": "An identical message is already in the outbox."}

    blocked = await _why_blocked(
        session, customer=customer, category=category, channel=channel)
    status = "BLOCKED" if blocked else "QUEUED"

    row = (await session.execute(text("""
        INSERT INTO outbound_messages
            (idempotency_key, channel, customer_id, to_address, category,
             template_code, body, reason, opportunity_key, status,
             blocked_reason, created_by, created_by_name)
        VALUES (:k, :ch, :c, :to, :cat, :tpl, :body, :why, :opp, :st, :br,
                :a, :an)
        RETURNING id
    """), {
        "k": key, "ch": channel,
        "c": str(customer_id) if customer_id else None, "to": to_address,
        "cat": category, "tpl": template_code, "body": body,
        "why": reason.strip(), "opp": opportunity_key, "st": status,
        "br": blocked,
        "a": str(actor.id) if actor is not None else None,
        "an": ((getattr(actor, "full_name", None)
                or getattr(actor, "username", None)) if actor else None),
    })).mappings().first()

    return {
        "id": str(row["id"]),
        "status": status,
        "blocked_reason": blocked,
        "duplicate": False,
        "note": (f"Refused and recorded: {blocked}" if blocked else
                 "Queued. Nothing sends it yet -- no provider is configured "
                 "and no sender exists."),
    }


async def outbox(
    session: AsyncSession, *, status: Optional[str] = None, limit: int = 100,
) -> dict:
    clause = "WHERE m.status = :s" if status else ""
    rows = (await session.execute(text(f"""
        SELECT m.id, m.channel, m.category, m.to_address, m.body, m.reason,
               m.status, m.blocked_reason, m.attempts, m.last_error,
               m.created_at, m.sent_at, m.created_by_name, c.name AS customer
          FROM outbound_messages m
     LEFT JOIN customers c ON c.id = m.customer_id
        {clause}
         ORDER BY m.created_at DESC LIMIT :l
    """), {"s": (status or "").upper(), "l": limit})).mappings().all()

    counts = {r["status"]: r["n"] for r in (await session.execute(text(
        "SELECT status, COUNT(*) AS n FROM outbound_messages GROUP BY status"
    ))).mappings().all()}

    return {
        "messages": [{
            "id": str(r["id"]), "channel": r["channel"],
            "category": r["category"], "to": r["to_address"],
            "customer": r["customer"], "body": r["body"], "reason": r["reason"],
            "status": r["status"], "blocked_reason": r["blocked_reason"],
            "attempts": r["attempts"], "last_error": r["last_error"],
            "created_by": r["created_by_name"],
            "created_at": (r["created_at"].isoformat()
                           if r["created_at"] else None),
            "sent_at": r["sent_at"].isoformat() if r["sent_at"] else None,
        } for r in rows],
        "counts": counts,
        "note": ("Nothing in this system sends any of these. There is no "
                 "sender: messages are queued or refused, and the refusals "
                 "are kept so the rules can be checked."),
    }
