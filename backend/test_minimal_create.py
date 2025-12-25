#!/usr/bin/env python3
"""
Minimal reproduction of the product creation issue
"""
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.models import Product, ProductPricing
from app.schemas import ProductCreate, ProductPricingCreate
import traceback

async def test_create():
    engine = create_async_engine(
        "postgresql+asyncpg://postgres:natiss_natiss@localhost:5432/axis_db",
        echo=False
    )
    
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as session:
        try:
            # Replicate exactly what the API does
            product_data = ProductCreate(
                sku="DEBUG-TEST-001",
                name="Debug Test Product",
                unit="each",
                pricing=[
                    ProductPricingCreate(
                        unit="each",
                        cost_price=10.0,
                        retail_price=15.0,
                        wholesale_price=12.5
                    )
                ]
            )
            
            print("Step 1: Extract pricing data...")
            pricing_data = product_data.pricing if hasattr(product_data, 'pricing') else []
            print(f"  Pricing data type: {type(pricing_data)}")
            print(f"  Pricing data: {pricing_data}")
            
            print("\nStep 2: Create product dict...")
            product_dict = product_data.model_dump(exclude={'pricing'})
            print(f"  Product dict keys: {product_dict.keys()}")
            
            print("\nStep 3: Create Product object...")
            new_product = Product(**product_dict)
            session.add(new_product)
            
            print("\nStep 4: Flush...")
            await session.flush()
            print(f"  Product ID: {new_product.id}")
            
            print("\nStep 5: Add pricing...")
            if pricing_data:
                for pricing_item in pricing_data:
                    pricing_dict = pricing_item.model_dump() if hasattr(pricing_item, 'model_dump') else pricing_item
                    print(f"  Pricing dict: {pricing_dict}")
                    
                    new_pricing = ProductPricing(
                        product_id=new_product.id,
                        unit=pricing_dict['unit'],
                        cost_price=pricing_dict['cost_price'],
                        retail_price=pricing_dict['retail_price'],
                        wholesale_price=pricing_dict['wholesale_price']
                    )
                    session.add(new_pricing)
            
            print("\nStep 6: Commit...")
            await session.commit()
            
            print("\nStep 7: Refresh...")
            await session.refresh(new_product)
            
            print(f"\nSUCCESS! Product created: {new_product.name}")
            print(f"  ID: {new_product.id}")
            
        except Exception as e:
            print(f"\nERROR at some step:")
            print(f"  Type: {type(e).__name__}")
            print(f"  Message: {e}")
            traceback.print_exc()
            await session.rollback()
    
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(test_create())
