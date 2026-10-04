"""Duplicate customer detection and merging.

Behind require_distribution_access for reading, require_admin for merging: a
merge rewrites commercial history across twelve tables and cannot be undone
cleanly, which is a heavier act than reading the customer book.
"""
from __future__ import annotations

from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.api.auth import require_admin, require_distribution_access
from app.models import User
from app.services import customer_merge as svc

router = APIRouter(prefix='/api/customers', tags=['Customer deduplication'])


class MergeIn(BaseModel):
    surviving_id: UUID
    merged_ids: List[UUID] = Field(..., min_items=1)
    reason: str = Field(..., min_length=3)


@router.get('/duplicates')
async def duplicate_candidates(
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_distribution_access),
):
    """Records that look like the same customer, computed live."""
    return await svc.candidates(session)


@router.post('/merge')
async def merge_customers(
    body: MergeIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_admin),
):
    """Combine duplicate records, moving all history onto the survivor."""
    result = await svc.merge(
        session, surviving_id=body.surviving_id, merged_ids=body.merged_ids,
        reason=body.reason, actor=user)
    await session.commit()
    return result


@router.get('/{customer_id}/merge-history')
async def merge_history(
    customer_id: UUID,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_distribution_access),
):
    return {'merges': await svc.history(session, customer_id=customer_id)}
