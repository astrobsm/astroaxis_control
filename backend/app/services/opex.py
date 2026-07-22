"""Operational expenditure -> journal translation.

Maintenance and QC costing share one shape: an expense is incurred against a
department, and it has to reach the ledger classified to a cost centre so it
shows up in the P&L and the cost-centre report instead of living only in an
operational table nobody in finance reads.

This is the sibling of app.services.posting for costs that originate in the
operations modules rather than in sales/production. It obeys the same rules:

  * posting is gated OFF by default (the same ACCOUNTING_POSTING_ENABLED /
    cutover switches), so turning the accounting engine on is one decision;
  * it never commits -- the journal lands in the caller's transaction, so the
    operational record and its accounting consequence are atomic;
  * it is idempotent on the source reference, so a retried request cannot post
    the same cost twice into an immutable ledger.
"""
from __future__ import annotations

from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.budgeting import resolve_cost_centre
from app.services.ledger import Line, money, post_entry
from app.services.posting import _should_post

# Where the cash came from. An operational cost is either paid now (cash/bank)
# or incurred on credit (accounts payable), and the credit side differs.
ACC_CASH = "1100"
ACC_BANK = "1200"
ACC_PAYABLE = "2100"

_CREDIT_ACCOUNT = {"cash": ACC_CASH, "bank": ACC_BANK, "credit": ACC_PAYABLE}


async def post_operational_cost(
    session: AsyncSession,
    *,
    expense_account: str,
    amount,
    reference: str,
    description: str,
    cost_centre: str,
    source_module: str,
    paid_from: str = "bank",
    on: Optional[date] = None,
    created_by: Optional[UUID] = None,
) -> Optional[UUID]:
    """Book an operating cost: Dr expense (to a cost centre) / Cr cash|bank|AP.

    The cost centre is validated against the master, not trusted as free text
    -- the whole point of costing a maintenance job or a QC check is that it
    lands against a department, and 'Maint'/'MAINT'/'maintenance' splitting
    into three would defeat that.

    Returns the entry id, or None if posting is disabled / before cutover /
    already posted for this reference. A zero or negative amount posts nothing.
    """
    amt = money(amount)
    if amt <= 0:
        return None

    credit_account = _CREDIT_ACCOUNT.get((paid_from or "bank").lower())
    if credit_account is None:
        raise HTTPException(
            status_code=400,
            detail=f"paid_from must be one of {sorted(_CREDIT_ACCOUNT)}, "
                   f"not {paid_from!r}.")

    # Validate the cost centre up front so a bad code fails the request rather
    # than posting an unattributable cost.
    centre = await resolve_cost_centre(session, code=cost_centre)

    entry_date = on or date.today()
    if not await _should_post(
            session, source_module=source_module,
            source_reference=reference, on=entry_date):
        return None

    return await post_entry(
        session,
        entry_date=entry_date,
        description=description,
        source_module=source_module,
        source_reference=reference,
        lines=[
            Line(expense_account, debit=amt, description=description,
                 cost_centre=centre),
            Line(credit_account, credit=amt,
                 description=f"Paid by {paid_from}"),
        ],
        created_by=created_by,
    )
