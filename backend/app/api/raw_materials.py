from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import List, Optional
import re
from datetime import datetime

from app.db import get_session
from app.schemas import (
    RawMaterialCreate,
    RawMaterialUpdate,
    ApiResponse
)

router = APIRouter(prefix='/api/raw-materials', tags=['Raw Materials'])


async def generate_raw_material_sku(name: str, session: AsyncSession) -> str:
    """Auto-generate SKU from raw material name.
    Takes first letters of each word, uppercased, prefixed with RM-.
    If collision, appends incrementing number.
    E.g., 'Cotton Gauze' -> 'RM-CG', 'Cotton Gauze Roll' -> 'RM-CGR'
    """
    words = re.sub(r'[^a-zA-Z0-9\s]', '', name).split()
    if not words:
        initials = 'XX'
    elif len(words) == 1:
        initials = words[0][:3].upper()
    else:
        initials = ''.join(w[0].upper() for w in words if w)
    
    base_sku = f'RM-{initials}'
    candidate = base_sku
    counter = 1
    
    while True:
        result = await session.execute(
            text("SELECT COUNT(*) FROM raw_materials WHERE sku = :sku"),
            {"sku": candidate}
        )
        if result.scalar_one() == 0:
            return candidate
        counter += 1
        candidate = f'{base_sku}{counter:02d}'


# Endpoint to get next suggested SKU
@router.get('/next-sku')
async def get_next_sku(
    name: str = Query(..., description="Raw material name to generate SKU from"),
    session: AsyncSession = Depends(get_session)
):
    """Generate next available SKU based on raw material name"""
    sku = await generate_raw_material_sku(name, session)
    return {"sku": sku}


def _row_to_dict(row):
    """Convert a raw SQL row to dict for API response"""
    return {
        "id": row.id,
        "sku": row.sku or '',
        "name": row.name or '',
        "manufacturer": getattr(row, 'manufacturer', None) or '',
        "unit": getattr(row, 'unit', None) or 'kg',
        "reorder_level": float(getattr(row, 'reorder_level', 0) or 0),
        "unit_cost": float(row.unit_cost or 0),
        "created_at": row.created_at.isoformat() if getattr(row, 'created_at', None) else datetime.now().isoformat()
    }


@router.post('/')
async def create_raw_material(
    material_data: RawMaterialCreate,
    session: AsyncSession = Depends(get_session)
):
    """Create a new raw material (SKU auto-generated if not provided)"""
    data = material_data.model_dump()
    
    # Auto-generate SKU if not provided or empty
    if not data.get('sku') or not data['sku'].strip():
        data['sku'] = await generate_raw_material_sku(data['name'], session)
    else:
        # Check for duplicate SKU
        r = await session.execute(
            text("SELECT COUNT(*) FROM raw_materials WHERE sku = :sku"),
            {"sku": data['sku']}
        )
        if r.scalar_one() > 0:
            raise HTTPException(status_code=400, detail=f"Raw material with SKU '{data['sku']}' already exists")
    
    # Insert using raw SQL (production DB has integer ID with auto-increment)
    # Production columns: name(NOT NULL), category(NOT NULL), source(NOT NULL), uom(NOT NULL),
    # reorder_point(NOT NULL int), unit_cost(NOT NULL float), rm_id, opening_stock,
    # sku, manufacturer, unit, reorder_level, created_at
    result = await session.execute(
        text("""
            INSERT INTO raw_materials (
                name, sku, manufacturer, unit, reorder_level, unit_cost, created_at,
                category, source, uom, reorder_point
            )
            VALUES (
                :name, :sku, :manufacturer, :unit, :reorder_level, :unit_cost, NOW(),
                :category, :source, :uom, :reorder_point
            )
            RETURNING id, name, sku, manufacturer, unit, reorder_level, unit_cost, created_at
        """),
        {
            "name": data['name'],
            "sku": data['sku'],
            "manufacturer": data.get('manufacturer') or '',
            "unit": data.get('unit') or 'kg',
            "reorder_level": float(data.get('reorder_level') or 0),
            "unit_cost": float(data.get('unit_cost') or 0),
            "category": data.get('category') or 'General',
            "source": data.get('source') or 'Local',
            "uom": data.get('unit') or 'kg',
            "reorder_point": int(data.get('reorder_level') or 0),
        }
    )
    await session.commit()
    row = result.fetchone()
    
    return {
        "message": f"Raw material '{row.name}' created successfully (SKU: {row.sku})",
        "data": _row_to_dict(row)
    }


@router.get('/')
async def list_raw_materials(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100),
    search: Optional[str] = Query(None, description="Search by SKU or name"),
    session: AsyncSession = Depends(get_session)
):
    """List raw materials with pagination and search"""
    offset = (page - 1) * size
    
    if search:
        count_result = await session.execute(
            text("SELECT COUNT(*) FROM raw_materials WHERE sku ILIKE :s OR name ILIKE :s"),
            {"s": f"%{search}%"}
        )
        total = count_result.scalar_one()
        
        result = await session.execute(
            text("""
                SELECT id, name, sku, manufacturer, unit, reorder_level, unit_cost, created_at
                FROM raw_materials WHERE sku ILIKE :s OR name ILIKE :s
                ORDER BY created_at DESC NULLS LAST
                LIMIT :limit OFFSET :offset
            """),
            {"s": f"%{search}%", "limit": size, "offset": offset}
        )
    else:
        count_result = await session.execute(text("SELECT COUNT(*) FROM raw_materials"))
        total = count_result.scalar_one()
        
        result = await session.execute(
            text("""
                SELECT id, name, sku, manufacturer, unit, reorder_level, unit_cost, created_at
                FROM raw_materials
                ORDER BY created_at DESC NULLS LAST
                LIMIT :limit OFFSET :offset
            """),
            {"limit": size, "offset": offset}
        )
    
    materials = [_row_to_dict(row) for row in result.fetchall()]
    
    return {
        "items": materials,
        "total": total,
        "page": page,
        "size": size,
        "pages": max(1, (total + size - 1) // size)
    }


@router.get('/{material_id}')
async def get_raw_material(
    material_id: int,
    session: AsyncSession = Depends(get_session)
):
    """Get a specific raw material by ID"""
    result = await session.execute(
        text("SELECT id, name, sku, manufacturer, unit, reorder_level, unit_cost, created_at FROM raw_materials WHERE id = :id"),
        {"id": material_id}
    )
    row = result.fetchone()
    
    if not row:
        raise HTTPException(status_code=404, detail="Raw material not found")
    
    return {
        "message": "Raw material retrieved successfully",
        "data": _row_to_dict(row)
    }


@router.put('/{material_id}')
async def update_raw_material(
    material_id: int,
    material_data: RawMaterialUpdate,
    session: AsyncSession = Depends(get_session)
):
    """Update a raw material"""
    r = await session.execute(
        text("SELECT id, name FROM raw_materials WHERE id = :id"),
        {"id": material_id}
    )
    if not r.fetchone():
        raise HTTPException(status_code=404, detail="Raw material not found")
    
    update_data = material_data.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    # Check for duplicate SKU if updating
    if 'sku' in update_data and update_data['sku']:
        dup = await session.execute(
            text("SELECT COUNT(*) FROM raw_materials WHERE sku = :sku AND id != :id"),
            {"sku": update_data['sku'], "id": material_id}
        )
        if dup.scalar_one() > 0:
            raise HTTPException(status_code=400, detail=f"Raw material with SKU '{update_data['sku']}' already exists")
    
    # Build dynamic update
    set_clauses = []
    params = {"id": material_id}
    for field, value in update_data.items():
        set_clauses.append(f"{field} = :{field}")
        params[field] = float(value) if field in ('unit_cost', 'reorder_level') and value is not None else value
    
    sql = f"UPDATE raw_materials SET {', '.join(set_clauses)} WHERE id = :id RETURNING id, name, sku, manufacturer, unit, reorder_level, unit_cost, created_at"
    result = await session.execute(text(sql), params)
    await session.commit()
    row = result.fetchone()
    
    return {
        "message": f"Raw material '{row.name}' updated successfully",
        "data": _row_to_dict(row)
    }


@router.delete('/{material_id}')
async def delete_raw_material(
    material_id: int,
    session: AsyncSession = Depends(get_session)
):
    """Delete a raw material"""
    r = await session.execute(
        text("SELECT name FROM raw_materials WHERE id = :id"),
        {"id": material_id}
    )
    row = r.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Raw material not found")
    
    name = row.name
    
    # Check for stock movements (handle case where raw_material_id column may not exist)
    try:
        mv = await session.execute(
            text("SELECT COUNT(*) FROM stock_movements WHERE raw_material_id = :id"),
            {"id": material_id}
        )
        if mv.scalar_one() > 0:
            raise HTTPException(
                status_code=400,
                detail="Cannot delete raw material with existing stock movements. Archive it instead."
            )
    except HTTPException:
        raise
    except Exception:
        # Column may not exist in production - rollback the failed transaction and skip the check
        await session.rollback()
    
    await session.execute(
        text("DELETE FROM raw_materials WHERE id = :id"),
        {"id": material_id}
    )
    await session.commit()
    
    return {"message": f"Raw material '{name}' deleted successfully"}


@router.get('/{material_id}/stock')
async def get_raw_material_stock(
    material_id: int,
    session: AsyncSession = Depends(get_session)
):
    """Get stock levels for a raw material across all warehouses"""
    result = await session.execute(
        text("""
            SELECT sl.current_stock, sl.min_stock, sl.max_stock,
                   w.id as wh_id, w.name as wh_name, w.wh_id as wh_code
            FROM stock_levels sl
            LEFT JOIN warehouses w ON sl.warehouse_id::text = w.id::text
            WHERE sl.raw_material_id = :rm_id
        """),
        {"rm_id": material_id}
    )
    
    stock_data = []
    for row in result.fetchall():
        current = float(row.current_stock or 0)
        min_s = float(row.min_stock or 0)
        max_s = float(row.max_stock or 0)
        stock_data.append({
            "warehouse": {
                "id": row.wh_id,
                "code": row.wh_code or '',
                "name": row.wh_name or 'Default'
            },
            "current_stock": current,
            "reserved_stock": 0,
            "available_stock": current,
            "min_stock": min_s,
            "max_stock": max_s,
            "stock_status": "LOW" if current <= min_s else "HIGH" if max_s > 0 and current >= max_s else "NORMAL"
        })
    
    return stock_data
