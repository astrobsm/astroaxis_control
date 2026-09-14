"""The distributor ordering portal behind a shareable link.

HOW PRICES ARE HIDDEN, AND WHAT THAT IS WORTH
=============================================
The distributor sees no unit prices and no line totals -- only one figure for
the whole basket, once they have chosen everything.

The catalogue query **does not select the price columns at all**. It would have
been easier to select them and drop them from the response model, and that is
precisely what not to do: a response filter is one refactor, one `dict(row)`, one
debug endpoint away from leaking the whole price list. What is never fetched
cannot be returned by accident.

Being straight about the limit of this: **a basket total reveals unit prices to
anyone who wants them.** Quote one carton of an item, then two, and the
difference is the unit price. That is inherent in showing a total at all, not a
flaw in this implementation, and no amount of care here changes it. What the
design does buy is real but narrower: the price list cannot be lifted wholesale,
screenshotted, or forwarded, and a distributor reading the screen is not handed
a per-item column to compare against a competitor's. If unit prices must be
genuinely secret from the person ordering, they cannot be shown a total either,
and that is a commercial decision rather than a technical one.

WHY THE TOTAL IS COMPUTED SERVER-SIDE, ALWAYS
=============================================
The browser never receives the numbers it would need to compute a total, so it
cannot compute one. Every figure shown comes from `quote`, and the order is
priced again from the database at submission rather than trusting anything the
browser sends back. A total that arrived from the client is a price the customer
chose.

THE ORDER IS A REAL SALES ORDER
===============================
`sales_orders` + `sales_order_lines`, priced from `product_pricing` exactly as
the existing public order path prices them, with `sales_channel='DISTRIBUTOR'`.
There is no distributor order table, no staging queue, and nothing to reconcile.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Reused rather than reimplemented: the wholesale unit rules are a commercial
# policy that already lives in the public ordering path, and two copies of a
# minimum-order rule will disagree the first time one is changed.
from app.api.public_orders import (
    _get_default_warehouse, _is_wholesale_only, _norm_unit, _wholesale_min_for,
)

DEFAULT_VALIDITY_DAYS = 30
MAX_VALIDITY_DAYS = 365
MAX_BASKET_LINES = 200

# How many failed-token attempts from one address get their own log row per
# hour. Logging every miss is the right instinct -- a run of them is what
# guessing at links looks like -- but the endpoint is UNAUTHENTICATED, so an
# unbounded row per request is a way for anyone on the internet to fill the
# disk. Past the cap the attempts are still refused; they just stop writing.
# The signal is not lost: hitting the cap is itself the finding.
MISS_LOG_CAP_PER_HOUR = 20


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# Issuing and managing links
# ---------------------------------------------------------------------------

async def issue_link(
    session: AsyncSession, *, distributor_id: UUID, label: str,
    recipient_name: Optional[str] = None, recipient_phone: Optional[str] = None,
    valid_days: int = DEFAULT_VALIDITY_DAYS, base_url: str = "", actor=None,
) -> dict:
    """Create a link. The token is returned ONCE and never stored in the clear.

    The caller must show it to the issuer immediately; there is no endpoint that
    can reveal it afterwards, because there is nothing to reveal it from.
    """
    if not label or len(label.strip()) < 3:
        raise HTTPException(
            status_code=400,
            detail=("Give the link a label saying who it is for -- 'Chinedu, "
                    "Aba depot'. A list of unlabelled links cannot be revoked "
                    "with any confidence six months from now."))
    if not 1 <= valid_days <= MAX_VALIDITY_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"Validity must be between 1 and {MAX_VALIDITY_DAYS} days.")

    dist = (await session.execute(
        text("""SELECT id, distributor_code, legal_name, status, customer_id
                  FROM distributors WHERE id = :d"""),
        {"d": str(distributor_id)})).mappings().first()
    if dist is None:
        raise HTTPException(status_code=404, detail="Distributor not found.")
    if dist["status"] != "ACTIVE":
        raise HTTPException(
            status_code=400,
            detail=(f"{dist['legal_name']} is {dist['status'].lower()}. Only an "
                    f"active distributor can be given an ordering link."))
    if not dist["customer_id"]:
        raise HTTPException(
            status_code=400,
            detail=(f"{dist['legal_name']} has no accounting identity yet, so "
                    f"an order could not be billed to anyone. Activate the "
                    f"distributor first."))

    token = secrets.token_urlsafe(32)
    link_id = uuid.uuid4()
    expires_at = datetime.now(timezone.utc) + timedelta(days=valid_days)

    await session.execute(
        text("""
            INSERT INTO distributor_order_links
                (id, distributor_id, token_sha256, token_hint, label,
                 recipient_name, recipient_phone, expires_at, created_by)
            VALUES (:id, :d, :h, :hint, :label, :rn, :rp, :exp, :by)
        """),
        {"id": str(link_id), "d": str(distributor_id), "h": _hash(token),
         "hint": token[-6:], "label": label.strip(),
         "rn": recipient_name, "rp": recipient_phone, "exp": expires_at,
         "by": str(actor.id) if actor else None},
    )

    from app.services.geography import audit
    await audit(session, event_type="ORDER_LINK_ISSUED",
                entity_type="distributor_order_link", entity_id=link_id,
                distributor_id=distributor_id, actor=actor,
                new_value={"label": label.strip(), "hint": token[-6:],
                           "expires_at": expires_at.isoformat(),
                           "valid_days": valid_days})

    base = (base_url or "").rstrip("/")
    return {
        "id": str(link_id),
        "label": label.strip(),
        "distributor": dist["legal_name"],
        "expires_at": expires_at.isoformat(),
        # Shown once. There is no way back to it.
        "url": f"{base}/order/{token}" if base else f"/order/{token}",
        "token_hint": token[-6:],
        "warning": ("This link is shown once and cannot be recovered. Anyone "
                    "holding it can place orders billed to "
                    f"{dist['legal_name']} until it expires or is revoked. "
                    "Send it to one named person, not a group."),
    }


async def revoke_link(
    session: AsyncSession, *, link_id: UUID, reason: str, actor=None,
) -> dict:
    if not reason or len(reason.strip()) < 3:
        raise HTTPException(
            status_code=400,
            detail="Say why the link is being revoked; it stays on the record.")

    row = (await session.execute(
        text("""SELECT id, label, distributor_id, revoked_at
                  FROM distributor_order_links WHERE id = :l FOR UPDATE"""),
        {"l": str(link_id)})).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Link not found.")
    if row["revoked_at"] is not None:
        raise HTTPException(
            status_code=400, detail="That link is already revoked.")

    await session.execute(
        text("""UPDATE distributor_order_links
                   SET revoked_at = NOW(), revoked_by = :by, revoke_reason = :r
                 WHERE id = :l"""),
        {"by": str(actor.id) if actor else None, "r": reason.strip(),
         "l": str(link_id)})
    await _log(session, link_id=link_id, event_type="REVOKED",
               detail=reason.strip())

    from app.services.geography import audit
    await audit(session, event_type="ORDER_LINK_REVOKED",
                entity_type="distributor_order_link", entity_id=link_id,
                distributor_id=row["distributor_id"], actor=actor,
                reason=reason.strip(), new_value={"label": row["label"]})
    return {"id": str(link_id), "revoked": True}


async def list_links(
    session: AsyncSession, *, distributor_id: Optional[UUID] = None,
    include_dead: bool = False,
) -> list[dict]:
    clauses, params = ["1 = 1"], {}
    if distributor_id:
        clauses.append("l.distributor_id = :d")
        params["d"] = str(distributor_id)
    if not include_dead:
        clauses.append("l.revoked_at IS NULL AND l.expires_at > NOW()")

    rows = (await session.execute(
        text(f"""SELECT l.id, l.label, l.token_hint, l.recipient_name,
                        l.recipient_phone, l.expires_at, l.revoked_at,
                        l.revoke_reason, l.last_used_at, l.use_count,
                        l.order_count, l.created_at,
                        (l.revoked_at IS NULL AND l.expires_at > NOW())
                            AS is_live,
                        d.distributor_code, d.legal_name,
                        u.full_name AS issued_by
                   FROM distributor_order_links l
                   JOIN distributors d ON d.id = l.distributor_id
                   LEFT JOIN users u ON u.id = l.created_by
                  WHERE {' AND '.join(clauses)}
                  ORDER BY l.created_at DESC"""),
        params)).mappings().all()
    return [dict(r) | {"id": str(r["id"])} for r in rows]


async def link_activity(
    session: AsyncSession, *, link_id: UUID, limit: int = 100,
) -> list[dict]:
    rows = (await session.execute(
        text("""SELECT e.event_type, e.detail, e.ip_address, e.user_agent,
                       e.created_at, o.order_number
                  FROM distributor_order_link_events e
                  LEFT JOIN sales_orders o ON o.id = e.sales_order_id
                 WHERE e.link_id = :l
                 ORDER BY e.created_at DESC LIMIT :lim"""),
        {"l": str(link_id), "lim": limit})).mappings().all()
    return [dict(r) for r in rows]


async def _log(
    session: AsyncSession, *, link_id: Optional[UUID], event_type: str,
    detail: Optional[str] = None, token_hint: Optional[str] = None,
    sales_order_id: Optional[UUID] = None, ip: str = "", user_agent: str = "",
) -> None:
    await session.execute(
        text("""
            INSERT INTO distributor_order_link_events
                (id, link_id, token_hint, event_type, detail, sales_order_id,
                 ip_address, user_agent)
            VALUES (gen_random_uuid(), CAST(:l AS uuid), :hint, :t, :detail,
                    CAST(:o AS uuid), :ip, :ua)
        """),
        {"l": str(link_id) if link_id else None, "hint": token_hint,
         "t": event_type, "detail": detail,
         "o": str(sales_order_id) if sales_order_id else None,
         "ip": (ip or "")[:64] or None, "ua": (user_agent or "")[:500] or None},
    )


async def _log_miss(
    session: AsyncSession, *, token_hint: Optional[str] = None,
    detail: Optional[str] = None, ip: str = "", user_agent: str = "",
) -> None:
    """Log a failed token attempt, but not without limit.

    This is called from an UNAUTHENTICATED endpoint. Writing a row per request
    means anyone on the internet can grow the table without bound, so a
    security log becomes a denial-of-service vector -- the failure mode where
    the monitoring is the outage.

    Past MISS_LOG_CAP_PER_HOUR from one address the attempts are still refused
    and simply stop being written. The signal survives: twenty misses from one
    address in an hour already says everything a hundred would, and the final
    row records that the cap was reached rather than going quiet.
    """
    recent = (await session.execute(
        text("""SELECT COUNT(*) FROM distributor_order_link_events
                 WHERE event_type = 'NOT_FOUND'
                   AND ip_address = :ip
                   AND created_at > NOW() - INTERVAL '1 hour'"""),
        {"ip": (ip or "")[:64] or None})).scalar() or 0

    if recent > MISS_LOG_CAP_PER_HOUR:
        return
    if recent == MISS_LOG_CAP_PER_HOUR:
        detail = (f"{MISS_LOG_CAP_PER_HOUR}+ failed attempts from this address "
                  f"in an hour; further attempts are refused but no longer "
                  f"logged individually.")
        token_hint = None

    await _log(session, link_id=None, event_type="NOT_FOUND",
               detail=detail, token_hint=token_hint, ip=ip,
               user_agent=user_agent)


# ---------------------------------------------------------------------------
# Resolving a token
# ---------------------------------------------------------------------------

async def resolve(
    session: AsyncSession, *, token: str, ip: str = "", user_agent: str = "",
    log_open: bool = False,
) -> dict:
    """Turn a token into the distributor it orders for, or refuse.

    Every refusal is logged, including tokens that match nothing -- a run of
    those is what someone guessing at links looks like, and it is invisible
    otherwise.
    """
    if not token or len(token) < 20:
        await _log_miss(session, detail="malformed token", ip=ip,
                        user_agent=user_agent)
        raise HTTPException(status_code=404, detail="This link is not valid.")

    row = (await session.execute(
        text("""SELECT l.id, l.distributor_id, l.label, l.token_hint,
                       l.expires_at, l.revoked_at,
                       d.legal_name, d.trading_name, d.distributor_code,
                       d.status AS distributor_status, d.customer_id,
                       d.warehouse_id
                  FROM distributor_order_links l
                  JOIN distributors d ON d.id = l.distributor_id
                 WHERE l.token_sha256 = :h"""),
        {"h": _hash(token)})).mappings().first()

    if row is None:
        await _log_miss(session, token_hint=token[-6:], ip=ip,
                        user_agent=user_agent)
        raise HTTPException(status_code=404, detail="This link is not valid.")

    if row["revoked_at"] is not None:
        await _log(session, link_id=row["id"], event_type="REVOKED",
                   detail="use attempted after revocation", ip=ip,
                   user_agent=user_agent)
        raise HTTPException(
            status_code=403,
            detail=("This ordering link has been withdrawn. Please contact "
                    "Bonnesante Medicals for a new one."))

    if row["expires_at"] <= datetime.now(timezone.utc):
        await _log(session, link_id=row["id"], event_type="EXPIRED", ip=ip,
                   user_agent=user_agent)
        raise HTTPException(
            status_code=403,
            detail=("This ordering link has expired. Please contact "
                    "Bonnesante Medicals for a new one."))

    if row["distributor_status"] != "ACTIVE":
        await _log(session, link_id=row["id"], event_type="REJECTED",
                   detail=f"distributor is {row['distributor_status']}", ip=ip,
                   user_agent=user_agent)
        raise HTTPException(
            status_code=403,
            detail=("Ordering is paused on this account. Please contact "
                    "Bonnesante Medicals."))

    if log_open:
        await _log(session, link_id=row["id"], event_type="OPENED", ip=ip,
                   user_agent=user_agent)
        await session.execute(
            text("""UPDATE distributor_order_links
                       SET last_used_at = NOW(), use_count = use_count + 1
                     WHERE id = :l"""), {"l": str(row["id"])})

    return dict(row)


# ---------------------------------------------------------------------------
# Catalogue -- deliberately without prices
# ---------------------------------------------------------------------------

async def catalogue(session: AsyncSession) -> list[dict]:
    """What can be ordered. NO PRICE COLUMNS ARE SELECTED.

    See the module docstring: the price columns are absent from the query, not
    filtered from the result. The row that reaches the response layer has no
    price in it to leak.

    A variant with no wholesale price is EXCLUDED rather than shown as
    unavailable -- offering something that cannot be priced only produces an
    error after the distributor has built a basket around it.
    """
    rows = (await session.execute(
        text("""
            SELECT p.id AS product_id, p.sku, p.name, p.description,
                   p.manufacturer, pp.unit,
                   COALESCE(
                       (SELECT SUM(sl.current_stock) FROM stock_levels sl
                         WHERE sl.product_id = p.id), 0) > 0 AS in_stock
              FROM product_pricing pp
              JOIN products p ON p.id = pp.product_id
             WHERE pp.unit IS NOT NULL
               AND COALESCE(pp.wholesale_price, 0) > 0
             ORDER BY p.name, pp.unit
        """),
    )).mappings().all()

    return [
        {
            "product_id": str(r["product_id"]),
            "sku": r["sku"],
            "name": r["name"],
            "description": r["description"],
            "manufacturer": r["manufacturer"],
            "unit": r["unit"],
            "in_stock": bool(r["in_stock"]),
            "minimum_quantity": _wholesale_min_for(_norm_unit(r["unit"])),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Pricing a basket
# ---------------------------------------------------------------------------

async def _price_basket(session: AsyncSession, items: list) -> tuple:
    """Price every line from the database. Returns (lines, total).

    `lines` carry prices and never leave this module's callers unfiltered --
    `quote` returns only the total. They exist because the order writer needs
    them, and computing them twice from two code paths is how a quoted total
    and an invoiced total come to differ.
    """
    if not items:
        raise HTTPException(
            status_code=400, detail="Nothing has been selected yet.")
    if len(items) > MAX_BASKET_LINES:
        raise HTTPException(
            status_code=400,
            detail=f"An order can hold at most {MAX_BASKET_LINES} lines.")

    wanted = set()
    for it in items:
        try:
            wanted.add(UUID(str(it.product_id)))
        except (ValueError, AttributeError, TypeError):
            raise HTTPException(
                status_code=400,
                detail=f"Unrecognised product: {it.product_id}")

    products = {
        r["id"]: dict(r) for r in (await session.execute(
            text("SELECT id, name, sku FROM products WHERE id = ANY(:ids)")
            .bindparams(ids=[str(p) for p in wanted]))).mappings().all()
    }

    pricing = {
        (r["product_id"], _norm_unit(r["unit"])): r
        for r in (await session.execute(
            text("""SELECT product_id, unit, wholesale_price
                      FROM product_pricing WHERE product_id = ANY(:ids)""")
            .bindparams(ids=[str(p) for p in wanted]))).mappings().all()
    }

    lines, total = [], Decimal("0")
    for it in items:
        pid = UUID(str(it.product_id))
        product = products.get(pid)
        if product is None:
            raise HTTPException(
                status_code=404, detail=f"Product not found: {it.product_id}")

        unit_norm = _norm_unit(it.unit)
        price_row = pricing.get((pid, unit_norm))
        if price_row is None:
            raise HTTPException(
                status_code=400,
                detail=(f"{product['name']} is not sold in '{it.unit}'. "
                        f"Please choose one of the listed pack sizes."))

        try:
            qty = Decimal(str(it.quantity))
        except (InvalidOperation, TypeError):
            raise HTTPException(
                status_code=400,
                detail=f"Quantity for {product['name']} is not a number.")
        if qty <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"Quantity for {product['name']} must be more than zero.")

        minimum = _wholesale_min_for(unit_norm)
        if qty < Decimal(minimum):
            raise HTTPException(
                status_code=400,
                detail=(f"{product['name']} is supplied from {minimum} "
                        f"{unit_norm} upwards."))

        price = Decimal(str(price_row["wholesale_price"] or 0))
        if price <= 0:
            # Should be unreachable: the catalogue excludes unpriced variants.
            raise HTTPException(
                status_code=400,
                detail=(f"{product['name']} ({it.unit}) cannot be ordered "
                        f"through this link at present."))

        line_total = money(price * qty)
        lines.append({
            "product_id": pid, "name": product["name"], "sku": product["sku"],
            "unit": it.unit, "quantity": qty, "unit_price": price,
            "line_total": line_total,
        })
        total += line_total

    return lines, money(total)


async def quote(
    session: AsyncSession, *, token: str, items: list, ip: str = "",
    user_agent: str = "",
) -> dict:
    """One number for the whole basket. No unit prices, no line totals.

    The per-line figures are computed and then deliberately not returned. The
    line list here carries quantities only, so the browser has what it needs to
    show the basket back and nothing it could use to derive a price list.
    """
    link = await resolve(session, token=token, ip=ip, user_agent=user_agent)
    lines, total = await _price_basket(session, items)

    await _log(session, link_id=link["id"], event_type="QUOTED",
               detail=f"{len(lines)} line(s)", ip=ip, user_agent=user_agent)

    return {
        "distributor": link["legal_name"],
        "line_count": len(lines),
        "items": [
            {"product_id": str(ln["product_id"]), "name": ln["name"],
             "unit": ln["unit"], "quantity": str(ln["quantity"])}
            for ln in lines
        ],
        "total": str(total),
        "currency": "NGN",
        "note": ("This is the value of the order as selected. Bonnesante "
                 "Medicals will confirm it before despatch."),
    }


# ---------------------------------------------------------------------------
# Placing the order
# ---------------------------------------------------------------------------

async def place_order(
    session: AsyncSession, *, token: str, items: list,
    notes: Optional[str] = None, required_date=None, ip: str = "",
    user_agent: str = "",
) -> dict:
    """Write a real sales order. Priced from the database, never from the client.

    Nothing about money crosses the wire inbound. The browser sends products,
    units and quantities; every price is looked up here. A total that arrived
    from the client would be a price the customer chose.
    """
    link = await resolve(session, token=token, ip=ip, user_agent=user_agent)
    lines, total = await _price_basket(session, items)

    warehouse = await _get_default_warehouse(session)
    order_id = uuid.uuid4()
    order_number = (f"DST-{datetime.now(timezone.utc):%Y%m%d}"
                    f"-{str(uuid.uuid4())[:6].upper()}")

    note_parts = [f"[DISTRIBUTOR: {link['distributor_code']}]",
                  f"[LINK: {link['token_hint']}]"]
    if notes:
        note_parts.append(notes.strip()[:500])

    await session.execute(
        text("""
            INSERT INTO sales_orders
                (id, order_number, customer_id, warehouse_id, status,
                 payment_status, total_amount, notes, required_date,
                 distributor_id, sales_channel, order_link_id)
            VALUES (:id, :num, :cust, :wh, 'pending', 'unpaid', :total, :notes,
                    :req, :dist, 'DISTRIBUTOR', :link)
        """),
        {"id": str(order_id), "num": order_number,
         "cust": str(link["customer_id"]), "wh": str(warehouse.id),
         "total": total, "notes": " ".join(note_parts), "req": required_date,
         "dist": str(link["distributor_id"]), "link": str(link["id"])},
    )

    for ln in lines:
        await session.execute(
            text("""
                INSERT INTO sales_order_lines
                    (id, sales_order_id, product_id, unit, quantity,
                     unit_price, line_total)
                VALUES (gen_random_uuid(), :o, :p, :u, :q, :price, :lt)
            """),
            {"o": str(order_id), "p": str(ln["product_id"]), "u": ln["unit"],
             "q": ln["quantity"], "price": ln["unit_price"],
             "lt": ln["line_total"]},
        )

    await session.execute(
        text("""UPDATE distributor_order_links
                   SET last_used_at = NOW(), use_count = use_count + 1,
                       order_count = order_count + 1
                 WHERE id = :l"""), {"l": str(link["id"])})
    await _log(session, link_id=link["id"], event_type="ORDERED",
               detail=f"{order_number}: {len(lines)} line(s)",
               sales_order_id=order_id, ip=ip, user_agent=user_agent)

    credit = await _credit_position(session, customer_id=link["customer_id"],
                                    pending=total)

    return {
        "order_number": order_number,
        "line_count": len(lines),
        "total": str(total),
        "currency": "NGN",
        "status": "pending",
        "credit": credit,
        "message": (f"Order {order_number} has been received. Bonnesante "
                    f"Medicals will confirm it and arrange despatch."),
    }


async def _credit_position(
    session: AsyncSession, *, customer_id, pending: Decimal,
) -> dict:
    """What this order does to the account, using the existing AR figures.

    Reported, never enforced at the portal. A distributor discovering mid-basket
    that they are over their limit, from a screen that cannot tell them by how
    much or what to pay, is worse than useless -- the company confirms every
    order anyway, and that is where a credit decision belongs. What this does is
    make sure the confirming staff member sees it.

    RUN INSIDE A SAVEPOINT, and this is not incidental. The first version caught
    the failure and returned `{"checked": False}`, which looked harmless and was
    not: a failed statement poisons the whole PostgreSQL transaction, so the
    caller's later commit rolled back THE ORDER ITSELF. The distributor was
    handed an order number for an order that did not exist. A savepoint confines
    the damage to this lookup, which is the only part that is genuinely
    optional.
    """
    from app.services.customer_debt import outstanding_for_customer

    try:
        async with session.begin_nested():
            position = await outstanding_for_customer(
                session, customer_id=UUID(str(customer_id)))
    except Exception as exc:
        # The order stands. Only the advisory figure is missing, and the reason
        # is said out loud rather than reported as an unexplained blank.
        return {"checked": False,
                "reason": f"Credit position unavailable: {type(exc).__name__}"}

    limit = money(position.get("credit_limit") or 0)
    outstanding = money(position.get("total_outstanding") or 0)
    if limit <= 0:
        return {"checked": True, "limit_set": False,
                "outstanding": str(outstanding)}

    projected = money(outstanding + pending)
    return {
        "checked": True,
        "limit_set": True,
        "credit_limit": str(limit),
        "outstanding": str(outstanding),
        "projected": str(projected),
        "over_limit": projected > limit,
        "headroom": str(money(limit - projected)),
    }


async def distributor_orders(
    session: AsyncSession, *, distributor_id: UUID, limit: int = 100,
) -> list[dict]:
    """Orders placed by this distributor, from the real sales_orders table."""
    rows = (await session.execute(
        text("""SELECT o.id, o.order_number, o.status, o.payment_status,
                       o.total_amount, o.order_date, o.notes,
                       l.label AS link_label, l.token_hint,
                       (SELECT COUNT(*) FROM sales_order_lines sol
                         WHERE sol.sales_order_id = o.id) AS line_count
                  FROM sales_orders o
                  LEFT JOIN distributor_order_links l ON l.id = o.order_link_id
                 WHERE o.distributor_id = :d
                 ORDER BY o.order_date DESC LIMIT :lim"""),
        {"d": str(distributor_id), "lim": limit})).mappings().all()
    return [dict(r) | {"id": str(r["id"])} for r in rows]
