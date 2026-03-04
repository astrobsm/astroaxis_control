"""
Payment Tracking & Reconciliation API
Unified payment system tied to invoices -> sales_orders -> customers
Tracks partial payments, balances, debt reminders, WhatsApp messages
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text, func
from app.database import get_session
from app.models import Invoice, InvoiceLine, Payment, SalesOrder, SalesOrderLine, Customer, Product
from uuid import UUID
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional
import uuid

router = APIRouter(prefix='/api/payment-tracking')


# ─── AUTO-CREATE INVOICE FROM SALES ORDER ────────────────────────────────────
@router.post('/invoices/from-order/{order_id}')
async def create_invoice_from_order(
    order_id: UUID,
    session: AsyncSession = Depends(get_session)
):
    """Auto-create an invoice from a sales order, copying all lines"""
    try:
        # Check if invoice already exists for this order
        existing = await session.execute(
            select(Invoice).where(Invoice.sales_order_id == order_id)
        )
        existing_inv = existing.scalars().first()
        if existing_inv:
            # Return existing invoice info
            paid_result = await session.execute(
                select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.invoice_id == existing_inv.id)
            )
            total_paid = paid_result.scalar()
            return {
                "message": "Invoice already exists for this order",
                "invoice": {
                    "id": str(existing_inv.id),
                    "invoice_number": existing_inv.invoice_number,
                    "total_amount": float(existing_inv.total_amount or 0),
                    "paid_amount": float(total_paid),
                    "balance": float((existing_inv.total_amount or 0) - total_paid),
                    "status": existing_inv.status,
                    "due_date": existing_inv.due_date.isoformat() if existing_inv.due_date else None,
                }
            }

        # Get the sales order with lines
        order_result = await session.execute(
            select(SalesOrder).where(SalesOrder.id == order_id)
        )
        order = order_result.scalars().first()
        if not order:
            raise HTTPException(status_code=404, detail="Sales order not found")

        lines_result = await session.execute(
            select(SalesOrderLine).where(SalesOrderLine.sales_order_id == order_id)
        )
        lines = lines_result.scalars().all()

        # Generate invoice number
        inv_number = f"INV-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"

        # Due date = 14 days from now (2 weeks)
        due_date = datetime.now(timezone.utc) + timedelta(days=14)

        # Create invoice
        invoice = Invoice(
            id=uuid.uuid4(),
            invoice_number=inv_number,
            customer_id=order.customer_id,
            sales_order_id=order.id,
            invoice_date=datetime.now(timezone.utc),
            due_date=due_date,
            total_amount=order.total_amount or 0,
            paid_amount=0,
            status='pending',
            notes=f"Auto-generated from order {order.order_number}"
        )
        session.add(invoice)
        await session.flush()

        # Copy order lines to invoice lines
        for line in lines:
            inv_line = InvoiceLine(
                id=uuid.uuid4(),
                invoice_id=invoice.id,
                product_id=line.product_id,
                quantity=line.quantity,
                unit_price=line.unit_price,
                line_total=line.line_total
            )
            session.add(inv_line)

        await session.commit()

        return {
            "message": f"Invoice {inv_number} created successfully",
            "invoice": {
                "id": str(invoice.id),
                "invoice_number": inv_number,
                "total_amount": float(invoice.total_amount or 0),
                "paid_amount": 0.0,
                "balance": float(invoice.total_amount or 0),
                "status": "pending",
                "due_date": due_date.isoformat(),
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Error creating invoice: {str(e)}")


# ─── LIST INVOICES ───────────────────────────────────────────────────────────
@router.get('/invoices')
async def list_invoices(
    customer_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_session)
):
    """List all invoices with payment summary"""
    try:
        filters = []
        params = {}
        if customer_id:
            filters.append("i.customer_id = :customer_id")
            params["customer_id"] = customer_id
        if status:
            filters.append("i.status = :status")
            params["status"] = status

        where_clause = "WHERE " + " AND ".join(filters) if filters else ""

        sql = text(f"""
            SELECT 
                i.id, i.invoice_number, i.customer_id, i.sales_order_id,
                i.invoice_date, i.due_date, i.total_amount, i.status, i.notes, i.created_at,
                c.name as customer_name, c.phone as customer_phone, c.email as customer_email,
                so.order_number,
                COALESCE(SUM(p.amount), 0) as total_paid
            FROM invoices i
            JOIN customers c ON c.id = i.customer_id
            LEFT JOIN sales_orders so ON so.id = i.sales_order_id
            LEFT JOIN payments p ON p.invoice_id = i.id
            {where_clause}
            GROUP BY i.id, i.invoice_number, i.customer_id, i.sales_order_id,
                     i.invoice_date, i.due_date, i.total_amount, i.status, i.notes, i.created_at,
                     c.name, c.phone, c.email, so.order_number
            ORDER BY i.created_at DESC
        """)
        result = await session.execute(sql, params)
        rows = result.fetchall()

        invoices = []
        for r in rows:
            total_amount = float(r.total_amount or 0)
            total_paid = float(r.total_paid or 0)
            balance = total_amount - total_paid
            invoices.append({
                "id": str(r.id),
                "invoice_number": r.invoice_number,
                "customer_id": str(r.customer_id),
                "customer_name": r.customer_name,
                "customer_phone": r.customer_phone,
                "customer_email": r.customer_email,
                "sales_order_id": str(r.sales_order_id) if r.sales_order_id else None,
                "order_number": r.order_number,
                "invoice_date": r.invoice_date.isoformat() if r.invoice_date else None,
                "due_date": r.due_date.isoformat() if r.due_date else None,
                "total_amount": total_amount,
                "total_paid": total_paid,
                "balance": balance,
                "status": r.status,
                "is_overdue": r.due_date and r.due_date < datetime.now(timezone.utc) and balance > 0,
                "notes": r.notes,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })

        return {"items": invoices, "total": len(invoices)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing invoices: {str(e)}")


# ─── GET INVOICE DETAIL WITH PAYMENT HISTORY ────────────────────────────────
@router.get('/invoices/{invoice_id}')
async def get_invoice_detail(
    invoice_id: UUID,
    session: AsyncSession = Depends(get_session)
):
    """Get full invoice detail with all payments, order lines, balance"""
    try:
        # Get invoice
        inv_result = await session.execute(
            select(Invoice).where(Invoice.id == invoice_id)
        )
        inv = inv_result.scalars().first()
        if not inv:
            raise HTTPException(status_code=404, detail="Invoice not found")

        # Customer
        cust_result = await session.execute(select(Customer).where(Customer.id == inv.customer_id))
        cust = cust_result.scalars().first()

        # Order
        order = None
        if inv.sales_order_id:
            order_result = await session.execute(select(SalesOrder).where(SalesOrder.id == inv.sales_order_id))
            order = order_result.scalars().first()

        # Invoice lines with product names
        lines_sql = text("""
            SELECT il.id, il.product_id, il.quantity, il.unit_price, il.line_total,
                   p.name as product_name, p.sku
            FROM invoice_lines il
            JOIN products p ON p.id = il.product_id
            WHERE il.invoice_id = :invoice_id
        """)
        lines_result = await session.execute(lines_sql, {"invoice_id": str(invoice_id)})
        lines = [
            {
                "id": str(r.id), "product_id": str(r.product_id),
                "product_name": r.product_name, "sku": r.sku,
                "quantity": float(r.quantity), "unit_price": float(r.unit_price),
                "line_total": float(r.line_total)
            } for r in lines_result.fetchall()
        ]

        # Payments
        payments_sql = text("""
            SELECT id, payment_method, amount, payment_date, reference, notes, created_at
            FROM payments WHERE invoice_id = :invoice_id
            ORDER BY payment_date ASC
        """)
        payments_result = await session.execute(payments_sql, {"invoice_id": str(invoice_id)})
        payments = []
        running_balance = float(inv.total_amount or 0)
        for r in payments_result.fetchall():
            amt = float(r.amount)
            running_balance -= amt
            payments.append({
                "id": str(r.id), "payment_method": r.payment_method,
                "amount": amt, "payment_date": r.payment_date.isoformat() if r.payment_date else None,
                "reference": r.reference, "notes": r.notes,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "running_balance": max(running_balance, 0),
            })

        total_paid = sum(p["amount"] for p in payments)
        balance = float(inv.total_amount or 0) - total_paid

        return {
            "invoice": {
                "id": str(inv.id), "invoice_number": inv.invoice_number,
                "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
                "due_date": inv.due_date.isoformat() if inv.due_date else None,
                "total_amount": float(inv.total_amount or 0),
                "total_paid": total_paid,
                "balance": max(balance, 0),
                "status": inv.status,
                "is_overdue": inv.due_date and inv.due_date < datetime.now(timezone.utc) and balance > 0,
                "notes": inv.notes,
            },
            "customer": {
                "id": str(cust.id) if cust else None,
                "name": cust.name if cust else "Unknown",
                "phone": cust.phone if cust else None,
                "email": cust.email if cust else None,
                "address": cust.address if cust else None,
            },
            "order": {
                "id": str(order.id) if order else None,
                "order_number": order.order_number if order else None,
                "order_date": order.order_date.isoformat() if order and order.order_date else None,
                "status": order.status if order else None,
            } if order else None,
            "lines": lines,
            "payments": payments,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting invoice detail: {str(e)}")


# ─── RECORD PAYMENT AGAINST INVOICE ─────────────────────────────────────────
@router.post('/invoices/{invoice_id}/payments')
async def record_payment(
    invoice_id: UUID,
    payment_data: dict,
    session: AsyncSession = Depends(get_session)
):
    """Record a payment against an invoice. Auto-calculates balance and updates statuses."""
    try:
        # Validate
        amount = float(payment_data.get("amount", 0))
        if amount <= 0:
            raise HTTPException(status_code=400, detail="Payment amount must be greater than zero")

        payment_method = payment_data.get("payment_method", "bank_transfer")
        reference = payment_data.get("reference", "")
        notes = payment_data.get("notes", "")
        payment_date_str = payment_data.get("payment_date")
        
        if payment_date_str:
            payment_date = datetime.fromisoformat(payment_date_str.replace('Z', '+00:00'))
        else:
            payment_date = datetime.now(timezone.utc)

        # Get invoice
        inv_result = await session.execute(select(Invoice).where(Invoice.id == invoice_id))
        inv = inv_result.scalars().first()
        if not inv:
            raise HTTPException(status_code=404, detail="Invoice not found")

        # Calculate current total paid
        paid_result = await session.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.invoice_id == invoice_id)
        )
        current_paid = float(paid_result.scalar())
        total_amount = float(inv.total_amount or 0)
        new_total_paid = current_paid + amount
        new_balance = total_amount - new_total_paid

        if new_balance < -0.01:
            raise HTTPException(
                status_code=400, 
                detail=f"Payment of {amount:,.2f} exceeds remaining balance of {(total_amount - current_paid):,.2f}"
            )

        # Create payment record
        payment = Payment(
            id=uuid.uuid4(),
            invoice_id=invoice_id,
            payment_method=payment_method,
            amount=Decimal(str(amount)),
            payment_date=payment_date,
            reference=reference,
            notes=notes,
        )
        session.add(payment)

        # Update invoice paid_amount and status
        inv.paid_amount = Decimal(str(new_total_paid))
        if new_balance <= 0.01:
            inv.status = 'paid'
        else:
            inv.status = 'partial'

        # Also update the linked sales order payment_status
        if inv.sales_order_id:
            order_result = await session.execute(
                select(SalesOrder).where(SalesOrder.id == inv.sales_order_id)
            )
            order = order_result.scalars().first()
            if order:
                if new_balance <= 0.01:
                    order.payment_status = 'paid'
                    order.payment_date = payment_date
                else:
                    order.payment_status = 'partial'

        await session.commit()

        return {
            "message": f"Payment of NGN {amount:,.2f} recorded successfully",
            "payment": {
                "id": str(payment.id),
                "amount": amount,
                "payment_date": payment_date.isoformat(),
                "payment_method": payment_method,
                "reference": reference,
            },
            "invoice_summary": {
                "total_amount": total_amount,
                "total_paid": new_total_paid,
                "balance": max(new_balance, 0),
                "status": inv.status,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Error recording payment: {str(e)}")


# ─── DELETE PAYMENT ──────────────────────────────────────────────────────────
@router.delete('/payments/{payment_id}')
async def delete_payment(
    payment_id: UUID,
    session: AsyncSession = Depends(get_session)
):
    """Delete a payment and recalculate invoice balance"""
    try:
        pay_result = await session.execute(select(Payment).where(Payment.id == payment_id))
        payment = pay_result.scalars().first()
        if not payment:
            raise HTTPException(status_code=404, detail="Payment not found")

        invoice_id = payment.invoice_id
        await session.delete(payment)

        # Recalculate invoice
        inv_result = await session.execute(select(Invoice).where(Invoice.id == invoice_id))
        inv = inv_result.scalars().first()
        if inv:
            paid_result = await session.execute(
                select(func.coalesce(func.sum(Payment.amount), 0))
                .where(Payment.invoice_id == invoice_id)
                .where(Payment.id != payment_id)
            )
            new_paid = float(paid_result.scalar())
            inv.paid_amount = Decimal(str(new_paid))
            balance = float(inv.total_amount or 0) - new_paid
            if balance <= 0.01:
                inv.status = 'paid'
            elif new_paid > 0:
                inv.status = 'partial'
            else:
                inv.status = 'pending'

            # Update linked sales order
            if inv.sales_order_id:
                order_result = await session.execute(
                    select(SalesOrder).where(SalesOrder.id == inv.sales_order_id)
                )
                order = order_result.scalars().first()
                if order:
                    if balance <= 0.01:
                        order.payment_status = 'paid'
                    elif new_paid > 0:
                        order.payment_status = 'partial'
                    else:
                        order.payment_status = 'unpaid'

        await session.commit()
        return {"message": "Payment deleted and balances recalculated"}
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Error deleting payment: {str(e)}")


# ─── DEBTORS DASHBOARD ──────────────────────────────────────────────────────
@router.get('/debtors')
async def get_debtors_dashboard(
    session: AsyncSession = Depends(get_session)
):
    """Get all customers with outstanding balances (debtors dashboard)"""
    try:
        sql = text("""
            SELECT 
                c.id as customer_id, c.name as customer_name, c.phone, c.email, c.address,
                COUNT(DISTINCT i.id) as total_invoices,
                COALESCE(SUM(i.total_amount), 0) as total_invoiced,
                COALESCE(SUM(
                    (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id)
                ), 0) as total_paid,
                MIN(i.invoice_date) as first_invoice_date,
                MAX(i.invoice_date) as last_invoice_date,
                MIN(i.due_date) as earliest_due_date
            FROM customers c
            JOIN invoices i ON i.customer_id = c.id
            WHERE i.status != 'cancelled'
            GROUP BY c.id, c.name, c.phone, c.email, c.address
            HAVING COALESCE(SUM(i.total_amount), 0) - COALESCE(SUM(
                (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id)
            ), 0) > 0.01
            ORDER BY (COALESCE(SUM(i.total_amount), 0) - COALESCE(SUM(
                (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id)
            ), 0)) DESC
        """)
        result = await session.execute(sql)
        rows = result.fetchall()

        debtors = []
        total_outstanding = 0
        for r in rows:
            total_inv = float(r.total_invoiced or 0)
            total_pd = float(r.total_paid or 0)
            balance = total_inv - total_pd
            total_outstanding += balance

            is_overdue = r.earliest_due_date and r.earliest_due_date < datetime.now(timezone.utc)
            days_overdue = (datetime.now(timezone.utc) - r.earliest_due_date).days if is_overdue else 0

            debtors.append({
                "customer_id": str(r.customer_id),
                "customer_name": r.customer_name,
                "phone": r.phone,
                "email": r.email,
                "address": r.address,
                "total_invoices": r.total_invoices,
                "total_invoiced": total_inv,
                "total_paid": total_pd,
                "balance": balance,
                "is_overdue": bool(is_overdue),
                "days_overdue": max(days_overdue, 0),
                "earliest_due_date": r.earliest_due_date.isoformat() if r.earliest_due_date else None,
                "last_invoice_date": r.last_invoice_date.isoformat() if r.last_invoice_date else None,
            })

        return {
            "debtors": debtors,
            "total_debtors": len(debtors),
            "total_outstanding": total_outstanding,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching debtors: {str(e)}")


# ─── CUSTOMER DEBT DETAIL ───────────────────────────────────────────────────
@router.get('/debtors/{customer_id}')
async def get_customer_debt_detail(
    customer_id: UUID,
    session: AsyncSession = Depends(get_session)
):
    """Full debt detail for a customer: all invoices, all payments, balances, timeline"""
    try:
        # Customer info
        cust_result = await session.execute(select(Customer).where(Customer.id == customer_id))
        cust = cust_result.scalars().first()
        if not cust:
            raise HTTPException(status_code=404, detail="Customer not found")

        # All invoices for this customer
        inv_sql = text("""
            SELECT i.id, i.invoice_number, i.sales_order_id, i.invoice_date, i.due_date,
                   i.total_amount, i.status, i.notes,
                   so.order_number, so.order_date,
                   COALESCE(SUM(p.amount), 0) as total_paid
            FROM invoices i
            LEFT JOIN sales_orders so ON so.id = i.sales_order_id
            LEFT JOIN payments p ON p.invoice_id = i.id
            WHERE i.customer_id = :customer_id AND i.status != 'cancelled'
            GROUP BY i.id, i.invoice_number, i.sales_order_id, i.invoice_date, i.due_date,
                     i.total_amount, i.status, i.notes, so.order_number, so.order_date
            ORDER BY i.invoice_date DESC
        """)
        inv_result = await session.execute(inv_sql, {"customer_id": str(customer_id)})
        invoices = []
        grand_total = 0
        grand_paid = 0

        for r in inv_result.fetchall():
            total = float(r.total_amount or 0)
            paid = float(r.total_paid or 0)
            balance = total - paid
            grand_total += total
            grand_paid += paid

            invoices.append({
                "id": str(r.id),
                "invoice_number": r.invoice_number,
                "order_number": r.order_number,
                "order_date": r.order_date.isoformat() if r.order_date else None,
                "invoice_date": r.invoice_date.isoformat() if r.invoice_date else None,
                "due_date": r.due_date.isoformat() if r.due_date else None,
                "total_amount": total,
                "total_paid": paid,
                "balance": max(balance, 0),
                "status": r.status,
                "is_overdue": r.due_date and r.due_date < datetime.now(timezone.utc) and balance > 0,
            })

        # All payments by this customer (across all invoices)
        pay_sql = text("""
            SELECT p.id, p.invoice_id, p.payment_method, p.amount, p.payment_date, 
                   p.reference, p.notes, i.invoice_number
            FROM payments p
            JOIN invoices i ON i.id = p.invoice_id
            WHERE i.customer_id = :customer_id
            ORDER BY p.payment_date ASC
        """)
        pay_result = await session.execute(pay_sql, {"customer_id": str(customer_id)})
        payments = [
            {
                "id": str(r.id), "invoice_id": str(r.invoice_id),
                "invoice_number": r.invoice_number,
                "payment_method": r.payment_method, "amount": float(r.amount),
                "payment_date": r.payment_date.isoformat() if r.payment_date else None,
                "reference": r.reference, "notes": r.notes,
            } for r in pay_result.fetchall()
        ]

        grand_balance = grand_total - grand_paid

        return {
            "customer": {
                "id": str(cust.id), "name": cust.name, "phone": cust.phone,
                "email": cust.email, "address": cust.address,
            },
            "summary": {
                "total_invoiced": grand_total,
                "total_paid": grand_paid,
                "total_balance": max(grand_balance, 0),
                "invoice_count": len(invoices),
                "payment_count": len(payments),
            },
            "invoices": invoices,
            "payments": payments,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching customer debt detail: {str(e)}")


# ─── WHATSAPP REMINDER MESSAGE GENERATOR ────────────────────────────────────
@router.get('/debtors/{customer_id}/reminder')
async def generate_reminder_message(
    customer_id: UUID,
    session: AsyncSession = Depends(get_session)
):
    """Generate a WhatsApp debt reminder message with full breakdown"""
    try:
        # Customer
        cust_result = await session.execute(select(Customer).where(Customer.id == customer_id))
        cust = cust_result.scalars().first()
        if not cust:
            raise HTTPException(status_code=404, detail="Customer not found")

        # Get unpaid invoices with payment totals
        inv_sql = text("""
            SELECT i.id, i.invoice_number, i.invoice_date, i.due_date,
                   i.total_amount, so.order_number, so.order_date,
                   COALESCE(SUM(p.amount), 0) as total_paid
            FROM invoices i
            LEFT JOIN sales_orders so ON so.id = i.sales_order_id
            LEFT JOIN payments p ON p.invoice_id = i.id
            WHERE i.customer_id = :customer_id AND i.status != 'cancelled' AND i.status != 'paid'
            GROUP BY i.id, i.invoice_number, i.invoice_date, i.due_date,
                     i.total_amount, so.order_number, so.order_date
            HAVING (i.total_amount - COALESCE(SUM(p.amount), 0)) > 0.01
            ORDER BY i.invoice_date ASC
        """)
        inv_result = await session.execute(inv_sql, {"customer_id": str(customer_id)})
        rows = inv_result.fetchall()

        if not rows:
            return {
                "message": "No outstanding debt for this customer",
                "whatsapp_message": None,
                "whatsapp_url": None,
            }

        # Build message
        lines = []
        lines.append(f"Dear {cust.name},")
        lines.append("")
        lines.append("This is a friendly reminder regarding your outstanding balance with *Bonnesante Medicals*.")
        lines.append("")
        lines.append("*OUTSTANDING INVOICES:*")
        lines.append("─────────────────────")

        grand_total = 0
        grand_paid = 0
        for r in rows:
            total = float(r.total_amount or 0)
            paid = float(r.total_paid or 0)
            balance = total - paid
            grand_total += total
            grand_paid += paid

            inv_date = r.invoice_date.strftime('%d/%m/%Y') if r.invoice_date else 'N/A'
            due_date = r.due_date.strftime('%d/%m/%Y') if r.due_date else 'N/A'

            lines.append(f"")
            lines.append(f"Invoice: *{r.invoice_number}*")
            if r.order_number:
                lines.append(f"Order: {r.order_number}")
            lines.append(f"Date: {inv_date}")
            lines.append(f"Due Date: {due_date}")
            lines.append(f"Total Amount: NGN {total:,.2f}")
            if paid > 0:
                lines.append(f"Amount Paid: NGN {paid:,.2f}")
            lines.append(f"*Balance Due: NGN {balance:,.2f}*")
            lines.append("─────────────────────")

        grand_balance = grand_total - grand_paid
        lines.append("")
        lines.append(f"*TOTAL BALANCE DUE: NGN {grand_balance:,.2f}*")
        lines.append("")
        lines.append("Please make payment to:")
        lines.append("*Moniepoint Microfinance Bank*")
        lines.append("Account Name: Bonnesante Medicals")
        lines.append("Account Number: 8259518195")
        lines.append("")
        lines.append("*Access Bank Nigeria*")
        lines.append("Account Name: Bonnesante Medicals")
        lines.append("Account Number: 1379643548")
        lines.append("")
        lines.append("Please reference the invoice number in your payment description.")
        lines.append("")
        lines.append("Thank you for your patronage.")
        lines.append("Bonnesante Medicals")
        lines.append("Tel: +234 707 679 3866, +234 901 283 5413")

        message_text = "\n".join(lines)

        # Generate WhatsApp URL
        phone = cust.phone
        whatsapp_url = None
        if phone:
            # Clean phone number
            clean_phone = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
            if clean_phone.startswith("0"):
                clean_phone = "234" + clean_phone[1:]
            elif not clean_phone.startswith("+") and not clean_phone.startswith("234"):
                clean_phone = "234" + clean_phone
            clean_phone = clean_phone.replace("+", "")
            
            import urllib.parse
            encoded_msg = urllib.parse.quote(message_text)
            whatsapp_url = f"https://wa.me/{clean_phone}?text={encoded_msg}"

        return {
            "customer_name": cust.name,
            "customer_phone": cust.phone,
            "total_balance": grand_balance,
            "invoice_count": len(rows),
            "whatsapp_message": message_text,
            "whatsapp_url": whatsapp_url,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating reminder: {str(e)}")


# ─── OVERDUE REMINDERS (auto-generated for invoices 14+ days old) ────────────
@router.get('/reminders')
async def get_overdue_reminders(
    session: AsyncSession = Depends(get_session)
):
    """Get all customers with overdue invoices (due date passed) for reminder sending"""
    try:
        sql = text("""
            SELECT 
                c.id as customer_id, c.name as customer_name, c.phone, c.email,
                COUNT(DISTINCT i.id) as overdue_invoices,
                COALESCE(SUM(i.total_amount), 0) as total_invoiced,
                COALESCE(SUM(
                    (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id)
                ), 0) as total_paid,
                MIN(i.due_date) as earliest_due_date
            FROM customers c
            JOIN invoices i ON i.customer_id = c.id
            WHERE i.status NOT IN ('cancelled', 'paid')
              AND i.due_date < NOW()
              AND (i.total_amount - COALESCE(
                  (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id)
              , 0)) > 0.01
            GROUP BY c.id, c.name, c.phone, c.email
            ORDER BY (COALESCE(SUM(i.total_amount), 0) - COALESCE(SUM(
                (SELECT COALESCE(SUM(p.amount), 0) FROM payments p WHERE p.invoice_id = i.id)
            ), 0)) DESC
        """)
        result = await session.execute(sql)
        rows = result.fetchall()

        reminders = []
        for r in rows:
            total = float(r.total_invoiced or 0)
            paid = float(r.total_paid or 0)
            balance = total - paid
            days_overdue = (datetime.now(timezone.utc) - r.earliest_due_date).days if r.earliest_due_date else 0

            reminders.append({
                "customer_id": str(r.customer_id),
                "customer_name": r.customer_name,
                "phone": r.phone,
                "email": r.email,
                "overdue_invoices": r.overdue_invoices,
                "total_invoiced": total,
                "total_paid": paid,
                "balance": balance,
                "days_overdue": max(days_overdue, 0),
            })

        return {"reminders": reminders, "total": len(reminders)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching reminders: {str(e)}")


# ─── PAYMENT RECONCILIATION SUMMARY ─────────────────────────────────────────
@router.get('/reconciliation')
async def payment_reconciliation(
    session: AsyncSession = Depends(get_session)
):
    """Overall payment reconciliation summary"""
    try:
        sql = text("""
            SELECT
                COUNT(DISTINCT i.id) as total_invoices,
                COALESCE(SUM(DISTINCT i.total_amount), 0) as grand_total_invoiced,
                (SELECT COALESCE(SUM(p.amount), 0) FROM payments p 
                 JOIN invoices inv ON inv.id = p.invoice_id WHERE inv.status != 'cancelled') as grand_total_paid,
                COUNT(DISTINCT CASE WHEN i.status = 'paid' THEN i.id END) as fully_paid_invoices,
                COUNT(DISTINCT CASE WHEN i.status = 'partial' THEN i.id END) as partially_paid_invoices,
                COUNT(DISTINCT CASE WHEN i.status = 'pending' THEN i.id END) as unpaid_invoices,
                COUNT(DISTINCT CASE WHEN i.status NOT IN ('cancelled', 'paid') 
                    AND i.due_date < NOW() THEN i.id END) as overdue_invoices
            FROM invoices i
            WHERE i.status != 'cancelled'
        """)
        result = await session.execute(sql)
        r = result.fetchone()

        total_invoiced = float(r.grand_total_invoiced or 0)
        total_paid = float(r.grand_total_paid or 0)

        return {
            "total_invoices": r.total_invoices or 0,
            "total_invoiced": total_invoiced,
            "total_paid": total_paid,
            "total_outstanding": total_invoiced - total_paid,
            "fully_paid": r.fully_paid_invoices or 0,
            "partially_paid": r.partially_paid_invoices or 0,
            "unpaid": r.unpaid_invoices or 0,
            "overdue": r.overdue_invoices or 0,
            "collection_rate": round((total_paid / total_invoiced * 100) if total_invoiced > 0 else 0, 1),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching reconciliation: {str(e)}")
