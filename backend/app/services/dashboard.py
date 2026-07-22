"""Executive dashboard: one consolidated read-model over the accounting system.

Every figure here is derived from the ledger and the operational tables that
feed it -- the dashboard originates nothing. It exists so the numbers a manager
looks at are the SAME numbers the ledger holds, rather than a parallel set of
hand-maintained totals that drift.

Where a figure cannot be trusted, the dashboard says so instead of showing it
as fact: a cash-flow statement that does not reconcile, or costs posted with no
cost centre, are surfaced as warnings. A dashboard that hides its own
uncertainty is worse than no dashboard.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ledger import (
    money, profit_and_loss, balance_sheet, cash_position, cash_flow_statement)


def _month_bounds(on: date) -> tuple[date, date]:
    start = on.replace(day=1)
    nxt = (start + timedelta(days=32)).replace(day=1)
    return start, nxt - timedelta(days=1)


async def _receivables_total(session: AsyncSession) -> Decimal:
    """Outstanding customer balances, from the receivables cache."""
    val = (await session.execute(
        text("""SELECT COALESCE(SUM(total_amount - paid_amount), 0)
                  FROM invoices
                 WHERE status <> 'cancelled'""")
    )).scalar()
    return money(val)


async def _payables_total(session: AsyncSession) -> Decimal:
    """Outstanding supplier balances, from purchase invoices."""
    # purchase_invoices is a runtime-created table; guard so the dashboard
    # works on a database where procurement has never been used.
    exists = (await session.execute(
        text("SELECT to_regclass('public.purchase_invoices')")
    )).scalar()
    if exists is None:
        return Decimal("0.00")
    val = (await session.execute(
        text("""SELECT COALESCE(SUM(total_amount - paid_amount), 0)
                  FROM purchase_invoices
                 WHERE status <> 'cancelled'""")
    )).scalar()
    return money(val)


async def _overdue_receivables(session: AsyncSession, on: date) -> dict:
    row = (await session.execute(
        text("""
            SELECT COUNT(*) AS n,
                   COALESCE(SUM(total_amount - paid_amount), 0) AS amt
              FROM invoices
             WHERE status <> 'cancelled'
               AND total_amount - paid_amount > 0.01
               AND due_date IS NOT NULL AND due_date < :on
        """), {"on": on},
    )).first()
    return {"count": row.n, "amount": float(money(row.amt))}


async def executive_summary(
    session: AsyncSession, *, as_at: Optional[date] = None,
) -> dict:
    """The consolidated picture: profitability, position, liquidity, exposure.

    Assembled from the same service functions the individual reports use, so
    the dashboard cannot disagree with the report a figure links to.
    """
    today = as_at or date.today()
    m_start, m_end = _month_bounds(today)
    year_start = today.replace(month=1, day=1)

    # Profitability: this month and year to date.
    pnl_month = await profit_and_loss(session, start=m_start, end=m_end)
    pnl_ytd = await profit_and_loss(session, start=year_start, end=today)

    # Financial position and liquidity.
    bs = await balance_sheet(session, as_at=today)
    cash = await cash_position(session)
    cashflow = await cash_flow_statement(session, start=m_start, end=m_end)

    # Working-capital exposure.
    receivables = await _receivables_total(session)
    payables = await _payables_total(session)
    overdue = await _overdue_receivables(session, today)

    # Cost-centre spend this month, so the biggest spenders are visible.
    cc_rows = (await session.execute(
        text("""
            SELECT COALESCE(NULLIF(TRIM(l.cost_centre), ''), 'UNALLOCATED')
                       AS centre,
                   SUM(CASE WHEN a.account_type = 'EXPENSE'
                            THEN l.debit - l.credit ELSE 0 END) AS spend
              FROM gl_journal_lines l
              JOIN gl_journal_entries e ON e.id = l.entry_id
              JOIN gl_accounts a ON a.id = l.account_id
             WHERE e.status <> 'DRAFT'
               AND e.entry_date BETWEEN :s AND :e
               AND a.account_type = 'EXPENSE'
             GROUP BY centre
             HAVING SUM(CASE WHEN a.account_type = 'EXPENSE'
                             THEN l.debit - l.credit ELSE 0 END) <> 0
             ORDER BY spend DESC
             LIMIT 6
        """), {"s": m_start, "e": m_end},
    )).fetchall()
    top_cost_centres = [
        {"cost_centre": r.centre, "spend": float(money(r.spend))}
        for r in cc_rows
    ]

    # Whether an approved budget even exists for this year -- a manager should
    # know they are flying without one rather than see an empty variance panel.
    has_budget = (await session.execute(
        text("""SELECT 1 FROM budgets
                 WHERE fiscal_year = :y AND status = 'APPROVED' LIMIT 1"""),
        {"y": today.year},
    )).first() is not None

    net_working_capital = money(
        Decimal(str(cash["total"])) + receivables - payables)

    warnings = []
    if not cashflow["reconciles"]:
        warnings.append(
            "The cash-flow statement does not reconcile to the cash balance; "
            "its classification is incomplete and should not be relied on.")
    if overdue["amount"] > 0:
        warnings.append(
            f"{overdue['count']} invoice(s) totalling "
            f"{overdue['amount']:,.2f} are overdue.")
    unalloc = next((c for c in top_cost_centres
                    if c["cost_centre"] == "UNALLOCATED"), None)
    if unalloc:
        warnings.append(
            f"{unalloc['spend']:,.2f} of this month's spend has no cost "
            f"centre and cannot be attributed to a department.")
    if not has_budget:
        warnings.append(
            f"No approved budget for {today.year}; variance cannot be reported.")

    return {
        "as_at": str(today),
        "profitability": {
            "month": {
                "period": pnl_month["period"],
                "income": pnl_month["total_income"],
                "expenses": pnl_month["total_expenses"],
                "net_profit": pnl_month["net_profit"],
            },
            "year_to_date": {
                "period": pnl_ytd["period"],
                "income": pnl_ytd["total_income"],
                "expenses": pnl_ytd["total_expenses"],
                "net_profit": pnl_ytd["net_profit"],
            },
        },
        "position": {
            "assets": bs["assets"],
            "liabilities": bs["liabilities"],
            "total_equity": bs["total_equity"],
            "balanced": bs["balanced"],
        },
        "liquidity": {
            "cash_on_hand": cash["total"],
            "cash_accounts": cash["accounts"],
            "net_cash_movement_month": cashflow["net_movement"],
            "net_working_capital": float(net_working_capital),
        },
        "working_capital": {
            "receivables": float(receivables),
            "payables": float(payables),
            "overdue_receivables": overdue,
        },
        "top_cost_centres": top_cost_centres,
        "has_approved_budget": has_budget,
        "warnings": warnings,
    }
