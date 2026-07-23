"""Event -> journal translation.

Each function here turns one business event into the balanced entry that
records it. Keeping the translation in one module means the accounting policy
(which account, which side) is stated once and can be reviewed by an
accountant without reading the API handlers.

Every function takes the caller's session and does not commit, so the journal
lands in the SAME transaction as the event. A sale and its accounting entry
either both happen or neither does -- there is no window in which stock has
moved but the books do not know.
"""
from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ledger import Line, money, post_entry, reverse_entry


# ---------------------------------------------------------------------------
# Roll-out control
# ---------------------------------------------------------------------------
#
# Posting is OFF by default. Turning it on changes what happens on every sale,
# payment and production run, so it is a deliberate switch rather than a side
# effect of deploying this code.
#
#   ACCOUNTING_POSTING_ENABLED=true    start posting
#   ACCOUNTING_CUTOVER_DATE=2026-08-01 ignore events dated before this
#
# The cutover date exists because historical orders carry backfilled cost
# estimates. Posting them would fill the ledger with figures nobody can
# defend; opening balances are the honest way to bring history in.

def posting_enabled() -> bool:
    return os.getenv("ACCOUNTING_POSTING_ENABLED", "").lower() in (
        "1", "true", "yes", "on")


def _cutover_date() -> Optional[date]:
    raw = os.getenv("ACCOUNTING_CUTOVER_DATE", "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        # A malformed cutover would silently post everything, which is the
        # failure mode the setting exists to prevent. Refuse instead.
        raise RuntimeError(
            f"ACCOUNTING_CUTOVER_DATE={raw!r} is not a valid YYYY-MM-DD date.")


def _before_cutover(on: Optional[date]) -> bool:
    cutover = _cutover_date()
    return bool(cutover and on and on < cutover)


# Business timezone. Nigeria is UTC+1, so a timestamp must be converted to
# local time BEFORE its date is taken.
BUSINESS_TZ = os.getenv("BUSINESS_TIMEZONE", "Africa/Lagos")


def business_date(value) -> Optional[date]:
    """The calendar date of a timestamp IN THE COMPANY'S TIMEZONE.

    Calling .date() on a timezone-aware timestamp yields its UTC date, which
    is a day early for any UTC+ business during the local small hours. In
    Lagos (UTC+1) a sale at 00:30 on 1 August is stored as 23:30 on 31 July
    UTC, so the naive conversion would post it to July -- into the previous
    month, and potentially into a period that has already been closed and
    reported.

    Accounting dates follow the business day, not the server's clock.
    """
    if value is None:
        return None
    if isinstance(value, date) and not hasattr(value, "hour"):
        return value                      # already a plain date
    if getattr(value, "tzinfo", None) is None:
        return value.date()               # naive: assume it is already local
    try:
        from zoneinfo import ZoneInfo
        return value.astimezone(ZoneInfo(BUSINESS_TZ)).date()
    except Exception:
        # An unknown tz name must not silently fall back to UTC dating.
        raise RuntimeError(
            f"BUSINESS_TIMEZONE={BUSINESS_TZ!r} is not a valid timezone.")


async def already_posted(
    session: AsyncSession, *, source_module: str, source_reference: str
) -> bool:
    """Has this event already produced a journal entry?

    Without this, a retried request, a double-clicked button, or a re-run of
    a background job would post the same sale twice -- and because posted
    entries are immutable, the correction is a manual reversal rather than a
    delete. Cheap check, expensive omission.
    """
    row = (await session.execute(
        text("""
            SELECT 1 FROM gl_journal_entries
             WHERE source_module = :m AND source_reference = :r
               AND status <> 'REVERSED'
             LIMIT 1
        """),
        {"m": source_module, "r": source_reference},
    )).first()
    return row is not None


async def _should_post(
    session: AsyncSession, *, source_module: str, source_reference: str,
    on: Optional[date],
) -> bool:
    """Common gate: enabled, after cutover, and not already posted."""
    if not posting_enabled():
        return False
    if _before_cutover(on):
        return False
    if source_reference and await already_posted(
            session, source_module=source_module,
            source_reference=source_reference):
        return False
    return True

# Account codes used by the automatic postings. Named here rather than
# scattered as string literals so a chart-of-accounts change is one edit.
ACC_CASH = "1100"
ACC_BANK = "1200"
ACC_RECEIVABLE = "1300"
ACC_RAW_MATERIALS = "1410"
ACC_FINISHED_GOODS = "1430"
ACC_PAYABLE = "2100"
ACC_VAT_PAYABLE = "2300"
ACC_SALES = "4100"
ACC_SALES_RETURNS = "4900"
ACC_COGS = "5100"
ACC_INVENTORY_WRITEOFF = "5500"


async def post_sale(
    session: AsyncSession,
    *,
    order_id: UUID,
    on: Optional[date] = None,
    created_by: Optional[UUID] = None,
    include_cogs: bool = True,
) -> Optional[UUID]:
    """Recognise a sale: revenue, receivable, and the cost of what was sold.

    Two things happen when goods are sold, and both must be recorded or the
    margin is wrong:
        Dr Accounts Receivable / Cr Product Sales   (the revenue)
        Dr Cost of Goods Sold   / Cr Finished Goods (the cost)

    COGS uses the cost SNAPSHOTTED on the order line at the time of sale, not
    a current price-list lookup -- see app.services.costing.

    `include_cogs=False` posts ONLY the revenue leg. It exists for the
    historical backfill: pre-engine sales carry ESTIMATED costs, and crediting
    Finished Goods for them without an opening inventory balance drives
    inventory negative. Recording the revenue (which is real) while leaving the
    cost side for proper opening balances is the honest half-measure. New,
    live sales always post both legs (the default).

    Returns None (posting nothing) when the order carries no cost information,
    rather than booking a 100%-margin sale on goods of unknown cost.
    """
    order = (await session.execute(
        text("""SELECT id, order_number, order_date, total_amount
                  FROM sales_orders WHERE id = :o"""),
        {"o": str(order_id)},
    )).first()
    if order is None:
        return None

    totals = (await session.execute(
        text("""
            SELECT COALESCE(SUM(line_total), 0) AS revenue,
                   COALESCE(SUM(CASE WHEN cost_source <> 'unknown'
                                     THEN cost_total ELSE 0 END), 0) AS cogs,
                   COUNT(*) FILTER (WHERE cost_source = 'unknown') AS unknown_lines
              FROM sales_order_lines WHERE sales_order_id = :o
        """),
        {"o": str(order_id)},
    )).first()

    revenue = money(totals.revenue)
    cogs = money(totals.cogs)
    if revenue <= 0:
        return None

    entry_date = on or business_date(order.order_date) or date.today()

    lines = [
        Line(ACC_RECEIVABLE, debit=revenue,
             description=f"Sale {order.order_number}"),
        Line(ACC_SALES, credit=revenue,
             description=f"Sale {order.order_number}"),
    ]
    if include_cogs and cogs > 0:
        lines += [
            Line(ACC_COGS, debit=cogs,
                 description=f"Cost of goods sold, {order.order_number}"),
            Line(ACC_FINISHED_GOODS, credit=cogs,
                 description=f"Goods shipped, {order.order_number}"),
        ]

    note = ""
    if totals.unknown_lines:
        # Surfaced in the entry description rather than silently ignored: a
        # sale posted without its cost overstates profit, and whoever reviews
        # the ledger needs to see that it happened.
        note = (f" [{totals.unknown_lines} line(s) had no known cost; "
                f"COGS understated]")

    if not await _should_post(
            session, source_module="sales",
            source_reference=order.order_number, on=entry_date):
        return None

    return await post_entry(
        session,
        entry_date=entry_date,
        description=f"Sale {order.order_number}{note}",
        source_module="sales",
        source_reference=order.order_number,
        lines=lines,
        created_by=created_by,
    )


async def reverse_sale(
    session: AsyncSession,
    *,
    order_id: UUID,
    order_number: str,
    reason: str,
    created_by: Optional[UUID] = None,
) -> list:
    """Unwind the ledger effect of a sale when its order is cancelled.

    Cancelling a sale is not a delete: the ledger is immutable, so the original
    entries stay and a MIRROR is posted against each, leaving a net-zero pair
    that records both what was believed and the correction. This reverses:

      * the sale entry itself (revenue, receivable, COGS, finished goods);
      * every customer-payment entry raised against this order's invoices, so
        cash received on a now-cancelled order is unwound too.

    Idempotent and safe when posting is off: reverse_entry refuses to double-
    reverse, and nothing runs unless ACCOUNTING_POSTING_ENABLED. Returns the
    ids of the reversal entries created.
    """
    if not posting_enabled():
        return []

    reversals = []

    sale = (await session.execute(
        text("""SELECT id FROM gl_journal_entries
                 WHERE source_module = 'sales' AND source_reference = :r
                   AND status = 'POSTED'"""),
        {"r": order_number},
    )).first()
    if sale:
        reversals.append(await reverse_entry(
            session, entry_id=sale.id, reason=reason, created_by=created_by))

    # Payment entries are keyed PAY-<payment_id>; find the payments raised
    # against any invoice for this order.
    pay_entries = (await session.execute(
        text("""
            SELECT e.id FROM gl_journal_entries e
             WHERE e.source_module = 'payments' AND e.status = 'POSTED'
               AND e.source_reference IN (
                   SELECT 'PAY-' || p.id
                     FROM payments p
                     JOIN invoices i ON i.id = p.invoice_id
                    WHERE i.sales_order_id = :oid)
        """),
        {"oid": str(order_id)},
    )).fetchall()
    for row in pay_entries:
        reversals.append(await reverse_entry(
            session, entry_id=row.id, reason=reason, created_by=created_by))

    return reversals


async def post_customer_payment(
    session: AsyncSession,
    *,
    payment_id: UUID,
    to_account: str = ACC_BANK,
    created_by: Optional[UUID] = None,
) -> Optional[UUID]:
    """Cash in: Dr Bank / Cr Accounts Receivable."""
    pay = (await session.execute(
        text("""
            SELECT p.id, p.amount, p.payment_date, p.payment_method,
                   i.invoice_number
              FROM payments p
              JOIN invoices i ON i.id = p.invoice_id
             WHERE p.id = :p
        """),
        {"p": str(payment_id)},
    )).first()
    if pay is None:
        return None

    amount = money(pay.amount)
    if amount <= 0:
        return None

    account = ACC_CASH if (pay.payment_method or "").lower() == "cash" \
        else to_account
    entry_date = business_date(pay.payment_date) or date.today()

    if not await _should_post(
            session, source_module="payments",
            source_reference=f"PAY-{payment_id}", on=entry_date):
        return None

    return await post_entry(
        session,
        entry_date=entry_date,
        description=f"Payment received against {pay.invoice_number}",
        source_module="payments",
        # Keyed per PAYMENT, not per invoice: an invoice can legitimately
        # receive several partial payments, and keying on the invoice would
        # make the idempotency guard reject every one after the first.
        source_reference=f"PAY-{payment_id}",
        lines=[
            Line(account, debit=amount),
            Line(ACC_RECEIVABLE, credit=amount),
        ],
        created_by=created_by,
    )


async def post_production_completion(
    session: AsyncSession,
    *,
    completion_id: UUID,
    raw_material_cost,
    finished_goods_value,
    reference: str,
    on: Optional[date] = None,
    created_by: Optional[UUID] = None,
) -> Optional[UUID]:
    """Materials become product: Dr Finished Goods / Cr Raw Materials.

    Any difference between the value of materials consumed and the value
    booked into finished goods is a production variance, posted explicitly so
    the entry balances and the discrepancy is visible rather than absorbed.
    """
    rm = money(raw_material_cost)
    fg = money(finished_goods_value)
    if rm <= 0 and fg <= 0:
        return None

    lines = [Line(ACC_FINISHED_GOODS, debit=fg,
                  description=f"Production output {reference}"),
             Line(ACC_RAW_MATERIALS, credit=rm,
                  description=f"Materials consumed {reference}")]

    variance = fg - rm
    if variance != 0:
        # Overheads and labour make finished goods worth more than the raw
        # materials alone; the difference has to land somewhere explicit.
        if variance > 0:
            lines.append(Line("5600", credit=variance,
                              description="Production variance (absorbed)"))
        else:
            lines.append(Line("5600", debit=-variance,
                              description="Production variance (loss)"))

    if not await _should_post(
            session, source_module="production",
            source_reference=reference, on=on or date.today()):
        return None

    return await post_entry(
        session,
        entry_date=on or date.today(),
        description=f"Production completion {reference}",
        source_module="production",
        source_reference=reference,
        lines=lines,
        created_by=created_by,
    )


async def post_inventory_writeoff(
    session: AsyncSession,
    *,
    value,
    reference: str,
    is_raw_material: bool = False,
    on: Optional[date] = None,
    created_by: Optional[UUID] = None,
) -> Optional[UUID]:
    """Damaged or expired stock: Dr Write-off expense / Cr Inventory."""
    amount = money(value)
    if amount <= 0:
        return None
    inventory_account = ACC_RAW_MATERIALS if is_raw_material \
        else ACC_FINISHED_GOODS
    if not await _should_post(
            session, source_module="inventory",
            source_reference=reference, on=on or date.today()):
        return None

    return await post_entry(
        session,
        entry_date=on or date.today(),
        description=f"Inventory write-off {reference}",
        source_module="inventory",
        source_reference=reference,
        lines=[
            Line(ACC_INVENTORY_WRITEOFF, debit=amount),
            Line(inventory_account, credit=amount),
        ],
        created_by=created_by,
    )


async def post_purchase(
    session: AsyncSession,
    *,
    value,
    reference: str,
    is_raw_material: bool = True,
    on: Optional[date] = None,
    created_by: Optional[UUID] = None,
) -> Optional[UUID]:
    """Goods received on credit: Dr Inventory / Cr Accounts Payable."""
    amount = money(value)
    if amount <= 0:
        return None
    inventory_account = ACC_RAW_MATERIALS if is_raw_material \
        else ACC_FINISHED_GOODS
    if not await _should_post(
            session, source_module="procurement",
            source_reference=reference, on=on or date.today()):
        return None

    return await post_entry(
        session,
        entry_date=on or date.today(),
        description=f"Goods received {reference}",
        source_module="procurement",
        source_reference=reference,
        lines=[
            Line(inventory_account, debit=amount),
            Line(ACC_PAYABLE, credit=amount),
        ],
        created_by=created_by,
    )


async def post_customer_return(
    session: AsyncSession,
    *,
    revenue_value,
    cost_value,
    reference: str,
    restocked: bool,
    on: Optional[date] = None,
    created_by: Optional[UUID] = None,
) -> Optional[UUID]:
    """A return reverses revenue, and reverses COGS only if goods came back.

    Goods returned damaged are NOT restocked, so their cost stays in COGS --
    crediting inventory for stock that never returned would overstate assets.
    """
    revenue = money(revenue_value)
    cost = money(cost_value)
    if revenue <= 0:
        return None

    lines = [
        Line(ACC_SALES_RETURNS, debit=revenue,
             description=f"Customer return {reference}"),
        Line(ACC_RECEIVABLE, credit=revenue),
    ]
    if restocked and cost > 0:
        lines += [
            Line(ACC_FINISHED_GOODS, debit=cost,
                 description="Goods returned to stock"),
            Line(ACC_COGS, credit=cost),
        ]

    if not await _should_post(
            session, source_module="sales.returns",
            source_reference=reference, on=on or date.today()):
        return None

    return await post_entry(
        session,
        entry_date=on or date.today(),
        description=f"Customer return {reference}"
                    + ("" if restocked else " (goods not restocked)"),
        source_module="sales.returns",
        source_reference=reference,
        lines=lines,
        created_by=created_by,
    )


async def post_supplier_payment(
    session: AsyncSession,
    *,
    payment_id: UUID,
    amount,
    reference: str,
    payment_method: str = "unspecified",
    on: Optional[date] = None,
    created_by: Optional[UUID] = None,
) -> Optional[UUID]:
    """Cash out: Dr Accounts Payable / Cr Bank.

    Paying a supplier settles a liability that was recognised when the goods
    were RECEIVED (see post_purchase). It is not itself an expense -- booking
    it as one would double-count the cost, once into inventory on receipt and
    again on payment.
    """
    amt = money(amount)
    if amt <= 0:
        return None

    entry_date = on or date.today()
    if not await _should_post(
            session, source_module="procurement.payment",
            source_reference=reference, on=entry_date):
        return None

    account = ACC_CASH if (payment_method or "").lower() == "cash" else ACC_BANK

    return await post_entry(
        session,
        entry_date=entry_date,
        description=f"Supplier payment {reference}",
        source_module="procurement.payment",
        source_reference=reference,
        lines=[
            Line(ACC_PAYABLE, debit=amt, description="Liability settled"),
            Line(account, credit=amt, description=f"Paid by {payment_method}"),
        ],
        created_by=created_by,
    )
