"""
Procurement & Purchase Requests Module
- Purchase requests (any category: raw_materials, consumables, machines, tools, general)
- Approval workflow (submitted → approved → ordered → received → closed)
- Purchase orders with vendor tracking
- Purchase invoices for accounting
- Dashboard with spend analytics
"""
from uuid import UUID
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.db import get_session

router = APIRouter(prefix="/api/procurement", tags=["Procurement"])

# ─── ENSURE TABLES (each statement executed separately for asyncpg) ──────────
TABLE_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS purchase_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_number VARCHAR(64) UNIQUE NOT NULL,
    requested_by VARCHAR(255) NOT NULL,
    department VARCHAR(100),
    category VARCHAR(50) NOT NULL DEFAULT 'general',
    priority VARCHAR(20) NOT NULL DEFAULT 'normal',
    status VARCHAR(30) NOT NULL DEFAULT 'submitted',
    title VARCHAR(255) NOT NULL,
    description TEXT,
    justification TEXT,
    total_estimated_cost NUMERIC(18,2) DEFAULT 0,
    approved_by VARCHAR(255),
    approved_at TIMESTAMPTZ,
    rejection_reason TEXT,
    vendor_name VARCHAR(255),
    vendor_contact VARCHAR(255),
    vendor_phone VARCHAR(50),
    vendor_email VARCHAR(255),
    expected_delivery_date DATE,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
)""",
    """CREATE TABLE IF NOT EXISTS purchase_request_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id UUID NOT NULL REFERENCES purchase_requests(id) ON DELETE CASCADE,
    item_type VARCHAR(50) NOT NULL DEFAULT 'general',
    item_name VARCHAR(255) NOT NULL,
    item_id UUID,
    specification TEXT,
    quantity NUMERIC(18,6) NOT NULL DEFAULT 1,
    unit VARCHAR(50) DEFAULT 'each',
    estimated_unit_cost NUMERIC(18,2) DEFAULT 0,
    estimated_total NUMERIC(18,2) DEFAULT 0,
    actual_unit_cost NUMERIC(18,2),
    actual_total NUMERIC(18,2),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
)""",
    """CREATE TABLE IF NOT EXISTS purchase_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    po_number VARCHAR(64) UNIQUE NOT NULL,
    request_id UUID REFERENCES purchase_requests(id),
    vendor_name VARCHAR(255) NOT NULL,
    vendor_contact VARCHAR(255),
    vendor_phone VARCHAR(50),
    vendor_email VARCHAR(255),
    vendor_address TEXT,
    status VARCHAR(30) NOT NULL DEFAULT 'draft',
    order_date TIMESTAMPTZ DEFAULT NOW(),
    expected_delivery DATE,
    delivery_date DATE,
    subtotal NUMERIC(18,2) DEFAULT 0,
    tax_amount NUMERIC(18,2) DEFAULT 0,
    shipping_cost NUMERIC(18,2) DEFAULT 0,
    total_amount NUMERIC(18,2) DEFAULT 0,
    paid_amount NUMERIC(18,2) DEFAULT 0,
    payment_status VARCHAR(30) DEFAULT 'unpaid',
    payment_method VARCHAR(50),
    payment_reference VARCHAR(255),
    payment_date TIMESTAMPTZ,
    notes TEXT,
    created_by VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
)""",
    """CREATE TABLE IF NOT EXISTS purchase_order_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    po_id UUID NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
    item_type VARCHAR(50) NOT NULL DEFAULT 'general',
    item_name VARCHAR(255) NOT NULL,
    item_id UUID,
    specification TEXT,
    quantity NUMERIC(18,6) NOT NULL DEFAULT 1,
    unit VARCHAR(50) DEFAULT 'each',
    unit_cost NUMERIC(18,2) DEFAULT 0,
    line_total NUMERIC(18,2) DEFAULT 0,
    received_qty NUMERIC(18,6) DEFAULT 0,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
)""",
    """CREATE TABLE IF NOT EXISTS purchase_invoices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_number VARCHAR(100) NOT NULL,
    po_id UUID REFERENCES purchase_orders(id),
    vendor_name VARCHAR(255) NOT NULL,
    invoice_date DATE NOT NULL DEFAULT CURRENT_DATE,
    due_date DATE,
    subtotal NUMERIC(18,2) DEFAULT 0,
    tax_amount NUMERIC(18,2) DEFAULT 0,
    total_amount NUMERIC(18,2) DEFAULT 0,
    paid_amount NUMERIC(18,2) DEFAULT 0,
    payment_status VARCHAR(30) DEFAULT 'unpaid',
    status VARCHAR(30) DEFAULT 'pending',
    category VARCHAR(50) DEFAULT 'general',
    description TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
)""",
    """CREATE TABLE IF NOT EXISTS expense_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    expense_number VARCHAR(64) UNIQUE NOT NULL,
    category VARCHAR(50) NOT NULL,
    subcategory VARCHAR(100),
    description TEXT NOT NULL,
    amount NUMERIC(18,2) NOT NULL,
    payment_method VARCHAR(50),
    payment_reference VARCHAR(255),
    payment_date DATE NOT NULL DEFAULT CURRENT_DATE,
    recipient VARCHAR(255),
    approved_by VARCHAR(255),
    po_id UUID REFERENCES purchase_orders(id),
    purchase_invoice_id UUID REFERENCES purchase_invoices(id),
    staff_id UUID,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
)""",
    "CREATE INDEX IF NOT EXISTS idx_pr_status ON purchase_requests(status)",
    "CREATE INDEX IF NOT EXISTS idx_pr_category ON purchase_requests(category)",
    "CREATE INDEX IF NOT EXISTS idx_po_status ON purchase_orders(status)",
    "CREATE INDEX IF NOT EXISTS idx_po_payment ON purchase_orders(payment_status)",
    "CREATE INDEX IF NOT EXISTS idx_pi_payment ON purchase_invoices(payment_status)",
    "CREATE INDEX IF NOT EXISTS idx_exp_category ON expense_records(category)",
    "CREATE INDEX IF NOT EXISTS idx_exp_date ON expense_records(payment_date)",
]


@router.on_event("startup")
async def init_procurement_tables():
    from app.db import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        for stmt in TABLE_STATEMENTS:
            await session.execute(text(stmt))
        await session.commit()
        print("Procurement tables ready")
        print("Procurement tables ready")


# ─── PURCHASE REQUESTS ──────────────────────────────────────────────────────

@router.post('/requests')
async def create_purchase_request(data: dict, session: AsyncSession = Depends(get_session)):
    """Create a new purchase request."""
    try:
        req_num = f"PR-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"
        items = data.get('items', [])
        total_est = sum(
            float(it.get('quantity', 1)) * float(it.get('estimated_unit_cost', 0))
            for it in items
        )
        sql = text("""
            INSERT INTO purchase_requests
                (request_number, requested_by, department, category, priority, title,
                 description, justification, total_estimated_cost, vendor_name,
                 vendor_contact, vendor_phone, vendor_email, expected_delivery_date, notes)
            VALUES
                (:rn, :rb, :dept, :cat, :pri, :title, :desc, :just, :cost,
                 :vn, :vc, :vp, :ve, :edd, :notes)
            RETURNING id, request_number, status, created_at
        """)
        edd = data.get('expected_delivery_date')
        result = await session.execute(sql, {
            'rn': req_num, 'rb': data.get('requested_by', ''),
            'dept': data.get('department', ''), 'cat': data.get('category', 'general'),
            'pri': data.get('priority', 'normal'), 'title': data.get('title', ''),
            'desc': data.get('description', ''), 'just': data.get('justification', ''),
            'cost': total_est, 'vn': data.get('vendor_name', ''),
            'vc': data.get('vendor_contact', ''), 'vp': data.get('vendor_phone', ''),
            've': data.get('vendor_email', ''),
            'edd': edd if edd else None, 'notes': data.get('notes', '')
        })
        row = result.fetchone()
        req_id = str(row.id)

        for it in items:
            est_total = float(it.get('quantity', 1)) * float(it.get('estimated_unit_cost', 0))
            await session.execute(text("""
                INSERT INTO purchase_request_items
                    (request_id, item_type, item_name, item_id, specification,
                     quantity, unit, estimated_unit_cost, estimated_total, notes)
                VALUES (:rid, :itype, :iname, :iid, :spec, :qty, :unit, :euc, :et, :notes)
            """), {
                'rid': req_id, 'itype': it.get('item_type', 'general'),
                'iname': it.get('item_name', ''), 'iid': it.get('item_id') or None,
                'spec': it.get('specification', ''),
                'qty': float(it.get('quantity', 1)), 'unit': it.get('unit', 'each'),
                'euc': float(it.get('estimated_unit_cost', 0)), 'et': est_total,
                'notes': it.get('notes', '')
            })

        await session.commit()
        return {"message": f"Purchase request {req_num} created", "request_number": req_num, "id": req_id}
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/requests')
async def list_purchase_requests(
    status: str = None,
    category: str = None,
    session: AsyncSession = Depends(get_session)
):
    """List purchase requests with filters."""
    try:
        where = []
        params = {}
        if status:
            where.append("pr.status = :status")
            params['status'] = status
        if category:
            where.append("pr.category = :category")
            params['category'] = category
        where_clause = " AND ".join(where) if where else "1=1"

        sql = text(f"""
            SELECT pr.*,
                (SELECT COUNT(*) FROM purchase_request_items WHERE request_id = pr.id) as item_count
            FROM purchase_requests pr
            WHERE {where_clause}
            ORDER BY pr.created_at DESC
        """)
        result = await session.execute(sql, params)
        rows = result.fetchall()
        items = []
        for r in rows:
            items.append({
                "id": str(r.id), "request_number": r.request_number,
                "requested_by": r.requested_by, "department": r.department,
                "category": r.category, "priority": r.priority,
                "status": r.status, "title": r.title,
                "total_estimated_cost": float(r.total_estimated_cost or 0),
                "vendor_name": r.vendor_name or '',
                "approved_by": r.approved_by or '',
                "approved_at": r.approved_at.isoformat() if r.approved_at else None,
                "expected_delivery_date": str(r.expected_delivery_date) if r.expected_delivery_date else None,
                "item_count": r.item_count,
                "created_at": r.created_at.isoformat() if r.created_at else None
            })
        return {"items": items, "total": len(items)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/requests/{request_id}')
async def get_purchase_request(request_id: UUID, session: AsyncSession = Depends(get_session)):
    """Get full detail of a purchase request including items."""
    try:
        sql = text("SELECT * FROM purchase_requests WHERE id = :id")
        result = await session.execute(sql, {"id": str(request_id)})
        r = result.fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="Request not found")

        items_sql = text("SELECT * FROM purchase_request_items WHERE request_id = :rid ORDER BY created_at")
        items_result = await session.execute(items_sql, {"rid": str(request_id)})
        items = []
        for it in items_result.fetchall():
            items.append({
                "id": str(it.id), "item_type": it.item_type,
                "item_name": it.item_name,
                "item_id": str(it.item_id) if it.item_id else None,
                "specification": it.specification or '',
                "quantity": float(it.quantity), "unit": it.unit,
                "estimated_unit_cost": float(it.estimated_unit_cost or 0),
                "estimated_total": float(it.estimated_total or 0),
                "actual_unit_cost": float(it.actual_unit_cost) if it.actual_unit_cost else None,
                "actual_total": float(it.actual_total) if it.actual_total else None,
                "notes": it.notes or ''
            })

        return {
            "id": str(r.id), "request_number": r.request_number,
            "requested_by": r.requested_by, "department": r.department,
            "category": r.category, "priority": r.priority,
            "status": r.status, "title": r.title,
            "description": r.description or '',
            "justification": r.justification or '',
            "total_estimated_cost": float(r.total_estimated_cost or 0),
            "vendor_name": r.vendor_name or '',
            "vendor_contact": r.vendor_contact or '',
            "vendor_phone": r.vendor_phone or '',
            "vendor_email": r.vendor_email or '',
            "approved_by": r.approved_by or '',
            "approved_at": r.approved_at.isoformat() if r.approved_at else None,
            "rejection_reason": r.rejection_reason or '',
            "expected_delivery_date": str(r.expected_delivery_date) if r.expected_delivery_date else None,
            "notes": r.notes or '',
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "items": items
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put('/requests/{request_id}/approve')
async def approve_request(request_id: UUID, data: dict, session: AsyncSession = Depends(get_session)):
    """Approve a purchase request."""
    try:
        sql = text("""
            UPDATE purchase_requests
            SET status = 'approved', approved_by = :ab, approved_at = NOW(), updated_at = NOW()
            WHERE id = :id AND status = 'submitted'
            RETURNING id, request_number
        """)
        result = await session.execute(sql, {
            "id": str(request_id),
            "ab": data.get('approved_by', 'Admin')
        })
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=400, detail="Request not found or not in submitted status")
        await session.commit()
        return {"message": f"Request {row.request_number} approved", "id": str(row.id)}
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.put('/requests/{request_id}/reject')
async def reject_request(request_id: UUID, data: dict, session: AsyncSession = Depends(get_session)):
    """Reject a purchase request."""
    try:
        sql = text("""
            UPDATE purchase_requests
            SET status = 'rejected', approved_by = :ab, approved_at = NOW(),
                rejection_reason = :reason, updated_at = NOW()
            WHERE id = :id AND status = 'submitted'
            RETURNING id, request_number
        """)
        result = await session.execute(sql, {
            "id": str(request_id),
            "ab": data.get('rejected_by', 'Admin'),
            "reason": data.get('reason', '')
        })
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=400, detail="Request not found or not in submitted status")
        await session.commit()
        return {"message": f"Request {row.request_number} rejected", "id": str(row.id)}
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ─── PURCHASE ORDERS ────────────────────────────────────────────────────────

@router.post('/orders')
async def create_purchase_order(data: dict, session: AsyncSession = Depends(get_session)):
    """Create purchase order (optionally from an approved request)."""
    try:
        po_num = f"PO-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"
        items = data.get('items', [])
        subtotal = sum(float(it.get('quantity', 1)) * float(it.get('unit_cost', 0)) for it in items)
        tax = float(data.get('tax_amount', 0))
        shipping = float(data.get('shipping_cost', 0))
        total = subtotal + tax + shipping

        req_id = data.get('request_id')

        sql = text("""
            INSERT INTO purchase_orders
                (po_number, request_id, vendor_name, vendor_contact, vendor_phone,
                 vendor_email, vendor_address, status, expected_delivery,
                 subtotal, tax_amount, shipping_cost, total_amount, notes, created_by)
            VALUES
                (:pn, :rid, :vn, :vc, :vp, :ve, :va, 'ordered', :ed,
                 :sub, :tax, :ship, :total, :notes, :cb)
            RETURNING id, po_number
        """)
        result = await session.execute(sql, {
            'pn': po_num, 'rid': req_id if req_id else None,
            'vn': data.get('vendor_name', ''),
            'vc': data.get('vendor_contact', ''),
            'vp': data.get('vendor_phone', ''),
            've': data.get('vendor_email', ''),
            'va': data.get('vendor_address', ''),
            'ed': data.get('expected_delivery') or None,
            'sub': subtotal, 'tax': tax, 'ship': shipping, 'total': total,
            'notes': data.get('notes', ''), 'cb': data.get('created_by', '')
        })
        row = result.fetchone()
        po_id = str(row.id)

        for it in items:
            lt = float(it.get('quantity', 1)) * float(it.get('unit_cost', 0))
            await session.execute(text("""
                INSERT INTO purchase_order_items
                    (po_id, item_type, item_name, item_id, specification,
                     quantity, unit, unit_cost, line_total, notes)
                VALUES (:pid, :itype, :iname, :iid, :spec, :qty, :unit, :uc, :lt, :notes)
            """), {
                'pid': po_id, 'itype': it.get('item_type', 'general'),
                'iname': it.get('item_name', ''),
                'iid': it.get('item_id') or None,
                'spec': it.get('specification', ''),
                'qty': float(it.get('quantity', 1)), 'unit': it.get('unit', 'each'),
                'uc': float(it.get('unit_cost', 0)), 'lt': lt,
                'notes': it.get('notes', '')
            })

        # Update request status if linked
        if req_id:
            await session.execute(text(
                "UPDATE purchase_requests SET status = 'ordered', updated_at = NOW() WHERE id = :rid"
            ), {"rid": req_id})

        await session.commit()
        return {"message": f"Purchase order {po_num} created", "po_number": po_num, "id": po_id, "total_amount": total}
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/orders')
async def list_purchase_orders(status: str = None, session: AsyncSession = Depends(get_session)):
    """List all purchase orders."""
    try:
        where = "po.status = :status" if status else "1=1"
        params = {"status": status} if status else {}
        sql = text(f"""
            SELECT po.*,
                (SELECT COUNT(*) FROM purchase_order_items WHERE po_id = po.id) as item_count,
                pr.request_number
            FROM purchase_orders po
            LEFT JOIN purchase_requests pr ON po.request_id = pr.id
            WHERE {where}
            ORDER BY po.created_at DESC
        """)
        result = await session.execute(sql, params)
        items = []
        for r in result.fetchall():
            items.append({
                "id": str(r.id), "po_number": r.po_number,
                "request_number": r.request_number or '',
                "vendor_name": r.vendor_name,
                "status": r.status,
                "order_date": r.order_date.isoformat() if r.order_date else None,
                "expected_delivery": str(r.expected_delivery) if r.expected_delivery else None,
                "delivery_date": str(r.delivery_date) if r.delivery_date else None,
                "total_amount": float(r.total_amount or 0),
                "paid_amount": float(r.paid_amount or 0),
                "payment_status": r.payment_status or 'unpaid',
                "item_count": r.item_count,
                "created_at": r.created_at.isoformat() if r.created_at else None
            })
        return {"items": items, "total": len(items)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/orders/{po_id}')
async def get_purchase_order(po_id: UUID, session: AsyncSession = Depends(get_session)):
    """Get full purchase order detail with items."""
    try:
        sql = text("SELECT * FROM purchase_orders WHERE id = :id")
        result = await session.execute(sql, {"id": str(po_id)})
        r = result.fetchone()
        if not r:
            raise HTTPException(status_code=404, detail="Purchase order not found")

        items_sql = text("SELECT * FROM purchase_order_items WHERE po_id = :pid ORDER BY created_at")
        items = []
        for it in (await session.execute(items_sql, {"pid": str(po_id)})).fetchall():
            items.append({
                "id": str(it.id), "item_type": it.item_type,
                "item_name": it.item_name,
                "item_id": str(it.item_id) if it.item_id else None,
                "specification": it.specification or '',
                "quantity": float(it.quantity), "unit": it.unit,
                "unit_cost": float(it.unit_cost or 0),
                "line_total": float(it.line_total or 0),
                "received_qty": float(it.received_qty or 0),
                "notes": it.notes or ''
            })

        return {
            "id": str(r.id), "po_number": r.po_number,
            "request_id": str(r.request_id) if r.request_id else None,
            "vendor_name": r.vendor_name,
            "vendor_contact": r.vendor_contact or '',
            "vendor_phone": r.vendor_phone or '',
            "vendor_email": r.vendor_email or '',
            "vendor_address": r.vendor_address or '',
            "status": r.status,
            "order_date": r.order_date.isoformat() if r.order_date else None,
            "expected_delivery": str(r.expected_delivery) if r.expected_delivery else None,
            "delivery_date": str(r.delivery_date) if r.delivery_date else None,
            "subtotal": float(r.subtotal or 0),
            "tax_amount": float(r.tax_amount or 0),
            "shipping_cost": float(r.shipping_cost or 0),
            "total_amount": float(r.total_amount or 0),
            "paid_amount": float(r.paid_amount or 0),
            "payment_status": r.payment_status or 'unpaid',
            "payment_method": r.payment_method or '',
            "payment_reference": r.payment_reference or '',
            "notes": r.notes or '',
            "created_by": r.created_by or '',
            "items": items
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put('/orders/{po_id}/receive')
async def receive_purchase_order(po_id: UUID, data: dict, session: AsyncSession = Depends(get_session)):
    """Mark purchase order as received and update request status."""
    try:
        sql = text("""
            UPDATE purchase_orders
            SET status = 'received', delivery_date = CURRENT_DATE, updated_at = NOW()
            WHERE id = :id AND status IN ('ordered', 'draft')
            RETURNING id, po_number, request_id
        """)
        result = await session.execute(sql, {"id": str(po_id)})
        row = result.fetchone()
        if not row:
            raise HTTPException(status_code=400, detail="PO not found or already received")

        if row.request_id:
            await session.execute(text(
                "UPDATE purchase_requests SET status = 'received', updated_at = NOW() WHERE id = :rid"
            ), {"rid": str(row.request_id)})

        await session.commit()
        return {"message": f"PO {row.po_number} marked as received"}
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.put('/orders/{po_id}/pay')
async def pay_purchase_order(po_id: UUID, data: dict, session: AsyncSession = Depends(get_session)):
    """Record payment for a purchase order."""
    try:
        amount = float(data.get('amount', 0))
        if amount <= 0:
            raise HTTPException(status_code=400, detail="Amount must be > 0")

        po_result = await session.execute(
            text("SELECT * FROM purchase_orders WHERE id = :id"), {"id": str(po_id)}
        )
        po = po_result.fetchone()
        if not po:
            raise HTTPException(status_code=404, detail="PO not found")

        new_paid = float(po.paid_amount or 0) + amount
        total = float(po.total_amount or 0)
        if new_paid > total:
            raise HTTPException(status_code=400, detail=f"Payment exceeds balance. Remaining: {total - float(po.paid_amount or 0):,.2f}")

        pay_status = 'paid' if new_paid >= total else 'partial'

        await session.execute(text("""
            UPDATE purchase_orders
            SET paid_amount = :paid, payment_status = :ps,
                payment_method = COALESCE(:pm, payment_method),
                payment_reference = COALESCE(:pr, payment_reference),
                payment_date = NOW(), updated_at = NOW()
            WHERE id = :id
        """), {
            "paid": new_paid, "ps": pay_status,
            "pm": data.get('payment_method'), "pr": data.get('payment_reference'),
            "id": str(po_id)
        })

        # Auto-create expense record
        exp_num = f"EXP-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"
        await session.execute(text("""
            INSERT INTO expense_records
                (expense_number, category, subcategory, description, amount,
                 payment_method, payment_reference, payment_date, recipient, po_id)
            VALUES (:en, 'procurement', :subcat, :desc, :amt, :pm, :pr, CURRENT_DATE, :recv, :pid)
        """), {
            "en": exp_num, "subcat": "purchase_order",
            "desc": f"Payment for PO {po.po_number} - {po.vendor_name}",
            "amt": amount,
            "pm": data.get('payment_method', ''), "pr": data.get('payment_reference', ''),
            "recv": po.vendor_name, "pid": str(po_id)
        })

        await session.commit()
        return {
            "message": f"Payment of NGN {amount:,.2f} recorded",
            "paid_amount": new_paid, "balance": total - new_paid,
            "payment_status": pay_status
        }
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ─── PURCHASE INVOICES ──────────────────────────────────────────────────────

@router.post('/invoices')
async def create_purchase_invoice(data: dict, session: AsyncSession = Depends(get_session)):
    """Record a purchase/vendor invoice for accounting."""
    try:
        sql = text("""
            INSERT INTO purchase_invoices
                (invoice_number, po_id, vendor_name, invoice_date, due_date,
                 subtotal, tax_amount, total_amount, category, description, notes)
            VALUES (:inv_num, :po_id, :vn, :inv_date, :due, :sub, :tax, :total, :cat, :desc, :notes)
            RETURNING id, invoice_number
        """)
        subtotal = float(data.get('subtotal', 0))
        tax = float(data.get('tax_amount', 0))
        total = float(data.get('total_amount', 0)) or (subtotal + tax)
        result = await session.execute(sql, {
            "inv_num": data.get('invoice_number', ''),
            "po_id": data.get('po_id') or None,
            "vn": data.get('vendor_name', ''),
            "inv_date": data.get('invoice_date', datetime.now(timezone.utc).date().isoformat()),
            "due": data.get('due_date') or None,
            "sub": subtotal, "tax": tax, "total": total,
            "cat": data.get('category', 'general'),
            "desc": data.get('description', ''),
            "notes": data.get('notes', '')
        })
        row = result.fetchone()
        await session.commit()
        return {"message": "Purchase invoice recorded", "id": str(row.id), "invoice_number": row.invoice_number}
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/invoices')
async def list_purchase_invoices(session: AsyncSession = Depends(get_session)):
    """List all purchase/vendor invoices."""
    try:
        sql = text("""
            SELECT pi.*, po.po_number
            FROM purchase_invoices pi
            LEFT JOIN purchase_orders po ON pi.po_id = po.id
            ORDER BY pi.created_at DESC
        """)
        result = await session.execute(sql)
        items = []
        for r in result.fetchall():
            items.append({
                "id": str(r.id), "invoice_number": r.invoice_number,
                "po_number": r.po_number or '',
                "vendor_name": r.vendor_name,
                "invoice_date": str(r.invoice_date) if r.invoice_date else None,
                "due_date": str(r.due_date) if r.due_date else None,
                "total_amount": float(r.total_amount or 0),
                "paid_amount": float(r.paid_amount or 0),
                "payment_status": r.payment_status or 'unpaid',
                "category": r.category or 'general',
                "description": r.description or ''
            })
        return {"items": items, "total": len(items)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── EXPENSE RECORDS (salaries, wages, logistics, procurement) ──────────────

@router.post('/expenses')
async def create_expense(data: dict, session: AsyncSession = Depends(get_session)):
    """Record an expense (salary, wages, logistics, procurement, misc)."""
    try:
        exp_num = f"EXP-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"
        sql = text("""
            INSERT INTO expense_records
                (expense_number, category, subcategory, description, amount,
                 payment_method, payment_reference, payment_date, recipient,
                 approved_by, staff_id, notes)
            VALUES (:en, :cat, :sub, :desc, :amt, :pm, :pr, :pd, :recv, :ab, :sid, :notes)
            RETURNING id, expense_number
        """)
        result = await session.execute(sql, {
            "en": exp_num, "cat": data.get('category', 'general'),
            "sub": data.get('subcategory', ''),
            "desc": data.get('description', ''),
            "amt": float(data.get('amount', 0)),
            "pm": data.get('payment_method', ''),
            "pr": data.get('payment_reference', ''),
            "pd": data.get('payment_date', datetime.now(timezone.utc).date().isoformat()),
            "recv": data.get('recipient', ''),
            "ab": data.get('approved_by', ''),
            "sid": data.get('staff_id') or None,
            "notes": data.get('notes', '')
        })
        row = result.fetchone()
        await session.commit()
        return {"message": "Expense recorded", "expense_number": row.expense_number, "id": str(row.id)}
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/expenses')
async def list_expenses(
    category: str = None,
    date_from: str = None,
    date_to: str = None,
    session: AsyncSession = Depends(get_session)
):
    """List expense records with optional filters."""
    try:
        where = []
        params = {}
        if category:
            where.append("category = :cat")
            params['cat'] = category
        if date_from:
            where.append("payment_date >= :df")
            params['df'] = date_from
        if date_to:
            where.append("payment_date <= :dt")
            params['dt'] = date_to
        where_clause = " AND ".join(where) if where else "1=1"

        sql = text(f"SELECT * FROM expense_records WHERE {where_clause} ORDER BY payment_date DESC, created_at DESC")
        result = await session.execute(sql, params)
        items = []
        for r in result.fetchall():
            items.append({
                "id": str(r.id), "expense_number": r.expense_number,
                "category": r.category, "subcategory": r.subcategory or '',
                "description": r.description or '',
                "amount": float(r.amount or 0),
                "payment_method": r.payment_method or '',
                "payment_reference": r.payment_reference or '',
                "payment_date": str(r.payment_date) if r.payment_date else None,
                "recipient": r.recipient or '',
                "approved_by": r.approved_by or '',
                "notes": r.notes or ''
            })
        return {"items": items, "total": len(items)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── PROCUREMENT DASHBOARD ──────────────────────────────────────────────────

@router.get('/dashboard')
async def procurement_dashboard(session: AsyncSession = Depends(get_session)):
    """Get procurement summary dashboard."""
    try:
        stats = {}
        # Request stats
        rq = await session.execute(text("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE status = 'submitted') as pending,
                COUNT(*) FILTER (WHERE status = 'approved') as approved,
                COUNT(*) FILTER (WHERE status = 'rejected') as rejected,
                COUNT(*) FILTER (WHERE status = 'ordered') as ordered,
                COUNT(*) FILTER (WHERE status = 'received') as received,
                COALESCE(SUM(total_estimated_cost), 0) as total_estimated
            FROM purchase_requests
        """))
        rr = rq.fetchone()
        stats['requests'] = {
            "total": rr.total, "pending": rr.pending, "approved": rr.approved,
            "rejected": rr.rejected, "ordered": rr.ordered, "received": rr.received,
            "total_estimated": float(rr.total_estimated)
        }

        # PO stats
        pq = await session.execute(text("""
            SELECT
                COUNT(*) as total,
                COALESCE(SUM(total_amount), 0) as total_ordered,
                COALESCE(SUM(paid_amount), 0) as total_paid,
                COALESCE(SUM(total_amount) - SUM(paid_amount), 0) as total_outstanding,
                COUNT(*) FILTER (WHERE payment_status = 'paid') as fully_paid,
                COUNT(*) FILTER (WHERE payment_status = 'partial') as partially_paid,
                COUNT(*) FILTER (WHERE payment_status = 'unpaid') as unpaid
            FROM purchase_orders
        """))
        pr = pq.fetchone()
        stats['orders'] = {
            "total": pr.total,
            "total_ordered": float(pr.total_ordered),
            "total_paid": float(pr.total_paid),
            "total_outstanding": float(pr.total_outstanding),
            "fully_paid": pr.fully_paid,
            "partially_paid": pr.partially_paid,
            "unpaid": pr.unpaid
        }

        # Expense summary by category
        eq = await session.execute(text("""
            SELECT category, COUNT(*) as count, COALESCE(SUM(amount), 0) as total
            FROM expense_records
            GROUP BY category ORDER BY total DESC
        """))
        expense_cats = []
        total_expenses = 0
        for er in eq.fetchall():
            total_expenses += float(er.total)
            expense_cats.append({
                "category": er.category, "count": er.count,
                "total": float(er.total)
            })
        stats['expenses'] = {
            "categories": expense_cats,
            "total_expenses": total_expenses
        }

        # Recent requests
        recent = await session.execute(text("""
            SELECT request_number, title, category, status, total_estimated_cost, created_at
            FROM purchase_requests ORDER BY created_at DESC LIMIT 5
        """))
        stats['recent_requests'] = [
            {
                "request_number": r.request_number, "title": r.title,
                "category": r.category, "status": r.status,
                "total_estimated_cost": float(r.total_estimated_cost or 0),
                "created_at": r.created_at.isoformat() if r.created_at else None
            }
            for r in recent.fetchall()
        ]

        return stats
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
