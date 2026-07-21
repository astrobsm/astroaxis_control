"""Network & WiFi Management API.

Centralized Wi-Fi authentication / captive portal endpoints plus the admin
management surface (settings, sessions, devices, logs, dashboard). Wi-Fi access
is tied to the EXISTING employee accounts via :class:`RadiusService`.

Security:
* Admin endpoints require a valid JWT belonging to a Super Admin (role=admin).
* Wi-Fi password / RADIUS secret are stored AES-encrypted and never returned in
  plaintext (a boolean "*_set" flag is returned instead). Super Admins may
  explicitly reveal a single secret through /settings/reveal.
* The public /authenticate endpoint is rate-limited per client IP.
"""
from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone, date
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import (
    User,
    WifiSettings,
    WifiSession,
    WifiDevice,
    WifiAuthLog,
    Staff,
    Attendance,
)
from app.api.auth import decode_token, create_access_token
from app.services.encryption import encrypt_secret, decrypt_secret, has_secret
from app.services.radius_service import RadiusService

router = APIRouter(prefix="/api/wifi", tags=["network-wifi"])

# ---------------------------------------------------------------------------
# Auth dependencies (reuse existing JWT scheme)
# ---------------------------------------------------------------------------
_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db: AsyncSession = Depends(get_session),
) -> User:
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Authentication required")
    payload = decode_token(credentials.credentials)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


async def require_super_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(
            status_code=403, detail="Super Admin access required"
        )
    return user


# ---------------------------------------------------------------------------
# Simple in-memory per-IP rate limiter (best effort across a single worker)
# ---------------------------------------------------------------------------
_RATE_WINDOW_SECONDS = 60
_RATE_MAX_HITS = 10
_rate_buckets: dict[str, deque] = defaultdict(deque)


def _rate_limit(ip: str) -> None:
    now = time.monotonic()
    bucket = _rate_buckets[ip]
    while bucket and now - bucket[0] > _RATE_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= _RATE_MAX_HITS:
        raise HTTPException(
            status_code=429,
            detail="Too many authentication attempts. Please wait and try again.",
        )
    bucket.append(now)


def _client_ip(request: Request) -> Optional[str]:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request and request.client else None


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------
class WifiAuthRequest(BaseModel):
    username: str
    password: str
    device_mac: Optional[str] = None
    device_name: Optional[str] = None
    remember_device: bool = False


class WifiLogoutRequest(BaseModel):
    session_id: Optional[uuid.UUID] = None
    employee_id: Optional[uuid.UUID] = None
    device_mac: Optional[str] = None


class WifiSettingsIn(BaseModel):
    company_ssid: Optional[str] = None
    company_password: Optional[str] = None          # plaintext in; encrypted at rest
    guest_ssid: Optional[str] = None
    guest_password: Optional[str] = None
    current_wifi_password: Optional[str] = None      # BONNESANTE "Current WiFi Password"
    radius_server_ip: Optional[str] = None
    radius_secret: Optional[str] = None
    captive_portal_url: Optional[str] = None
    session_timeout: Optional[int] = Field(None, ge=1, le=1440)
    max_devices: Optional[int] = Field(None, ge=1, le=50)
    bandwidth_limit: Optional[int] = Field(None, ge=0, le=10000)
    guest_network_enabled: Optional[bool] = None
    attendance_on_login: Optional[bool] = None


class RegisterDeviceIn(BaseModel):
    employee_id: Optional[uuid.UUID] = None
    device_name: str
    device_mac: str
    device_type: Optional[str] = "other"


class RevealSecretIn(BaseModel):
    field: str  # company_password | guest_password | current_wifi_password | radius_secret


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _get_or_create_settings(db: AsyncSession) -> WifiSettings:
    result = await db.execute(select(WifiSettings).limit(1))
    settings = result.scalar_one_or_none()
    if not settings:
        settings = WifiSettings(id=uuid.uuid4())
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings


def _settings_public(s: WifiSettings) -> dict:
    """Serialize settings WITHOUT exposing any decrypted secrets."""
    return {
        "id": str(s.id),
        "company_ssid": s.company_ssid,
        "company_password_set": has_secret(s.encrypted_company_password),
        "guest_ssid": s.guest_ssid,
        "guest_password_set": has_secret(s.encrypted_guest_password),
        "current_wifi_password_set": has_secret(s.encrypted_current_wifi_password),
        "radius_server_ip": s.radius_server_ip,
        "radius_secret_set": has_secret(s.encrypted_radius_secret),
        "captive_portal_url": s.captive_portal_url,
        "session_timeout": s.session_timeout,
        "max_devices": s.max_devices,
        "bandwidth_limit": s.bandwidth_limit,
        "guest_network_enabled": s.guest_network_enabled,
        "attendance_on_login": s.attendance_on_login,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


async def _maybe_register_attendance(db: AsyncSession, user: User) -> bool:
    """Best-effort Time-In when attendance_on_login is enabled.

    Wi-Fi auth is keyed on the `users` table while attendance uses `staff`. We
    map the two by phone number, then by full name. Duplicate Time-In entries
    for the same day are prevented.
    """
    settings = await _get_or_create_settings(db)
    if not settings.attendance_on_login:
        return False

    staff = None
    if user.phone:
        res = await db.execute(select(Staff).where(Staff.phone == user.phone))
        staff = res.scalar_one_or_none()
    if not staff and user.full_name:
        parts = user.full_name.strip().split()
        if len(parts) >= 2:
            res = await db.execute(
                select(Staff).where(
                    func.lower(Staff.first_name) == parts[0].lower(),
                    func.lower(Staff.last_name) == parts[-1].lower(),
                )
            )
            staff = res.scalar_one_or_none()
    if not staff:
        return False

    # Prevent duplicate Time-In for today.
    today = datetime.now(timezone.utc).date()
    res = await db.execute(
        select(Attendance).where(
            Attendance.staff_id == staff.id,
            func.date(Attendance.clock_in) == today,
        )
    )
    if res.scalars().first():
        return False

    db.add(
        Attendance(
            id=uuid.uuid4(),
            staff_id=staff.id,
            clock_in=datetime.now(timezone.utc),
            status="open",
            notes="Auto Time-In via Wi-Fi captive portal",
        )
    )
    await db.commit()
    return True


# ---------------------------------------------------------------------------
# Public captive-portal endpoints
# ---------------------------------------------------------------------------
@router.post("/authenticate")
async def authenticate(
    body: WifiAuthRequest, request: Request, db: AsyncSession = Depends(get_session)
):
    """Authenticate a captive-portal client against existing employee accounts."""
    ip = _client_ip(request)
    _rate_limit(ip or "unknown")

    service = RadiusService(db)
    result = await service.authenticate_user(
        body.username,
        body.password,
        device_mac=body.device_mac,
        device_name=body.device_name,
        ip_address=ip,
        remember_device=body.remember_device,
    )

    if not result.success:
        raise HTTPException(status_code=401, detail=result.reason or "Authentication failed")

    attendance_registered = False
    try:
        attendance_registered = await _maybe_register_attendance(db, result.user)
    except Exception:
        await db.rollback()

    # Issue an app access token so the portal can redirect into the dashboard.
    access_token = create_access_token(
        data={"sub": str(result.user.id), "email": result.user.email, "role": result.user.role}
    )

    return {
        "success": True,
        "message": "Wi-Fi access granted",
        "access_token": access_token,
        "token_type": "bearer",
        "session_id": str(result.session.id) if result.session else None,
        "attendance_registered": attendance_registered,
        "user": {
            "id": str(result.user.id),
            "email": result.user.email,
            "full_name": result.user.full_name,
            "role": result.user.role,
        },
    }


@router.post("/logout")
async def wifi_logout(body: WifiLogoutRequest, db: AsyncSession = Depends(get_session)):
    """Close an active Wi-Fi session (from portal or admin)."""
    if not any([body.session_id, body.employee_id, body.device_mac]):
        raise HTTPException(status_code=400, detail="A session_id, employee_id or device_mac is required")
    service = RadiusService(db)
    closed = await service.disconnect_user(
        session_id=body.session_id,
        employee_id=body.employee_id,
        device_mac=body.device_mac,
    )
    return {"success": True, "sessions_closed": closed}


# ---------------------------------------------------------------------------
# Admin: settings (Super Admin only)
# ---------------------------------------------------------------------------
@router.get("/settings")
async def get_settings(
    db: AsyncSession = Depends(get_session), _: User = Depends(require_super_admin)
):
    settings = await _get_or_create_settings(db)
    return _settings_public(settings)


@router.put("/settings")
async def update_settings(
    body: WifiSettingsIn,
    db: AsyncSession = Depends(get_session),
    admin: User = Depends(require_super_admin),
):
    settings = await _get_or_create_settings(db)

    # Plain fields
    for field in (
        "company_ssid",
        "guest_ssid",
        "radius_server_ip",
        "captive_portal_url",
        "session_timeout",
        "max_devices",
        "bandwidth_limit",
        "guest_network_enabled",
        "attendance_on_login",
    ):
        value = getattr(body, field)
        if value is not None:
            setattr(settings, field, value)

    # Encrypted secrets — only overwrite when a (non-empty) value is provided.
    if body.company_password:
        settings.encrypted_company_password = encrypt_secret(body.company_password)
    if body.guest_password:
        settings.encrypted_guest_password = encrypt_secret(body.guest_password)
    if body.current_wifi_password:
        settings.encrypted_current_wifi_password = encrypt_secret(body.current_wifi_password)
    if body.radius_secret:
        settings.encrypted_radius_secret = encrypt_secret(body.radius_secret)

    settings.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(settings)
    return {"success": True, "settings": _settings_public(settings)}


@router.post("/settings/reveal")
async def reveal_secret(
    body: RevealSecretIn,
    db: AsyncSession = Depends(get_session),
    admin: User = Depends(require_super_admin),
):
    """Reveal a single decrypted secret to a Super Admin (audited by JWT identity)."""
    settings = await _get_or_create_settings(db)
    field_map = {
        "company_password": settings.encrypted_company_password,
        "guest_password": settings.encrypted_guest_password,
        "current_wifi_password": settings.encrypted_current_wifi_password,
        "radius_secret": settings.encrypted_radius_secret,
    }
    if body.field not in field_map:
        raise HTTPException(status_code=400, detail="Unknown secret field")
    return {"field": body.field, "value": decrypt_secret(field_map[body.field]) or ""}


# ---------------------------------------------------------------------------
# Admin: sessions / logs / dashboard
# ---------------------------------------------------------------------------
@router.get("/sessions")
async def list_sessions(
    status_filter: Optional[str] = None,
    db: AsyncSession = Depends(get_session),
    _: User = Depends(require_super_admin),
):
    service = RadiusService(db)
    await service._expire_stale_sessions()
    query = select(WifiSession, User).join(User, WifiSession.employee_id == User.id)
    if status_filter:
        query = query.where(WifiSession.session_status == status_filter)
    query = query.order_by(WifiSession.login_time.desc()).limit(500)
    result = await db.execute(query)
    rows = result.all()
    return [
        {
            "id": str(s.id),
            "employee_id": str(s.employee_id),
            "employee_name": u.full_name,
            "device_mac": s.device_mac,
            "device_name": s.device_name,
            "ip_address": s.ip_address,
            "login_time": s.login_time.isoformat() if s.login_time else None,
            "logout_time": s.logout_time.isoformat() if s.logout_time else None,
            "session_status": s.session_status,
            "data_used_mb": float(s.data_used_mb or 0),
        }
        for s, u in rows
    ]


@router.get("/logs")
async def list_logs(
    result_filter: Optional[str] = None,
    limit: int = 200,
    db: AsyncSession = Depends(get_session),
    _: User = Depends(require_super_admin),
):
    query = select(WifiAuthLog).order_by(WifiAuthLog.timestamp.desc())
    if result_filter:
        query = query.where(WifiAuthLog.authentication_result == result_filter)
    query = query.limit(min(max(limit, 1), 1000))
    result = await db.execute(query)
    logs = result.scalars().all()
    return [
        {
            "id": str(l.id),
            "employee_id": str(l.employee_id) if l.employee_id else None,
            "username": l.username,
            "ip_address": l.ip_address,
            "device_mac": l.device_mac,
            "authentication_result": l.authentication_result,
            "failure_reason": l.failure_reason,
            "timestamp": l.timestamp.isoformat() if l.timestamp else None,
        }
        for l in logs
    ]


@router.get("/dashboard")
async def dashboard(
    db: AsyncSession = Depends(get_session), _: User = Depends(require_super_admin)
):
    """Aggregate widgets for the Network & WiFi Management dashboard."""
    service = RadiusService(db)
    await service._expire_stale_sessions()
    today = datetime.now(timezone.utc).date()

    active_sessions = (
        await db.execute(
            select(func.count()).select_from(WifiSession).where(
                WifiSession.session_status == "active"
            )
        )
    ).scalar() or 0

    current_users = (
        await db.execute(
            select(func.count(func.distinct(WifiSession.employee_id))).where(
                WifiSession.session_status == "active"
            )
        )
    ).scalar() or 0

    failed_today = (
        await db.execute(
            select(func.count()).select_from(WifiAuthLog).where(
                WifiAuthLog.authentication_result == "failure",
                func.date(WifiAuthLog.timestamp) == today,
            )
        )
    ).scalar() or 0

    logins_today = (
        await db.execute(
            select(func.count()).select_from(WifiAuthLog).where(
                WifiAuthLog.authentication_result == "success",
                func.date(WifiAuthLog.timestamp) == today,
            )
        )
    ).scalar() or 0

    connected_devices = (
        await db.execute(
            select(func.count()).select_from(WifiDevice).where(
                WifiDevice.status == "active"
            )
        )
    ).scalar() or 0

    bandwidth_used = (
        await db.execute(
            select(func.coalesce(func.sum(WifiSession.data_used_mb), 0)).where(
                func.date(WifiSession.login_time) == today
            )
        )
    ).scalar() or 0

    return {
        "current_wifi_users": current_users,
        "failed_login_attempts": failed_today,
        "connected_devices": connected_devices,
        "bandwidth_usage_mb": float(bandwidth_used),
        "todays_logins": logins_today,
        "active_sessions": active_sessions,
    }


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------
@router.get("/devices")
async def list_devices(
    employee_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """List devices. Super Admins see all; employees see only their own."""
    query = select(WifiDevice, User).join(User, WifiDevice.employee_id == User.id)
    if user.role != "admin":
        query = query.where(WifiDevice.employee_id == user.id)
    elif employee_id:
        query = query.where(WifiDevice.employee_id == employee_id)
    query = query.order_by(WifiDevice.created_at.desc())
    result = await db.execute(query)
    rows = result.all()
    return [
        {
            "id": str(d.id),
            "employee_id": str(d.employee_id),
            "employee_name": u.full_name,
            "device_name": d.device_name,
            "device_mac": d.device_mac,
            "device_type": d.device_type,
            "last_connected": d.last_connected.isoformat() if d.last_connected else None,
            "status": d.status,
        }
        for d, u in rows
    ]


@router.post("/register-device")
async def register_device(
    body: RegisterDeviceIn,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Register a device for the current employee (or any employee for admins)."""
    target_id = user.id
    if body.employee_id and user.role == "admin":
        target_id = body.employee_id

    # Enforce max-devices policy.
    settings = await _get_or_create_settings(db)
    max_devices = settings.max_devices or 3
    count = (
        await db.execute(
            select(func.count()).select_from(WifiDevice).where(
                WifiDevice.employee_id == target_id,
                WifiDevice.status == "active",
            )
        )
    ).scalar() or 0

    existing = (
        await db.execute(
            select(WifiDevice).where(
                WifiDevice.employee_id == target_id,
                WifiDevice.device_mac == body.device_mac,
            )
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Device already registered")
    if count >= max_devices:
        raise HTTPException(
            status_code=400,
            detail=f"Device limit reached ({max_devices}). Remove a device first.",
        )

    device = WifiDevice(
        id=uuid.uuid4(),
        employee_id=target_id,
        device_name=body.device_name,
        device_mac=body.device_mac,
        device_type=body.device_type or "other",
        last_connected=None,
        status="active",
    )
    db.add(device)
    await db.commit()
    await db.refresh(device)
    return {"success": True, "device_id": str(device.id)}


@router.delete("/remove-device")
async def remove_device(
    device_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    result = await db.execute(select(WifiDevice).where(WifiDevice.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    if user.role != "admin" and device.employee_id != user.id:
        raise HTTPException(status_code=403, detail="Not authorized to remove this device")
    await db.execute(delete(WifiDevice).where(WifiDevice.id == device_id))
    await db.commit()
    return {"success": True}


@router.post("/devices/{device_id}/block")
async def block_device(
    device_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
    admin: User = Depends(require_super_admin),
):
    result = await db.execute(select(WifiDevice).where(WifiDevice.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    device.status = "blocked"
    # Disconnect any active sessions for this device.
    await RadiusService(db).disconnect_user(device_mac=device.device_mac)
    await db.commit()
    return {"success": True, "status": "blocked"}


@router.post("/devices/{device_id}/unblock")
async def unblock_device(
    device_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
    admin: User = Depends(require_super_admin),
):
    result = await db.execute(select(WifiDevice).where(WifiDevice.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    device.status = "active"
    await db.commit()
    return {"success": True, "status": "active"}
