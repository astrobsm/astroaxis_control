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
) -> UUID:
    """Apply one stock movement, updating the balance and writing the ledger.

    Returns the new `stock_movements` row id. Does not commit.
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
                 quantity, unit_cost, reference, notes, created_by, created_at)
            VALUES (gen_random_uuid(), :wid, :pid, :rmid, :mtype,
                    :qty, :cost, :ref, :notes, :by, NOW())
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
