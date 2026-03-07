"""
Previous / Legacy Debts Module API
Records and tracks debts that existed before the ERP system was deployed.
These debts are integrated with the main debtors dashboard and WhatsApp reminders.
Auto-creates the legacy_debts and legacy_debt_payments tables if they don't exist.
"""
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Optional
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.db import get_session
from app.api.auth import get_current_user

router = APIRouter(prefix='/api/legacy-debts')

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS legacy_debts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    debt_number VARCHAR(64) UNIQUE NOT NULL,
    customer_id UUID NOT NULL REFERENCES customers(id),
    description TEXT NOT NULL,
    original_amount NUMERIC(18,2) NOT NULL,
    paid_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    debt_date DATE NOT NULL,
    due_date DATE,
    notes TEXT,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
)
"""

CREATE_PAYMENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS legacy_debt_payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    legacy_debt_id UUID NOT NULL REFERENCES legacy_debts(id) ON DELETE CASCADE,
    amount NUMERIC(18,2) NOT NULL,
    payment_method VARCHAR(50) NOT NULL DEFAULT 'bank_transfer',
    payment_date TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    reference VARCHAR(255),
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
)
"""

CREATE_INDEX_SQLS = [
    "CREATE INDEX IF NOT EXISTS idx_ld_customer ON legacy_debts(customer_id)",
    "CREATE INDEX IF NOT EXISTS idx_ld_status ON legacy_debts(status)",
    "CREATE INDEX IF NOT EXISTS idx_ldp_debt ON legacy_debt_payments(legacy_debt_id)",
]


async def _ensure_tables(session: AsyncSession):
    await session.execute(text(CREATE_TABLE_SQL))
    await session.execute(text(CREATE_PAYMENTS_TABLE_SQL))
    for idx_sql in CREATE_INDEX_SQLS:
        await session.execute(text(idx_sql))
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


def _generate_debt_number():
    """Generate a unique legacy debt number like LD-0001"""
    return f"LD-{uuid.uuid4().hex[:8].upper()}"


# ─── LIST ALL LEGACY DEBTS ────────────────────────────────────────────────────
@router.get('/')
async def list_legacy_debts(
    status: Optional[str] = None,
    customer_id: Optional[str] = None,
    session: AsyncSession = Depends(get_session)
):
    """List all legacy debts with customer info and payment summary"""
    await _ensure_tables(session)

    where_clauses = []
    params = {}

    if status:
        where_clauses.append("ld.status = :status")
        params["status"] = status

    if customer_id:
        where_clauses.append("ld.customer_id = :customer_id")
        params["customer_id"] = customer_id

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    sql = text(f"""
        SELECT ld.id, ld.debt_number, ld.customer_id, ld.description,
               ld.original_amount, ld.paid_amount, ld.status,
               ld.debt_date, ld.due_date, ld.notes, ld.created_at,
               c.name as customer_name, c.phone as customer_phone,
               COALESCE(SUM(ldp.amount), 0) as actual_paid
        FROM legacy_debts ld
        JOIN customers c ON c.id = ld.customer_id
        LEFT JOIN legacy_debt_payments ldp ON ldp.legacy_debt_id = ld.id
        {where_sql}
        GROUP BY ld.id, ld.debt_number, ld.customer_id, ld.description,
                 ld.original_amount, ld.paid_amount, ld.status,
                 ld.debt_date, ld.due_date, ld.notes, ld.created_at,
                 c.name, c.phone
        ORDER BY ld.created_at DESC
    """)

    result = await session.execute(sql, params)
    rows = result.fetchall()

    debts = []
    total_owed = 0
    total_paid = 0
    for r in rows:
        original = float(r.original_amount or 0)
        paid = float(r.actual_paid or 0)
        balance = original - paid
        total_owed += original
        total_paid += paid

        debts.append({
            "id": str(r.id),
            "debt_number": r.debt_number,
            "customer_id": str(r.customer_id),
            "customer_name": r.customer_name,
            "customer_phone": r.customer_phone,
            "description": r.description,
            "original_amount": original,
            "paid_amount": paid,
            "balance": max(balance, 0),
            "status": r.status,
            "debt_date": r.debt_date.isoformat() if r.debt_date else None,
            "due_date": r.due_date.isoformat() if r.due_date else None,
            "notes": r.notes or '',
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })

    return {
        "debts": debts,
        "total": len(debts),
        "summary": {
            "total_debts": len(debts),
            "total_original": total_owed,
            "total_paid": total_paid,
            "total_balance": total_owed - total_paid,
        }
    }


# ─── GET SINGLE LEGACY DEBT DETAIL ────────────────────────────────────────────
@router.get('/{debt_id}')
async def get_legacy_debt_detail(debt_id: str, session: AsyncSession = Depends(get_session)):
    """Get full detail for a single legacy debt including all payments"""
    await _ensure_tables(session)

    debt_sql = text("""
        SELECT ld.*, c.name as customer_name, c.phone as customer_phone, c.email as customer_email
        FROM legacy_debts ld
        JOIN customers c ON c.id = ld.customer_id
        WHERE ld.id = :debt_id
    """)
    result = await session.execute(debt_sql, {"debt_id": debt_id})
    debt = result.fetchone()
    if not debt:
        raise HTTPException(status_code=404, detail="Legacy debt not found")

    # Payments
    pay_sql = text("""
        SELECT id, amount, payment_method, payment_date, reference, notes, created_at
        FROM legacy_debt_payments
        WHERE legacy_debt_id = :debt_id
        ORDER BY payment_date ASC
    """)
    pay_result = await session.execute(pay_sql, {"debt_id": debt_id})
    payments = []
    running_balance = float(debt.original_amount)
    for p in pay_result.fetchall():
        running_balance -= float(p.amount)
        payments.append({
            "id": str(p.id),
            "amount": float(p.amount),
            "payment_method": p.payment_method,
            "payment_date": p.payment_date.isoformat() if p.payment_date else None,
            "reference": p.reference or '',
            "notes": p.notes or '',
            "running_balance": max(running_balance, 0),
        })

    original = float(debt.original_amount)
    total_paid = sum(p["amount"] for p in payments)

    return {
        "id": str(debt.id),
        "debt_number": debt.debt_number,
        "customer_id": str(debt.customer_id),
        "customer_name": debt.customer_name,
        "customer_phone": debt.customer_phone,
        "customer_email": debt.customer_email,
        "description": debt.description,
        "original_amount": original,
        "paid_amount": total_paid,
        "balance": max(original - total_paid, 0),
        "status": debt.status,
        "debt_date": debt.debt_date.isoformat() if debt.debt_date else None,
        "due_date": debt.due_date.isoformat() if debt.due_date else None,
        "notes": debt.notes or '',
        "payments": payments,
    }


# ─── CREATE A NEW LEGACY DEBT ────────────────────────────────────────────────
@router.post('/')
async def create_legacy_debt(
    data: dict,
    session: AsyncSession = Depends(get_session),
    authorization: Optional[str] = Header(None)
):
    """Record a new previous / legacy debt for a customer"""
    await _ensure_tables(session)
    user_id, _, _ = await _auth_user(authorization, session)

    customer_id = data.get('customer_id')
    description = data.get('description')
    original_amount = data.get('original_amount')
    debt_date = data.get('debt_date')

    if not customer_id or not description or not original_amount or not debt_date:
        raise HTTPException(
            status_code=400,
            detail="customer_id, description, original_amount, and debt_date are required"
        )

    try:
        amount = float(original_amount)
        if amount <= 0:
            raise ValueError
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="original_amount must be a positive number")

    # Check if customer exists
    cust_check = await session.execute(
        text("SELECT id FROM customers WHERE id = :cid"),
        {"cid": customer_id}
    )
    if not cust_check.fetchone():
        raise HTTPException(status_code=404, detail="Customer not found")

    # Generate sequential debt number
    seq_result = await session.execute(text("SELECT COUNT(*) FROM legacy_debts"))
    count = seq_result.scalar() or 0
    debt_number = f"LD-{count + 1:04d}"

    # Check uniqueness
    dup_check = await session.execute(
        text("SELECT id FROM legacy_debts WHERE debt_number = :dn"),
        {"dn": debt_number}
    )
    if dup_check.fetchone():
        debt_number = f"LD-{uuid.uuid4().hex[:6].upper()}"

    # Amount already paid (from before system)
    already_paid = float(data.get('amount_already_paid', 0))
    if already_paid < 0:
        already_paid = 0
    if already_paid > amount:
        already_paid = amount

    balance = amount - already_paid
    status = 'paid' if balance <= 0.01 else ('partial' if already_paid > 0 else 'pending')

    debt_id = str(uuid.uuid4())

    insert_sql = text("""
        INSERT INTO legacy_debts (id, debt_number, customer_id, description, original_amount, 
                                   paid_amount, status, debt_date, due_date, notes, created_by)
        VALUES (:id, :debt_number, :customer_id, :description, :original_amount,
                :paid_amount, :status, :debt_date, :due_date, :notes, :created_by)
    """)

    await session.execute(insert_sql, {
        "id": debt_id,
        "debt_number": debt_number,
        "customer_id": customer_id,
        "description": description,
        "original_amount": amount,
        "paid_amount": already_paid,
        "status": status,
        "debt_date": debt_date,
        "due_date": data.get('due_date'),
        "notes": data.get('notes', ''),
        "created_by": user_id,
    })

    # If there was an amount already paid, record it as a payment
    if already_paid > 0:
        await session.execute(text("""
            INSERT INTO legacy_debt_payments (id, legacy_debt_id, amount, payment_method, payment_date, reference, notes)
            VALUES (:id, :debt_id, :amount, :method, :pay_date, :ref, :notes)
        """), {
            "id": str(uuid.uuid4()),
            "debt_id": debt_id,
            "amount": already_paid,
            "method": "historical",
            "pay_date": debt_date,
            "ref": "Pre-system payment",
            "notes": "Amount already paid before system recording",
        })

    await session.commit()

    return {
        "message": f"Legacy debt {debt_number} recorded successfully",
        "debt_id": debt_id,
        "debt_number": debt_number,
        "balance": balance,
        "status": status,
    }


# ─── UPDATE A LEGACY DEBT ─────────────────────────────────────────────────────
@router.put('/{debt_id}')
async def update_legacy_debt(
    debt_id: str,
    data: dict,
    session: AsyncSession = Depends(get_session)
):
    """Update legacy debt details (description, notes, due_date)"""
    await _ensure_tables(session)

    # Check existence
    check = await session.execute(
        text("SELECT id FROM legacy_debts WHERE id = :id"), {"id": debt_id}
    )
    if not check.fetchone():
        raise HTTPException(status_code=404, detail="Legacy debt not found")

    updates = []
    params = {"id": debt_id}

    if 'description' in data:
        updates.append("description = :description")
        params["description"] = data["description"]
    if 'due_date' in data:
        updates.append("due_date = :due_date")
        params["due_date"] = data["due_date"]
    if 'notes' in data:
        updates.append("notes = :notes")
        params["notes"] = data["notes"]
    if 'original_amount' in data:
        try:
            new_amount = float(data["original_amount"])
            if new_amount > 0:
                updates.append("original_amount = :original_amount")
                params["original_amount"] = new_amount
        except (ValueError, TypeError):
            pass

    if not updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")

    updates.append("updated_at = NOW()")
    sql = text(f"UPDATE legacy_debts SET {', '.join(updates)} WHERE id = :id")
    await session.execute(sql, params)

    # Recalculate status
    await _recalculate_debt_status(session, debt_id)
    await session.commit()

    return {"message": "Legacy debt updated successfully"}


# ─── DELETE A LEGACY DEBT ──────────────────────────────────────────────────────
@router.delete('/{debt_id}')
async def delete_legacy_debt(
    debt_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Delete a legacy debt (and all its payments via CASCADE)"""
    await _ensure_tables(session)

    check = await session.execute(
        text("SELECT id, debt_number FROM legacy_debts WHERE id = :id"), {"id": debt_id}
    )
    row = check.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Legacy debt not found")

    await session.execute(text("DELETE FROM legacy_debts WHERE id = :id"), {"id": debt_id})
    await session.commit()

    return {"message": f"Legacy debt {row.debt_number} deleted successfully"}


# ─── RECORD PAYMENT AGAINST LEGACY DEBT ──────────────────────────────────────
@router.post('/{debt_id}/payments')
async def record_legacy_debt_payment(
    debt_id: str,
    data: dict,
    session: AsyncSession = Depends(get_session)
):
    """Record a payment against a legacy debt"""
    await _ensure_tables(session)

    # Check debt exists
    debt_result = await session.execute(
        text("SELECT id, original_amount, status FROM legacy_debts WHERE id = :id"),
        {"id": debt_id}
    )
    debt = debt_result.fetchone()
    if not debt:
        raise HTTPException(status_code=404, detail="Legacy debt not found")

    if debt.status == 'paid':
        raise HTTPException(status_code=400, detail="This debt is already fully paid")

    amount = data.get('amount')
    if not amount:
        raise HTTPException(status_code=400, detail="amount is required")

    try:
        amount = float(amount)
        if amount <= 0:
            raise ValueError
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="amount must be a positive number")

    # Insert payment
    payment_id = str(uuid.uuid4())
    await session.execute(text("""
        INSERT INTO legacy_debt_payments (id, legacy_debt_id, amount, payment_method, payment_date, reference, notes)
        VALUES (:id, :debt_id, :amount, :method, :pay_date, :ref, :notes)
    """), {
        "id": payment_id,
        "debt_id": debt_id,
        "amount": amount,
        "method": data.get('payment_method', 'bank_transfer'),
        "pay_date": data.get('payment_date', datetime.now(timezone.utc).isoformat()),
        "ref": data.get('reference', ''),
        "notes": data.get('notes', ''),
    })

    # Recalculate status
    await _recalculate_debt_status(session, debt_id)
    await session.commit()

    return {
        "message": "Payment recorded successfully",
        "payment_id": payment_id,
    }


# ─── DELETE PAYMENT FROM LEGACY DEBT ─────────────────────────────────────────
@router.delete('/payments/{payment_id}')
async def delete_legacy_debt_payment(
    payment_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Delete a payment from a legacy debt and recalculate balance"""
    await _ensure_tables(session)

    # Find payment
    pay_result = await session.execute(
        text("SELECT id, legacy_debt_id FROM legacy_debt_payments WHERE id = :id"),
        {"id": payment_id}
    )
    pay = pay_result.fetchone()
    if not pay:
        raise HTTPException(status_code=404, detail="Payment not found")

    debt_id = str(pay.legacy_debt_id)

    await session.execute(
        text("DELETE FROM legacy_debt_payments WHERE id = :id"),
        {"id": payment_id}
    )

    await _recalculate_debt_status(session, debt_id)
    await session.commit()

    return {"message": "Payment deleted and balance recalculated"}


# ─── SUMMARY / DASHBOARD ─────────────────────────────────────────────────────
@router.get('/summary/stats')
async def legacy_debts_summary(session: AsyncSession = Depends(get_session)):
    """Get summary statistics for all legacy debts"""
    await _ensure_tables(session)

    sql = text("""
        SELECT
            COUNT(*) as total_debts,
            COUNT(CASE WHEN status = 'pending' THEN 1 END) as pending_debts,
            COUNT(CASE WHEN status = 'partial' THEN 1 END) as partial_debts,
            COUNT(CASE WHEN status = 'paid' THEN 1 END) as paid_debts,
            COALESCE(SUM(original_amount), 0) as total_original,
            COALESCE(SUM(paid_amount), 0) as total_paid_stored,
            COALESCE(
                (SELECT SUM(ldp.amount) FROM legacy_debt_payments ldp 
                 JOIN legacy_debts ld2 ON ld2.id = ldp.legacy_debt_id),
            0) as total_paid_actual,
            COUNT(DISTINCT customer_id) as unique_customers
        FROM legacy_debts
    """)
    result = await session.execute(sql)
    r = result.fetchone()

    total_original = float(r.total_original or 0)
    total_paid = float(r.total_paid_actual or 0)

    return {
        "total_debts": r.total_debts or 0,
        "pending": r.pending_debts or 0,
        "partial": r.partial_debts or 0,
        "paid": r.paid_debts or 0,
        "total_original": total_original,
        "total_paid": total_paid,
        "total_balance": total_original - total_paid,
        "unique_customers": r.unique_customers or 0,
        "collection_rate": round((total_paid / total_original * 100) if total_original > 0 else 0, 1),
    }


# ─── HELPER: RECALCULATE DEBT STATUS ────────────────────────────────────────
async def _recalculate_debt_status(session: AsyncSession, debt_id: str):
    """Recalculate paid_amount and status for a legacy debt based on its payments"""
    paid_sql = text("""
        SELECT COALESCE(SUM(amount), 0) as total_paid
        FROM legacy_debt_payments WHERE legacy_debt_id = :debt_id
    """)
    paid_result = await session.execute(paid_sql, {"debt_id": debt_id})
    total_paid = float(paid_result.scalar() or 0)

    debt_result = await session.execute(
        text("SELECT original_amount FROM legacy_debts WHERE id = :id"),
        {"id": debt_id}
    )
    debt = debt_result.fetchone()
    if not debt:
        return

    original = float(debt.original_amount)
    balance = original - total_paid

    if balance <= 0.01:
        status = 'paid'
    elif total_paid > 0:
        status = 'partial'
    else:
        status = 'pending'

    await session.execute(text("""
        UPDATE legacy_debts 
        SET paid_amount = :paid, status = :status, updated_at = NOW()
        WHERE id = :id
    """), {"paid": total_paid, "status": status, "id": debt_id})
