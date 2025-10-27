#!/usr/bin/env python3
"""
Clean startup script for ASTRO-ASIX ERP
This bypasses any module path conflicts
"""
import sys
import os
from pathlib import Path

# Add the current backend directory to Python path
backend_dir = Path(__file__).parent.absolute()
sys.path.insert(0, str(backend_dir))

print(f"🔧 Starting ASTRO-ASIX ERP from: {backend_dir}")
print(f"🐍 Python executable: {sys.executable}")

# Clear any problematic environment variables
os.environ.pop('PYTHONPATH', None)

try:
    # Import our app directly
    from app.main import app
    print(f"✅ Successfully imported ASTRO-ASIX ERP: {app.title}")
    
    # Start uvicorn programmatically
    import uvicorn
    print("🚀 Starting ASTRO-ASIX ERP server...")
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8000, 
        reload=True,
        reload_dirs=[str(backend_dir)],
        log_level="info"
    )
    
except ImportError as e:
    print(f"❌ Import error: {e}")
    sys.exit(1)
except Exception as e:
    print(f"❌ Startup error: {e}")
    sys.exit(1)