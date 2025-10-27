import asyncio
import sys
sys.path.insert(0, 'C:/Users/USER/ASTROAXIS/backend')

from app.db import AsyncSessionLocal
from app.models import User
import uuid
import hashlib
from sqlalchemy import select

async def create_test_user():
    async with AsyncSessionLocal() as db:
        # Check if user exists
        result = await db.execute(select(User).where(User.email == "admin@astroasix.com"))
        existing = result.scalar_one_or_none()
        
        if existing:
            print(f"✅ User already exists: {existing.email}")
            print(f"   ID: {existing.id}")
            print(f"   Role: {existing.role}")
            print(f"   Active: {existing.is_active}")
            return
        
        # Create new admin user
        password = "Admin123!"
        hashed = hashlib.sha256(password.encode()).hexdigest()
        
        new_user = User(
            id=uuid.uuid4(),
            email="admin@astroasix.com",
            hashed_password=hashed,
            full_name="Admin User",
            role="admin",
            is_active=True,
            is_locked=False,
            failed_login_attempts=0,
            two_factor_enabled=False
        )
        
        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)
        
        print(f"✅ Created admin user: {new_user.email}")
        print(f"   Password: Admin123!")
        print(f"   Role: {new_user.role}")

if __name__ == "__main__":
    asyncio.run(create_test_user())
