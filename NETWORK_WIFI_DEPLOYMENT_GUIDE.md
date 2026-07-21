# Network & WiFi Management — Deployment Guide

Centralized Wi-Fi authentication & captive portal for **BONNESANTE MEDICALS /
ASTRO-ASIX ERP**. Employees authenticate to company Wi-Fi using their **existing
company account credentials** — no separate credential store is introduced.

---

## 1. What was added

### Backend (FastAPI)
| File | Purpose |
|------|---------|
| `backend/app/models.py` | `WifiSettings`, `WifiSession`, `WifiDevice`, `WifiAuthLog` models |
| `backend/app/services/encryption.py` | AES (Fernet) encryption for stored secrets |
| `backend/app/services/radius_service.py` | `RadiusService` — authenticate / disconnect / authorize_device / get_active_sessions |
| `backend/app/api/wifi.py` | REST API + RBAC + rate limiting |
| `backend/alembic/versions/j9012345678i_add_wifi_management_tables.py` | DB migration |
| `backend/tests/test_wifi.py` | Integration tests |

### Frontend (React)
| File | Purpose |
|------|---------|
| `frontend/src/WifiLogin.js` | Captive portal page served at `/wifi-login` |
| `frontend/src/NetworkManagementDashboard.js` | Admin module (tabs + widgets) |
| `frontend/src/WifiSettings.js` | WiFi Settings page |
| `frontend/src/WifiSessions.js` | Active Sessions page |
| `frontend/src/WifiDevices.js` | Connected Devices page |
| `frontend/src/WifiLogs.js` | Authentication Logs page |
| `frontend/src/wifiApi.js` | Shared fetch helper |

`App.js` routes `/wifi-login` to the captive portal; `AppMain.js` exposes the
**NETWORK & WIFI** sidebar entry (Super Admin / `role === 'admin'` only).

---

## 2. API endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/wifi/authenticate` | public (rate-limited) | Captive-portal login against employee DB |
| POST | `/api/wifi/logout` | public | Close a Wi-Fi session |
| GET | `/api/wifi/settings` | Super Admin | Read settings (secrets masked) |
| PUT | `/api/wifi/settings` | Super Admin | Update settings (secrets encrypted) |
| POST | `/api/wifi/settings/reveal` | Super Admin | Reveal one decrypted secret |
| GET | `/api/wifi/sessions` | Super Admin | List sessions |
| GET | `/api/wifi/devices` | Employee/Admin | List devices (own / all) |
| GET | `/api/wifi/logs` | Super Admin | Authentication audit logs |
| GET | `/api/wifi/dashboard` | Super Admin | Widget aggregates |
| POST | `/api/wifi/register-device` | Employee/Admin | Register a device |
| DELETE | `/api/wifi/remove-device?device_id=` | Employee/Admin | Remove a device |
| POST | `/api/wifi/devices/{id}/block` | Super Admin | Block a device |
| POST | `/api/wifi/devices/{id}/unblock` | Super Admin | Unblock a device |

---

## 3. Install & migrate

```bash
cd backend
# new dependency
pip install -r requirements.txt          # adds cryptography

# generate a dedicated encryption key (PRODUCTION REQUIRED)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# put the value in backend/.env:
#   WIFI_ENCRYPTION_KEY=<generated-key>

# run the migration
alembic upgrade head
```

> If `WIFI_ENCRYPTION_KEY` is not set, a key is derived from `SECRET_KEY`
> (development fallback only). **Set a dedicated key in production** and never
> rotate it without re-encrypting existing secrets.

Build the frontend:

```bash
cd frontend
npm install
npm run build
```

---

## 4. FreeRADIUS integration (Ubuntu)

The captive-portal `POST /api/wifi/authenticate` endpoint is the single
integration point. Point FreeRADIUS at it with the `rlm_rest` module so RADIUS
authenticates against the same employee accounts.

```bash
sudo apt-get install freeradius freeradius-rest
```

`/etc/freeradius/3.0/mods-available/rest`:

```
rest {
    connect_uri = "https://your-domain.com"
    authorize {
        uri = "${..connect_uri}/api/wifi/authenticate"
        method = 'post'
        body = 'json'
        data = '{ "username": "%{User-Name}", "password": "%{User-Password}", "device_mac": "%{Calling-Station-Id}" }'
        tls = ${..tls}
    }
}
```

Enable it and reference `rest` in the `authorize`/`authenticate` sections of
`/etc/freeradius/3.0/sites-enabled/default`, then:

```bash
sudo ln -s ../mods-available/rest /etc/freeradius/3.0/mods-enabled/rest
sudo systemctl restart freeradius
```

**Account deactivation** automatically disables Wi-Fi: `RadiusService` rejects
any login where `is_active == False` or `is_locked == True`, and a `403` is
returned to RADIUS → access denied.

**Future LDAP / Active Directory**: replace `RadiusService._find_user` /
`verify_password` with an LDAP bind — callers (portal, RADIUS, RBAC) are
unchanged.

---

## 5. Nginx + captive portal redirect

Add the portal path to your existing server block (`nginx-astrobsm.conf`):

```nginx
# Captive portal SPA route — served by the React build
location = /wifi-login {
    try_files /index.html =404;
}

# API proxy (existing)
location /api/ {
    proxy_pass http://127.0.0.1:8008;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;   # used for rate-limit + logs
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Configure your gateway / RADIUS NAS to redirect unauthenticated clients to
`https://your-domain.com/wifi-login` (set this same value as
**Captive Portal URL** in WiFi Settings).

---

## 6. Workflow

```
Connect Wi-Fi → gateway redirects to /wifi-login
  → employee enters company email/phone + password
  → POST /api/wifi/authenticate → validated vs employees DB (existing hashes)
    ├─ success → Wi-Fi granted, session opened, (optional) Time-In recorded,
    │            JWT issued → redirect to dashboard
    └─ failure → access denied, error shown, attempt logged
```

---

## 7. Docker

No new services required. Rebuild the existing images so the new dependency and
frontend bundle are included:

```bash
docker compose build backend frontend
docker compose up -d
docker compose exec backend alembic upgrade head
```

Pass `WIFI_ENCRYPTION_KEY` through `docker-compose.yml` env / secrets.

---

## 8. Tests

```bash
cd backend
pytest tests/test_wifi.py -v      # requires a reachable PostgreSQL (DATABASE_URL)
```

Covers encryption round-trip, captive-portal auth (success / wrong password /
inactive account), RBAC on settings, secret reveal, device-limit enforcement,
and the session/dashboard lifecycle.

---

## 9. Security checklist

- ✅ Reuses existing employee auth & password hashing (no new auth system)
- ✅ AES-encrypted Wi-Fi / RADIUS secrets at rest; never returned in plaintext
- ✅ JWT + role-based access control (Super Admin gate on admin endpoints)
- ✅ Per-IP rate limiting on `/authenticate`
- ✅ Full audit logging (`wifi_auth_logs`)
- ✅ Parameterized SQLAlchemy queries (SQL-injection safe)
- ✅ React escapes output by default (XSS safe)
- ✅ Deactivated employees automatically lose Wi-Fi access
