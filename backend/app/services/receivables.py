"""Single source of truth for money received from customers.

The ERP grew three independent, non-communicating definitions of "revenue":

  1. `sales_orders.payment_status` -- a flag, read by financial.py
  2. `invoices.paid_amount`        -- a stored total, written by payment_tracking
  3. `payments` rows              -- actual cash, read by profits.py

No code path kept them in agreement, so the dashboards disagreed by design: an
order marked paid via sales.py's mark-paid endpoint created no Payment row and
touched no Invoice, so profits.py reported zero revenue on it while
financial.py counted it in full.

This module makes `payments` authoritative -- cash actually received is the
only defensible basis for revenue, and it is the only one of the three that is
an immutable record of an event rather than a mutable summary. Everything else
becomes a derived cache, recomputed from the payment rows and never assigned
independently.

The accounting module depends on this. A general ledger posted from three
disagreeing sources produces a trial balance that cannot be reconciled to
anything.

Money is Decimal throughout. Several call sites used float, where
0.1 + 0.2 != 0.3 caused a final settling payment to be rejected as an
overpayment, or left a fully-paid invoice stuck at 'partial' forever.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Currency tolerance. Amounts are stored as NUMERIC(18,2), so anything below
# half a minor unit is rounding noise rather than a real balance.
CENT = Decimal("0.01")


def money(value) -> Decimal:
    """Coerce to a 2dp Decimal, the precision the money columns store."""
    if value is None:
        return Decimal("0.00")
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


async def recompute_invoice_paid(
    session: AsyncSession, invoice_id: UUID) -> tuple[Decimal, Decimal, str]:
    """Recompute an invoice's paid amount FROM ITS PAYMENT ROWS.

    Returns (total_paid, balance, status). This is the only place
    `invoices.paid_amount` is ever written -- it is a cache of SUM(payments),
    never an independently assigned figure. Previously it was assigned from a
    locally computed running total, so a concurrent payment could leave it
    disagreeing with the payment rows permanently.

    Assumes the caller already holds a lock on the invoice row.
    """
    row = (await session.execute(
        text("""
            SELECT i.total_amount,
                   COALESCE((SELECT SUM(p.amount) FROM payments p
                              WHERE p.invoice_id = i.id), 0) AS paid
              FROM invoices i
             WHERE i.id = :iid
        """),
        {"iid": str(invoice_id)},
    )).first()

    if row is None:
        raise HTTPException(status_code=404, detail="Invoice not found")

    total = money(row.total_amount)
    paid = money(row.paid)
    balance = money(total - paid)

    if paid <= 0:
        status = 'pending'
    elif balance <= CENT:
        status = 'paid'
    else:
        status = 'partial'

    await session.execute(
        text("UPDATE invoices SET paid_amount = :paid, status = :st WHERE id = :iid"),
        {"paid": str(paid), "st": status, "iid": str(invoice_id)},
    )

    # Keep the order flag in step. It stays denormalised because the sales UI
    # reads it heavily, but it is now strictly derived -- never assigned on
    # its own the way sales.py's mark-paid endpoint used to.
    order_status = {'paid': 'paid', 'partial': 'partial', 'pending': 'unpaid'}[status]
    # is_paid is passed as its own parameter rather than re-using :ps inside
    # the CASE: asyncpg deduces a parameter's type from its usage, and a
    # single placeholder appearing both as a varchar assignment target and in
    # an equality comparison raises AmbiguousParameterError.
    await session.execute(
        text("""
            UPDATE sales_orders so
               SET payment_status = :ps,
                   payment_date = CASE WHEN :is_paid
                                       THEN COALESCE(so.payment_date, NOW())
                                       ELSE NULL END
              FROM invoices i
             WHERE i.id = :iid AND so.id = i.sales_order_id
        """),
        {"ps": order_status, "is_paid": status == 'paid',
         "iid": str(invoice_id)},
    )

    return paid, balance, status


async def _lock_invoice(session: AsyncSession, invoice_id: UUID):
    row = (await session.execute(
        text("SELECT id, total_amount FROM invoices WHERE id = :iid FOR UPDATE"),
        {"iid": str(invoice_id)},
    )).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return row


async def record_payment(
    session: AsyncSession,
    *,
    invoice_id: UUID,
    amount,
    payment_method: str,
    reference: Optional[str] = None,
    notes: Optional[str] = None,
    payment_date=None,
    allow_overpayment: bool = False,
) -> dict:
    """Record a customer payment against an invoice.

    Locks the invoice first, so the overpayment guard is actually enforceable:
    previously two operators recording the final payment simultaneously both
    read paid=0, both passed the check, and both inserted -- leaving twice the
    invoice total in `payments` while `paid_amount` showed the correct figure.
    profits.py reads the payment rows, so revenue doubled on that sale.

    Does not commit; the caller owns the transaction.
    """
    amt = money(amount)
    if amt <= 0:
        raise HTTPException(
            status_code=400, detail="Payment amount must be greater than zero.")

    inv = await _lock_invoice(session, invoice_id)

    current_paid = money((await session.execute(
        text("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE invoice_id = :iid"),
        {"iid": str(invoice_id)},
    )).scalar())

    total = money(inv.total_amount)
    remaining = money(total - current_paid)

    if not allow_overpayment and amt - remaining > CENT:
        raise HTTPException(
            status_code=400,
            detail=(f"Payment of {amt:,.2f} exceeds the remaining balance "
                    f"of {remaining:,.2f}."),
        )

    payment_id = uuid4()
    await session.execute(
        text("""
            INSERT INTO payments
                (id, invoice_id, payment_method, amount, payment_date,
                 reference, notes, created_at)
            VALUES (:id, :iid, :method, :amt,
                    COALESCE(:pdate, NOW()), :ref, :notes, NOW())
        """),
        {
            "id": str(payment_id), "iid": str(invoice_id),
            "method": payment_method, "amt": str(amt),
            "pdate": payment_date, "ref": reference, "notes": notes,
        },
    )

    paid, balance, status = await recompute_invoice_paid(session, invoice_id)

    # Post the cash receipt to the ledger in the same transaction. Imported
    # here rather than at module scope because posting imports ledger, which
    # would otherwise create a cycle back through receivables.
    from app.services.posting import post_customer_payment
    await post_customer_payment(session, payment_id=payment_id)

    # Distribute the receipt to each product's designated account (MAPD).
    #
    # This is the ONLY automatic entry point, and it is here rather than in the
    # API handlers because every route that takes customer money -- mark-paid,
    # payment tracking, the public order portal -- funnels through this
    # function. Hooking the handlers instead would guarantee that one of them
    # eventually got missed.
    #
    # It reports rather than raises: the payment is a fact about money that has
    # already changed hands, and a suspended destination account is not a
    # reason to refuse to record it. Failed distributions are queued and
    # retried; see app.services.settlement.
    from app.services.settlement import distribute_payment_safely
    settlement = await distribute_payment_safely(
        session, payment_id=payment_id)

    return {
        "payment_id": payment_id,
        "amount": amt,
        "total_paid": paid,
        "balance": balance,
        "status": status,
        "settlement": settlement,
    }


async def delete_payment(session: AsyncSession, *, payment_id: UUID) -> dict:
    """Remove a payment and recompute the invoice from what remains."""
    row = (await session.execute(
        text("SELECT invoice_id FROM payments WHERE id = :pid"),
        {"pid": str(payment_id)},
    )).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Payment not found")

    invoice_id = row.invoice_id
    await _lock_invoice(session, invoice_id)

    # A distributed payment cannot simply vanish: its money has already been
    # allocated to product accounts and posted to the ledger. Deleting the row
    # would leave settlement_details pointing at a payment that never happened
    # -- which the foreign key would reject anyway, with a message nobody can
    # act on. Say what to do instead.
    settled = (await session.execute(
        text("""SELECT settlement_reference FROM settlements
                 WHERE payment_id = :pid
                   AND status IN ('PENDING','COMPLETED')
                 LIMIT 1"""),
        {"pid": str(payment_id)},
    )).first() if (await session.execute(
        text("SELECT to_regclass('public.settlements') IS NOT NULL")
    )).scalar() else None
    if settled is not None:
        raise HTTPException(
            status_code=400,
            detail=(f"This payment has been distributed (settlement "
                    f"{settled.settlement_reference}). Reverse the settlement "
                    f"first so the allocation is unwound on the record, then "
                    f"remove the payment."))

    await session.execute(
        text("DELETE FROM payments WHERE id = :pid"), {"pid": str(payment_id)})

    paid, balance, status = await recompute_invoice_paid(session, invoice_id)
    return {"invoice_id": invoice_id, "total_paid": paid,
            "balance": balance, "status": status}


async def ensure_invoice_for_order(
    session: AsyncSession, *, order_id: UUID, created_by: Optional[UUID] = None
) -> UUID:
    """Return the invoice for a sales order, creating it if absent.

    Revenue cannot be recognised without an invoice to hang it on. Orders used
    to deduct stock at creation while invoicing stayed a separate manual step,
    so any order never pushed through that step consumed inventory and
    produced no revenue record anywhere.
    """
    existing = (await session.execute(
        text("SELECT id FROM invoices WHERE sales_order_id = :oid LIMIT 1"),
        {"oid": str(order_id)},
    )).first()
    if existing:
        return existing.id

    order = (await session.execute(
        text("""SELECT id, order_number, customer_id, total_amount
                  FROM sales_orders WHERE id = :oid"""),
        {"oid": str(order_id)},
    )).first()
    if order is None:
        raise HTTPException(status_code=404, detail="Sales order not found")

    invoice_id = uuid4()
    invoice_number = f"INV-{order.order_number}"

    await session.execute(
        text("""
            INSERT INTO invoices
                (id, invoice_number, customer_id, sales_order_id,
                 invoice_date, due_date, total_amount, paid_amount,
                 status, notes, created_at)
            VALUES (:id, :num, :cid, :oid, NOW(), NOW() + INTERVAL '30 days',
                    :total, 0, 'pending',
                    'Auto-generated on payment recognition', NOW())
        """),
        {
            "id": str(invoice_id), "num": invoice_number,
            "cid": str(order.customer_id), "oid": str(order_id),
            "total": str(money(order.total_amount)),
        },
    )

    # Mirror the order lines so the invoice stands on its own as a document,
    # carrying the cost snapshot taken at the time of sale rather than
    # re-deriving it now (which would cost the goods at today's prices).
    await session.execute(
        text("""
            INSERT INTO invoice_lines
                (id, invoice_id, product_id, quantity, unit_price, line_total,
                 unit_cost, cost_total, cost_source)
            SELECT gen_random_uuid(), :iid, sol.product_id, sol.quantity,
                   sol.unit_price, sol.line_total,
                   sol.unit_cost, sol.cost_total, sol.cost_source
              FROM sales_order_lines sol
             WHERE sol.sales_order_id = :oid
        """),
        {"iid": str(invoice_id), "oid": str(order_id)},
    )

    return invoice_id


async def revenue_between(
    session: AsyncSession, *, start=None, end=None) -> Decimal:
    """Cash collected in a period -- THE definition of revenue for this app.

    financial.py used to sum `sales_orders.total_amount WHERE payment_status =
    'paid'`, which counted an order with 99% collected as zero revenue and the
    full amount as outstanding. Summing actual payments avoids both errors.
    """
    clauses, params = [], {}
    if start is not None:
        clauses.append("p.payment_date >= :start")
        params["start"] = start
    if end is not None:
        clauses.append("p.payment_date < :end")
        params["end"] = end
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    return money((await session.execute(
        text(f"SELECT COALESCE(SUM(p.amount), 0) FROM payments p {where}"),
        params,
    )).scalar())


async def outstanding_receivables(session: AsyncSession) -> Decimal:
    """Invoiced but not yet collected -- computed per invoice, not per flag."""
    return money((await session.execute(
        text("""
            SELECT COALESCE(SUM(GREATEST(i.total_amount - COALESCE((
                       SELECT SUM(p.amount) FROM payments p
                        WHERE p.invoice_id = i.id), 0), 0)), 0)
              FROM invoices i
             WHERE i.status <> 'cancelled'
        """)
    )).scalar())


async def reconciliation_report(session: AsyncSession) -> dict:
    """Find rows where the derived caches disagree with the payment rows.

    Anything listed here is a historical inconsistency created before these
    values became derived. Surfacing them is the point: the accounting module
    must not post journals from data that does not reconcile.
    """
    drifted = (await session.execute(
        text("""
            SELECT i.id, i.invoice_number, i.total_amount, i.paid_amount,
                   COALESCE((SELECT SUM(p.amount) FROM payments p
                              WHERE p.invoice_id = i.id), 0) AS actual_paid,
                   i.status
              FROM invoices i
             WHERE ABS(i.paid_amount - COALESCE((
                       SELECT SUM(p.amount) FROM payments p
                        WHERE p.invoice_id = i.id), 0)) > 0.01
             ORDER BY ABS(i.paid_amount - COALESCE((
                       SELECT SUM(p.amount) FROM payments p
                        WHERE p.invoice_id = i.id), 0)) DESC
        """)
    )).fetchall()

    # Orders flagged paid with no invoice at all -- the sales.py mark-paid
    # legacy path. These carry revenue that profits.py has never seen.
    orphans = (await session.execute(
        text("""
            SELECT so.id, so.order_number, so.total_amount, so.payment_status
              FROM sales_orders so
              LEFT JOIN invoices i ON i.sales_order_id = so.id
             WHERE i.id IS NULL
               AND so.payment_status IN ('paid', 'partial')
             ORDER BY so.total_amount DESC
        """)
    )).fetchall()

    return {
        "invoices_with_drifted_cache": [
            {
                "invoice_id": str(r.id),
                "invoice_number": r.invoice_number,
                "total_amount": float(r.total_amount or 0),
                "cached_paid_amount": float(r.paid_amount or 0),
                "actual_payments_sum": float(r.actual_paid or 0),
                "difference": float(money(r.paid_amount) - money(r.actual_paid)),
                "status": r.status,
            } for r in drifted
        ],
        "orders_flagged_paid_without_invoice": [
            {
                "order_id": str(r.id),
                "order_number": r.order_number,
                "total_amount": float(r.total_amount or 0),
                "payment_status": r.payment_status,
            } for r in orphans
        ],
        "drifted_count": len(drifted),
        "orphan_count": len(orphans),
        "unrecognised_revenue": float(sum(money(r.total_amount) for r in orphans)),
    }
