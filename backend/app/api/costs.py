"""Cost master maintenance — the data the accounting engine needs to value
stock and compute gross profit.

A focused surface for keeping product and raw-material unit costs correct.
Without these, inventory cannot be valued and every sale's margin is unknown;
the opening-balance and going-forward COGS postings both read from here.

Each product row also carries its selling price and the implied margin, so a
cost typed in higher than the selling price -- the exact class of error that
made historical COGS come out at 101% of revenue -- is visible as a negative
margin the moment it is entered.
"""
from __future__ import annotations

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.api.auth import require_admin
from app.models import User

router = APIRouter(prefix='/api/costs', tags=['Cost Master'])


class CostUpdate(BaseModel):
    id: UUID
    cost: float = Field(..., ge=0)


class BulkCostIn(BaseModel):
    updates: List[CostUpdate]


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

@router.get('/products')
async def list_product_costs(
    session: AsyncSession = Depends(get_session),
    _admin: User = Depends(require_admin),
):
    """Every product PRICE LINE with its cost, selling price, margin and stock.

    Costs live on `product_pricing` — one row per (product, unit), the same
    table the product-registration form writes. This overview reads that source
    of truth (not the vestigial products.cost_price), so it reflects what the
    costing engine actually uses to value stock and compute COGS. Each row is a
    unit variant (e.g. a product sold by TUBE and by CARTON has two).
    """
    rows = (await session.execute(text("""
        SELECT pp.id, p.sku, p.name, pp.unit,
               pp.cost_price, pp.retail_price, pp.wholesale_price,
               COALESCE(s.on_hand, 0) AS on_hand
          FROM product_pricing pp
          JOIN products p ON p.id = pp.product_id
          LEFT JOIN (
              SELECT product_id, SUM(current_stock) AS on_hand
                FROM stock_levels
               WHERE product_id IS NOT NULL
               GROUP BY product_id
          ) s ON s.product_id = pp.product_id
         ORDER BY p.name, pp.unit
    """))).fetchall()
    out = []
    for r in rows:
        cost = float(r.cost_price) if r.cost_price is not None else None
        sell = float(r.retail_price) if r.retail_price is not None else None
        margin = None
        if cost is not None and sell:
            margin = round((sell - cost) / sell * 100, 1)
        out.append({
            "id": str(r.id), "sku": r.sku, "name": r.name, "unit": r.unit,
            "cost_price": cost, "selling_price": sell,
            "margin_percent": margin,
            "on_hand": float(r.on_hand or 0),
            "missing_cost": cost is None or cost == 0,
        })
    return out


@router.put('/products')
async def update_product_costs(
    body: BulkCostIn,
    session: AsyncSession = Depends(get_session),
    _admin: User = Depends(require_admin),
):
    """Set cost_price on one or more product_pricing rows (by price-line id)."""
    updated = 0
    try:
        for u in body.updates:
            res = await session.execute(
                text("UPDATE product_pricing SET cost_price = :c WHERE id = :i"),
                {"c": u.cost, "i": str(u.id)})
            updated += res.rowcount or 0
        await session.commit()
        return {"success": True, "updated": updated}
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Raw materials
# ---------------------------------------------------------------------------

@router.get('/materials')
async def list_material_costs(
    session: AsyncSession = Depends(get_session),
    _admin: User = Depends(require_admin),
):
    """Every raw material with its unit cost and stock on hand.

    Stock value (cost x on-hand) is surfaced so an outlier cost -- the WATER at
    3,000/kg that valued to ~15M -- stands out against the rest.
    """
    rows = (await session.execute(text("""
        SELECT rm.id, rm.name, COALESCE(rm.uom, rm.unit) AS unit, rm.unit_cost,
               COALESCE(s.on_hand, 0) AS on_hand
          FROM raw_materials rm
          LEFT JOIN (
              SELECT raw_material_id, SUM(current_stock) AS on_hand
                FROM stock_levels
               WHERE raw_material_id IS NOT NULL
               GROUP BY raw_material_id
          ) s ON s.raw_material_id = rm.id
         ORDER BY (COALESCE(rm.unit_cost,0) * COALESCE(s.on_hand,0)) DESC,
                  rm.name
    """))).fetchall()
    return [
        {"id": str(r.id), "name": r.name, "unit": r.unit,
         "unit_cost": float(r.unit_cost) if r.unit_cost is not None else None,
         "on_hand": float(r.on_hand or 0),
         "stock_value": round(float(r.unit_cost or 0) * float(r.on_hand or 0), 2),
         "missing_cost": r.unit_cost is None or r.unit_cost == 0}
        for r in rows
    ]


@router.put('/materials')
async def update_material_costs(
    body: BulkCostIn,
    session: AsyncSession = Depends(get_session),
    _admin: User = Depends(require_admin),
):
    """Set unit_cost on one or more raw materials."""
    updated = 0
    try:
        for u in body.updates:
            res = await session.execute(
                text("UPDATE raw_materials SET unit_cost = :c WHERE id = :i"),
                {"c": u.cost, "i": str(u.id)})
            updated += res.rowcount or 0
        await session.commit()
        return {"success": True, "updated": updated}
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e))
