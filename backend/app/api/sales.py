from fastapi import APIRouter, Depends, HTTPException, Query, Header
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import func
from typing import Optional
from uuid import UUID
import uuid
from datetime import datetime, timezone
import io

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
import os

from app.db import get_session
from app.models import (
    Customer,
    SalesOrder,
    SalesOrderLine,
    Product,
    user_warehouses,
)
from app.schemas import (
    CustomerSchema, CustomerCreate, CustomerUpdate,
    SalesOrderSchema, SalesOrderCreate, SalesOrderUpdate,
    PaginatedResponse, ApiResponse
)
from app.api.auth import get_current_user

router = APIRouter(prefix='/api/sales')

# Customer endpoints
@router.post('/customers', response_model=ApiResponse)
async def create_customer(
    customer_data: CustomerCreate,
    session: AsyncSession = Depends(get_session)
):
    """Create a new customer"""
    try:
        customer = Customer(**customer_data.model_dump())
        session.add(customer)
        await session.commit()
        await session.refresh(customer)
        
        return ApiResponse(
            message=f"Customer '{customer.name}' created successfully",
            data=CustomerSchema.model_validate(customer)
        )
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error creating customer: {str(e)}")

@router.get('/customers', response_model=PaginatedResponse[CustomerSchema])
async def list_customers(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    active_only: bool = Query(True),
    search: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_session)
):
    """List customers with pagination and search"""
    # Build base query with filters
    query = select(Customer)
    
    if active_only:
        query = query.where(Customer.is_active == True)
    
    if search:
        query = query.where(Customer.name.ilike(f'%{search}%'))
    
    # Get total count using the same filters (FIX: Remove cartesian product)
    count_query = select(func.count(Customer.id))
    if active_only:
        count_query = count_query.where(Customer.is_active == True)
    if search:
        count_query = count_query.where(Customer.name.ilike(f'%{search}%'))
    
    count_result = await session.execute(count_query)
    total = count_result.scalar_one()
    
    # Get paginated results
    query = query.offset(skip).limit(limit)
    result = await session.execute(query)
    customers = result.scalars().all()
    
    return PaginatedResponse(
        items=customers,
        total=total,
        skip=skip,
        limit=limit,
        pages=(total + limit - 1) // limit
    )

@router.get('/customers/{customer_id}', response_model=CustomerSchema)
async def get_customer(
    customer_id: UUID,
    session: AsyncSession = Depends(get_session)
):
    """Get customer by ID"""
    result = await session.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalars().first()
    
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    return customer

@router.put('/customers/{customer_id}', response_model=CustomerSchema)
async def update_customer(
    customer_id: UUID,
    customer_data: CustomerUpdate,
    session: AsyncSession = Depends(get_session)
):
    """Update customer"""
    result = await session.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalars().first()
    
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    try:
        update_data = customer_data.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(customer, field, value)
        
        await session.commit()
        await session.refresh(customer)
        return customer
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error updating customer: {str(e)}")

@router.delete('/customers/{customer_id}', response_model=ApiResponse)
async def delete_customer(
    customer_id: UUID,
    session: AsyncSession = Depends(get_session)
):
    """Soft delete customer (set inactive)"""
    result = await session.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalars().first()
    
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    try:
        customer.is_active = False
        await session.commit()
        return ApiResponse(message="Customer deactivated successfully")
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error deactivating customer: {str(e)}")

# Sales Order endpoints
@router.post('/orders', response_model=ApiResponse)
async def create_sales_order(
    order_data: SalesOrderCreate,
    session: AsyncSession = Depends(get_session),
    authorization: Optional[str] = Header(None)
):
    """Create a new sales order"""
    try:
        if not order_data.warehouse_id:
            raise HTTPException(status_code=400, detail="Warehouse selection is required")

        user_role = None
        user_id = None

        if authorization and authorization.startswith("Bearer "):
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

        # Verify customer exists
        customer_result = await session.execute(select(Customer).where(Customer.id == order_data.customer_id))
        customer = customer_result.scalars().first()
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        if user_role and user_role != 'admin':
            try:
                user_uuid = uuid.UUID(user_id)
            except Exception:
                raise HTTPException(status_code=401, detail="Invalid user context")

            access_stmt = select(user_warehouses.c.warehouse_id).where(
                user_warehouses.c.user_id == user_uuid,
                user_warehouses.c.warehouse_id == order_data.warehouse_id,
            )
            access_result = await session.execute(access_stmt)
            if access_result.scalar_one_or_none() is None:
                raise HTTPException(status_code=403, detail="You do not have access to the selected warehouse")
        
        # Generate order number
        order_number = f"SO-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"
        
        # Create sales order
        order_dict = order_data.model_dump(exclude={'lines'})
        sales_order = SalesOrder(
            order_number=order_number,
            **order_dict
        )
        session.add(sales_order)
        await session.flush()  # Get the ID
        
        # Create order lines and calculate total
        total_amount = 0
        order_lines = []
        
        for line_data in order_data.lines:
            # Verify product exists
            product_result = await session.execute(select(Product).where(Product.id == line_data.product_id))
            product = product_result.scalars().first()
            if not product:
                raise HTTPException(status_code=404, detail=f"Product {line_data.product_id} not found")
            
            line_total = line_data.quantity * line_data.unit_price
            total_amount += line_total
            
            order_line = SalesOrderLine(
                sales_order_id=sales_order.id,
                **line_data.model_dump(),
                line_total=line_total
            )
            order_lines.append(order_line)
            session.add(order_line)
        
        sales_order.total_amount = total_amount
        await session.commit()
        
        # Reload with relationships
        result = await session.execute(
            select(SalesOrder)
            .options(selectinload(SalesOrder.lines))
            .where(SalesOrder.id == sales_order.id)
        )
        order = result.scalars().first()
        
        return ApiResponse(
            message=f"Sales order {order.order_number} created successfully",
            data=SalesOrderSchema.model_validate(order)
        )
        
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error creating sales order: {str(e)}")

@router.get('/orders', response_model=PaginatedResponse[SalesOrderSchema])
async def list_sales_orders(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    customer_id: Optional[UUID] = Query(None),
    session: AsyncSession = Depends(get_session)
):
    """List sales orders with pagination and filters"""
    query = select(SalesOrder).options(selectinload(SalesOrder.lines))
    
    if status:
        query = query.where(SalesOrder.status == status)
    
    if customer_id:
        query = query.where(SalesOrder.customer_id == customer_id)
    
    # Get total count
    count_query = select(func.count(SalesOrder.id))
    if status:
        count_query = count_query.where(SalesOrder.status == status)
    if customer_id:
        count_query = count_query.where(SalesOrder.customer_id == customer_id)
    
    count_result = await session.execute(count_query)
    total = count_result.scalar_one()
    
    # Get paginated results
    query = query.offset(skip).limit(limit).order_by(SalesOrder.created_at.desc())
    result = await session.execute(query)
    orders = result.scalars().all()
    
    return PaginatedResponse(
        items=orders,
        total=total,
        skip=skip,
        limit=limit,
        pages=(total + limit - 1) // limit
    )

@router.get('/orders/{order_id}', response_model=ApiResponse)
async def get_sales_order(
    order_id: UUID,
    session: AsyncSession = Depends(get_session)
):
    """Get sales order by ID"""
    result = await session.execute(
        select(SalesOrder)
        .options(selectinload(SalesOrder.lines))
        .where(SalesOrder.id == order_id)
    )
    order = result.scalars().first()
    
    if not order:
        raise HTTPException(status_code=404, detail="Sales order not found")
    
    return ApiResponse(
        message="Sales order retrieved successfully",
        data=SalesOrderSchema.model_validate(order)
    )

@router.put('/orders/{order_id}', response_model=SalesOrderSchema)
async def update_sales_order(
    order_id: UUID,
    order_data: SalesOrderUpdate,
    session: AsyncSession = Depends(get_session)
):
    """Update sales order"""
    result = await session.execute(select(SalesOrder).where(SalesOrder.id == order_id))
    order = result.scalars().first()
    
    if not order:
        raise HTTPException(status_code=404, detail="Sales order not found")
    
    try:
        update_data = order_data.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(order, field, value)
        
        await session.commit()
        
        # Reload with relationships
        result = await session.execute(
            select(SalesOrder)
            .options(selectinload(SalesOrder.lines))
            .where(SalesOrder.id == order_id)
        )
        return result.scalars().first()
        
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error updating sales order: {str(e)}")

@router.delete('/orders/{order_id}', response_model=ApiResponse)
async def cancel_sales_order(
    order_id: UUID,
    session: AsyncSession = Depends(get_session)
):
    """Cancel sales order"""
    result = await session.execute(select(SalesOrder).where(SalesOrder.id == order_id))
    order = result.scalars().first()
    
    if not order:
        raise HTTPException(status_code=404, detail="Sales order not found")
    
    if order.status in ['shipped', 'delivered']:
        raise HTTPException(status_code=400, detail="Cannot cancel shipped or delivered orders")
    
    try:
        order.status = 'cancelled'
        await session.commit()
        return ApiResponse(message="Sales order cancelled successfully")
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error cancelling sales order: {str(e)}")

# Import missing func from sqlalchemy
from sqlalchemy import func
from fastapi.responses import StreamingResponse
import io
import os
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib import colors


@router.get('/generate-invoice-pdf/{order_id}')
async def generate_invoice_pdf(
    order_id: UUID,
    session: AsyncSession = Depends(get_session)
):
    """Generate PDF invoice for a sales order"""
    try:
        # Get sales order with related data
        order_result = await session.execute(
            select(SalesOrder)
            .options(
                selectinload(SalesOrder.customer),
                selectinload(SalesOrder.lines).selectinload(SalesOrderLine.product)
            )
            .where(SalesOrder.id == order_id)
        )
        order = order_result.scalars().first()
        
        if not order:
            raise HTTPException(status_code=404, detail="Sales order not found")
        
        # Create PDF in memory
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        story = []
        styles = getSampleStyleSheet()
        
        # Add Company Logo
        logo_path = os.path.join(os.path.dirname(__file__), '..', '..', 'company-logo.png')
        if os.path.exists(logo_path):
            logo = Image(logo_path, width=1.5*inch, height=1.5*inch)
            story.append(logo)
            story.append(Spacer(1, 0.2*inch))
        
        # Company Header with Full Details
        story.append(Paragraph("AstroBSM", styles['Title']))
        story.append(Paragraph("Bonnesante Medicals", styles['Normal']))
        story.append(Spacer(1, 0.2*inch))
        
        # Company Address
        company_address = """
        NO 6B PEACE AVENUE/17A ISUOFIA STREET<br/>
        FEDERAL HOUSING ESTATE TRANS EKULU<br/>
        ENUGU, NIGERIA<br/>
        Phone: +234 707 679 3866, +234 901 283 5413<br/>
        Email: astrobsm@gmail.com
        """
        story.append(Paragraph(company_address, styles['Normal']))
        story.append(Spacer(1, 0.3*inch))
        
        # Invoice Header
        story.append(Paragraph(f"INVOICE #{order.order_number}", styles['Heading1']))
        story.append(Spacer(1, 0.3*inch))
        
        # Order Details
        order_info = [
            ['Invoice Date:', (order.order_date or datetime.now(timezone.utc)).strftime('%B %d, %Y')],
            ['Customer:', order.customer.name if order.customer else 'Unknown Customer'],
            ['Customer Email:', order.customer.email if order.customer and order.customer.email else 'N/A'],
            ['Customer Phone:', order.customer.phone if order.customer and order.customer.phone else 'N/A'],
            ['Customer Address:', order.customer.address if order.customer and order.customer.address else 'N/A'],
            ['Order Status:', order.status.title()],
            ['Order Number:', order.order_number]
        ]
        
        order_table = Table(order_info, colWidths=[2*inch, 4*inch])
        order_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        story.append(order_table)
        story.append(Spacer(1, 0.5*inch))
        
        # Items Table
        items_data = [['Item', 'Quantity', 'Unit Price', 'Total']]
        
        for line in order.lines:
            product_name = line.product.name if line.product else f"Product {line.product_id}"
            items_data.append([
                product_name,
                str(line.quantity),
                f"₦{float(line.unit_price):,.2f}",
                f"₦{float(line.quantity * line.unit_price):,.2f}"
            ])
        
        # Add totals
        items_data.append(['', '', 'Subtotal:', f"₦{float(order.total_amount or 0):,.2f}"])
        items_data.append(['', '', 'Tax:', '₦0.00'])
        items_data.append(['', '', 'Total:', f"₦{float(order.total_amount or 0):,.2f}"])
        
        items_table = Table(items_data, colWidths=[3*inch, 1*inch, 1.5*inch, 1.5*inch])
        items_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTNAME', (0, -3), (-1, -1), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -4), 1, colors.black),
            ('LINEBELOW', (0, -3), (-1, -3), 2, colors.black),
        ]))
        story.append(items_table)
        story.append(Spacer(1, 0.5*inch))
        
        # Payment Information Section
        payment_info = """
        <b>PAYMENT INFORMATION</b><br/>
        <br/>
        <b>Account Details:</b><br/>
        <br/>
        <b>Moniepoint Microfinance Bank</b><br/>
        Account Name: Bonnesante Medicals<br/>
        Account Number: 8259518195<br/>
        <br/>
        <b>Access Bank Nigeria</b><br/>
        Account Name: Bonnesante Medicals<br/>
        Account Number: 1379643548<br/>
        <br/>
        Please reference invoice number in payment description.
        """
        story.append(Paragraph(payment_info, styles['Normal']))
        story.append(Spacer(1, 0.3*inch))
        
        # Footer
        footer_text = """
        <b>Thank you for your business!</b><br/>
        <br/>
        <i>This is a computer-generated invoice from AstroBSM - Bonnesante Medicals.<br/>
        For inquiries, contact us at astrobsm@gmail.com or call +234 707 679 3866, +234 901 283 5413<br/>
        Visit our office at NO 6B PEACE AVENUE/17A ISUOFIA STREET, FEDERAL HOUSING ESTATE TRANS EKULU, ENUGU.</i>
        """
        story.append(Paragraph(footer_text, styles['Normal']))
        
        # Build PDF
        doc.build(story)
        buffer.seek(0)
        
        return StreamingResponse(
            io.BytesIO(buffer.read()),
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=Invoice-{order.order_number}.pdf"}
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating PDF: {str(e)}")


@router.post('/process-order/{order_id}', response_model=ApiResponse)
async def process_order_and_deduct_stock(
    order_id: UUID,
    session: AsyncSession = Depends(get_session)
):
    """Process sales order and automatically deduct stock"""
    try:
        # Import models needed for stock management
        from app.models import StockLevel, StockMovement, Warehouse, Product
        
        # Get sales order with lines and products
        order_result = await session.execute(
            select(SalesOrder)
            .options(
                selectinload(SalesOrder.lines).selectinload(SalesOrderLine.product)
            )
            .where(SalesOrder.id == order_id)
        )
        order = order_result.scalars().first()
        
        if not order:
            raise HTTPException(status_code=404, detail="Sales order not found")
        
        if order.status == 'completed':
            raise HTTPException(status_code=400, detail="Order already processed")
        
        # Get active warehouse
        warehouse_result = await session.execute(
            select(Warehouse).where(Warehouse.is_active == True)
        )
        warehouse = warehouse_result.scalars().first()
        if not warehouse:
            warehouse_result = await session.execute(select(Warehouse))
            warehouse = warehouse_result.scalars().first()
        
        if not warehouse:
            raise HTTPException(status_code=404, detail="No warehouse found")
        
        # Process each line item
        low_stock_items = []
        insufficient_stock_items = []
        
        for line in order.lines:
            # Get current stock level
            stock_result = await session.execute(
                select(StockLevel).where(
                    StockLevel.product_id == line.product_id,
                    StockLevel.warehouse_id == warehouse.id
                )
            )
            stock_level = stock_result.scalars().first()
            
            if not stock_level:
                # Create stock level entry if it doesn't exist
                stock_level = StockLevel(
                    product_id=line.product_id,
                    warehouse_id=warehouse.id,
                    current_stock=0,
                    reserved_stock=0,
                    min_stock=0
                )
                session.add(stock_level)
                await session.flush()
            
            # Check if sufficient stock is available
            if stock_level.current_stock < line.quantity:
                insufficient_stock_items.append({
                    'product_name': line.product.name if line.product else f"Product {line.product_id}",
                    'requested': line.quantity,
                    'available': stock_level.current_stock
                })
                continue
            
            # Deduct stock
            old_stock = stock_level.current_stock
            stock_level.current_stock -= line.quantity
            
            # Create stock movement record
            movement = StockMovement(
                product_id=line.product_id,
                movement_type='OUT',
                quantity=line.quantity,
                warehouse_id=warehouse.id,
                reference=f"Sales Order {order.order_number}",
                notes=(f"Stock deducted for sales order "
                       f"{order.order_number} - was {old_stock}, "
                       f"now {stock_level.current_stock}")
            )
            session.add(movement)
            
            # Check if stock is now below reorder level
            if (stock_level.current_stock <= (
                    line.product.reorder_level or 0) and
                    line.product and line.product.reorder_level):
                low_stock_items.append({
                    'product_id': str(line.product_id),
                    'product_name': line.product.name,
                    'current_stock': stock_level.current_stock,
                    'reorder_level': line.product.reorder_level
                })
        
        if insufficient_stock_items:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient stock: {insufficient_stock_items}"
            )
        
        # Update order status
        order.status = 'completed'
        
        await session.commit()
        
        return ApiResponse(
            success=True,
            message=f'Order {order.order_number} processed successfully',
            data={
                'low_stock_items': low_stock_items,
                'items_processed': len(order.lines)
            }
        )
        
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Error processing order: {str(e)}"
        )


# Payment Management Endpoints
@router.patch('/orders/{order_id}/mark-paid', response_model=ApiResponse)
async def mark_order_paid(
    order_id: UUID,
    session: AsyncSession = Depends(get_session)
):
    """Mark a sales order as paid and generate receipt"""
    try:
        # Get order with relationships
        result = await session.execute(
            select(SalesOrder)
            .options(selectinload(SalesOrder.lines), selectinload(SalesOrder.customer))
            .where(SalesOrder.id == order_id)
        )
        order = result.scalars().first()
        
        if not order:
            raise HTTPException(status_code=404, detail="Sales order not found")
        
        if order.payment_status == 'paid':
            return ApiResponse(
                message="Order already marked as paid",
                data=SalesOrderSchema.model_validate(order)
            )
        
        # Update payment status
        order.payment_status = 'paid'
        order.payment_date = datetime.now(timezone.utc)
        
        await session.commit()
        await session.refresh(order)
        
        return ApiResponse(
            message=f"Order {order.order_number} marked as paid. Receipt can be generated.",
            data=SalesOrderSchema.model_validate(order)
        )
        
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error updating payment status: {str(e)}")


@router.get('/orders/{order_id}/receipt')
async def generate_receipt(
    order_id: UUID,
    session: AsyncSession = Depends(get_session)
):
    """Generate PDF receipt for paid order"""
    try:
        # Get order with relationships
        result = await session.execute(
            select(SalesOrder)
            .options(
                selectinload(SalesOrder.lines).selectinload(SalesOrderLine.product),
                selectinload(SalesOrder.customer)
            )
            .where(SalesOrder.id == order_id)
        )
        order = result.scalars().first()
        
        if not order:
            raise HTTPException(status_code=404, detail="Sales order not found")
        
        if order.payment_status != 'paid':
            raise HTTPException(status_code=400, detail="Cannot generate receipt for unpaid order")
        
        # Create PDF in memory
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=72, leftMargin=72, topMargin=72, bottomMargin=18)
        
        # Container for PDF elements
        elements = []
        styles = getSampleStyleSheet()
        
        # Add company logo if exists
        logo_path = '/app/frontend/build/company-logo.png'
        if os.path.exists(logo_path):
            img = Image(logo_path, width=1.5*inch, height=1.5*inch)
            elements.append(img)
            elements.append(Spacer(1, 12))
        
        # Title
        title = Paragraph('<b>PAYMENT RECEIPT</b>', styles['Title'])
        elements.append(title)
        elements.append(Spacer(1, 12))
        
        # Company and customer details
        company_info = f"""
        <b>BONNESANTE MEDICALS</b><br/>
        Phone: +234 702 575 5406, +234 707 679 3866<br/>
        Email: astrobsm@gmail.com
        """
        elements.append(Paragraph(company_info, styles['Normal']))
        elements.append(Spacer(1, 12))
        
        # Receipt details
        receipt_info = f"""
        <b>Receipt No:</b> {order.order_number}<br/>
        <b>Date:</b> {order.payment_date.strftime('%Y-%m-%d %H:%M') if order.payment_date else 'N/A'}<br/>
        <b>Customer:</b> {order.customer.name}<br/>
        <b>Payment Status:</b> PAID
        """
        elements.append(Paragraph(receipt_info, styles['Normal']))
        elements.append(Spacer(1, 20))
        
        # Order lines table
        table_data = [['Product', 'Quantity', 'Unit', 'Unit Price', 'Total']]
        for line in order.lines:
            table_data.append([
                line.product.name,
                str(line.quantity),
                line.unit or 'units',
                f'₦{float(line.unit_price):,.2f}',
                f'₦{float(line.line_total):,.2f}'
            ])
        
        # Add total row
        table_data.append(['', '', '', 'TOTAL:', f'₦{float(order.total_amount):,.2f}'])
        
        table = Table(table_data, colWidths=[3*inch, 1*inch, 0.8*inch, 1.2*inch, 1.2*inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#667eea')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -2), colors.beige),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#d1fae5')),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        elements.append(table)
        elements.append(Spacer(1, 30))
        
        # Footer
        footer = Paragraph('Thank you for your business!<br/><i>This is a computer-generated receipt.</i>', styles['Normal'])
        elements.append(footer)
        
        # Build PDF
        doc.build(elements)
        buffer.seek(0)
        
        return StreamingResponse(
            buffer,
            media_type='application/pdf',
            headers={'Content-Disposition': f'attachment; filename="receipt_{order.order_number}.pdf"'}
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating receipt: {str(e)}")


@router.get('/orders/{order_id}/invoice')
async def generate_invoice(
    order_id: UUID,
    session: AsyncSession = Depends(get_session)
):
    """Generate PDF invoice for unpaid order"""
    try:
        # Get order with relationships
        result = await session.execute(
            select(SalesOrder)
            .options(
                selectinload(SalesOrder.lines).selectinload(SalesOrderLine.product),
                selectinload(SalesOrder.customer)
            )
            .where(SalesOrder.id == order_id)
        )
        order = result.scalars().first()
        
        if not order:
            raise HTTPException(status_code=404, detail="Sales order not found")
        
        # Create PDF in memory
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=72, leftMargin=72, topMargin=72, bottomMargin=18)
        
        # Container for PDF elements
        elements = []
        styles = getSampleStyleSheet()
        
        # Add company logo if exists
        logo_path = '/app/frontend/build/company-logo.png'
        if os.path.exists(logo_path):
            img = Image(logo_path, width=1.5*inch, height=1.5*inch)
            elements.append(img)
            elements.append(Spacer(1, 12))
        
        # Title
        title = Paragraph('<b>INVOICE</b>', styles['Title'])
        elements.append(title)
        elements.append(Spacer(1, 12))
        
        # Company and customer details
        company_info = f"""
        <b>BONNESANTE MEDICALS</b><br/>
        Phone: +234 702 575 5406, +234 707 679 3866<br/>
        Email: astrobsm@gmail.com
        """
        elements.append(Paragraph(company_info, styles['Normal']))
        elements.append(Spacer(1, 12))
        
        # Invoice details
        due_date = order.required_date.strftime('%Y-%m-%d') if order.required_date else 'Upon Receipt'
        payment_status_display = 'PAID' if order.payment_status == 'paid' else 'UNPAID'
        payment_status_color = 'green' if order.payment_status == 'paid' else 'red'
        
        invoice_info = f"""
        <b>Invoice No:</b> {order.order_number}<br/>
        <b>Date:</b> {order.order_date.strftime('%Y-%m-%d')}<br/>
        <b>Due Date:</b> {due_date}<br/>
        <b>Customer:</b> {order.customer.name}<br/>
        <b>Payment Status:</b> <font color="{payment_status_color}">{payment_status_display}</font>
        """
        elements.append(Paragraph(invoice_info, styles['Normal']))
        elements.append(Spacer(1, 20))
        
        # Order lines table
        table_data = [['Product', 'Quantity', 'Unit', 'Unit Price', 'Total']]
        for line in order.lines:
            table_data.append([
                line.product.name,
                str(line.quantity),
                line.unit or 'units',
                f'₦{float(line.unit_price):,.2f}',
                f'₦{float(line.line_total):,.2f}'
            ])
        
        # Add total row
        table_data.append(['', '', '', 'TOTAL DUE:', f'₦{float(order.total_amount):,.2f}'])
        
        table = Table(table_data, colWidths=[3*inch, 1*inch, 0.8*inch, 1.2*inch, 1.2*inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#667eea')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -2), colors.beige),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#fee2e2')),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        elements.append(table)
        elements.append(Spacer(1, 30))
        
        # Payment terms
        payment_terms = """
        <b>Payment Terms:</b><br/>
        Payment is due upon receipt. Please make payment to:<br/>
        <br/>
        <b>Bank: ACCESS BANK NIG PLC</b><br/>
        Account No: 1379643548<br/>
        Account Name: BONNESANTE MEDICALS<br/>
        <br/>
        <b>Bank: MONIEPOINT MICROFINANCE BANK</b><br/>
        Account No: 8259518195<br/>
        Account Name: BONNESANTE MEDICALS
        """
        elements.append(Paragraph(payment_terms, styles['Normal']))
        elements.append(Spacer(1, 20))
        
        # Footer
        footer = Paragraph('<i>This is a computer-generated invoice.</i>', styles['Normal'])
        elements.append(footer)
        
        # Build PDF
        doc.build(elements)
        buffer.seek(0)
        
        return StreamingResponse(
            buffer,
            media_type='application/pdf',
            headers={'Content-Disposition': f'attachment; filename="invoice_{order.order_number}.pdf"'}
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating invoice: {str(e)}")
