"""Integration tests for the Network & WiFi Management module.

Covers:
* AES encryption round-trip for stored secrets.
* Captive-portal authentication against existing employee accounts.
* Authentication failure paths (wrong password, inactive account).
* Settings update + secret reveal (Super Admin only) / RBAC enforcement.
* Device registration limits and removal.
* Session listing / logout and dashboard aggregation.
"""
import os
import sys
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Ensure we import the local app package
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import main as main_mod
from app import db as db_mod
from app import models as models_mod
from app.api import auth as auth_mod
from app.services import encryption as enc_mod

engine = db_mod.engine
Base = models_mod.Base
app = main_mod.app


@pytest_asyncio.fixture(scope="function", autouse=True)
async def setup_database():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


async def create_user(role="sales_staff", password="TestPass123!", **kwargs):
    """Create an employee account directly in the DB; returns (user_id, email)."""
    email = kwargs.pop("email", f"wifi_{uuid.uuid4().hex}@example.com")
    async with db_mod.AsyncSessionLocal() as session:
        user_id = uuid.uuid4()
        user = models_mod.User(
            id=user_id,
            email=email,
            hashed_password=auth_mod.hash_password(password),
            full_name=kwargs.pop("full_name", "Wifi Tester"),
            role=role,
            is_active=kwargs.pop("is_active", True),
            is_locked=kwargs.pop("is_locked", False),
            phone=kwargs.pop("phone", None),
        )
        session.add(user)
        await session.commit()
    return user_id, email


def admin_headers(user_id, email):
    token = auth_mod.create_access_token(
        data={"sub": str(user_id), "email": email, "role": "admin"}
    )
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# Encryption
# --------------------------------------------------------------------------- #
def test_encryption_round_trip():
    secret = "SuperSecretWifiPass!"
    enc = enc_mod.encrypt_secret(secret)
    assert enc is not None
    assert enc != secret  # stored value must not be plaintext
    assert enc_mod.decrypt_secret(enc) == secret
    assert enc_mod.encrypt_secret("") is None
    assert enc_mod.decrypt_secret(None) is None


# --------------------------------------------------------------------------- #
# Captive portal authentication
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_authenticate_success():
    uid, email = await create_user(password="GoodPass1!")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/wifi/authenticate",
            json={"username": email, "password": "GoodPass1!",
                  "device_mac": "AA:BB:CC:DD:EE:01", "remember_device": True},
        )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["success"] is True
    assert data["access_token"]
    assert data["session_id"]


@pytest.mark.asyncio
async def test_authenticate_wrong_password():
    uid, email = await create_user(password="GoodPass1!")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/wifi/authenticate",
            json={"username": email, "password": "WrongPass"},
        )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_authenticate_inactive_account_denied():
    uid, email = await create_user(password="GoodPass1!", is_active=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/wifi/authenticate",
            json={"username": email, "password": "GoodPass1!"},
        )
    assert res.status_code == 401


# --------------------------------------------------------------------------- #
# Settings + RBAC
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_settings_requires_super_admin():
    uid, email = await create_user(role="sales_staff")
    token = auth_mod.create_access_token(
        data={"sub": str(uid), "email": email, "role": "sales_staff"}
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get(
            "/api/wifi/settings", headers={"Authorization": f"Bearer {token}"}
        )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_update_and_reveal_settings():
    uid, email = await create_user(role="admin")
    headers = admin_headers(uid, email)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.put(
            "/api/wifi/settings",
            headers=headers,
            json={
                "company_ssid": "BONNESANTE-CORP",
                "company_password": "Corp@Wifi2026",
                "current_wifi_password": "Mgmt@Pass2026",
                "session_timeout": 120,
                "max_devices": 2,
            },
        )
        assert res.status_code == 200, res.text
        public = res.json()["settings"]
        # Secret must never be returned in the public payload
        assert "company_password" not in public
        assert public["company_password_set"] is True
        assert public["current_wifi_password_set"] is True

        # Super Admin can explicitly reveal a secret
        reveal = await client.post(
            "/api/wifi/settings/reveal",
            headers=headers,
            json={"field": "current_wifi_password"},
        )
        assert reveal.status_code == 200
        assert reveal.json()["value"] == "Mgmt@Pass2026"


# --------------------------------------------------------------------------- #
# Devices
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_device_registration_limit_and_removal():
    # Admin sets max_devices = 1
    auid, aemail = await create_user(role="admin")
    aheaders = admin_headers(auid, aemail)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.put("/api/wifi/settings", headers=aheaders,
                         json={"max_devices": 1})

    # Employee registers a device, second should be rejected
    uid, email = await create_user(role="sales_staff")
    token = auth_mod.create_access_token(
        data={"sub": str(uid), "email": email, "role": "sales_staff"}
    )
    eheaders = {"Authorization": f"Bearer {token}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post(
            "/api/wifi/register-device", headers=eheaders,
            json={"device_name": "Phone", "device_mac": "AA:BB:CC:00:00:01"},
        )
        assert r1.status_code == 200, r1.text
        device_id = r1.json()["device_id"]

        r2 = await client.post(
            "/api/wifi/register-device", headers=eheaders,
            json={"device_name": "Laptop", "device_mac": "AA:BB:CC:00:00:02"},
        )
        assert r2.status_code == 400  # limit reached

        # Remove first device, then second registration succeeds
        rd = await client.request(
            "DELETE", f"/api/wifi/remove-device?device_id={device_id}",
            headers=eheaders,
        )
        assert rd.status_code == 200
        r3 = await client.post(
            "/api/wifi/register-device", headers=eheaders,
            json={"device_name": "Laptop", "device_mac": "AA:BB:CC:00:00:02"},
        )
        assert r3.status_code == 200


# --------------------------------------------------------------------------- #
# Sessions / logout / dashboard
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_session_lifecycle_and_dashboard():
    auid, aemail = await create_user(role="admin")
    aheaders = admin_headers(auid, aemail)
    uid, email = await create_user(password="GoodPass1!")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        auth_res = await client.post(
            "/api/wifi/authenticate",
            json={"username": email, "password": "GoodPass1!",
                  "device_mac": "AA:BB:CC:DD:EE:09"},
        )
        assert auth_res.status_code == 200
        session_id = auth_res.json()["session_id"]

        sessions = await client.get("/api/wifi/sessions", headers=aheaders)
        assert sessions.status_code == 200
        assert any(s["id"] == session_id for s in sessions.json())

        dash = await client.get("/api/wifi/dashboard", headers=aheaders)
        assert dash.status_code == 200
        assert dash.json()["active_sessions"] >= 1
        assert dash.json()["todays_logins"] >= 1

        logout = await client.post(
            "/api/wifi/logout", json={"session_id": session_id}
        )
        assert logout.status_code == 200
        assert logout.json()["sessions_closed"] == 1
