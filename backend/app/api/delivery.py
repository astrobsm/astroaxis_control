"""The delivery workflow: dispatching a run, and what happened at each drop.

Replaces the status endpoint in logistics.py, which accepted any string at
all. That one is left in place and now delegates here, so nothing that calls
it breaks while the validation starts applying.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.api.auth import require_authenticated_user
from app.models import User
from app.services import delivery as svc

router = APIRouter(prefix='/api/delivery', tags=['Delivery'])


class ManifestStatusIn(BaseModel):
    status: str
    note: Optional[str] = None


class DropStatusIn(BaseModel):
    status: str
    reason: Optional[str] = None
    receiver_name: Optional[str] = None


class ReturnIn(BaseModel):
    warehouse_id: UUID


@router.put('/manifests/{manifest_id}/status')
async def set_manifest_status(
    manifest_id: UUID,
    body: ManifestStatusIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_authenticated_user),
):
    """Move a run along. Refuses a transition that makes no sense."""
    result = await svc.set_manifest_status(
        session, manifest_id=manifest_id, status=body.status,
        note=body.note, actor=user)
    await session.commit()
    return result


@router.put('/drops/{drop_id}/status')
async def set_drop_status(
    drop_id: UUID,
    body: DropStatusIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_authenticated_user),
):
    """Record what happened at one customer. A failure needs a reason."""
    result = await svc.set_drop_status(
        session, drop_id=drop_id, status=body.status, reason=body.reason,
        receiver_name=body.receiver_name, actor=user)
    await session.commit()
    return result


@router.post('/drops/{drop_id}/return-to-stock')
async def return_to_stock(
    drop_id: UUID,
    body: ReturnIn,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_authenticated_user),
):
    """Put a failed delivery's goods back on the shelf. Runs once."""
    result = await svc.return_to_stock(
        session, drop_id=drop_id, warehouse_id=body.warehouse_id, actor=user)
    await session.commit()
    return result


@router.get('/awaiting-return')
async def awaiting_return(
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    """Failed deliveries whose goods are not yet back on the system."""
    return await svc.awaiting_return(session)


@router.get('/unclosed-runs')
async def unclosed_runs(
    older_than_days: int = 2,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    """Runs that went out and were never closed.

    Declared before the /manifests/{id} route so a static path cannot be
    swallowed by a parameter route.
    """
    return await svc.unclosed_runs(session, older_than_days=older_than_days)


@router.get('/manifests/{manifest_id}/history')
async def manifest_history(
    manifest_id: UUID,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    return {'events': await svc.history(session, manifest_id=manifest_id)}
