from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Optional
from datetime import datetime

from app.db import get_session

router = APIRouter(prefix='/api/production-consumables', tags=['Production Consumables'])


def _row_to_dict(r):
    return {
        "id": str(r.id), "name": r.name, "unit": r.unit,
        "unit_cost": float(r.unit_cost), "category": r.category or 'General',
        "description": r.description or '',
        "current_stock": float(r.current_stock) if r.current_stock else 0,
        "reorder_level": float(r.reorder_level) if r.reorder_level else 0,
        "is_low_stock": (float(r.current_stock or 0) <= float(r.reorder_level or 0)) and float(r.reorder_level or 0) > 0,
        "last_restocked": r.last_restocked.isoformat() if r.last_restocked else None,
        "created_at": r.created_at.isoformat() if r.created_at else ''
    }


SELECT_COLS = "id, name, unit, unit_cost, category, description, current_stock, reorder_level, last_restocked, created_at"


@router.post('/')
async def create_consumable(data: dict, session: AsyncSession = Depends(get_session)):
    """Register a new production consumable with stock tracking."""
    result = await session.execute(
        text(f"""
            INSERT INTO production_consumables (id, name, unit, unit_cost, category, description, current_stock, reorder_level, created_at)
            VALUES (gen_random_uuid(), :name, :unit, :unit_cost, :category, :description, :current_stock, :reorder_level, NOW())
            RETURNING {SELECT_COLS}
        """),
        {
            "name": data.get('name', ''),
            "unit": data.get('unit', 'unit'),
            "unit_cost": float(data.get('unit_cost', 0)),
            "category": data.get('category', 'General'),
            "description": data.get('description', ''),
            "current_stock": float(data.get('current_stock', 0)),
            "reorder_level": float(data.get('reorder_level', 0)),
        }
    )
    row = result.fetchone()
    await session.commit()
    return {"message": f"Consumable '{row.name}' created", "data": _row_to_dict(row)}


@router.get('/')
async def list_consumables(
    page: int = Query(1, ge=1),
    size: int = Query(100, ge=1, le=200),
    session: AsyncSession = Depends(get_session)
):
    """List all production consumables with stock levels."""
    count_r = await session.execute(text("SELECT COUNT(*) FROM production_consumables"))
    total = count_r.scalar_one()
    offset = (page - 1) * size
    result = await session.execute(
        text(f"""
            SELECT {SELECT_COLS}
            FROM production_consumables ORDER BY name
            LIMIT :limit OFFSET :offset
        """),
        {"limit": size, "offset": offset}
    )
    items = [_row_to_dict(r) for r in result.fetchall()]
    return {"items": items, "total": total, "page": page, "size": size}


@router.get('/low-stock')
async def get_low_stock_consumables(session: AsyncSession = Depends(get_session)):
    """Get consumables that are at or below their reorder level."""
    result = await session.execute(
        text(f"""
            SELECT {SELECT_COLS}
            FROM production_consumables
            WHERE reorder_level > 0 AND current_stock <= reorder_level
            ORDER BY (current_stock / NULLIF(reorder_level, 0)) ASC, name
        """)
    )
    items = [_row_to_dict(r) for r in result.fetchall()]
    return {
        "items": items,
        "count": len(items),
        "alert": len(items) > 0,
        "message": f"{len(items)} consumable(s) at or below reorder level" if items else "All consumables adequately stocked"
    }


@router.put('/{consumable_id}')
async def update_consumable(consumable_id: str, data: dict, session: AsyncSession = Depends(get_session)):
    """Update a production consumable."""
    result = await session.execute(
        text(f"""
            UPDATE production_consumables
            SET name = :name, unit = :unit, unit_cost = :unit_cost, category = :category,
                description = :description, reorder_level = :reorder_level
            WHERE id = :id
            RETURNING {SELECT_COLS}
        """),
        {
            "id": consumable_id,
            "name": data.get('name', ''),
            "unit": data.get('unit', 'unit'),
            "unit_cost": float(data.get('unit_cost', 0)),
            "category": data.get('category', 'General'),
            "description": data.get('description', ''),
            "reorder_level": float(data.get('reorder_level', 0)),
        }
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Consumable not found")
    await session.commit()
    return {"message": f"Consumable '{row.name}' updated", "data": _row_to_dict(row)}


@router.post('/{consumable_id}/restock')
async def restock_consumable(consumable_id: str, data: dict, session: AsyncSession = Depends(get_session)):
    """Add stock to a consumable (restock / receive goods)."""
    quantity = float(data.get('quantity', 0))
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be positive")

    result = await session.execute(
        text(f"""
            UPDATE production_consumables
            SET current_stock = current_stock + :qty, last_restocked = NOW()
            WHERE id = :id
            RETURNING {SELECT_COLS}
        """),
        {"id": consumable_id, "qty": quantity}
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Consumable not found")
    await session.commit()
    return {
        "message": f"Restocked '{row.name}' with {quantity} {row.unit}. New stock: {float(row.current_stock)}",
        "data": _row_to_dict(row)
    }


@router.post('/{consumable_id}/adjust-stock')
async def adjust_stock(consumable_id: str, data: dict, session: AsyncSession = Depends(get_session)):
    """Manual stock adjustment (positive to add, negative to deduct)."""
    quantity = float(data.get('quantity', 0))
    reason = data.get('reason', 'Manual adjustment')

    result = await session.execute(
        text(f"""
            UPDATE production_consumables
            SET current_stock = GREATEST(current_stock + :qty, 0)
            WHERE id = :id
            RETURNING {SELECT_COLS}
        """),
        {"id": consumable_id, "qty": quantity}
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Consumable not found")
    await session.commit()
    return {
        "message": f"Stock adjusted for '{row.name}': {'+' if quantity >= 0 else ''}{quantity}. New stock: {float(row.current_stock)}",
        "data": _row_to_dict(row)
    }


@router.delete('/{consumable_id}')
async def delete_consumable(consumable_id: str, session: AsyncSession = Depends(get_session)):
    """Delete a production consumable."""
    result = await session.execute(
        text("DELETE FROM production_consumables WHERE id = :id RETURNING name"),
        {"id": consumable_id}
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Consumable not found")
    await session.commit()
    return {"message": f"Consumable '{row.name}' deleted"}
