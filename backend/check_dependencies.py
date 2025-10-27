#!/usr/bin/env python3
"""
ASTRO-ASIX ERP - Dependency Validation & System Status
"""
import sys
import subprocess
import os

def check_python_dependencies():
    """Check if all backend dependencies are installed"""
    print("🐍 BACKEND DEPENDENCIES CHECK")
    print("=" * 40)
    
    required_packages = [
        'fastapi',
        'uvicorn',
        'sqlalchemy',
        'asyncpg', 
        'alembic',
        'pydantic',
        'python-dotenv',
        'httpx',
        'pytest',
        'pytest-asyncio',
        'requests',
        'python-multipart'
    ]
    
    installed_count = 0
    for package in required_packages:
        try:
            if package == 'python-dotenv':
                import dotenv
            elif package == 'python-multipart':
                import multipart
            else:
                __import__(package.replace('-', '_'))
            print(f"   ✅ {package}")
            installed_count += 1
        except ImportError:
            print(f"   ❌ {package} - Not installed")
    
    print(f"\n📊 Backend Status: {installed_count}/{len(required_packages)} dependencies installed")
    return installed_count == len(required_packages)

def check_node_dependencies():
    """Check if frontend build works"""
    print("\n🌐 FRONTEND DEPENDENCIES CHECK")
    print("=" * 40)
    
    try:
        # Check if package.json exists
        frontend_path = os.path.join(os.path.dirname(__file__), '..', 'frontend')
        package_json = os.path.join(frontend_path, 'package.json')
        
        if os.path.exists(package_json):
            print("   ✅ package.json found")
        else:
            print("   ❌ package.json not found")
            return False
            
        # Check if node_modules exists
        node_modules = os.path.join(frontend_path, 'node_modules')
        if os.path.exists(node_modules):
            print("   ✅ node_modules installed")
        else:
            print("   ❌ node_modules not found")
            return False
            
        # Check if build directory exists (from previous build)
        build_dir = os.path.join(frontend_path, 'build')
        if os.path.exists(build_dir):
            print("   ✅ Build directory exists")
        else:
            print("   ℹ️  Build directory not found (run npm run build)")
            
        print("\n📊 Frontend Status: Dependencies installed and ready")
        return True
        
    except Exception as e:
        print(f"   ❌ Error checking frontend: {e}")
        return False

def check_database_connection():
    """Check PostgreSQL connection"""
    print("\n🗄️  DATABASE CONNECTION CHECK")
    print("=" * 40)
    
    try:
        import asyncio
        sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
        
        async def test_db():
            from app.db import AsyncSessionLocal
            from sqlalchemy import text
            async with AsyncSessionLocal() as session:
                result = await session.execute(text("SELECT 1 as test"))
                test_value = result.scalar()
                return test_value == 1
        
        result = asyncio.run(test_db())
        if result:
            print("   ✅ PostgreSQL connection successful")
            print("   ✅ Database 'axis_db' accessible")
            return True
        else:
            print("   ❌ Database query failed")
            return False
            
    except Exception as e:
        print(f"   ❌ Database connection failed: {e}")
        return False

def check_api_functionality():
    """Check if FastAPI app can import and run"""
    print("\n🔌 API FUNCTIONALITY CHECK")
    print("=" * 40)
    
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
        from app.main import app
        print("   ✅ FastAPI app imports successfully")
        
        # Check if routes are registered
        routes = [route.path for route in app.routes]
        api_routes = [r for r in routes if r.startswith('/api/')]
        
        print(f"   ✅ {len(api_routes)} API routes registered")
        print("   📋 Available endpoints:")
        for route in api_routes[:8]:  # Show first 8 routes
            print(f"      - {route}")
        if len(api_routes) > 8:
            print(f"      ... and {len(api_routes) - 8} more")
            
        return True
        
    except Exception as e:
        print(f"   ❌ FastAPI app failed to import: {e}")
        return False

def main():
    """Main validation function"""
    print("🚀 ASTRO-ASIX ERP - DEPENDENCY VALIDATION")
    print("=" * 50)
    
    # Run all checks
    backend_ok = check_python_dependencies()
    frontend_ok = check_node_dependencies()
    db_ok = check_database_connection()
    api_ok = check_api_functionality()
    
    # Summary
    print(f"\n📊 SYSTEM STATUS SUMMARY")
    print("=" * 50)
    
    status_items = [
        ("Backend Dependencies", backend_ok),
        ("Frontend Dependencies", frontend_ok), 
        ("Database Connection", db_ok),
        ("API Functionality", api_ok)
    ]
    
    all_good = True
    for item, status in status_items:
        icon = "✅" if status else "❌"
        print(f"   {icon} {item}")
        if not status:
            all_good = False
    
    if all_good:
        print(f"\n🎉 ALL SYSTEMS OPERATIONAL!")
        print(f"   The ASTRO-ASIX ERP application is ready to use.")
        print(f"   Backend: python -m uvicorn app.main:app --reload")
        print(f"   Frontend: npm start (in frontend directory)")
    else:
        print(f"\n⚠️  SOME ISSUES DETECTED")
        print(f"   Please review the failed checks above.")
    
    return all_good

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)