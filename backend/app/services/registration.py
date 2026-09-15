"""Distributor self-registration, and linking an applicant to who they already are.

THE CUSTOMER DROPDOWN, AND WHY IT IS NOT ONE
============================================
The obvious build is a type-ahead on the public form: start typing a business
name, see matching customers, pick yourself. It is also a way to hand the
company's entire customer list to anyone who has the link -- type "a", collect
every customer beginning with A, repeat through the alphabet. A competitor with
a forwarded WhatsApp link would have the whole book in an afternoon.

So `confirm_existing_customer` is a CONFIRMATION, not a search:

* it matches on a FULL phone number, not a name prefix, so the applicant has to
  already know the number;
* it returns at most ONE account, with the name partially masked -- enough to
  recognise yourself, not enough to harvest;
* it is rate-limited per link, because the residual risk is somebody testing
  phone numbers one at a time;
* every lookup is counted.

The staff-side review sees the full candidate list through
`distributors.find_possible_duplicates`, which already exists and already
explains WHY each candidate matched. That is where the real matching happens,
by somebody authenticated who can judge -- and it is a better answer than the
dropdown, because the applicant does not always know they are already a
customer under a slightly different name.

THERE IS NOTHING TO IMPORT
==========================
"Import all their past transactions" sounds like copying rows into distributor
tables. It must not be, and the reason is the decision the whole module rests
on: a distributor IS a customer plus a warehouse. An approved applicant linked
to an existing customer ALREADY has their entire history in `sales_orders`.
Copying it would double-count revenue, create a second answer to "what did they
buy", and break the recall trace by splitting one order across two records.

`attribute_history` therefore sets `distributor_id` on the orders that were
always theirs, and records `distributor_attributed_at` so an order that was
attributed later is distinguishable from one actually placed as a distributor
order.

`sales_channel` is deliberately NOT changed. Those were direct sales at the time.
Rewriting them as distributor sales would falsify history to make a report look
tidy, and every figure measured by channel would silently move.

Performance is unaffected, and that is correct: the performance engine measures
DOWNSTREAM sales (distributor to outlet), not what the company sold them. A new
distributor does not acquire a sell-through history by being linked.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import distributors as dsvc
from app.services.geography import audit

DEFAULT_VALIDITY_DAYS = 90
MAX_VALIDITY_DAYS = 730

# Lookups allowed per link per hour. The residual risk after requiring a full
# phone number is somebody testing numbers one at a time; this bounds it.
LOOKUP_CAP_PER_HOUR = 30
# Submissions per address per hour, so one script cannot flood the review queue.
SUBMIT_CAP_PER_HOUR = 5


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _digits(value: Optional[str]) -> str:
    return re.sub(r"\D", "", value or "")


def _reference() -> str:
    return f"REG-{date.today():%Y%m}-{secrets.token_hex(3).upper()}"


def _mask(name: str) -> str:
    """Enough of a name to recognise yourself by, not enough to harvest.

    "DIVIDEND PHARMACY" -> "DIV••••••• PH•••••". Someone who is that customer
    knows immediately; someone fishing learns almost nothing.
    """
    out = []
    for word in (name or "").split():
        out.append(word if len(word) <= 3
                   else word[:3] + "•" * (len(word) - 3))
    return " ".join(out)


# ---------------------------------------------------------------------------
# Issuing the link
# ---------------------------------------------------------------------------

async def issue_link(
    session: AsyncSession, *, label: str, campaign: Optional[str] = None,
    valid_days: int = DEFAULT_VALIDITY_DAYS,
    max_submissions: Optional[int] = None, base_url: str = "", actor=None,
) -> dict:
    """Create a registration link. The token is returned once and not stored."""
    if not label or len(label.strip()) < 3:
        raise HTTPException(
            status_code=400,
            detail=("Label the link with where it is going -- 'Trade fair, Aba, "
                    "March'. When it needs revoking you will be reading this "
                    "list, not remembering."))
    if not 1 <= valid_days <= MAX_VALIDITY_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"Validity must be between 1 and {MAX_VALIDITY_DAYS} days.")

    token = secrets.token_urlsafe(32)
    link_id = uuid4()
    expires_at = datetime.now(timezone.utc) + timedelta(days=valid_days)

    await session.execute(
        text("""
            INSERT INTO distributor_registration_links
                (id, token_sha256, token, token_hint, label, campaign,
                 expires_at, max_submissions, created_by)
            VALUES (:id, :h, :tok, :hint, :label, :camp, :exp, :max, :by)
        """),
        {"id": str(link_id), "h": _hash(token), "tok": token,
         "hint": token[-6:],
         "label": label.strip(), "camp": campaign, "exp": expires_at,
         "max": max_submissions, "by": str(actor.id) if actor else None},
    )
    await audit(session, event_type="REGISTRATION_LINK_ISSUED",
                entity_type="distributor_registration_link", entity_id=link_id,
                actor=actor,
                new_value={"label": label.strip(), "hint": token[-6:],
                           "expires_at": expires_at.isoformat()})

    base = (base_url or "").rstrip("/")
    return {
        "id": str(link_id), "label": label.strip(),
        "expires_at": expires_at.isoformat(), "token_hint": token[-6:],
        "url": f"{base}/register/{token}" if base else f"/register/{token}",
        "warning": ("This link is meant to be shared widely -- it lets anyone "
                    "APPLY, and nothing more. Every application still has to "
                    "be reviewed and approved before it becomes a distributor. "
                    "You can copy it again from the list at any time."),
    }


async def revoke_link(
    session: AsyncSession, *, link_id: UUID, reason: str, actor=None,
) -> dict:
    if not reason or len(reason.strip()) < 3:
        raise HTTPException(status_code=400, detail="Say why.")
    row = (await session.execute(
        text("""SELECT id, label, revoked_at
                  FROM distributor_registration_links
                 WHERE id = :l FOR UPDATE"""),
        {"l": str(link_id)})).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Link not found.")
    if row["revoked_at"] is not None:
        raise HTTPException(status_code=400, detail="Already revoked.")

    await session.execute(
        text("""UPDATE distributor_registration_links
                   SET revoked_at = NOW(), revoked_by = :by, revoke_reason = :r
                 WHERE id = :l"""),
        {"by": str(actor.id) if actor else None, "r": reason.strip(),
         "l": str(link_id)})
    await audit(session, event_type="REGISTRATION_LINK_REVOKED",
                entity_type="distributor_registration_link", entity_id=link_id,
                actor=actor, reason=reason.strip(),
                new_value={"label": row["label"]})
    return {"id": str(link_id), "revoked": True}


async def list_links(
    session: AsyncSession, *, include_dead: bool = False, base_url: str = "",
) -> list[dict]:
    """The links, each with the URL to share.

    The URL is returned rather than only a fingerprint because a registration
    link is meant to be published -- see i4567890123h. A link issued before
    that migration has no stored token and cannot be reconstructed, so `url` is
    None and the screen says to reissue rather than showing an empty box.
    """
    clause = ("" if include_dead
              else "WHERE l.revoked_at IS NULL AND l.expires_at > NOW()")
    rows = (await session.execute(
        text(f"""SELECT l.id, l.label, l.campaign, l.token, l.token_hint,
                        l.expires_at, l.revoked_at, l.revoke_reason,
                        l.view_count, l.submission_count, l.max_submissions,
                        l.last_used_at,
                        (l.revoked_at IS NULL AND l.expires_at > NOW())
                            AS is_live,
                        u.full_name AS issued_by
                   FROM distributor_registration_links l
                   LEFT JOIN users u ON u.id = l.created_by
                   {clause}
                  ORDER BY l.created_at DESC"""))).mappings().all()

    base = (base_url or "").rstrip("/")
    out = []
    for row in rows:
        link = dict(row) | {"id": str(row["id"])}
        token = link.pop("token", None)
        link["url"] = (f"{base}/register/{token}" if token else None)
        link["recoverable"] = token is not None
        out.append(link)
    return out


# ---------------------------------------------------------------------------
# The public side
# ---------------------------------------------------------------------------

async def resolve(
    session: AsyncSession, *, token: str, count_view: bool = False,
) -> dict:
    if not token or len(token) < 20:
        raise HTTPException(status_code=404,
                            detail="This registration link is not valid.")

    row = (await session.execute(
        text("""SELECT id, label, campaign, expires_at, revoked_at,
                       max_submissions, submission_count
                  FROM distributor_registration_links
                 WHERE token_sha256 = :h"""),
        {"h": _hash(token)})).mappings().first()
    if row is None:
        raise HTTPException(status_code=404,
                            detail="This registration link is not valid.")
    if row["revoked_at"] is not None:
        raise HTTPException(
            status_code=403,
            detail=("This registration link has been withdrawn. Please contact "
                    "Bonnesante Medicals for a current one."))
    if row["expires_at"] <= datetime.now(timezone.utc):
        raise HTTPException(
            status_code=403,
            detail=("This registration link has expired. Please contact "
                    "Bonnesante Medicals for a current one."))
    if (row["max_submissions"] is not None
            and row["submission_count"] >= row["max_submissions"]):
        raise HTTPException(
            status_code=403,
            detail=("This registration link has reached the number of "
                    "applications it was opened for. Please contact "
                    "Bonnesante Medicals."))

    if count_view:
        await session.execute(
            text("""UPDATE distributor_registration_links
                       SET view_count = view_count + 1, last_used_at = NOW()
                     WHERE id = :l"""), {"l": str(row["id"])})
    return dict(row)


async def confirm_existing_customer(
    session: AsyncSession, *, token: str, phone: str,
) -> dict:
    """Does a full phone number match an account? One answer, masked.

    NOT a search. See the module docstring: a type-ahead over customer names on
    a public form is a way to export the customer list. This requires the
    applicant to already know the number and tells them only enough to
    recognise themselves.
    """
    link = await resolve(session, token=token)

    digits = _digits(phone)
    if len(digits) < 10:
        raise HTTPException(
            status_code=400,
            detail="Enter the full phone number the account is held under.")

    lookups = (await session.execute(
        text("""SELECT COUNT(*) FROM distributor_audit_logs
                 WHERE event_type = 'REGISTRATION_CUSTOMER_LOOKUP'
                   AND entity_id = :l
                   AND created_at > NOW() - INTERVAL '1 hour'"""),
        {"l": str(link["id"])})).scalar() or 0
    if lookups >= LOOKUP_CAP_PER_HOUR:
        raise HTTPException(
            status_code=429,
            detail=("Too many lookups from this link in the last hour. Continue "
                    "without it -- if you already buy from us, say so in the "
                    "form and we will match your account when we review it."))

    await audit(session, event_type="REGISTRATION_CUSTOMER_LOOKUP",
                entity_type="distributor_registration_link",
                entity_id=link["id"],
                new_value={"tail": digits[-4:]})

    # Last 10 digits, so +234 / 0 prefixes match the same account.
    row = (await session.execute(
        text("""SELECT id, name FROM customers
                 WHERE regexp_replace(COALESCE(phone, ''), '[^0-9]', '', 'g')
                       LIKE :tail
                   AND is_active
                 LIMIT 2"""),
        {"tail": f"%{digits[-10:]}"})).mappings().all()

    if len(row) != 1:
        # Zero matches and several matches answer the same way: we cannot tell
        # you. Distinguishing them would leak how many accounts share a number.
        return {
            "found": False,
            "note": ("We could not match that number to a single account. Carry "
                     "on with the form -- tick that you already buy from us and "
                     "we will match your account when we review it."),
        }

    return {
        "found": True,
        "customer_id": str(row[0]["id"]),
        "masked_name": _mask(row[0]["name"]),
        "note": ("If that is you, tick the box and we will bring your existing "
                 "account and purchase history across once you are approved."),
    }


async def submit(
    session: AsyncSession, *, token: str, payload: dict, ip: str = "",
    user_agent: str = "",
) -> dict:
    """Record an application. Creates no distributor and grants nothing."""
    link = await resolve(session, token=token)

    recent = (await session.execute(
        text("""SELECT COUNT(*) FROM distributor_registrations
                 WHERE ip_address = :ip
                   AND submitted_at > NOW() - INTERVAL '1 hour'"""),
        {"ip": (ip or "")[:64] or None})).scalar() or 0
    if recent >= SUBMIT_CAP_PER_HOUR:
        raise HTTPException(
            status_code=429,
            detail=("Several applications have already been sent from here in "
                    "the last hour. If that was not you, please contact "
                    "Bonnesante Medicals directly."))

    name = (payload.get("legal_name") or "").strip()
    phone = (payload.get("phone") or "").strip()
    if len(name) < 2:
        raise HTTPException(status_code=400,
                            detail="Enter the name of the business.")
    if len(_digits(phone)) < 7:
        raise HTTPException(status_code=400,
                            detail="Enter a phone number we can reach you on.")

    entity = (payload.get("entity_type") or "COMPANY").upper()
    if entity not in ("COMPANY", "INDIVIDUAL", "PARTNERSHIP", "COOPERATIVE"):
        entity = "COMPANY"

    claimed = payload.get("claimed_customer_id")
    if claimed:
        # Only accept an id that actually exists; a forged one would attach the
        # application to somebody else's account in the review queue.
        exists = (await session.execute(
            text("SELECT 1 FROM customers WHERE id = CAST(:c AS uuid)"),
            {"c": str(claimed)})).first()
        if exists is None:
            claimed = None

    registration_id = uuid4()
    reference = _reference()
    await session.execute(
        text("""
            INSERT INTO distributor_registrations
                (id, registration_reference, link_id, legal_name, trading_name,
                 entity_type, contact_name, phone, whatsapp, email,
                 business_address, state_id, lga_id, town, cac_number, tin,
                 years_in_operation, business_type, employee_count,
                 marketer_count, storage_description, products_of_interest,
                 applicant_note, claims_existing_customer, claimed_customer_id,
                 ip_address, user_agent)
            VALUES (:id, :ref, :link, :name, :trading, :entity, :contact,
                    :phone, :wa, :email, :addr, CAST(:state AS uuid),
                    CAST(:lga AS uuid), :town, :cac, :tin, :years, :btype,
                    :emp, :mkt, :storage, :products, :note, :claims,
                    CAST(:claimed AS uuid), :ip, :ua)
        """),
        {"id": str(registration_id), "ref": reference,
         "link": str(link["id"]), "name": name,
         "trading": payload.get("trading_name"), "entity": entity,
         "contact": payload.get("contact_name"), "phone": phone,
         "wa": payload.get("whatsapp"), "email": payload.get("email"),
         "addr": payload.get("business_address"),
         "state": payload.get("state_id"), "lga": payload.get("lga_id"),
         "town": payload.get("town"), "cac": payload.get("cac_number"),
         "tin": payload.get("tin"),
         "years": payload.get("years_in_operation"),
         "btype": payload.get("business_type"),
         "emp": payload.get("employee_count"),
         "mkt": payload.get("marketer_count"),
         "storage": payload.get("storage_description"),
         "products": payload.get("products_of_interest"),
         "note": payload.get("applicant_note"),
         "claims": bool(payload.get("claims_existing_customer")),
         "claimed": str(claimed) if claimed else None,
         "ip": (ip or "")[:64] or None, "ua": (user_agent or "")[:500] or None},
    )
    await session.execute(
        text("""UPDATE distributor_registration_links
                   SET submission_count = submission_count + 1,
                       last_used_at = NOW()
                 WHERE id = :l"""), {"l": str(link["id"])})

    return {
        "registration_reference": reference,
        "status": "PENDING",
        "message": (f"Thank you. Your application has been received as "
                    f"{reference}. Somebody from Bonnesante Medicals will "
                    f"review it and contact you on {phone}."),
        # Said plainly, so nobody believes they are now a distributor.
        "note": ("This is an application. It does not create an account, and "
                 "you cannot order until it has been reviewed and approved."),
    }


# ---------------------------------------------------------------------------
# The staff side
# ---------------------------------------------------------------------------

async def list_registrations(
    session: AsyncSession, *, status: Optional[str] = None,
    pending_only: bool = False,
) -> list[dict]:
    clauses, params = ["1 = 1"], {}
    if pending_only:
        clauses.append("r.status IN ('PENDING','REVIEWING')")
    elif status:
        clauses.append("r.status = :s")
        params["s"] = status

    rows = (await session.execute(
        text(f"""SELECT r.id, r.registration_reference, r.legal_name,
                        r.trading_name, r.phone, r.email, r.town,
                        r.entity_type, r.business_type, r.status,
                        r.claims_existing_customer, r.submitted_at,
                        r.orders_attributed, r.review_note,
                        s.name AS state, l.name AS lga,
                        link.label AS came_from,
                        c.name AS claimed_customer,
                        lc.name AS linked_customer,
                        d.distributor_code,
                        u.full_name AS reviewed_by_name
                   FROM distributor_registrations r
                   LEFT JOIN states s ON s.id = r.state_id
                   LEFT JOIN lgas l ON l.id = r.lga_id
                   LEFT JOIN distributor_registration_links link
                          ON link.id = r.link_id
                   LEFT JOIN customers c ON c.id = r.claimed_customer_id
                   LEFT JOIN customers lc ON lc.id = r.linked_customer_id
                   LEFT JOIN distributors d ON d.id = r.distributor_id
                   LEFT JOIN users u ON u.id = r.reviewed_by
                  WHERE {' AND '.join(clauses)}
                  ORDER BY r.submitted_at DESC"""),
        params)).mappings().all()
    return [dict(r) | {"id": str(r["id"])} for r in rows]


async def review_packet(session: AsyncSession, *, registration_id: UUID) -> dict:
    """The application, plus who the company thinks this already is.

    The candidate list is produced HERE, for an authenticated reviewer, rather
    than on the public form. It uses the existing duplicate matcher, so it also
    catches an applicant who does not realise they are already a customer under
    a slightly different name.
    """
    row = (await session.execute(
        text("""SELECT r.*, s.name AS state, l.name AS lga,
                       link.label AS came_from,
                       c.name AS claimed_customer_name,
                       c.customer_code AS claimed_customer_code
                  FROM distributor_registrations r
                  LEFT JOIN states s ON s.id = r.state_id
                  LEFT JOIN lgas l ON l.id = r.lga_id
                  LEFT JOIN distributor_registration_links link
                         ON link.id = r.link_id
                  LEFT JOIN customers c ON c.id = r.claimed_customer_id
                 WHERE r.id = :r"""),
        {"r": str(registration_id)})).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Registration not found.")

    candidates = await dsvc.find_possible_duplicates(
        session, legal_name=row["legal_name"], phone=row["phone"] or "",
        email=row["email"] or "", cac_number=row["cac_number"] or "",
        tin=row["tin"] or "")

    # What each candidate would bring with them, so the reviewer can see the
    # consequence of linking before they do it.
    for candidate in candidates:
        if candidate["kind"] != "customer":
            continue
        summary = (await session.execute(
            text("""SELECT COUNT(*) AS orders,
                           COALESCE(SUM(total_amount), 0) AS value,
                           MIN(order_date) AS first_order,
                           MAX(order_date) AS last_order
                      FROM sales_orders WHERE customer_id = CAST(:c AS uuid)"""),
            {"c": candidate["id"]})).mappings().first()
        candidate["history"] = {
            "orders": summary["orders"],
            "value": str(Decimal(str(summary["value"] or 0))),
            "first_order": (summary["first_order"].isoformat()
                            if summary["first_order"] else None),
            "last_order": (summary["last_order"].isoformat()
                           if summary["last_order"] else None),
        }

    out = dict(row)
    for key in ("id", "link_id", "state_id", "lga_id", "claimed_customer_id",
                "linked_customer_id", "reviewed_by", "distributor_id"):
        if out.get(key) is not None:
            out[key] = str(out[key])

    return {
        "registration": out,
        "candidates": candidates,
        "note": ("Everything the applicant submitted is unverified -- it is what "
                 "somebody typed into a public form. The candidates below come "
                 "from matching it against existing customers and distributors; "
                 "linking one brings their purchase history across, and that is "
                 "a decision for you, not for the applicant."),
    }


async def attribute_history(
    session: AsyncSession, *, distributor_id: UUID, customer_id: UUID,
    actor=None,
) -> dict:
    """Attribute a customer's existing orders to the distributor they became.

    NOT an import. The orders are already in `sales_orders` and already belong
    to this customer; this makes them visible in the distributor's dossier by
    setting `distributor_id`. Copying them would double-count revenue and give
    two answers to "what did they buy".

    `sales_channel` is left alone on purpose. Those were direct sales when they
    happened, and rewriting them as distributor sales would move every figure
    measured by channel and falsify what was true at the time.
    """
    result = await session.execute(
        text("""UPDATE sales_orders
                   SET distributor_id = :d, distributor_attributed_at = NOW()
                 WHERE customer_id = :c
                   AND distributor_id IS NULL"""),
        {"d": str(distributor_id), "c": str(customer_id)})
    attributed = result.rowcount or 0

    summary = (await session.execute(
        text("""SELECT COUNT(*) AS orders,
                       COALESCE(SUM(total_amount), 0) AS value,
                       MIN(order_date) AS first_order
                  FROM sales_orders WHERE distributor_id = :d"""),
        {"d": str(distributor_id)})).mappings().first()

    await audit(session, event_type="DISTRIBUTOR_HISTORY_ATTRIBUTED",
                entity_type="distributor", entity_id=distributor_id,
                distributor_id=distributor_id, actor=actor,
                new_value={"customer_id": str(customer_id),
                           "orders_attributed": attributed,
                           "total_orders_now": summary["orders"]})

    return {
        "orders_attributed": attributed,
        "orders_total": summary["orders"],
        "value": str(Decimal(str(summary["value"] or 0))),
        "trading_since": (summary["first_order"].isoformat()
                          if summary["first_order"] else None),
        "note": ("These orders were already in the system against this "
                 "customer; linking made them visible under the distributor. "
                 "Nothing was copied and no revenue was recounted. They keep "
                 "sales_channel = DIRECT because that is what they were when "
                 "they happened."),
    }


async def review(
    session: AsyncSession, *, registration_id: UUID, approve: bool,
    note: str, link_customer_id: Optional[UUID] = None,
    acknowledge_duplicates: bool = False, actor=None,
) -> dict:
    """Approve or refuse an application.

    Approving creates the distributor through the ordinary `create_distributor`
    path -- there is exactly one way a distributor comes into existence -- and,
    where a customer is linked, attributes their existing orders.
    """
    if not note or len(note.strip()) < 3:
        raise HTTPException(
            status_code=400,
            detail="Record why. The applicant is entitled to a reason.")

    row = (await session.execute(
        text("""SELECT * FROM distributor_registrations
                 WHERE id = :r FOR UPDATE"""),
        {"r": str(registration_id)})).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Registration not found.")
    if row["status"] in ("APPROVED", "REJECTED", "DUPLICATE"):
        raise HTTPException(
            status_code=400,
            detail=(f"{row['registration_reference']} was already "
                    f"{row['status'].lower()}."))

    if not approve:
        await session.execute(
            text("""UPDATE distributor_registrations
                       SET status = 'REJECTED', reviewed_by = :by,
                           reviewed_at = NOW(), review_note = :n
                     WHERE id = :r"""),
            {"by": str(actor.id) if actor else None, "n": note.strip(),
             "r": str(registration_id)})
        await audit(session, event_type="REGISTRATION_REJECTED",
                    entity_type="distributor_registration",
                    entity_id=registration_id, actor=actor,
                    reason=note.strip(),
                    new_value={"reference": row["registration_reference"]})
        return {"id": str(registration_id), "status": "REJECTED"}

    created = await dsvc.create_distributor(
        session, legal_name=row["legal_name"],
        entity_type=row["entity_type"], trading_name=row["trading_name"],
        phone=row["phone"], whatsapp=row["whatsapp"], email=row["email"],
        business_address=row["business_address"], state_id=row["state_id"],
        lga_id=row["lga_id"], town=row["town"], cac_number=row["cac_number"],
        tin=row["tin"], years_in_operation=row["years_in_operation"],
        business_type=row["business_type"],
        employee_count=row["employee_count"],
        marketer_count=row["marketer_count"], actor=actor,
        acknowledge_duplicates=acknowledge_duplicates)
    distributor_id = UUID(created["id"])

    history = None
    if link_customer_id is not None:
        # provision_identities links the EXISTING customer rather than creating
        # a new one, which is what keeps this an integration and not a copy.
        await dsvc.provision_identities(
            session, distributor_id=distributor_id,
            link_customer_id=link_customer_id, actor=actor)
        history = await attribute_history(
            session, distributor_id=distributor_id,
            customer_id=link_customer_id, actor=actor)

    await session.execute(
        text("""UPDATE distributor_registrations
                   SET status = 'APPROVED', reviewed_by = :by,
                       reviewed_at = NOW(), review_note = :n,
                       distributor_id = :d, linked_customer_id = CAST(:c AS uuid),
                       orders_attributed = :att
                 WHERE id = :r"""),
        {"by": str(actor.id) if actor else None, "n": note.strip(),
         "d": str(distributor_id),
         "c": str(link_customer_id) if link_customer_id else None,
         "att": history["orders_attributed"] if history else None,
         "r": str(registration_id)})

    await audit(session, event_type="REGISTRATION_APPROVED",
                entity_type="distributor_registration",
                entity_id=registration_id, distributor_id=distributor_id,
                actor=actor, reason=note.strip(),
                new_value={"reference": row["registration_reference"],
                           "distributor_code": created["distributor_code"],
                           "linked_customer": (str(link_customer_id)
                                               if link_customer_id else None),
                           "orders_attributed": (history["orders_attributed"]
                                                 if history else 0)})

    return {
        "id": str(registration_id), "status": "APPROVED",
        "distributor": created,
        "history": history,
        "next": ("The distributor is in DRAFT. Move it through the usual "
                 "lifecycle -- applied, reviewed, approved, active -- before it "
                 "can hold territory or order."),
    }
