from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Optional
from datetime import datetime

from app.db import get_session

router = APIRouter(prefix='/api/production-consumables', tags=['Production Consumables'])


@router.post('/')
async def create_consumable(data: dict, session: AsyncSession = Depends(get_session)):
    """Register a new production consumable (energy, lunch, packaging, etc.)"""
    result = await session.execute(
        text("""
            INSERT INTO production_consumables (id, name, unit, unit_cost, category, description, created_at)
            VALUES (gen_random_uuid(), :name, :unit, :unit_cost, :category, :description, NOW())
            RETURNING id, name, unit, unit_cost, category, description, created_at
        """),
        {
            "name": data.get('name', ''),
            "unit": data.get('unit', 'unit'),
            "unit_cost": float(data.get('unit_cost', 0)),
            "category": data.get('category', 'General'),
            "description": data.get('description', ''),
        }
    )
    row = result.fetchone()
    await session.commit()
    return {
        "message": f"Consumable '{row.name}' created",
        "data": {
            "id": str(row.id), "name": row.name, "unit": row.unit,
            "unit_cost": float(row.unit_cost), "category": row.category,
            "description": row.description or '',
            "created_at": row.created_at.isoformat() if row.created_at else ''
        }
    }


@router.get('/')
async def list_consumables(
    page: int = Query(1, ge=1),
    size: int = Query(100, ge=1, le=200),
    session: AsyncSession = Depends(get_session)
):
    """List all production consumables"""
    count_r = await session.execute(text("SELECT COUNT(*) FROM production_consumables"))
    total = count_r.scalar_one()
    offset = (page - 1) * size
    result = await session.execute(
        text("""
            SELECT id, name, unit, unit_cost, category, description, created_at
            FROM production_consumables ORDER BY name
            LIMIT :limit OFFSET :offset
        """),
        {"limit": size, "offset": offset}
    )
    items = []
    for r in result.fetchall():
        items.append({
            "id": str(r.id), "name": r.name, "unit": r.unit,
            "unit_cost": float(r.unit_cost), "category": r.category,
            "description": r.description or '',
            "created_at": r.created_at.isoformat() if r.created_at else ''
        })
    return {"items": items, "total": total, "page": page, "size": size}


@router.put('/{consumable_id}')
async def update_consumable(consumable_id: str, data: dict, session: AsyncSession = Depends(get_session)):
    """Update a production consumable"""
    result = await session.execute(
        text("""
            UPDATE production_consumables
            SET name = :name, unit = :unit, unit_cost = :unit_cost, category = :category, description = :description
            WHERE id = :id
            RETURNING id, name, unit, unit_cost, category, description
        """),
        {
            "id": consumable_id,
            "name": data.get('name', ''),
            "unit": data.get('unit', 'unit'),
            "unit_cost": float(data.get('unit_cost', 0)),
            "category": data.get('category', 'General'),
            "description": data.get('description', ''),
        }
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Consumable not found")
    await session.commit()
    return {
        "message": f"Consumable '{row.name}' updated",
        "data": {"id": str(row.id), "name": row.name, "unit": row.unit, "unit_cost": float(row.unit_cost), "category": row.category}
    }


@router.delete('/{consumable_id}')
async def delete_consumable(consumable_id: str, session: AsyncSession = Depends(get_session)):
    """Delete a production consumable"""
    result = await session.execute(
        text("DELETE FROM production_consumables WHERE id = :id RETURNING name"),
        {"id": consumable_id}
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Consumable not found")
    await session.commit()
    return {"message": f"Consumable '{row.name}' deleted"}
