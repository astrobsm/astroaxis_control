from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

# Create AstroBSM StockMaster app
app = FastAPI(
    title='AstroBSM StockMaster', 
    description='Enterprise Resource Planning system for AstroBSM - Bonnesante Medicals',
    version='1.0.0'
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Frontend serving - MUST be before API routers to avoid auth conflicts
# Calculate path: __file__ is /app/app/main.py, so parent.parent is /app
frontend_build_path = Path(__file__).parent.parent / "frontend" / "build"
print(f"🔍 AstroBSM StockMaster Frontend path: {frontend_build_path}")

if frontend_build_path.exists():
    # Mount static files FIRST with HTMLResponse to prevent auth blocking
    from fastapi.responses import HTMLResponse, Response
    
    @app.get("/static/{full_path:path}")
    async def serve_static(full_path: str):
        """Serve static files without authentication"""
        static_file = frontend_build_path / "static" / full_path
        if static_file.exists() and static_file.is_file():
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
    from app.api import staff, attendance, products, raw_materials, stock, warehouses, production, sales, stock_management, bom, settings, auth, permissions, financial, bulk_upload, notifications, production_consumables, machines_equipment, production_completions
    
    app.include_router(auth.router)
    app.include_router(permissions.router)
    app.include_router(staff.router)
    app.include_router(attendance.router)
    app.include_router(products.router)
    app.include_router(raw_materials.router)
    app.include_router(stock.router)
    app.include_router(warehouses.router)
    app.include_router(production.router)
    app.include_router(sales.router)
    app.include_router(stock_management.router)
    app.include_router(bom.router)
    app.include_router(settings.router)
    app.include_router(financial.router)
    app.include_router(bulk_upload.router)
    app.include_router(notifications.router)
    app.include_router(production_consumables.router)
    app.include_router(machines_equipment.router)
    app.include_router(production_completions.router)
    
    print("✅ AstroBSM StockMaster: all routers loaded including production_consumables, machines_equipment, production_completions")
except ImportError as e:
    print(f"❌ Module import failed: {e}")
except Exception as e:
    print(f"❌ Router setup failed: {e}")

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
        return {"error": "Service Worker not found"}
    
    @app.get("/{filename}.png")
    async def serve_images(filename: str):
        """Serve PNG images (logos, icons) from build folder"""
        # Deny API paths
        if filename.startswith("api") or "/" in filename:
            return {"error": "Not found"}
        
        image_path = frontend_build_path / f"{filename}.png"
        if image_path.exists():
            return FileResponse(str(image_path), media_type="image/png")
        return {"error": "Image not found"}
    
    @app.get("/{filename}.ico")
    async def serve_favicon(filename: str):
        """Serve favicon"""
        favicon_path = frontend_build_path / f"{filename}.ico"
        if favicon_path.exists():
            return FileResponse(str(favicon_path), media_type="image/x-icon")
        return {"error": "Favicon not found"}
    
    @app.get("/{full_path:path}")
    async def serve_react_app(full_path: str):
        # Don't handle API routes here - let them pass to API routers
        if full_path.startswith("api/") or full_path.startswith("docs") or full_path.startswith("openapi"):
            raise HTTPException(status_code=404, detail="Not Found")
        
        # Serve static assets from build directory
        static_path = frontend_build_path / full_path
        if static_path.exists() and static_path.is_file():
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