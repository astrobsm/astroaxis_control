"""Holding stock for an order, releasing it, and consuming it.

THE ONE RULE THIS MODULE EXISTS TO ENFORCE
==========================================
    available = current_stock - reserved_stock

`available` is the only figure a salesperson, a quotation or an order should
ever see. `current_stock` is what is physically in the warehouse, and showing
it to somebody deciding whether they can promise goods is how a customer is
sold something that is already spoken for.

`available_stock` below is the single implementation. Every caller uses it;
nothing recomputes the subtraction locally.

RESERVING DOES NOT MOVE STOCK
=============================
A reservation changes `reserved_stock` and nothing else. The goods are still
there and still on the balance sheet. Consuming a reservation is what moves
stock, and that goes through `apply_stock_movement` so the invariants in
`inventory.py` -- a movement row for every balance change, a row lock, a
positive magnitude, a negative-stock guard -- all still hold.

WHY THE AGGREGATE IS MAINTAINED RATHER THAN SUMMED
==================================================
`stock_levels.reserved_stock` is a running total, which is a deliberate
exception to the rule that nothing is stored twice. It is the same exception
`current_stock` already is, for the same reason: summing live reservations on
every stock display would put a correlated subquery into a dozen hot queries.

The safety is structural rather than careful: the aggregate is only ever
changed in the same statement as the reservation it reflects, under the row
lock `inventory._lock_or_create_level` takes. `reconcile` proves the two
agree and a test runs it. That is inventory's trial balance.

A RESERVATION THAT CANNOT EXPIRE IS A LEAK
==========================================
Every hold has an expiry. An abandoned basket or an order left pending while
somebody is on leave would otherwise take stock out of circulation forever,
and the warehouse would read as empty while being full -- a failure that is
harder to diagnose than a plain stock error, because every individual number
looks right.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.inventory import _as_decimal, _lock_or_create_level
from app.services.messaging import get_setting

REFERENCE_TYPES = ("SALES_ORDER", "QUOTATION", "PRODUCTION_ORDER",
                   "TRANSFER", "MANUAL")


def _tidy(value: Decimal) -> str:
    """A quantity as a person would write it.

    stock_levels is NUMERIC(18,6), so a plain str() tells a salesperson
    "Only 10.000000 available", which reads as a system talking to itself.
    """
    d = value.normalize()
    # normalize() turns 100 into 1E+2; quantize it back to a plain integer.
    return f"{d:f}" if d == d.to_integral_value() else f"{d.normalize():f}"


def _item_columns(product_id, raw_material_id):
    if (product_id is None) == (raw_material_id is None):
        raise HTTPException(
            status_code=400,
            detail="A reservation is for a product or a raw material, not both.")
    if product_id is not None:
        return "product_id", product_id
    return "raw_material_id", raw_material_id


async def available_stock(
    session: AsyncSession, *, warehouse_id: UUID,
    product_id: Optional[UUID] = None,
    raw_material_id: Optional[UUID] = None,
) -> dict:
    """What is there, what is promised, and what may still be sold.

    The one implementation of the subtraction. Returning all three rather than
    only the difference is deliberate: a screen that says "none available"
    when there are forty on the shelf has to be able to explain itself.
    """
    col, item_id = _item_columns(product_id, raw_material_id)
    row = (await session.execute(
        text(f"""SELECT current_stock, COALESCE(reserved_stock, 0) AS reserved
                   FROM stock_levels
                  WHERE warehouse_id = :w AND {col} = :i"""),
        {"w": str(warehouse_id), "i": str(item_id)})).mappings().first()

    on_hand = _as_decimal(row["current_stock"]) if row else Decimal("0")
    reserved = _as_decimal(row["reserved"]) if row else Decimal("0")
    return {
        "on_hand": on_hand,
        "reserved": reserved,
        "available": on_hand - reserved,
    }


async def reserve(
    session: AsyncSession, *, warehouse_id: UUID, quantity,
    reference_type: str, product_id: Optional[UUID] = None,
    raw_material_id: Optional[UUID] = None,
    reference_id: Optional[UUID] = None,
    reference_label: Optional[str] = None,
    hold_hours: Optional[int] = None, actor=None,
) -> dict:
    """Hold stock, refusing rather than overselling.

    The check and the write happen under the same row lock, so two orders for
    the last unit cannot both succeed. That is the entire point of taking the
    lock before reading.
    """
    reference_type = (reference_type or "").upper()
    if reference_type not in REFERENCE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Reference type must be one of {', '.join(REFERENCE_TYPES)}.")

    col, item_id = _item_columns(product_id, raw_material_id)
    qty = _as_decimal(quantity)
    if qty <= 0:
        raise HTTPException(
            status_code=400, detail="Reserve a quantity greater than zero.")

    # Locks the balance row, creating it if absent. Everything below runs
    # while this transaction holds it.
    await _lock_or_create_level(session, warehouse_id, product_id,
                                raw_material_id)

    row = (await session.execute(
        text(f"""SELECT current_stock, COALESCE(reserved_stock, 0) AS reserved
                   FROM stock_levels
                  WHERE warehouse_id = :w AND {col} = :i"""),
        {"w": str(warehouse_id), "i": str(item_id)})).mappings().first()
    on_hand = _as_decimal(row["current_stock"])
    reserved = _as_decimal(row["reserved"])
    free = on_hand - reserved

    if qty > free:
        raise HTTPException(
            status_code=409,
            detail=(f"Only {_tidy(free)} available to reserve: "
                    f"{_tidy(on_hand)} in stock, {_tidy(reserved)} already "
                    f"held for other orders. Nothing was reserved."))

    hours = hold_hours or await get_setting(session, "RESERVATION_HOLD_HOURS", 72)
    expires = datetime.now(timezone.utc) + timedelta(hours=int(hours))

    result = (await session.execute(
        text(f"""INSERT INTO inventory_reservations
                    (warehouse_id, {col}, quantity, reference_type,
                     reference_id, reference_label, expires_at, created_by,
                     created_by_name)
                 VALUES (:w, :i, :q, :rt, :ri, :rl, :e, :by, :byn)
                 RETURNING id"""),
        {"w": str(warehouse_id), "i": str(item_id), "q": str(qty),
         "rt": reference_type,
         "ri": str(reference_id) if reference_id else None,
         "rl": reference_label, "e": expires,
         "by": str(actor.id) if actor is not None else None,
         "byn": ((getattr(actor, "full_name", None)
                  or getattr(actor, "username", None)) if actor else None)}
    )).mappings().first()

    # Written in the same transaction, under the same lock, as the row above.
    # That is what stops the aggregate drifting from the reservations.
    await session.execute(
        text(f"""UPDATE stock_levels
                    SET reserved_stock = COALESCE(reserved_stock, 0) + :q,
                        updated_at = NOW()
                  WHERE warehouse_id = :w AND {col} = :i"""),
        {"q": str(qty), "w": str(warehouse_id), "i": str(item_id)})

    return {
        "reservation_id": str(result["id"]),
        "quantity": float(qty),
        "expires_at": expires.isoformat(),
        "available_after": float(free - qty),
        "note": (f"Held until {expires:%d %b %Y %H:%M}. The stock has not "
                 f"moved and is still on the shelf; it is simply no longer "
                 f"available to promise to anybody else."),
    }


async def release(
    session: AsyncSession, *, reservation_id: UUID, reason: str, actor=None,
) -> dict:
    """Give held stock back, because the order went away or the hold lapsed."""
    if len((reason or "").strip()) < 3:
        raise HTTPException(
            status_code=400,
            detail="Say why the stock is being released.")

    r = (await session.execute(
        text("""SELECT id, warehouse_id, product_id, raw_material_id,
                       quantity, status
                  FROM inventory_reservations WHERE id = :i FOR UPDATE"""),
        {"i": str(reservation_id)})).mappings().first()
    if r is None:
        raise HTTPException(status_code=404, detail="Reservation not found.")
    if r["status"] != "HELD":
        raise HTTPException(
            status_code=400,
            detail=(f"That reservation is already {r['status'].lower()}; "
                    f"nothing was changed."))

    col = "product_id" if r["product_id"] else "raw_material_id"
    item_id = r["product_id"] or r["raw_material_id"]

    await _lock_or_create_level(
        session, r["warehouse_id"], r["product_id"], r["raw_material_id"])

    await session.execute(
        text("""UPDATE inventory_reservations
                   SET status = 'RELEASED', released_at = NOW(),
                       release_reason = :why
                 WHERE id = :i"""),
        {"why": reason.strip(), "i": str(reservation_id)})

    # GREATEST guards the aggregate against going negative if anything ever
    # did drift: a wrong number is bad, a negative reserved_stock would make
    # available exceed what is physically present.
    await session.execute(
        text(f"""UPDATE stock_levels
                    SET reserved_stock = GREATEST(
                            COALESCE(reserved_stock, 0) - :q, 0),
                        updated_at = NOW()
                  WHERE warehouse_id = :w AND {col} = :i"""),
        {"q": str(r["quantity"]), "w": str(r["warehouse_id"]),
         "i": str(item_id)})

    return {"reservation_id": str(reservation_id), "released": float(r["quantity"]),
            "note": "The stock is available again."}


async def consume(
    session: AsyncSession, *, reservation_id: UUID, actor=None,
) -> dict:
    """Ship what was held: the hold ends and the stock actually leaves.

    Both halves happen together. Releasing the hold without moving the stock
    would make the goods available again after they had gone out of the door.
    """
    from app.services.inventory import apply_stock_movement

    r = (await session.execute(
        text("""SELECT id, warehouse_id, product_id, raw_material_id,
                       quantity, status, reference_type, reference_id,
                       reference_label
                  FROM inventory_reservations WHERE id = :i FOR UPDATE"""),
        {"i": str(reservation_id)})).mappings().first()
    if r is None:
        raise HTTPException(status_code=404, detail="Reservation not found.")
    if r["status"] != "HELD":
        raise HTTPException(
            status_code=400,
            detail=f"That reservation is already {r['status'].lower()}.")

    col = "product_id" if r["product_id"] else "raw_material_id"
    item_id = r["product_id"] or r["raw_material_id"]

    # The hold comes off first so that the movement's own negative-stock
    # guard sees the true picture rather than counting this reservation
    # against the goods it is about to release.
    await session.execute(
        text(f"""UPDATE stock_levels
                    SET reserved_stock = GREATEST(
                            COALESCE(reserved_stock, 0) - :q, 0),
                        updated_at = NOW()
                  WHERE warehouse_id = :w AND {col} = :i"""),
        {"q": str(r["quantity"]), "w": str(r["warehouse_id"]),
         "i": str(item_id)})

    await apply_stock_movement(
        session,
        warehouse_id=r["warehouse_id"],
        product_id=r["product_id"],
        raw_material_id=r["raw_material_id"],
        quantity=r["quantity"],
        movement_type="OUT",
        reference=r["reference_label"] or str(r["reference_id"] or ""),
        notes=f"Reservation {reservation_id} consumed",
    )

    await session.execute(
        text("""UPDATE inventory_reservations
                   SET status = 'CONSUMED', consumed_at = NOW()
                 WHERE id = :i"""),
        {"i": str(reservation_id)})

    return {"reservation_id": str(reservation_id),
            "quantity": float(r["quantity"]),
            "note": "Stock has left the warehouse and the hold is cleared."}


async def release_for_reference(
    session: AsyncSession, *, reference_type: str, reference_id: UUID,
    reason: str, actor=None,
) -> dict:
    """Release every hold belonging to one order.

    Cancelling an order has to free all of its lines, not the one somebody
    happened to click.
    """
    rows = (await session.execute(
        text("""SELECT id FROM inventory_reservations
                 WHERE reference_type = :t AND reference_id = :i
                   AND status = 'HELD'"""),
        {"t": reference_type.upper(), "i": str(reference_id)})).fetchall()

    for row in rows:
        await release(session, reservation_id=row.id, reason=reason,
                      actor=actor)
    return {"released": len(rows),
            "note": (f"{len(rows)} hold(s) released for that "
                     f"{reference_type.lower().replace('_', ' ')}.")}


async def release_expired(session: AsyncSession) -> dict:
    """Give back stock whose hold has lapsed.

    Run on a schedule. Without this, an abandoned basket holds stock forever
    and the warehouse reads as empty while being full.
    """
    rows = (await session.execute(
        text("""SELECT id FROM inventory_reservations
                 WHERE status = 'HELD' AND expires_at <= NOW()"""))).fetchall()
    for row in rows:
        await release(session, reservation_id=row.id,
                      reason="Hold expired without the order being completed")
    return {"released": len(rows)}


async def reconcile(session: AsyncSession) -> dict:
    """Prove reserved_stock agrees with the reservations behind it.

    Inventory's trial balance. The aggregate is a stored total, so something
    has to check it rather than everybody assuming it is right -- and a
    disagreement here means somebody is being promised stock that is held, or
    stock is being held back that nobody wants.
    """
    rows = (await session.execute(text("""
        SELECT sl.warehouse_id, sl.product_id, sl.raw_material_id,
               COALESCE(sl.reserved_stock, 0) AS aggregate,
               COALESCE(r.held, 0)            AS live_holds,
               w.name AS warehouse,
               COALESCE(p.name, rm.name) AS item
          FROM stock_levels sl
          JOIN warehouses w ON w.id = sl.warehouse_id
     LEFT JOIN products p  ON p.id = sl.product_id
     LEFT JOIN raw_materials rm ON rm.id = sl.raw_material_id
     LEFT JOIN (
            SELECT warehouse_id, product_id, raw_material_id,
                   SUM(quantity) AS held
              FROM inventory_reservations WHERE status = 'HELD'
             GROUP BY warehouse_id, product_id, raw_material_id
          ) r
            ON r.warehouse_id = sl.warehouse_id
           AND r.product_id IS NOT DISTINCT FROM sl.product_id
           AND r.raw_material_id IS NOT DISTINCT FROM sl.raw_material_id
         WHERE COALESCE(sl.reserved_stock, 0) <> COALESCE(r.held, 0)
    """))).mappings().all()

    return {
        "balanced": len(rows) == 0,
        "discrepancies": [{
            "warehouse": r["warehouse"], "item": r["item"],
            "reserved_stock": float(r["aggregate"]),
            "live_holds": float(r["live_holds"]),
            "difference": float(_as_decimal(r["aggregate"])
                                - _as_decimal(r["live_holds"])),
        } for r in rows],
        "note": ("reserved_stock is a running total of live holds. These must "
                 "agree: a difference means stock is being promised twice, or "
                 "held back from everybody."),
    }


async def expiry_scheduler() -> None:
    """Release lapsed holds, hourly, forever.

    Hourly rather than daily because a reservation is measured in hours: a
    72-hour hold released up to 24 hours late is a day of stock nobody could
    sell. Failures are logged and the loop continues -- a database hiccup
    should cost one pass, not every pass.
    """
    import asyncio
    import logging

    log = logging.getLogger("reservations.expiry")
    from app.db import AsyncSessionLocal

    while True:
        try:
            await asyncio.sleep(60 * 60)
            async with AsyncSessionLocal() as session:
                result = await release_expired(session)
                await session.commit()
            if result["released"]:
                log.info("Released %s expired stock reservation(s)",
                         result["released"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the loop must survive
            log.warning("Reservation expiry pass failed: %s", exc)
