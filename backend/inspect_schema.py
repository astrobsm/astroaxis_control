#!/usr/bin/env python3
"""
Database schema inspector to understand existing structure
"""
import asyncpg
import asyncio
from typing import Dict, List
import json

async def inspect_database():
    """Inspect the current database schema"""
    try:
        # Connect to database
        conn = await asyncpg.connect(
            host='localhost',
            database='axis_db',
            user='postgres',
            password='postgres'
        )
        
        # Get all tables
        tables_query = """
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public' 
        ORDER BY table_name;
        """
        
        tables = await conn.fetch(tables_query)
        print(f"Found {len(tables)} tables:")
        
        schema_info = {}
        
        for table in tables:
            table_name = table['table_name']
            print(f"\n=== {table_name.upper()} ===")
            
            # Get columns
            columns_query = """
            SELECT 
                column_name,
                data_type,
                is_nullable,
                column_default,
                character_maximum_length,
                numeric_precision,
                numeric_scale
            FROM information_schema.columns 
            WHERE table_schema = 'public' 
            AND table_name = $1
            ORDER BY ordinal_position;
            """
            
            columns = await conn.fetch(columns_query, table_name)
            schema_info[table_name] = {
                'columns': []
            }
            
            for col in columns:
                col_info = {
                    'name': col['column_name'],
                    'type': col['data_type'],
                    'nullable': col['is_nullable'] == 'YES',
                    'default': col['column_default'],
                    'max_length': col['character_maximum_length'],
                    'precision': col['numeric_precision'],
                    'scale': col['numeric_scale']
                }
                schema_info[table_name]['columns'].append(col_info)
                
                type_str = col['data_type']
                if col['character_maximum_length']:
                    type_str += f"({col['character_maximum_length']})"
                elif col['numeric_precision'] and col['numeric_scale']:
                    type_str += f"({col['numeric_precision']},{col['numeric_scale']})"
                
                nullable_str = "NULL" if col['is_nullable'] == 'YES' else "NOT NULL"
                default_str = f" DEFAULT {col['column_default']}" if col['column_default'] else ""
                
                print(f"  {col['column_name']:<25} {type_str:<20} {nullable_str}{default_str}")
        
        # Save schema to file
        with open('database_schema.json', 'w') as f:
            json.dump(schema_info, f, indent=2, default=str)
        
        print(f"\n\nSchema saved to database_schema.json")
        
        await conn.close()
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == '__main__':
    asyncio.run(inspect_database())