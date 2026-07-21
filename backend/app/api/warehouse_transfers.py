from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, and_
from typing import Optional
from uuid import UUID
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pydantic import BaseModel

from app.db import get_session
from app.services.inventory import transfer_stock
from app.models import (
    WarehouseTransfer,
    Warehouse,
    Product,
    StockLevel,
    StockMovement,
    User,
)
from app.api.auth import get_current_user

router = APIRouter(prefix='/api/transfers')


# ---------- Schemas ----------
class TransferCreate(BaseModel):
    from_warehouse_id: UUID
    to_warehouse_id: UUID
    product_id: UUID
    quantity: float
    reason: str
    notes: Optional[str] = None


class TransferResponse(BaseModel):
    message: str
    transfer_number: Optional[str] = None


# ---------- Helpers ----------
async def _auth_user(authorization: Optional[str], session: AsyncSession):
    """Extract user info from token."""
    if not authorization or not authorization.startswith("Bearer "):
        return None, None, None
    token = authorization.replace("Bearer ", "")
    try:
        payload = await get_current_user(token, session)
        return payload.get('id'), payload.get('role'), payload.get('full_name', '')
    except Exception:
        return None, None, None


# ---------- Endpoints ----------

@router.get('/')
async def list_transfers(
    session: AsyncSession = Depends(get_session),
):
    """List all warehouse transfers."""
    result = await session.execute(
        select(WarehouseTransfer).order_by(WarehouseTransfer.created_at.desc())
    )
    transfers = result.scalars().all()

    # Enrich with names
    out = []
    for t in transfers:
        # from warehouse
        fw = await session.execute(select(Warehouse).where(Warehouse.id == t.from_warehouse_id))
        fw = fw.scalars().first()
        # to warehouse
        tw = await session.execute(select(Warehouse).where(Warehouse.id == t.to_warehouse_id))
        tw = tw.scalars().first()
        # product
        pr = await session.execute(select(Product).where(Product.id == t.product_id))
        pr = pr.scalars().first()
        # created_by user
        user_name = ''
        if t.created_by:
            u = await session.execute(select(User).where(User.id == t.created_by))
            u = u.scalars().first()
            user_name = u.full_name if u else ''

        out.append({
            'id': str(t.id),
            'transfer_number': t.transfer_number,
            'from_warehouse_id': str(t.from_warehouse_id),
            'from_warehouse_name': fw.name if fw else 'N/A',
            'to_warehouse_id': str(t.to_warehouse_id),
            'to_warehouse_name': tw.name if tw else 'N/A',
            'product_id': str(t.product_id),
            'product_name': pr.name if pr else 'N/A',
            'product_sku': pr.sku if pr else '',
            'quantity': float(t.quantity),
            'reason': t.reason,
            'status': t.status,
            'notes': t.notes or '',
            'created_by': str(t.created_by) if t.created_by else None,
            'created_by_name': user_name,
            'created_at': t.created_at.isoformat() if t.created_at else None,
        })
    return out


@router.post('/', response_model=TransferResponse)
async def create_transfer(
    data: TransferCreate,
    session: AsyncSession = Depends(get_session),
    authorization: Optional[str] = Header(None),
):
    """
    Create a warehouse transfer.
    - Deducts stock from source warehouse
    - Adds stock to destination warehouse
    - Records stock movements on both sides
    Only admin and production staff roles are allowed.
    """
    user_id, user_role, user_name = await _auth_user(authorization, session)

    # Role check: only admin and production
    allowed_roles = {'admin', 'production', 'production_staff', 'warehouse', 'warehouse_manager'}
    if user_role and user_role not in allowed_roles:
        raise HTTPException(status_code=403, detail="Only admin and production staff can create transfers")

    # Validate same warehouse
    if data.from_warehouse_id == data.to_warehouse_id:
        raise HTTPException(status_code=400, detail="Source and destination warehouses must be different")

    if data.quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be greater than zero")

    if not data.reason or not data.reason.strip():
        raise HTTPException(status_code=400, detail="Reason for transfer is required")

    # Validate warehouses exist
    from_wh = await session.execute(select(Warehouse).where(Warehouse.id == data.from_warehouse_id))
    from_wh = from_wh.scalars().first()
    if not from_wh:
        raise HTTPException(status_code=404, detail="Source warehouse not found")

    to_wh = await session.execute(select(Warehouse).where(Warehouse.id == data.to_warehouse_id))
    to_wh = to_wh.scalars().first()
    if not to_wh:
        raise HTTPException(status_code=404, detail="Destination warehouse not found")

    # Validate product exists
    product = await session.execute(select(Product).where(Product.id == data.product_id))
    product = product.scalars().first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    qty = Decimal(str(data.quantity))

    # Generate transfer number
    transfer_number = f"TRF-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"

    try:
        user_uuid = uuid.UUID(user_id) if user_id else None

        # Both legs, both movements, one locked and validated path.
        #
        # The stock check used to happen on an unlocked read well before the
        # decrement, so two concurrent transfers of 60 against 100 units both
        # passed the guard and both wrote 40 -- inventory appeared from
        # nowhere. transfer_stock locks each balance row before reading it and
        # locks the two warehouses in a consistent order, so simultaneous
        # A->B and B->A transfers cannot deadlock.
        await transfer_stock(
            session,
            from_warehouse_id=data.from_warehouse_id,
            to_warehouse_id=data.to_warehouse_id,
            product_id=data.product_id,
            quantity=qty,
            reference=transfer_number,
            notes=f"{from_wh.name} -> {to_wh.name}: {data.reason}",
            created_by=user_uuid,
        )

        # 4. Create transfer record
        transfer = WarehouseTransfer(
            transfer_number=transfer_number,
            from_warehouse_id=data.from_warehouse_id,
            to_warehouse_id=data.to_warehouse_id,
            product_id=data.product_id,
            quantity=qty,
            reason=data.reason.strip(),
            notes=data.notes,
            created_by=user_uuid,
            status='completed',
        )
        session.add(transfer)

        await session.commit()
        return TransferResponse(message=f"Transfer {transfer_number} completed successfully", transfer_number=transfer_number)

    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Transfer failed: {str(e)}")


@router.get('/summary')
async def transfer_summary(
    session: AsyncSession = Depends(get_session),
):
    """Return summary stats for the transfer dashboard."""
    # Total transfers
    total = await session.execute(select(func.count(WarehouseTransfer.id)))
    total_count = total.scalar() or 0

    # Total quantity moved
    total_qty = await session.execute(select(func.sum(WarehouseTransfer.quantity)))
    total_quantity = float(total_qty.scalar() or 0)

    # Today's transfers
    from datetime import date
    today = date.today()
    today_count_r = await session.execute(
        select(func.count(WarehouseTransfer.id)).where(
            func.date(WarehouseTransfer.created_at) == today
        )
    )
    today_count = today_count_r.scalar() or 0

    return {
        'total_transfers': total_count,
        'total_quantity_moved': total_quantity,
        'today_transfers': today_count,
    }
