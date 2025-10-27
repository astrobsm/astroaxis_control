"""
Isolated ASTRO-ASIX ERP Server
This completely bypasses any system-wide Python package conflicts
"""
import os
import sys
from pathlib import Path

# Get absolute path to backend directory
BACKEND_DIR = Path(__file__).parent.absolute()
APP_DIR = BACKEND_DIR / "app"

# Clear Python path to avoid conflicts
sys.path = [str(BACKEND_DIR)] + [p for p in sys.path if 'AstroBSM' not in p and 'IVANSTAMAS' not in p]

# Set environment variables
os.environ.pop('PYTHONPATH', None)
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'  # Prevent .pyc files

print(f"🔧 ASTRO-ASIX ERP Server")
print(f"📁 Backend Directory: {BACKEND_DIR}")
print(f"📦 App Directory: {APP_DIR}")
print(f"🐍 Python: {sys.executable}")
print(f"📍 Python Path: {sys.path[:3]}...")

def start_server():
    try:
        # Import FastAPI components directly
        from fastapi import FastAPI
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.staticfiles import StaticFiles
        from fastapi.responses import FileResponse
        
        # Create clean app
        app = FastAPI(
            title='ASTRO-ASIX ERP',
            description='Enterprise Resource Planning system for ASTRO-ASIX',
            version='1.0.0'
        )
        
        # Add CORS
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["*"],
        )
        
        # Import our routers directly
        sys.path.insert(0, str(APP_DIR))
        
        # Import all available API modules
        try:
            from api import staff, attendance, products, raw_materials, production, sales, stock, warehouses, bom
            
            # Core modules (always include)
            app.include_router(staff.router)
            app.include_router(attendance.router)
            
            # ERP modules (include if available)
            app.include_router(products.router)
            app.include_router(raw_materials.router)
            app.include_router(production.router)
            app.include_router(sales.router)
            app.include_router(stock.router)
            app.include_router(warehouses.router)
            app.include_router(bom.router)
            
            print("✅ ASTRO-ASIX complete ERP modules loaded successfully")
            print("📦 Products, 🧱 Raw Materials, 🏭 Production, 💰 Sales, 📊 Stock, 🏪 Warehouses, 📋 BOM")
            
        except ImportError as e:
            print(f"⚠️ Some modules unavailable: {e}")
            # Fallback to core modules only
            from api import staff, attendance
            app.include_router(staff.router)
            app.include_router(attendance.router)
            print("✅ ASTRO-ASIX core modules loaded (staff & attendance)")
        
        print("🏢 ASTRO-ASIX ERP - Staff Management & Attendance with PIN system")
        
        # Add health check
        @app.get('/api/health')
        async def health():
            return {'status': 'ok', 'message': 'ASTRO-ASIX ERP is running', 'version': '1.0.0'}
        
        # Frontend serving
        frontend_build = BACKEND_DIR.parent / "frontend" / "build"
        if frontend_build.exists():
            app.mount("/static", StaticFiles(directory=str(frontend_build / "static")), name="static")
            
            @app.get("/")
            async def serve_frontend():
                return FileResponse(str(frontend_build / "index.html"), media_type="text/html")
                
            @app.get("/manifest.json")
            async def serve_manifest():
                manifest_path = frontend_build / "manifest.json"
                if manifest_path.exists():
                    return FileResponse(str(manifest_path), media_type="application/json")
                return {"error": "Manifest not found"}
                
            @app.get("/logo.png")
            async def serve_logo():
                # Return a simple response or redirect to a default logo
                return {"message": "ASTRO-ASIX ERP Logo", "status": "placeholder"}
                
            # Catch-all for React SPA routing
            @app.get("/{full_path:path}")
            async def serve_spa(full_path: str):
                # Don't serve React app for API routes
                if full_path.startswith("api/") or full_path.startswith("docs") or full_path.startswith("openapi"):
                    return {"error": "API endpoint not found"}
                
                # Serve index.html for all other routes (React Router handles them)
                return FileResponse(str(frontend_build / "index.html"), media_type="text/html")
        
        # Start server
        import uvicorn
        print("🚀 Starting ASTRO-ASIX ERP server on port 8004...")
        uvicorn.run(app, host="127.0.0.1", port=8004, reload=False)
        
    except Exception as e:
        print(f"❌ Server startup error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    start_server()