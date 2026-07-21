"""Fixed assets, depreciation, and cash flow reporting."""
from __future__ import annotations

import uuid
from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.api.auth import require_authenticated_user, require_admin
from app.models import User
from app.services.assets import (
    run_depreciation, dispose_asset, asset_register, carrying_amount)
from app.services.ledger import cash_flow_statement, cash_position

router = APIRouter(prefix='/api/assets', tags=['Fixed Assets'])


class AssetIn(BaseModel):
    name: str = Field(..., min_length=1)
    category: str = 'equipment'
    acquisition_date: date
    cost: float = Field(..., gt=0)
    residual_value: float = 0
    useful_life_months: int = Field(..., gt=0)
    method: str = 'STRAIGHT_LINE'
    annual_rate_percent: Optional[float] = None
    asset_account: str = '1520'
    accumulated_account: str = '1590'
    expense_account: str = '6800'
    cost_centre: Optional[str] = None
    location: Optional[str] = None
    serial_number: Optional[str] = None
    description: Optional[str] = None


class DepreciationRunIn(BaseModel):
    period_start: date
    period_end: date


class DisposalIn(BaseModel):
    disposal_date: date
    proceeds: float = Field(..., ge=0)
    notes: Optional[str] = None


@router.get('/')
async def list_assets(
    include_disposed: bool = Query(False),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    """Fixed asset register with carrying amounts.

    Carrying amount is derived from the recorded depreciation charges, not
    from a stored current_value that a read endpoint kept overwriting.
    """
    return await asset_register(session, include_disposed=include_disposed)


@router.post('/')
async def create_asset(
    body: AssetIn,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    if body.residual_value > body.cost:
        raise HTTPException(
            status_code=400, detail="Residual value cannot exceed cost.")
    if body.method == 'REDUCING_BALANCE' and not body.annual_rate_percent:
        raise HTTPException(
            status_code=400,
            detail="Reducing-balance depreciation requires an annual rate.")
    try:
        asset_number = f"FA-{uuid.uuid4().hex[:8].upper()}"
        await session.execute(text("""
            INSERT INTO fixed_assets
                (asset_number, name, description, category, acquisition_date,
                 cost, residual_value, useful_life_months, method,
                 annual_rate_percent, asset_account, accumulated_account,
                 expense_account, cost_centre, location, serial_number,
                 created_by)
            VALUES (:num, :name, :descr, :cat, :acq, :cost, :res, :life,
                    :method, :rate, :aa, :ca, :ea, :cc, :loc, :sn, :by)
        """), {
            "num": asset_number, "name": body.name,
            "descr": body.description, "cat": body.category,
            "acq": body.acquisition_date, "cost": body.cost,
            "res": body.residual_value, "life": body.useful_life_months,
            "method": body.method, "rate": body.annual_rate_percent,
            "aa": body.asset_account, "ca": body.accumulated_account,
            "ea": body.expense_account, "cc": body.cost_centre,
            "loc": body.location, "sn": body.serial_number,
            "by": str(current_user.id),
        })
        await session.commit()
        return {"success": True, "asset_number": asset_number}
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.post('/depreciation/run')
async def post_depreciation(
    body: DepreciationRunIn,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Charge one period of depreciation and post it to the ledger.

    Idempotent: a unique constraint on (asset, period) means a retried run
    cannot double-charge.
    """
    try:
        result = await run_depreciation(
            session, period_start=body.period_start,
            period_end=body.period_end, created_by=current_user.id)
        await session.commit()
        return {
            "success": True,
            "run_number": result["run_number"],
            "asset_count": result["asset_count"],
            "total_charge": float(result["total_charge"]),
            "journal_entry_id": (str(result["journal_entry_id"])
                                 if result["journal_entry_id"] else None),
            "charges": [
                {"asset_number": c["asset_number"], "name": c["name"],
                 "charge": float(c["charge"]),
                 "closing_carrying_amount":
                     float(c["closing_carrying_amount"])}
                for c in result["charges"]],
        }
    except HTTPException:
        await session.rollback()
        raise


@router.post('/{asset_id}/dispose')
async def dispose(
    asset_id: UUID,
    body: DisposalIn,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Dispose of an asset, recognising the gain or loss."""
    try:
        r = await dispose_asset(
            session, asset_id=asset_id, disposal_date=body.disposal_date,
            proceeds=body.proceeds, notes=body.notes,
            created_by=current_user.id)
        await session.commit()
        return {
            "success": True,
            "asset_number": r["asset_number"],
            "cost": float(r["cost"]),
            "accumulated_depreciation": float(r["accumulated_depreciation"]),
            "carrying_amount": float(r["carrying_amount"]),
            "proceeds": float(r["proceeds"]),
            "gain_or_loss": float(r["gain_or_loss"]),
            "outcome": r["outcome"],
            "journal_entry_id": (str(r["journal_entry_id"])
                                 if r["journal_entry_id"] else None),
        }
    except HTTPException:
        await session.rollback()
        raise


@router.get('/{asset_id}/schedule')
async def asset_schedule(
    asset_id: UUID,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    """Every depreciation charge recorded against one asset."""
    rows = (await session.execute(text("""
        SELECT period_start, period_end, amount,
               opening_carrying_amount, closing_carrying_amount
          FROM asset_depreciation_charges
         WHERE asset_id = :a ORDER BY period_start
    """), {"a": str(asset_id)})).fetchall()
    return {
        "carrying_amount": float(
            await carrying_amount(session, asset_id=asset_id)),
        "charges": [
            {"period_start": str(r.period_start),
             "period_end": str(r.period_end),
             "amount": float(r.amount),
             "opening_carrying_amount": float(r.opening_carrying_amount),
             "closing_carrying_amount": float(r.closing_carrying_amount)}
            for r in rows],
    }


# ---------------------------------------------------------------------------
# Cash flow
# ---------------------------------------------------------------------------

cash_router = APIRouter(prefix='/api/cash', tags=['Cash Flow'])


@cash_router.get('/flow')
async def get_cash_flow(
    start: date = Query(...),
    end: date = Query(...),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    """Cash flow statement for a period, derived from the journal.

    Every figure traces back to the entries that produced it, and the
    statement reports whether it reconciles to the actual cash balance --
    if it does not, the classification has dropped something and the report
    should not be relied on.
    """
    if end < start:
        raise HTTPException(status_code=400, detail="end cannot precede start.")
    return await cash_flow_statement(session, start=start, end=end)


@cash_router.get('/position')
async def get_cash_position(
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    """Current balance of every cash and bank account."""
    return await cash_position(session)
