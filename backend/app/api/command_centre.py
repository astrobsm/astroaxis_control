"""The distribution command centre.

Reads are open to any authenticated user. Exports are administrator-only and
recorded: a CSV of the distributor register takes names, phone numbers and
trading history out of the building on somebody's laptop.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_admin, require_authenticated_user
from app.db import get_session
from app.models import User
from app.services import command_centre as svc

router = APIRouter(prefix="/api/command-centre",
                   tags=["Distribution command centre"])


@router.get("")
async def overview(
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Counts, coverage and this month's sales by provenance.

    There is deliberately no overall health score: producing one would mean
    averaging a compliance failure against a good sales month.
    """
    return await svc.command_centre(session)


@router.get("/coverage")
async def coverage(
    since: Optional[date] = None,
    until: Optional[date] = None,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Per-state coverage and verified sales.

    A state with no LGAs loaded returns NULL sales and status
    NO_COVERAGE_DATA -- not zero. Rendering those the same colour would say
    "nobody is selling there" when the truth is "nothing could be recorded
    there".
    """
    return await svc.coverage_map(session, since=since, until=until)


@router.get("/ranking")
async def ranking(
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Distributors ranked on VERIFIED sales for one month."""
    return await svc.ranking(session, year=year, month=month)


@router.get("/territories")
async def territory_rollup(
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Territories with the target in force and what was verified against it."""
    return await svc.territory_rollup(session, year=year, month=month)


@router.get("/exports")
async def list_exports(
    user: User = Depends(require_authenticated_user),
):
    return {
        "datasets": [{"name": k, "description": v}
                     for k, v in sorted(svc.EXPORTS.items())],
        "note": ("Sales exports carry separate verified and reported columns "
                 "rather than one total. A spreadsheet is where a claim and a "
                 "confirmed fact become the same column, and nothing here can "
                 "put the distinction back afterwards."),
    }


@router.get("/exports/{dataset}")
async def export(
    dataset: str,
    request: Request,
    year: Optional[int] = Query(None, ge=2000, le=2100),
    month: Optional[int] = Query(None, ge=1, le=12),
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Download a CSV. The download is recorded in the distributor audit trail."""
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() or (
        request.client.host if request.client else "")

    content, filename, rows = await svc.export_csv(
        session, dataset=dataset, year=year, month=month, actor=user,
        ip_address=ip, user_agent=request.headers.get("user-agent", ""))
    await session.commit()

    return Response(
        content=content, media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Row-Count": str(rows),
        })
