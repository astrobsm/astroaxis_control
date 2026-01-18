#!/usr/bin/env python3
"""
Create admin user for ASTRO-ASIX ERP
"""
import asyncio
import hashlib
import sys

sys.path.append('/app')

from app.models import User  # noqa: E402
from app.db import AsyncSessionLocal  # noqa: E402
from sqlalchemy import select  # noqa: E402


async def create_admin():
    async with AsyncSessionLocal() as db:
        # Check if admin user already exists
        result = await db.execute(
            select(User).where(User.email == 'admin@astroasix.com')
        )
        existing_user = result.scalar_one_or_none()

        if existing_user:
            print("✅ Admin user already exists!")
            return

        # Create admin user
        admin = User(
            email='admin@astroasix.com',
            hashed_password=hashlib.sha256(
                'admin123'.encode()
            ).hexdigest(),
            full_name='Admin User',
            role='admin',
            is_active=True,
            is_locked=False,
            failed_login_attempts=0
        )

        db.add(admin)
        await db.commit()
        print("✅ Admin user created successfully!")
        print("📧 Email: admin@astroasix.com")
        print("🔑 Password: admin123")


if __name__ == "__main__":
    asyncio.run(create_admin())
