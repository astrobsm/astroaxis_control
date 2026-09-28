"""The field portal: a distributor's marketers, and nothing else.

WHO THIS IS FOR
===============
People employed by a distributor -- Penacea's reps, Tripleluminance's reps --
who need to price a product, register a customer and log a visit. They are not
employees of Bonnesante Medicals and they do not get a `users` row. The
comment on `distributor_marketers` refusing them a login stands; this gives
them an account in a table of their own instead.

THE ONE RULE EVERYTHING ELSE RESTS ON
=====================================
`distributor_id` comes from the signed token. Never from a path, never from a
query string, never from a body.

That is what makes this multi-tenant rather than merely filtered. A marketer
cannot ask for another distributor's catalogue, customers or prices because
there is nowhere in the API to express which distributor they mean -- the
question does not have a parameter. Compare that with the marketing module
today, where `staff_id` is a query parameter the client chooses, and any
marketer can read the whole team's work by changing it.

Every function here takes a resolved `session_claims` dict and reads the
tenant from it. A function that took a distributor_id argument would be one
refactor away from taking it from the request.

SEPARATE KEY, SEPARATE TYPE
===========================
Tokens are signed with FIELD_PORTAL_SECRET -- not SECRET_KEY, not
MEETING_GUEST_SECRET -- and carry `typ: "field_marketer"`. A field token
presented to the ERP fails on the signature; if the secrets were ever
misconfigured to the same value it still fails on the type, which
`require_authenticated_user` checks explicitly.

LOCATION IS PERSONAL DATA
=========================
`record_location` refuses to store anything until the marketer has consented,
stamps every row with a purge date, and marks whether the marketer had started
their working day. None of that is a policy document; it is the only code path
that can write to the table.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from fastapi import HTTPException
from jose import jwt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# A field session lasts a working day and no longer. These are phones carried
# into hospitals and markets; a token that lives for a month is a token that
# outlives the handset it was issued to.
SESSION_HOURS = int(os.getenv("FIELD_SESSION_HOURS", "12"))

DEFAULT_INVITE_DAYS = 14
MAX_INVITE_DAYS = 90

# How long a position trail is kept. Ninety days answers "where was the team
# last quarter" and stops well short of a permanent record of a person's
# movements.
LOCATION_RETENTION_DAYS = int(os.getenv("FIELD_LOCATION_RETENTION_DAYS", "90"))

# Positions closer together than this are dropped. A phone reporting every few
# seconds fills the table with noise and tells nobody anything they did not
# already know from the ping before it.
MIN_PING_SECONDS = 60

LOCKOUT_ATTEMPTS = 6

CONSENT_TEXT = (
    "I agree that my location may be recorded while I am working, so that "
    "visits I log can be verified and my activity reported to my distributor "
    "and to Bonnesante Medicals. I understand the record is kept for "
    f"{LOCATION_RETENTION_DAYS} days and then deleted, that I can see my own "
    "trail at any time, and that I can withdraw this agreement, after which "
    "no further location is recorded."
)


def field_secret() -> str:
    """The key that signs field sessions.

    Refused rather than defaulted. Falling back to SECRET_KEY would make a
    field token and a staff session interchangeable to anything that checks
    only the signature -- which is the entire point of keeping distributor
    staff out of the ERP.
    """
    secret = os.getenv("FIELD_PORTAL_SECRET")
    if not secret:
        raise HTTPException(
            status_code=503,
            detail=("The field portal is not configured on this server. "
                    "Set FIELD_PORTAL_SECRET."))
    return secret


def public_base_url() -> str:
    return (os.getenv("PUBLIC_BASE_URL")
            or "https://erp.bonnesantemedicals.com").rstrip("/")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_password(raw: str) -> str:
    from app.api.auth import hash_password
    return hash_password(raw)


def _check_password(raw: str, hashed: str) -> bool:
    from app.api.auth import verify_password
    return verify_password(raw, hashed)


def _digits(value: Optional[str]) -> str:
    return "".join(c for c in (value or "") if c.isdigit())


async def audit(
    session: AsyncSession, *, event_type: str,
    distributor_id: Optional[UUID] = None, marketer_id: Optional[UUID] = None,
    detail: Optional[dict] = None, ip: str = "", user_agent: str = "",
) -> None:
    await session.execute(
        text("""
            INSERT INTO field_audit_logs
                (id, distributor_id, marketer_id, event_type, detail,
                 ip_address, user_agent)
            VALUES (gen_random_uuid(), :d, :m, :e, CAST(:j AS JSONB), :ip, :ua)
        """),
        {"d": str(distributor_id) if distributor_id else None,
         "m": str(marketer_id) if marketer_id else None, "e": event_type,
         "j": json.dumps(detail) if detail else None,
         "ip": (ip or "")[:64] or None, "ua": (user_agent or "")[:500] or None})


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def issue_session(*, account_id: UUID, marketer_id: UUID,
                  distributor_id: UUID, name: str) -> str:
    now = _now()
    return jwt.encode(
        {
            "typ": "field_marketer",
            "account_id": str(account_id),
            "marketer_id": str(marketer_id),
            # The tenancy key. Signed, so it cannot be edited; read on every
            # request, so it cannot be bypassed.
            "distributor_id": str(distributor_id),
            "name": name,
            "jti": uuid4().hex,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=SESSION_HOURS)).timestamp()),
        },
        field_secret(), algorithm="HS256")


def decode_session(token: str) -> dict:
    try:
        claims = jwt.decode(token, field_secret(), algorithms=["HS256"])
    except Exception:
        raise HTTPException(status_code=401,
                            detail="Your session has ended. Please sign in again.")
    if claims.get("typ") != "field_marketer":
        raise HTTPException(status_code=401,
                            detail="Your session has ended. Please sign in again.")
    return claims


async def resolve_session(session: AsyncSession, token: str) -> dict:
    """Turn a token into a live account, or refuse.

    The account is re-read on every request rather than trusted from the
    token, so deactivating a marketer takes effect immediately instead of when
    their session happens to expire. A rep who left this morning should not
    still be pulling the price list this afternoon.
    """
    claims = decode_session(token)
    row = (await session.execute(
        text("""SELECT a.id, a.marketer_id, a.distributor_id, a.is_active,
                       a.is_locked, a.consented_at, m.full_name, m.is_active
                           AS marketer_active, d.legal_name AS distributor_name
                  FROM field_marketer_accounts a
                  JOIN distributor_marketers m ON m.id = a.marketer_id
                  JOIN distributors d ON d.id = a.distributor_id
                 WHERE a.id = :a"""),
        {"a": claims["account_id"]})).mappings().first()

    if row is None or not row["is_active"] or row["is_locked"] \
            or not row["marketer_active"]:
        raise HTTPException(
            status_code=401,
            detail="This account is no longer active. Contact your distributor.")

    return {
        "account_id": row["id"],
        "marketer_id": row["marketer_id"],
        "distributor_id": row["distributor_id"],
        "name": row["full_name"],
        "distributor_name": row["distributor_name"],
        "consented_at": row["consented_at"],
    }


# ---------------------------------------------------------------------------
# Getting an account in the first place
# ---------------------------------------------------------------------------

async def issue_invite(
    session: AsyncSession, *, distributor_id: UUID, label: str,
    valid_days: int = DEFAULT_INVITE_DAYS, max_uses: Optional[int] = None,
    actor=None,
) -> dict:
    """A link a distributor sends to its own field team."""
    if not label or len(label.strip()) < 3:
        raise HTTPException(
            status_code=400,
            detail="Label the link with who it is going to -- 'Enugu team, "
                   "October'. When it needs revoking you will be reading this "
                   "list, not remembering.")
    if not 1 <= valid_days <= MAX_INVITE_DAYS:
        raise HTTPException(status_code=400,
                            detail=f"Validity must be 1-{MAX_INVITE_DAYS} days.")

    token = secrets.token_urlsafe(32)
    invite_id = uuid4()
    expires = _now() + timedelta(days=valid_days)

    await session.execute(
        text("""INSERT INTO field_marketer_invites
                    (id, distributor_id, label, token_sha256, token_hint,
                     expires_at, max_uses, created_by)
                VALUES (:i, :d, :l, :h, :hint, :e, :max, :by)"""),
        {"i": str(invite_id), "d": str(distributor_id), "l": label.strip(),
         "h": _hash(token), "hint": token[-6:], "e": expires,
         "max": max_uses, "by": str(actor.id) if actor else None})

    await audit(session, event_type="FIELD_INVITE_ISSUED",
                distributor_id=distributor_id,
                detail={"label": label.strip(), "hint": token[-6:]})

    return {
        "id": str(invite_id),
        "label": label.strip(),
        "join_url": f"{public_base_url()}/field/join/{token}",
        "expires_at": expires.isoformat(),
        "warning": ("Anyone with this link can create a field account on your "
                    "distributor record. Send it to your own team only, and "
                    "revoke it when they have all signed up."),
    }


async def resolve_invite(session: AsyncSession, *, token: str) -> dict:
    if not token or len(token) < 20:
        raise HTTPException(status_code=404, detail="This link is not valid.")

    row = (await session.execute(
        text("""SELECT i.*, d.legal_name AS distributor_name
                  FROM field_marketer_invites i
                  JOIN distributors d ON d.id = i.distributor_id
                 WHERE i.token_sha256 = :h"""),
        {"h": _hash(token)})).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="This link is not valid.")
    if row["revoked_at"] is not None:
        raise HTTPException(status_code=403,
                            detail="This link has been withdrawn.")
    if row["expires_at"] <= _now():
        raise HTTPException(status_code=403, detail="This link has expired.")
    if row["max_uses"] is not None and row["use_count"] >= row["max_uses"]:
        raise HTTPException(
            status_code=403,
            detail="This link has been used the number of times it was opened "
                   "for. Ask your distributor for a new one.")
    return dict(row)


async def register_from_invite(
    session: AsyncSession, *, token: str, full_name: str, phone: str,
    password: str, email: Optional[str] = None, ip: str = "",
    user_agent: str = "",
) -> dict:
    """Create the marketer record and the account behind one invite link."""
    invite = await resolve_invite(session, token=token)

    full_name = (full_name or "").strip()[:160]
    if len(full_name) < 3:
        raise HTTPException(status_code=400, detail="Enter your full name.")
    if len(_digits(phone)) < 10:
        raise HTTPException(status_code=400,
                            detail="Enter the phone number you will sign in with.")
    if len(password or "") < 8:
        raise HTTPException(status_code=400,
                            detail="Choose a password of at least 8 characters.")

    phone = phone.strip()
    taken = (await session.execute(
        text("SELECT 1 FROM field_marketer_accounts WHERE login_phone = :p"),
        {"p": phone})).first()
    if taken is not None:
        raise HTTPException(
            status_code=409,
            detail="An account already exists for that number. Sign in instead, "
                   "or ask your distributor to reset it.")

    marketer_id = uuid4()
    await session.execute(
        text("""INSERT INTO distributor_marketers
                    (id, distributor_id, full_name, phone, is_active)
                VALUES (:i, :d, :n, :p, TRUE)"""),
        {"i": str(marketer_id), "d": str(invite["distributor_id"]),
         "n": full_name, "p": phone})

    account_id = uuid4()
    await session.execute(
        text("""INSERT INTO field_marketer_accounts
                    (id, marketer_id, distributor_id, login_phone, email,
                     password_hash)
                VALUES (:i, :m, :d, :p, :e, :h)"""),
        {"i": str(account_id), "m": str(marketer_id),
         "d": str(invite["distributor_id"]), "p": phone, "e": email,
         "h": _hash_password(password)})

    await session.execute(
        text("""UPDATE field_marketer_invites
                   SET use_count = use_count + 1 WHERE id = :i"""),
        {"i": str(invite["id"])})

    await audit(session, event_type="FIELD_ACCOUNT_CREATED",
                distributor_id=invite["distributor_id"],
                marketer_id=marketer_id, detail={"name": full_name},
                ip=ip, user_agent=user_agent)

    return {
        "account_id": str(account_id),
        "marketer_id": str(marketer_id),
        "distributor": invite["distributor_name"],
        "token": issue_session(
            account_id=account_id, marketer_id=marketer_id,
            distributor_id=invite["distributor_id"], name=full_name),
        "consent_required": True,
        "consent_text": CONSENT_TEXT,
    }


async def sign_in(
    session: AsyncSession, *, phone: str, password: str, ip: str = "",
    user_agent: str = "",
) -> dict:
    row = (await session.execute(
        text("""SELECT a.*, m.full_name, m.is_active AS marketer_active,
                       d.legal_name AS distributor_name
                  FROM field_marketer_accounts a
                  JOIN distributor_marketers m ON m.id = a.marketer_id
                  JOIN distributors d ON d.id = a.distributor_id
                 WHERE a.login_phone = :p"""),
        {"p": (phone or "").strip()})).mappings().first()

    # One answer for a wrong number and a wrong password. Distinguishing them
    # tells somebody probing which of your distributors' staff exist.
    refusal = HTTPException(status_code=401,
                            detail="That phone number or password is not correct.")
    if row is None:
        raise refusal
    if row["is_locked"]:
        raise HTTPException(
            status_code=403,
            detail="This account is locked. Ask your distributor to reset it.")
    if not row["is_active"] or not row["marketer_active"]:
        raise HTTPException(
            status_code=403,
            detail="This account is no longer active.")

    if not _check_password(password or "", row["password_hash"]):
        attempts = int(row["failed_attempts"] or 0) + 1
        await session.execute(
            text("""UPDATE field_marketer_accounts
                       SET failed_attempts = :n, is_locked = :lock
                     WHERE id = :i"""),
            {"n": attempts, "lock": attempts >= LOCKOUT_ATTEMPTS,
             "i": str(row["id"])})
        await audit(session, event_type="FIELD_SIGN_IN_FAILED",
                    distributor_id=row["distributor_id"],
                    marketer_id=row["marketer_id"],
                    detail={"attempt": attempts}, ip=ip, user_agent=user_agent)
        raise refusal

    await session.execute(
        text("""UPDATE field_marketer_accounts
                   SET failed_attempts = 0, last_login_at = NOW()
                 WHERE id = :i"""), {"i": str(row["id"])})
    await audit(session, event_type="FIELD_SIGNED_IN",
                distributor_id=row["distributor_id"],
                marketer_id=row["marketer_id"], ip=ip, user_agent=user_agent)

    return {
        "token": issue_session(
            account_id=row["id"], marketer_id=row["marketer_id"],
            distributor_id=row["distributor_id"], name=row["full_name"]),
        "name": row["full_name"],
        "distributor": row["distributor_name"],
        "consented": row["consented_at"] is not None,
        "consent_text": CONSENT_TEXT,
    }


async def set_consent(
    session: AsyncSession, *, me: dict, agreed: bool,
) -> dict:
    """Record or withdraw agreement to being located.

    Withdrawal clears the date, which stops every future ping at the one place
    that writes them. It does not delete what is already there -- that is a
    separate, deliberate act -- but nothing further is added.
    """
    await session.execute(
        text("""UPDATE field_marketer_accounts
                   SET consented_at = :at, consent_text = :txt,
                       updated_at = NOW()
                 WHERE id = :a"""),
        {"at": _now() if agreed else None,
         "txt": CONSENT_TEXT if agreed else None,
         "a": str(me["account_id"])})
    await audit(session,
                event_type="FIELD_CONSENT_GIVEN" if agreed
                else "FIELD_CONSENT_WITHDRAWN",
                distributor_id=me["distributor_id"],
                marketer_id=me["marketer_id"])
    return {"consented": agreed}


# ---------------------------------------------------------------------------
# What the marketer can see and do
# ---------------------------------------------------------------------------

async def catalogue(session: AsyncSession, *, me: dict) -> dict:
    """The products this distributor carries, at this distributor's prices.

    The price list IS the range: a product with a live price row is one they
    sell, and one without simply is not visible. There is no second table of
    permitted products that could disagree with it.
    """
    rows = (await session.execute(
        text("""SELECT p.id, p.name, p.sku, p.description, p.unit AS base_unit,
                       pl.unit, pl.price, pl.effective_from
                  FROM distributor_price_list pl
                  JOIN products p ON p.id = pl.product_id
                 WHERE pl.distributor_id = :d AND pl.effective_to IS NULL
                 ORDER BY p.name, pl.unit"""),
        {"d": str(me["distributor_id"])})).mappings().all()

    return {
        "distributor": me["distributor_name"],
        "items": [
            {"product_id": str(r["id"]), "name": r["name"], "sku": r["sku"],
             "description": r["description"], "unit": r["unit"],
             "price": str(Decimal(str(r["price"]))),
             "priced_since": r["effective_from"].isoformat()
             if r["effective_from"] else None}
            for r in rows
        ],
        "note": ("These are your distributor's prices. They are set by your "
                 "distributor, not by Bonnesante Medicals."),
    }


async def register_customer(
    session: AsyncSession, *, me: dict, name: str, phone: Optional[str] = None,
    email: Optional[str] = None, address: Optional[str] = None,
    ip: str = "",
) -> dict:
    """Add a customer to the company database, tagged to this marketer.

    Into `customers` -- the same table every other customer is in. A separate
    list for distributor-introduced customers would give the company two
    answers to who it sells to, and the second one would be the one nobody
    reconciles.
    """
    name = (name or "").strip()[:255]
    if len(name) < 2:
        raise HTTPException(status_code=400, detail="Enter the customer's name.")

    if phone:
        existing = (await session.execute(
            text("""SELECT id, name FROM customers
                     WHERE regexp_replace(COALESCE(phone,''), '[^0-9]', '', 'g')
                           LIKE :tail
                     LIMIT 1"""),
            {"tail": f"%{_digits(phone)[-10:]}"})).mappings().first()
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail=(f"That number is already registered to "
                        f"{existing['name']}. If this is the same business, "
                        f"there is no need to add it again."))

    customer_id = uuid4()
    code = "C" + secrets.token_hex(4).upper()
    await session.execute(
        text("""INSERT INTO customers
                    (id, customer_code, name, phone, email, address,
                     registered_by_marketer_id, introduced_by_distributor_id)
                VALUES (:i, :c, :n, :p, :e, :a, :m, :d)"""),
        {"i": str(customer_id), "c": code, "n": name, "p": phone, "e": email,
         "a": address, "m": str(me["marketer_id"]),
         "d": str(me["distributor_id"])})

    await audit(session, event_type="FIELD_CUSTOMER_REGISTERED",
                distributor_id=me["distributor_id"],
                marketer_id=me["marketer_id"],
                detail={"customer_id": str(customer_id), "name": name}, ip=ip)

    return {"id": str(customer_id), "customer_code": code, "name": name}


async def my_customers(session: AsyncSession, *, me: dict) -> list[dict]:
    """Only the ones this marketer registered. Not the company's customer book.

    Scoped to the marketer, not to the distributor: a rep sees who they
    brought in. Handing every rep the distributor's whole customer list would
    be handing it to whoever leaves next.
    """
    rows = (await session.execute(
        text("""SELECT id, customer_code, name, phone, address, created_at
                  FROM customers
                 WHERE registered_by_marketer_id = :m
                 ORDER BY created_at DESC LIMIT 500"""),
        {"m": str(me["marketer_id"])})).mappings().all()
    return [dict(r) | {"id": str(r["id"])} for r in rows]


async def log_visit(
    session: AsyncSession, *, me: dict, place_name: str, purpose: str = "VISIT",
    customer_id: Optional[UUID] = None, outcome: Optional[str] = None,
    products_discussed: Optional[str] = None,
    order_value: Optional[Decimal] = None, follow_up_on: Optional[date] = None,
    latitude: Optional[float] = None, longitude: Optional[float] = None,
    accuracy_m: Optional[float] = None, address: Optional[str] = None,
) -> dict:
    place_name = (place_name or "").strip()[:255]
    if len(place_name) < 2:
        raise HTTPException(status_code=400,
                            detail="Where were you? Enter the place you visited.")
    purpose = (purpose or "VISIT").upper()
    if purpose not in ("VISIT", "CALL", "DELIVERY", "COLLECTION",
                       "PROSPECTING", "OTHER"):
        purpose = "OTHER"

    # A customer can only be attached if this marketer registered them. Without
    # this, a visit could be logged against any customer id in the company.
    if customer_id is not None:
        owned = (await session.execute(
            text("""SELECT 1 FROM customers
                     WHERE id = :c AND registered_by_marketer_id = :m"""),
            {"c": str(customer_id), "m": str(me["marketer_id"])})).first()
        if owned is None:
            raise HTTPException(
                status_code=403,
                detail="You can only log a visit against a customer you "
                       "registered.")

    visit_id = uuid4()
    await session.execute(
        text("""INSERT INTO field_visits
                    (id, marketer_id, distributor_id, customer_id, place_name,
                     purpose, outcome, products_discussed, order_value,
                     follow_up_on, latitude, longitude, accuracy_m, address)
                VALUES (:i, :m, :d, :c, :pl, :pu, :o, :pr, :ov, :f,
                        :lat, :lng, :acc, :addr)"""),
        {"i": str(visit_id), "m": str(me["marketer_id"]),
         "d": str(me["distributor_id"]),
         "c": str(customer_id) if customer_id else None, "pl": place_name,
         "pu": purpose, "o": outcome, "pr": products_discussed,
         "ov": order_value, "f": follow_up_on, "lat": latitude,
         "lng": longitude, "acc": accuracy_m, "addr": address})

    await audit(session, event_type="FIELD_VISIT_LOGGED",
                distributor_id=me["distributor_id"],
                marketer_id=me["marketer_id"],
                detail={"place": place_name, "purpose": purpose})
    return {"id": str(visit_id), "place_name": place_name, "purpose": purpose}


async def my_visits(
    session: AsyncSession, *, me: dict, days: int = 30,
) -> list[dict]:
    rows = (await session.execute(
        text("""SELECT v.id, v.place_name, v.purpose, v.outcome,
                       v.products_discussed, v.order_value, v.follow_up_on,
                       v.latitude, v.longitude, v.address, v.started_at,
                       c.name AS customer_name
                  FROM field_visits v
                  LEFT JOIN customers c ON c.id = v.customer_id
                 WHERE v.marketer_id = :m
                   AND v.started_at >= NOW() - CAST(:d || ' days' AS interval)
                 ORDER BY v.started_at DESC LIMIT 500"""),
        {"m": str(me["marketer_id"]), "d": str(int(days))})).mappings().all()
    return [dict(r) | {"id": str(r["id"]),
                       "order_value": (str(r["order_value"])
                                       if r["order_value"] is not None else None)}
            for r in rows]


async def record_location(
    session: AsyncSession, *, me: dict, latitude: float, longitude: float,
    accuracy_m: Optional[float] = None, speed_mps: Optional[float] = None,
    working: bool = True,
) -> dict:
    """Store one position, if the marketer has agreed to it.

    The consent check is here rather than at the edge because this is the only
    function that writes to the table. A check on the route could be bypassed
    by a second route added later; this one cannot.
    """
    if me.get("consented_at") is None:
        raise HTTPException(
            status_code=403,
            detail="Location is not being recorded because you have not agreed "
                   "to it. You can agree in your profile.")

    if not (-90 <= latitude <= 90) or not (-180 <= longitude <= 180):
        raise HTTPException(status_code=400, detail="That position is not valid.")

    # A phone reporting every few seconds fills the table and says nothing the
    # previous row did not.
    recent = (await session.execute(
        text("""SELECT 1 FROM field_marketer_locations
                 WHERE marketer_id = :m
                   AND recorded_at > NOW() - CAST(:s || ' seconds' AS interval)
                 LIMIT 1"""),
        {"m": str(me["marketer_id"]), "s": str(MIN_PING_SECONDS)})).first()
    if recent is not None:
        return {"stored": False, "reason": "too soon after the last position"}

    await session.execute(
        text("""INSERT INTO field_marketer_locations
                    (marketer_id, distributor_id, latitude, longitude,
                     accuracy_m, speed_mps, captured_while_working, purge_after)
                VALUES (:m, :d, :lat, :lng, :acc, :spd, :w,
                        CURRENT_DATE + CAST(:keep || ' days' AS interval))"""),
        {"m": str(me["marketer_id"]), "d": str(me["distributor_id"]),
         "lat": latitude, "lng": longitude, "acc": accuracy_m, "spd": speed_mps,
         "w": working, "keep": str(LOCATION_RETENTION_DAYS)})
    return {"stored": True}


async def my_trail(
    session: AsyncSession, *, me: dict, on: Optional[date] = None,
) -> list[dict]:
    """The marketer's own movements. Theirs to see, not only their manager's."""
    target = on or date.today()
    rows = (await session.execute(
        text("""SELECT latitude, longitude, accuracy_m, speed_mps, recorded_at
                  FROM field_marketer_locations
                 WHERE marketer_id = :m AND recorded_at::date = :d
                 ORDER BY recorded_at"""),
        {"m": str(me["marketer_id"]), "d": target})).mappings().all()
    return [dict(r) for r in rows]


async def my_performance(
    session: AsyncSession, *, me: dict, days: int = 30,
) -> dict:
    """What this marketer did. Counted, not scored.

    No single number. A rep with forty visits and no orders and a rep with
    four visits and four orders are different situations, and an index that
    blends them hides which one you are looking at.
    """
    row = (await session.execute(
        text("""SELECT COUNT(*) AS visits,
                       COUNT(DISTINCT v.customer_id)
                           FILTER (WHERE v.customer_id IS NOT NULL) AS customers_seen,
                       COUNT(*) FILTER (WHERE v.order_value > 0) AS visits_with_order,
                       COALESCE(SUM(v.order_value), 0) AS order_value,
                       COUNT(*) FILTER (WHERE v.follow_up_on IS NOT NULL
                                        AND v.follow_up_on >= CURRENT_DATE)
                           AS follow_ups_due,
                       COUNT(DISTINCT v.started_at::date) AS days_active
                  FROM field_visits v
                 WHERE v.marketer_id = :m
                   AND v.started_at >= NOW() - CAST(:d || ' days' AS interval)"""),
        {"m": str(me["marketer_id"]), "d": str(int(days))})).mappings().first()

    registered = (await session.execute(
        text("""SELECT COUNT(*) FROM customers
                 WHERE registered_by_marketer_id = :m
                   AND created_at >= NOW() - CAST(:d || ' days' AS interval)"""),
        {"m": str(me["marketer_id"]), "d": str(int(days))})).scalar() or 0

    return {
        "window_days": days,
        "visits": row["visits"],
        "customers_seen": row["customers_seen"],
        "visits_with_order": row["visits_with_order"],
        "order_value": str(Decimal(str(row["order_value"] or 0))),
        "customers_registered": registered,
        "follow_ups_due": row["follow_ups_due"],
        "days_active": row["days_active"],
        "note": ("Counts of what was recorded, over the period shown. Orders "
                 "are what was entered on a visit, not confirmed sales."),
    }


# ---------------------------------------------------------------------------
# The distributor's side, called from the ERP
# ---------------------------------------------------------------------------

async def set_price(
    session: AsyncSession, *, distributor_id: UUID, product_id: UUID,
    unit: str, price: Decimal, note: Optional[str] = None, actor=None,
) -> dict:
    """Set one price. The previous one is ended, never overwritten.

    A visit logged last month can then still be read against the price that
    was quoted at the time, which an UPDATE would have destroyed.
    """
    if price < 0:
        raise HTTPException(status_code=400, detail="A price cannot be negative.")
    unit = (unit or "unit").strip() or "unit"

    await session.execute(
        text("""UPDATE distributor_price_list
                   SET effective_to = CURRENT_DATE
                 WHERE distributor_id = :d AND product_id = :p
                   AND lower(unit) = lower(:u) AND effective_to IS NULL"""),
        {"d": str(distributor_id), "p": str(product_id), "u": unit})

    await session.execute(
        text("""INSERT INTO distributor_price_list
                    (id, distributor_id, product_id, unit, price, set_by, note)
                VALUES (gen_random_uuid(), :d, :p, :u, :pr, :by, :n)"""),
        {"d": str(distributor_id), "p": str(product_id), "u": unit,
         "pr": price, "by": str(actor.id) if actor else None, "n": note})

    await audit(session, event_type="FIELD_PRICE_SET",
                distributor_id=distributor_id,
                detail={"product_id": str(product_id), "unit": unit,
                        "price": str(price)})
    return {"product_id": str(product_id), "unit": unit, "price": str(price)}


async def price_list(
    session: AsyncSession, *, distributor_id: UUID, include_history: bool = False,
) -> list[dict]:
    clause = "" if include_history else "AND pl.effective_to IS NULL"
    rows = (await session.execute(
        text(f"""SELECT pl.id, pl.product_id, p.name AS product_name, p.sku,
                        pl.unit, pl.price, pl.effective_from, pl.effective_to,
                        pl.note, u.full_name AS set_by_name
                   FROM distributor_price_list pl
                   JOIN products p ON p.id = pl.product_id
                   LEFT JOIN users u ON u.id = pl.set_by
                  WHERE pl.distributor_id = :d {clause}
                  ORDER BY p.name, pl.unit, pl.effective_from DESC"""),
        {"d": str(distributor_id)})).mappings().all()
    return [dict(r) | {"id": str(r["id"]),
                       "product_id": str(r["product_id"]),
                       "price": str(Decimal(str(r["price"])))} for r in rows]


async def team_activity(
    session: AsyncSession, *, distributor_id: UUID, days: int = 30,
) -> list[dict]:
    """Every marketer on this distributor, and what they did.

    For the distributor and for Bonnesante staff who may see the distributor
    record -- not for the marketers themselves, who see only their own.
    """
    rows = (await session.execute(
        text("""SELECT m.id, m.full_name, m.phone, m.is_active,
                       (a.id IS NOT NULL) AS has_account,
                       a.last_login_at, (a.consented_at IS NOT NULL) AS consented,
                       COUNT(v.id) AS visits,
                       COALESCE(SUM(v.order_value), 0) AS order_value,
                       MAX(v.started_at) AS last_visit,
                       (SELECT COUNT(*) FROM customers c
                         WHERE c.registered_by_marketer_id = m.id) AS customers
                  FROM distributor_marketers m
                  LEFT JOIN field_marketer_accounts a ON a.marketer_id = m.id
                  LEFT JOIN field_visits v ON v.marketer_id = m.id
                       AND v.started_at >= NOW() - CAST(:d || ' days' AS interval)
                 WHERE m.distributor_id = :dist
                 GROUP BY m.id, m.full_name, m.phone, m.is_active, a.id,
                          a.last_login_at, a.consented_at
                 ORDER BY visits DESC, m.full_name"""),
        {"dist": str(distributor_id), "d": str(int(days))})).mappings().all()
    return [dict(r) | {"id": str(r["id"]),
                       "order_value": str(Decimal(str(r["order_value"] or 0)))}
            for r in rows]


async def purge_expired_locations(session: AsyncSession) -> dict:
    """Delete position rows past their retention date.

    Run on a schedule. The date is on the row, so this cannot drift from the
    policy the marketer agreed to -- it deletes exactly what it promised to.
    """
    result = await session.execute(
        text("DELETE FROM field_marketer_locations WHERE purge_after <= CURRENT_DATE"))
    return {"deleted": result.rowcount or 0}


async def purge_scheduler() -> None:
    """Run the purge once a day, forever.

    A retention promise nobody executes is not a retention promise. This is
    started from main.py so the deletion happens on the server that holds the
    data, rather than depending on somebody remembering a cron entry.

    Failures are logged and the loop continues: a database hiccup at 02:00
    should cost one day's purge, not every day's.
    """
    log = logging.getLogger("field_portal.purge")
    from app.db import AsyncSessionLocal

    while True:
        try:
            await asyncio.sleep(24 * 60 * 60)
            async with AsyncSessionLocal() as session:
                result = await purge_expired_locations(session)
                await session.commit()
            if result["deleted"]:
                log.info("Purged %s expired field positions", result["deleted"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the loop must survive
            log.warning("Field location purge failed: %s", exc)
