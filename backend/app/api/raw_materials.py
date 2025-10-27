from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, and_, or_
from typing import List, Optional
import uuid

from app.db import get_session
from app.models import RawMaterial, StockLevel, StockMovement, Warehouse
from app.schemas import (
    RawMaterialSchema,
    RawMaterialCreate,
    RawMaterialUpdate,
    PaginatedResponse,
    ApiResponse
)

router = APIRouter(prefix='/api/raw-materials', tags=['Raw Materials'])

@router.post('/', response_model=ApiResponse)
async def create_raw_material(
    material_data: RawMaterialCreate,
    session: AsyncSession = Depends(get_session)
):
    """Create a new raw material"""
    # Check for duplicate SKU
    existing = await session.execute(
        select(RawMaterial).where(RawMaterial.sku == material_data.sku)
    )
    if existing.scalar():
        raise HTTPException(status_code=400, detail=f"Raw material with SKU '{material_data.sku}' already exists")
    
    new_material = RawMaterial(**material_data.model_dump())
    session.add(new_material)
    await session.commit()
    await session.refresh(new_material)
    
    return ApiResponse(
        message=f"Raw material '{new_material.name}' created successfully",
        data=RawMaterialSchema.model_validate(new_material)
    )

@router.get('/', response_model=PaginatedResponse)
async def list_raw_materials(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100),
    search: Optional[str] = Query(None, description="Search by SKU or name"),
    session: AsyncSession = Depends(get_session)
):
    """List raw materials with pagination and search"""
    offset = (page - 1) * size
    
    query = select(RawMaterial)
    count_query = select(func.count(RawMaterial.id))
    
    if search:
        search_filter = or_(
            RawMaterial.sku.ilike(f"%{search}%"),
            RawMaterial.name.ilike(f"%{search}%")
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)
    
    # Get total count
    total_result = await session.execute(count_query)
    total = total_result.scalar()
    
    # Get paginated results
    query = query.offset(offset).limit(size).order_by(RawMaterial.created_at.desc())
    result = await session.execute(query)
    materials = result.scalars().all()
    
    return PaginatedResponse(
        items=[RawMaterialSchema.model_validate(m) for m in materials],
        total=total,
        page=page,
        size=size,
        pages=(total + size - 1) // size
    )

@router.get('/{material_id}', response_model=ApiResponse)
async def get_raw_material(
    material_id: uuid.UUID,
    session: AsyncSession = Depends(get_session)
):
    """Get a specific raw material by ID"""
    result = await session.execute(
        select(RawMaterial).where(RawMaterial.id == material_id)
    )
    material = result.scalar()
    
    if not material:
        raise HTTPException(status_code=404, detail="Raw material not found")
    
    return ApiResponse(
        message="Raw material retrieved successfully",
        data=RawMaterialSchema.model_validate(material)
    )

@router.put('/{material_id}', response_model=ApiResponse)
async def update_raw_material(
    material_id: uuid.UUID,
    material_data: RawMaterialUpdate,
    session: AsyncSession = Depends(get_session)
):
    """Update a raw material"""
    result = await session.execute(
        select(RawMaterial).where(RawMaterial.id == material_id)
    )
    material = result.scalar()
    
    if not material:
        raise HTTPException(status_code=404, detail="Raw material not found")
    
    # Check for duplicate SKU if updating
    if material_data.sku and material_data.sku != material.sku:
        existing = await session.execute(
            select(RawMaterial).where(and_(RawMaterial.sku == material_data.sku, RawMaterial.id != material_id))
        )
        if existing.scalar():
            raise HTTPException(status_code=400, detail=f"Raw material with SKU '{material_data.sku}' already exists")
    
    # Update fields
    update_data = material_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(material, field, value)
    
    await session.commit()
    await session.refresh(material)
    
    return ApiResponse(
        message=f"Raw material '{material.name}' updated successfully",
        data=RawMaterialSchema.model_validate(material)
    )

@router.delete('/{material_id}', response_model=ApiResponse)
async def delete_raw_material(
    material_id: uuid.UUID,
    session: AsyncSession = Depends(get_session)
):
    """Delete a raw material (soft delete if has stock movements)"""
    result = await session.execute(
        select(RawMaterial).where(RawMaterial.id == material_id)
    )
    material = result.scalar()
    
    if not material:
        raise HTTPException(status_code=404, detail="Raw material not found")
    
    # Check if material has stock movements
    movements_result = await session.execute(
        select(func.count(StockMovement.id)).where(StockMovement.raw_material_id == material_id)
    )
    has_movements = movements_result.scalar() > 0
    
    if has_movements:
        raise HTTPException(
            status_code=400, 
            detail="Cannot delete raw material with existing stock movements. Archive it instead."
        )
    
    await session.delete(material)
    await session.commit()
    
    return ApiResponse(message=f"Raw material '{material.name}' deleted successfully")

@router.get('/{material_id}/stock', response_model=List[dict])
async def get_raw_material_stock(
    material_id: uuid.UUID,
    session: AsyncSession = Depends(get_session)
):
    """Get stock levels for a raw material across all warehouses"""
    result = await session.execute(
        select(StockLevel, Warehouse)
        .join(Warehouse, StockLevel.warehouse_id == Warehouse.id)
        .where(and_(StockLevel.raw_material_id == material_id, Warehouse.is_active == True))
    )
    
    stock_data = []
    for stock_level, warehouse in result:
        stock_data.append({
            "warehouse": {
                "id": warehouse.id,
                "code": warehouse.code,
                "name": warehouse.name
            },
            "current_stock": stock_level.current_stock,
            "reserved_stock": stock_level.reserved_stock,
            "available_stock": stock_level.current_stock - stock_level.reserved_stock,
            "min_stock": stock_level.min_stock,
            "max_stock": stock_level.max_stock,
            "stock_status": "LOW" if stock_level.current_stock <= stock_level.min_stock else 
                          "HIGH" if stock_level.current_stock >= stock_level.max_stock else "NORMAL"
        })
    
    return stock_data
