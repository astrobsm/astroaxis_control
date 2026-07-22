"""VAT returns: prepare, file, and remit."""
from __future__ import annotations

from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.api.auth import require_authenticated_user, require_admin
from app.models import User
from app.services.tax import (
    compute_vat_return, file_vat_return, record_vat_payment, list_vat_returns)

router = APIRouter(prefix='/api/tax', tags=['Tax'])


class FileReturnIn(BaseModel):
    period_start: date
    period_end: date
    filed_by: str = Field(..., min_length=1)
    firs_reference: Optional[str] = None
    notes: Optional[str] = None


class VatPaymentIn(BaseModel):
    amount: float = Field(..., gt=0)
    paid_from: str = 'bank'
    on: Optional[date] = None


@router.get('/vat/compute')
async def compute(
    start: date = Query(...),
    end: date = Query(...),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    """Compute (without saving) the VAT position for a period."""
    return await compute_vat_return(session, start=start, end=end)


@router.get('/vat/returns')
async def list_returns(
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    return await list_vat_returns(session)


@router.post('/vat/returns')
async def file_return(
    body: FileReturnIn,
    session: AsyncSession = Depends(get_session),
    _admin: User = Depends(require_admin),
):
    """File a return: snapshot the period's figures as declared to FIRS."""
    try:
        result = await file_vat_return(
            session, start=body.period_start, end=body.period_end,
            filed_by=body.filed_by, firs_reference=body.firs_reference,
            notes=body.notes)
        await session.commit()
        return {"success": True, **result}
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.post('/vat/returns/{vat_return_id}/payment')
async def pay_return(
    vat_return_id: UUID,
    body: VatPaymentIn,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Record remittance of a filed return and post it to the ledger."""
    try:
        result = await record_vat_payment(
            session, vat_return_id=vat_return_id, amount=body.amount,
            paid_from=body.paid_from, on=body.on, created_by=current_user.id)
        await session.commit()
        return {"success": True, **result}
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e))
