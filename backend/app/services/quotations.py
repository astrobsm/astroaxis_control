"""Quotations: preparing a price, standing behind it, and turning it into an order.

THE PRICE IS TAKEN ONCE
=======================
Prices are read from `product_pricing` -- the same source as the in-app price
list and the public order page, so there is one answer to what a product costs
-- and then **copied onto the line**. Everything after that reads the copy.

This is the point of the module. A quotation is a promise with a date on it,
and a promise that changes when the price list changes is not a promise. The
cost is a little duplication; the alternative is a customer being quoted one
figure and invoiced another, which is how a company loses an account.

WHAT CAN HAPPEN TO A QUOTE
==========================
    DRAFT     being prepared, nobody has seen it
    SENT      the customer has it, the clock is running
    ACCEPTED  they said yes; it may now become an order
    CONVERTED an order exists, and the quote points at it
    DECLINED  they said no, with a reason worth keeping
    CANCELLED withdrawn by us

Expiry is not in that list, because it is not a state anybody sets. A quote is
expired when `valid_until` has passed, computed when somebody looks. A nightly
job that marks quotes expired is a job that can fail, and a quote that still
looks live three days after it died is worse than one that never existed.

DISCOUNTS ABOVE A THRESHOLD NEED A SECOND NAME
==============================================
`QUOTATION_MAX_DISCOUNT_PERCENT` is what a salesperson may give on their own.
Above it, an administrator's name goes on the quote. The threshold is a
setting rather than a constant because what counts as unusual is a commercial
judgement that will change.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.messaging import get_setting

CENT = Decimal("0.01")

OPEN_STATUSES = ("SENT", "ACCEPTED")
# What a quote may become, from where. Anything not listed is refused, so a
# new state cannot be reached by accident from an old one.
TRANSITIONS = {
    "DRAFT": {"SENT", "CANCELLED"},
    "SENT": {"ACCEPTED", "DECLINED", "CANCELLED"},
    "ACCEPTED": {"CONVERTED", "CANCELLED"},
    "DECLINED": set(),
    "EXPIRED": set(),
    "CONVERTED": set(),
    "CANCELLED": set(),
}


def money(value) -> Decimal:
    if value is None:
        return Decimal("0.00")
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


def _norm_unit(unit: Optional[str]) -> str:
    return (unit or "unit").strip().lower()


async def _price_for(
    session: AsyncSession, *, product_ids: list, customer_type: str,
) -> dict:
    """The current price per (product, unit), from the one pricing table."""
    rows = (await session.execute(
        text("""SELECT pp.product_id, pp.unit, pp.retail_price,
                       pp.wholesale_price, p.name
                  FROM product_pricing pp
                  JOIN products p ON p.id = pp.product_id
                 WHERE pp.product_id = ANY(CAST(:ids AS uuid[]))"""),
        {"ids": [str(p) for p in product_ids]})).mappings().all()

    out = {}
    for r in rows:
        price = (r["wholesale_price"] if customer_type == "wholesale"
                 else r["retail_price"])
        out[(str(r["product_id"]), _norm_unit(r["unit"]))] = {
            "price": money(price), "name": r["name"]}
    return out


async def _recalculate(session: AsyncSession, *, quotation_id: UUID) -> dict:
    """Add the lines up and write the totals back.

    Totals are stored rather than summed on read because a quotation is a
    document: the figure at the bottom of the page the customer received must
    not move because somebody later edited a line.
    """
    row = (await session.execute(
        text("""SELECT COALESCE(SUM(line_total), 0) AS subtotal
                  FROM quotation_lines WHERE quotation_id = :q"""),
        {"q": str(quotation_id)})).mappings().first()
    subtotal = money(row["subtotal"])

    q = (await session.execute(
        text("""SELECT discount_percent, delivery_charge FROM quotations
                 WHERE id = :q"""), {"q": str(quotation_id)})).mappings().first()

    discount = money(subtotal * money(q["discount_percent"]) / Decimal("100"))
    total = money(subtotal - discount + money(q["delivery_charge"]))

    await session.execute(
        text("""UPDATE quotations
                   SET subtotal = :s, discount_amount = :d,
                       total_amount = :t, updated_at = NOW()
                 WHERE id = :q"""),
        {"s": str(subtotal), "d": str(discount), "t": str(total),
         "q": str(quotation_id)})
    return {"subtotal": subtotal, "discount_amount": discount,
            "total_amount": total}


async def _event(session: AsyncSession, *, quotation_id: UUID,
                 from_status: Optional[str], to_status: str,
                 note: Optional[str] = None, actor=None) -> None:
    await session.execute(
        text("""INSERT INTO quotation_events
                    (quotation_id, from_status, to_status, note, actor_id,
                     actor_name)
                VALUES (:q, :f, :t, :n, :a, :an)"""),
        {"q": str(quotation_id), "f": from_status, "t": to_status, "n": note,
         "a": str(actor.id) if actor is not None else None,
         "an": ((getattr(actor, "full_name", None)
                 or getattr(actor, "username", None)) if actor else None)})


# ---------------------------------------------------------------------------
# Creating
# ---------------------------------------------------------------------------

async def create(
    session: AsyncSession, *, customer_id: UUID, items: list,
    customer_type: str = "retail", valid_days: Optional[int] = None,
    discount_percent: Decimal = Decimal("0"), delivery_charge=0,
    terms: Optional[str] = None, notes: Optional[str] = None, actor=None,
) -> dict:
    """Prepare a quotation, taking each price once and keeping it."""
    customer_type = (customer_type or "retail").strip().lower()
    if customer_type not in ("retail", "wholesale"):
        raise HTTPException(
            status_code=400,
            detail="Customer type must be retail or wholesale.")
    if not items:
        raise HTTPException(
            status_code=400, detail="A quotation needs at least one line.")

    customer = (await session.execute(
        text("""SELECT id, name, merged_into_id FROM customers
                 WHERE id = :i"""), {"i": str(customer_id)})).mappings().first()
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found.")
    if customer["merged_into_id"]:
        raise HTTPException(
            status_code=400,
            detail=("That customer record has been merged into another. Quote "
                    "the surviving record."))

    discount_percent = money(discount_percent)
    max_discount = await get_setting(
        session, "QUOTATION_MAX_DISCOUNT_PERCENT", 10)
    is_admin = getattr(actor, "role", None) == "admin"
    if discount_percent > Decimal(str(max_discount)) and not is_admin:
        raise HTTPException(
            status_code=403,
            detail=(f"A discount above {max_discount}% has to be approved by "
                    f"an administrator. This one is {discount_percent}%."))

    days = valid_days or await get_setting(session, "QUOTATION_VALID_DAYS", 14)
    valid_until = date.today() + timedelta(days=int(days))

    pricing = await _price_for(
        session, product_ids=[i["product_id"] for i in items],
        customer_type=customer_type)

    qid = uuid4()
    number = (f"QT-{datetime.now(timezone.utc):%Y%m%d}"
              f"-{uuid4().hex[:6].upper()}")
    name = ((getattr(actor, "full_name", None)
             or getattr(actor, "username", None)) if actor else None)

    await session.execute(
        text("""INSERT INTO quotations
                    (id, quotation_number, customer_id, status, customer_type,
                     valid_until, discount_percent, delivery_charge, terms,
                     notes, prepared_by, prepared_by_name,
                     discount_approved_by, discount_approved_by_name)
                VALUES (:i, :n, :c, 'DRAFT', :ct, :v, :dp, :dc, :te, :no,
                        :by, :byn, :ap, :apn)"""),
        {"i": str(qid), "n": number, "c": str(customer_id), "ct": customer_type,
         "v": valid_until, "dp": str(discount_percent),
         "dc": str(money(delivery_charge)), "te": terms, "no": notes,
         "by": str(actor.id) if actor is not None else None, "byn": name,
         "ap": (str(actor.id) if is_admin and discount_percent > 0
                and actor is not None else None),
         "apn": name if is_admin and discount_percent > 0 else None})

    for seq, item in enumerate(items):
        key = (str(item["product_id"]), _norm_unit(item.get("unit")))
        found = pricing.get(key)
        if found is None:
            raise HTTPException(
                status_code=400,
                detail=(f"No price is set for that product in unit "
                        f"'{item.get('unit') or 'unit'}'. Set one on the "
                        f"price list first."))

        qty = money(item["quantity"])
        if qty <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"Quantity must be more than zero for {found['name']}.")

        # The price is COPIED here. Everything afterwards reads the copy.
        unit_price = (money(item["unit_price"])
                      if item.get("unit_price") is not None else found["price"])
        if unit_price <= 0:
            raise HTTPException(
                status_code=400,
                detail=(f"{found['name']} has no price for this customer "
                        f"category. Set one before quoting it."))

        await session.execute(
            text("""INSERT INTO quotation_lines
                        (quotation_id, product_id, product_name, unit,
                         quantity, unit_price, line_total, line_note, sequence)
                    VALUES (:q, :p, :pn, :u, :qty, :up, :lt, :ln, :s)"""),
            {"q": str(qid), "p": str(item["product_id"]), "pn": found["name"],
             "u": _norm_unit(item.get("unit")), "qty": str(qty),
             "up": str(unit_price), "lt": str(money(qty * unit_price)),
             "ln": item.get("note"), "s": seq})

    totals = await _recalculate(session, quotation_id=qid)
    await _event(session, quotation_id=qid, from_status=None,
                 to_status="DRAFT", actor=actor)

    return {
        "id": str(qid), "quotation_number": number,
        "customer": customer["name"], "status": "DRAFT",
        "valid_until": valid_until.isoformat(),
        "total_amount": float(totals["total_amount"]),
        "note": ("Prepared as a draft. The prices on it are fixed now and "
                 "will not change if the price list does."),
    }


# ---------------------------------------------------------------------------
# Moving it along
# ---------------------------------------------------------------------------

async def set_status(
    session: AsyncSession, *, quotation_id: UUID, status: str,
    note: Optional[str] = None, actor=None,
) -> dict:
    """Move a quotation to a new state, if it may go there."""
    status = (status or "").upper()
    q = (await session.execute(
        text("""SELECT id, quotation_number, status, valid_until
                  FROM quotations WHERE id = :i FOR UPDATE"""),
        {"i": str(quotation_id)})).mappings().first()
    if q is None:
        raise HTTPException(status_code=404, detail="Quotation not found.")

    allowed = TRANSITIONS.get(q["status"], set())
    if status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=(f"{q['quotation_number']} is {q['status']}; it cannot "
                    f"become {status}."
                    + (f" It may become: {', '.join(sorted(allowed))}."
                       if allowed else " It is in a final state.")))

    # Accepting after the date has passed is refused rather than quietly
    # allowed: the price on it was only promised until then.
    if status == "ACCEPTED" and q["valid_until"] < date.today():
        raise HTTPException(
            status_code=400,
            detail=(f"{q['quotation_number']} expired on {q['valid_until']}. "
                    f"Prepare a new quotation at current prices rather than "
                    f"honouring a price that was only promised until then."))

    if status == "DECLINED" and not (note or "").strip():
        raise HTTPException(
            status_code=400,
            detail=("Record why it was declined. A declined quote with no "
                    "reason teaches nobody anything."))

    sets = ["status = :s", "updated_at = NOW()"]
    params = {"s": status, "i": str(quotation_id), "n": note}
    if status == "SENT":
        sets.append("sent_at = NOW()")
    if status in ("ACCEPTED", "DECLINED", "CANCELLED"):
        sets.append("decided_at = NOW()")
        sets.append("decision_note = :n")

    await session.execute(
        text(f"UPDATE quotations SET {', '.join(sets)} WHERE id = :i"), params)
    await _event(session, quotation_id=quotation_id,
                 from_status=q["status"], to_status=status, note=note,
                 actor=actor)

    return {"id": str(quotation_id), "quotation_number": q["quotation_number"],
            "status": status}


async def convert(
    session: AsyncSession, *, quotation_id: UUID, actor=None,
) -> dict:
    """Turn an accepted quotation into a sales order.

    The order is created `pending`, exactly as the public order page does, so
    it joins the existing confirmation workflow rather than bypassing it. No
    stock moves and none is reserved: that happens when staff confirm.
    """
    q = (await session.execute(
        text("""SELECT id, quotation_number, customer_id, status, valid_until,
                       total_amount, notes
                  FROM quotations WHERE id = :i FOR UPDATE"""),
        {"i": str(quotation_id)})).mappings().first()
    if q is None:
        raise HTTPException(status_code=404, detail="Quotation not found.")
    if q["status"] != "ACCEPTED":
        raise HTTPException(
            status_code=400,
            detail=(f"{q['quotation_number']} is {q['status']}. Only an "
                    f"ACCEPTED quotation becomes an order -- that is the "
                    f"record of the customer agreeing to the price."))

    warehouse = (await session.execute(
        text("SELECT id FROM warehouses ORDER BY created_at LIMIT 1")
    )).scalar()

    order_id = uuid4()
    order_number = (f"SO-{datetime.now(timezone.utc):%Y%m%d}"
                    f"-{uuid4().hex[:6].upper()}")

    await session.execute(
        text("""INSERT INTO sales_orders
                    (id, order_number, customer_id, warehouse_id, status,
                     payment_status, order_date, total_amount, notes,
                     sales_channel, quotation_id, created_by)
                VALUES (:i, :n, :c, :w, 'pending', 'unpaid', NOW(), :t, :no,
                        'QUOTATION', :q, :by)"""),
        {"i": str(order_id), "n": order_number, "c": str(q["customer_id"]),
         "w": str(warehouse) if warehouse else None,
         "t": str(money(q["total_amount"])),
         "no": f"From quotation {q['quotation_number']}"
               + (f". {q['notes']}" if q["notes"] else ""),
         "q": str(quotation_id),
         "by": str(actor.id) if actor is not None else None})

    lines = (await session.execute(
        text("""SELECT product_id, unit, quantity, unit_price, line_total
                  FROM quotation_lines WHERE quotation_id = :q
                 ORDER BY sequence"""),
        {"q": str(quotation_id)})).mappings().all()

    for line in lines:
        await session.execute(
            text("""INSERT INTO sales_order_lines
                        (id, sales_order_id, product_id, unit, quantity,
                         unit_price, line_total)
                    VALUES (:i, :o, :p, :u, :q, :up, :lt)"""),
            {"i": str(uuid4()), "o": str(order_id), "p": str(line["product_id"]),
             "u": line["unit"], "q": str(line["quantity"]),
             "up": str(line["unit_price"]), "lt": str(line["line_total"])})

    await session.execute(
        text("""UPDATE quotations
                   SET status = 'CONVERTED', converted_order_id = :o,
                       updated_at = NOW()
                 WHERE id = :i"""),
        {"o": str(order_id), "i": str(quotation_id)})
    await _event(session, quotation_id=quotation_id, from_status="ACCEPTED",
                 to_status="CONVERTED",
                 note=f"Order {order_number}", actor=actor)

    return {
        "quotation_number": q["quotation_number"],
        "order_id": str(order_id), "order_number": order_number,
        "total_amount": float(money(q["total_amount"])),
        "lines": len(lines),
        "note": (f"Order {order_number} created as pending. Staff confirm it "
                 f"in the usual way; no stock has moved."),
    }


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _expired(row) -> bool:
    return (row["status"] in OPEN_STATUSES
            and row["valid_until"] < date.today())


async def get(session: AsyncSession, *, quotation_id: UUID) -> dict:
    q = (await session.execute(
        text("""SELECT q.*, c.name AS customer_name, c.phone, c.email,
                       c.address, o.order_number
                  FROM quotations q
                  JOIN customers c ON c.id = q.customer_id
             LEFT JOIN sales_orders o ON o.id = q.converted_order_id
                 WHERE q.id = :i"""),
        {"i": str(quotation_id)})).mappings().first()
    if q is None:
        raise HTTPException(status_code=404, detail="Quotation not found.")

    lines = (await session.execute(
        text("""SELECT product_name, unit, quantity, unit_price, line_total,
                       line_note
                  FROM quotation_lines WHERE quotation_id = :q
                 ORDER BY sequence"""),
        {"q": str(quotation_id)})).mappings().all()

    events = (await session.execute(
        text("""SELECT from_status, to_status, note, actor_name, created_at
                  FROM quotation_events WHERE quotation_id = :q
                 ORDER BY created_at DESC"""),
        {"q": str(quotation_id)})).mappings().all()

    expired = _expired(q)
    return {
        "id": str(q["id"]), "quotation_number": q["quotation_number"],
        "status": q["status"],
        "is_expired": expired,
        "customer": {"id": str(q["customer_id"]), "name": q["customer_name"],
                     "phone": q["phone"], "email": q["email"],
                     "address": q["address"]},
        "customer_type": q["customer_type"],
        "valid_until": str(q["valid_until"]),
        "days_left": (q["valid_until"] - date.today()).days,
        "subtotal": float(money(q["subtotal"])),
        "discount_percent": float(money(q["discount_percent"])),
        "discount_amount": float(money(q["discount_amount"])),
        "delivery_charge": float(money(q["delivery_charge"])),
        "total_amount": float(money(q["total_amount"])),
        "terms": q["terms"], "notes": q["notes"],
        "prepared_by": q["prepared_by_name"],
        "discount_approved_by": q["discount_approved_by_name"],
        "converted_order": q["order_number"],
        "lines": [{"product_name": l["product_name"], "unit": l["unit"],
                   "quantity": float(l["quantity"]),
                   "unit_price": float(money(l["unit_price"])),
                   "line_total": float(money(l["line_total"])),
                   "note": l["line_note"]} for l in lines],
        "history": [{"from": e["from_status"], "to": e["to_status"],
                     "note": e["note"], "actor": e["actor_name"],
                     "at": e["created_at"].isoformat()} for e in events],
        "may_become": sorted(TRANSITIONS.get(q["status"], set())),
        "note": ("The prices on this quotation were fixed when it was "
                 "prepared and do not follow the price list."
                 + (" It has expired: a new quotation at current prices is "
                    "the right answer rather than honouring this one."
                    if expired else "")),
    }


async def listing(
    session: AsyncSession, *, status: Optional[str] = None,
    customer_id: Optional[UUID] = None, limit: int = 100,
) -> dict:
    parts, params = [], {"l": limit}
    if status:
        parts.append("q.status = :s")
        params["s"] = status.upper()
    if customer_id:
        parts.append("q.customer_id = :c")
        params["c"] = str(customer_id)
    where = ("WHERE " + " AND ".join(parts)) if parts else ""

    rows = (await session.execute(text(f"""
        SELECT q.id, q.quotation_number, q.status, q.valid_until,
               q.total_amount, q.created_at, q.prepared_by_name,
               c.name AS customer_name
          FROM quotations q JOIN customers c ON c.id = q.customer_id
        {where}
         ORDER BY q.created_at DESC LIMIT :l
    """), params)).mappings().all()

    out = []
    for r in rows:
        expired = _expired(r)
        out.append({
            "id": str(r["id"]), "quotation_number": r["quotation_number"],
            "customer": r["customer_name"], "status": r["status"],
            "is_expired": expired,
            "valid_until": str(r["valid_until"]),
            "days_left": (r["valid_until"] - date.today()).days,
            "total_amount": float(money(r["total_amount"])),
            "prepared_by": r["prepared_by_name"],
            "created_at": str(r["created_at"])[:10],
        })

    return {
        "quotations": out,
        "open_value": float(money(sum(
            Decimal(str(q["total_amount"])) for q in out
            if q["status"] in OPEN_STATUSES and not q["is_expired"]))),
        "note": ("Expiry is worked out when you look, not set by a nightly "
                 "job -- a job that fails would leave dead quotations looking "
                 "live."),
    }
