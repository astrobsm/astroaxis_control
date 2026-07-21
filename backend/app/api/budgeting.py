"""Cost centres, budgets and variance reporting."""
from __future__ import annotations

import uuid
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
from app.services.budgeting import (
    resolve_cost_centre, cost_centre_report, approve_budget, budget_variance)

router = APIRouter(prefix='/api/budgeting', tags=['Budgeting'])


class CostCentreIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1)
    category: str = 'operations'
    manager_name: Optional[str] = None


class BudgetLineIn(BaseModel):
    account_code: str
    cost_centre: Optional[str] = None
    period_month: date
    amount: float = Field(..., ge=0)
    notes: Optional[str] = None


class BudgetIn(BaseModel):
    name: str = Field(..., min_length=1)
    fiscal_year: int
    period_start: date
    period_end: date
    lines: List[BudgetLineIn] = []
    notes: Optional[str] = None


class ApproveIn(BaseModel):
    approved_by: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Cost centres
# ---------------------------------------------------------------------------

@router.get('/cost-centres')
async def list_cost_centres(
    active_only: bool = Query(True),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    where = "WHERE is_active = TRUE" if active_only else ""
    rows = (await session.execute(text(f"""
        SELECT code, name, category, manager_name, is_active, notes
          FROM cost_centres {where} ORDER BY category, code
    """))).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post('/cost-centres')
async def create_cost_centre(
    body: CostCentreIn,
    session: AsyncSession = Depends(get_session),
    _admin: User = Depends(require_admin),
):
    try:
        await session.execute(text("""
            INSERT INTO cost_centres (code, name, category, manager_name)
            VALUES (UPPER(TRIM(:c)), :n, :cat, :m)
        """), {"c": body.code, "n": body.name, "cat": body.category,
               "m": body.manager_name})
        await session.commit()
        return {"success": True, "code": body.code.strip().upper()}
    except Exception as e:
        await session.rollback()
        if "uq_cost_centres_code_ci" in str(e):
            raise HTTPException(
                status_code=400,
                detail=f"A cost centre with code {body.code!r} already "
                       f"exists (codes are case-insensitive).")
        raise HTTPException(status_code=400, detail=str(e))


@router.get('/cost-centres/report')
async def get_cost_centre_report(
    start: date = Query(...),
    end: date = Query(...),
    cost_centre: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    """Income and expenditure by cost centre.

    Postings with no cost centre appear as UNALLOCATED rather than being
    dropped -- unattributed cost is a finding, not a rounding error.
    """
    if end < start:
        raise HTTPException(status_code=400, detail="end cannot precede start.")
    return await cost_centre_report(
        session, start=start, end=end, cost_centre=cost_centre)


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------

@router.get('/budgets')
async def list_budgets(
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    rows = (await session.execute(text("""
        SELECT b.id, b.name, b.fiscal_year, b.period_start, b.period_end,
               b.status, b.version, b.approved_by, b.approved_at,
               COALESCE(SUM(bl.amount), 0) AS total,
               COUNT(bl.id) AS line_count
          FROM budgets b LEFT JOIN budget_lines bl ON bl.budget_id = b.id
         GROUP BY b.id ORDER BY b.fiscal_year DESC, b.created_at DESC
    """))).fetchall()
    return [
        {"id": str(r.id), "name": r.name, "fiscal_year": r.fiscal_year,
         "period_start": str(r.period_start), "period_end": str(r.period_end),
         "status": r.status, "version": r.version,
         "approved_by": r.approved_by,
         "approved_at": r.approved_at.isoformat() if r.approved_at else None,
         "total": float(r.total), "line_count": r.line_count}
        for r in rows
    ]


@router.post('/budgets')
async def create_budget(
    body: BudgetIn,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Create a DRAFT budget. It is not reported against until approved."""
    if body.period_end < body.period_start:
        raise HTTPException(
            status_code=400, detail="period_end cannot precede period_start.")
    try:
        budget_id = uuid.uuid4()
        await session.execute(text("""
            INSERT INTO budgets
                (id, name, fiscal_year, period_start, period_end, notes,
                 created_by)
            VALUES (:i, :n, :y, :s, :e, :notes, :by)
        """), {"i": str(budget_id), "n": body.name, "y": body.fiscal_year,
               "s": body.period_start, "e": body.period_end,
               "notes": body.notes, "by": str(current_user.id)})

        for ln in body.lines:
            cc = None
            if ln.cost_centre:
                # Validate against the master so a typo cannot create a
                # phantom cost centre that never appears in any report.
                cc = await resolve_cost_centre(session, code=ln.cost_centre)
            await session.execute(text("""
                INSERT INTO budget_lines
                    (budget_id, account_code, cost_centre, period_month,
                     amount, notes)
                VALUES (:b, :a, :c, DATE_TRUNC('month', CAST(:m AS date)),
                        :amt, :notes)
                ON CONFLICT (budget_id, account_code, cost_centre,
                             period_month)
                DO UPDATE SET amount = EXCLUDED.amount
            """), {"b": str(budget_id), "a": ln.account_code, "c": cc,
                   "m": ln.period_month, "amt": ln.amount,
                   "notes": ln.notes})

        await session.commit()
        return {"success": True, "budget_id": str(budget_id),
                "status": "DRAFT",
                "next_step": "Approve the budget before reporting against it."}
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.post('/budgets/{budget_id}/approve')
async def approve(
    budget_id: UUID,
    body: ApproveIn,
    session: AsyncSession = Depends(get_session),
    _admin: User = Depends(require_admin),
):
    """Approve a budget, superseding any earlier approved one for that year."""
    try:
        result = await approve_budget(
            session, budget_id=budget_id, approved_by=body.approved_by)
        await session.commit()
        return {"success": True, **result}
    except HTTPException:
        await session.rollback()
        raise


@router.get('/budgets/{budget_id}/lines')
async def budget_lines(
    budget_id: UUID,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    rows = (await session.execute(text("""
        SELECT bl.account_code, a.name AS account_name, bl.cost_centre,
               bl.period_month, bl.amount, bl.notes
          FROM budget_lines bl
          LEFT JOIN gl_accounts a ON a.code = bl.account_code
         WHERE bl.budget_id = :b
         ORDER BY bl.period_month, bl.account_code
    """), {"b": str(budget_id)})).fetchall()
    return [
        {"account_code": r.account_code, "account_name": r.account_name,
         "cost_centre": r.cost_centre, "period_month": str(r.period_month),
         "amount": float(r.amount), "notes": r.notes}
        for r in rows
    ]


@router.get('/variance')
async def get_variance(
    fiscal_year: int = Query(...),
    start: Optional[date] = Query(None),
    end: Optional[date] = Query(None),
    cost_centre: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    """Budget against actual, with each variance interpreted.

    Variance is reported as FAVOURABLE or ADVERSE rather than as a bare
    signed number: under-spending and under-earning are both negative, but
    one is good news and the other is not.
    """
    return await budget_variance(
        session, fiscal_year=fiscal_year, start=start, end=end,
        cost_centre=cost_centre)
