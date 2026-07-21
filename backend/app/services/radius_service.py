"""RadiusService — centralized Wi-Fi authentication service layer.

Authenticates Wi-Fi clients against the EXISTING employee account store
(`users` table) reusing the existing password hashing scheme. This service is
the single integration point the captive portal and a FreeRADIUS `rlm_rest`
module talk to, and is designed so an LDAP / Active Directory backend can be
slotted in later without touching the callers.

Key behaviours
--------------
* Re-uses existing password hashes (no new credential store).
* Refuses access when an employee account is inactive or locked (i.e. Wi-Fi is
  automatically disabled when the employee account is deactivated).
* Enforces "maximum devices per employee" and device block-lists.
* Records every attempt to `wifi_auth_logs` for auditing.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    User,
    WifiSettings,
    WifiSession,
    WifiDevice,
    WifiAuthLog,
)

# Reuse the EXISTING authentication primitives — do not create a new system.
from app.api.auth import verify_password


class AuthResult:
    """Lightweight result object returned by authenticate_user()."""

    def __init__(
        self,
        success: bool,
        user: Optional[User] = None,
        reason: Optional[str] = None,
        session: Optional[WifiSession] = None,
    ):
        self.success = success
        self.user = user
        self.reason = reason
        self.session = session


class RadiusService:
    """Service layer wrapping Wi-Fi authentication & session management."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------------ utils
    async def _get_settings(self) -> Optional[WifiSettings]:
        result = await self.db.execute(select(WifiSettings).limit(1))
        return result.scalar_one_or_none()

    async def _find_user(self, username: str) -> Optional[User]:
        """Resolve an employee by email or phone (existing login identifiers)."""
        ident = (username or "").strip().lower()
        result = await self.db.execute(
            select(User).where(func.lower(User.email) == ident)
        )
        user = result.scalar_one_or_none()
        if user:
            return user
        # Fall back to phone-based lookup (existing system supports phone login).
        result = await self.db.execute(select(User).where(User.phone == username))
        return result.scalar_one_or_none()

    async def _log(
        self,
        *,
        employee_id: Optional[uuid.UUID],
        username: str,
        ip_address: Optional[str],
        device_mac: Optional[str],
        result: str,
        failure_reason: Optional[str] = None,
    ) -> None:
        self.db.add(
            WifiAuthLog(
                id=uuid.uuid4(),
                employee_id=employee_id,
                username=username,
                ip_address=ip_address,
                device_mac=device_mac,
                authentication_result=result,
                failure_reason=failure_reason,
            )
        )

    # ----------------------------------------------------------- authenticate
    async def authenticate_user(
        self,
        username: str,
        password: str,
        *,
        device_mac: Optional[str] = None,
        device_name: Optional[str] = None,
        ip_address: Optional[str] = None,
        remember_device: bool = False,
    ) -> AuthResult:
        """Validate credentials against the employee DB and open a Wi-Fi session."""
        user = await self._find_user(username)

        if not user:
            await self._log(
                employee_id=None,
                username=username,
                ip_address=ip_address,
                device_mac=device_mac,
                result="failure",
                failure_reason="Unknown account",
            )
            await self.db.commit()
            return AuthResult(False, reason="Invalid username or password")

        # Account state checks — deactivating an employee disables Wi-Fi access.
        if user.is_locked:
            await self._reject(user, username, ip_address, device_mac, "Account locked")
            return AuthResult(False, user=user, reason="Account is locked")
        if not user.is_active:
            await self._reject(user, username, ip_address, device_mac, "Account inactive")
            return AuthResult(False, user=user, reason="Account is inactive")

        # Reuse existing password hashes.
        if not verify_password(password, user.hashed_password):
            await self._reject(
                user, username, ip_address, device_mac, "Invalid password"
            )
            return AuthResult(False, user=user, reason="Invalid username or password")

        # Device authorization + per-employee device cap.
        allowed, reason = await self.authorize_device(
            user, device_mac, device_name, remember_device=remember_device
        )
        if not allowed:
            await self._reject(user, username, ip_address, device_mac, reason)
            return AuthResult(False, user=user, reason=reason)

        # Open a Wi-Fi session.
        session = WifiSession(
            id=uuid.uuid4(),
            employee_id=user.id,
            device_mac=device_mac,
            device_name=device_name,
            ip_address=ip_address,
            login_time=datetime.now(timezone.utc),
            session_status="active",
            data_used_mb=0,
        )
        self.db.add(session)

        await self._log(
            employee_id=user.id,
            username=username,
            ip_address=ip_address,
            device_mac=device_mac,
            result="success",
        )
        await self.db.commit()
        await self.db.refresh(session)
        return AuthResult(True, user=user, session=session)

    async def _reject(self, user, username, ip_address, device_mac, reason):
        await self._log(
            employee_id=user.id if user else None,
            username=username,
            ip_address=ip_address,
            device_mac=device_mac,
            result="failure",
            failure_reason=reason,
        )
        await self.db.commit()

    # ------------------------------------------------------- device authority
    async def authorize_device(
        self,
        user: User,
        device_mac: Optional[str],
        device_name: Optional[str] = None,
        *,
        remember_device: bool = False,
    ) -> tuple[bool, Optional[str]]:
        """Check device block-list + max-devices policy. Optionally register it."""
        if not device_mac:
            # MAC not provided by portal — allow (RADIUS may still enforce).
            return True, None

        # Existing device?
        result = await self.db.execute(
            select(WifiDevice).where(
                WifiDevice.employee_id == user.id,
                WifiDevice.device_mac == device_mac,
            )
        )
        device = result.scalar_one_or_none()

        if device and device.status == "blocked":
            return False, "This device has been blocked by an administrator"

        settings = await self._get_settings()
        max_devices = settings.max_devices if settings and settings.max_devices else 3

        if not device:
            # Count distinct registered (active) devices for this employee.
            count_result = await self.db.execute(
                select(func.count()).select_from(WifiDevice).where(
                    WifiDevice.employee_id == user.id,
                    WifiDevice.status == "active",
                )
            )
            current = count_result.scalar() or 0
            if current >= max_devices:
                return False, (
                    f"Device limit reached ({max_devices}). "
                    "Remove an existing device to continue."
                )
            if remember_device:
                device = WifiDevice(
                    id=uuid.uuid4(),
                    employee_id=user.id,
                    device_name=device_name,
                    device_mac=device_mac,
                    device_type="other",
                    last_connected=datetime.now(timezone.utc),
                    status="active",
                )
                self.db.add(device)
        else:
            device.last_connected = datetime.now(timezone.utc)
            if device_name and not device.device_name:
                device.device_name = device_name

        return True, None

    # ------------------------------------------------------------- disconnect
    async def disconnect_user(
        self,
        *,
        employee_id: Optional[uuid.UUID] = None,
        session_id: Optional[uuid.UUID] = None,
        device_mac: Optional[str] = None,
    ) -> int:
        """Close active Wi-Fi session(s). Returns the number of sessions closed."""
        query = select(WifiSession).where(WifiSession.session_status == "active")
        if session_id:
            query = query.where(WifiSession.id == session_id)
        if employee_id:
            query = query.where(WifiSession.employee_id == employee_id)
        if device_mac:
            query = query.where(WifiSession.device_mac == device_mac)

        result = await self.db.execute(query)
        sessions = result.scalars().all()
        now = datetime.now(timezone.utc)
        for s in sessions:
            s.session_status = "closed"
            s.logout_time = now
        await self.db.commit()
        return len(sessions)

    # ---------------------------------------------------------- active queries
    async def get_active_sessions(self) -> list[WifiSession]:
        """Return all currently active Wi-Fi sessions, expiring stale ones first."""
        await self._expire_stale_sessions()
        result = await self.db.execute(
            select(WifiSession)
            .where(WifiSession.session_status == "active")
            .order_by(WifiSession.login_time.desc())
        )
        return list(result.scalars().all())

    async def _expire_stale_sessions(self) -> None:
        """Mark sessions older than the configured timeout as expired."""
        settings = await self._get_settings()
        timeout_minutes = (
            settings.session_timeout if settings and settings.session_timeout else 60
        )
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)
        result = await self.db.execute(
            select(WifiSession).where(
                WifiSession.session_status == "active",
                WifiSession.login_time < cutoff,
            )
        )
        stale = result.scalars().all()
        if not stale:
            return
        now = datetime.now(timezone.utc)
        for s in stale:
            s.session_status = "expired"
            s.logout_time = now
        await self.db.commit()
