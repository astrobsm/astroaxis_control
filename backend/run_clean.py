"""
Clean ASTRO-ASIX ERP Server Runner
Ensures no Oracle/IVANSTAMAS imports
"""
import sys
import os
from pathlib import Path

# Get absolute path to THIS project
BACKEND_DIR = Path(__file__).parent.absolute()
PROJECT_ROOT = BACKEND_DIR.parent

print(f"🚀 ASTRO-ASIX ERP Clean Startup")
print(f"📁 Backend: {BACKEND_DIR}")
print(f"📁 Project: {PROJECT_ROOT}")

# Filter sys.path to remove ANY Oracle/IVANSTAMAS references
cleaned_path = [str(BACKEND_DIR)]
for p in sys.path:
    p_lower = p.lower()
    if 'oracle' not in p_lower and 'ivanstamas' not in p_lower and 'astrobsm' not in p_lower:
        if p not in cleaned_path:
            cleaned_path.append(p)

sys.path = cleaned_path
os.environ['PYTHONPATH'] = str(BACKEND_DIR)

print(f"✅ Python path cleaned: {len(sys.path)} entries")
print(f"🔍 First 3 paths: {sys.path[:3]}")

# Now start uvicorn
if __name__ == "__main__":
    import uvicorn
    
    # Change to backend directory
    os.chdir(BACKEND_DIR)
    
    print(f"📂 Working directory: {os.getcwd()}")
    print(f"🌐 Starting server on http://127.0.0.1:8004")
    
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8004,
        reload=True,
        log_level="info"
    )
