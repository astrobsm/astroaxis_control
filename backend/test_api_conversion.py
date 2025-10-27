#!/usr/bin/env python3
"""
Manual test of the exact same conversion that the API does
"""
import asyncio
import sys
import os
import json

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.db import AsyncSessionLocal
from app.models import Product
from app.schemas import ProductSchema
from sqlalchemy import select

async def test_api_conversion():
    """Test the exact same conversion logic as the API"""
    print("🔍 Testing API conversion logic...")
    
    async with AsyncSessionLocal() as session:
        # Same query as API
        result = await session.execute(select(Product).limit(3))
        products = result.scalars().all()
        
        # Same conversion as API
        converted_items = []
        for p in products:
            try:
                schema_obj = ProductSchema.model_validate(p)
                item_dict = schema_obj.model_dump()
                converted_items.append(item_dict)
                print(f"✅ Converted: {p.name} -> {item_dict}")
            except Exception as e:
                print(f"❌ Failed to convert {p.name}: {e}")
        
        # Final result
        api_response = {
            "items": converted_items,
            "total": len(products),
            "page": 1,
            "size": 50,
            "pages": 1
        }
        
        print(f"\n📋 Final API Response structure:")
        print(json.dumps(api_response, indent=2, default=str))

if __name__ == "__main__":
    asyncio.run(test_api_conversion())