import asyncio
import hashlib
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from app.models import User
import uuid

DATABASE_URL = "postgresql+asyncpg://postgres:natiss_natiss@localhost:5432/axis_db"

def hash_password(password: str) -> str:
    """Hash password using SHA-256"""
    return hashlib.sha256(password.encode()).hexdigest()

async def reset_admin():
    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as session:
        # Check for existing admin
        result = await session.execute(
            select(User).where(User.email == "admin@astroasix.com")
        )
        admin = result.scalar_one_or_none()
        
        if admin:
            # Update admin
            admin.hashed_password = hash_password("Admin123!")
            admin.is_active = True
            admin.is_locked = False
            admin.failed_login_attempts = 0
            admin.phone = "08033328385"
            print(f"✅ Updated admin user:")
        else:
            # Create admin
            admin = User(
                id=uuid.uuid4(),
                email="admin@astroasix.com",
                hashed_password=hash_password("Admin123!"),
                full_name="System Administrator",
                role="admin",
                phone="08033328385",
                department="Administration",
                is_active=True,
                is_locked=False,
                failed_login_attempts=0,
                two_factor_enabled=False
            )
            session.add(admin)
            print(f"✅ Created new admin user:")
        
        await session.commit()
        await session.refresh(admin)
        
        print(f"  Email: {admin.email}")
        print(f"  Phone: {admin.phone}")
        print(f"  Role: {admin.role}")
        print(f"  Active: {admin.is_active}")
        print(f"  Locked: {admin.is_locked}")
        print(f"  Failed Attempts: {admin.failed_login_attempts}")
        print(f"\n🔐 Login credentials:")
        print(f"  Email: admin@astroasix.com")
        print(f"  Password: Admin123!")
        print(f"  Phone: 08033328385")
        print(f"  Physical Password: NATISS")

if __name__ == "__main__":
    asyncio.run(reset_admin())
