from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from typing import List, Optional
from uuid import UUID
import uuid
from datetime import datetime, timezone

from app.db import get_session
from app.models import (
    ProductionOrder, ProductionOrderMaterial, Product, RawMaterial, 
    BOM, BOMLine, SalesOrder, StockLevel, StockMovement, Warehouse
)
from app.schemas import (
    ProductionOrderSchema, ProductionOrderCreate, ProductionOrderUpdate,
    PaginatedResponse, ApiResponse
)
from sqlalchemy import func

router = APIRouter(prefix='/api/production')

@router.post('/orders', response_model=ProductionOrderSchema)
async def create_production_order(
    order_data: ProductionOrderCreate,
    session: AsyncSession = Depends(get_session)
):
    """Create a new production order"""
    try:
        # Verify product exists
        product_result = await session.execute(select(Product).where(Product.id == order_data.product_id))
        product = product_result.scalars().first()
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
        
        # Verify sales order exists if provided
        if hasattr(order_data, 'sales_order_id') and order_data.sales_order_id:
            sales_result = await session.execute(select(SalesOrder).where(SalesOrder.id == order_data.sales_order_id))
            sales_order = sales_result.scalars().first()
            if not sales_order:
                raise HTTPException(status_code=404, detail="Sales order not found")
        
        # Generate production order number
        order_number = f"PO-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"
        
        # Create production order (exclude sales_order_id as it's not in the model yet)
        order_dict = order_data.dict(exclude={'sales_order_id'})
        production_order = ProductionOrder(
            order_number=order_number,
            **order_dict
        )
        session.add(production_order)
        await session.flush()  # Get the ID
        
        # Create material requirements based on BOM
        bom_result = await session.execute(
            select(BOM)
            .options(selectinload(BOM.lines))
            .where(BOM.product_id == order_data.product_id)
        )
        bom = bom_result.scalars().first()
        
        if bom:
            # Get default warehouse
            warehouse_result = await session.execute(
                select(Warehouse).where(Warehouse.is_active == True).limit(1)
            )
            default_warehouse = warehouse_result.scalars().first()
            
            for bom_line in bom.lines:
                required_qty = bom_line.qty_per_unit * order_data.quantity_planned
                
                material_req = ProductionOrderMaterial(
                    production_order_id=production_order.id,
                    raw_material_id=bom_line.raw_material_id,
                    quantity_required=required_qty,
                    warehouse_id=default_warehouse.id if default_warehouse else None
                )
                session.add(material_req)
        
        await session.commit()
        await session.refresh(production_order)
        return production_order
        
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        import traceback
        error_detail = f"Error creating production order: {str(e)}\n{traceback.format_exc()}"
        print(f"PRODUCTION ORDER ERROR: {error_detail}")
        raise HTTPException(status_code=400, detail=f"Error creating production order: {str(e)}")

@router.get('/orders', response_model=PaginatedResponse[ProductionOrderSchema])
async def list_production_orders(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    product_id: Optional[UUID] = Query(None),
    assigned_to: Optional[UUID] = Query(None),
    session: AsyncSession = Depends(get_session)
):
    """List production orders with pagination and filters"""
    query = select(ProductionOrder)
    
    if status:
        query = query.where(ProductionOrder.status == status)
    
    if product_id:
        query = query.where(ProductionOrder.product_id == product_id)
    
    if assigned_to:
        query = query.where(ProductionOrder.assigned_to == assigned_to)
    
    # Get total count
    count_query = select(func.count(ProductionOrder.id))
    if status:
        count_query = count_query.where(ProductionOrder.status == status)
    if product_id:
        count_query = count_query.where(ProductionOrder.product_id == product_id)
    if assigned_to:
        count_query = count_query.where(ProductionOrder.assigned_to == assigned_to)
    
    count_result = await session.execute(count_query)
    total = count_result.scalar_one()
    
    # Get paginated results
    query = query.offset(skip).limit(limit).order_by(ProductionOrder.created_at.desc())
    result = await session.execute(query)
    orders = result.scalars().all()
    
    return PaginatedResponse(
        items=orders,
        total=total,
        skip=skip,
        limit=limit,
        pages=(total + limit - 1) // limit
    )

@router.get('/orders/{order_id}', response_model=ProductionOrderSchema)
async def get_production_order(
    order_id: UUID,
    session: AsyncSession = Depends(get_session)
):
    """Get production order by ID"""
    result = await session.execute(select(ProductionOrder).where(ProductionOrder.id == order_id))
    order = result.scalars().first()
    
    if not order:
        raise HTTPException(status_code=404, detail="Production order not found")
    
    return order

@router.put('/orders/{order_id}', response_model=ProductionOrderSchema)
async def update_production_order(
    order_id: UUID,
    order_data: ProductionOrderUpdate,
    session: AsyncSession = Depends(get_session)
):
    """Update production order"""
    result = await session.execute(select(ProductionOrder).where(ProductionOrder.id == order_id))
    order = result.scalars().first()
    
    if not order:
        raise HTTPException(status_code=404, detail="Production order not found")
    
    try:
        update_data = order_data.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(order, field, value)
        
        await session.commit()
        await session.refresh(order)
        return order
        
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error updating production order: {str(e)}")

@router.post('/orders/{order_id}/start', response_model=ProductionOrderSchema)
async def start_production_order(
    order_id: UUID,
    session: AsyncSession = Depends(get_session)
):
    """Start production order"""
    result = await session.execute(select(ProductionOrder).where(ProductionOrder.id == order_id))
    order = result.scalars().first()
    
    if not order:
        raise HTTPException(status_code=404, detail="Production order not found")
    
    if order.status != 'planned':
        raise HTTPException(status_code=400, detail="Can only start planned production orders")
    
    try:
        order.status = 'in_progress'
        order.actual_start_date = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(order)
        return order
        
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error starting production order: {str(e)}")

@router.post('/orders/{order_id}/complete', response_model=ProductionOrderSchema)
async def complete_production_order(
    order_id: UUID,
    quantity_produced: float = Query(..., gt=0),
    warehouse_id: Optional[UUID] = Query(None),
    session: AsyncSession = Depends(get_session)
):
    """Complete production order and update stock"""
    result = await session.execute(select(ProductionOrder).where(ProductionOrder.id == order_id))
    order = result.scalars().first()
    
    if not order:
        raise HTTPException(status_code=404, detail="Production order not found")
    
    if order.status != 'in_progress':
        raise HTTPException(status_code=400, detail="Can only complete in-progress production orders")
    
    try:
        # Get default warehouse if not specified
        if not warehouse_id:
            warehouse_result = await session.execute(
                select(Warehouse).where(Warehouse.is_active == True).limit(1)
            )
            warehouse = warehouse_result.scalars().first()
            if not warehouse:
                raise HTTPException(status_code=400, detail="No active warehouse found")
            warehouse_id = warehouse.id
        
        # Update production order
        order.status = 'completed'
        order.quantity_produced = quantity_produced
        order.actual_end_date = datetime.now(timezone.utc)
        
        # Create stock movement for produced goods
        stock_movement = StockMovement(
            warehouse_id=warehouse_id,
            product_id=order.product_id,
            movement_type='IN',
            quantity=quantity_produced,
            reference=f"Production Order {order.order_number}",
            notes=f"Production completed"
        )
        session.add(stock_movement)
        
        # Update stock levels
        stock_level_result = await session.execute(
            select(StockLevel).where(
                StockLevel.warehouse_id == warehouse_id,
                StockLevel.product_id == order.product_id
            )
        )
        stock_level = stock_level_result.scalars().first()
        
        if stock_level:
            stock_level.current_stock += quantity_produced
        else:
            stock_level = StockLevel(
                warehouse_id=warehouse_id,
                product_id=order.product_id,
                current_stock=quantity_produced
            )
            session.add(stock_level)
        
        await session.commit()
        await session.refresh(order)
        return order
        
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error completing production order: {str(e)}")

@router.delete('/orders/{order_id}', response_model=ApiResponse)
async def cancel_production_order(
    order_id: UUID,
    session: AsyncSession = Depends(get_session)
):
    """Cancel production order"""
    result = await session.execute(select(ProductionOrder).where(ProductionOrder.id == order_id))
    order = result.scalars().first()
    
    if not order:
        raise HTTPException(status_code=404, detail="Production order not found")
    
    if order.status == 'completed':
        raise HTTPException(status_code=400, detail="Cannot cancel completed production orders")
    
    try:
        order.status = 'cancelled'
        await session.commit()
        return ApiResponse(message="Production order cancelled successfully")
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error cancelling production order: {str(e)}")

@router.get('/orders/{order_id}/materials')
async def get_production_order_materials(
    order_id: UUID,
    session: AsyncSession = Depends(get_session)
):
    """Get material requirements for production order"""
    # Verify order exists
    order_result = await session.execute(select(ProductionOrder).where(ProductionOrder.id == order_id))
    order = order_result.scalars().first()
    
    if not order:
        raise HTTPException(status_code=404, detail="Production order not found")
    
    # Get material requirements
    materials_result = await session.execute(
        select(ProductionOrderMaterial, RawMaterial)
        .join(RawMaterial, ProductionOrderMaterial.raw_material_id == RawMaterial.id)
        .where(ProductionOrderMaterial.production_order_id == order_id)
    )
    
    materials = []
    for material_req, raw_material in materials_result.all():
        materials.append({
            'raw_material_id': raw_material.id,
            'raw_material_name': raw_material.name,
            'raw_material_sku': raw_material.sku,
            'quantity_required': float(material_req.quantity_required),
            'quantity_consumed': float(material_req.quantity_consumed),
            'warehouse_id': material_req.warehouse_id
        })
    
    return {'materials': materials}

@router.get('/dashboard/stats')
async def get_production_dashboard_stats(
    session: AsyncSession = Depends(get_session)
):
    """Get production dashboard statistics"""
    try:
        # Count orders by status
        status_counts = await session.execute(
            select(ProductionOrder.status, func.count(ProductionOrder.id))
            .group_by(ProductionOrder.status)
        )
        
        stats = {
            'orders_by_status': dict(status_counts.all()),
            'total_orders': 0,
            'active_orders': 0
        }
        
        # Calculate totals
        for status, count in stats['orders_by_status'].items():
            stats['total_orders'] += count
            if status in ['planned', 'in_progress']:
                stats['active_orders'] += count
        
        return stats
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting dashboard stats: {str(e)}")


@router.post('/calculate-requirements')
async def calculate_production_requirements(
    product_id: UUID,
    quantity: int,
    session: AsyncSession = Depends(get_session)
):
    """Calculate raw material requirements for production"""
    try:
        # Verify product exists
        product_result = await session.execute(
            select(Product).where(Product.id == product_id)
        )
        product = product_result.scalars().first()
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
        
        # Get BOM for the product
        bom_result = await session.execute(
            select(BOM)
            .options(selectinload(BOM.bom_lines).selectinload(BOMLine.raw_material))
            .where(BOM.product_id == product_id)
            .where(BOM.is_active == True)
        )
        bom = bom_result.scalars().first()
        
        if not bom:
            raise HTTPException(status_code=404, detail="No active BOM found for this product")
        
        # Calculate requirements for each raw material
        requirements = []
        can_produce = True
        
        for bom_line in bom.bom_lines:
            required_quantity = bom_line.quantity * quantity
            
            # Get current stock level for this raw material
            stock_result = await session.execute(
                select(StockLevel)
                .where(StockLevel.raw_material_id == bom_line.raw_material_id)
            )
            stock_level = stock_result.scalars().first()
            available_quantity = stock_level.current_stock if stock_level else 0
            
            # Check if sufficient stock is available
            is_sufficient = available_quantity >= required_quantity
            if not is_sufficient:
                can_produce = False
            
            requirements.append({
                'raw_material_id': str(bom_line.raw_material_id),
                'raw_material_name': bom_line.raw_material.name,
                'raw_material_code': bom_line.raw_material.material_code,
                'required_quantity': required_quantity,
                'available_quantity': available_quantity,
                'unit': bom_line.raw_material.unit,
                'is_sufficient': is_sufficient,
                'shortage': max(0, required_quantity - available_quantity)
            })
        
        return {
            'product_id': str(product_id),
            'product_name': product.name,
            'product_sku': product.sku,
            'quantity': quantity,
            'bom_id': str(bom.id),
            'can_produce': can_produce,
            'requirements': requirements,
            'total_cost': sum(req['required_quantity'] * (bom_line.raw_material.cost_per_unit or 0) 
                            for req, bom_line in zip(requirements, bom.bom_lines))
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error calculating requirements: {str(e)}")


@router.post('/execute-production')
async def execute_production(
    product_id: UUID,
    quantity: int,
    notes: Optional[str] = None,
    session: AsyncSession = Depends(get_session)
):
    """Execute production order and deduct raw materials from stock"""
    try:
        # First calculate requirements to verify we can produce
        requirements_data = await calculate_production_requirements(product_id, quantity, session)
        
        if not requirements_data['can_produce']:
            raise HTTPException(
                status_code=400, 
                detail="Insufficient raw materials to complete production"
            )
        
        # Get default warehouse
        warehouse_result = await session.execute(
            select(Warehouse).where(Warehouse.is_default == True)
        )
        warehouse = warehouse_result.scalars().first()
        if not warehouse:
            warehouse_result = await session.execute(select(Warehouse))
            warehouse = warehouse_result.scalars().first()
        
        if not warehouse:
            raise HTTPException(status_code=404, detail="No warehouse found")
        
        # Generate production order number
        order_number = f"PO-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"
        
        # Create production order
        production_order = ProductionOrder(
            order_number=order_number,
            product_id=product_id,
            quantity_planned=quantity,
            quantity_produced=quantity,  # Mark as produced immediately
            status='completed',
            start_date=datetime.now(timezone.utc),
            end_date=datetime.now(timezone.utc),
            notes=notes or "Automated production via console",
            warehouse_id=warehouse.id
        )
        session.add(production_order)
        await session.flush()  # Get the production order ID
        
        # Deduct raw materials from stock
        stock_movements = []
        for requirement in requirements_data['requirements']:
            raw_material_id = UUID(requirement['raw_material_id'])
            required_qty = requirement['required_quantity']
            
            # Update stock level
            stock_result = await session.execute(
                select(StockLevel).where(StockLevel.raw_material_id == raw_material_id)
            )
            stock_level = stock_result.scalars().first()
            
            if stock_level:
                old_stock = stock_level.current_stock
                stock_level.current_stock -= required_qty
                stock_level.reserved_stock = max(0, stock_level.reserved_stock - required_qty)
                
                # Create stock movement record
                movement = StockMovement(
                    raw_material_id=raw_material_id,
                    movement_type='production_consumption',
                    quantity=-required_qty,  # Negative for consumption
                    warehouse_id=warehouse.id,
                    reference_type='production_order',
                    reference_id=str(production_order.id),
                    notes=f"Raw material consumed for production order {order_number}",
                    old_stock_level=old_stock,
                    new_stock_level=stock_level.current_stock
                )
                session.add(movement)
                stock_movements.append(movement)
                
                # Create production order material record
                order_material = ProductionOrderMaterial(
                    production_order_id=production_order.id,
                    raw_material_id=raw_material_id,
                    quantity_planned=required_qty,
                    quantity_used=required_qty,
                    cost_per_unit=requirement.get('cost_per_unit', 0)
                )
                session.add(order_material)
        
        # Add finished product to stock
        product_stock_result = await session.execute(
            select(StockLevel).where(StockLevel.product_id == product_id)
        )
        product_stock = product_stock_result.scalars().first()
        
        if product_stock:
            old_product_stock = product_stock.current_stock
            product_stock.current_stock += quantity
        else:
            # Create new stock level entry
            old_product_stock = 0
            product_stock = StockLevel(
                product_id=product_id,
                warehouse_id=warehouse.id,
                current_stock=quantity,
                reserved_stock=0,
                minimum_stock=0
            )
            session.add(product_stock)
        
        # Create stock movement for finished product
        product_movement = StockMovement(
            product_id=product_id,
            movement_type='production_output',
            quantity=quantity,
            warehouse_id=warehouse.id,
            reference_type='production_order',
            reference_id=str(production_order.id),
            notes=f"Production output for order {order_number}",
            old_stock_level=old_product_stock,
            new_stock_level=old_product_stock + quantity
        )
        session.add(product_movement)
        
        await session.commit()
        
        return {
            'success': True,
            'message': f'Production order {order_number} completed successfully',
            'production_order_id': str(production_order.id),
            'order_number': order_number,
            'product_name': requirements_data['product_name'],
            'quantity_produced': quantity,
            'raw_materials_consumed': len(requirements_data['requirements']),
            'total_cost': requirements_data['total_cost']
        }
        
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Error executing production: {str(e)}")


@router.post('/register-output')
async def register_production_output(
    production_order_id: UUID,
    actual_quantity: float,
    quality_grade: str = 'A',
    defective_quantity: float = 0,
    completion_date: Optional[str] = None,
    notes: Optional[str] = None,
    session: AsyncSession = Depends(get_session)
):
    """Register production output for a production order"""
    try:
        # Verify production order exists
        order_result = await session.execute(
            select(ProductionOrder).where(ProductionOrder.id == production_order_id)
        )
        production_order = order_result.scalars().first()
        if not production_order:
            raise HTTPException(status_code=404, detail="Production order not found")
        
        # Update production order with actual output
        production_order.quantity_produced = actual_quantity
        production_order.defective_quantity = defective_quantity
        production_order.quality_grade = quality_grade
        production_order.status = 'completed'
        
        if completion_date:
            production_order.end_date = datetime.fromisoformat(completion_date)
        else:
            production_order.end_date = datetime.now(timezone.utc)
        
        if notes:
            production_order.notes = (production_order.notes or '') + f"\nOutput Notes: {notes}"
        
        await session.commit()
        
        return {
            'success': True,
            'message': f'Production output registered successfully for order {production_order.order_number}',
            'production_order_id': str(production_order.id),
            'actual_quantity': actual_quantity,
            'quality_grade': quality_grade,
            'defective_quantity': defective_quantity
        }
        
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Error registering production output: {str(e)}")


@router.post('/register-participants')
async def register_production_participants(
    production_order_id: UUID,
    participants: List[UUID],
    shift: str = 'morning',
    supervisor_id: Optional[UUID] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    notes: Optional[str] = None,
    session: AsyncSession = Depends(get_session)
):
    """Register participants for a production order"""
    try:
        # Import Staff model here to avoid circular imports
        from app.models import Staff
        
        # Verify production order exists
        order_result = await session.execute(
            select(ProductionOrder).where(ProductionOrder.id == production_order_id)
        )
        production_order = order_result.scalars().first()
        if not production_order:
            raise HTTPException(status_code=404, detail="Production order not found")
        
        # Verify all participants exist
        participants_result = await session.execute(
            select(Staff).where(Staff.id.in_(participants))
        )
        staff_members = participants_result.scalars().all()
        
        if len(staff_members) != len(participants):
            raise HTTPException(status_code=404, detail="One or more staff members not found")
        
        # Verify supervisor exists if provided
        supervisor = None
        if supervisor_id:
            supervisor_result = await session.execute(
                select(Staff).where(Staff.id == supervisor_id)
            )
            supervisor = supervisor_result.scalars().first()
            if not supervisor:
                raise HTTPException(status_code=404, detail="Supervisor not found")
        
        # Create participant records (we'll need to create a new model for this)
        # For now, we'll update the production order with participant information
        participant_names = [f"{staff.first_name} {staff.last_name}" for staff in staff_members]
        supervisor_name = f"{supervisor.first_name} {supervisor.last_name}" if supervisor else "Not assigned"
        
        participant_info = {
            'shift': shift,
            'supervisor': supervisor_name,
            'participants': participant_names,
            'start_time': start_time,
            'end_time': end_time,
            'participant_notes': notes
        }
        
        # Update production order notes with participant information
        current_notes = production_order.notes or ''
        production_order.notes = current_notes + f"\nParticipants Info: {participant_info}"
        
        await session.commit()
        
        return {
            'success': True,
            'message': f'Production participants registered successfully for order {production_order.order_number}',
            'production_order_id': str(production_order.id),
            'participants_count': len(participants),
            'shift': shift,
            'supervisor': supervisor_name
        }
        
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Error registering production participants: {str(e)}")
