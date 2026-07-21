"""
Returned Products Module API
Tracks customer returns with details of action taken, reasons for return,
return condition, refund status, and stock restoration.
"""
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, and_, text
from typing import Optional
from uuid import UUID
import uuid
from datetime import datetime, timezone, date
from decimal import Decimal
from pydantic import BaseModel

from app.db import get_session
from app.services.inventory import apply_stock_movement
from app.services.posting import post_customer_return
from app.models import (
    ReturnedStock,
    Product,
    Warehouse,
    Customer,
    SalesOrder,
    Staff,
    StockLevel,
    StockMovement,
    User,
)
from app.api.auth import get_current_user

router = APIRouter(prefix='/api/returns')


# ---------- Schemas ----------
class ReturnCreate(BaseModel):
    warehouse_id: UUID
    product_id: UUID
    sales_order_id: Optional[UUID] = None
    customer_id: Optional[UUID] = None
    quantity: float
    return_reason: str
    return_condition: str  # good, damaged, defective, expired
    refund_amount: Optional[float] = None
    action_taken: Optional[str] = None
    notes: Optional[str] = None


class ReturnUpdate(BaseModel):
    refund_status: Optional[str] = None   # pending, approved, processed, rejected
    refund_amount: Optional[float] = None
    action_taken: Optional[str] = None
    notes: Optional[str] = None
    return_condition: Optional[str] = None


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


def _user_uuid(user_id):
    """Coerce the token's user id to UUID, tolerating None/garbage."""
    if not user_id:
        return None
    try:
        return uuid.UUID(str(user_id))
    except (ValueError, AttributeError, TypeError):
        return None


# ---------- Endpoints ----------

@router.get('/')
async def list_returns(
    session: AsyncSession = Depends(get_session),
):
    """List all returned products with enriched names."""
    result = await session.execute(
        select(ReturnedStock).order_by(ReturnedStock.created_at.desc())
    )
    returns = result.scalars().all()

    out = []
    for r in returns:
        # warehouse name
        wh = await session.execute(select(Warehouse).where(Warehouse.id == r.warehouse_id))
        wh = wh.scalars().first()

        # product name
        pr = await session.execute(select(Product).where(Product.id == r.product_id))
        pr = pr.scalars().first()

        # customer name
        cust_name = ''
        if r.customer_id:
            cust = await session.execute(select(Customer).where(Customer.id == r.customer_id))
            cust = cust.scalars().first()
            cust_name = cust.name if cust else ''

        # sales order number
        so_number = ''
        if r.sales_order_id:
            so = await session.execute(select(SalesOrder).where(SalesOrder.id == r.sales_order_id))
            so = so.scalars().first()
            so_number = so.order_number if so else ''

        # processed by (staff name)
        proc_name = ''
        if r.processed_by:
            st = await session.execute(select(Staff).where(Staff.id == r.processed_by))
            st = st.scalars().first()
            if st:
                proc_name = f"{st.first_name} {st.last_name}"

        out.append({
            'id': str(r.id),
            'warehouse_id': str(r.warehouse_id),
            'warehouse_name': wh.name if wh else 'N/A',
            'product_id': str(r.product_id),
            'product_name': pr.name if pr else 'N/A',
            'product_sku': pr.sku if pr else '',
            'sales_order_id': str(r.sales_order_id) if r.sales_order_id else None,
            'sales_order_number': so_number,
            'customer_id': str(r.customer_id) if r.customer_id else None,
            'customer_name': cust_name,
            'quantity': float(r.quantity),
            'return_reason': r.return_reason,
            'return_condition': r.return_condition,
            'return_date': r.return_date.isoformat() if r.return_date else None,
            'refund_status': r.refund_status,
            'refund_amount': float(r.refund_amount) if r.refund_amount else 0,
            'processed_by': str(r.processed_by) if r.processed_by else None,
            'processed_by_name': proc_name,
            'notes': r.notes or '',
            'created_at': r.created_at.isoformat() if r.created_at else None,
        })
    return out


@router.post('/')
async def create_return(
    data: ReturnCreate,
    session: AsyncSession = Depends(get_session),
    authorization: Optional[str] = Header(None),
):
    """
    Record a product return from a customer.
    - Validates product and warehouse
    - Restores stock if return condition is 'good'
    - Records stock movement
    """
    user_id, user_role, user_name = await _auth_user(authorization, session)

    if data.quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be greater than zero")

    if not data.return_reason or not data.return_reason.strip():
        raise HTTPException(status_code=400, detail="Return reason is required")

    # Validate warehouse
    wh = await session.execute(select(Warehouse).where(Warehouse.id == data.warehouse_id))
    wh = wh.scalars().first()
    if not wh:
        raise HTTPException(status_code=404, detail="Warehouse not found")

    # Validate product
    product = await session.execute(select(Product).where(Product.id == data.product_id))
    product = product.scalars().first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    qty = Decimal(str(data.quantity))

    # Find staff ID for processed_by (use first staff matching user name or None)
    processed_by_id = None
    if user_id:
        # Try to find staff record matching user
        usr = await session.execute(select(User).where(User.id == uuid.UUID(user_id)))
        usr = usr.scalars().first()
        if usr:
            st = await session.execute(
                select(Staff).where(
                    func.concat(Staff.first_name, ' ', Staff.last_name) == usr.full_name
                )
            )
            st = st.scalars().first()
            if st:
                processed_by_id = st.id

    try:
        # Create return record
        ret = ReturnedStock(
            warehouse_id=data.warehouse_id,
            product_id=data.product_id,
            sales_order_id=data.sales_order_id,
            customer_id=data.customer_id,
            quantity=qty,
            return_reason=data.return_reason.strip(),
            return_condition=data.return_condition,
            refund_status='pending',
            refund_amount=Decimal(str(data.refund_amount)) if data.refund_amount else None,
            processed_by=processed_by_id,
            notes=data.notes,
        )
        session.add(ret)

        # Only goods in resalable condition go back on the shelf. The service
        # writes the balance and the RETURN movement together, so the two can
        # never disagree the way they did when this restocked by hand.
        if data.return_condition == 'good':
            await session.flush()  # ensure ret.id exists for the reference
            await apply_stock_movement(
                session,
                warehouse_id=data.warehouse_id,
                product_id=data.product_id,
                movement_type='RETURN',
                quantity=qty,
                reference=f"RETURN-{ret.id}",
                notes=f"Return ({data.return_condition}): {data.return_reason}",
                created_by=_user_uuid(user_id),
            )
        else:
            await session.flush()  # ret.id is needed for the ledger reference

        # Reverse the revenue, and reverse COGS only if the goods actually
        # came back. Cost is taken from the original sale line where the
        # return is linked to an order; a standalone return has no cost basis
        # to reverse, so only the revenue side moves.
        refund_value = Decimal(str(data.refund_amount)) if data.refund_amount \
            else Decimal("0")
        cost_value = Decimal("0")
        if data.sales_order_id:
            row = (await session.execute(
                text("""SELECT unit_cost, cost_source
                          FROM sales_order_lines
                         WHERE sales_order_id = :o AND product_id = :p
                         LIMIT 1"""),
                {"o": str(data.sales_order_id), "p": str(data.product_id)},
            )).first()
            if row and row.cost_source != 'unknown' and row.unit_cost:
                cost_value = Decimal(str(row.unit_cost)) * qty

        if refund_value > 0:
            await post_customer_return(
                session,
                revenue_value=refund_value,
                cost_value=cost_value,
                reference=f"RETURN-{ret.id}",
                restocked=(data.return_condition == 'good'),
                created_by=_user_uuid(user_id),
            )

        await session.commit()
        condition_msg = " Stock restored." if data.return_condition == 'good' else " Stock NOT restored (damaged/defective)."
        return {
            "success": True,
            "message": f"Return recorded for {float(qty)} x {product.name}.{condition_msg}",
        }

    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to record return: {str(e)}")


@router.put('/{return_id}')
async def update_return(
    return_id: UUID,
    data: ReturnUpdate,
    session: AsyncSession = Depends(get_session),
    authorization: Optional[str] = Header(None),
):
    """Update a return record (e.g., refund status, action taken)."""
    user_id, _role, _name = await _auth_user(authorization, session)

    result = await session.execute(
        select(ReturnedStock).where(ReturnedStock.id == return_id)
    )
    ret = result.scalars().first()
    if not ret:
        raise HTTPException(status_code=404, detail="Return record not found")

    if data.refund_status is not None:
        ret.refund_status = data.refund_status
    if data.refund_amount is not None:
        ret.refund_amount = Decimal(str(data.refund_amount))
    if data.notes is not None:
        ret.notes = data.notes

    try:
        # Reclassifying the condition changes whether the goods are sellable,
        # so it has to move stock. create_return only restocks when the
        # condition is 'good'; letting this field be edited afterwards with no
        # compensating movement meant a damaged->good reclassification claimed
        # the units were back on the shelf while the balance never changed
        # (and good->damaged left phantom sellable stock).
        if (data.return_condition is not None
                and data.return_condition != ret.return_condition):
            was_good = ret.return_condition == 'good'
            now_good = data.return_condition == 'good'

            if now_good and not was_good:
                await apply_stock_movement(
                    session,
                    warehouse_id=ret.warehouse_id,
                    product_id=ret.product_id,
                    movement_type='RETURN',
                    quantity=ret.quantity,
                    reference=f"RETURN-RECLASS-{ret.id}",
                    notes=(f"Reclassified {ret.return_condition} -> "
                           f"{data.return_condition}; goods restocked"),
                    created_by=_user_uuid(user_id),
                )
            elif was_good and not now_good:
                await apply_stock_movement(
                    session,
                    warehouse_id=ret.warehouse_id,
                    product_id=ret.product_id,
                    movement_type='DAMAGE',
                    quantity=ret.quantity,
                    reference=f"RETURN-RECLASS-{ret.id}",
                    notes=(f"Reclassified {ret.return_condition} -> "
                           f"{data.return_condition}; goods removed from "
                           f"sellable stock"),
                    created_by=_user_uuid(user_id),
                )

            ret.return_condition = data.return_condition

        await session.commit()
        return {"success": True, "message": "Return record updated successfully"}
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Update failed: {str(e)}")


@router.delete('/{return_id}')
async def delete_return(
    return_id: UUID,
    session: AsyncSession = Depends(get_session),
    authorization: Optional[str] = Header(None),
):
    """Delete a return record."""
    user_id, _role, _name = await _auth_user(authorization, session)

    result = await session.execute(
        select(ReturnedStock).where(ReturnedStock.id == return_id)
    )
    ret = result.scalars().first()
    if not ret:
        raise HTTPException(status_code=404, detail="Return record not found")

    try:
        # A 'good' return added stock and wrote a RETURN movement. Deleting
        # the record without reversing that left the balance permanently
        # inflated and the ledger row pointing at a reference that no longer
        # resolves -- unexplainable stock. Reverse it first, so the deletion
        # is a net-zero pair in the ledger rather than a silent gap.
        if ret.return_condition == 'good':
            await apply_stock_movement(
                session,
                warehouse_id=ret.warehouse_id,
                product_id=ret.product_id,
                movement_type='OUT',
                quantity=ret.quantity,
                reference=f"RETURN-REVERSAL-{ret.id}",
                notes="Reversal of restock for deleted return record",
                created_by=_user_uuid(user_id),
                # The restocked units may already have been sold on. Allow the
                # reversal through so the record can still be corrected; the
                # negative balance is itself the signal that needs attention.
                allow_negative=True,
            )

        await session.delete(ret)
        await session.commit()
        return {"success": True, "message": "Return record deleted"}
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Delete failed: {str(e)}")


@router.get('/summary')
async def returns_summary(
    session: AsyncSession = Depends(get_session),
):
    """Return summary stats for the returns dashboard."""
    # Total returns
    total = await session.execute(select(func.count(ReturnedStock.id)))
    total_count = total.scalar() or 0

    # Total quantity returned
    total_qty = await session.execute(select(func.sum(ReturnedStock.quantity)))
    total_quantity = float(total_qty.scalar() or 0)

    # Pending refunds
    pending = await session.execute(
        select(func.count(ReturnedStock.id)).where(
            ReturnedStock.refund_status == 'pending'
        )
    )
    pending_count = pending.scalar() or 0

    # Total refund amount (processed)
    refund_total = await session.execute(
        select(func.sum(ReturnedStock.refund_amount)).where(
            ReturnedStock.refund_status == 'processed'
        )
    )
    refund_amount = float(refund_total.scalar() or 0)

    # Today's returns
    today = date.today()
    today_count_r = await session.execute(
        select(func.count(ReturnedStock.id)).where(
            ReturnedStock.return_date == today
        )
    )
    today_count = today_count_r.scalar() or 0

    # Condition breakdown
    conditions = {}
    for cond in ['good', 'damaged', 'defective', 'expired']:
        c = await session.execute(
            select(func.count(ReturnedStock.id)).where(
                ReturnedStock.return_condition == cond
            )
        )
        conditions[cond] = c.scalar() or 0

    return {
        'total_returns': total_count,
        'total_quantity_returned': total_quantity,
        'pending_refunds': pending_count,
        'total_refund_amount': refund_amount,
        'today_returns': today_count,
        'condition_breakdown': conditions,
    }
