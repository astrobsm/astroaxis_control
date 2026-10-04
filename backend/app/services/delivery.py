"""Dispatching a van, recording what happened at each drop, and failures.

TWO LEVELS, AND THEY ARE NOT THE SAME THING
===========================================
A manifest is one vehicle run. A drop is one customer on it. A run can be
finished while one of its drops failed, and that is the normal case this
module exists to express -- the old schema could not, because a drop was
either `pending` or `delivered` and nothing else.

    manifest   preparing -> dispatched -> in_transit -> completed
    drop       pending -> out_for_delivery -> delivered | failed -> returned

WHY A FAILED DELIVERY MATTERS MORE THAN A SUCCESSFUL ONE
========================================================
When the van comes back with goods the customer refused or could not pay for,
the stock is physically on the shelf again -- but it was deducted when the
invoice was raised, so the system believes it is gone. Nobody notices until
the next stock count, where it appears as an unexplained surplus months later
and teaches nobody anything.

`return_to_stock` is the half of this module that actually matters. It puts
the goods back, once, through `apply_stock_movement` so there is a movement
row behind the balance change like every other.

NO STOCK MOVES ON DISPATCH
==========================
Stock leaves when the invoice is raised, in `payment_tracking.py`. A manifest
moves goods the system has already written off, so deducting again would halve
the warehouse. The only movement here is the return.

NOTHING IS SENT TO ANY CUSTOMER
===============================
Status changes queue a message into the outbox when the delivery
notification setting is on. Nothing drains that outbox, so this is the
pipeline being built rather than messages going out -- and because the
messages are real rows with real reasons, what WOULD be sent can be read on a
real delivery before any provider exists.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import messaging as msg

# What a run may become, from where. A manifest cannot go from preparing
# straight to completed: that is a run that never left the yard and arrived
# anyway.
MANIFEST_TRANSITIONS = {
    "preparing": {"dispatched", "cancelled"},
    "dispatched": {"in_transit", "completed", "cancelled"},
    "in_transit": {"completed", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}

DROP_TRANSITIONS = {
    "pending": {"out_for_delivery", "delivered", "failed", "cancelled"},
    "out_for_delivery": {"delivered", "failed"},
    # A failed drop is not final: the goods come back, and that is recorded
    # as its own state so "failed but still on the van" is distinguishable
    # from "failed and back on the shelf".
    "failed": {"returned", "out_for_delivery"},
    "delivered": set(),
    "returned": set(),
    "cancelled": set(),
}

# Which drop statuses are worth telling a customer about, and what to say.
# Absent from this map means no message -- an internal state change is not
# news to the person waiting at the other end.
CUSTOMER_MESSAGES = {
    "out_for_delivery": ("Good day {name}. Your order from Bonnesante "
                         "Medicals is out for delivery today."),
    "delivered": ("Good day {name}. Your order from Bonnesante Medicals has "
                  "been delivered. Thank you for your business."),
    "failed": ("Good day {name}. We attempted to deliver your order today but "
               "were unable to complete it. Our team will contact you to "
               "arrange another time."),
}


async def _event(
    session: AsyncSession, *, manifest_id=None, drop_id=None,
    from_status: Optional[str], to_status: str, note: Optional[str] = None,
    actor=None,
) -> None:
    await session.execute(
        text("""INSERT INTO delivery_events
                    (manifest_id, manifest_customer_id, from_status,
                     to_status, note, actor_id, actor_name)
                VALUES (:m, :d, :f, :t, :n, :a, :an)"""),
        {"m": str(manifest_id) if manifest_id else None,
         "d": str(drop_id) if drop_id else None,
         "f": from_status, "t": to_status, "n": note,
         "a": str(actor.id) if actor is not None else None,
         "an": ((getattr(actor, "full_name", None)
                 or getattr(actor, "username", None)) if actor else None)})


async def _notify(
    session: AsyncSession, *, drop: dict, status: str, actor=None,
) -> Optional[dict]:
    """Queue a message to the customer, if that is switched on.

    Returns None when nothing was queued, which is the usual case today.
    """
    template = CUSTOMER_MESSAGES.get(status)
    if template is None:
        return None
    if not await msg.get_setting(session, "DELIVERY_NOTIFY_CUSTOMER", False):
        return None
    to = drop.get("customer_phone")
    if not to:
        return None

    name = (drop.get("customer_name") or "").split(" ")[0] or "there"
    return await msg.enqueue(
        session, channel="WHATSAPP", to_address=to,
        body=template.format(name=name),
        reason=f"Delivery {status.replace('_', ' ')} for "
               f"{drop.get('customer_name')}",
        category="TRANSACTIONAL",
        customer_id=drop.get("customer_id"),
        idempotency_key=f"delivery:{drop['id']}:{status}",
        actor=actor)


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

async def set_manifest_status(
    session: AsyncSession, *, manifest_id: UUID, status: str,
    note: Optional[str] = None, actor=None,
) -> dict:
    """Move a run along, if it may go there.

    Replaces an endpoint that wrote whatever string it was handed, so a typo
    set a manifest to a status nothing else recognised and no screen would
    ever show it again.
    """
    status = (status or "").strip().lower()
    m = (await session.execute(
        text("""SELECT id, manifest_number, status FROM delivery_manifests
                 WHERE id = :i FOR UPDATE"""),
        {"i": str(manifest_id)})).mappings().first()
    if m is None:
        raise HTTPException(status_code=404, detail="Manifest not found.")

    allowed = MANIFEST_TRANSITIONS.get(m["status"], set())
    if status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=(f"{m['manifest_number']} is {m['status']}; it cannot "
                    f"become {status}."
                    + (f" It may become: {', '.join(sorted(allowed))}."
                       if allowed else " It is in a final state.")))

    if status == "cancelled" and len((note or "").strip()) < 3:
        raise HTTPException(
            status_code=400, detail="Say why the run is being cancelled.")

    # Completing a run with drops still open would record a delivery that
    # nobody made. The driver has to say what happened to each one.
    if status == "completed":
        open_drops = (await session.execute(
            text("""SELECT COUNT(*) FROM manifest_customers
                     WHERE manifest_id = :m
                       AND status IN ('pending','out_for_delivery')"""),
            {"m": str(manifest_id)})).scalar()
        if open_drops:
            raise HTTPException(
                status_code=400,
                detail=(f"{open_drops} drop(s) on {m['manifest_number']} have "
                        f"no outcome recorded. Mark each one delivered or "
                        f"failed before closing the run."))

    stamps = {"dispatched": "dispatched_at = NOW()",
              "completed": "completed_at = NOW()",
              "cancelled": "cancelled_at = NOW(), cancel_reason = :n"}
    extra = f", {stamps[status]}" if status in stamps else ""

    await session.execute(
        text(f"""UPDATE delivery_manifests
                    SET status = :s, updated_at = NOW(){extra}
                  WHERE id = :i"""),
        {"s": status, "i": str(manifest_id), "n": note})

    await _event(session, manifest_id=manifest_id, from_status=m["status"],
                 to_status=status, note=note, actor=actor)

    # Dispatching the run puts every drop on it out for delivery, which is
    # what the customer cares about and what each drop's own message hangs on.
    queued = 0
    if status == "dispatched":
        drops = (await session.execute(
            text("""SELECT id, customer_id, customer_name, customer_phone,
                           status
                      FROM manifest_customers
                     WHERE manifest_id = :m AND status = 'pending'"""),
            {"m": str(manifest_id)})).mappings().all()
        for d in drops:
            await session.execute(
                text("""UPDATE manifest_customers
                           SET status = 'out_for_delivery'
                         WHERE id = :i"""), {"i": str(d["id"])})
            await _event(session, drop_id=d["id"], manifest_id=manifest_id,
                         from_status=d["status"], to_status="out_for_delivery",
                         note="Run dispatched", actor=actor)
            if await _notify(session, drop=dict(d),
                             status="out_for_delivery", actor=actor):
                queued += 1

    return {
        "manifest_number": m["manifest_number"], "status": status,
        "messages_queued": queued,
        "note": ("Nothing has been sent: the outbox has no sender yet."
                 if queued else None),
    }


# ---------------------------------------------------------------------------
# Each drop
# ---------------------------------------------------------------------------

async def set_drop_status(
    session: AsyncSession, *, drop_id: UUID, status: str,
    reason: Optional[str] = None, receiver_name: Optional[str] = None,
    actor=None,
) -> dict:
    """Record what happened at one customer."""
    status = (status or "").strip().lower()
    d = (await session.execute(
        text("""SELECT id, manifest_id, customer_id, customer_name,
                       customer_phone, status, attempt_count
                  FROM manifest_customers WHERE id = :i FOR UPDATE"""),
        {"i": str(drop_id)})).mappings().first()
    if d is None:
        raise HTTPException(status_code=404, detail="Delivery drop not found.")

    allowed = DROP_TRANSITIONS.get(d["status"], set())
    if status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=(f"That drop is {d['status']}; it cannot become {status}."
                    + (f" It may become: {', '.join(sorted(allowed))}."
                       if allowed else " It is in a final state.")))

    if status == "failed" and len((reason or "").strip()) < 3:
        raise HTTPException(
            status_code=400,
            detail=("Record why the delivery failed. A failure with no reason "
                    "is the commonest one to leave blank and the only kind "
                    "nobody can act on."))

    sets = ["status = :s"]
    params = {"s": status, "i": str(drop_id), "r": (reason or "").strip() or None,
              "rn": receiver_name}
    if status == "delivered":
        sets += ["delivered_at = NOW()", "attempt_count = attempt_count + 1"]
        if receiver_name:
            sets.append("receiver_name = :rn")
    elif status == "failed":
        sets += ["failed_at = NOW()", "failure_reason = :r",
                 "attempt_count = attempt_count + 1"]

    await session.execute(
        text(f"UPDATE manifest_customers SET {', '.join(sets)} WHERE id = :i"),
        params)
    await _event(session, drop_id=drop_id, manifest_id=d["manifest_id"],
                 from_status=d["status"], to_status=status, note=reason,
                 actor=actor)

    queued = await _notify(session, drop=dict(d), status=status, actor=actor)

    return {
        "drop_id": str(drop_id), "customer": d["customer_name"],
        "status": status,
        "message_queued": bool(queued),
        "note": ("The goods are still recorded as sold. Put them back with "
                 "'return to stock' when the van is unloaded."
                 if status == "failed" else None),
    }


async def return_to_stock(
    session: AsyncSession, *, drop_id: UUID, warehouse_id: UUID, actor=None,
) -> dict:
    """Put a failed delivery's goods back on the shelf.

    The half of this module that matters. Stock was deducted when the invoice
    was raised, so after a failed delivery the goods are physically present
    and absent from the system. Left alone it surfaces at the next count as an
    unexplained surplus, months later, indistinguishable from a counting
    error.

    Runs once per drop: `stock_returned_at` is the guard, so a second attempt
    cannot double the stock.
    """
    from app.services.inventory import apply_stock_movement

    d = (await session.execute(
        text("""SELECT id, manifest_id, customer_name, status,
                       stock_returned_at
                  FROM manifest_customers WHERE id = :i FOR UPDATE"""),
        {"i": str(drop_id)})).mappings().first()
    if d is None:
        raise HTTPException(status_code=404, detail="Delivery drop not found.")
    if d["status"] != "failed":
        raise HTTPException(
            status_code=400,
            detail=(f"Only a failed delivery has goods to return; that drop "
                    f"is {d['status']}."))
    if d["stock_returned_at"] is not None:
        raise HTTPException(
            status_code=400,
            detail=("Those goods have already been returned to stock. "
                    "Returning them twice would invent inventory."))

    items = (await session.execute(
        text("""SELECT product_id, product_name, quantity
                  FROM manifest_items WHERE manifest_customer_id = :d"""),
        {"d": str(drop_id)})).mappings().all()
    if not items:
        raise HTTPException(
            status_code=400,
            detail="That drop has no items recorded, so there is nothing to "
                   "put back.")

    returned = []
    for item in items:
        if item["product_id"] is None:
            continue
        await apply_stock_movement(
            session, warehouse_id=warehouse_id,
            product_id=item["product_id"],
            quantity=Decimal(str(item["quantity"])),
            movement_type="RETURN",
            reference=f"Failed delivery {drop_id}",
            notes=(f"Returned from failed delivery to "
                   f"{d['customer_name']}"))
        returned.append({"product": item["product_name"],
                         "quantity": float(item["quantity"])})

    await session.execute(
        text("""UPDATE manifest_customers
                   SET status = 'returned', stock_returned_at = NOW()
                 WHERE id = :i"""), {"i": str(drop_id)})
    await _event(session, drop_id=drop_id, manifest_id=d["manifest_id"],
                 from_status="failed", to_status="returned",
                 note=f"{len(returned)} line(s) returned to stock",
                 actor=actor)

    return {
        "drop_id": str(drop_id), "customer": d["customer_name"],
        "returned": returned,
        "note": ("Back on the shelf and back on the system. The invoice is "
                 "unchanged -- a credit note is a separate decision for "
                 "whoever handles the account."),
    }


async def awaiting_return(session: AsyncSession) -> dict:
    """Failed deliveries whose goods are not yet back on the system.

    The question asked after every failed run, and the one nobody can answer
    without this: whose goods are in the building and missing from stock.
    """
    rows = (await session.execute(text("""
        SELECT mc.id, mc.customer_name, mc.failure_reason, mc.failed_at,
               dm.manifest_number, dm.delivery_date,
               COUNT(mi.id) AS lines
          FROM manifest_customers mc
          JOIN delivery_manifests dm ON dm.id = mc.manifest_id
     LEFT JOIN manifest_items mi ON mi.manifest_customer_id = mc.id
         WHERE mc.status = 'failed' AND mc.stock_returned_at IS NULL
         GROUP BY mc.id, mc.customer_name, mc.failure_reason, mc.failed_at,
                  dm.manifest_number, dm.delivery_date
         ORDER BY mc.failed_at
    """))).mappings().all()

    return {
        "drops": [{
            "drop_id": str(r["id"]), "customer": r["customer_name"],
            "reason": r["failure_reason"],
            "failed_at": r["failed_at"].isoformat() if r["failed_at"] else None,
            "manifest_number": r["manifest_number"],
            "delivery_date": str(r["delivery_date"]) if r["delivery_date"] else None,
            "lines": int(r["lines"]),
        } for r in rows],
        "count": len(rows),
        "note": ("Stock was deducted when the invoice was raised, so these "
                 "goods are on the shelf and missing from the system until "
                 "they are returned. Left alone they appear at the next count "
                 "as an unexplained surplus."),
    }


async def unclosed_runs(session: AsyncSession, *, older_than_days: int = 2) -> dict:
    """Runs that went out and were never closed.

    In the live database today there are 16 drops still `pending` on manifests
    dispatched in March and April -- vans that left and whose outcome nobody
    recorded. The transition rules added with this module stop new ones
    accumulating, because a run can no longer be completed while a drop has no
    outcome. They do nothing about the ones already there.

    Each of these is a customer who may or may not have received their goods,
    and nobody can say which. That is worth a screen rather than a comment.
    """
    rows = (await session.execute(
        text("""
            SELECT dm.id, dm.manifest_number, dm.delivery_date, dm.status,
                   dm.driver_name,
                   (CURRENT_DATE - dm.delivery_date) AS days_old,
                   COUNT(mc.id) FILTER (
                       WHERE mc.status IN ('pending','out_for_delivery')
                   ) AS open_drops,
                   COUNT(mc.id) AS total_drops
              FROM delivery_manifests dm
              JOIN manifest_customers mc ON mc.manifest_id = dm.id
             WHERE dm.status IN ('dispatched','in_transit')
               AND dm.delivery_date <= CURRENT_DATE - CAST(:d || ' days' AS interval)
             GROUP BY dm.id, dm.manifest_number, dm.delivery_date, dm.status,
                      dm.driver_name
            HAVING COUNT(mc.id) FILTER (
                       WHERE mc.status IN ('pending','out_for_delivery')) > 0
             ORDER BY dm.delivery_date
        """), {"d": str(int(older_than_days))})).mappings().all()

    return {
        "runs": [{
            "manifest_id": str(r["id"]),
            "manifest_number": r["manifest_number"],
            "delivery_date": str(r["delivery_date"]) if r["delivery_date"] else None,
            "days_old": int(r["days_old"] or 0),
            "status": r["status"],
            "driver": r["driver_name"],
            "open_drops": int(r["open_drops"]),
            "total_drops": int(r["total_drops"]),
        } for r in rows],
        "count": len(rows),
        "open_drops": sum(int(r["open_drops"]) for r in rows),
        "note": ("Each open drop is a customer who may or may not have "
                 "received their goods, and nobody can say which. Mark each "
                 "one delivered or failed, then close the run."),
    }


async def history(session: AsyncSession, *, manifest_id: UUID) -> list:
    rows = (await session.execute(
        text("""SELECT e.from_status, e.to_status, e.note, e.actor_name,
                       e.created_at, mc.customer_name
                  FROM delivery_events e
             LEFT JOIN manifest_customers mc
                    ON mc.id = e.manifest_customer_id
                 WHERE e.manifest_id = :m
                 ORDER BY e.created_at DESC"""),
        {"m": str(manifest_id)})).mappings().all()
    return [{"from": r["from_status"], "to": r["to_status"],
             "note": r["note"], "actor": r["actor_name"],
             "customer": r["customer_name"],
             "at": r["created_at"].isoformat()} for r in rows]
