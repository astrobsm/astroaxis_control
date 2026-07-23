from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
import os

# Create AstroBSM StockMaster app
app = FastAPI(
    title='AstroBSM StockMaster', 
    description='Enterprise Resource Planning system for AstroBSM - Bonnesante Medicals',
    version='1.0.0'
)

# Add CORS middleware
# CORS. This app authenticates with Bearer tokens held in localStorage, not
# cookies, so allow_credentials is not required -- and combining it with a "*"
# origin makes Starlette echo back any caller's Origin, which would let any
# site read authenticated responses. Set ALLOWED_ORIGINS (comma-separated) to
# lock this down further; the default stays wildcard-but-uncredentialed.
_origins_env = os.getenv("ALLOWED_ORIGINS", "").strip()
_allowed_origins = [o.strip() for o in _origins_env.split(",") if o.strip()] or ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=_allowed_origins != ["*"],
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["*"],
)

# Frontend serving - MUST be before API routers to avoid auth conflicts
# Calculate path: __file__ is /app/app/main.py, so parent.parent is /app
frontend_build_path = Path(__file__).parent.parent / "frontend" / "build"
print(f"🔍 AstroBSM StockMaster Frontend path: {frontend_build_path}")


def _safe_build_path(root: Path, user_path: str):
    """Resolve user_path under root, refusing anything that escapes it.

    Starlette hands us the URL-decoded path, so percent-encoded traversal
    (%2e%2e) arrives here as literal '..' segments. Joining that with / and
    passing it to FileResponse would serve any file the process can read --
    including backend/.env. Resolve first, then require the result to still
    be inside root. Returns None if it is not.
    """
    try:
        candidate = (root / user_path).resolve()
    except (OSError, ValueError):
        return None
    root_resolved = root.resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        return None
    return candidate if candidate.exists() else None

if frontend_build_path.exists():
    # Mount static files FIRST with HTMLResponse to prevent auth blocking
    from fastapi.responses import HTMLResponse, Response
    
    @app.get("/static/{full_path:path}")
    async def serve_static(full_path: str):
        """Serve static files without authentication"""
        static_file = _safe_build_path(frontend_build_path / "static", full_path)
        if static_file is not None and static_file.is_file():
            # Determine content type
            content_type = "application/octet-stream"
            if full_path.endswith('.js'):
                content_type = "application/javascript"
            elif full_path.endswith('.css'):
                content_type = "text/css"
            elif full_path.endswith('.map'):
                content_type = "application/json"
            elif full_path.endswith('.png'):
                content_type = "image/png"
            elif full_path.endswith('.jpg') or full_path.endswith('.jpeg'):
                content_type = "image/jpeg"
            elif full_path.endswith('.svg'):
                content_type = "image/svg+xml"
            
            with open(static_file, 'rb') as f:
                return Response(content=f.read(), media_type=content_type)
        return {"error": "File not found"}
    
    print("✅ Static files route configured at /static")
else:
    print("⚠️ Frontend build not found, skipping static file serving")

# Health check
@app.get('/api/health')
async def health():
    return {'status': 'ok', 'message': 'AstroBSM StockMaster is running clean', 'version': '1.0.0'}

# Import and include API routers (no COM/Oracle dependencies)
try:
    from app.api import staff, attendance, products, raw_materials, stock, warehouses, production, sales, stock_management, bom, settings, auth, permissions, financial, bulk_upload, notifications, production_consumables, machines_equipment, production_completions, marketing, hr_customercare, payment_tracking, procurement, logistics, warehouse_transfers, returns, damaged_transfers, receive_transfers, legacy_debts, communication, sop, public_orders, production_tasks, profits, announcements, radio, geo, regulatory, wifi, accounting, payroll, assets, budgeting, tax, maintenance, dashboard, costs
    
    from fastapi import Depends
    from app.api.auth import require_authenticated_user, require_admin

    # Every authenticated user must present a valid bearer token; the
    # dependency re-loads the User row, so locked/deactivated/demoted accounts
    # lose access immediately rather than at token expiry.
    authed = [Depends(require_authenticated_user)]
    admin_only = [Depends(require_admin)]

    # --- Public by design -------------------------------------------------
    # auth:          login / register / password reset
    # attendance:    /quick-attendance is the pre-login clock-in terminal
    # public_orders: customer-facing ordering page (its /admin/* routes are
    #                guarded individually inside the module)
    # wifi:          captive-portal login; already enforces its own HTTPBearer
    # profits:       already enforces its own _admin_only() on every route
    app.include_router(auth.router)
    app.include_router(attendance.router)
    app.include_router(public_orders.router)
    app.include_router(wifi.router)
    app.include_router(profits.router)

    # --- Admin only -------------------------------------------------------
    app.include_router(permissions.router, dependencies=admin_only)
    app.include_router(financial.router, dependencies=admin_only)

    # --- Authenticated ----------------------------------------------------
    for _router in (
        staff, products, raw_materials, stock, warehouses, production, sales,
        stock_management, bom, settings, bulk_upload, notifications,
        production_consumables, machines_equipment, production_completions,
        marketing, hr_customercare, payment_tracking, procurement, logistics,
        warehouse_transfers, returns, damaged_transfers, receive_transfers,
        legacy_debts, communication, sop, production_tasks, announcements,
        radio, geo, regulatory, accounting, payroll, assets,
        budgeting, tax, maintenance, dashboard, costs,
    ):
        app.include_router(_router.router, dependencies=authed)
    app.include_router(assets.cash_router, dependencies=authed)

    # Verify every imported API module actually contributed routes.
    #
    # A module can be imported at the top of this block and then simply not
    # appear in any include_router call -- which is silent: the app boots,
    # health checks pass, and that module's endpoints just do not exist.
    # This happened when `budgeting` was added to the import list but not to
    # the tuple above. Compare what was imported against what registered.
    import sys as _sys
    _registered_prefixes = {
        r.path for r in app.routes if getattr(r, "path", "").startswith("/api")
    }
    _missing = []
    for _name, _mod in list(vars().items()):
        if not (hasattr(_mod, "__name__")
                and str(getattr(_mod, "__name__", "")).startswith("app.api.")):
            continue
        _r = getattr(_mod, "router", None)
        _prefix = getattr(_r, "prefix", None) if _r is not None else None
        if not _prefix:
            continue
        if not any(p.startswith(_prefix) for p in _registered_prefixes):
            _missing.append(f"{_name} (prefix {_prefix})")
    if _missing:
        raise RuntimeError(
            "These API modules were imported but registered no routes, so "
            "their endpoints would silently 404: " + ", ".join(sorted(_missing))
        )

    print(f"All routers loaded — {len(_registered_prefixes)} API routes registered")
except Exception as e:
    # Previously this only printed. Because the import on the line above and
    # every include_router() call share one try block, a single bad import
    # produced a running app with ZERO API routes -- /api/health still returned
    # ok, every readiness probe passed, and the whole ERP 404'd. Fail loudly so
    # a broken deploy cannot go green.
    import traceback
    traceback.print_exc()
    raise RuntimeError(f"API router registration failed, refusing to start: {e}") from e


# Bootstrap geo columns (idempotent)
@app.on_event("startup")
async def _bootstrap_geo_schema():
    try:
        from app.db import AsyncSessionLocal
        from app.api.geo import bootstrap_geo_schema
        async with AsyncSessionLocal() as s:
            await bootstrap_geo_schema(s)
        print("✅ Geo schema bootstrap complete")
    except Exception as e:
        print(f"⚠️ Geo schema bootstrap failed: {e}")


# Bootstrap regulatory compliance tables (idempotent)
@app.on_event("startup")
async def _bootstrap_regulatory_schema():
    try:
        from app.db import AsyncSessionLocal
        from app.api.regulatory import bootstrap_regulatory_schema
        async with AsyncSessionLocal() as s:
            await bootstrap_regulatory_schema(s)
        print("✅ Regulatory Compliance schema bootstrap complete")
    except Exception as e:
        print(f"⚠️ Regulatory Compliance schema bootstrap failed: {e}")


# Background scheduler: auto clock-out at 17:10 Africa/Lagos
@app.on_event("startup")
async def _start_auto_clockout_scheduler():
    try:
        import asyncio
        from app.api.attendance import auto_clockout_scheduler
        app.state._auto_clockout_task = asyncio.create_task(auto_clockout_scheduler())
        print("✅ Auto clock-out scheduler started (17:10 Africa/Lagos)")
    except Exception as e:
        print(f"❌ Failed to start auto clock-out scheduler: {e}")


@app.on_event("shutdown")
async def _stop_auto_clockout_scheduler():
    task = getattr(app.state, "_auto_clockout_task", None)
    if task:
        task.cancel()


# Frontend HTML routes (after API routers but before catch-all)
if frontend_build_path.exists():
    @app.get("/")
    async def serve_frontend():
        index_path = frontend_build_path / "index.html"
        if index_path.exists():
            return FileResponse(
                str(index_path), 
                media_type="text/html",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0"
                }
            )
        return {"error": "Frontend not found"}
    
    @app.get("/manifest.json")
    async def manifest():
        manifest_path = frontend_build_path / "manifest.json"
        if manifest_path.exists():
            return FileResponse(str(manifest_path), media_type="application/json")
        return {"error": "Manifest not found"}
    
    @app.get("/serviceWorker.js")
    async def service_worker():
        sw_path = frontend_build_path / "serviceWorker.js"
        if sw_path.exists():
            return FileResponse(
                str(sw_path), 
                media_type="application/javascript",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Service-Worker-Allowed": "/"
                }
            )
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse("// Service Worker not found", media_type="application/javascript", status_code=404)
    
    @app.get("/{filename}.png")
    async def serve_images(filename: str):
        """Serve PNG images (logos, icons) from build folder"""
        # Deny API paths
        if filename.startswith("api") or "/" in filename:
            return {"error": "Not found"}
        
        image_path = _safe_build_path(frontend_build_path, f"{filename}.png")
        if image_path is not None:
            return FileResponse(str(image_path), media_type="image/png")
        return {"error": "Image not found"}
    
    @app.get("/{filename}.ico")
    async def serve_favicon(filename: str):
        """Serve favicon"""
        favicon_path = _safe_build_path(frontend_build_path, f"{filename}.ico")
        if favicon_path is not None:
            return FileResponse(str(favicon_path), media_type="image/x-icon")
        return {"error": "Favicon not found"}
    
    @app.get("/{full_path:path}")
    async def serve_react_app(full_path: str):
        # Don't handle API routes here - let them pass to API routers
        if full_path.startswith("api/") or full_path.startswith("docs") or full_path.startswith("openapi"):
            raise HTTPException(status_code=404, detail="Not Found")
        
        # Serve static assets from build directory
        static_path = _safe_build_path(frontend_build_path, full_path)
        if static_path is not None and static_path.is_file():
            return FileResponse(str(static_path))
        
        index_path = frontend_build_path / "index.html"
        if index_path.exists():
            return FileResponse(
                str(index_path), 
                media_type="text/html",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0"
                }
            )
        return {"error": "Frontend not found"}
else:
    @app.get("/")
    async def no_frontend():
        return {
            "message": "AstroBSM StockMaster Backend",
            "version": "1.0.0",
            "status": "Frontend build not found"
        }

print("🚀 AstroBSM StockMaster initialized successfully - no legacy imports")