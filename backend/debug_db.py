"""
Debug script to test database connectivity and model queries directly
"""
import asyncio
import sys
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.db import engine, AsyncSessionLocal
from app.models import Product, RawMaterial, Warehouse
from sqlalchemy import select

async def test_database_queries():
    """Test database queries directly"""
    print("🔧 Testing Database Queries Directly...")
    
    async with AsyncSessionLocal() as session:
        # Test products
        print("\n📦 Testing Products Query:")
        try:
            result = await session.execute(select(Product))
            products = result.scalars().all()
            print(f"   Products found: {len(products)}")
            for p in products[:3]:
                print(f"   - {p.sku}: {p.name}")
        except Exception as e:
            print(f"   ❌ Products query failed: {e}")
        
        # Test raw materials
        print("\n🧱 Testing Raw Materials Query:")
        try:
            result = await session.execute(select(RawMaterial))
            raw_materials = result.scalars().all()
            print(f"   Raw materials found: {len(raw_materials)}")
            for rm in raw_materials[:3]:
                print(f"   - {rm.sku}: {rm.name} (${rm.unit_cost})")
        except Exception as e:
            print(f"   ❌ Raw materials query failed: {e}")
            
        # Test warehouses
        print("\n🏭 Testing Warehouses Query:")
        try:
            result = await session.execute(select(Warehouse))
            warehouses = result.scalars().all()
            print(f"   Warehouses found: {len(warehouses)}")
            for wh in warehouses[:3]:
                print(f"   - {wh.code}: {wh.name} ({wh.location})")
        except Exception as e:
            print(f"   ❌ Warehouses query failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_database_queries())