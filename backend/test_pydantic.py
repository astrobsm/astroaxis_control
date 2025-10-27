#!/usr/bin/env python3
"""
Test Pydantic model validation
"""
import sys
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.models import Product as ProductModel
from app.schemas import ProductSchema
import asyncio
from app.db import AsyncSessionLocal
from sqlalchemy import select

async def test_pydantic_conversion():
    """Test if Pydantic model conversion works"""
    print("🧪 Testing Pydantic Model Conversion...")
    
    async with AsyncSessionLocal() as session:
        # Get a product from database
        result = await session.execute(select(ProductModel).limit(1))
        product = result.scalar_one_or_none()
        
        if product:
            print(f"Raw product: {product.id}, {product.sku}, {product.name}")
            print(f"Product attributes: {dir(product)}")
            
            # Try converting to Pydantic model
            try:
                product_schema = ProductSchema.model_validate(product)
                print(f"✅ Conversion successful: {product_schema}")
                print(f"Model dump: {product_schema.model_dump()}")
            except Exception as e:
                print(f"❌ Conversion failed: {e}")
                
                # Try manual construction
                try:
                    manual_schema = ProductSchema(
                        id=product.id,
                        sku=product.sku,
                        name=product.name,
                        description=product.description,
                        unit=product.unit,
                        created_at=product.created_at
                    )
                    print(f"✅ Manual construction: {manual_schema}")
                except Exception as e2:
                    print(f"❌ Manual construction failed: {e2}")
        else:
            print("❌ No products found in database")

if __name__ == "__main__":
    asyncio.run(test_pydantic_conversion())