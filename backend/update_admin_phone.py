import asyncio
from app.db import AsyncSessionLocal
from app.models import User
from sqlalchemy import select


async def update_admin_phone():
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.email == 'admin@astrobsm.com')
        )
        user = result.scalar_one_or_none()

        if user:
            user.phone = '08033328385'
            await session.commit()
            print('✅ Admin phone updated successfully!')
            print('📧 Email: admin@astrobsm.com')
            print('📱 Phone: 08033328385')
            print('🔑 Password: admin123')
            print('👤 Role: admin')
        else:
            print('❌ Admin user not found')


if __name__ == '__main__':
    asyncio.run(update_admin_phone())
