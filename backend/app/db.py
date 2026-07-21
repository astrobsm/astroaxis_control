import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()
# Allow overriding DATABASE_URL in environment
# Use specified PostgreSQL credentials as default
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    # No hardcoded fallback: a committed credential is readable by anyone with
    # repo access, and silently connecting to a local database hides a
    # misconfigured deployment instead of surfacing it.
    raise RuntimeError(
        "DATABASE_URL is not set. Set it in the environment (or backend/.env) "
        "before starting the application."
    )

engine = create_async_engine(DATABASE_URL, future=True)
AsyncSessionLocal = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def get_session():
    async with AsyncSessionLocal() as session:
        yield session
