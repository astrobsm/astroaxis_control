"""What a customer actually owes, and the statement that goes on their invoice.

WHY THIS EXISTS
---------------
"Outstanding debt" was being computed in the sales UI as:

    SUM(sales_orders.total_amount) WHERE payment_status IN ('unpaid','partial')

That is wrong in two directions at once, and it is the same error
`app.services.receivables` was written to eliminate:

  * a customer who has paid ₦99,000 of a ₦100,000 order is shown as owing the
    FULL ₦100,000, not the ₦1,000 that is actually outstanding;
  * `payment_status` is a denormalised flag. Payments are the record of an
    event; a flag is a summary somebody has to remember to update.

It also missed legacy debts entirely -- money carried over from before the ERP,
tracked in its own table -- so a customer could be handed an invoice saying
they owed nothing while `legacy_debts` said otherwise.

This module computes the balance from the PAYMENTS, per document, and unions
the two sources of debt the business actually has. It is the only place that
answers the question, so the sales screen, the API and the printed invoice
cannot disagree.

THE INVOICE TOTAL IS NOT TOUCHED
--------------------------------
Prior debt is presented as a STATEMENT section alongside the invoice, with a
combined "total amount payable". It is deliberately NOT added into
`invoices.total_amount` / `sales_orders.total_amount`, because those figures
drive revenue recognition, the general ledger and MAPD's allocation of the
payment across product accounts. Folding last month's debt into this month's
invoice total would:

  * recognise the same revenue twice, once per invoice it appears on;
  * unbalance the ledger against the goods actually shipped;
  * make the settlement engine allocate money to product accounts for goods
    that are not on the invoice.

The customer sees one number to pay. The books see two separate obligations.
That is the same thing every statement-of-account does.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ledger import money

CENT = Decimal("0.01")
ZERO = Decimal("0.00")

# Aging buckets, in days. Standard receivables presentation: anything a
# customer disputes is usually about age, so the statement has to show it.
AGING_BUCKETS = (
    ("current", 0, 0),
    ("days_1_30", 1, 30),
    ("days_31_60", 31, 60),
    ("days_61_90", 61, 90),
    ("days_over_90", 91, None),
)


async def _legacy_table_exists(session: AsyncSession) -> bool:
    """Is the legacy-debt table present?

    `legacy_debts` is created lazily by its own API module rather than by a
    migration, so on a database where that screen has never been opened the
    table does not exist. Probing beats catching UndefinedTable: a swallowed
    exception here would silently under-report what a customer owes.
    """
    return bool((await session.execute(
        text("SELECT to_regclass('public.legacy_debts') IS NOT NULL"))).scalar())


def _age_days(on, as_of: date) -> Optional[int]:
    if on is None:
        return None
    d = on.date() if isinstance(on, datetime) else on
    return max(0, (as_of - d).days)


def _bucket_for(age: Optional[int]) -> str:
    if age is None:
        return "current"
    for name, lo, hi in AGING_BUCKETS:
        if age >= lo and (hi is None or age <= hi):
            return name
    return "days_over_90"


async def outstanding_for_customer(
    session: AsyncSession,
    *,
    customer_id: UUID,
    exclude_order_id: Optional[UUID] = None,
    as_of: Optional[date] = None,
) -> dict:
    """Every unsettled document for one customer, with its real balance.

    `exclude_order_id` leaves one order out. That is what makes this safe to
    embed in an invoice: the order being invoiced must not appear in its own
    "previous balance" section, or the customer is asked for it twice.

    Returns Decimals for money so the caller can add them without float drift.
    """
    as_of = as_of or datetime.now(timezone.utc).date()

    customer = (await session.execute(
        text("""SELECT id, name, customer_code, phone, email, address,
                       credit_limit
                  FROM customers WHERE id = :cid"""),
        {"cid": str(customer_id)},
    )).first()
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")

    # ---- sales orders --------------------------------------------------
    #
    # Balance comes from the PAYMENTS, not from payment_status. An order is
    # joined to its invoice only to find the payments; an order with no invoice
    # yet is still a debt -- invoices are created lazily in this system
    # (see receivables.ensure_invoice_for_order), so most unpaid orders have no
    # invoice row at all and a purely invoice-based query would miss them.
    orders = (await session.execute(
        text("""
            SELECT so.id, so.order_number, so.order_date, so.required_date,
                   so.total_amount, so.payment_status, so.notes,
                   COALESCE(paid.amount, 0) AS paid,
                   -- Scalar subquery, NOT a join. `invoices.sales_order_id`
                   -- carries no unique constraint, so an order that somehow
                   -- has two live invoices would be returned twice by a join
                   -- and its balance counted twice in the total. A subquery
                   -- returns one row per order whatever the data looks like.
                   (SELECT i.invoice_number FROM invoices i
                     WHERE i.sales_order_id = so.id
                       AND i.status <> 'cancelled'
                     ORDER BY i.invoice_number
                     LIMIT 1) AS invoice_number
              FROM sales_orders so
              LEFT JOIN (
                    SELECT i2.sales_order_id, SUM(p.amount) AS amount
                      FROM invoices i2
                      JOIN payments p ON p.invoice_id = i2.id
                     WHERE i2.status <> 'cancelled'
                     GROUP BY i2.sales_order_id
              ) paid ON paid.sales_order_id = so.id
             WHERE so.customer_id = :cid
               AND so.status <> 'cancelled'
               AND (CAST(:exclude AS uuid) IS NULL OR so.id <> CAST(:exclude AS uuid))
               AND so.total_amount - COALESCE(paid.amount, 0) > :cent
             ORDER BY so.order_date
        """),
        {"cid": str(customer_id),
         "exclude": str(exclude_order_id) if exclude_order_id else None,
         "cent": str(CENT)},
    )).fetchall()

    order_ids = [str(r.id) for r in orders]
    lines_by_order: dict[str, list] = {}
    if order_ids:
        # An expanding bindparam rather than ANY(CAST(:ids AS uuid[])): asyncpg
        # requires a real sequence for an array parameter and rejects a
        # Postgres array literal string, so the cast form fails at runtime.
        # Expanding renders one placeholder per id and works on every driver.
        line_rows = (await session.execute(
            text("""
                SELECT sol.sales_order_id, sol.quantity, sol.unit_price,
                       sol.line_total, sol.unit,
                       COALESCE(p.name, 'Unknown product') AS product_name
                  FROM sales_order_lines sol
                  LEFT JOIN products p ON p.id = sol.product_id
                 WHERE sol.sales_order_id IN :ids
                 ORDER BY sol.sales_order_id, p.name
            """).bindparams(bindparam("ids", expanding=True)),
            {"ids": order_ids},
        )).fetchall()
        for r in line_rows:
            lines_by_order.setdefault(str(r.sales_order_id), []).append({
                "product_name": r.product_name,
                "quantity": float(r.quantity or 0),
                "unit": r.unit,
                "unit_price": float(money(r.unit_price)),
                "line_total": float(money(r.line_total)),
            })

    items: list[dict] = []
    orders_outstanding = ZERO

    for r in orders:
        total = money(r.total_amount)
        paid = money(r.paid)
        balance = money(total - paid)
        age = _age_days(r.order_date, as_of)
        orders_outstanding += balance
        items.append({
            "kind": "ORDER",
            "id": str(r.id),
            "reference": r.order_number,
            "invoice_number": r.invoice_number,
            "date": r.order_date.isoformat() if r.order_date else None,
            "due_date": r.required_date.isoformat() if r.required_date else None,
            "description": (r.notes or "").strip() or None,
            "original_amount": float(total),
            "paid_amount": float(paid),
            "balance": float(balance),
            # Derived from the payments, not read from payment_status: the flag
            # is a cache and this is the figure the customer will dispute.
            "status": "partial" if paid > ZERO else "unpaid",
            "age_days": age,
            "aging_bucket": _bucket_for(age),
            "lines": lines_by_order.get(str(r.id), []),
            "_balance": balance,
        })

    # ---- legacy debts --------------------------------------------------
    legacy_outstanding = ZERO
    if await _legacy_table_exists(session):
        legacy = (await session.execute(
            text("""
                SELECT id, debt_number, description, original_amount,
                       paid_amount, debt_date, due_date, status, notes
                  FROM legacy_debts
                 WHERE customer_id = :cid
                   AND status <> 'cancelled'
                   AND original_amount - paid_amount > :cent
                 ORDER BY debt_date
            """),
            {"cid": str(customer_id), "cent": str(CENT)},
        )).fetchall()
        for r in legacy:
            total = money(r.original_amount)
            paid = money(r.paid_amount)
            balance = money(total - paid)
            age = _age_days(r.debt_date, as_of)
            legacy_outstanding += balance
            items.append({
                "kind": "LEGACY",
                "id": str(r.id),
                "reference": r.debt_number,
                "invoice_number": None,
                "date": r.debt_date.isoformat() if r.debt_date else None,
                "due_date": r.due_date.isoformat() if r.due_date else None,
                "description": r.description,
                "original_amount": float(total),
                "paid_amount": float(paid),
                "balance": float(balance),
                "status": "partial" if paid > ZERO else "unpaid",
                "age_days": age,
                "aging_bucket": _bucket_for(age),
                "lines": [],
                "_balance": balance,
            })

    # Oldest first: a statement is read to find out what has been outstanding
    # longest, and that is also the order money should be applied in.
    items.sort(key=lambda it: (it["date"] or "", it["reference"]))

    aging = {name: ZERO for name, _, _ in AGING_BUCKETS}
    for it in items:
        aging[it["aging_bucket"]] += it["_balance"]

    total_outstanding = money(orders_outstanding + legacy_outstanding)
    ages = [it["age_days"] for it in items if it["age_days"] is not None]

    for it in items:
        del it["_balance"]

    credit_limit = money(customer.credit_limit)

    return {
        "customer_id": str(customer_id),
        "customer_name": customer.name,
        "customer_code": customer.customer_code,
        "customer_phone": customer.phone,
        "customer_email": customer.email,
        "customer_address": customer.address,
        "items": items,
        "order_count": len([i for i in items if i["kind"] == "ORDER"]),
        "legacy_count": len([i for i in items if i["kind"] == "LEGACY"]),
        "count": len(items),
        "orders_outstanding": orders_outstanding,
        "legacy_outstanding": legacy_outstanding,
        "total_outstanding": total_outstanding,
        "aging": {k: money(v) for k, v in aging.items()},
        "oldest_days": max(ages) if ages else None,
        "credit_limit": credit_limit,
        # Only meaningful when a limit is actually set; 0 is this schema's
        # default and means "no limit recorded", not "no credit allowed".
        "credit_limit_exceeded": bool(
            credit_limit > ZERO and total_outstanding > credit_limit),
        "as_of": as_of.isoformat(),
    }


async def invoice_statement(
    session: AsyncSession, *, order_id: UUID, as_of: Optional[date] = None
) -> dict:
    """The debt statement to print on one order's invoice.

    Combines this invoice with everything the customer already owed, EXCLUDING
    this order, and gives the single figure they should pay.

    `this_invoice` is what the order itself still needs -- the order total less
    anything already received against it. On an unpaid order that is the full
    total; on a part-paid one it is the remainder, which is what a customer
    handed a re-printed invoice needs to see.
    """
    as_of = as_of or datetime.now(timezone.utc).date()

    order = (await session.execute(
        text("""
            SELECT so.id, so.order_number, so.customer_id, so.total_amount,
                   so.order_date, so.required_date,
                   COALESCE((SELECT SUM(p.amount)
                               FROM invoices i
                               JOIN payments p ON p.invoice_id = i.id
                              WHERE i.sales_order_id = so.id
                                AND i.status <> 'cancelled'), 0) AS paid
              FROM sales_orders so
             WHERE so.id = :oid
        """),
        {"oid": str(order_id)},
    )).first()
    if order is None:
        raise HTTPException(status_code=404, detail="Sales order not found")

    prior = await outstanding_for_customer(
        session, customer_id=order.customer_id,
        exclude_order_id=order_id, as_of=as_of)

    invoice_total = money(order.total_amount)
    invoice_paid = money(order.paid)
    invoice_due = money(max(ZERO, invoice_total - invoice_paid))
    previous = prior["total_outstanding"]

    return {
        "order_id": str(order_id),
        "order_number": order.order_number,
        "customer_name": prior["customer_name"],
        "invoice_total": invoice_total,
        "invoice_paid": invoice_paid,
        "invoice_due": invoice_due,
        "previous_outstanding": previous,
        # The one number the customer settles. Kept out of invoice_total on
        # purpose -- see the module docstring.
        "total_payable": money(invoice_due + previous),
        "previous_items": prior["items"],
        "previous_count": prior["count"],
        "aging": prior["aging"],
        "oldest_days": prior["oldest_days"],
        "credit_limit": prior["credit_limit"],
        "credit_limit_exceeded": bool(
            prior["credit_limit"] > ZERO
            and money(invoice_due + previous) > prior["credit_limit"]),
        "as_of": as_of.isoformat(),
    }


def to_floats(statement: dict) -> dict:
    """JSON-safe copy of a statement: Decimals become floats.

    Kept separate so the internal callers (the PDF builders) keep working in
    Decimal, and only the API boundary loses precision.
    """
    out = dict(statement)
    for key in ("invoice_total", "invoice_paid", "invoice_due",
                "previous_outstanding", "total_payable", "credit_limit",
                "orders_outstanding", "legacy_outstanding",
                "total_outstanding"):
        if key in out and isinstance(out[key], Decimal):
            out[key] = float(out[key])
    if isinstance(out.get("aging"), dict):
        out["aging"] = {k: float(v) for k, v in out["aging"].items()}
    return out
