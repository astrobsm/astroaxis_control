"""Executive dashboard: one consolidated view over the accounting system."""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.api.auth import require_authenticated_user
from app.models import User
from app.services.dashboard import executive_summary

router = APIRouter(prefix='/api/dashboard', tags=['Executive Dashboard'])


@router.get('/executive')
async def executive(
    as_at: Optional[date] = Query(None),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    """Profitability, position, liquidity and exposure in one call.

    Every figure is derived from the ledger; where a number cannot be trusted
    (an unreconciled cash flow, unallocated cost, no approved budget) the
    dashboard returns a warning rather than presenting it as fact.
    """
    return await executive_summary(session, as_at=as_at)
