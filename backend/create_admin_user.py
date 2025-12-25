#!/usr/bin/env python3
"""Create admin user for ASTRO-ASIX ERP"""
import asyncio
import hashlib
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text

async def create_admin():
    """Create admin user with phone 08033328385 and password NATISS"""
    # Connect to database
    engine = create_async_engine('postgresql+asyncpg://postgres:natiss_natiss@db:5432/axis_db')
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as session:
        # Hash password with SHA256
        password_hash = hashlib.sha256('NATISS'.encode()).hexdigest()
        
        # Insert or update admin user
        query = text('''
            INSERT INTO users (email, password_hash, full_name, phone, role, is_active, created_at, updated_at)
            VALUES (:email, :password, :name, :phone, :role, true, NOW(), NOW())
            ON CONFLICT (email) DO UPDATE 
            SET password_hash = EXCLUDED.password_hash,
                phone = EXCLUDED.phone,
                full_name = EXCLUDED.full_name,
                role = EXCLUDED.role,
                is_active = true,
                updated_at = NOW()
            RETURNING id, email, full_name, phone, role
        ''')
        
        result = await session.execute(query, {
            'email': 'admin@astroasix.com',
            'password': password_hash,
            'name': 'Administrator',
            'phone': '08033328385',
            'role': 'admin'
        })
        
        await session.commit()
        user = result.fetchone()
        
        if user:
            print('✅ Admin user created/updated successfully!')
            print(f'   Email: {user.email}')
            print(f'   Name: {user.full_name}')
            print(f'   Phone: {user.phone}')
            print(f'   Role: {user.role}')
            print(f'   ID: {user.id}')
            print('')
            print('🔐 Login credentials:')
            print('   Phone: 08033328385')
            print('   Password: NATISS')
        else:
            print('❌ Failed to create admin user')

if __name__ == '__main__':
    asyncio.run(create_admin())
