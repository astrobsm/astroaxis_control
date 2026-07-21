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
from datetime import datetime, timezone, date as date_type
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from decimal import Decimal
from app.db import get_session
from app.models import User
from app.api.auth import require_authenticated_user, require_admin
from app.services.inventory import apply_stock_movement
from app.services.posting import post_purchase, post_supplier_payment
from app.services.payables import (
    pay_supplier, get_or_create_supplier, supplier_balance,
    outstanding_payables, supplier_aging, supplier_statement, money)

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
        edd_raw = data.get('expected_delivery_date')
        # Convert date string to date object for asyncpg
        if isinstance(edd_raw, str) and edd_raw:
            edd_val = date_type.fromisoformat(edd_raw)
        elif isinstance(edd_raw, date_type):
            edd_val = edd_raw
        else:
            edd_val = None
        result = await session.execute(sql, {
            'rn': req_num, 'rb': data.get('requested_by', ''),
            'dept': data.get('department', ''), 'cat': data.get('category', 'general'),
            'pri': data.get('priority', 'normal'), 'title': data.get('title', ''),
            'desc': data.get('description', ''), 'just': data.get('justification', ''),
            'cost': total_est, 'vn': data.get('vendor_name', ''),
            'vc': data.get('vendor_contact', ''), 'vp': data.get('vendor_phone', ''),
            've': data.get('vendor_email', ''),
            'edd': edd_val, 'notes': data.get('notes', '')
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
            'ed': date_type.fromisoformat(data['expected_delivery']) if isinstance(data.get('expected_delivery'), str) and data.get('expected_delivery') else (data.get('expected_delivery') if isinstance(data.get('expected_delivery'), date_type) else None),
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
async def receive_purchase_order(
    po_id: UUID,
    data: dict,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_authenticated_user),
):
    """Receive a purchase order: book the goods into stock and the liability.

    This endpoint used to do nothing but flip a status. Goods physically
    arrived at the warehouse and inventory never changed -- the same class of
    break as production completions recording cost with no stock. Materials
    then had to be introduced by a manual stock adjustment, which is exactly
    the hole the movement ledger exists to close.

    Receiving now:
      * moves each line's quantity into the receiving warehouse, at the
        purchase cost (which becomes part of the weighted-average cost used
        for COGS on future sales),
      * records the liability: Dr Inventory / Cr Accounts Payable.

    Paying the supplier is a SEPARATE event -- see /orders/{id}/pay.
    """
    try:
        # Claim the PO atomically. The status transition is the idempotency
        # key: only the caller that flips 'ordered' -> 'received' books stock,
        # so a double-clicked Receive cannot deliver the goods twice.
        result = await session.execute(
            text("""
                UPDATE purchase_orders
                   SET status = 'received', delivery_date = CURRENT_DATE,
                       stock_received_at = NOW(),
                       receiving_warehouse_id = COALESCE(
                           CAST(:wid AS uuid), receiving_warehouse_id),
                       updated_at = NOW()
                 WHERE id = :id AND status IN ('ordered', 'draft')
             RETURNING id, po_number, request_id, supplier_id, vendor_name,
                       total_amount, receiving_warehouse_id
            """),
            {"id": str(po_id), "wid": data.get("warehouse_id")},
        )
        row = result.fetchone()
        if not row:
            existing = (await session.execute(
                text("SELECT status FROM purchase_orders WHERE id = :id"),
                {"id": str(po_id)},
            )).fetchone()
            await session.rollback()
            if existing is None:
                raise HTTPException(status_code=404, detail="PO not found")
            raise HTTPException(
                status_code=400,
                detail=f"PO cannot be received (status: {existing.status})")

        warehouse_id = row.receiving_warehouse_id
        if not warehouse_id:
            wh = (await session.execute(
                text("SELECT id FROM warehouses WHERE is_active = TRUE "
                     "ORDER BY code LIMIT 1")
            )).fetchone()
            if not wh:
                raise HTTPException(
                    status_code=400,
                    detail="No warehouse available to receive goods into. "
                           "Pass warehouse_id.")
            warehouse_id = wh.id
            await session.execute(
                text("UPDATE purchase_orders SET receiving_warehouse_id = :w "
                     "WHERE id = :id"),
                {"w": str(warehouse_id), "id": str(po_id)},
            )

        # Book each line into stock. Lines that reference a real product or
        # raw material move inventory; free-text lines (services, sundries)
        # cannot, and are reported back so nothing is silently skipped.
        items = (await session.execute(
            text("""SELECT id, item_type, item_name, item_id, quantity,
                           unit_cost, received_qty
                      FROM purchase_order_items WHERE po_id = :p"""),
            {"p": str(po_id)},
        )).fetchall()

        stocked_value = Decimal("0")
        unstocked = []
        for it in items:
            qty = Decimal(str(it.quantity or 0))
            if qty <= 0:
                continue
            if not it.item_id or it.item_type not in ('product', 'raw_material'):
                unstocked.append(it.item_name)
                continue

            is_raw = it.item_type == 'raw_material'
            await apply_stock_movement(
                session,
                warehouse_id=warehouse_id,
                product_id=None if is_raw else it.item_id,
                raw_material_id=it.item_id if is_raw else None,
                movement_type='IN',
                quantity=qty,
                unit_cost=it.unit_cost,
                reference=f"GRN-{row.po_number}",
                notes=f"Goods received against PO {row.po_number}",
                created_by=current_user.id,
            )
            stocked_value += qty * Decimal(str(it.unit_cost or 0))
            await session.execute(
                text("UPDATE purchase_order_items SET received_qty = quantity "
                     "WHERE id = :i"),
                {"i": str(it.id)},
            )

        # Recognise the liability for everything on the order, not just the
        # stockable lines -- the supplier is owed for all of it.
        await post_purchase(
            session,
            value=Decimal(str(row.total_amount or 0)),
            reference=f"GRN-{row.po_number}",
            is_raw_material=True,
            created_by=current_user.id,
        )

        if row.request_id:
            await session.execute(text(
                "UPDATE purchase_requests SET status = 'received', updated_at = NOW() WHERE id = :rid"
            ), {"rid": str(row.request_id)})

        await session.commit()
        return {
            "message": f"PO {row.po_number} received",
            "warehouse_id": str(warehouse_id),
            "stocked_value": float(stocked_value),
            "lines_not_stocked": unstocked,
            "note": ("Some lines could not be booked into inventory because "
                     "they are not linked to a product or raw material."
                     if unstocked else None),
        }
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.put('/orders/{po_id}/pay')
async def pay_purchase_order(
    po_id: UUID,
    data: dict,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_authenticated_user),
):
    """Record a payment to a supplier against a purchase order.

    The previous implementation read purchase_orders.paid_amount, added to it
    in FLOAT, and wrote it back with no row lock. That gave two concurrent
    payments the same starting total (one silently overwrote the other), let
    float drift reject a legitimate final instalment as an overpayment, and
    left no record of the individual payments -- so no supplier statement
    could be produced or disputed.

    Payments are now rows; paid_amount is recomputed from them.
    """
    try:
        result = await pay_supplier(
            session,
            po_id=po_id,
            amount=data.get('amount', 0),
            payment_method=data.get('payment_method') or 'unspecified',
            payment_reference=data.get('payment_reference'),
            notes=data.get('notes'),
            created_by=current_user.id,
        )

        # Keep the human-facing fields on the PO in step for the UI.
        await session.execute(text("""
            UPDATE purchase_orders
               SET payment_method = COALESCE(:pm, payment_method),
                   payment_reference = COALESCE(:pr, payment_reference),
                   payment_date = NOW(), updated_at = NOW()
             WHERE id = :id
        """), {
            "pm": data.get('payment_method'),
            "pr": data.get('payment_reference'),
            "id": str(po_id),
        })

        po = (await session.execute(
            text("SELECT po_number, vendor_name FROM purchase_orders WHERE id = :id"),
            {"id": str(po_id)},
        )).fetchone()

        # Expense record retained for the existing expenses report.
        exp_num = f"EXP-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"
        await session.execute(text("""
            INSERT INTO expense_records
                (expense_number, category, subcategory, description, amount,
                 payment_method, payment_reference, payment_date, recipient, po_id)
            VALUES (:en, 'procurement', :subcat, :desc, :amt, :pm, :pr, CURRENT_DATE, :recv, :pid)
        """), {
            "en": exp_num, "subcat": "purchase_order",
            "desc": f"Payment for PO {po.po_number} - {po.vendor_name}",
            "amt": str(result["amount"]),
            "pm": data.get('payment_method', ''),
            "pr": data.get('payment_reference', ''),
            "recv": po.vendor_name, "pid": str(po_id),
        })

        # Settle the liability: Dr Accounts Payable / Cr Bank.
        await post_supplier_payment(
            session,
            payment_id=result["payment_id"],
            amount=result["amount"],
            reference=result["payment_number"],
            payment_method=data.get('payment_method') or 'unspecified',
            created_by=current_user.id,
        )

        await session.commit()
        return {
            "message": f"Payment of NGN {result['amount']:,.2f} recorded",
            "payment_number": result["payment_number"],
            "paid_amount": float(result["total_paid"]),
            "balance": float(result["balance"]),
            "payment_status": result["payment_status"],
        }
    except HTTPException:
        await session.rollback()
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
            "inv_date": date_type.fromisoformat(data['invoice_date']) if isinstance(data.get('invoice_date'), str) and data.get('invoice_date') else (data.get('invoice_date') if isinstance(data.get('invoice_date'), date_type) else datetime.now(timezone.utc).date()),
            "due": date_type.fromisoformat(data['due_date']) if isinstance(data.get('due_date'), str) and data.get('due_date') else (data.get('due_date') if isinstance(data.get('due_date'), date_type) else None),
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
            "pd": date_type.fromisoformat(data['payment_date']) if isinstance(data.get('payment_date'), str) and data.get('payment_date') else (data.get('payment_date') if isinstance(data.get('payment_date'), date_type) else datetime.now(timezone.utc).date()),
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


# ─── SUPPLIERS & ACCOUNTS PAYABLE ───────────────────────────────────────────

@router.get('/suppliers')
async def list_suppliers(
    active_only: bool = True,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    """Supplier master with each supplier's outstanding balance."""
    where = "WHERE s.is_active = TRUE" if active_only else ""
    rows = (await session.execute(text(f"""
        SELECT s.id, s.supplier_code, s.name, s.classification,
               s.contact_person, s.phone, s.email, s.address,
               s.credit_limit, s.payment_terms_days, s.is_active,
               COALESCE((SELECT SUM(po.total_amount) FROM purchase_orders po
                          WHERE po.supplier_id = s.id
                            AND po.status NOT IN ('cancelled','draft')), 0)
             - COALESCE((SELECT SUM(sp.amount) FROM supplier_payments sp
                          WHERE sp.supplier_id = s.id), 0) AS balance
          FROM suppliers s {where}
         ORDER BY s.name
    """))).fetchall()
    return [
        {
            "id": str(r.id), "supplier_code": r.supplier_code, "name": r.name,
            "classification": r.classification,
            "contact_person": r.contact_person, "phone": r.phone,
            "email": r.email, "address": r.address,
            "credit_limit": float(r.credit_limit or 0),
            "payment_terms_days": r.payment_terms_days,
            "is_active": r.is_active,
            "outstanding_balance": float(r.balance or 0),
            # Surfaced rather than merely stored: a supplier over their limit
            # is a decision the buyer needs at the point of ordering.
            "over_credit_limit": bool(
                r.credit_limit and float(r.balance or 0) > float(r.credit_limit)),
        }
        for r in rows
    ]


@router.post('/suppliers')
async def create_supplier(
    data: dict,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    """Create a supplier, or return the existing one with that name."""
    try:
        supplier_id = await get_or_create_supplier(
            session,
            name=data.get('name', ''),
            contact_person=data.get('contact_person'),
            phone=data.get('phone'),
            email=data.get('email'),
            address=data.get('address'),
            payment_terms_days=data.get('payment_terms_days'),
            credit_limit=data.get('credit_limit'),
        )
        await session.commit()
        return {"success": True, "supplier_id": str(supplier_id)}
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.get('/payables/aging')
async def get_payables_aging(
    as_at: date_type = None,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    """Outstanding payables bucketed by how overdue they are.

    Ageing runs from the DUE date (order date + the supplier's payment terms),
    not the order date -- an invoice on 60-day terms is not overdue at 45 days.
    """
    return await supplier_aging(session, as_at=as_at)


@router.get('/payables/outstanding')
async def get_outstanding_payables(
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    return {"total_outstanding": float(await outstanding_payables(session))}


@router.get('/suppliers/{supplier_id}/statement')
async def get_supplier_statement(
    supplier_id: UUID,
    start: date_type = None,
    end: date_type = None,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    """Every charge and payment for one supplier, with a running balance."""
    return await supplier_statement(
        session, supplier_id=supplier_id, start=start, end=end)


@router.get('/suppliers/{supplier_id}/payments')
async def list_supplier_payments(
    supplier_id: UUID,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    rows = (await session.execute(text("""
        SELECT sp.payment_number, sp.amount, sp.payment_method,
               sp.payment_reference, sp.payment_date, sp.notes,
               po.po_number
          FROM supplier_payments sp
          LEFT JOIN purchase_orders po ON po.id = sp.po_id
         WHERE sp.supplier_id = :s
         ORDER BY sp.payment_date DESC, sp.created_at DESC
    """), {"s": str(supplier_id)})).fetchall()
    return [
        {"payment_number": r.payment_number, "amount": float(r.amount),
         "payment_method": r.payment_method,
         "payment_reference": r.payment_reference,
         "payment_date": str(r.payment_date), "po_number": r.po_number,
         "notes": r.notes}
        for r in rows
    ]
