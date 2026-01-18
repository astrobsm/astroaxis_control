import asyncio
from app.db import AsyncSessionLocal
from app.models import User
from passlib.context import CryptContext
from sqlalchemy import select

pwd_context = CryptContext(schemes=['bcrypt'], deprecated='auto')


async def create_admin():
    async with AsyncSessionLocal() as session:
        # Check if admin exists
        result = await session.execute(
            select(User).where(User.email == 'admin@astrobsm.com')
        )
        if result.scalar_one_or_none():
            print('✅ Admin already exists')
            print('📧 Email: admin@astrobsm.com')
            print('🔑 Password: admin123')
            return

        # Create admin
        admin = User(
            email='admin@astrobsm.com',
            username='admin',
            hashed_password=pwd_context.hash('admin123'),
            full_name='System Administrator',
            phone='08033328385',
            role='admin',
            department='IT',
            is_active=True,
            is_locked=False
        )
        session.add(admin)
        await session.commit()
        print('✅ Admin user created successfully!')
        print('📧 Email: admin@astrobsm.com')
        print('🔑 Password: admin123')

if __name__ == '__main__':
    asyncio.run(create_admin())
