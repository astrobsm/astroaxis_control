"""Cost of goods sold: determine unit cost AT THE MOMENT OF SALE.

profits.py derives COGS by joining `product_pricing` live at query time. That
has three consequences, all of which make a general ledger impossible:

  1. **Closed periods get restated.** Raising a product's cost price today
     silently lowers the reported profit of every month already closed, paid
     out, and reported. Persisted `profit_settlements` no longer reconcile
     against the query they were derived from, and nothing explains the gap.
  2. **Duplicate pricing rows multiply COGS.** `(product_id, unit)` has no
     unique constraint, so a second 'carton' row makes the join fan out.
     `invoice_cost` doubles while `invoice_total` does not -- reported profit
     then exceeds the cash received, and the negative capital that implies is
     clamped to zero, hiding the inconsistency.
  3. **Deleting a price deletes history.** products.py deletes and recreates
     all ProductPricing rows on any product update.

The fix is to stop deriving historical cost and start recording it. Cost is
captured onto the order line when the sale happens and never recomputed --
the same principle that makes `stock_movements` an auditable ledger rather
than a guess.

This module only decides what that number is. Persisting it is the caller's
job (see sales.py order creation and receivables.ensure_invoice_for_order).
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Inbound movement types that carry a purchase/production cost worth averaging.
COSTED_INBOUND = ("IN", "PRODUCTION_IN", "TRANSFER_IN", "DAMAGE_TRANSFER_IN")

CENT = Decimal("0.01")
PRECISION = Decimal("0.000001")   # unit_cost columns are NUMERIC(18,6)


def _q(value, exp=PRECISION) -> Decimal:
    if value is None:
        return Decimal("0")
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    return d.quantize(exp, rounding=ROUND_HALF_UP)


async def weighted_average_cost(
    session: AsyncSession,
    *,
    product_id: UUID,
    warehouse_id: Optional[UUID] = None,
) -> Optional[Decimal]:
    """Weighted-average unit cost from the movement ledger.

    Uses the costs actually recorded when stock arrived -- purchase intakes
    and the per-unit production cost snapshotted by production completions --
    weighted by the quantity received. This is a real historical figure rather
    than whatever the price list happens to say today.

    Returns None when no inbound movement carries a cost, so callers can fall
    back explicitly instead of silently costing goods at zero.
    """
    clauses = ["product_id = :pid",
               "movement_type = ANY(:types)",
               "unit_cost IS NOT NULL",
               "unit_cost > 0",
               "quantity > 0"]
    params = {"pid": str(product_id), "types": list(COSTED_INBOUND)}
    if warehouse_id is not None:
        clauses.append("warehouse_id = :wid")
        params["wid"] = str(warehouse_id)

    row = (await session.execute(
        text(f"""
            SELECT SUM(quantity * unit_cost) AS total_cost,
                   SUM(quantity)             AS total_qty
              FROM stock_movements
             WHERE {' AND '.join(clauses)}
        """),
        params,
    )).first()

    if row is None or not row.total_qty or Decimal(str(row.total_qty)) <= 0:
        return None
    return _q(Decimal(str(row.total_cost)) / Decimal(str(row.total_qty)))


async def resolve_unit_cost(
    session: AsyncSession,
    *,
    product_id: UUID,
    unit: Optional[str] = None,
    warehouse_id: Optional[UUID] = None,
) -> tuple[Decimal, str]:
    """Best available unit cost right now, plus how it was determined.

    Returns (cost, source). `source` is stored alongside the cost so a later
    reader can tell a ledger-derived figure from a price-list fallback -- an
    audit needs to know which numbers are evidence and which are estimates.

    Order of preference:
      1. weighted average of this warehouse's costed inbound movements
      2. weighted average across all warehouses
      3. the matching product_pricing row for this unit
      4. products.cost_price
      5. zero, marked 'unknown'
    """
    if warehouse_id is not None:
        wac = await weighted_average_cost(
            session, product_id=product_id, warehouse_id=warehouse_id)
        if wac:
            return wac, "wac_warehouse"

    wac = await weighted_average_cost(session, product_id=product_id)
    if wac:
        return wac, "wac_global"

    if unit:
        row = (await session.execute(
            text("""
                SELECT cost_price FROM product_pricing
                 WHERE product_id = :pid AND LOWER(unit) = LOWER(:unit)
                   AND cost_price > 0
                 ORDER BY created_at DESC
                 LIMIT 1
            """),
            {"pid": str(product_id), "unit": unit},
        )).first()
        if row:
            return _q(row.cost_price), "price_list_unit"

    row = (await session.execute(
        text("SELECT cost_price FROM products WHERE id = :pid"),
        {"pid": str(product_id)},
    )).first()
    if row and row.cost_price and Decimal(str(row.cost_price)) > 0:
        return _q(row.cost_price), "product_cost_price"

    # Deliberately explicit rather than silently zero: 'unknown' lets profit
    # reporting exclude the line instead of booking 100% margin on it.
    return Decimal("0"), "unknown"


async def snapshot_line_cost(
    session: AsyncSession,
    *,
    line_id: UUID,
    table: str,
    product_id: UUID,
    quantity,
    unit: Optional[str] = None,
    warehouse_id: Optional[UUID] = None,
) -> tuple[Decimal, str]:
    """Resolve and persist the cost of one sale line, once.

    `table` is 'sales_order_lines' or 'invoice_lines' -- both carry the
    snapshot so an invoice remains a self-contained document.
    """
    if table not in ("sales_order_lines", "invoice_lines"):
        raise ValueError(f"unexpected table {table!r}")

    cost, source = await resolve_unit_cost(
        session, product_id=product_id, unit=unit, warehouse_id=warehouse_id)

    qty = _q(quantity)
    await session.execute(
        text(f"""
            UPDATE {table}
               SET unit_cost = :cost,
                   cost_total = :total,
                   cost_source = :source
             WHERE id = :lid
        """),
        {
            "cost": str(cost),
            "total": str((cost * qty).quantize(CENT, rounding=ROUND_HALF_UP)),
            "source": source,
            "lid": str(line_id),
        },
    )
    return cost, source
