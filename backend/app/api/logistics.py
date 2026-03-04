"""
Logistics & Delivery Tracking Module
- Sales officers log deliveries with items, location, dates, times, transport costs
- Track delivery costs and items delivered
- Analytics and summary reports
"""
from uuid import UUID
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.db import get_session

router = APIRouter(prefix="/api/logistics", tags=["Logistics"])

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS logistics_deliveries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    delivery_number VARCHAR(64) UNIQUE NOT NULL,
    sales_order_id UUID,
    sales_officer VARCHAR(255) NOT NULL,
    staff_id UUID,
    customer_name VARCHAR(255) NOT NULL,
    customer_phone VARCHAR(50),
    delivery_address TEXT NOT NULL,
    city VARCHAR(100),
    state VARCHAR(100),
    landmark TEXT,
    delivery_date DATE NOT NULL DEFAULT CURRENT_DATE,
    departure_time TIME,
    arrival_time TIME,
    transport_mode VARCHAR(50) DEFAULT 'vehicle',
    vehicle_details VARCHAR(255),
    driver_name VARCHAR(255),
    driver_phone VARCHAR(50),
    transport_cost NUMERIC(18,2) NOT NULL DEFAULT 0,
    additional_charges NUMERIC(18,2) DEFAULT 0,
    total_cost NUMERIC(18,2) NOT NULL DEFAULT 0,
    payment_method VARCHAR(50),
    payment_reference VARCHAR(255),
    status VARCHAR(30) NOT NULL DEFAULT 'pending',
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS logistics_delivery_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    delivery_id UUID NOT NULL REFERENCES logistics_deliveries(id) ON DELETE CASCADE,
    product_id UUID,
    product_name VARCHAR(255) NOT NULL,
    sku VARCHAR(100),
    quantity NUMERIC(18,6) NOT NULL DEFAULT 1,
    unit VARCHAR(50) DEFAULT 'each',
    weight_kg NUMERIC(10,3),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ld_officer ON logistics_deliveries(sales_officer);
CREATE INDEX IF NOT EXISTS idx_ld_date ON logistics_deliveries(delivery_date);
CREATE INDEX IF NOT EXISTS idx_ld_status ON logistics_deliveries(status);
CREATE INDEX IF NOT EXISTS idx_ld_customer ON logistics_deliveries(customer_name);
"""


@router.on_event("startup")
async def init_logistics_tables():
    from app.db import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        await session.execute(text(CREATE_TABLES_SQL))
        await session.commit()
        print("Logistics tables ready")


# ─── CREATE DELIVERY ─────────────────────────────────────────────────────────

@router.post('/deliveries')
async def create_delivery(data: dict, session: AsyncSession = Depends(get_session)):
    """Log a new delivery by a sales officer."""
    try:
        del_num = f"DEL-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"
        transport = float(data.get('transport_cost', 0))
        additional = float(data.get('additional_charges', 0))
        total = transport + additional

        sql = text("""
            INSERT INTO logistics_deliveries
                (delivery_number, sales_order_id, sales_officer, staff_id,
                 customer_name, customer_phone, delivery_address, city, state, landmark,
                 delivery_date, departure_time, arrival_time,
                 transport_mode, vehicle_details, driver_name, driver_phone,
                 transport_cost, additional_charges, total_cost,
                 payment_method, payment_reference, status, notes)
            VALUES
                (:dn, :soid, :so, :sid, :cn, :cp, :addr, :city, :state, :lm,
                 :dd, :dept, :arrt, :tm, :vd, :drn, :drp,
                 :tc, :ac, :tot, :pm, :pr, :status, :notes)
            RETURNING id, delivery_number
        """)
        result = await session.execute(sql, {
            'dn': del_num,
            'soid': data.get('sales_order_id') or None,
            'so': data.get('sales_officer', ''),
            'sid': data.get('staff_id') or None,
            'cn': data.get('customer_name', ''),
            'cp': data.get('customer_phone', ''),
            'addr': data.get('delivery_address', ''),
            'city': data.get('city', ''),
            'state': data.get('state', ''),
            'lm': data.get('landmark', ''),
            'dd': data.get('delivery_date', datetime.now(timezone.utc).date().isoformat()),
            'dept': data.get('departure_time') or None,
            'arrt': data.get('arrival_time') or None,
            'tm': data.get('transport_mode', 'vehicle'),
            'vd': data.get('vehicle_details', ''),
            'drn': data.get('driver_name', ''),
            'drp': data.get('driver_phone', ''),
            'tc': transport, 'ac': additional, 'tot': total,
            'pm': data.get('payment_method', ''),
            'pr': data.get('payment_reference', ''),
            'status': data.get('status', 'completed'),
            'notes': data.get('notes', '')
        })
        row = result.fetchone()
        del_id = str(row.id)

        items = data.get('items', [])
        for it in items:
            await session.execute(text("""
                INSERT INTO logistics_delivery_items
                    (delivery_id, product_id, product_name, sku, quantity, unit, weight_kg, notes)
                VALUES (:did, :pid, :pn, :sku, :qty, :unit, :wt, :notes)
            """), {
                'did': del_id,
                'pid': it.get('product_id') or None,
                'pn': it.get('product_name', ''),
                'sku': it.get('sku', ''),
                'qty': float(it.get('quantity', 1)),
                'unit': it.get('unit', 'each'),
                'wt': float(it.get('weight_kg', 0)) if it.get('weight_kg') else None,
                'notes': it.get('notes', '')
            })

        await session.commit()
        return {
            "message": f"Delivery {del_num} logged",
            "delivery_number": del_num, "id": del_id,
            "total_cost": total
        }
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ─── LIST DELIVERIES ─────────────────────────────────────────────────────────

@router.get('/deliveries')
async def list_deliveries(
    sales_officer: str = None,
    status: str = None,
    date_from: str = None,
    date_to: str = None,
    session: AsyncSession = Depends(get_session)
):
    """List all deliveries with optional filters."""
    try:
        where = []
        params = {}
        if sales_officer:
            where.append("ld.sales_officer ILIKE :so")
            params['so'] = f"%{sales_officer}%"
        if status:
            where.append("ld.status = :status")
            params['status'] = status
        if date_from:
            where.append("ld.delivery_date >= :df")
            params['df'] = date_from
        if date_to:
            where.append("ld.delivery_date <= :dt")
            params['dt'] = date_to
        where_clause = " AND ".join(where) if where else "1=1"

        sql = text(f"""
            SELECT ld.*,
                (SELECT COUNT(*) FROM logistics_delivery_items WHERE delivery_id = ld.id) as item_count
            FROM logistics_deliveries ld
            WHERE {where_clause}
            ORDER BY ld.delivery_date DESC, ld.created_at DESC
        """)
        result = await session.execute(sql, params)
        items = []
        for r in result.fetchall():
            items.append({
                "id": str(r.id), "delivery_number": r.delivery_number,
                "sales_officer": r.sales_officer,
                "customer_name": r.customer_name,
                "customer_phone": r.customer_phone or '',
                "delivery_address": r.delivery_address,
                "city": r.city or '', "state": r.state or '',
                "delivery_date": str(r.delivery_date) if r.delivery_date else None,
                "departure_time": str(r.departure_time) if r.departure_time else None,
                "arrival_time": str(r.arrival_time) if r.arrival_time else None,
                "transport_mode": r.transport_mode or 'vehicle',
                "transport_cost": float(r.transport_cost or 0),
                "additional_charges": float(r.additional_charges or 0),
                "total_cost": float(r.total_cost or 0),
                "status": r.status,
                "item_count": r.item_count,
                "created_at": r.created_at.isoformat() if r.created_at else None
            })
        return {"items": items, "total": len(items)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── GET DELIVERY DETAIL ─────────────────────────────────────────────────────

@router.get('/deliveries/{delivery_id}')
async def get_delivery(delivery_id: UUID, session: AsyncSession = Depends(get_session)):
    """Get full delivery detail with items."""
    try:
        sql = text("SELECT * FROM logistics_deliveries WHERE id = :id")
        result = await session.execute(sql, {"id": str(delivery_id)})
        r = result.fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="Delivery not found")

        items_sql = text("SELECT * FROM logistics_delivery_items WHERE delivery_id = :did ORDER BY created_at")
        items = []
        for it in (await session.execute(items_sql, {"did": str(delivery_id)})).fetchall():
            items.append({
                "id": str(it.id),
                "product_id": str(it.product_id) if it.product_id else None,
                "product_name": it.product_name,
                "sku": it.sku or '',
                "quantity": float(it.quantity),
                "unit": it.unit,
                "weight_kg": float(it.weight_kg) if it.weight_kg else None,
                "notes": it.notes or ''
            })

        return {
            "id": str(r.id), "delivery_number": r.delivery_number,
            "sales_order_id": str(r.sales_order_id) if r.sales_order_id else None,
            "sales_officer": r.sales_officer,
            "staff_id": str(r.staff_id) if r.staff_id else None,
            "customer_name": r.customer_name,
            "customer_phone": r.customer_phone or '',
            "delivery_address": r.delivery_address,
            "city": r.city or '', "state": r.state or '',
            "landmark": r.landmark or '',
            "delivery_date": str(r.delivery_date) if r.delivery_date else None,
            "departure_time": str(r.departure_time) if r.departure_time else None,
            "arrival_time": str(r.arrival_time) if r.arrival_time else None,
            "transport_mode": r.transport_mode or 'vehicle',
            "vehicle_details": r.vehicle_details or '',
            "driver_name": r.driver_name or '',
            "driver_phone": r.driver_phone or '',
            "transport_cost": float(r.transport_cost or 0),
            "additional_charges": float(r.additional_charges or 0),
            "total_cost": float(r.total_cost or 0),
            "payment_method": r.payment_method or '',
            "payment_reference": r.payment_reference or '',
            "status": r.status,
            "notes": r.notes or '',
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "items": items
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── UPDATE DELIVERY STATUS ──────────────────────────────────────────────────

@router.put('/deliveries/{delivery_id}/status')
async def update_delivery_status(delivery_id: UUID, data: dict, session: AsyncSession = Depends(get_session)):
    """Update delivery status: pending, in_transit, completed, cancelled."""
    try:
        new_status = data.get('status', 'completed')
        sql = text("""
            UPDATE logistics_deliveries SET status = :status, updated_at = NOW()
            WHERE id = :id RETURNING id, delivery_number
        """)
        result = await session.execute(sql, {"status": new_status, "id": str(delivery_id)})
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Delivery not found")
        await session.commit()
        return {"message": f"Delivery {row.delivery_number} updated to {new_status}"}
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ─── LOGISTICS DASHBOARD ─────────────────────────────────────────────────────

@router.get('/dashboard')
async def logistics_dashboard(session: AsyncSession = Depends(get_session)):
    """Get logistics summary dashboard."""
    try:
        stats = {}

        # Overall delivery stats
        dq = await session.execute(text("""
            SELECT
                COUNT(*) as total_deliveries,
                COUNT(*) FILTER (WHERE status = 'completed') as completed,
                COUNT(*) FILTER (WHERE status = 'pending') as pending,
                COUNT(*) FILTER (WHERE status = 'in_transit') as in_transit,
                COUNT(*) FILTER (WHERE status = 'cancelled') as cancelled,
                COALESCE(SUM(total_cost), 0) as total_logistics_cost,
                COALESCE(AVG(total_cost), 0) as avg_delivery_cost
            FROM logistics_deliveries
        """))
        dr = dq.fetchone()
        stats['summary'] = {
            "total_deliveries": dr.total_deliveries,
            "completed": dr.completed, "pending": dr.pending,
            "in_transit": dr.in_transit, "cancelled": dr.cancelled,
            "total_logistics_cost": float(dr.total_logistics_cost),
            "avg_delivery_cost": round(float(dr.avg_delivery_cost), 2)
        }

        # Per-officer stats
        oq = await session.execute(text("""
            SELECT sales_officer,
                COUNT(*) as deliveries,
                COALESCE(SUM(total_cost), 0) as total_cost
            FROM logistics_deliveries
            GROUP BY sales_officer ORDER BY deliveries DESC LIMIT 10
        """))
        stats['by_officer'] = [
            {"sales_officer": r.sales_officer, "deliveries": r.deliveries,
             "total_cost": float(r.total_cost)}
            for r in oq.fetchall()
        ]

        # Per-city/location stats
        lq = await session.execute(text("""
            SELECT COALESCE(city, state, 'Unknown') as location,
                COUNT(*) as deliveries,
                COALESCE(SUM(total_cost), 0) as total_cost
            FROM logistics_deliveries
            GROUP BY COALESCE(city, state, 'Unknown') ORDER BY deliveries DESC LIMIT 10
        """))
        stats['by_location'] = [
            {"location": r.location, "deliveries": r.deliveries,
             "total_cost": float(r.total_cost)}
            for r in lq.fetchall()
        ]

        # Monthly trend (last 6 months)
        mq = await session.execute(text("""
            SELECT TO_CHAR(delivery_date, 'YYYY-MM') as month,
                COUNT(*) as deliveries,
                COALESCE(SUM(total_cost), 0) as total_cost
            FROM logistics_deliveries
            WHERE delivery_date >= CURRENT_DATE - INTERVAL '6 months'
            GROUP BY TO_CHAR(delivery_date, 'YYYY-MM')
            ORDER BY month DESC
        """))
        stats['monthly_trend'] = [
            {"month": r.month, "deliveries": r.deliveries,
             "total_cost": float(r.total_cost)}
            for r in mq.fetchall()
        ]

        # Recent deliveries
        rq = await session.execute(text("""
            SELECT delivery_number, sales_officer, customer_name, delivery_address,
                delivery_date, total_cost, status
            FROM logistics_deliveries ORDER BY created_at DESC LIMIT 5
        """))
        stats['recent_deliveries'] = [
            {
                "delivery_number": r.delivery_number,
                "sales_officer": r.sales_officer,
                "customer_name": r.customer_name,
                "delivery_address": r.delivery_address,
                "delivery_date": str(r.delivery_date) if r.delivery_date else None,
                "total_cost": float(r.total_cost or 0),
                "status": r.status
            }
            for r in rq.fetchall()
        ]

        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── TRANSPORT COST ANALYTICS ────────────────────────────────────────────────

@router.get('/analytics')
async def logistics_analytics(
    date_from: str = None,
    date_to: str = None,
    session: AsyncSession = Depends(get_session)
):
    """Get detailed cost analytics for logistics."""
    try:
        where = []
        params = {}
        if date_from:
            where.append("delivery_date >= :df")
            params['df'] = date_from
        if date_to:
            where.append("delivery_date <= :dt")
            params['dt'] = date_to
        where_clause = " AND ".join(where) if where else "1=1"

        # Transport mode breakdown
        tmq = await session.execute(text(f"""
            SELECT transport_mode,
                COUNT(*) as count,
                COALESCE(SUM(total_cost), 0) as total,
                COALESCE(AVG(total_cost), 0) as avg_cost
            FROM logistics_deliveries
            WHERE {where_clause}
            GROUP BY transport_mode ORDER BY total DESC
        """), params)
        by_mode = [
            {"mode": r.transport_mode, "count": r.count,
             "total": float(r.total), "avg_cost": round(float(r.avg_cost), 2)}
            for r in tmq.fetchall()
        ]

        # Top customers by delivery cost
        cq = await session.execute(text(f"""
            SELECT customer_name, COUNT(*) as deliveries,
                COALESCE(SUM(total_cost), 0) as total_cost
            FROM logistics_deliveries
            WHERE {where_clause}
            GROUP BY customer_name ORDER BY total_cost DESC LIMIT 10
        """), params)
        top_customers = [
            {"customer": r.customer_name, "deliveries": r.deliveries,
             "total_cost": float(r.total_cost)}
            for r in cq.fetchall()
        ]

        return {
            "by_transport_mode": by_mode,
            "top_customers": top_customers
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
