"""
HR / Customer Care Module API
- Read-only staff overview with performance data
- Read-only product catalog with prices
- Staff performance summary (attendance + production)
- Create sales invoices (uses existing sales order flow)
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Optional
from datetime import date, timedelta

from app.db import get_session

router = APIRouter(prefix='/api/hr-customercare', tags=['HR & Customer Care'])


# ======================== DASHBOARD ========================

@router.get('/dashboard')
async def hr_dashboard(session: AsyncSession = Depends(get_session)):
    """HR/Customer Care dashboard with key metrics."""
    today = date.today()
    
    # Total staff and active count
    r1 = await session.execute(text("SELECT COUNT(*), SUM(CASE WHEN is_active THEN 1 ELSE 0 END) FROM staff"))
    staff_row = r1.fetchone()
    total_staff = staff_row[0]
    active_staff = staff_row[1] or 0

    # Today's attendance
    r2 = await session.execute(text(
        "SELECT COUNT(DISTINCT staff_id) FROM attendance WHERE date = :today"
    ), {"today": today})
    today_attendance = r2.scalar_one()

    # Total products
    r3 = await session.execute(text("SELECT COUNT(*) FROM products"))
    total_products = r3.scalar_one()

    # Total customers
    r4 = await session.execute(text("SELECT COUNT(*) FROM customers"))
    total_customers = r4.scalar_one()

    # Orders this month
    month_start = today.replace(day=1)
    r5 = await session.execute(text(
        "SELECT COUNT(*), COALESCE(SUM(total_amount),0) FROM sales_orders WHERE created_at >= :ms"
    ), {"ms": month_start})
    orders_row = r5.fetchone()
    
    # Pending orders
    r6 = await session.execute(text(
        "SELECT COUNT(*) FROM sales_orders WHERE status IN ('pending','processing')"
    ))
    pending_orders = r6.scalar_one()

    # Upcoming birthdays (7 days)
    r7 = await session.execute(text("""
        SELECT COUNT(*) FROM staff 
        WHERE date_of_birth IS NOT NULL 
        AND (
            (EXTRACT(MONTH FROM date_of_birth) = EXTRACT(MONTH FROM CURRENT_DATE) 
             AND EXTRACT(DAY FROM date_of_birth) >= EXTRACT(DAY FROM CURRENT_DATE)
             AND EXTRACT(DAY FROM date_of_birth) <= EXTRACT(DAY FROM CURRENT_DATE) + 7)
            OR
            (EXTRACT(MONTH FROM date_of_birth) = EXTRACT(MONTH FROM CURRENT_DATE + INTERVAL '7 days')
             AND EXTRACT(DAY FROM date_of_birth) <= EXTRACT(DAY FROM CURRENT_DATE + INTERVAL '7 days'))
        )
    """))
    upcoming_birthdays = r7.scalar_one()

    return {
        "total_staff": total_staff,
        "active_staff": int(active_staff),
        "today_attendance": today_attendance,
        "total_products": total_products,
        "total_customers": total_customers,
        "month_orders": orders_row[0],
        "month_revenue": float(orders_row[1]),
        "pending_orders": pending_orders,
        "upcoming_birthdays": upcoming_birthdays,
    }


# ======================== STAFF OVERVIEW (READ-ONLY) ========================

@router.get('/staff')
async def list_all_staff(session: AsyncSession = Depends(get_session)):
    """Read-only list of all staff with key details."""
    result = await session.execute(text("""
        SELECT id, employee_id, first_name, last_name, position, phone, is_active,
               date_of_birth, hourly_rate, bank_name, bank_account_number
        FROM staff ORDER BY first_name, last_name
    """))
    items = []
    for r in result.fetchall():
        items.append({
            "id": str(r.id), "employee_id": r.employee_id,
            "first_name": r.first_name, "last_name": r.last_name,
            "name": f"{r.first_name} {r.last_name}",
            "position": r.position or '', "phone": r.phone or '',
            "is_active": r.is_active, "date_of_birth": str(r.date_of_birth) if r.date_of_birth else '',
            "hourly_rate": float(r.hourly_rate or 0),
            "bank_name": r.bank_name or '', "bank_account_number": r.bank_account_number or '',
        })
    return {"items": items, "total": len(items)}


# ======================== STAFF PERFORMANCE ========================

@router.get('/staff-performance')
async def staff_performance(days: int = Query(30, ge=1, le=365), session: AsyncSession = Depends(get_session)):
    """Staff performance summary over a period."""
    since = date.today() - timedelta(days=days)
    
    result = await session.execute(text("""
        SELECT s.id, s.employee_id, s.first_name, s.last_name, s.position,
            COUNT(DISTINCT a.date) as days_present,
            COALESCE(SUM(
                CASE WHEN a.clock_out IS NOT NULL THEN 
                    EXTRACT(EPOCH FROM (a.clock_out - a.clock_in)) / 3600.0 
                    ELSE 0 END
            ), 0) as total_hours,
            (SELECT COUNT(*) FROM production_completions pc 
             WHERE pc.staff_id = s.id AND pc.production_date >= :since) as production_tasks,
            (SELECT COALESCE(SUM(pc.total_cost), 0) FROM production_completions pc 
             WHERE pc.staff_id = s.id AND pc.production_date >= :since) as production_value
        FROM staff s
        LEFT JOIN attendance a ON s.id = a.staff_id AND a.date >= :since
        WHERE s.is_active = true
        GROUP BY s.id, s.employee_id, s.first_name, s.last_name, s.position
        ORDER BY days_present DESC, total_hours DESC
    """), {"since": since})
    
    items = []
    for r in result.fetchall():
        items.append({
            "id": str(r.id), "employee_id": r.employee_id,
            "name": f"{r.first_name} {r.last_name}", "position": r.position or '',
            "days_present": r.days_present,
            "total_hours": round(float(r.total_hours), 2),
            "production_tasks": r.production_tasks,
            "production_value": float(r.production_value),
            "avg_hours_per_day": round(float(r.total_hours) / max(r.days_present, 1), 2),
        })
    return {"items": items, "period_days": days, "since": str(since)}


# ======================== ATTENDANCE LOG ========================

@router.get('/attendance-log')
async def attendance_log(days: int = Query(7, ge=1, le=90), session: AsyncSession = Depends(get_session)):
    """Recent attendance records for HR review."""
    since = date.today() - timedelta(days=days)
    result = await session.execute(text("""
        SELECT a.*, s.first_name, s.last_name, s.employee_id
        FROM attendance a
        JOIN staff s ON a.staff_id = s.id
        WHERE a.date >= :since
        ORDER BY a.date DESC, a.clock_in DESC
    """), {"since": since})
    items = []
    for r in result.fetchall():
        hours = 0
        if r.clock_out:
            hours = round((r.clock_out - r.clock_in).total_seconds() / 3600, 2)
        items.append({
            "id": str(r.id), "employee_id": r.employee_id,
            "name": f"{r.first_name} {r.last_name}",
            "date": str(r.date),
            "clock_in": str(r.clock_in) if r.clock_in else '',
            "clock_out": str(r.clock_out) if r.clock_out else 'Still working',
            "hours_worked": hours,
        })
    return {"items": items, "total": len(items)}


# ======================== PRODUCTS CATALOG (READ-ONLY) ========================

@router.get('/products-catalog')
async def view_products(session: AsyncSession = Depends(get_session)):
    """Read-only product catalog with pricing for HR/Customer Care."""
    result = await session.execute(text("""
        SELECT p.id, p.name, p.sku, p.description, p.category,
            pp.unit, pp.selling_price, pp.wholesale_price, pp.distributor_price
        FROM products p
        LEFT JOIN product_pricing pp ON p.id = pp.product_id
        ORDER BY p.name
    """))
    products = {}
    for r in result.fetchall():
        pid = str(r.id)
        if pid not in products:
            products[pid] = {
                "id": pid, "name": r.name, "sku": r.sku or '',
                "description": r.description or '', "category": r.category or '',
                "pricing": []
            }
        if r.unit:
            products[pid]["pricing"].append({
                "unit": r.unit,
                "selling_price": float(r.selling_price or 0),
                "wholesale_price": float(r.wholesale_price or 0),
                "distributor_price": float(r.distributor_price or 0),
            })
    return {"items": list(products.values()), "total": len(products)}


# ======================== CUSTOMERS LIST ========================

@router.get('/customers')
async def list_customers(session: AsyncSession = Depends(get_session)):
    """List all customers for order placement."""
    result = await session.execute(text("""
        SELECT id, name, email, phone, address, created_at
        FROM customers ORDER BY name
    """))
    items = []
    for r in result.fetchall():
        items.append({
            "id": str(r.id), "name": r.name, "email": r.email or '',
            "phone": r.phone or '', "address": r.address or '',
            "created_at": str(r.created_at),
        })
    return {"items": items, "total": len(items)}


# ======================== SALES ORDERS VIEW ========================

@router.get('/sales-orders')
async def view_sales_orders(page: int = Query(1, ge=1), size: int = Query(50),
                            status: Optional[str] = None,
                            session: AsyncSession = Depends(get_session)):
    """View all sales orders for HR/Customer Care."""
    where = ""
    params = {"limit": size, "offset": (page - 1) * size}
    if status:
        where = "WHERE so.status = :status"
        params["status"] = status
    count_r = await session.execute(text(f"SELECT COUNT(*) FROM sales_orders so {where}"), params)
    total = count_r.scalar_one()
    result = await session.execute(text(f"""
        SELECT so.id, so.order_number, so.customer_id, c.name as customer_name,
            so.warehouse_id, so.total_amount, so.status, so.payment_status, so.created_at
        FROM sales_orders so
        LEFT JOIN customers c ON so.customer_id = c.id
        {where}
        ORDER BY so.created_at DESC
        LIMIT :limit OFFSET :offset
    """), params)
    items = []
    for r in result.fetchall():
        items.append({
            "id": str(r.id), "order_number": r.order_number or '',
            "customer_name": r.customer_name or 'Unknown',
            "total_amount": float(r.total_amount or 0),
            "status": r.status, "payment_status": r.payment_status or 'unpaid',
            "created_at": str(r.created_at),
        })
    return {"items": items, "total": total}
