from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

# Create ASTRO-ASIX ERP app
app = FastAPI(
    title='ASTRO-ASIX ERP', 
    description='Enterprise Resource Planning system for ASTRO-ASIX',
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

# Health check
@app.get('/api/health')
async def health():
    return {'status': 'ok', 'message': 'ASTRO-ASIX ERP is running clean', 'version': '1.0.0'}

# Import and include API routers (no COM/Oracle dependencies)
try:
    from app.api import staff, attendance, products, raw_materials, stock, warehouses, production, sales, stock_management, bom, settings, auth
    
    app.include_router(auth.router)
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
    
    print("✅ ASTRO-ASIX: auth, staff, attendance, products, raw_materials, stock, warehouses, production, sales, stock_management, bom, settings")
except ImportError as e:
    print(f"❌ Module import failed: {e}")
except Exception as e:
    print(f"❌ Router setup failed: {e}")

# Frontend serving
frontend_build_path = Path(__file__).parent.parent.parent / "frontend" / "build"
print(f"🔍 ASTRO-ASIX Frontend path: {frontend_build_path}")

if frontend_build_path.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_build_path / "static")), name="static")
    
    @app.get("/")
    async def serve_frontend():
        index_path = frontend_build_path / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path), media_type="text/html")
        return {"error": "Frontend not found"}
    
    @app.get("/manifest.json")
    async def manifest():
        manifest_path = frontend_build_path / "manifest.json"
        if manifest_path.exists():
            return FileResponse(str(manifest_path), media_type="application/json")
        return {"error": "Manifest not found"}
    
    @app.get("/{full_path:path}")
    async def serve_react_app(full_path: str):
        if full_path.startswith("api/") or full_path.startswith("docs") or full_path.startswith("openapi"):
            return {"error": "API endpoint not found"}
        
        index_path = frontend_build_path / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path), media_type="text/html")
        return {"error": "Frontend not found"}
else:
    @app.get("/")
    async def no_frontend():
        return {
            "message": "ASTRO-ASIX ERP Backend",
            "version": "1.0.0",
            "status": "Frontend build not found"
        }

print("🚀 ASTRO-ASIX ERP initialized successfully - no legacy imports")
