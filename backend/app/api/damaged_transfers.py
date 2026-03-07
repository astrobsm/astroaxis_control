"""
Damaged Product Transfers Module API
Handles transfer of damaged/defective products between warehouses.
Auto-creates the damaged_product_transfers table if it doesn't exist.
Supports a receipt workflow: pending -> dispatched -> received.
"""
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Optional
import uuid
from datetime import datetime, timezone

from app.db import get_session
from app.api.auth import get_current_user

router = APIRouter(prefix='/api/damaged-transfers')

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS damaged_product_transfers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transfer_number VARCHAR(64) UNIQUE NOT NULL,
    from_warehouse_id UUID NOT NULL REFERENCES warehouses(id),
    to_warehouse_id UUID NOT NULL REFERENCES warehouses(id),
    product_id UUID REFERENCES products(id),
    raw_material_id UUID REFERENCES raw_materials(id),
    quantity NUMERIC(18,6) NOT NULL,
    damage_type VARCHAR(100) NOT NULL,
    damage_reason TEXT,
    action_taken VARCHAR(100),
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    notes TEXT,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    dispatched_at TIMESTAMP WITH TIME ZONE,
    received_by UUID REFERENCES users(id),
    received_at TIMESTAMP WITH TIME ZONE,
    receipt_notes TEXT,
    receipt_condition VARCHAR(100)
);
CREATE INDEX IF NOT EXISTS idx_dpt_status ON damaged_product_transfers(status);
CREATE INDEX IF NOT EXISTS idx_dpt_from ON damaged_product_transfers(from_warehouse_id);
CREATE INDEX IF NOT EXISTS idx_dpt_to ON damaged_product_transfers(to_warehouse_id);
"""


async def _ensure_table(session: AsyncSession):
    await session.execute(text(CREATE_TABLE_SQL))
    await session.commit()


async def _auth_user(authorization: Optional[str], session: AsyncSession):
    if not authorization or not authorization.startswith("Bearer "):
        return None, None, None
    token = authorization.replace("Bearer ", "")
    try:
        payload = await get_current_user(token, session)
        return payload.get('id'), payload.get('role'), payload.get('full_name', '')
    except Exception:
        return None, None, None


def _row_to_dict(row):
    return {
        'id': str(row.id),
        'transfer_number': row.transfer_number,
        'from_warehouse_id': str(row.from_warehouse_id),
        'to_warehouse_id': str(row.to_warehouse_id),
        'product_id': str(row.product_id) if row.product_id else None,
        'raw_material_id': str(row.raw_material_id) if row.raw_material_id else None,
        'quantity': float(row.quantity),
        'damage_type': row.damage_type,
        'damage_reason': row.damage_reason or '',
        'action_taken': row.action_taken or '',
        'status': row.status,
        'notes': row.notes or '',
        'created_by': str(row.created_by) if row.created_by else None,
        'created_at': row.created_at.isoformat() if row.created_at else None,
        'dispatched_at': row.dispatched_at.isoformat() if row.dispatched_at else None,
        'received_by': str(row.received_by) if row.received_by else None,
        'received_at': row.received_at.isoformat() if row.received_at else None,
        'receipt_notes': row.receipt_notes or '',
        'receipt_condition': row.receipt_condition or '',
    }


# ---------- Endpoints ----------

@router.get('/')
async def list_damaged_transfers(
    session: AsyncSession = Depends(get_session),
):
    """List all damaged product transfers with enriched warehouse/product names."""
    await _ensure_table(session)

    result = await session.execute(text("""
        SELECT dpt.*,
               fw.name AS from_warehouse_name,
               tw.name AS to_warehouse_name,
               p.name AS product_name,
               p.sku AS product_sku,
               rm.name AS raw_material_name,
               u1.full_name AS created_by_name,
               u2.full_name AS received_by_name
        FROM damaged_product_transfers dpt
        LEFT JOIN warehouses fw ON fw.id = dpt.from_warehouse_id
        LEFT JOIN warehouses tw ON tw.id = dpt.to_warehouse_id
        LEFT JOIN products p ON p.id = dpt.product_id
        LEFT JOIN raw_materials rm ON rm.id = dpt.raw_material_id
        LEFT JOIN users u1 ON u1.id = dpt.created_by
        LEFT JOIN users u2 ON u2.id = dpt.received_by
        ORDER BY dpt.created_at DESC
    """))
    rows = result.fetchall()

    out = []
    for r in rows:
        d = _row_to_dict(r)
        d['from_warehouse_name'] = r.from_warehouse_name or 'N/A'
        d['to_warehouse_name'] = r.to_warehouse_name or 'N/A'
        d['product_name'] = r.product_name or (r.raw_material_name or 'N/A')
        d['product_sku'] = r.product_sku or ''
        d['created_by_name'] = r.created_by_name or ''
        d['received_by_name'] = r.received_by_name or ''
        out.append(d)
    return out


@router.post('/')
async def create_damaged_transfer(
    data: dict,
    session: AsyncSession = Depends(get_session),
    authorization: Optional[str] = Header(None),
):
    """
    Create a damaged product transfer between warehouses.
    Status starts as 'pending', must be received at destination.
    """
    await _ensure_table(session)
    user_id, user_role, user_name = await _auth_user(authorization, session)

    from_wh = data.get('from_warehouse_id')
    to_wh = data.get('to_warehouse_id')
    product_id = data.get('product_id') or None
    raw_material_id = data.get('raw_material_id') or None
    quantity = float(data.get('quantity', 0))
    damage_type = data.get('damage_type', '')
    damage_reason = data.get('damage_reason', '')
    action_taken = data.get('action_taken', '')
    notes = data.get('notes', '')

    if not from_wh or not to_wh:
        raise HTTPException(status_code=400, detail="Source and destination warehouses are required")
    if from_wh == to_wh:
        raise HTTPException(status_code=400, detail="Source and destination must be different")
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be greater than zero")
    if not damage_type:
        raise HTTPException(status_code=400, detail="Damage type is required")
    if not product_id and not raw_material_id:
        raise HTTPException(status_code=400, detail="Product or raw material is required")

    # Validate warehouses exist
    vf = await session.execute(text("SELECT id FROM warehouses WHERE id = :wid"), {"wid": from_wh})
    if not vf.fetchone():
        raise HTTPException(status_code=404, detail="Source warehouse not found")
    vt = await session.execute(text("SELECT id FROM warehouses WHERE id = :wid"), {"wid": to_wh})
    if not vt.fetchone():
        raise HTTPException(status_code=404, detail="Destination warehouse not found")

    transfer_number = f"DMG-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"

    try:
        # Deduct from source warehouse stock
        if product_id:
            stock_r = await session.execute(text("""
                SELECT current_stock FROM stock_levels
                WHERE warehouse_id = :wid AND product_id = :pid
            """), {"wid": from_wh, "pid": product_id})
            stock_row = stock_r.fetchone()
            available = float(stock_row.current_stock) if stock_row else 0
            if available < quantity:
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient stock. Available: {available}, Requested: {quantity}"
                )
            await session.execute(text("""
                UPDATE stock_levels SET current_stock = current_stock - :qty
                WHERE warehouse_id = :wid AND product_id = :pid
            """), {"qty": quantity, "wid": from_wh, "pid": product_id})

            # Record stock movement OUT
            await session.execute(text("""
                INSERT INTO stock_movements (id, warehouse_id, product_id, movement_type, quantity, reference, notes, created_by, created_at)
                VALUES (gen_random_uuid(), :wid, :pid, 'DAMAGE_TRANSFER_OUT', :qty, :ref, :notes, :uid, NOW())
            """), {
                "wid": from_wh, "pid": product_id, "qty": quantity,
                "ref": transfer_number,
                "notes": f"Damaged transfer to destination: {damage_type} - {damage_reason}",
                "uid": user_id
            })

        elif raw_material_id:
            stock_r = await session.execute(text("""
                SELECT current_stock FROM stock_levels
                WHERE warehouse_id = :wid AND raw_material_id = :rid
            """), {"wid": from_wh, "rid": raw_material_id})
            stock_row = stock_r.fetchone()
            available = float(stock_row.current_stock) if stock_row else 0
            if available < quantity:
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient stock. Available: {available}, Requested: {quantity}"
                )
            await session.execute(text("""
                UPDATE stock_levels SET current_stock = current_stock - :qty
                WHERE warehouse_id = :wid AND raw_material_id = :rid
            """), {"qty": quantity, "wid": from_wh, "rid": raw_material_id})

            await session.execute(text("""
                INSERT INTO stock_movements (id, warehouse_id, raw_material_id, movement_type, quantity, reference, notes, created_by, created_at)
                VALUES (gen_random_uuid(), :wid, :rid, 'DAMAGE_TRANSFER_OUT', :qty, :ref, :notes, :uid, NOW())
            """), {
                "wid": from_wh, "rid": raw_material_id, "qty": quantity,
                "ref": transfer_number,
                "notes": f"Damaged transfer to destination: {damage_type} - {damage_reason}",
                "uid": user_id
            })

        # Create the transfer record with status 'pending' (needs receipt)
        await session.execute(text("""
            INSERT INTO damaged_product_transfers
                (id, transfer_number, from_warehouse_id, to_warehouse_id,
                 product_id, raw_material_id, quantity, damage_type,
                 damage_reason, action_taken, status, notes, created_by, created_at)
            VALUES
                (gen_random_uuid(), :tn, :fwid, :twid,
                 :pid, :rid, :qty, :dtype,
                 :dreason, :action, 'pending', :notes, :uid, NOW())
        """), {
            "tn": transfer_number,
            "fwid": from_wh, "twid": to_wh,
            "pid": product_id, "rid": raw_material_id,
            "qty": quantity, "dtype": damage_type,
            "dreason": damage_reason, "action": action_taken,
            "notes": notes, "uid": user_id,
        })

        await session.commit()
        return {
            "success": True,
            "message": f"Damaged transfer {transfer_number} created. Awaiting receipt at destination.",
            "transfer_number": transfer_number,
        }

    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create damaged transfer: {str(e)}")


@router.post('/{transfer_id}/dispatch')
async def dispatch_damaged_transfer(
    transfer_id: str,
    session: AsyncSession = Depends(get_session),
    authorization: Optional[str] = Header(None),
):
    """Mark a damaged transfer as dispatched (in transit)."""
    await _ensure_table(session)

    result = await session.execute(text(
        "SELECT * FROM damaged_product_transfers WHERE id = :tid"
    ), {"tid": transfer_id})
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Transfer not found")
    if row.status != 'pending':
        raise HTTPException(status_code=400, detail=f"Cannot dispatch transfer with status '{row.status}'")

    await session.execute(text("""
        UPDATE damaged_product_transfers
        SET status = 'dispatched', dispatched_at = NOW()
        WHERE id = :tid
    """), {"tid": transfer_id})
    await session.commit()

    return {"success": True, "message": f"Transfer {row.transfer_number} marked as dispatched"}


@router.post('/{transfer_id}/receive')
async def receive_damaged_transfer(
    transfer_id: str,
    data: dict,
    session: AsyncSession = Depends(get_session),
    authorization: Optional[str] = Header(None),
):
    """
    Receive a damaged product transfer at the destination warehouse.
    Adds stock to destination, marks as received.
    """
    await _ensure_table(session)
    user_id, user_role, user_name = await _auth_user(authorization, session)

    result = await session.execute(text(
        "SELECT * FROM damaged_product_transfers WHERE id = :tid"
    ), {"tid": transfer_id})
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Transfer not found")
    if row.status == 'received':
        raise HTTPException(status_code=400, detail="Transfer already received")
    if row.status == 'cancelled':
        raise HTTPException(status_code=400, detail="Transfer was cancelled")

    receipt_notes = data.get('receipt_notes', '')
    receipt_condition = data.get('receipt_condition', 'as_expected')

    try:
        # Add stock to destination warehouse
        quantity = float(row.quantity)
        if row.product_id:
            # Check if stock level exists at destination
            dest_stock = await session.execute(text("""
                SELECT id, current_stock FROM stock_levels
                WHERE warehouse_id = :wid AND product_id = :pid
            """), {"wid": str(row.to_warehouse_id), "pid": str(row.product_id)})
            dest_row = dest_stock.fetchone()
            if dest_row:
                await session.execute(text("""
                    UPDATE stock_levels SET current_stock = current_stock + :qty
                    WHERE warehouse_id = :wid AND product_id = :pid
                """), {"qty": quantity, "wid": str(row.to_warehouse_id), "pid": str(row.product_id)})
            else:
                await session.execute(text("""
                    INSERT INTO stock_levels (id, warehouse_id, product_id, current_stock)
                    VALUES (gen_random_uuid(), :wid, :pid, :qty)
                """), {"wid": str(row.to_warehouse_id), "pid": str(row.product_id), "qty": quantity})

            # Record stock movement IN
            await session.execute(text("""
                INSERT INTO stock_movements (id, warehouse_id, product_id, movement_type, quantity, reference, notes, created_by, created_at)
                VALUES (gen_random_uuid(), :wid, :pid, 'DAMAGE_TRANSFER_IN', :qty, :ref, :notes, :uid, NOW())
            """), {
                "wid": str(row.to_warehouse_id), "pid": str(row.product_id),
                "qty": quantity, "ref": row.transfer_number,
                "notes": f"Damaged transfer received: {receipt_condition} - {receipt_notes}",
                "uid": user_id,
            })

        elif row.raw_material_id:
            dest_stock = await session.execute(text("""
                SELECT id, current_stock FROM stock_levels
                WHERE warehouse_id = :wid AND raw_material_id = :rid
            """), {"wid": str(row.to_warehouse_id), "rid": str(row.raw_material_id)})
            dest_row = dest_stock.fetchone()
            if dest_row:
                await session.execute(text("""
                    UPDATE stock_levels SET current_stock = current_stock + :qty
                    WHERE warehouse_id = :wid AND raw_material_id = :rid
                """), {"qty": quantity, "wid": str(row.to_warehouse_id), "rid": str(row.raw_material_id)})
            else:
                await session.execute(text("""
                    INSERT INTO stock_levels (id, warehouse_id, raw_material_id, current_stock)
                    VALUES (gen_random_uuid(), :wid, :rid, :qty)
                """), {"wid": str(row.to_warehouse_id), "rid": str(row.raw_material_id), "qty": quantity})

            await session.execute(text("""
                INSERT INTO stock_movements (id, warehouse_id, raw_material_id, movement_type, quantity, reference, notes, created_by, created_at)
                VALUES (gen_random_uuid(), :wid, :rid, 'DAMAGE_TRANSFER_IN', :qty, :ref, :notes, :uid, NOW())
            """), {
                "wid": str(row.to_warehouse_id), "rid": str(row.raw_material_id),
                "qty": quantity, "ref": row.transfer_number,
                "notes": f"Damaged transfer received: {receipt_condition} - {receipt_notes}",
                "uid": user_id,
            })

        # Mark transfer as received
        await session.execute(text("""
            UPDATE damaged_product_transfers
            SET status = 'received', received_by = :uid,
                received_at = NOW(), receipt_notes = :rn, receipt_condition = :rc
            WHERE id = :tid
        """), {
            "uid": user_id, "rn": receipt_notes,
            "rc": receipt_condition, "tid": transfer_id,
        })

        await session.commit()
        return {
            "success": True,
            "message": f"Damaged transfer {row.transfer_number} received successfully",
        }

    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to receive transfer: {str(e)}")


@router.get('/summary')
async def damaged_transfers_summary(
    session: AsyncSession = Depends(get_session),
):
    """Return summary stats for the damaged transfers dashboard."""
    await _ensure_table(session)

    total = await session.execute(text("SELECT COUNT(*) FROM damaged_product_transfers"))
    total_count = total.scalar() or 0

    pending = await session.execute(text(
        "SELECT COUNT(*) FROM damaged_product_transfers WHERE status = 'pending'"
    ))
    pending_count = pending.scalar() or 0

    dispatched = await session.execute(text(
        "SELECT COUNT(*) FROM damaged_product_transfers WHERE status = 'dispatched'"
    ))
    dispatched_count = dispatched.scalar() or 0

    received = await session.execute(text(
        "SELECT COUNT(*) FROM damaged_product_transfers WHERE status = 'received'"
    ))
    received_count = received.scalar() or 0

    total_qty = await session.execute(text(
        "SELECT COALESCE(SUM(quantity), 0) FROM damaged_product_transfers"
    ))
    total_quantity = float(total_qty.scalar() or 0)

    return {
        'total_transfers': total_count,
        'pending': pending_count,
        'dispatched': dispatched_count,
        'received': received_count,
        'total_quantity': total_quantity,
    }
