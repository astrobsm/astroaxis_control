"""Geo-tagging support for clock-in/out and access events.

Provides:
- Runtime DDL bootstrap to add lat/lng/accuracy/address columns to
  `attendance` and `audit_logs` tables (idempotent — uses IF NOT EXISTS).
- GET /api/geo/attendance — list staff clock-in/out events with location.
- GET /api/geo/access     — list user access (login/logout) events with location.
- GET /api/geo/health     — quick sanity check that columns are present.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session

router = APIRouter(prefix="/api/geo", tags=["geo"])


# ─────────────────────────────────────────────────────────────────────────────
# DDL bootstrap (runs at startup; safe to re-run)
# ─────────────────────────────────────────────────────────────────────────────
ATTENDANCE_ALTERS = [
    "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS clock_in_lat DOUBLE PRECISION",
    "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS clock_in_lng DOUBLE PRECISION",
    "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS clock_in_accuracy DOUBLE PRECISION",
    "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS clock_in_address TEXT",
    "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS clock_out_lat DOUBLE PRECISION",
    "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS clock_out_lng DOUBLE PRECISION",
    "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS clock_out_accuracy DOUBLE PRECISION",
    "ALTER TABLE attendance ADD COLUMN IF NOT EXISTS clock_out_address TEXT",
]

AUDIT_ALTERS = [
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION",
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION",
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS accuracy DOUBLE PRECISION",
    "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS location_address TEXT",
]

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_attendance_clock_in_loc ON attendance(clock_in_lat, clock_in_lng)",
    "CREATE INDEX IF NOT EXISTS idx_audit_logs_loc ON audit_logs(latitude, longitude)",
]


async def bootstrap_geo_schema(session: AsyncSession) -> None:
    """Idempotently add geo columns + indexes. Call once at startup."""
    for sql in (*ATTENDANCE_ALTERS, *AUDIT_ALTERS, *INDEXES):
        try:
            await session.execute(text(sql))
        except Exception:  # pragma: no cover - best effort
            # Some Postgres versions/perm issues — continue.
            await session.rollback()
            continue
    await session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Map endpoints
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/health")
async def geo_health(session: AsyncSession = Depends(get_session)):
    row = (await session.execute(text(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_name='attendance' AND column_name LIKE 'clock_%_lat'"
    ))).scalar() or 0
    return {"attendance_geo_columns": int(row), "ok": int(row) >= 2}


@router.get("/attendance")
async def list_attendance_geo(
    days: int = Query(30, ge=1, le=365),
    staff_id: Optional[str] = Query(None),
    limit: int = Query(500, ge=1, le=5000),
    session: AsyncSession = Depends(get_session),
):
    """Return clock-in/out events with location (one row per event, both directions)."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    params = {"since": since, "limit": limit}
    where_staff = ""
    if staff_id:
        where_staff = " AND a.staff_id = :sid"
        params["sid"] = staff_id

    sql = f"""
        WITH ev AS (
            SELECT a.id AS attendance_id,
                   a.staff_id,
                   'clock_in' AS event,
                   a.clock_in AS at,
                   a.clock_in_lat AS lat,
                   a.clock_in_lng AS lng,
                   a.clock_in_accuracy AS accuracy,
                   a.clock_in_address AS address
            FROM attendance a
            WHERE a.clock_in >= :since AND a.clock_in_lat IS NOT NULL{where_staff}
            UNION ALL
            SELECT a.id, a.staff_id, 'clock_out', a.clock_out,
                   a.clock_out_lat, a.clock_out_lng,
                   a.clock_out_accuracy, a.clock_out_address
            FROM attendance a
            WHERE a.clock_out >= :since AND a.clock_out_lat IS NOT NULL{where_staff}
        )
        SELECT ev.*, s.first_name, s.last_name, s.employee_id
        FROM ev
        LEFT JOIN staff s ON s.id = ev.staff_id
        ORDER BY ev.at DESC
        LIMIT :limit
    """
    rows = (await session.execute(text(sql), params)).mappings().all()
    return [
        {
            "attendance_id": str(r["attendance_id"]),
            "staff_id": str(r["staff_id"]) if r["staff_id"] else None,
            "staff_name": f"{r['first_name'] or ''} {r['last_name'] or ''}".strip() or None,
            "employee_id": r["employee_id"],
            "event": r["event"],
            "at": r["at"].isoformat() if r["at"] else None,
            "lat": float(r["lat"]) if r["lat"] is not None else None,
            "lng": float(r["lng"]) if r["lng"] is not None else None,
            "accuracy": float(r["accuracy"]) if r["accuracy"] is not None else None,
            "address": r["address"],
        }
        for r in rows
    ]


@router.get("/access")
async def list_access_geo(
    days: int = Query(30, ge=1, le=365),
    user_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None, description="Filter by action e.g. USER_LOGIN"),
    limit: int = Query(500, ge=1, le=5000),
    session: AsyncSession = Depends(get_session),
):
    """Return user access events (login/logout/etc.) with captured location."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    params = {"since": since, "limit": limit}
    where = ["al.created_at >= :since", "al.latitude IS NOT NULL"]
    if user_id:
        where.append("al.user_id = :uid")
        params["uid"] = user_id
    if action:
        where.append("al.action = :act")
        params["act"] = action
    sql = f"""
        SELECT al.id, al.user_id, al.action, al.module, al.details, al.created_at,
               al.latitude, al.longitude, al.accuracy, al.location_address,
               al.ip_address, u.full_name, u.email
        FROM audit_logs al
        LEFT JOIN users u ON u.id = al.user_id
        WHERE {' AND '.join(where)}
        ORDER BY al.created_at DESC
        LIMIT :limit
    """
    rows = (await session.execute(text(sql), params)).mappings().all()
    return [
        {
            "id": str(r["id"]),
            "user_id": str(r["user_id"]) if r["user_id"] else None,
            "user_name": r["full_name"],
            "user_email": r["email"],
            "action": r["action"],
            "module": r["module"],
            "details": r["details"],
            "at": r["created_at"].isoformat() if r["created_at"] else None,
            "lat": float(r["latitude"]) if r["latitude"] is not None else None,
            "lng": float(r["longitude"]) if r["longitude"] is not None else None,
            "accuracy": float(r["accuracy"]) if r["accuracy"] is not None else None,
            "address": r["location_address"],
            "ip_address": r["ip_address"],
        }
        for r in rows
    ]
