#!/usr/bin/env python3
"""Quick database connection test"""
import asyncio
from app.db import engine, AsyncSessionLocal
from sqlalchemy import text

async def test_connection():
    """Test if we can connect to the database"""
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(text("SELECT 1"))
            print("✅ Database connection successful!")
            
            # Check if products table exists
            result = await session.execute(text("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'products'
            """))
            if result.scalar():
                print("✅ Products table exists")
            else:
                print("❌ Products table does NOT exist - run migrations!")
                
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await engine.dispose()

if __name__ == "__main__":
    asyncio.run(test_connection())
