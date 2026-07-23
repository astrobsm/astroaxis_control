from fastapi import APIRouter, Depends, HTTPException, Query, Header
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import func, text
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
from app.services.receivables import (
    ensure_invoice_for_order, record_payment, recompute_invoice_paid, money)
from app.services.inventory import apply_stock_movement
from app.services.costing import snapshot_line_cost
from app.services.posting import post_sale, reverse_sale
from app.models import (
    Customer,
    SalesOrder,
    SalesOrderLine,
    Product,
    user_warehouses,
    StockLevel,
    StockMovement,
)
from app.schemas import (
    CustomerSchema, CustomerCreate, CustomerUpdate,
    SalesOrderSchema, SalesOrderCreate, SalesOrderUpdate,
    PaginatedResponse, ApiResponse
)
from app.api.auth import get_current_user
from app.api.notifications import (
    fire_notification,
    notify_sale_created,
    notify_payment_received,
)
from decimal import Decimal
from sqlalchemy import and_

router = APIRouter(prefix='/api/sales')


async def deduct_stock_for_order(session: AsyncSession, order: SalesOrder):
    """Deduct stock from warehouse for all lines in the order"""
    # Get order lines
    lines_result = await session.execute(
        select(SalesOrderLine).where(SalesOrderLine.sales_order_id == order.id)
    )
    lines = lines_result.scalars().all()
    
    for line in lines:
        # One locked, validated path. The old code read the balance, checked
        # it, then mutated it in Python -- with no row lock, so two concurrent
        # orders for the same product both passed the check and both wrote,
        # overselling stock that was never there.
        await apply_stock_movement(
            session,
            warehouse_id=order.warehouse_id,
            product_id=line.product_id,
            movement_type='OUT',
            quantity=Decimal(str(line.quantity)),
            reference=f"Sales Order: {order.order_number}",
            notes=f"Stock deducted for sales order {order.order_number}",
            created_by=order.created_by,
        )


async def restore_stock_for_order(session: AsyncSession, order: SalesOrder):
    """Restore stock to warehouse when order is cancelled"""
    # Get order lines
    lines_result = await session.execute(
        select(SalesOrderLine).where(SalesOrderLine.sales_order_id == order.id)
    )
    lines = lines_result.scalars().all()
    
    for line in lines:
        # Cancelling returns the committed stock. Previously this silently did
        # nothing when no StockLevel row existed, so cancelling an order whose
        # balance row had since been removed quietly lost the units.
        await apply_stock_movement(
            session,
            warehouse_id=order.warehouse_id,
            product_id=line.product_id,
            movement_type='RETURN',
            quantity=Decimal(str(line.quantity)),
            reference=f"Cancelled Order: {order.order_number}",
            notes=f"Stock restored from cancelled order {order.order_number}",
            created_by=order.created_by,
        )


async def cancel_order_effects(session: AsyncSession, order: SalesOrder,
                               reason: str = "Order cancelled"):
    """Fully unwind a sales order so it no longer counts anywhere.

    One atomic operation, in the caller's transaction:
      1. return the committed stock to the warehouse (through the inventory
         service, which relocks and writes a movement + balance together);
      2. reverse the sale's journal entries and any payments against it, so
         revenue, receivable, COGS and cash all back out of the ledger;
      3. cancel the order's invoices so they drop out of receivables;
      4. mark the order CANCELLED.

    Idempotent: a second call on an already-cancelled order does nothing, so a
    double-clicked Cancel button cannot restock or reverse twice.
    """
    if order.status == 'cancelled':
        return False

    # 1. Restore stock only if it was ever committed. A draft/pending order
    #    never deducted, so restoring would invent units that never left.
    if order.status in ('confirmed', 'completed'):
        await restore_stock_for_order(session, order)

    # 2. Reverse the ledger (no-op unless posting is enabled).
    await reverse_sale(session, order_id=order.id,
                       order_number=order.order_number, reason=reason,
                       created_by=order.created_by)

    # 3. Drop this order's invoices out of receivables.
    await session.execute(
        text("UPDATE invoices SET status = 'cancelled' "
             "WHERE sales_order_id = :oid AND status <> 'cancelled'"),
        {"oid": str(order.id)},
    )

    # 4. The order no longer persists as a live sale.
    order.status = 'cancelled'
    order.payment_status = 'cancelled'
    return True


# Customer endpoints
@router.post('/customers', response_model=ApiResponse)
async def create_customer(
    customer_data: CustomerCreate,
    session: AsyncSession = Depends(get_session)
):
    """Create a new customer with auto-generated customer code"""
    try:
        data_dict = customer_data.model_dump()
        
        # Auto-generate customer_code if not provided
        if not data_dict.get('customer_code'):
            # Get ALL existing customer codes and find the highest numeric suffix
            result = await session.execute(
                select(Customer.customer_code)
            )
            existing_codes = [r[0] for r in result.fetchall() if r[0]]
            max_num = 0
            import re
            for code in existing_codes:
                # Extract trailing digits from any code format
                nums = re.findall(r'\d+', code or '')
                if nums:
                    num = int(nums[-1])
                    if num > max_num:
                        max_num = num
            next_num = max_num + 1
            data_dict['customer_code'] = f'CUST-{next_num:04d}'
        
        customer = Customer(**data_dict)
        session.add(customer)
        await session.commit()
        await session.refresh(customer)
        
        return ApiResponse(
            message=f"Customer '{customer.name}' created successfully (Code: {customer.customer_code})",
            data=CustomerSchema.model_validate(customer)
        )
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error creating customer: {str(e)}")

@router.get('/customers', response_model=PaginatedResponse[CustomerSchema])
async def list_customers(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
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
    
    # Calculate page from skip/limit
    page = (skip // limit) + 1 if limit > 0 else 1
    size = limit
    
    # Get paginated results
    query = query.offset(skip).limit(limit)
    result = await session.execute(query)
    customers = result.scalars().all()
    
    return PaginatedResponse(
        items=customers,
        total=total,
        page=page,
        size=size,
        pages=(total + size - 1) // size if size > 0 else 1
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
        order_dict = order_data.model_dump(exclude={'lines', 'order_type'})
        sales_order = SalesOrder(
            order_number=order_number,
            **order_dict
        )
        # If payment_status is 'paid', set payment_date
        if sales_order.payment_status == 'paid':
            sales_order.payment_date = datetime.now(timezone.utc)
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
            
            # Round each line to the 2dp the column stores, THEN sum.
            # Previously total_amount summed the unrounded products while the
            # database rounded each line_total on insert, so the printed line
            # totals could sum to a different figure than the printed TOTAL
            # (three lines at 1.005 -> lines 3.03, total 3.02). That drift
            # propagated into invoices and was then absorbed by the payment
            # tolerance, which masked it rather than fixing it.
            line_total = money(line_data.quantity * line_data.unit_price)
            total_amount += line_total

            order_line = SalesOrderLine(
                sales_order_id=sales_order.id,
                **line_data.model_dump(),
                line_total=line_total
            )
            order_lines.append(order_line)
            session.add(order_line)

        await session.flush()  # order lines need ids before costing them

        # Snapshot cost of goods sold at the moment of sale. Recomputing this
        # later from the price list restates the profit of closed periods.
        for order_line in order_lines:
            await snapshot_line_cost(
                session,
                line_id=order_line.id,
                table='sales_order_lines',
                product_id=order_line.product_id,
                quantity=order_line.quantity,
                unit=order_line.unit,
                warehouse_id=sales_order.warehouse_id,
            )

        sales_order.total_amount = total_amount
        
        # Always deduct stock when a sales order is created (order commits inventory)
        sales_order.status = 'confirmed'
        await deduct_stock_for_order(session, sales_order)

        # Recognise the sale in the general ledger, in this same transaction:
        # revenue, receivable, and the cost of the goods just shipped. If the
        # posting fails the whole order rolls back, so stock can never move
        # without the books knowing. No-ops unless ACCOUNTING_POSTING_ENABLED.
        await post_sale(session, order_id=sales_order.id,
                        created_by=sales_order.created_by)

        await session.commit()

        # Reload with relationships
        result = await session.execute(
            select(SalesOrder)
            .options(selectinload(SalesOrder.lines))
            .where(SalesOrder.id == sales_order.id)
        )
        order = result.scalars().first()
        
        # Fire push notification (non-blocking)
        try:
            fire_notification(notify_sale_created(
                order_number=order.order_number,
                customer_name=customer.name if customer else "Customer",
                total_amount=float(order.total_amount or 0),
                line_count=len(order.lines or []),
            ))
            if order.payment_status == 'paid':
                fire_notification(notify_payment_received(
                    order_number=order.order_number,
                    amount=float(order.total_amount or 0),
                    customer_name=customer.name if customer else "Customer",
                ))
        except Exception as _notif_err:
            print(f"[sales] notification dispatch error: {_notif_err}")
        
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
    limit: int = Query(20, ge=1, le=1000),
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
    
    # Calculate page from skip/limit
    page = (skip // limit) + 1 if limit > 0 else 1
    size = limit
    
    # Get paginated results
    query = query.offset(skip).limit(limit).order_by(SalesOrder.created_at.desc())
    result = await session.execute(query)
    orders = result.scalars().all()
    
    return PaginatedResponse(
        items=orders,
        total=total,
        page=page,
        size=size,
        pages=(total + size - 1) // size if size > 0 else 1
    )


@router.get('/customer/{customer_id}/unpaid')
async def get_customer_unpaid_orders(
    customer_id: UUID,
    session: AsyncSession = Depends(get_session)
):
    """Get all unpaid/partial orders for a customer with line-item details"""
    try:
        # Verify customer exists
        cust_result = await session.execute(
            select(Customer).where(Customer.id == customer_id)
        )
        customer = cust_result.scalars().first()
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        # Fetch unpaid and partial orders
        result = await session.execute(
            select(SalesOrder)
            .options(selectinload(SalesOrder.lines).selectinload(SalesOrderLine.product))
            .where(
                SalesOrder.customer_id == customer_id,
                SalesOrder.payment_status.in_(['unpaid', 'partial']),
                SalesOrder.status != 'cancelled'
            )
            .order_by(SalesOrder.order_date.desc())
        )
        orders = result.scalars().all()

        unpaid_orders = []
        total_outstanding = 0.0
        for order in orders:
            order_total = float(order.total_amount or 0)
            total_outstanding += order_total
            lines_desc = []
            for line in order.lines:
                product_name = line.product.name if line.product else 'Unknown Product'
                lines_desc.append({
                    "product_name": product_name,
                    "quantity": float(line.quantity),
                    "unit_price": float(line.unit_price),
                    "line_total": float(line.quantity * line.unit_price)
                })
            unpaid_orders.append({
                "order_id": str(order.id),
                "order_number": order.order_number,
                "order_date": str(order.order_date) if order.order_date else None,
                "payment_status": order.payment_status,
                "total_amount": order_total,
                "notes": order.notes,
                "lines": lines_desc
            })

        return {
            "customer_id": str(customer_id),
            "customer_name": customer.name,
            "unpaid_orders": unpaid_orders,
            "total_outstanding": total_outstanding,
            "count": len(unpaid_orders)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching unpaid orders: {str(e)}")


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
        old_status = order.status
        update_data = order_data.dict(exclude_unset=True)
        new_status = update_data.get('status', old_status)
        
        for field, value in update_data.items():
            setattr(order, field, value)
        
        # Handle stock changes based on status transitions
        if old_status != new_status:
            # Cancelling fully unwinds stock AND the ledger, in real time.
            if new_status == 'cancelled':
                await cancel_order_effects(
                    session, order, reason=f"Order {order.order_number} cancelled")
            # Deduct stock when confirming an order
            elif old_status in ['pending', 'draft'] and new_status == 'confirmed':
                await deduct_stock_for_order(session, order)
            # Restore stock when reverting from confirmed to pending
            elif old_status == 'confirmed' and new_status in ['pending', 'draft']:
                await restore_stock_for_order(session, order)

        await session.commit()
        
        # Reload with relationships
        result = await session.execute(
            select(SalesOrder)
            .options(selectinload(SalesOrder.lines))
            .where(SalesOrder.id == order_id)
        )
        return result.scalars().first()
        
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error updating sales order: {str(e)}")

@router.post('/orders/{order_id}/cancel', response_model=ApiResponse)
async def cancel_sales_order(
    order_id: UUID,
    session: AsyncSession = Depends(get_session),
    reason: Optional[str] = None,
):
    """Cancel an order returned by the customer.

    Restores the stock, reverses the sale (and any payments) in the ledger, and
    marks the order CANCELLED — all in one transaction — so the returned order
    stops counting towards stock, revenue or receivables the moment it is
    cancelled. The record is kept (not deleted) so the reversal is auditable.
    """
    result = await session.execute(select(SalesOrder).where(SalesOrder.id == order_id))
    order = result.scalars().first()
    if not order:
        raise HTTPException(status_code=404, detail="Sales order not found")
    try:
        changed = await cancel_order_effects(
            session, order, reason=reason or f"Order {order.order_number} cancelled")
        await session.commit()
        if not changed:
            return ApiResponse(message=f"Order {order.order_number} was already cancelled")
        return ApiResponse(message=f"Order {order.order_number} cancelled; stock restored and sales reversed")
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error cancelling order: {str(e)}")


@router.delete('/orders/{order_id}', response_model=ApiResponse)
async def delete_sales_order(
    order_id: UUID,
    session: AsyncSession = Depends(get_session)
):
    """Permanently delete a sales order and all related records"""
    result = await session.execute(select(SalesOrder).where(SalesOrder.id == order_id))
    order = result.scalars().first()
    
    if not order:
        raise HTTPException(status_code=404, detail="Sales order not found")
    
    try:
        # Restore stock if the order had stock deducted (confirmed/completed)
        if order.status in ['confirmed', 'completed']:
            await restore_stock_for_order(session, order)

        # Reverse the sale in the ledger BEFORE deleting the order, or the
        # posted revenue/receivable/COGS would be orphaned from the record that
        # produced them. The reversal entries stay in the immutable ledger; the
        # operational order records below are what get removed.
        if order.status != 'cancelled':
            await reverse_sale(
                session, order_id=order.id, order_number=order.order_number,
                reason=f"Order {order.order_number} deleted", created_by=order.created_by)

        # Delete in dependency order: payments → invoice_lines → invoices → order_lines → returned_stock → order
        # 1. Find related invoices
        inv_result = await session.execute(
            text("SELECT id FROM invoices WHERE sales_order_id = :oid"),
            {"oid": order.id}
        )
        invoice_ids = [row[0] for row in inv_result.fetchall()]
        
        if invoice_ids:
            # 2. Delete payments linked to those invoices
            for inv_id in invoice_ids:
                await session.execute(
                    text("DELETE FROM payments WHERE invoice_id = :iid"),
                    {"iid": inv_id}
                )
            # 3. Delete invoice lines
            for inv_id in invoice_ids:
                await session.execute(
                    text("DELETE FROM invoice_lines WHERE invoice_id = :iid"),
                    {"iid": inv_id}
                )
            # 4. Delete invoices
            await session.execute(
                text("DELETE FROM invoices WHERE sales_order_id = :oid"),
                {"oid": order.id}
            )
        
        # 5. Delete returned stock records
        await session.execute(
            text("DELETE FROM returned_stock WHERE sales_order_id = :oid"),
            {"oid": order.id}
        )
        
        # 6. Delete order lines
        await session.execute(
            text("DELETE FROM sales_order_lines WHERE sales_order_id = :oid"),
            {"oid": order.id}
        )
        
        # 7. Delete stock movements referencing this order
        await session.execute(
            text("DELETE FROM stock_movements WHERE reference LIKE :ref"),
            {"ref": f"%{order.order_number}%"}
        )
        
        # 8. Delete the order itself
        await session.delete(order)
        await session.commit()
        return ApiResponse(message=f"Sales order {order.order_number} permanently deleted and stock restored")
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Error deleting sales order: {str(e)}")

# PDF imports
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
        
        # Add Company Logo - try multiple locations
        logo_paths = [
            '/app/company-logo.png',
            '/app/frontend/build/company-logo.png',
            os.path.join(os.path.dirname(__file__), '..', '..', 'company-logo.png')
        ]
        logo_path = None
        for path in logo_paths:
            if os.path.exists(path):
                logo_path = path
                break
        if logo_path:
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
        Please reference invoice number in payment description.<br/>
        <br/>
        <b><font color="green">After making payment, kindly send evidence of payment via WhatsApp to: +234 702 575 5406</font></b>
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
        
        # Name file after customer + date
        cust_name = (order.customer.name if order.customer else 'Unknown').replace(' ', '_')
        inv_date = (order.order_date or datetime.now(timezone.utc)).strftime('%Y-%m-%d')
        safe_filename = f"Invoice-{cust_name}-{inv_date}.pdf"
        return StreamingResponse(
            io.BytesIO(buffer.read()),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'}
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

        if order.status == 'cancelled':
            raise HTTPException(
                status_code=400, detail="Cannot process a cancelled order")

        # Use the warehouse from the sales order itself
        if order.warehouse_id:
            warehouse_result = await session.execute(
                select(Warehouse).where(Warehouse.id == order.warehouse_id)
            )
            warehouse = warehouse_result.scalars().first()
        else:
            # Fallback: get first active warehouse
            warehouse_result = await session.execute(
                select(Warehouse).where(Warehouse.is_active == True)
            )
            warehouse = warehouse_result.scalars().first()

        if not warehouse:
            raise HTTPException(status_code=404, detail="No warehouse found for this order")

        # Stock was already committed when the order was created (see
        # create_sales_order, which sets status='confirmed' and calls
        # deduct_stock_for_order). This endpoint used to deduct a SECOND time,
        # because its only guard was status == 'completed' and creation left
        # the order at 'confirmed'. Every processed order therefore shipped
        # twice as much stock as it sold, and deleting the order restored only
        # one deduction -- so the shortfall was permanent.
        #
        # Processing is now purely a state transition. It reports low stock so
        # the UI keeps working, but moves no inventory.
        low_stock_items = []

        for line in order.lines:
            # Get current stock level
            stock_result = await session.execute(
                select(StockLevel).where(
                    StockLevel.product_id == line.product_id,
                    StockLevel.warehouse_id == warehouse.id
                )
            )
            stock_level = stock_result.scalars().first()

            # Read-only: report anything now at or below its reorder level so
            # the caller still gets restock warnings. No mutation here.
            if (stock_level and line.product and line.product.reorder_level
                    and stock_level.current_stock <= line.product.reorder_level):
                low_stock_items.append({
                    'product_id': str(line.product_id),
                    'product_name': line.product.name,
                    'current_stock': stock_level.current_stock,
                    'reorder_level': line.product.reorder_level
                })


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

        # Marking an order paid means cash was received, so it must produce an
        # actual Payment row against an actual Invoice.
        #
        # This endpoint used to set payment_status = 'paid' and nothing else --
        # no Payment, no Invoice, not even an import of Invoice in this module.
        # profits.py and payment-tracking both read from payments/invoices, so
        # every sale settled through here was invisible to them: profit
        # reported zero on it while the debtors list still chased the customer
        # for money they had already handed over.
        invoice_id = await ensure_invoice_for_order(
            session, order_id=order.id, created_by=order.created_by)

        remaining = (await session.execute(
            text("""
                SELECT i.total_amount - COALESCE((
                           SELECT SUM(p.amount) FROM payments p
                            WHERE p.invoice_id = i.id), 0) AS remaining
                  FROM invoices i WHERE i.id = :iid
            """),
            {"iid": str(invoice_id)},
        )).scalar()

        remaining = money(remaining)
        if remaining > 0:
            await record_payment(
                session,
                invoice_id=invoice_id,
                amount=remaining,
                payment_method='unspecified',
                reference=f"MARK-PAID-{order.order_number}",
                notes="Settled in full via order mark-paid",
            )
        else:
            # Already fully covered by existing payments; just resync the
            # derived flags rather than inventing a zero-value payment.
            await recompute_invoice_paid(session, invoice_id)

        await session.commit()
        await session.refresh(order)
        await session.refresh(order)
        
        # Fire payment notification
        try:
            customer_name = order.customer.name if order.customer else "Customer"
            fire_notification(notify_payment_received(
                order_number=order.order_number,
                amount=float(order.total_amount or 0),
                customer_name=customer_name,
            ))
        except Exception as _notif_err:
            print(f"[sales] notification dispatch error: {_notif_err}")
        
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
@router.post('/orders/{order_id}/generate-receipt')
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
        
        if order.payment_status == 'unpaid':
            raise HTTPException(status_code=400, detail="Cannot generate receipt for unpaid order. Please mark as paid or record a partial payment first.")
        
        # Create PDF in memory
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=72, leftMargin=72, topMargin=72, bottomMargin=18)
        
        # Container for PDF elements
        elements = []
        styles = getSampleStyleSheet()
        
        # Add company logo - try multiple locations
        logo_paths = [
            '/app/company-logo.png',
            '/app/frontend/build/company-logo.png'
        ]
        logo_path = None
        for path in logo_paths:
            if os.path.exists(path):
                logo_path = path
                break
        if logo_path:
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
        payment_label = 'PAID' if order.payment_status == 'paid' else 'PARTIAL PAYMENT'
        receipt_info = f"""
        <b>Receipt No:</b> {order.order_number}<br/>
        <b>Date:</b> {order.payment_date.strftime('%Y-%m-%d %H:%M') if order.payment_date else datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}<br/>
        <b>Customer:</b> {order.customer.name}<br/>
        <b>Payment Status:</b> {payment_label}
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
        
        # WhatsApp payment evidence notice
        whatsapp_notice = Paragraph(
            '<b><font color="green">After making payment, kindly send evidence of payment via WhatsApp to: +234 702 575 5406</font></b>',
            styles['Normal']
        )
        elements.append(whatsapp_notice)
        elements.append(Spacer(1, 12))
        
        # Footer
        footer = Paragraph('Thank you for your business!<br/><i>This is a computer-generated receipt.</i>', styles['Normal'])
        elements.append(footer)
        
        # Build PDF
        doc.build(elements)
        buffer.seek(0)
        
        # Name file after customer + date
        cust_name = (order.customer.name if order.customer else 'Unknown').replace(' ', '_')
        rcpt_date = (order.payment_date or datetime.now(timezone.utc)).strftime('%Y-%m-%d')
        safe_filename = f"Receipt-{cust_name}-{rcpt_date}.pdf"
        return StreamingResponse(
            buffer,
            media_type='application/pdf',
            headers={'Content-Disposition': f'attachment; filename="{safe_filename}"'}
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating receipt: {str(e)}")


@router.get('/orders/{order_id}/invoice')
@router.post('/orders/{order_id}/generate-invoice')
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
        
        # Add company logo - try multiple locations
        logo_paths = [
            '/app/company-logo.png',
            '/app/frontend/build/company-logo.png'
        ]
        logo_path = None
        for path in logo_paths:
            if os.path.exists(path):
                logo_path = path
                break
        if logo_path:
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
        Account Name: BONNESANTE MEDICALS<br/>
        <br/>
        <b><font color="green">After making payment, kindly send evidence of payment via WhatsApp to: +234 702 575 5406</font></b>
        """
        elements.append(Paragraph(payment_terms, styles['Normal']))
        elements.append(Spacer(1, 20))
        
        # Footer
        footer = Paragraph('<i>This is a computer-generated invoice.</i>', styles['Normal'])
        elements.append(footer)
        
        # Build PDF
        doc.build(elements)
        buffer.seek(0)
        
        # Name file after customer + date
        cust_name = (order.customer.name if order.customer else 'Unknown').replace(' ', '_')
        inv_date = (order.order_date or datetime.now(timezone.utc)).strftime('%Y-%m-%d')
        safe_filename = f"Invoice-{cust_name}-{inv_date}.pdf"
        return StreamingResponse(
            buffer,
            media_type='application/pdf',
            headers={'Content-Disposition': f'attachment; filename="{safe_filename}"'}
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating invoice: {str(e)}")


def _thermal_styles():
    """Shared CSS for 80mm thermal printer: Georgia font, 12px, company branding."""
    return """
@page { size: 80mm auto; margin: 2mm; }
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: Georgia, 'Times New Roman', serif; font-size: 12px; width: 76mm; max-width: 76mm; margin: 0 auto; padding: 2mm; color: #000; }
.logo { text-align: center; margin-bottom: 4px; }
.logo img { max-width: 50mm; max-height: 20mm; }
.company-name { text-align: center; font-weight: bold; font-size: 14px; margin-bottom: 2px; }
.company-info { text-align: center; font-size: 10px; margin-bottom: 6px; line-height: 1.3; }
.doc-title { text-align: center; font-weight: bold; font-size: 14px; border-top: 2px solid #000; border-bottom: 2px solid #000; padding: 3px 0; margin-bottom: 6px; }
.info-section { font-size: 11px; margin-bottom: 6px; line-height: 1.5; }
.info-section strong { font-weight: bold; }
.separator { border-top: 1px dashed #000; margin: 4px 0; }
.items-table { width: 100%; border-collapse: collapse; font-size: 10px; margin-bottom: 4px; }
.items-table th { background: #333; color: #fff; padding: 2px 3px; text-align: left; font-size: 10px; }
.items-table td { padding: 2px 3px; border-bottom: 1px solid #ddd; }
.items-table .right { text-align: right; }
.total-row { font-weight: bold; font-size: 12px; border-top: 2px solid #000; padding-top: 3px; margin-top: 3px; text-align: right; }
.payment-info { font-size: 10px; margin-top: 6px; line-height: 1.4; }
.payment-info strong { font-weight: bold; }
.whatsapp { font-size: 10px; margin-top: 4px; font-weight: bold; }
.footer { text-align: center; font-size: 9px; margin-top: 6px; font-style: italic; border-top: 1px solid #000; padding-top: 3px; }
.status-paid { font-weight: bold; }
.status-unpaid { font-weight: bold; }
@media print { body { width: 80mm; } @page { size: 80mm auto; margin: 2mm; } }
"""


def _thermal_header():
    """Shared header HTML for thermal prints."""
    return """<div class="logo"><img src="/company-logo.png" onerror="this.style.display='none'" alt="Logo"></div>
<div class="company-name">BONNESANTE MEDICALS</div>
<div class="company-info">NO 6B PEACE AVE/17A ISUOFIA ST, FED HOUSING TRANS EKULU, ENUGU<br>Tel: +234 702 575 5406, +234 707 679 3866<br>Email: astrobsm@gmail.com</div>"""


@router.get('/orders/{order_id}/thermal-receipt')
@router.post('/orders/{order_id}/thermal-receipt')
async def thermal_receipt(order_id: UUID, session: AsyncSession = Depends(get_session)):
    """Generate thermal-printer-optimized HTML receipt (80mm, Georgia 12pt, logo)."""
    try:
        from fastapi.responses import HTMLResponse
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
        if order.payment_status == 'unpaid':
            raise HTTPException(status_code=400, detail="Cannot print receipt for unpaid order")

        payment_label = 'PAID' if order.payment_status == 'paid' else 'PARTIAL PAYMENT'
        items_rows = ""
        for line in order.lines:
            items_rows += f"""<tr>
                <td>{line.product.name}</td>
                <td class="right">{line.quantity}</td>
                <td class="right">&#8358;{float(line.unit_price):,.2f}</td>
                <td class="right">&#8358;{float(line.line_total):,.2f}</td>
            </tr>"""

        receipt_date = order.payment_date.strftime('%Y-%m-%d %H:%M') if order.payment_date else datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')

        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Receipt {order.order_number}</title>
<style>{_thermal_styles()}</style></head><body>
{_thermal_header()}
<div class="doc-title">PAYMENT RECEIPT</div>
<div class="info-section">
    <div><strong>Receipt #:</strong> {order.order_number}</div>
    <div><strong>Date:</strong> {receipt_date}</div>
    <div><strong>Customer:</strong> {order.customer.name}</div>
    <div><strong>Status:</strong> <span class="status-paid">{payment_label}</span></div>
</div>
<div class="separator"></div>
<table class="items-table">
    <thead><tr><th>Product</th><th>Qty</th><th>Price</th><th>Total</th></tr></thead>
    <tbody>{items_rows}</tbody>
</table>
<div class="total-row">TOTAL: &#8358;{float(order.total_amount):,.2f}</div>
<div class="separator"></div>
<div class="whatsapp">After payment, send evidence via WhatsApp: +234 702 575 5406</div>
<div class="footer">Thank you for your business!<br>AstroBSM - Bonnesante Medicals</div>
<script>window.onload=function(){{window.print();}}</script>
</body></html>"""
        return HTMLResponse(content=html)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating thermal receipt: {str(e)}")


@router.get('/orders/{order_id}/thermal-invoice')
@router.post('/orders/{order_id}/thermal-invoice')
async def thermal_invoice(order_id: UUID, session: AsyncSession = Depends(get_session)):
    """Generate thermal-printer-optimized HTML invoice (80mm, Georgia 12pt, logo)."""
    try:
        from fastapi.responses import HTMLResponse
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

        due_date = order.required_date.strftime('%Y-%m-%d') if order.required_date else 'Upon Receipt'
        payment_status_display = 'PAID' if order.payment_status == 'paid' else 'UNPAID'

        items_rows = ""
        for line in order.lines:
            items_rows += f"""<tr>
                <td>{line.product.name}</td>
                <td class="right">{line.quantity}</td>
                <td class="right">&#8358;{float(line.unit_price):,.2f}</td>
                <td class="right">&#8358;{float(line.line_total):,.2f}</td>
            </tr>"""

        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Invoice {order.order_number}</title>
<style>{_thermal_styles()}</style></head><body>
{_thermal_header()}
<div class="doc-title">INVOICE</div>
<div class="info-section">
    <div><strong>Invoice #:</strong> {order.order_number}</div>
    <div><strong>Date:</strong> {order.order_date.strftime('%Y-%m-%d')}</div>
    <div><strong>Due:</strong> {due_date}</div>
    <div><strong>Customer:</strong> {order.customer.name}</div>
    <div><strong>Status:</strong> <span class="status-unpaid">{payment_status_display}</span></div>
</div>
<div class="separator"></div>
<table class="items-table">
    <thead><tr><th>Product</th><th>Qty</th><th>Price</th><th>Total</th></tr></thead>
    <tbody>{items_rows}</tbody>
</table>
<div class="total-row">TOTAL DUE: &#8358;{float(order.total_amount):,.2f}</div>
<div class="separator"></div>
<div class="payment-info">
    <strong>Payment Terms:</strong><br>
    Payment is due upon receipt. Pay to:<br><br>
    <strong>ACCESS BANK NIG PLC</strong><br>
    Acct: 1379643548<br>
    Name: BONNESANTE MEDICALS<br><br>
    <strong>MONIEPOINT MFB</strong><br>
    Acct: 8259518195<br>
    Name: BONNESANTE MEDICALS
</div>
<div class="whatsapp">After payment, send evidence via WhatsApp: +234 702 575 5406</div>
<div class="footer">AstroBSM - Bonnesante Medicals<br>Computer-generated invoice</div>
<script>window.onload=function(){{window.print();}}</script>
</body></html>"""
        return HTMLResponse(content=html)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating thermal invoice: {str(e)}")
