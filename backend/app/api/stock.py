from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, and_, or_, text
from typing import List, Optional
from decimal import Decimal
import uuid

from app.db import get_session
from app.models import (
    StockMovement, StockLevel, Product, RawMaterial, 
    Warehouse, User
)
from app.schemas import (
    StockMovementCreateProduct,
    StockMovementCreateRawMaterial, 
    StockMovementSchema,
    StockLevelSchema,
    StockIntakeCreate,
    RawMaterialStockIntakeCreate,
    PaginatedResponse,
    ApiResponse
)

router = APIRouter(prefix='/api/stock', tags=['Stock Management'])

async def update_stock_level(
    session: AsyncSession,
    warehouse_id: uuid.UUID,
    product_id: Optional[uuid.UUID] = None,
    raw_material_id: Optional[uuid.UUID] = None,
    quantity_change: Decimal = 0,
    movement_type: str = "IN"
):
    """Update stock level based on movement"""
    # Find existing stock level
    filters = [StockLevel.warehouse_id == warehouse_id]
    if product_id:
        filters.append(StockLevel.product_id == product_id)
    if raw_material_id:
        filters.append(StockLevel.raw_material_id == raw_material_id)
    
    result = await session.execute(
        select(StockLevel).where(and_(*filters))
    )
    stock_level = result.scalar()
    
    if not stock_level:
        # Create new stock level record
        stock_level = StockLevel(
            warehouse_id=warehouse_id,
            product_id=product_id,
            raw_material_id=raw_material_id,
            current_stock=0,
            reserved_stock=0,
            min_stock=0,
            max_stock=0
        )
        session.add(stock_level)
    
    # Calculate new stock based on movement type
    if movement_type in ["IN", "RETURN"]:
        stock_level.current_stock += quantity_change
    elif movement_type in ["OUT", "DAMAGE", "TRANSFER"]:
        if stock_level.current_stock < quantity_change:
            raise HTTPException(
                status_code=400, 
                detail=f"Insufficient stock. Available: {stock_level.current_stock}, Required: {quantity_change}"
            )
        stock_level.current_stock -= quantity_change
    
    return stock_level

@router.post('/movement/product', response_model=ApiResponse)
async def create_product_movement(
    movement_data: StockMovementCreateProduct,
    # TODO: Add current_user dependency for created_by
    session: AsyncSession = Depends(get_session)
):
    """Create a stock movement for a product"""
    # Verify product exists
    product_result = await session.execute(
        select(Product).where(Product.id == movement_data.product_id)
    )
    product = product_result.scalar()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Verify warehouse exists
    warehouse_result = await session.execute(
        select(Warehouse).where(and_(Warehouse.id == movement_data.warehouse_id, Warehouse.is_active == True))
    )
    warehouse = warehouse_result.scalar()
    if not warehouse:
        raise HTTPException(status_code=404, detail="Warehouse not found or inactive")
    
    # Create stock movement
    movement = StockMovement(
        **movement_data.model_dump(),
        created_by=None  # TODO: Set to current user ID
    )
    session.add(movement)
    
    # Update stock level
    await update_stock_level(
        session,
        movement_data.warehouse_id,
        product_id=movement_data.product_id,
        quantity_change=movement_data.quantity,
        movement_type=movement_data.movement_type
    )
    
    await session.commit()
    await session.refresh(movement)
    
    return ApiResponse(
        message=f"Stock movement for {product.name} recorded successfully",
        data=StockMovementSchema.model_validate(movement)
    )

@router.post('/movement/raw-material', response_model=ApiResponse)
async def create_raw_material_movement(
    movement_data: StockMovementCreateRawMaterial,
    session: AsyncSession = Depends(get_session)
):
    """Create a stock movement for a raw material"""
    # Verify raw material exists
    material_result = await session.execute(
        select(RawMaterial).where(RawMaterial.id == movement_data.raw_material_id)
    )
    material = material_result.scalar()
    if not material:
        raise HTTPException(status_code=404, detail="Raw material not found")
    
    # Verify warehouse exists
    warehouse_result = await session.execute(
        select(Warehouse).where(and_(Warehouse.id == movement_data.warehouse_id, Warehouse.is_active == True))
    )
    warehouse = warehouse_result.scalar()
    if not warehouse:
        raise HTTPException(status_code=404, detail="Warehouse not found or inactive")
    
    # Create stock movement
    movement = StockMovement(
        **movement_data.model_dump(),
        created_by=None  # TODO: Set to current user ID
    )
    session.add(movement)
    
    # Update stock level
    await update_stock_level(
        session,
        movement_data.warehouse_id,
        raw_material_id=movement_data.raw_material_id,
        quantity_change=movement_data.quantity,
        movement_type=movement_data.movement_type
    )
    
    await session.commit()
    await session.refresh(movement)
    
    return ApiResponse(
        message=f"Stock movement for {material.name} recorded successfully",
        data=StockMovementSchema.model_validate(movement)
    )

@router.get('/movements', response_model=PaginatedResponse)
async def list_stock_movements(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100),
    warehouse_id: Optional[uuid.UUID] = Query(None),
    movement_type: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_session)
):
    """List stock movements with pagination and filtering"""
    offset = (page - 1) * size
    
    query = select(StockMovement)
    count_query = select(func.count(StockMovement.id))
    
    filters = []
    if warehouse_id:
        filters.append(StockMovement.warehouse_id == warehouse_id)
    if movement_type:
        filters.append(StockMovement.movement_type == movement_type)
    
    if filters:
        query = query.where(and_(*filters))
        count_query = count_query.where(and_(*filters))
    
    # Get total count
    total_result = await session.execute(count_query)
    total = total_result.scalar()
    
    # Get paginated results
    query = query.offset(offset).limit(size).order_by(StockMovement.created_at.desc())
    result = await session.execute(query)
    movements = result.scalars().all()
    
    return PaginatedResponse(
        items=[StockMovementSchema.model_validate(m) for m in movements],
        total=total,
        page=page,
        size=size,
        pages=(total + size - 1) // size
    )

@router.get('/levels', response_model=List[dict])
async def get_stock_levels(
    warehouse_id: Optional[uuid.UUID] = Query(None),
    low_stock_only: bool = Query(False, description="Show only items with low stock"),
    session: AsyncSession = Depends(get_session)
):
    """Get current stock levels across warehouses"""
    query = """
    SELECT 
        sl.id,
        w.code as warehouse_code,
        w.name as warehouse_name,
        COALESCE(p.sku, rm.sku) as item_sku,
        COALESCE(p.name, rm.name) as item_name,
        CASE 
            WHEN p.id IS NOT NULL THEN 'product'
            ELSE 'raw_material'
        END as item_type,
        sl.current_stock,
        sl.reserved_stock,
        (sl.current_stock - sl.reserved_stock) as available_stock,
        sl.min_stock,
        sl.max_stock,
        CASE 
            WHEN sl.current_stock <= sl.min_stock THEN 'LOW'
            WHEN sl.current_stock >= sl.max_stock THEN 'HIGH'
            ELSE 'NORMAL'
        END as stock_status
    FROM stock_levels sl
    JOIN warehouses w ON sl.warehouse_id = w.id
    LEFT JOIN products p ON sl.product_id = p.id
    LEFT JOIN raw_materials rm ON sl.raw_material_id = rm.id
    WHERE w.is_active = true
    """
    
    params = {}
    if warehouse_id:
        query += " AND w.id = :warehouse_id"
        params["warehouse_id"] = warehouse_id
    
    if low_stock_only:
        query += " AND sl.current_stock <= sl.min_stock"
    
    query += " ORDER BY w.code, item_sku"
    
    result = await session.execute(text(query), params)
    
    stock_levels = []
    for row in result:
        stock_levels.append({
            "id": row.id,
            "warehouse": {
                "code": row.warehouse_code,
                "name": row.warehouse_name
            },
            "item": {
                "sku": row.item_sku,
                "name": row.item_name,
                "type": row.item_type
            },
            "current_stock": row.current_stock,
            "reserved_stock": row.reserved_stock,
            "available_stock": row.available_stock,
            "min_stock": row.min_stock,
            "max_stock": row.max_stock,
            "stock_status": row.stock_status
        })
    
    return stock_levels

@router.get('/valuation', response_model=dict)
async def get_inventory_valuation(
    warehouse_id: Optional[uuid.UUID] = Query(None),
    session: AsyncSession = Depends(get_session)
):
    """Get inventory valuation summary"""
    # Raw materials valuation
    rm_query = """
    SELECT 
        COUNT(*) as item_count,
        SUM(sl.current_stock * rm.unit_cost) as total_value,
        'raw_materials' as category
    FROM stock_levels sl
    JOIN raw_materials rm ON sl.raw_material_id = rm.id
    JOIN warehouses w ON sl.warehouse_id = w.id
    WHERE w.is_active = true AND sl.current_stock > 0
    """
    
    # Products valuation (simplified - using average raw material costs)
    prod_query = """
    SELECT 
        COUNT(*) as item_count,
        SUM(sl.current_stock * COALESCE(pc.unit_cost, 0)) as total_value,
        'products' as category
    FROM stock_levels sl
    JOIN products p ON sl.product_id = p.id
    JOIN warehouses w ON sl.warehouse_id = w.id
    LEFT JOIN product_costs pc ON p.id = pc.product_id
    WHERE w.is_active = true AND sl.current_stock > 0
    """
    
    params = {}
    if warehouse_id:
        rm_query += " AND w.id = :warehouse_id"
        prod_query += " AND w.id = :warehouse_id"
        params["warehouse_id"] = warehouse_id
    
    rm_result = await session.execute(text(rm_query), params)
    prod_result = await session.execute(text(prod_query), params)
    
    rm_data = rm_result.first()
    prod_data = prod_result.first()
    
    return {
        "raw_materials": {
            "item_count": rm_data.item_count or 0,
            "total_value": float(rm_data.total_value or 0)
        },
        "products": {
            "item_count": prod_data.item_count or 0,
            "total_value": float(prod_data.total_value or 0)
        },
        "grand_total": float((rm_data.total_value or 0) + (prod_data.total_value or 0))
    }

@router.post('/intake/', response_model=ApiResponse)
async def create_stock_intake(
    intake_data: StockIntakeCreate,
    session: AsyncSession = Depends(get_session)
):
    """Create a stock intake (incoming stock) for a product"""
    # Verify product exists
    product_result = await session.execute(
        select(Product).where(Product.id == intake_data.product_id)
    )
    product = product_result.scalar()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Verify warehouse exists
    warehouse_result = await session.execute(
        select(Warehouse).where(and_(Warehouse.id == intake_data.warehouse_id, Warehouse.is_active == True))
    )
    warehouse = warehouse_result.scalar()
    if not warehouse:
        raise HTTPException(status_code=404, detail="Warehouse not found or inactive")
    
    # Create stock movement for the intake
    movement = StockMovement(
        product_id=intake_data.product_id,
        warehouse_id=intake_data.warehouse_id,
        movement_type="IN",
        quantity=intake_data.quantity,
        unit_cost=intake_data.unit_cost or product.unit_price,
        reference=f"INTAKE-{uuid.uuid4().hex[:8].upper()}",
        notes=f"Stock Intake - Supplier: {intake_data.supplier or 'N/A'}, Batch: {intake_data.batch_number or 'N/A'}, Notes: {intake_data.notes or 'N/A'}",
        created_by=None  # TODO: Set to current user ID
    )
    session.add(movement)
    
    # Update stock level
    await update_stock_level(
        session,
        intake_data.warehouse_id,
        product_id=intake_data.product_id,
        quantity_change=intake_data.quantity,
        movement_type="IN"
    )
    
    await session.commit()
    await session.refresh(movement)
    
    return ApiResponse(
        message=f"Stock intake for {product.name} completed successfully. Added {intake_data.quantity} units.",
        data={
            "movement_id": movement.id,
            "product_name": product.name,
            "warehouse_name": warehouse.name,
            "quantity_added": intake_data.quantity,
            "reference_number": movement.reference
        }
    )

@router.post('/intake/raw-material/', response_model=ApiResponse)
async def create_raw_material_stock_intake(
    intake_data: RawMaterialStockIntakeCreate,
    session: AsyncSession = Depends(get_session)
):
    """Create a stock intake (incoming stock) for a raw material"""
    # Verify raw material exists
    raw_material_result = await session.execute(
        select(RawMaterial).where(RawMaterial.id == intake_data.raw_material_id)
    )
    raw_material = raw_material_result.scalar()
    if not raw_material:
        raise HTTPException(status_code=404, detail="Raw material not found")
    
    # Verify warehouse exists
    warehouse_result = await session.execute(
        select(Warehouse).where(and_(Warehouse.id == intake_data.warehouse_id, Warehouse.is_active == True))
    )
    warehouse = warehouse_result.scalar()
    if not warehouse:
        raise HTTPException(status_code=404, detail="Warehouse not found or inactive")
    
    # Create stock movement for the intake
    movement = StockMovement(
        raw_material_id=intake_data.raw_material_id,
        warehouse_id=intake_data.warehouse_id,
        movement_type="IN",
        quantity=intake_data.quantity,
        unit_cost=intake_data.unit_cost or raw_material.unit_cost,
        reference=f"RM-INTAKE-{uuid.uuid4().hex[:8].upper()}",
        notes=f"Raw Material Stock Intake - Supplier: {intake_data.supplier or 'N/A'}, Batch: {intake_data.batch_number or 'N/A'}, Notes: {intake_data.notes or 'N/A'}",
        created_by=None  # TODO: Set to current user ID
    )
    session.add(movement)
    
    # Update stock level
    await update_stock_level(
        session,
        intake_data.warehouse_id,
        raw_material_id=intake_data.raw_material_id,
        quantity_change=intake_data.quantity,
        movement_type="IN"
    )
    
    await session.commit()
    await session.refresh(movement)
    
    return ApiResponse(
        message=f"Raw material stock intake for {raw_material.name} completed successfully. Added {intake_data.quantity} units.",
        data={
            "movement_id": movement.id,
            "raw_material_name": raw_material.name,
            "warehouse_name": warehouse.name,
            "quantity_added": intake_data.quantity,
            "reference_number": movement.reference
        }
    )
