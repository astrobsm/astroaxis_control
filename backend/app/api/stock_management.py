from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, and_, or_, case, text
from typing import Optional, List
from uuid import UUID
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal
from pydantic import BaseModel

from app.db import get_session
from app.models import (
    StockLevel, StockMovement, DamagedStock, ReturnedStock,
    Product, RawMaterial, Warehouse, ProductPricing
)

router = APIRouter(prefix='/api/stock-management')

# Pydantic schemas
class ProductIntakeRequest(BaseModel):
    warehouse_id: UUID
    product_id: UUID
    quantity: float
    unit_cost: float
    supplier: Optional[str] = None
    batch_number: Optional[str] = None
    notes: Optional[str] = None

class RawMaterialIntakeRequest(BaseModel):
    warehouse_id: UUID
    raw_material_id: UUID
    quantity: float
    unit_cost: float
    supplier: Optional[str] = None
    batch_number: Optional[str] = None
    notes: Optional[str] = None


# Transfer Request Schema
class TransferRequest(BaseModel):
    product_id: UUID
    from_warehouse_id: UUID
    to_warehouse_id: UUID
    quantity: float
    unit: Optional[str] = None
    reference: Optional[str] = None
    notes: Optional[str] = None

# Product Stock Intake
@router.post('/product-intake')
async def product_stock_intake(
    request: ProductIntakeRequest,
    session: AsyncSession = Depends(get_session)
):
    """Record product stock intake"""
    try:
        # Create stock movement
        movement = StockMovement(
            warehouse_id=request.warehouse_id,
            product_id=request.product_id,
            movement_type='IN',
            quantity=Decimal(str(request.quantity)),
            unit_cost=Decimal(str(request.unit_cost)),
            reference=f"Supplier: {request.supplier or 'N/A'}" + (f", Batch: {request.batch_number}" if request.batch_number else ""),
            notes=request.notes
        )
        session.add(movement)
        
        # Update or create stock level
        stock_level_result = await session.execute(
            select(StockLevel).where(
                and_(StockLevel.warehouse_id == request.warehouse_id, StockLevel.product_id == request.product_id)
            )
        )
        stock_level = stock_level_result.scalars().first()
        
        if stock_level:
            stock_level.current_stock += Decimal(str(request.quantity))
            stock_level.updated_at = datetime.now(timezone.utc)
        else:
            stock_level = StockLevel(
                warehouse_id=request.warehouse_id,
                product_id=request.product_id,
                current_stock=Decimal(str(request.quantity))
            )
            session.add(stock_level)
        
        await session.commit()
        await session.refresh(movement)
        
        return {
            "success": True,
            "message": f"Stock intake recorded: {request.quantity} units added",
            "movement_id": str(movement.id),
            "new_stock_level": float(stock_level.current_stock)
        }
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error recording stock intake: {str(e)}")


# Raw Material Stock Intake
@router.post('/raw-material-intake')
async def raw_material_stock_intake(
    request: RawMaterialIntakeRequest,
    session: AsyncSession = Depends(get_session)
):
    """Record raw material stock intake"""
    try:
        # Create stock movement
        movement = StockMovement(
            warehouse_id=request.warehouse_id,
            raw_material_id=request.raw_material_id,
            movement_type='IN',
            quantity=Decimal(str(request.quantity)),
            unit_cost=Decimal(str(request.unit_cost)),
            reference=f"Supplier: {request.supplier or 'N/A'}" + (f", Batch: {request.batch_number}" if request.batch_number else ""),
            notes=request.notes
        )
        session.add(movement)
        
        # Update or create stock level
        stock_level_result = await session.execute(
            select(StockLevel).where(
                and_(StockLevel.warehouse_id == request.warehouse_id, StockLevel.raw_material_id == request.raw_material_id)
            )
        )
        stock_level = stock_level_result.scalars().first()
        
        if stock_level:
            stock_level.current_stock += Decimal(str(request.quantity))
            stock_level.updated_at = datetime.now(timezone.utc)
        else:
            stock_level = StockLevel(
                warehouse_id=request.warehouse_id,
                raw_material_id=request.raw_material_id,
                current_stock=Decimal(str(request.quantity))
            )
            session.add(stock_level)
        
        await session.commit()
        await session.refresh(movement)
        
        return {
            "success": True,
            "message": f"Raw material intake recorded: {request.quantity} units added",
            "movement_id": str(movement.id),
            "new_stock_level": float(stock_level.current_stock)
        }
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error recording raw material intake: {str(e)}")


# Get Product Stock Levels
@router.get('/product-levels')
async def get_product_stock_levels(
    warehouse_id: Optional[str] = None,
    low_stock_only: bool = False,
    session: AsyncSession = Depends(get_session)
):
    """Get current product stock levels (raw SQL for production schema compatibility)"""
    try:
        sql = """
            SELECT sl.id, sl.warehouse_id, sl.product_id, sl.current_stock,
                   COALESCE(sl.min_stock, 0) as min_stock,
                   p.name as product_name, p.sku as product_sku,
                   COALESCE(NULLIF(sl.min_stock, 0), 10) as reorder_level,
                   sl.updated_at,
                   w.name as warehouse_name
            FROM stock_levels sl
            LEFT JOIN products p ON sl.product_id = p.id::text
            LEFT JOIN warehouses w ON sl.warehouse_id::text = w.id::text
            WHERE sl.product_id IS NOT NULL
        """
        params = {}
        if warehouse_id:
            sql += " AND sl.warehouse_id::text = :wh_id"
            params['wh_id'] = str(warehouse_id)
        
        result = await session.execute(text(sql), params)
        rows = result.fetchall()
        
        stock_levels = []
        for row in rows:
            current_stock = float(row.current_stock or 0)
            reorder_level = float(row.reorder_level or 10)
            is_low_stock = current_stock <= reorder_level
            
            if low_stock_only and not is_low_stock:
                continue
            
            stock_levels.append({
                'stock_level_id': str(row.id),
                'warehouse_id': str(row.warehouse_id or ''),
                'warehouse_name': row.warehouse_name or 'Default',
                'product_id': str(row.product_id),
                'product_name': row.product_name or 'Unknown',
                'product_sku': row.product_sku or '',
                'current_stock': current_stock,
                'reserved_stock': 0,
                'available_stock': current_stock,
                'reorder_level': reorder_level,
                'is_low_stock': is_low_stock,
                'updated_at': row.updated_at.isoformat() if row.updated_at else None
            })
        
        return stock_levels
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching product stock levels: {str(e)}")


# Get Raw Material Stock Levels
@router.get('/raw-material-levels')
async def get_raw_material_stock_levels(
    warehouse_id: Optional[str] = None,
    low_stock_only: bool = False,
    session: AsyncSession = Depends(get_session)
):
    """Get current raw material stock levels (raw SQL for production schema compatibility)"""
    try:
        sql = """
            SELECT sl.id, sl.warehouse_id, sl.raw_material_id, sl.current_stock,
                   COALESCE(sl.min_stock, 0) as min_stock,
                   rm.name as rm_name, COALESCE(rm.rm_id, rm.name) as rm_sku,
                   COALESCE(rm.reorder_point, 10) as reorder_level,
                   COALESCE(rm.uom, 'kg') as unit,
                   sl.updated_at,
                   w.name as warehouse_name
            FROM stock_levels sl
            LEFT JOIN raw_materials rm ON sl.raw_material_id = rm.id
            LEFT JOIN warehouses w ON sl.warehouse_id::text = w.id::text
            WHERE sl.raw_material_id IS NOT NULL
        """
        params = {}
        if warehouse_id:
            sql += " AND sl.warehouse_id::text = :wh_id"
            params['wh_id'] = str(warehouse_id)
        
        result = await session.execute(text(sql), params)
        rows = result.fetchall()
        
        stock_levels = []
        for row in rows:
            current_stock = float(row.current_stock or 0)
            reorder_level = float(row.reorder_level or 10)
            is_low_stock = current_stock <= reorder_level
            
            if low_stock_only and not is_low_stock:
                continue
            
            stock_levels.append({
                'stock_level_id': str(row.id),
                'warehouse_id': str(row.warehouse_id or ''),
                'warehouse_name': row.warehouse_name or 'Default',
                'raw_material_id': str(row.raw_material_id),
                'raw_material_name': row.rm_name or 'Unknown',
                'raw_material_sku': row.rm_sku or '',
                'current_stock': current_stock,
                'reserved_stock': 0,
                'available_stock': current_stock,
                'reorder_level': reorder_level,
                'is_low_stock': is_low_stock,
                'unit': row.unit or 'kg',
                'updated_at': row.updated_at.isoformat() if row.updated_at else None
            })
        
        return stock_levels
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching raw material stock levels: {str(e)}")


# Record Damaged Product
@router.post('/damaged-product')
async def record_damaged_product(
    warehouse_id: UUID,
    product_id: UUID,
    quantity: float,
    damage_type: str,
    damage_reason: Optional[str] = None,
    notes: Optional[str] = None,
    session: AsyncSession = Depends(get_session)
):
    """Record damaged product"""
    try:
        # Create damaged stock record
        damaged = DamagedStock(
            warehouse_id=warehouse_id,
            product_id=product_id,
            quantity=Decimal(str(quantity)),
            damage_type=damage_type,
            damage_reason=damage_reason,
            notes=notes
        )
        session.add(damaged)
        
        # Create stock movement (OUT)
        movement = StockMovement(
            warehouse_id=warehouse_id,
            product_id=product_id,
            movement_type='DAMAGE',
            quantity=Decimal(str(quantity)),
            reference=f"Damage: {damage_type}",
            notes=damage_reason
        )
        session.add(movement)
        
        # Update stock level
        stock_level_result = await session.execute(
            select(StockLevel).where(
                and_(StockLevel.warehouse_id == warehouse_id, StockLevel.product_id == product_id)
            )
        )
        stock_level = stock_level_result.scalars().first()
        
        if stock_level:
            stock_level.current_stock -= Decimal(str(quantity))
            stock_level.updated_at = datetime.now(timezone.utc)
        
        await session.commit()
        await session.refresh(damaged)
        
        return {
            "success": True,
            "message": f"Damaged product recorded: {quantity} units",
            "damaged_id": str(damaged.id),
            "remaining_stock": float(stock_level.current_stock) if stock_level else 0
        }
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error recording damaged product: {str(e)}")


# Record Damaged Raw Material
@router.post('/damaged-raw-material')
async def record_damaged_raw_material(
    warehouse_id: UUID,
    raw_material_id: UUID,
    quantity: float,
    damage_type: str,
    damage_reason: Optional[str] = None,
    notes: Optional[str] = None,
    session: AsyncSession = Depends(get_session)
):
    """Record damaged raw material"""
    try:
        # Create damaged stock record
        damaged = DamagedStock(
            warehouse_id=warehouse_id,
            raw_material_id=raw_material_id,
            quantity=Decimal(str(quantity)),
            damage_type=damage_type,
            damage_reason=damage_reason,
            notes=notes
        )
        session.add(damaged)
        
        # Create stock movement (DAMAGE)
        movement = StockMovement(
            warehouse_id=warehouse_id,
            raw_material_id=raw_material_id,
            movement_type='DAMAGE',
            quantity=Decimal(str(quantity)),
            reference=f"Damage: {damage_type}",
            notes=damage_reason
        )
        session.add(movement)
        
        # Update stock level
        stock_level_result = await session.execute(
            select(StockLevel).where(
                and_(StockLevel.warehouse_id == warehouse_id, StockLevel.raw_material_id == raw_material_id)
            )
        )
        stock_level = stock_level_result.scalars().first()
        
        if stock_level:
            stock_level.current_stock -= Decimal(str(quantity))
            stock_level.updated_at = datetime.now(timezone.utc)
        
        await session.commit()
        await session.refresh(damaged)
        
        return {
            "success": True,
            "message": f"Damaged raw material recorded: {quantity} units",
            "damaged_id": str(damaged.id),
            "remaining_stock": float(stock_level.current_stock) if stock_level else 0
        }
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error recording damaged raw material: {str(e)}")


# Record Returned Product
@router.post('/returned-product')
async def record_returned_product(
    warehouse_id: UUID,
    product_id: UUID,
    quantity: float,
    return_reason: str,
    return_condition: str,  # good, damaged, expired
    customer_name: Optional[str] = None,
    refund_amount: Optional[float] = 0,
    notes: Optional[str] = None,
    session: AsyncSession = Depends(get_session)
):
    """Record product return from customer"""
    try:
        # Create returned stock record
        returned = ReturnedStock(
            warehouse_id=warehouse_id,
            product_id=product_id,
            quantity=Decimal(str(quantity)),
            return_reason=return_reason,
            return_condition=return_condition,
            refund_amount=Decimal(str(refund_amount)),
            notes=f"Customer: {customer_name or 'N/A'}\n{notes or ''}"
        )
        session.add(returned)
        
        # Create stock movement (RETURN)
        movement = StockMovement(
            warehouse_id=warehouse_id,
            product_id=product_id,
            movement_type='RETURN',
            quantity=Decimal(str(quantity)),
            reference=f"Return: {return_reason}",
            notes=f"Condition: {return_condition}"
        )
        session.add(movement)
        
        # Update stock level only if condition is good
        if return_condition == 'good':
            stock_level_result = await session.execute(
                select(StockLevel).where(
                    and_(StockLevel.warehouse_id == warehouse_id, StockLevel.product_id == product_id)
                )
            )
            stock_level = stock_level_result.scalars().first()
            
            if stock_level:
                stock_level.current_stock += Decimal(str(quantity))
                stock_level.updated_at = datetime.now(timezone.utc)
            else:
                stock_level = StockLevel(
                    warehouse_id=warehouse_id,
                    product_id=product_id,
                    current_stock=Decimal(str(quantity))
                )
                session.add(stock_level)
        
        await session.commit()
        await session.refresh(returned)
        
        return {
            "success": True,
            "message": f"Product return recorded: {quantity} units ({return_condition})",
            "returned_id": str(returned.id),
            "restocked": return_condition == 'good'
        }
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error recording product return: {str(e)}")


# Transfer product between warehouses
@router.post('/transfer')
async def transfer_product(
    request: TransferRequest,
    session: AsyncSession = Depends(get_session)
):
    """Transfer product quantity from one warehouse to another atomically"""
    if request.from_warehouse_id == request.to_warehouse_id:
        raise HTTPException(status_code=400, detail="Source and destination warehouse must differ")

    if request.quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be greater than zero")

    try:
        # Fetch source stock level
        src_result = await session.execute(
            select(StockLevel).where(
                and_(StockLevel.warehouse_id == request.from_warehouse_id, StockLevel.product_id == request.product_id)
            )
        )
        src_level = src_result.scalars().first()

        if not src_level or src_level.current_stock < Decimal(str(request.quantity)):
            raise HTTPException(status_code=400, detail="Insufficient stock in source warehouse")

        # Fetch destination stock level
        dst_result = await session.execute(
            select(StockLevel).where(
                and_(StockLevel.warehouse_id == request.to_warehouse_id, StockLevel.product_id == request.product_id)
            )
        )
        dst_level = dst_result.scalars().first()

        # Perform updates
        src_level.current_stock -= Decimal(str(request.quantity))
        src_level.updated_at = datetime.now(timezone.utc)

        if dst_level:
            dst_level.current_stock += Decimal(str(request.quantity))
            dst_level.updated_at = datetime.now(timezone.utc)
        else:
            dst_level = StockLevel(
                warehouse_id=request.to_warehouse_id,
                product_id=request.product_id,
                current_stock=Decimal(str(request.quantity))
            )
            session.add(dst_level)

        # Record stock movements
        out_movement = StockMovement(
            warehouse_id=request.from_warehouse_id,
            product_id=request.product_id,
            movement_type='TRANSFER_OUT',
            quantity=Decimal(str(request.quantity)),
            reference=request.reference or f"Transfer to {request.to_warehouse_id}",
            notes=request.notes
        )
        in_movement = StockMovement(
            warehouse_id=request.to_warehouse_id,
            product_id=request.product_id,
            movement_type='TRANSFER_IN',
            quantity=Decimal(str(request.quantity)),
            reference=request.reference or f"Transfer from {request.from_warehouse_id}",
            notes=request.notes
        )
        session.add(out_movement)
        session.add(in_movement)

        await session.commit()
        # refresh levels for response
        await session.refresh(src_level)
        await session.refresh(dst_level)

        return {
            "success": True,
            "message": f"Transferred {request.quantity} units",
            "from_warehouse_new_level": float(src_level.current_stock),
            "to_warehouse_new_level": float(dst_level.current_stock),
            "out_movement_id": str(out_movement.id),
            "in_movement_id": str(in_movement.id)
        }
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error transferring product: {str(e)}")


# Stock Analysis & Dashboard
@router.get('/analysis')
async def get_stock_analysis(session: AsyncSession = Depends(get_session)):
    """Get comprehensive stock analysis and statistics (raw SQL for production schema compatibility)"""
    try:
        # Total products in stock
        r = await session.execute(text("SELECT COUNT(*) FROM stock_levels WHERE product_id IS NOT NULL"))
        total_product_items = r.scalar_one()
        
        # Total raw materials in stock
        r = await session.execute(text("SELECT COUNT(*) FROM stock_levels WHERE raw_material_id IS NOT NULL"))
        total_raw_material_items = r.scalar_one()
        
        # Low stock products (use stock_levels.min_stock as reorder level, default 10)
        r = await session.execute(text("""
            SELECT COUNT(*) FROM stock_levels sl
            WHERE sl.product_id IS NOT NULL
              AND sl.current_stock <= COALESCE(NULLIF(sl.min_stock, 0), 10)
        """))
        low_stock_products = r.scalar_one()
        
        # Low stock raw materials
        r = await session.execute(text("""
            SELECT COUNT(*) FROM stock_levels sl
            LEFT JOIN raw_materials rm ON sl.raw_material_id = rm.id
            WHERE sl.raw_material_id IS NOT NULL
              AND sl.current_stock <= COALESCE(rm.reorder_point, 10)
        """))
        low_stock_raw_materials = r.scalar_one()
        
        # Damaged items (last 30 days)
        r = await session.execute(text("""
            SELECT COUNT(*) FROM damaged_stock
            WHERE damage_date >= CURRENT_DATE - INTERVAL '30 days'
        """))
        damaged_items_count = r.scalar_one()
        
        # Returned items (last 30 days)
        r = await session.execute(text("""
            SELECT COUNT(*) FROM returned_stock
            WHERE return_date >= CURRENT_DATE - INTERVAL '30 days'
        """))
        returned_items_count = r.scalar_one()
        
        # Total stock value (products) - use product_pricing cost_price
        r = await session.execute(text("""
            SELECT COALESCE(SUM(sl.current_stock * pp.cost_price), 0)
            FROM stock_levels sl
            JOIN product_pricing pp ON sl.product_id = pp.product_id
            WHERE sl.product_id IS NOT NULL
        """))
        total_product_value = float(r.scalar_one() or 0)
        
        # Fallback: if no product_pricing data, use products.price
        if total_product_value == 0:
            r = await session.execute(text("""
                SELECT COALESCE(SUM(sl.current_stock * COALESCE(p.price, 0)), 0)
                FROM stock_levels sl
                LEFT JOIN products p ON sl.product_id = p.id::text
                WHERE sl.product_id IS NOT NULL
            """))
            total_product_value = float(r.scalar_one() or 0)
        
        # Total stock value (raw materials)
        r = await session.execute(text("""
            SELECT COALESCE(SUM(sl.current_stock * COALESCE(rm.unit_cost, 0)), 0)
            FROM stock_levels sl
            LEFT JOIN raw_materials rm ON sl.raw_material_id = rm.id
            WHERE sl.raw_material_id IS NOT NULL
        """))
        total_raw_material_value = float(r.scalar_one() or 0)
        
        return {
            "summary": {
                "total_product_items": total_product_items,
                "total_raw_material_items": total_raw_material_items,
                "low_stock_products": low_stock_products,
                "low_stock_raw_materials": low_stock_raw_materials,
                "damaged_items_30_days": damaged_items_count,
                "returned_items_30_days": returned_items_count,
                "total_product_value": total_product_value,
                "total_raw_material_value": total_raw_material_value,
                "total_stock_value": total_product_value + total_raw_material_value
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating stock analysis: {str(e)}")
