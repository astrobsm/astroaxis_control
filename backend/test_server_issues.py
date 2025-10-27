#!/usr/bin/env python3
"""
Comprehensive test script to identify server issues
"""
import asyncio
import sys
import traceback
from sqlalchemy.ext.asyncio import AsyncSession

async def test_database_connection():
    """Test database connection"""
    try:
        from app.db import get_session
        async for session in get_session():
            print("✅ Database connection successful")
            break
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        traceback.print_exc()
        return False
    return True

async def test_model_imports():
    """Test all model imports"""
    try:
        from app.models import (
            User, Product, RawMaterial, BOM, BOMLine, ProductCost,
            Warehouse, StockMovement, StockLevel, Customer, SalesOrder,
            SalesOrderLine, ProductionOrder, ProductionOrderMaterial,
            Department, Employee, WorkLog, Staff, Invoice, InvoiceLine,
            Payment, PayrollEntry, ProductionLaborLog, AuditLog
        )
        print("✅ All models imported successfully")
        return True
    except Exception as e:
        print(f"❌ Model import failed: {e}")
        traceback.print_exc()
        return False

async def test_schema_imports():
    """Test all schema imports"""
    try:
        from app.schemas import (
            ProductSchema, RawMaterialSchema, WarehouseSchema,
            CustomerSchema, SalesOrderSchema, ProductionOrderSchema,
            EmployeeSchema, StaffSchema, DepartmentSchema
        )
        print("✅ All schemas imported successfully")
        return True
    except Exception as e:
        print(f"❌ Schema import failed: {e}")
        traceback.print_exc()
        return False

async def test_api_imports():
    """Test API module imports"""
    try:
        from app.api import (
            auth, bom, products, raw_materials, stock, warehouses,
            sales, production, staff, debug
        )
        print("✅ All API modules imported successfully")
        return True
    except Exception as e:
        print(f"❌ API import failed: {e}")
        traceback.print_exc()
        return False

async def test_app_creation():
    """Test FastAPI app creation"""
    try:
        from app.main import app
        print("✅ FastAPI app created successfully")
        print(f"📊 Registered routes: {len(app.routes)}")
        return True
    except Exception as e:
        print(f"❌ FastAPI app creation failed: {e}")
        traceback.print_exc()
        return False

async def test_database_queries():
    """Test basic database queries"""
    try:
        from app.db import get_session
        from app.models import Product, Customer, Warehouse
        from sqlalchemy.future import select
        
        async for session in get_session():
            # Test products query
            result = await session.execute(select(Product).limit(1))
            products = result.scalars().all()
            print(f"✅ Products query successful - found {len(products)} products")
            
            # Test customers query
            result = await session.execute(select(Customer).limit(1))
            customers = result.scalars().all()
            print(f"✅ Customers query successful - found {len(customers)} customers")
            
            # Test warehouses query
            result = await session.execute(select(Warehouse).limit(1))
            warehouses = result.scalars().all()
            print(f"✅ Warehouses query successful - found {len(warehouses)} warehouses")
            break
        return True
    except Exception as e:
        print(f"❌ Database queries failed: {e}")
        traceback.print_exc()
        return False

async def main():
    """Run all tests"""
    print("🔍 ASTRO-ASIX ERP - Comprehensive System Test")
    print("=" * 50)
    
    tests = [
        ("Model Imports", test_model_imports),
        ("Schema Imports", test_schema_imports),
        ("API Imports", test_api_imports),
        ("Database Connection", test_database_connection),
        ("Database Queries", test_database_queries),
        ("FastAPI App Creation", test_app_creation),
    ]
    
    results = []
    for test_name, test_func in tests:
        print(f"\n🧪 Testing: {test_name}")
        try:
            result = await test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ {test_name} failed with exception: {e}")
            results.append((test_name, False))
    
    print("\n" + "=" * 50)
    print("📋 TEST SUMMARY")
    print("=" * 50)
    
    passed = 0
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {test_name}")
        if result:
            passed += 1
    
    print(f"\n🎯 Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! The application should work correctly.")
    else:
        print("⚠️  Some tests failed. Check the errors above.")
        return 1
    
    return 0

if __name__ == "__main__":
    result = asyncio.run(main())
    sys.exit(result)