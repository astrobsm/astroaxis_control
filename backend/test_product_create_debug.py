#!/usr/bin/env python3
"""Debug script to test product creation directly"""
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.models import Product, ProductPricing, Base
from app.schemas import ProductCreate, ProductPricingCreate
import uuid

async def test_direct_create():
    """Test creating a product directly in the database"""
    # Use same database as main app
    engine = create_async_engine(
        "postgresql+asyncpg://postgres:admin@localhost/astroasix",
        echo=True
    )
    
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as session:
        try:
            # Create product data
            product_data = ProductCreate(
                sku=f"TEST-{uuid.uuid4().hex[:8]}",
                name="Direct Test Product",
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
            
            print(f"Creating product: {product_data.sku}")
            
            # Extract pricing
            pricing_data = product_data.pricing
            product_dict = product_data.model_dump(exclude={'pricing'})
            
            # Create product
            new_product = Product(**product_dict)
            session.add(new_product)
            await session.flush()
            
            print(f"Product created with ID: {new_product.id}")
            
            # Add pricing
            if pricing_data:
                for pricing_item in pricing_data:
                    pricing_dict = pricing_item.model_dump()
                    print(f"Adding pricing: {pricing_dict}")
                    
                    new_pricing = ProductPricing(
                        product_id=new_product.id,
                        unit=pricing_dict['unit'],
                        cost_price=pricing_dict['cost_price'],
                        retail_price=pricing_dict['retail_price'],
                        wholesale_price=pricing_dict['wholesale_price']
                    )
                    session.add(new_pricing)
            
            await session.commit()
            await session.refresh(new_product)
            
            print(f"✅ SUCCESS! Product created: {new_product.name}")
            print(f"   ID: {new_product.id}")
            print(f"   Pricing entries: {len(new_product.pricing)}")
            
        except Exception as e:
            print(f"❌ ERROR: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            await session.rollback()
    
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(test_direct_create())
