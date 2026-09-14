"""Batches, quarantine, recall and the honest limits of a trace.

THE REPORT THIS MODULE REFUSES TO WRITE
=======================================
A traceability percentage with nothing behind it. `traceability_report` answers
in two parts -- what is traceable and what is not -- and never folds them into
one reassuring number.

Every unit that moved before migration b7890123456a has no batch. That is not a
gap to be papered over; it is a fact about what the company knows. A recall that
reports "94% traced" reads like success and is actually a statement that 6% of
the goods are somewhere unknown. The figures here are always absolute
quantities, and unbatched stock is named as untraceable rather than omitted.

WHY A BALANCE IS NEVER STORED
=============================
`inventory.batch_balance` derives it from `stock_movements`. A cached batch
balance is a second copy of a number the system already has, and the two drift
apart precisely when it matters. See the migration docstring.

RECALL IS NOT DELETION
======================
Recalling a batch does not remove stock or cancel orders. It blocks despatch,
records why, and produces the list of everyone who already received it. Making
the stock vanish would destroy the evidence of where it went.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.inventory import INBOUND, OUTBOUND, batch_balance

BLOCKED_STATUSES = ("QUARANTINED", "RECALLED", "WITHDRAWN")

_IN = ", ".join(f"'{t}'" for t in sorted(INBOUND))
_OUT = ", ".join(f"'{t}'" for t in sorted(OUTBOUND))

# The signed balance expression, written once. Every query below derives from
# stock_movements the same way, so no two of them can disagree.
_BALANCE = (f"COALESCE(SUM(CASE WHEN sm.movement_type IN ({_IN}) "
            f"THEN sm.quantity ELSE 0 END), 0) "
            f"- COALESCE(SUM(CASE WHEN sm.movement_type IN ({_OUT}) "
            f"THEN sm.quantity ELSE 0 END), 0)")


# ---------------------------------------------------------------------------
# Creating batches
# ---------------------------------------------------------------------------

async def create_batch(
    session: AsyncSession, *, product_id: UUID, batch_number: str,
    expiry_date: Optional[date] = None, manufactured_on: Optional[date] = None,
    origin: str = "PRODUCTION", origin_reference: Optional[str] = None,
    supplier_name: Optional[str] = None, quantity_produced=None,
    notes: Optional[str] = None, actor=None,
) -> dict:
    """Register a batch. Registering it does not put any stock anywhere.

    Creating the batch and receiving the goods are separate acts on purpose: a
    batch that exists with no movements is visibly empty, whereas a batch that
    silently created stock would let someone conjure inventory by filling in a
    form.
    """
    number = (batch_number or "").strip()
    if len(number) < 2:
        raise HTTPException(
            status_code=400,
            detail="A batch number is needed, as printed on the goods.")
    if origin not in ("PRODUCTION", "PURCHASE", "OPENING"):
        raise HTTPException(
            status_code=400,
            detail="Origin must be PRODUCTION, PURCHASE or OPENING.")

    product = (await session.execute(
        text("SELECT id, name FROM products WHERE id = :p"),
        {"p": str(product_id)})).mappings().first()
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found.")

    clash = (await session.execute(
        text("""SELECT id FROM product_batches
                 WHERE product_id = :p AND batch_number = :n"""),
        {"p": str(product_id), "n": number})).first()
    if clash:
        raise HTTPException(
            status_code=409,
            detail=(f"{product['name']} already has a batch {number}. Two "
                    f"batches with one number cannot be told apart in a "
                    f"recall."))

    if expiry_date is not None and expiry_date < date.today():
        raise HTTPException(
            status_code=400,
            detail=(f"That expiry date ({expiry_date}) has already passed. "
                    f"If the goods are genuinely expired, record the batch and "
                    f"quarantine it rather than entering a date you do not "
                    f"mean."))

    batch_id = uuid4()
    await session.execute(
        text("""
            INSERT INTO product_batches
                (id, product_id, batch_number, manufactured_on, expiry_date,
                 origin, origin_reference, supplier_name, quantity_produced,
                 notes, created_by)
            VALUES (:id, :p, :n, :m, :e, :o, :ref, :sup, :qty, :notes, :by)
        """),
        {"id": str(batch_id), "p": str(product_id), "n": number,
         "m": manufactured_on, "e": expiry_date, "o": origin,
         "ref": origin_reference, "sup": supplier_name,
         "qty": str(quantity_produced) if quantity_produced is not None else None,
         "notes": notes, "by": str(actor.id) if actor else None},
    )
    await session.execute(
        text("""INSERT INTO batch_status_events
                    (id, batch_id, from_status, to_status, reason, decided_by,
                     decided_by_label)
                VALUES (gen_random_uuid(), :b, NULL, 'AVAILABLE', :r, :by,
                        :label)"""),
        {"b": str(batch_id),
         "r": f"Batch registered ({origin.lower()})"
              + (f": {origin_reference}" if origin_reference else ""),
         "by": str(actor.id) if actor else None,
         "label": getattr(actor, "full_name", None)},
    )
    return {"id": str(batch_id), "batch_number": number,
            "product": product["name"], "status": "AVAILABLE",
            "expiry_date": str(expiry_date) if expiry_date else None}


# ---------------------------------------------------------------------------
# Quarantine, release, recall
# ---------------------------------------------------------------------------

async def set_status(
    session: AsyncSession, *, batch_id: UUID, status: str, reason: str,
    evidence_document_id: Optional[UUID] = None, actor=None,
) -> dict:
    """Move a batch between AVAILABLE, QUARANTINED, RECALLED and WITHDRAWN.

    Always with a reason, always appended to the history. A recall is judged on
    this record afterwards, so "who decided and why" is not optional metadata.
    """
    if status not in ("AVAILABLE", "QUARANTINED", "RECALLED", "WITHDRAWN",
                      "CONSUMED"):
        raise HTTPException(status_code=400, detail="Unknown batch status.")
    if not reason or len(reason.strip()) < 3:
        raise HTTPException(
            status_code=400,
            detail=("Say why. This is the record the decision is judged on if "
                    "the batch is ever questioned."))

    batch = (await session.execute(
        text("""SELECT id, batch_number, status, product_id
                  FROM product_batches WHERE id = :b FOR UPDATE"""),
        {"b": str(batch_id)})).mappings().first()
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found.")
    if batch["status"] == status:
        return {"id": str(batch_id), "status": status, "changed": False}

    if batch["status"] == "RECALLED" and status == "AVAILABLE":
        raise HTTPException(
            status_code=409,
            detail=(f"Batch {batch['batch_number']} was recalled and cannot be "
                    f"returned to sale. If the recall was raised in error, "
                    f"that is a decision to record against a new batch, not an "
                    f"edit to this one."))

    await session.execute(
        text("""UPDATE product_batches
                   SET status = :s, status_reason = :r, updated_at = NOW()
                 WHERE id = :b"""),
        {"s": status, "r": reason.strip(), "b": str(batch_id)})
    await session.execute(
        text("""INSERT INTO batch_status_events
                    (id, batch_id, from_status, to_status, reason, decided_by,
                     decided_by_label, evidence_document_id)
                VALUES (gen_random_uuid(), :b, :fr, :to, :r, :by, :label,
                        CAST(:ev AS uuid))"""),
        {"b": str(batch_id), "fr": batch["status"], "to": status,
         "r": reason.strip(), "by": str(actor.id) if actor else None,
         "label": getattr(actor, "full_name", None),
         "ev": str(evidence_document_id) if evidence_document_id else None},
    )

    result = {"id": str(batch_id), "batch_number": batch["batch_number"],
              "status": status, "changed": True, "was": batch["status"]}
    if status == "RECALLED":
        # The point of a recall is the list, not the flag.
        result["trace"] = await recall_trace(session, batch_id=batch_id)
    return result


async def status_history(session: AsyncSession, batch_id: UUID) -> list[dict]:
    rows = (await session.execute(
        text("""SELECT e.from_status, e.to_status, e.reason, e.created_at,
                       COALESCE(u.full_name, e.decided_by_label) AS decided_by
                  FROM batch_status_events e
                  LEFT JOIN users u ON u.id = e.decided_by
                 WHERE e.batch_id = :b ORDER BY e.created_at"""),
        {"b": str(batch_id)})).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Where a batch is, and where it went
# ---------------------------------------------------------------------------

async def batch_detail(session: AsyncSession, batch_id: UUID) -> dict:
    batch = (await session.execute(
        text("""SELECT b.*, p.name AS product_name, p.sku,
                       u.full_name AS created_by_name
                  FROM product_batches b
                  JOIN products p ON p.id = b.product_id
                  LEFT JOIN users u ON u.id = b.created_by
                 WHERE b.id = :b"""),
        {"b": str(batch_id)})).mappings().first()
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found.")

    out = dict(batch)
    for key in ("id", "product_id", "created_by"):
        if out.get(key) is not None:
            out[key] = str(out[key])

    out["on_hand"] = str(await batch_balance(session, batch_id=batch_id))
    out["locations"] = await batch_locations(session, batch_id=batch_id)
    out["history"] = await status_history(session, batch_id)
    out["expired"] = bool(batch["expiry_date"]
                          and batch["expiry_date"] < date.today())
    out["dispatchable"] = (batch["status"] == "AVAILABLE"
                           and not out["expired"])
    return out


async def batch_locations(
    session: AsyncSession, *, batch_id: UUID,
) -> list[dict]:
    """Where this batch physically is now, derived from the movements."""
    rows = (await session.execute(
        text(f"""
            SELECT w.id AS warehouse_id, w.code, w.name,
                   w.warehouse_kind, w.distributor_id,
                   d.legal_name AS distributor,
                   {_BALANCE} AS on_hand
              FROM stock_movements sm
              JOIN warehouses w ON w.id = sm.warehouse_id
              LEFT JOIN distributors d ON d.id = w.distributor_id
             WHERE sm.batch_id = :b
             GROUP BY w.id, w.code, w.name, w.warehouse_kind, w.distributor_id,
                      d.legal_name
            HAVING {_BALANCE} <> 0
             ORDER BY w.name
        """), {"b": str(batch_id)})).mappings().all()
    return [dict(r) | {"warehouse_id": str(r["warehouse_id"]),
                       "on_hand": str(r["on_hand"]),
                       "distributor_id": (str(r["distributor_id"])
                                          if r["distributor_id"] else None)}
            for r in rows]


async def recall_trace(session: AsyncSession, *, batch_id: UUID) -> dict:
    """Who has this batch, and who was sent it.

    Two answers, because they are two different questions. Stock still held can
    be stopped; stock already despatched has to be chased, and the second list
    is the one somebody has to act on.
    """
    batch = (await session.execute(
        text("""SELECT b.batch_number, b.status, b.expiry_date, p.name
                  FROM product_batches b JOIN products p ON p.id = b.product_id
                 WHERE b.id = :b"""),
        {"b": str(batch_id)})).mappings().first()
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found.")

    locations = await batch_locations(session, batch_id=batch_id)

    # Despatched to a customer. sales_order_lines.batch_id is the precise
    # record; it exists only for lines picked after this phase shipped.
    shipped = (await session.execute(
        text("""SELECT o.order_number, o.order_date, o.status,
                       c.name AS customer, c.phone, c.customer_code,
                       d.legal_name AS distributor, d.id AS distributor_id,
                       sol.quantity, sol.unit
                  FROM sales_order_lines sol
                  JOIN sales_orders o ON o.id = sol.sales_order_id
                  JOIN customers c ON c.id = o.customer_id
                  LEFT JOIN distributors d ON d.id = o.distributor_id
                 WHERE sol.batch_id = :b
                 ORDER BY o.order_date DESC"""),
        {"b": str(batch_id)})).mappings().all()

    held = sum(Decimal(loc["on_hand"]) for loc in locations)
    out_qty = sum(Decimal(str(r["quantity"])) for r in shipped)

    return {
        "batch_number": batch["batch_number"],
        "product": batch["name"],
        "status": batch["status"],
        "expiry_date": (str(batch["expiry_date"]) if batch["expiry_date"]
                        else None),
        # Can be stopped: it is still on a shelf the company controls or can
        # reach through a distributor it knows.
        "still_held": locations,
        "quantity_still_held": str(held),
        # Must be chased.
        "despatched_to": [
            dict(r) | {"quantity": str(r["quantity"]),
                       "distributor_id": (str(r["distributor_id"])
                                          if r["distributor_id"] else None)}
            for r in shipped
        ],
        "quantity_despatched": str(out_qty),
        "recipients": len({r["customer_code"] for r in shipped}),
    }


# ---------------------------------------------------------------------------
# Choosing a batch to pick
# ---------------------------------------------------------------------------

async def available_batches(
    session: AsyncSession, *, product_id: UUID, warehouse_id: UUID,
) -> list[dict]:
    """What can be picked here, soonest expiry first.

    First-expiry-first-out, not first-in-first-out. FIFO is a proxy for FEFO and
    the two differ exactly when it matters -- a batch received later with a
    shorter life is the one that should go first, and picking by arrival leaves
    it on the shelf to expire.

    Batches with no expiry date sort last rather than first: an unknown date is
    not a distant one, and pushing them to the back means a dated batch is
    always preferred while the unknown ones stay visible.
    """
    rows = (await session.execute(
        text(f"""
            SELECT b.id, b.batch_number, b.expiry_date, b.manufactured_on,
                   b.status, {_BALANCE} AS on_hand
              FROM product_batches b
              JOIN stock_movements sm ON sm.batch_id = b.id
                   AND sm.warehouse_id = :w
             WHERE b.product_id = :p
               AND b.status = 'AVAILABLE'
               AND (b.expiry_date IS NULL OR b.expiry_date >= CURRENT_DATE)
             GROUP BY b.id, b.batch_number, b.expiry_date, b.manufactured_on,
                      b.status
            HAVING {_BALANCE} > 0
             ORDER BY b.expiry_date ASC NULLS LAST, b.manufactured_on ASC
        """), {"p": str(product_id), "w": str(warehouse_id)})).mappings().all()

    today = date.today()
    return [
        dict(r) | {
            "id": str(r["id"]),
            "on_hand": str(r["on_hand"]),
            "days_to_expiry": ((r["expiry_date"] - today).days
                               if r["expiry_date"] else None),
        }
        for r in rows
    ]


async def expiring_batches(
    session: AsyncSession, *, within_days: int = 90,
) -> dict:
    """What is about to expire, and what already has.

    Reported separately: one is a commercial problem and the other is stock
    that must not be despatched at all.
    """
    rows = (await session.execute(
        text(f"""
            SELECT b.id, b.batch_number, b.expiry_date, b.status,
                   p.name AS product, p.sku,
                   (b.expiry_date - CURRENT_DATE) AS days_left,
                   {_BALANCE} AS on_hand
              FROM product_batches b
              JOIN products p ON p.id = b.product_id
              LEFT JOIN stock_movements sm ON sm.batch_id = b.id
             WHERE b.expiry_date IS NOT NULL
               AND b.expiry_date <= CURRENT_DATE + CAST(:d AS integer)
               AND b.status <> 'CONSUMED'
             GROUP BY b.id, b.batch_number, b.expiry_date, b.status, p.name,
                      p.sku
            HAVING {_BALANCE} > 0
             ORDER BY b.expiry_date
        """), {"d": within_days})).mappings().all()

    expired, soon = [], []
    today = date.today()
    for r in rows:
        item = dict(r) | {"id": str(r["id"]), "on_hand": str(r["on_hand"])}
        (expired if r["expiry_date"] < today else soon).append(item)
    return {"expired": expired, "expiring_soon": soon,
            "within_days": within_days}


async def list_batches(
    session: AsyncSession, *, product_id: Optional[UUID] = None,
    status: Optional[str] = None, include_empty: bool = False,
) -> list[dict]:
    clauses, params = ["1 = 1"], {}
    if product_id:
        clauses.append("b.product_id = :p")
        params["p"] = str(product_id)
    if status:
        clauses.append("b.status = :s")
        params["s"] = status
    having = "" if include_empty else f"HAVING {_BALANCE} > 0"

    rows = (await session.execute(
        text(f"""
            SELECT b.id, b.batch_number, b.status, b.expiry_date,
                   b.manufactured_on, b.origin, b.supplier_name,
                   p.name AS product, p.sku, p.id AS product_id,
                   {_BALANCE} AS on_hand
              FROM product_batches b
              JOIN products p ON p.id = b.product_id
              LEFT JOIN stock_movements sm ON sm.batch_id = b.id
             WHERE {' AND '.join(clauses)}
             GROUP BY b.id, b.batch_number, b.status, b.expiry_date,
                      b.manufactured_on, b.origin, b.supplier_name, p.name,
                      p.sku, p.id
             {having}
             ORDER BY b.expiry_date ASC NULLS LAST, p.name
        """), params)).mappings().all()
    return [dict(r) | {"id": str(r["id"]),
                       "product_id": str(r["product_id"]),
                       "on_hand": str(r["on_hand"])} for r in rows]


# ---------------------------------------------------------------------------
# The honest report
# ---------------------------------------------------------------------------

async def traceability_report(
    session: AsyncSession, *, product_id: Optional[UUID] = None,
) -> dict:
    """How much stock can actually be traced, in absolute quantities.

    NO PERCENTAGE IS RETURNED AS THE HEADLINE. "94% traced" reads like a pass
    mark; what it means is that some quantity of medical goods is somewhere
    nobody can name. The caller gets both quantities and has to show them.

    Untraceable stock is not a defect to be fixed by backfilling. It is stock
    that moved before batches were recorded, and the only honest remedies are a
    physical count that assigns real batch numbers, or waiting for it to sell
    through.
    """
    clause = "WHERE sm.product_id IS NOT NULL"
    params: dict = {}
    if product_id:
        clause += " AND sm.product_id = :p"
        params["p"] = str(product_id)

    row = (await session.execute(
        text(f"""
            SELECT
                COALESCE(SUM(CASE WHEN sm.batch_id IS NOT NULL
                    THEN CASE WHEN sm.movement_type IN ({_IN}) THEN sm.quantity
                              WHEN sm.movement_type IN ({_OUT}) THEN -sm.quantity
                              ELSE 0 END
                    ELSE 0 END), 0) AS batched,
                COALESCE(SUM(CASE WHEN sm.batch_id IS NULL
                    THEN CASE WHEN sm.movement_type IN ({_IN}) THEN sm.quantity
                              WHEN sm.movement_type IN ({_OUT}) THEN -sm.quantity
                              ELSE 0 END
                    ELSE 0 END), 0) AS unbatched
              FROM stock_movements sm
              {clause}
        """), params)).mappings().first()

    batched = Decimal(str(row["batched"] or 0))
    unbatched = Decimal(str(row["unbatched"] or 0))

    # The balance the rest of the app shows, for comparison. If these disagree
    # something is wrong with the movement history and the report says so
    # instead of quietly reporting the prettier number.
    level_clause = "WHERE product_id IS NOT NULL"
    if product_id:
        level_clause += " AND product_id = :p"
    declared = Decimal(str((await session.execute(
        text(f"SELECT COALESCE(SUM(current_stock), 0) FROM stock_levels "
             f"{level_clause}"), params)).scalar() or 0))

    derived = batched + unbatched
    return {
        "traceable_quantity": str(batched),
        "untraceable_quantity": str(unbatched),
        "stock_levels_total": str(declared),
        "movements_total": str(derived),
        "reconciles": derived == declared,
        "discrepancy": str(declared - derived),
        "note": (
            "Untraceable stock moved before batch recording began. It cannot "
            "be traced retrospectively and nothing has invented a batch number "
            "for it. It clears as that stock sells through, or sooner if a "
            "physical count assigns real batch numbers to what is on the shelf."
            if unbatched > 0 else
            "Every unit currently on hand is attributed to a batch."),
        "reconciliation_note": (
            None if derived == declared else
            "The movement history and the stock balances disagree. Investigate "
            "before relying on either for a recall: one of them is wrong, and "
            "a trace built on the wrong one will miss goods."),
    }
