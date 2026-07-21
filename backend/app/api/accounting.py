"""Accounting API: chart of accounts, journal, and financial statements.

Read endpoints are open to any authenticated user; anything that writes to the
ledger, closes a period, or changes the chart of accounts is admin-only.
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.api.auth import require_authenticated_user, require_admin
from app.models import User
from app.services.ledger import (
    Line, post_entry, reverse_entry, trial_balance, profit_and_loss,
    balance_sheet, account_ledger)

router = APIRouter(prefix='/api/accounting', tags=['Accounting'])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class JournalLineIn(BaseModel):
    account_code: str
    debit: float = 0
    credit: float = 0
    description: Optional[str] = None
    cost_centre: Optional[str] = None


class JournalEntryIn(BaseModel):
    entry_date: date
    description: str = Field(..., min_length=1)
    lines: List[JournalLineIn] = Field(..., min_length=2)
    source_reference: Optional[str] = None


class ReversalIn(BaseModel):
    reason: str = Field(..., min_length=1)
    entry_date: Optional[date] = None


class PeriodIn(BaseModel):
    name: str
    start_date: date
    end_date: date


# ---------------------------------------------------------------------------
# Chart of accounts
# ---------------------------------------------------------------------------

@router.get('/accounts')
async def list_accounts(
    account_type: Optional[str] = Query(None),
    active_only: bool = Query(True),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    clauses, params = [], {}
    if account_type:
        clauses.append("account_type = :t")
        params["t"] = account_type.upper()
    if active_only:
        clauses.append("is_active = TRUE")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    rows = (await session.execute(
        text(f"""SELECT code, name, account_type, normal_balance,
                        is_postable, is_active, description
                   FROM gl_accounts {where} ORDER BY code"""),
        params,
    )).fetchall()
    return [dict(r._mapping) for r in rows]


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------

@router.post('/journal')
async def create_journal_entry(
    body: JournalEntryIn,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Post a manual journal entry. Rejected unless debits equal credits."""
    try:
        entry_id = await post_entry(
            session,
            entry_date=body.entry_date,
            description=body.description,
            source_module="manual",
            source_reference=body.source_reference,
            lines=[Line(account_code=ln.account_code, debit=ln.debit,
                        credit=ln.credit, description=ln.description,
                        cost_centre=ln.cost_centre)
                   for ln in body.lines],
            created_by=current_user.id,
        )
        await session.commit()
        return {"success": True, "entry_id": str(entry_id)}
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Posting failed: {e}")


@router.post('/journal/{entry_id}/reverse')
async def reverse_journal_entry(
    entry_id: UUID,
    body: ReversalIn,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Reverse an entry. The original is preserved; nothing is deleted."""
    try:
        reversal_id = await reverse_entry(
            session, entry_id=entry_id, reason=body.reason,
            on=body.entry_date, created_by=current_user.id)
        await session.commit()
        return {"success": True, "reversal_entry_id": str(reversal_id)}
    except HTTPException:
        await session.rollback()
        raise


@router.get('/journal')
async def list_journal_entries(
    start: Optional[date] = Query(None),
    end: Optional[date] = Query(None),
    source_module: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    clauses, params = [], {"limit": limit, "offset": offset}
    if start:
        clauses.append("e.entry_date >= :start"); params["start"] = start
    if end:
        clauses.append("e.entry_date <= :end"); params["end"] = end
    if source_module:
        clauses.append("e.source_module = :m"); params["m"] = source_module
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    rows = (await session.execute(
        text(f"""
            SELECT e.id, e.entry_number, e.entry_date, e.description,
                   e.source_module, e.source_reference, e.status,
                   COALESCE(SUM(l.debit), 0) AS total
              FROM gl_journal_entries e
              LEFT JOIN gl_journal_lines l ON l.entry_id = e.id
              {where}
             GROUP BY e.id, e.entry_number, e.entry_date, e.description,
                      e.source_module, e.source_reference, e.status, e.seq
             ORDER BY e.entry_date DESC, e.seq DESC
             LIMIT :limit OFFSET :offset
        """),
        params,
    )).fetchall()
    return [
        {"id": str(r.id), "entry_number": r.entry_number,
         "entry_date": str(r.entry_date), "description": r.description,
         "source_module": r.source_module, "reference": r.source_reference,
         "status": r.status, "amount": float(r.total)}
        for r in rows
    ]


@router.get('/journal/{entry_id}')
async def get_journal_entry(
    entry_id: UUID,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    entry = (await session.execute(
        text("""SELECT id, entry_number, entry_date, description,
                       source_module, source_reference, status, posted_at
                  FROM gl_journal_entries WHERE id = :i"""),
        {"i": str(entry_id)},
    )).first()
    if entry is None:
        raise HTTPException(status_code=404, detail="Journal entry not found")

    lines = (await session.execute(
        text("""SELECT a.code, a.name, l.debit, l.credit, l.description,
                       l.cost_centre
                  FROM gl_journal_lines l
                  JOIN gl_accounts a ON a.id = l.account_id
                 WHERE l.entry_id = :i ORDER BY l.line_number"""),
        {"i": str(entry_id)},
    )).fetchall()

    return {
        "id": str(entry.id), "entry_number": entry.entry_number,
        "entry_date": str(entry.entry_date), "description": entry.description,
        "source_module": entry.source_module,
        "reference": entry.source_reference, "status": entry.status,
        "lines": [
            {"account_code": r.code, "account_name": r.name,
             "debit": float(r.debit), "credit": float(r.credit),
             "description": r.description, "cost_centre": r.cost_centre}
            for r in lines
        ],
    }


# ---------------------------------------------------------------------------
# Financial statements
# ---------------------------------------------------------------------------

@router.get('/trial-balance')
async def get_trial_balance(
    start: Optional[date] = Query(None),
    end: Optional[date] = Query(None),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    return await trial_balance(session, start=start, end=end)


@router.get('/profit-and-loss')
async def get_profit_and_loss(
    start: Optional[date] = Query(None),
    end: Optional[date] = Query(None),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    return await profit_and_loss(session, start=start, end=end)


@router.get('/balance-sheet')
async def get_balance_sheet(
    as_at: Optional[date] = Query(None),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    return await balance_sheet(session, as_at=as_at)


@router.get('/accounts/{account_code}/ledger')
async def get_account_ledger(
    account_code: str,
    start: Optional[date] = Query(None),
    end: Optional[date] = Query(None),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    return await account_ledger(
        session, account_code=account_code, start=start, end=end)


# ---------------------------------------------------------------------------
# Periods
# ---------------------------------------------------------------------------

@router.get('/periods')
async def list_periods(
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    rows = (await session.execute(
        text("""SELECT id, name, start_date, end_date, status, closed_at
                  FROM gl_periods ORDER BY start_date DESC""")
    )).fetchall()
    return [
        {"id": str(r.id), "name": r.name, "start_date": str(r.start_date),
         "end_date": str(r.end_date), "status": r.status,
         "closed_at": r.closed_at.isoformat() if r.closed_at else None}
        for r in rows
    ]


@router.post('/periods')
async def create_period(
    body: PeriodIn,
    session: AsyncSession = Depends(get_session),
    _admin: User = Depends(require_admin),
):
    if body.end_date < body.start_date:
        raise HTTPException(
            status_code=400, detail="end_date cannot precede start_date")
    try:
        await session.execute(
            text("""INSERT INTO gl_periods (id, name, start_date, end_date)
                    VALUES (gen_random_uuid(), :n, :s, :e)"""),
            {"n": body.name, "s": body.start_date, "e": body.end_date},
        )
        await session.commit()
        return {"success": True, "message": f"Period {body.name} created"}
    except Exception as e:
        await session.rollback()
        # The exclusion constraint rejects overlaps; say so in plain terms.
        if "ex_gl_periods_no_overlap" in str(e):
            raise HTTPException(
                status_code=400,
                detail="That date range overlaps an existing period.")
        raise HTTPException(status_code=400, detail=f"Could not create: {e}")


@router.post('/periods/{period_id}/close')
async def close_period(
    period_id: UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Close a period so nothing further can be posted into it."""
    result = await session.execute(
        text("""UPDATE gl_periods
                   SET status = 'CLOSED', closed_at = NOW(), closed_by = :u
                 WHERE id = :i AND status = 'OPEN'
             RETURNING name"""),
        {"i": str(period_id), "u": str(current_user.id)},
    )
    row = result.first()
    if row is None:
        await session.rollback()
        raise HTTPException(
            status_code=400,
            detail="Period not found or already closed.")
    await session.commit()
    return {"success": True, "message": f"Period {row.name} closed"}


@router.post('/periods/{period_id}/reopen')
async def reopen_period(
    period_id: UUID,
    session: AsyncSession = Depends(get_session),
    _admin: User = Depends(require_admin),
):
    """Reopen a closed period.

    Deliberately admin-only and logged by the audit trail: reopening a
    reported period means published figures can change.
    """
    result = await session.execute(
        text("""UPDATE gl_periods
                   SET status = 'OPEN', closed_at = NULL, closed_by = NULL
                 WHERE id = :i AND status = 'CLOSED'
             RETURNING name"""),
        {"i": str(period_id)},
    )
    row = result.first()
    if row is None:
        await session.rollback()
        raise HTTPException(
            status_code=400, detail="Period not found or already open.")
    await session.commit()
    return {"success": True,
            "message": f"Period {row.name} reopened -- previously reported "
                       f"figures for this period may now change."}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@router.get('/health')
async def ledger_health(
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    """Is the ledger internally consistent?

    Checks the two things that must never be false: the trial balance
    balances, and the balance-sheet equation holds. If either fails, stop
    trusting the statements and investigate before posting anything else.
    """
    tb = await trial_balance(session)
    bs = await balance_sheet(session)

    unbalanced = (await session.execute(
        text("""
            SELECT e.entry_number,
                   COALESCE(SUM(l.debit), 0)  AS dr,
                   COALESCE(SUM(l.credit), 0) AS cr
              FROM gl_journal_entries e
              JOIN gl_journal_lines l ON l.entry_id = e.id
             GROUP BY e.id, e.entry_number
            HAVING COALESCE(SUM(l.debit), 0) <> COALESCE(SUM(l.credit), 0)
        """)
    )).fetchall()

    healthy = tb["balanced"] and bs["balanced"] and not unbalanced
    return {
        "healthy": healthy,
        "trial_balance_balanced": tb["balanced"],
        "trial_balance_difference": tb["difference"],
        "balance_sheet_balanced": bs["balanced"],
        "balance_sheet_difference": bs["difference"],
        "unbalanced_entries": [
            {"entry_number": r.entry_number, "debit": float(r.dr),
             "credit": float(r.cr)} for r in unbalanced
        ],
        "summary": ("Ledger is internally consistent."
                    if healthy else
                    "LEDGER INCONSISTENT -- do not rely on the financial "
                    "statements until this is resolved."),
    }
