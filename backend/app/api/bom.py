from fastapi import APIRouter, Depends, HTTPException
from decimal import Decimal, getcontext
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import and_, func
from typing import List, Optional
from uuid import UUID
import uuid
from datetime import datetime, timezone
from pydantic import BaseModel

from app.db import get_session
from app.models import (
    BOM, BOMLine, RawMaterial, ProductCost, Product, 
    StockLevel, StockMovement, Warehouse
)

# set decimal precision high to avoid rounding issues
getcontext().prec = 28

router = APIRouter(prefix='/api/bom')

class ApproveProductionRequest(BaseModel):
    product_id: str
    quantity: float
    warehouse_id: str
    notes: Optional[str] = None

# Pydantic schemas
class BOMLineCreate(BaseModel):
    raw_material_id: str
    qty_per_unit: float
    unit: Optional[str] = None

class BOMCreateRequest(BaseModel):
    product_id: str
    lines: List[BOMLineCreate]

# ==================== BOM MANAGEMENT ====================

@router.post('/create')
async def create_bom(
    request: BOMCreateRequest,
    session: AsyncSession = Depends(get_session)
):
    """
    Create a Bill of Materials for a product
    lines format: [{"raw_material_id": "uuid", "qty_per_unit": 2.5, "unit": "kg"}, ...]
    """
    try:
        # Verify product exists
        product_result = await session.execute(select(Product).where(Product.id == UUID(request.product_id)))
        product = product_result.scalars().first()
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
        
        # Check if BOM already exists for this product
        existing_bom = await session.execute(select(BOM).where(BOM.product_id == UUID(request.product_id)))
        bom = existing_bom.scalars().first()
        
        if bom:
            # Delete existing BOM lines
            await session.execute(select(BOMLine).where(BOMLine.bom_id == bom.id))
            # We'll update the existing BOM
        else:
            # Create new BOM
            bom = BOM(
                id=uuid.uuid4(),
                product_id=UUID(request.product_id)
            )
            session.add(bom)
            await session.flush()
        
        # Create BOM lines
        for line_data in request.lines:
            # Verify raw material exists
            rm_result = await session.execute(
                select(RawMaterial).where(RawMaterial.id == UUID(line_data.raw_material_id))
            )
            raw_material = rm_result.scalars().first()
            if not raw_material:
                raise HTTPException(status_code=404, detail=f"Raw material {line_data.raw_material_id} not found")
            
            bom_line = BOMLine(
                id=uuid.uuid4(),
                bom_id=bom.id,
                raw_material_id=UUID(line_data.raw_material_id),
                qty_per_unit=Decimal(str(line_data.qty_per_unit))
            )
            session.add(bom_line)
        
        await session.commit()
        await session.refresh(bom)
        
        return {
            "success": True,
            "message": f"BOM created for {product.name}",
            "bom_id": str(bom.id),
            "product_id": str(product.id),
            "lines_count": len(request.lines)
        }
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        import traceback
        error_detail = f"Error creating BOM: {str(e)}\n{traceback.format_exc()}"
        print(f"BOM CREATE ERROR: {error_detail}")
        raise HTTPException(status_code=400, detail=f"Error creating BOM: {str(e)}")


@router.get('/product/{product_id}')
async def get_product_bom(
    product_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Get BOM for a specific product"""
    try:
        # Get BOM
        bom_result = await session.execute(
            select(BOM).where(BOM.product_id == UUID(product_id))
        )
        bom = bom_result.scalars().first()
        
        if not bom:
            return {
                "has_bom": False,
                "message": "No BOM defined for this product"
            }
        
        # Get BOM lines with raw material details
        lines_result = await session.execute(
            select(BOMLine, RawMaterial)
            .join(RawMaterial, BOMLine.raw_material_id == RawMaterial.id)
            .where(BOMLine.bom_id == bom.id)
        )
        
        bom_lines = []
        for line, raw_material in lines_result:
            bom_lines.append({
                "id": str(line.id),
                "raw_material_id": str(raw_material.id),
                "raw_material_name": raw_material.name,
                "raw_material_sku": raw_material.sku,
                "qty_per_unit": float(line.qty_per_unit),
                "unit": raw_material.unit,
                "unit_cost": float(raw_material.unit_cost),
                "line_cost": float(line.qty_per_unit * raw_material.unit_cost)
            })
        
        # Get product details
        product_result = await session.execute(select(Product).where(Product.id == UUID(product_id)))
        product = product_result.scalars().first()
        
        total_material_cost = sum(line["line_cost"] for line in bom_lines)
        
        return {
            "has_bom": True,
            "bom_id": str(bom.id),
            "product_id": str(product.id),
            "product_name": product.name,
            "product_sku": product.sku,
            "lines": bom_lines,
            "total_material_cost": total_material_cost,
            "lines_count": len(bom_lines)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching BOM: {str(e)}")


@router.get('/calculate-requirements')
async def calculate_production_requirements(
    product_id: str,
    quantity: float,
    warehouse_id: Optional[str] = None,
    session: AsyncSession = Depends(get_session)
):
    """
    Calculate raw material requirements for producing a given quantity of product
    """
    try:
        # Get BOM
        bom_result = await session.execute(
            select(BOM).where(BOM.product_id == UUID(product_id))
        )
        bom = bom_result.scalars().first()
        
        if not bom:
            raise HTTPException(status_code=404, detail="No BOM defined for this product")
        
        # Get BOM lines with raw material details
        lines_result = await session.execute(
            select(BOMLine, RawMaterial)
            .join(RawMaterial, BOMLine.raw_material_id == RawMaterial.id)
            .where(BOMLine.bom_id == bom.id)
        )
        
        requirements = []
        total_cost = Decimal('0')
        can_produce = True
        shortages = []
        
        for line, raw_material in lines_result:
            required_qty = Decimal(str(quantity)) * line.qty_per_unit
            line_cost = required_qty * raw_material.unit_cost
            total_cost += line_cost
            
            # Check stock availability if warehouse specified
            available_stock = None
            if warehouse_id:
                stock_result = await session.execute(
                    select(StockLevel).where(and_(
                        StockLevel.warehouse_id == UUID(warehouse_id),
                        StockLevel.raw_material_id == raw_material.id
                    ))
                )
                stock_level = stock_result.scalars().first()
                if stock_level:
                    available_stock = float(stock_level.current_stock - (stock_level.reserved_stock or 0))
                else:
                    available_stock = 0.0
                
                # Check if we have enough stock
                if available_stock < float(required_qty):
                    can_produce = False
                    shortages.append({
                        "raw_material_name": raw_material.name,
                        "required": float(required_qty),
                        "available": available_stock,
                        "shortage": float(required_qty) - available_stock
                    })
            
            requirements.append({
                "raw_material_id": str(raw_material.id),
                "raw_material_name": raw_material.name,
                "raw_material_sku": raw_material.sku,
                "qty_per_unit": float(line.qty_per_unit),
                "required_quantity": float(required_qty),
                "unit": raw_material.unit,
                "unit_cost": float(raw_material.unit_cost),
                "line_cost": float(line_cost),
                "available_stock": available_stock,
                "sufficient_stock": available_stock >= float(required_qty) if available_stock is not None else None
            })
        
        # Get product details
        product_result = await session.execute(select(Product).where(Product.id == UUID(product_id)))
        product = product_result.scalars().first()
        
        return {
            "product_id": str(product.id),
            "product_name": product.name,
            "product_sku": product.sku,
            "quantity_to_produce": quantity,
            "requirements": requirements,
            "total_material_cost": float(total_cost),
            "cost_per_unit": float(total_cost / Decimal(str(quantity))),
            "can_produce": can_produce,
            "shortages": shortages,
            "warehouse_id": warehouse_id
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        error_detail = f"Error calculating requirements: {str(e)}\n{traceback.format_exc()}"
        print(f"CALCULATE REQUIREMENTS ERROR: {error_detail}")
        raise HTTPException(status_code=500, detail=f"Error calculating requirements: {str(e)}")


@router.post('/approve-production')
async def approve_production_and_deduct_stock(
    request: ApproveProductionRequest,
    session: AsyncSession = Depends(get_session)
):
    """
    Approve production and deduct raw materials from stock
    This will create stock movements and update stock levels
    """
    try:
        # Get BOM
        bom_result = await session.execute(
            select(BOM).where(BOM.product_id == UUID(request.product_id))
        )
        bom = bom_result.scalars().first()
        
        if not bom:
            raise HTTPException(status_code=404, detail="No BOM defined for this product")
        
        # Verify warehouse exists
        warehouse_result = await session.execute(select(Warehouse).where(Warehouse.id == UUID(request.warehouse_id)))
        warehouse = warehouse_result.scalars().first()
        if not warehouse:
            raise HTTPException(status_code=404, detail="Warehouse not found")
        
        # Get BOM lines
        lines_result = await session.execute(
            select(BOMLine, RawMaterial)
            .join(RawMaterial, BOMLine.raw_material_id == RawMaterial.id)
            .where(BOMLine.bom_id == bom.id)
        )
        
        deductions = []
        shortages = []
        
        # First pass: Check if we have enough stock for all materials
        for line, raw_material in lines_result:
            required_qty = Decimal(str(request.quantity)) * line.qty_per_unit
            
            # Check stock availability
            stock_result = await session.execute(
                select(StockLevel).where(and_(
                    StockLevel.warehouse_id == UUID(request.warehouse_id),
                    StockLevel.raw_material_id == raw_material.id
                ))
            )
            stock_level = stock_result.scalars().first()
            
            if not stock_level or (stock_level.current_stock - (stock_level.reserved_stock or 0)) < required_qty:
                available = float(stock_level.current_stock - (stock_level.reserved_stock or 0)) if stock_level else 0.0
                shortages.append({
                    "raw_material_name": raw_material.name,
                    "required": float(required_qty),
                    "available": available,
                    "shortage": float(required_qty) - available
                })
        
        # If there are shortages, return error
        if shortages:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "Insufficient stock for production",
                    "shortages": shortages
                }
            )
        
        # Second pass: Deduct from stock and create movements
        lines_result = await session.execute(
            select(BOMLine, RawMaterial)
            .join(RawMaterial, BOMLine.raw_material_id == RawMaterial.id)
            .where(BOMLine.bom_id == bom.id)
        )
        
        for line, raw_material in lines_result:
            required_qty = Decimal(str(request.quantity)) * line.qty_per_unit
            
            # Get stock level
            stock_result = await session.execute(
                select(StockLevel).where(and_(
                    StockLevel.warehouse_id == UUID(request.warehouse_id),
                    StockLevel.raw_material_id == raw_material.id
                ))
            )
            stock_level = stock_result.scalars().first()
            
            # Deduct from stock
            stock_level.current_stock -= required_qty
            stock_level.updated_at = datetime.now(timezone.utc)
            
            # Create stock movement record
            movement = StockMovement(
                id=uuid.uuid4(),
                warehouse_id=UUID(request.warehouse_id),
                raw_material_id=raw_material.id,
                movement_type='PRODUCTION',
                quantity=required_qty,
                unit_cost=raw_material.unit_cost,
                reference=f"Production: {request.quantity} units of product {request.product_id}",
                notes=request.notes or f"Raw material consumed for production"
            )
            session.add(movement)
            
            deductions.append({
                "raw_material_name": raw_material.name,
                "quantity_deducted": float(required_qty),
                "unit": line.unit or raw_material.unit,
                "remaining_stock": float(stock_level.current_stock)
            })
        
        # Get product details
        product_result = await session.execute(select(Product).where(Product.id == UUID(request.product_id)))
        product = product_result.scalars().first()
        
        await session.commit()
        
        return {
            "success": True,
            "message": f"Production approved and raw materials deducted for {request.quantity} units of {product.name}",
            "product_id": str(product.id),
            "product_name": product.name,
            "quantity_produced": quantity,
            "warehouse_id": warehouse_id,
            "deductions": deductions
        }
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Error approving production: {str(e)}")


@router.get('/products-with-bom')
async def list_products_with_bom(session: AsyncSession = Depends(get_session)):
    """List all products that have a BOM defined"""
    try:
        result = await session.execute(
            select(Product, BOM)
            .join(BOM, Product.id == BOM.product_id)
        )
        
        products = []
        for product, bom in result:
            # Count BOM lines
            lines_count_result = await session.execute(
                select(func.count(BOMLine.id)).where(BOMLine.bom_id == bom.id)
            )
            lines_count = lines_count_result.scalar_one()
            
            products.append({
                "product_id": str(product.id),
                "product_name": product.name,
                "product_sku": product.sku,
                "bom_id": str(bom.id),
                "bom_version": bom.version,
                "raw_materials_count": lines_count
            })
        
        return products
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching products: {str(e)}")


@router.get('/{bom_id}/cost')
async def compute_bom_cost(bom_id: str, session: AsyncSession = Depends(get_session)):
    # Pure computation: fetch BOM lines and raw material costs, compute cost per unit deterministically
    q = await session.execute(select(BOM).where(BOM.id==UUID(bom_id)))
    bom = q.scalars().first()
    if not bom:
        raise HTTPException(status_code=404, detail='BOM not found')
    q2 = await session.execute(select(BOMLine).where(BOMLine.bom_id==bom.id))
    lines = q2.scalars().all()
    total_material_cost = Decimal('0')
    for line in lines:
        qrm = await session.execute(select(RawMaterial).where(RawMaterial.id==line.raw_material_id))
        rm = qrm.scalars().first()
        if not rm:
            raise HTTPException(status_code=400, detail=f'Raw material {line.raw_material_id} missing')
        qty = Decimal(line.qty_per_unit)
        unit_cost = Decimal(rm.unit_cost)
        total_material_cost += qty * unit_cost
    # For demo purposes, we add placeholders for labor, machine, overheads
    labor = Decimal('0')
    machine = Decimal('0')
    overheads = Decimal('0')
    unit_cost = total_material_cost + labor + machine + overheads
    # store computed cost (side-effect but intentional)
    pc = ProductCost(product_id=bom.product_id, unit_cost=unit_cost)
    session.add(pc)
    await session.commit()
    return {'product_id': str(bom.product_id), 'unit_cost': str(unit_cost)}

