"""
Receive Transfers Module API
Shows all incoming transfers (both regular warehouse transfers and damaged product transfers)
that need to be received/confirmed at the destination warehouse.
Provides a unified view of all pending incoming shipments.
"""
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Optional

from app.db import get_session
from app.api.auth import get_current_user

router = APIRouter(prefix='/api/receive-transfers')


async def _auth_user(authorization: Optional[str], session: AsyncSession):
    if not authorization or not authorization.startswith("Bearer "):
        return None, None, None
    token = authorization.replace("Bearer ", "")
    try:
        payload = await get_current_user(token, session)
        return payload.get('id'), payload.get('role'), payload.get('full_name', '')
    except Exception:
        return None, None, None


@router.get('/')
async def list_pending_receipts(
    session: AsyncSession = Depends(get_session),
):
    """
    List all transfers awaiting receipt - combines:
    1. Damaged product transfers with status pending/dispatched
    2. Regular warehouse transfers (already completed, shown for history)
    """
    # Ensure damaged_product_transfers table exists
    await session.execute(text("""
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
        )
    """))
    await session.commit()

    # Pending/dispatched damaged transfers
    dmg_result = await session.execute(text("""
        SELECT dpt.id, dpt.transfer_number, dpt.from_warehouse_id, dpt.to_warehouse_id,
               dpt.product_id, dpt.raw_material_id, dpt.quantity, dpt.damage_type,
               dpt.damage_reason, dpt.action_taken, dpt.status, dpt.notes,
               dpt.created_by, dpt.created_at, dpt.dispatched_at,
               dpt.received_by, dpt.received_at, dpt.receipt_notes, dpt.receipt_condition,
               fw.name AS from_warehouse_name,
               tw.name AS to_warehouse_name,
               p.name AS product_name,
               p.sku AS product_sku,
               rm.name AS raw_material_name,
               u1.full_name AS created_by_name,
               u2.full_name AS received_by_name,
               'damaged' AS transfer_type
        FROM damaged_product_transfers dpt
        LEFT JOIN warehouses fw ON fw.id = dpt.from_warehouse_id
        LEFT JOIN warehouses tw ON tw.id = dpt.to_warehouse_id
        LEFT JOIN products p ON p.id = dpt.product_id
        LEFT JOIN raw_materials rm ON rm.id = dpt.raw_material_id
        LEFT JOIN users u1 ON u1.id = dpt.created_by
        LEFT JOIN users u2 ON u2.id = dpt.received_by
        ORDER BY dpt.created_at DESC
    """))
    dmg_rows = dmg_result.fetchall()

    # Regular warehouse transfers (all)
    reg_result = await session.execute(text("""
        SELECT wt.id, wt.transfer_number, wt.from_warehouse_id, wt.to_warehouse_id,
               wt.product_id, NULL AS raw_material_id, wt.quantity,
               'N/A' AS damage_type, '' AS damage_reason, '' AS action_taken,
               wt.status, wt.notes,
               wt.created_by, wt.created_at, NULL AS dispatched_at,
               NULL AS received_by, NULL AS received_at, '' AS receipt_notes, '' AS receipt_condition,
               fw.name AS from_warehouse_name,
               tw.name AS to_warehouse_name,
               p.name AS product_name,
               p.sku AS product_sku,
               '' AS raw_material_name,
               u1.full_name AS created_by_name,
               '' AS received_by_name,
               'regular' AS transfer_type
        FROM warehouse_transfers wt
        LEFT JOIN warehouses fw ON fw.id = wt.from_warehouse_id
        LEFT JOIN warehouses tw ON tw.id = wt.to_warehouse_id
        LEFT JOIN products p ON p.id = wt.product_id
        LEFT JOIN users u1 ON u1.id = wt.created_by
        ORDER BY wt.created_at DESC
    """))
    reg_rows = reg_result.fetchall()

    out = []
    for r in dmg_rows:
        out.append({
            'id': str(r.id),
            'transfer_number': r.transfer_number,
            'transfer_type': 'damaged',
            'from_warehouse_id': str(r.from_warehouse_id),
            'from_warehouse_name': r.from_warehouse_name or 'N/A',
            'to_warehouse_id': str(r.to_warehouse_id),
            'to_warehouse_name': r.to_warehouse_name or 'N/A',
            'product_id': str(r.product_id) if r.product_id else None,
            'product_name': r.product_name or (r.raw_material_name or 'N/A'),
            'product_sku': r.product_sku or '',
            'quantity': float(r.quantity),
            'damage_type': r.damage_type,
            'damage_reason': r.damage_reason or '',
            'action_taken': r.action_taken or '',
            'status': r.status,
            'notes': r.notes or '',
            'created_by_name': r.created_by_name or '',
            'received_by_name': r.received_by_name or '',
            'created_at': r.created_at.isoformat() if r.created_at else None,
            'dispatched_at': r.dispatched_at.isoformat() if r.dispatched_at else None,
            'received_at': r.received_at.isoformat() if r.received_at else None,
            'receipt_notes': r.receipt_notes or '',
            'receipt_condition': r.receipt_condition or '',
            'can_receive': r.status in ('pending', 'dispatched'),
        })
    for r in reg_rows:
        out.append({
            'id': str(r.id),
            'transfer_number': r.transfer_number,
            'transfer_type': 'regular',
            'from_warehouse_id': str(r.from_warehouse_id),
            'from_warehouse_name': r.from_warehouse_name or 'N/A',
            'to_warehouse_id': str(r.to_warehouse_id),
            'to_warehouse_name': r.to_warehouse_name or 'N/A',
            'product_id': str(r.product_id) if r.product_id else None,
            'product_name': r.product_name or 'N/A',
            'product_sku': r.product_sku or '',
            'quantity': float(r.quantity),
            'damage_type': 'N/A',
            'damage_reason': '',
            'action_taken': '',
            'status': r.status,
            'notes': r.notes or '',
            'created_by_name': r.created_by_name or '',
            'received_by_name': '',
            'created_at': r.created_at.isoformat() if r.created_at else None,
            'dispatched_at': None,
            'received_at': None,
            'receipt_notes': '',
            'receipt_condition': '',
            'can_receive': False,  # Regular transfers are already completed atomically
        })

    # Sort all by created_at descending
    out.sort(key=lambda x: x['created_at'] or '', reverse=True)
    return out


@router.post('/{transfer_id}/receive')
async def receive_transfer(
    transfer_id: str,
    data: dict,
    session: AsyncSession = Depends(get_session),
    authorization: Optional[str] = Header(None),
):
    """Receive a damaged product transfer at the destination warehouse.

    This genuinely delegates to the damaged-transfers implementation rather
    than reimplementing it. It previously carried its own copy of the receive
    logic against the same table, and the two copies had already drifted --
    both suffered the same double-receive race, and fixing one would have left
    the other able to double-add stock. One implementation, one place to fix.
    """
    from app.api.damaged_transfers import receive_damaged_transfer

    return await receive_damaged_transfer(
        transfer_id=transfer_id,
        data=data,
        session=session,
        authorization=authorization,
    )


@router.get('/summary')
async def receive_summary(
    session: AsyncSession = Depends(get_session),
):
    """Summary stats for the receive module dashboard."""
    # Ensure table exists
    await session.execute(text("""
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
        )
    """))
    await session.commit()

    # Damaged - awaiting receipt
    pending_dmg = await session.execute(text(
        "SELECT COUNT(*) FROM damaged_product_transfers WHERE status IN ('pending', 'dispatched')"
    ))
    pending_damaged = pending_dmg.scalar() or 0

    # Damaged - received
    received_dmg = await session.execute(text(
        "SELECT COUNT(*) FROM damaged_product_transfers WHERE status = 'received'"
    ))
    received_damaged = received_dmg.scalar() or 0

    # Regular completed transfers
    regular = await session.execute(text(
        "SELECT COUNT(*) FROM warehouse_transfers WHERE status = 'completed'"
    ))
    regular_completed = regular.scalar() or 0

    # Total qty awaiting receipt
    pending_qty = await session.execute(text(
        "SELECT COALESCE(SUM(quantity), 0) FROM damaged_product_transfers WHERE status IN ('pending', 'dispatched')"
    ))
    pending_quantity = float(pending_qty.scalar() or 0)

    return {
        'pending_damaged_transfers': pending_damaged,
        'received_damaged_transfers': received_damaged,
        'regular_completed_transfers': regular_completed,
        'pending_receipt_quantity': pending_quantity,
        'total_all_transfers': pending_damaged + received_damaged + regular_completed,
    }
