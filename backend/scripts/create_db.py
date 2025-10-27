"""
Script to create axis_db database and run DDL.
Run this first before running seed_data.py
"""
import asyncio
import asyncpg
import sys
import os

async def create_database():
    # Connect to postgres default database to create axis_db
    conn = await asyncpg.connect(
        host='localhost',
        port=5432,
        user='postgres',
        password='natiss_natiss',
        database='postgres'  # Connect to default postgres db first
    )
    
    try:
        # Check if axis_db exists
        result = await conn.fetch("SELECT 1 FROM pg_database WHERE datname = 'axis_db'")
        if result:
            print("Database axis_db already exists.")
        else:
            # Create axis_db database
            await conn.execute("CREATE DATABASE axis_db")
            print("Created database axis_db.")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await conn.close()
    
    # Now connect to axis_db and run DDL
    conn = await asyncpg.connect(
        host='localhost',
        port=5432,
        user='postgres',
        password='natiss_natiss',
        database='axis_db'
    )
    
    try:
        # Read and execute DDL
        ddl_path = os.path.join(os.path.dirname(__file__), '..', 'migrations', 'ddl.sql')
        with open(ddl_path, 'r') as f:
            ddl_content = f.read()
        
        await conn.execute(ddl_content)
        print("DDL executed successfully.")
    except Exception as e:
        print(f"DDL execution error: {e}")
    finally:
        await conn.close()

if __name__ == '__main__':
    asyncio.run(create_database())