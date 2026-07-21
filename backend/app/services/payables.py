"""Accounts Payable: what the business owes suppliers.

The mirror of app.services.receivables, and it exists for the same reason.
`purchase_orders.paid_amount` was a mutable running total with no payment
rows behind it: the endpoint read it, added, and wrote back, in floats,
with no lock. That gives three failure modes, all of which were present:

  * two concurrent payments both read the same total, both pass the
    "exceeds balance" guard, and one silently overwrites the other;
  * float drift makes a final settling payment either rejected as an
    overpayment or leaves the PO stuck at 'partial' forever;
  * there is no record of the individual payments, so a supplier statement
    cannot be produced or disputed.

Supplier payments are now events. `paid_amount` is a cache recomputed from
them and never assigned independently.

Receiving goods is separated from paying for them, because they are different
events with different accounting consequences:

    goods received  ->  Dr Inventory        / Cr Accounts Payable
    supplier paid   ->  Dr Accounts Payable / Cr Bank

Booking both at payment time (as the old code effectively did) means stock
does not exist until the invoice is settled.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

CENT = Decimal("0.01")


def money(value) -> Decimal:
    if value is None:
        return Decimal("0.00")
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Suppliers
# ---------------------------------------------------------------------------

async def get_or_create_supplier(
    session: AsyncSession, *, name: str, **fields
) -> UUID:
    """Resolve a supplier by name, creating one if needed.

    Matching is case- and whitespace-insensitive so "Acme Ltd", "acme ltd "
    and "ACME LTD" resolve to one supplier rather than three sets of books.
    """
    clean = (name or "").strip()
    if not clean:
        raise HTTPException(
            status_code=400, detail="Supplier name is required.")

    row = (await session.execute(
        text("SELECT id FROM suppliers WHERE LOWER(name) = LOWER(:n)"),
        {"n": clean},
    )).first()
    if row:
        return row.id

    supplier_id = uuid4()
    code = f"SUP-{uuid4().hex[:8].upper()}"
    await session.execute(
        text("""
            INSERT INTO suppliers
                (id, supplier_code, name, contact_person, phone, email,
                 address, payment_terms_days, credit_limit)
            VALUES (:id, :code, :name, :contact, :phone, :email, :address,
                    :terms, :limit)
        """),
        {
            "id": str(supplier_id), "code": code, "name": clean,
            "contact": fields.get("contact_person"),
            "phone": fields.get("phone"),
            "email": fields.get("email"),
            "address": fields.get("address"),
            "terms": int(fields.get("payment_terms_days") or 30),
            "limit": str(money(fields.get("credit_limit") or 0)),
        },
    )
    return supplier_id


async def supplier_balance(session: AsyncSession, *, supplier_id: UUID) -> Decimal:
    """What is still owed to this supplier, computed from the documents."""
    row = (await session.execute(
        text("""
            SELECT COALESCE((
                     SELECT SUM(po.total_amount) FROM purchase_orders po
                      WHERE po.supplier_id = :s
                        AND po.status NOT IN ('cancelled', 'draft')), 0)
                 - COALESCE((
                     SELECT SUM(sp.amount) FROM supplier_payments sp
                      WHERE sp.supplier_id = :s), 0) AS balance
        """),
        {"s": str(supplier_id)},
    )).first()
    return money(row.balance)


# ---------------------------------------------------------------------------
# Payment recording
# ---------------------------------------------------------------------------

async def recompute_po_paid(
    session: AsyncSession, po_id: UUID) -> tuple[Decimal, Decimal, str]:
    """Recompute a PO's paid amount FROM ITS PAYMENT ROWS.

    The only place purchase_orders.paid_amount is written.
    """
    row = (await session.execute(
        text("""
            SELECT po.total_amount,
                   COALESCE((SELECT SUM(sp.amount) FROM supplier_payments sp
                              WHERE sp.po_id = po.id), 0) AS paid
              FROM purchase_orders po WHERE po.id = :p
        """),
        {"p": str(po_id)},
    )).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    total = money(row.total_amount)
    paid = money(row.paid)
    balance = money(total - paid)
    status = 'unpaid' if paid <= 0 else ('paid' if balance <= CENT else 'partial')

    await session.execute(
        text("""UPDATE purchase_orders
                   SET paid_amount = :paid, payment_status = :st,
                       updated_at = NOW()
                 WHERE id = :p"""),
        {"paid": str(paid), "st": status, "p": str(po_id)},
    )
    return paid, balance, status


async def pay_supplier(
    session: AsyncSession,
    *,
    po_id: UUID,
    amount,
    payment_method: str,
    payment_reference: Optional[str] = None,
    payment_date: Optional[date] = None,
    notes: Optional[str] = None,
    created_by: Optional[UUID] = None,
) -> dict:
    """Record a payment to a supplier against a purchase order.

    Locks the PO first, so the overpayment guard is actually enforceable
    rather than a read of stale data.
    """
    amt = money(amount)
    if amt <= 0:
        raise HTTPException(
            status_code=400, detail="Payment amount must be greater than zero.")

    po = (await session.execute(
        text("""SELECT id, po_number, supplier_id, total_amount, status
                  FROM purchase_orders WHERE id = :p FOR UPDATE"""),
        {"p": str(po_id)},
    )).first()
    if po is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    if po.status == 'cancelled':
        raise HTTPException(
            status_code=400, detail="Cannot pay a cancelled purchase order.")

    current_paid = money((await session.execute(
        text("SELECT COALESCE(SUM(amount), 0) FROM supplier_payments "
             "WHERE po_id = :p"),
        {"p": str(po_id)},
    )).scalar())

    total = money(po.total_amount)
    remaining = money(total - current_paid)
    if amt - remaining > CENT:
        raise HTTPException(
            status_code=400,
            detail=(f"Payment of {amt:,.2f} exceeds the outstanding balance "
                    f"of {remaining:,.2f} on {po.po_number}."),
        )

    payment_id = uuid4()
    payment_number = f"SP-{uuid4().hex[:10].upper()}"
    await session.execute(
        text("""
            INSERT INTO supplier_payments
                (id, payment_number, supplier_id, po_id, amount,
                 payment_method, payment_reference, payment_date, notes,
                 created_by)
            VALUES (:id, :num, :sup, :po, :amt, :method, :ref,
                    COALESCE(:pdate, CURRENT_DATE), :notes, :by)
        """),
        {
            "id": str(payment_id), "num": payment_number,
            "sup": str(po.supplier_id) if po.supplier_id else None,
            "po": str(po_id), "amt": str(amt),
            "method": payment_method, "ref": payment_reference,
            "pdate": payment_date, "notes": notes,
            "by": str(created_by) if created_by else None,
        },
    )

    paid, balance, status = await recompute_po_paid(session, po_id)
    return {
        "payment_id": payment_id,
        "payment_number": payment_number,
        "po_number": po.po_number,
        "amount": amt,
        "total_paid": paid,
        "balance": balance,
        "payment_status": status,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

async def outstanding_payables(session: AsyncSession) -> Decimal:
    """Total still owed across all suppliers."""
    return money((await session.execute(
        text("""
            SELECT COALESCE(SUM(GREATEST(po.total_amount - COALESCE((
                       SELECT SUM(sp.amount) FROM supplier_payments sp
                        WHERE sp.po_id = po.id), 0), 0)), 0)
              FROM purchase_orders po
             WHERE po.status NOT IN ('cancelled', 'draft')
        """)
    )).scalar())


async def supplier_aging(
    session: AsyncSession, *, as_at: Optional[date] = None) -> dict:
    """Outstanding payables bucketed by how overdue they are.

    Buckets run from the DUE date (order date + the supplier's payment terms),
    not the order date -- an invoice on 60-day terms is not overdue at 45
    days, and treating it as such would misstate the position.
    """
    rows = (await session.execute(
        text("""
            WITH po_balance AS (
                SELECT po.id, po.po_number, po.supplier_id,
                       COALESCE(s.name, po.vendor_name) AS supplier_name,
                       COALESCE(s.payment_terms_days, 30) AS terms,
                       po.order_date::date AS order_date,
                       po.total_amount,
                       po.total_amount - COALESCE((
                           SELECT SUM(sp.amount) FROM supplier_payments sp
                            WHERE sp.po_id = po.id), 0) AS balance
                  FROM purchase_orders po
                  LEFT JOIN suppliers s ON s.id = po.supplier_id
                 WHERE po.status NOT IN ('cancelled', 'draft')
            )
            SELECT supplier_id, supplier_name, po_number, total_amount,
                   balance,
                   (COALESCE(:as_at, CURRENT_DATE)
                    - (order_date + terms * INTERVAL '1 day')::date) AS days_overdue
              FROM po_balance
             WHERE balance > 0.01
             ORDER BY days_overdue DESC
        """),
        {"as_at": as_at},
    )).fetchall()

    buckets = {"current": Decimal("0"), "1_30": Decimal("0"),
               "31_60": Decimal("0"), "61_90": Decimal("0"),
               "over_90": Decimal("0")}
    detail = []
    for r in rows:
        bal = money(r.balance)
        overdue = int(r.days_overdue or 0)
        if overdue <= 0:
            bucket = "current"
        elif overdue <= 30:
            bucket = "1_30"
        elif overdue <= 60:
            bucket = "31_60"
        elif overdue <= 90:
            bucket = "61_90"
        else:
            bucket = "over_90"
        buckets[bucket] += bal
        detail.append({
            "supplier_id": str(r.supplier_id) if r.supplier_id else None,
            "supplier_name": r.supplier_name,
            "po_number": r.po_number,
            "total_amount": float(money(r.total_amount)),
            "balance": float(bal),
            "days_overdue": max(overdue, 0),
            "bucket": bucket,
        })

    return {
        "as_at": str(as_at) if as_at else None,
        "buckets": {k: float(v) for k, v in buckets.items()},
        "total_outstanding": float(sum(buckets.values())),
        "items": detail,
    }


async def supplier_statement(
    session: AsyncSession, *, supplier_id: UUID,
    start: Optional[date] = None, end: Optional[date] = None,
) -> dict:
    """Every charge and payment for one supplier, with a running balance."""
    supplier = (await session.execute(
        text("SELECT id, supplier_code, name, payment_terms_days, credit_limit "
             "FROM suppliers WHERE id = :s"),
        {"s": str(supplier_id)},
    )).first()
    if supplier is None:
        raise HTTPException(status_code=404, detail="Supplier not found")

    rows = (await session.execute(
        text("""
            SELECT * FROM (
                SELECT po.order_date::date AS entry_date, 'PURCHASE' AS kind,
                       po.po_number AS reference, po.total_amount AS charge,
                       0::numeric AS payment
                  FROM purchase_orders po
                 WHERE po.supplier_id = :s
                   AND po.status NOT IN ('cancelled', 'draft')
                UNION ALL
                SELECT sp.payment_date, 'PAYMENT',
                       sp.payment_number, 0::numeric, sp.amount
                  FROM supplier_payments sp
                 WHERE sp.supplier_id = :s
            ) t
             WHERE (CAST(:start AS date) IS NULL OR t.entry_date >= :start)
               AND (CAST(:end   AS date) IS NULL OR t.entry_date <= :end)
             ORDER BY t.entry_date, t.kind DESC
        """),
        {"s": str(supplier_id), "start": start, "end": end},
    )).fetchall()

    running = Decimal("0.00")
    lines = []
    for r in rows:
        charge, payment = money(r.charge), money(r.payment)
        running += charge - payment
        lines.append({
            "date": str(r.entry_date), "type": r.kind,
            "reference": r.reference,
            "charge": float(charge), "payment": float(payment),
            "balance": float(running),
        })

    return {
        "supplier": {"id": str(supplier.id), "code": supplier.supplier_code,
                     "name": supplier.name,
                     "payment_terms_days": supplier.payment_terms_days,
                     "credit_limit": float(money(supplier.credit_limit))},
        "lines": lines,
        "closing_balance": float(running),
    }
