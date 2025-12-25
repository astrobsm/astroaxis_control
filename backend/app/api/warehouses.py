from fastapi import APIRouter, Depends, HTTPException, Query, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, and_, or_
from typing import List, Optional
import uuid

from app.db import get_session
from app.models import Warehouse, StockLevel, StockMovement, user_warehouses
from app.schemas import (
    WarehouseSchema,
    WarehouseCreate,
    WarehouseUpdate,
    PaginatedResponse,
    ApiResponse
)
from app.api.auth import get_current_user

router = APIRouter(prefix='/api/warehouses', tags=['Warehouses'])

@router.post('/', response_model=ApiResponse)
async def create_warehouse(
    warehouse_data: WarehouseCreate,
    session: AsyncSession = Depends(get_session)
):
    """Create a new warehouse"""
    # Check for duplicate code
    existing = await session.execute(
        select(Warehouse).where(Warehouse.code == warehouse_data.code)
    )
    if existing.scalar():
        raise HTTPException(status_code=400, detail=f"Warehouse with code '{warehouse_data.code}' already exists")
    
    new_warehouse = Warehouse(**warehouse_data.model_dump())
    session.add(new_warehouse)
    await session.commit()
    await session.refresh(new_warehouse)
    
    return ApiResponse(
        message=f"Warehouse '{new_warehouse.name}' created successfully",
        data=WarehouseSchema.model_validate(new_warehouse)
    )

@router.get('/', response_model=PaginatedResponse)
async def list_warehouses(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100),
    search: Optional[str] = Query(None, description="Search warehouses"),
    active_only: bool = Query(True, description="Show only active"),
    authorization: Optional[str] = Header(None),
    session: AsyncSession = Depends(get_session)
):
    """List warehouses - admins see all, others see only their assigned warehouses"""
    offset = (page - 1) * size
    
    # Get current user if authenticated
    user_role = None
    user_id = None
    
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Authorization header required",
        )

    token = authorization.replace("Bearer ", "")
    try:
        user_payload = await get_current_user(token, session)
        user_role = user_payload.get('role')
        user_id = user_payload.get('id')
    except HTTPException:
        raise
    except Exception as auth_error:
        raise HTTPException(
            status_code=401,
            detail=f"Invalid authentication context: {auth_error}",
        )
    
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail="User context is missing an identifier",
        )
    
    try:
        user_uuid = uuid.UUID(user_id)
    except Exception:
        raise HTTPException(
            status_code=401,
            detail="Invalid user identifier",
        )
    
    query = select(Warehouse)
    count_query = select(func.count(Warehouse.id))
    
    filters = []
    if active_only:
        filters.append(Warehouse.is_active.is_(True))
    
    # Filter warehouses based on user access (non-admins only)
    if user_role and user_role != 'admin':
        query = query.join(
            user_warehouses,
            Warehouse.id == user_warehouses.c.warehouse_id
        ).where(user_warehouses.c.user_id == user_uuid)
        count_query = count_query.join(
            user_warehouses,
            Warehouse.id == user_warehouses.c.warehouse_id
        ).where(user_warehouses.c.user_id == user_uuid)
    
    if search:
        search_filter = or_(
            Warehouse.code.ilike(f"%{search}%"),
            Warehouse.name.ilike(f"%{search}%"),
            Warehouse.location.ilike(f"%{search}%")
        )
        filters.append(search_filter)
    
    if filters:
        query = query.where(and_(*filters))
        count_query = count_query.where(and_(*filters))
    
    # Get total count
    total_result = await session.execute(count_query)
    total = total_result.scalar()
    
    # Get paginated results
    query = query.offset(offset).limit(size).order_by(
        Warehouse.created_at.desc()
    )
    result = await session.execute(query)
    warehouses = result.scalars().all()
    
    return PaginatedResponse(
        items=[WarehouseSchema.model_validate(w) for w in warehouses],
        total=total,
        page=page,
        size=size,
        pages=(total + size - 1) // size
    )

@router.get('/{warehouse_id}', response_model=ApiResponse)
async def get_warehouse(
    warehouse_id: uuid.UUID,
    session: AsyncSession = Depends(get_session)
):
    """Get a specific warehouse by ID"""
    result = await session.execute(
        select(Warehouse).where(Warehouse.id == warehouse_id)
    )
    warehouse = result.scalar()
    
    if not warehouse:
        raise HTTPException(status_code=404, detail="Warehouse not found")
    
    return ApiResponse(
        message="Warehouse retrieved successfully",
        data=WarehouseSchema.model_validate(warehouse)
    )

@router.put('/{warehouse_id}', response_model=ApiResponse)
async def update_warehouse(
    warehouse_id: uuid.UUID,
    warehouse_data: WarehouseUpdate,
    session: AsyncSession = Depends(get_session)
):
    """Update a warehouse"""
    result = await session.execute(
        select(Warehouse).where(Warehouse.id == warehouse_id)
    )
    warehouse = result.scalar()
    
    if not warehouse:
        raise HTTPException(status_code=404, detail="Warehouse not found")
    
    # Check for duplicate code if updating
    if warehouse_data.code and warehouse_data.code != warehouse.code:
        existing = await session.execute(
            select(Warehouse).where(and_(Warehouse.code == warehouse_data.code, Warehouse.id != warehouse_id))
        )
        if existing.scalar():
            raise HTTPException(status_code=400, detail=f"Warehouse with code '{warehouse_data.code}' already exists")
    
    # Update fields
    update_data = warehouse_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(warehouse, field, value)
    
    await session.commit()
    await session.refresh(warehouse)
    
    return ApiResponse(
        message=f"Warehouse '{warehouse.name}' updated successfully",
        data=WarehouseSchema.model_validate(warehouse)
    )

@router.delete('/{warehouse_id}', response_model=ApiResponse)
async def delete_warehouse(
    warehouse_id: uuid.UUID,
    force: bool = Query(False, description="Force delete even with stock"),
    session: AsyncSession = Depends(get_session)
):
    """Delete or deactivate a warehouse"""
    result = await session.execute(
        select(Warehouse).where(Warehouse.id == warehouse_id)
    )
    warehouse = result.scalar()
    
    if not warehouse:
        raise HTTPException(status_code=404, detail="Warehouse not found")
    
    # Check if warehouse has stock
    stock_result = await session.execute(
        select(func.count(StockLevel.id)).where(and_(
            StockLevel.warehouse_id == warehouse_id,
            StockLevel.current_stock > 0
        ))
    )
    has_stock = stock_result.scalar() > 0
    
    if has_stock and not force:
        # Deactivate instead of delete
        warehouse.is_active = False
        await session.commit()
        return ApiResponse(message=f"Warehouse '{warehouse.name}' deactivated (has existing stock)")
    
    # Check for movements
    movements_result = await session.execute(
        select(func.count(StockMovement.id)).where(StockMovement.warehouse_id == warehouse_id)
    )
    has_movements = movements_result.scalar() > 0
    
    if has_movements and not force:
        raise HTTPException(
            status_code=400, 
            detail="Cannot delete warehouse with stock movements. Use deactivation instead or force delete."
        )
    
    await session.delete(warehouse)
    await session.commit()
    
    return ApiResponse(message=f"Warehouse '{warehouse.name}' deleted successfully")

@router.get('/{warehouse_id}/summary', response_model=dict)
async def get_warehouse_summary(
    warehouse_id: uuid.UUID,
    session: AsyncSession = Depends(get_session)
):
    """Get warehouse summary with stock counts and values"""
    # Verify warehouse exists
    warehouse_result = await session.execute(
        select(Warehouse).where(Warehouse.id == warehouse_id)
    )
    warehouse = warehouse_result.scalar()
    
    if not warehouse:
        raise HTTPException(status_code=404, detail="Warehouse not found")
    
    # Get stock counts
    stock_result = await session.execute(
        select(
            func.count(StockLevel.id).label('total_items'),
            func.sum(StockLevel.current_stock).label('total_quantity'),
            func.count().filter(StockLevel.current_stock <= StockLevel.min_stock).label('low_stock_items')
        ).where(and_(
            StockLevel.warehouse_id == warehouse_id,
            StockLevel.current_stock > 0
        ))
    )
    stock_data = stock_result.first()
    
    # Get recent movements count
    recent_movements = await session.execute(
        select(func.count(StockMovement.id)).where(and_(
            StockMovement.warehouse_id == warehouse_id,
            func.date(StockMovement.created_at) == func.current_date()
        ))
    )
    
    return {
        "warehouse": WarehouseSchema.model_validate(warehouse).model_dump(),
        "stock_summary": {
            "total_items": stock_data.total_items or 0,
            "total_quantity": float(stock_data.total_quantity or 0),
            "low_stock_items": stock_data.low_stock_items or 0
        },
        "activity": {
            "movements_today": recent_movements.scalar() or 0
        }
    }
