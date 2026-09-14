"""Single write path for every stock mutation.

Before this module, ~12 call sites each hand-rolled their own balance update.
The consequences were: balances changed without a ledger entry (and vice
versa), read-modify-write with no row lock so concurrent updates were lost,
one writer storing OUT quantities as negative numbers while every other stored
positive magnitudes, and no consistent negative-stock guard.

Every stock change now goes through `apply_stock_movement`, which guarantees
the four invariants that make product traceability possible:

  1. A balance never changes without a matching `stock_movements` row.
  2. The balance row is locked FOR UPDATE before it is read, so concurrent
     writers serialise instead of overwriting each other.
  3. Movements always store a positive magnitude; direction comes from
     `movement_type`.
  4. Stock cannot go negative unless the caller explicitly opts in.

None of these functions commit. The caller owns the transaction boundary, so
a multi-step operation (a transfer, a production completion) either lands
completely or not at all.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Direction of each movement type. A type absent from this map is rejected
# rather than silently ignored -- the old code had an if/elif chain where an
# unrecognised type changed nothing and returned success.
MOVEMENT_DIRECTION: dict[str, int] = {
    # inbound
    "IN": +1,
    "RETURN": +1,
    "TRANSFER_IN": +1,
    "PRODUCTION_IN": +1,
    "DAMAGE_TRANSFER_IN": +1,
    "ADJUST_IN": +1,
    # outbound
    "OUT": -1,
    "DAMAGE": -1,
    "TRANSFER_OUT": -1,
    "PRODUCTION_OUT": -1,
    "DAMAGE_TRANSFER_OUT": -1,
    "ADJUST_OUT": -1,
}

INBOUND = {k for k, v in MOVEMENT_DIRECTION.items() if v > 0}
OUTBOUND = {k for k, v in MOVEMENT_DIRECTION.items() if v < 0}


def _as_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


async def _lock_or_create_level(
    session: AsyncSession,
    warehouse_id: UUID,
    product_id: Optional[UUID],
    raw_material_id: Optional[UUID],
):
    """Return the (warehouse, item) balance row, locked FOR UPDATE.

    Creates the row if absent. The INSERT ... ON CONFLICT DO NOTHING relies on
    the partial unique indexes added in migration k0123456789j: without them,
    two concurrent first-time writers would each insert a row.
    """
    if product_id is not None:
        item_col, item_id, conflict = "product_id", product_id, (
            "(warehouse_id, product_id) WHERE product_id IS NOT NULL")
    else:
        item_col, item_id, conflict = "raw_material_id", raw_material_id, (
            "(warehouse_id, raw_material_id) WHERE raw_material_id IS NOT NULL")

    await session.execute(
        text(f"""
            INSERT INTO stock_levels
                (id, warehouse_id, {item_col}, current_stock,
                 reserved_stock, min_stock, max_stock, updated_at)
            VALUES (gen_random_uuid(), :wid, :iid, 0, 0, 0, 0, NOW())
            ON CONFLICT {conflict} DO NOTHING
        """),
        {"wid": str(warehouse_id), "iid": str(item_id)},
    )

    # FOR UPDATE is the whole point: it serialises concurrent mutations of the
    # same balance so a read-modify-write cannot lose an update.
    row = (await session.execute(
        text(f"""
            SELECT id, current_stock
              FROM stock_levels
             WHERE warehouse_id = :wid AND {item_col} = :iid
             FOR UPDATE
        """),
        {"wid": str(warehouse_id), "iid": str(item_id)},
    )).first()

    if row is None:
        # Only reachable if another transaction deleted the row between the
        # upsert and the lock.
        raise HTTPException(
            status_code=409,
            detail="Stock level row disappeared during update; please retry.",
        )
    return row


async def batch_balance(
    session: AsyncSession, *, batch_id: UUID,
    warehouse_id: Optional[UUID] = None,
):
    """How much of a batch is on hand, DERIVED from the movements.

    There is no stored batch balance, on purpose: a second copy of a quantity
    the system already knows drifts, and the drift surfaces during a recall --
    the one moment the number has to be right. See migration b7890123456a.
    """
    clauses = ["batch_id = :b"]
    params = {"b": str(batch_id)}
    if warehouse_id is not None:
        clauses.append("warehouse_id = :w")
        params["w"] = str(warehouse_id)

    inbound = ", ".join(f"'{t}'" for t in sorted(INBOUND))
    outbound = ", ".join(f"'{t}'" for t in sorted(OUTBOUND))
    row = (await session.execute(
        text(f"""
            SELECT COALESCE(SUM(CASE WHEN movement_type IN ({inbound})
                                     THEN quantity ELSE 0 END), 0)
                 - COALESCE(SUM(CASE WHEN movement_type IN ({outbound})
                                     THEN quantity ELSE 0 END), 0) AS balance
              FROM stock_movements
             WHERE {' AND '.join(clauses)}
        """), params)).first()
    return _as_decimal(row.balance if row else 0)


async def _check_batch(
    session: AsyncSession, *, batch_id: UUID, product_id: Optional[UUID],
    warehouse_id: UUID, movement_type: str, qty: Decimal, direction: int,
    allow_negative: bool,
) -> None:
    """Refuse a movement the batch cannot support, with a readable reason."""
    batch = (await session.execute(
        text("""SELECT id, batch_number, product_id, status, expiry_date
                  FROM product_batches WHERE id = :b"""),
        {"b": str(batch_id)})).mappings().first()
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found.")

    if product_id is None or str(batch["product_id"]) != str(product_id):
        raise HTTPException(
            status_code=400,
            detail=(f"Batch {batch['batch_number']} belongs to a different "
                    f"product. Stock cannot be attributed to a batch of "
                    f"something else."))

    if direction < 0:
        blocked = {
            "RECALLED": (f"Batch {batch['batch_number']} has been RECALLED and "
                         f"cannot be despatched. Goods on hand must be "
                         f"returned or destroyed, not sold."),
            "QUARANTINED": (f"Batch {batch['batch_number']} is QUARANTINED "
                            f"pending investigation. Release it first, with a "
                            f"reason."),
            "WITHDRAWN": (f"Batch {batch['batch_number']} has been withdrawn "
                          f"from sale and cannot be despatched."),
        }.get(batch["status"])
        if blocked:
            raise HTTPException(status_code=409, detail=blocked)

        if batch["expiry_date"] is not None and batch["expiry_date"] < date.today():
            raise HTTPException(
                status_code=409,
                detail=(f"Batch {batch['batch_number']} expired on "
                        f"{batch['expiry_date']} and cannot be despatched."))

        if not allow_negative:
            held = await batch_balance(
                session, batch_id=batch_id, warehouse_id=warehouse_id)
            if qty > held:
                raise HTTPException(
                    status_code=400,
                    detail=(f"Batch {batch['batch_number']} holds {held} in "
                            f"this warehouse; {qty} was requested. Issuing "
                            f"more of a batch than arrived would make its "
                            f"trace unusable."))


async def apply_stock_movement(
    session: AsyncSession,
    *,
    warehouse_id: UUID,
    movement_type: str,
    quantity,
    product_id: Optional[UUID] = None,
    raw_material_id: Optional[UUID] = None,
    reference: Optional[str] = None,
    notes: Optional[str] = None,
    created_by: Optional[UUID] = None,
    unit_cost=None,
    allow_negative: bool = False,
    batch_id: Optional[UUID] = None,
) -> UUID:
    """Apply one stock movement, updating the balance and writing the ledger.

    Returns the new `stock_movements` row id. Does not commit.

    BATCHES
    -------
    `batch_id` is optional and stays optional. Stock that moved before batches
    existed has none, and nothing invents one for it -- see migration
    b7890123456a. When a batch IS given, two further guarantees hold:

      5. A batch cannot be issued for more than it holds. The batch balance is
         derived from this same table, so it cannot disagree with itself.
      6. A quarantined, recalled, withdrawn or expired batch cannot leave the
         building. The database enforces this too, on every INSERT; the check
         here exists so the caller gets a useful error instead of a raw
         constraint violation.
    """
    if (product_id is None) == (raw_material_id is None):
        raise HTTPException(
            status_code=400,
            detail="Exactly one of product_id or raw_material_id must be given.",
        )

    direction = MOVEMENT_DIRECTION.get(movement_type)
    if direction is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown movement_type {movement_type!r}. "
                   f"Expected one of: {', '.join(sorted(MOVEMENT_DIRECTION))}",
        )

    qty = _as_decimal(quantity)
    if qty <= 0:
        # Direction is carried by movement_type, never by the sign of the
        # quantity -- mixing the two conventions corrupts every aggregate.
        raise HTTPException(
            status_code=400,
            detail="Quantity must be a positive magnitude; "
                   "use movement_type to indicate direction.",
        )

    if batch_id is not None:
        await _check_batch(
            session, batch_id=batch_id, product_id=product_id,
            warehouse_id=warehouse_id, movement_type=movement_type, qty=qty,
            direction=direction, allow_negative=allow_negative)

    level = await _lock_or_create_level(
        session, warehouse_id, product_id, raw_material_id)

    current = _as_decimal(level.current_stock or 0)
    new_balance = current + (qty * direction)

    if new_balance < 0 and not allow_negative:
        raise HTTPException(
            status_code=400,
            detail=(f"Insufficient stock. Available: {current}, "
                    f"requested: {qty}."),
        )

    await session.execute(
        text("""
            UPDATE stock_levels
               SET current_stock = :bal, updated_at = NOW()
             WHERE id = :lid
        """),
        {"bal": str(new_balance), "lid": str(level.id)},
    )

    movement_id = (await session.execute(
        text("""
            INSERT INTO stock_movements
                (id, warehouse_id, product_id, raw_material_id, movement_type,
                 quantity, unit_cost, reference, notes, created_by, batch_id,
                 created_at)
            VALUES (gen_random_uuid(), :wid, :pid, :rmid, :mtype,
                    :qty, :cost, :ref, :notes, :by, CAST(:batch AS uuid), NOW())
            RETURNING id
        """),
        {
            "wid": str(warehouse_id),
            "pid": str(product_id) if product_id else None,
            "rmid": str(raw_material_id) if raw_material_id else None,
            "mtype": movement_type,
            "qty": str(qty),
            "cost": str(_as_decimal(unit_cost)) if unit_cost is not None else None,
            "ref": reference,
            "notes": notes,
            "by": str(created_by) if created_by else None,
            "batch": str(batch_id) if batch_id else None,
        },
    )).scalar_one()

    return movement_id


async def transfer_stock(
    session: AsyncSession,
    *,
    from_warehouse_id: UUID,
    to_warehouse_id: UUID,
    quantity,
    product_id: Optional[UUID] = None,
    raw_material_id: Optional[UUID] = None,
    reference: Optional[str] = None,
    notes: Optional[str] = None,
    created_by: Optional[UUID] = None,
    batch_id: Optional[UUID] = None,
) -> tuple[UUID, UUID]:
    """Move stock between warehouses as one atomic pair of movements.

    Both legs are written in the caller's transaction, so a transfer can never
    leave goods deducted from the source without arriving at the destination.
    Warehouses are locked in a consistent (sorted) order to avoid deadlocking
    against a simultaneous transfer in the opposite direction.
    """
    if from_warehouse_id == to_warehouse_id:
        raise HTTPException(
            status_code=400,
            detail="Source and destination warehouses must differ.",
        )

    legs = [
        (from_warehouse_id, "TRANSFER_OUT"),
        (to_warehouse_id, "TRANSFER_IN"),
    ]
    # Deterministic lock ordering: A->B and B->A running concurrently would
    # otherwise each hold the lock the other needs.
    legs.sort(key=lambda leg: str(leg[0]))

    ids = {}
    for warehouse_id, movement_type in legs:
        ids[movement_type] = await apply_stock_movement(
            session,
            warehouse_id=warehouse_id,
            movement_type=movement_type,
            quantity=quantity,
            product_id=product_id,
            raw_material_id=raw_material_id,
            reference=reference,
            notes=notes,
            created_by=created_by,
            # Carried on BOTH legs. A transfer that dropped the batch at the
            # destination would move goods out of traceability by moving them
            # between shelves, which is the failure this phase exists to stop.
            batch_id=batch_id,
        )
    return ids["TRANSFER_OUT"], ids["TRANSFER_IN"]


async def get_available_stock(
    session: AsyncSession,
    *,
    warehouse_id: UUID,
    product_id: Optional[UUID] = None,
    raw_material_id: Optional[UUID] = None,
) -> Decimal:
    """Current on-hand balance, or 0 if the item has never been stocked."""
    item_col = "product_id" if product_id is not None else "raw_material_id"
    item_id = product_id if product_id is not None else raw_material_id
    row = (await session.execute(
        text(f"""
            SELECT current_stock FROM stock_levels
             WHERE warehouse_id = :wid AND {item_col} = :iid
        """),
        {"wid": str(warehouse_id), "iid": str(item_id)},
    )).first()
    return _as_decimal(row.current_stock) if row else Decimal("0")
