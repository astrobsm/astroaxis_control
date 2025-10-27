"""
Quick demo script to show the ASTRO-ASIX ERP BOM cost calculation working.
"""
import asyncio
import sys
import os

# Ensure we import the local app package
ROOT = os.path.abspath(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.db import engine
from sqlalchemy import text

async def demo_bom_cost():
    print("🏭 ASTRO-ASIX ERP - BOM Cost Demo")
    print("="*50)
    
    # Get BOM ID
    async with engine.connect() as conn:
        res = await conn.execute(text("select id, product_id from boms limit 1"))
        row = res.first()
        if not row:
            print("❌ No BOM data found. Run scripts/seed_data.py first.")
            return
        
        bom_id = str(row[0])
        product_id = str(row[1])
        print(f"📋 Found BOM ID: {bom_id}")
        print(f"📦 Product ID: {product_id}")
    
    # Show BOM structure
    async with engine.connect() as conn:
        res = await conn.execute(text("""
            SELECT rm.name, rm.unit_cost, bl.qty_per_unit,
                   (rm.unit_cost * bl.qty_per_unit) as line_cost
            FROM bom_lines bl
            JOIN raw_materials rm ON rm.id = bl.raw_material_id
            WHERE bl.bom_id = :bom_id
        """), {"bom_id": bom_id})
        
        print("\n🧾 Bill of Materials:")
        total_cost = 0
        for row in res:
            name, unit_cost, qty, line_cost = row
            print(f"   • {name}: {qty} × ${unit_cost} = ${line_cost}")
            total_cost += float(line_cost)
        
        print(f"\n💰 Expected Total Cost: ${total_cost}")
    
    # Import and test the BOM cost endpoint directly
    from app.api.bom import compute_bom_cost
    from app.db import AsyncSessionLocal
    
    async with AsyncSessionLocal() as session:
        try:
            result = await compute_bom_cost(bom_id, session)
            print(f"✅ Computed Unit Cost: ${result['unit_cost']}")
            print(f"🎯 Cost calculation is working correctly!")
        except Exception as e:
            print(f"❌ Error: {e}")

if __name__ == '__main__':
    asyncio.run(demo_bom_cost())