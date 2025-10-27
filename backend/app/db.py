import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()
# Allow overriding DATABASE_URL in environment; use your specified PostgreSQL credentials as default
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    # Use your specified PostgreSQL credentials: user=postgres, password=natiss_natiss, database=axis_db
    DATABASE_URL = 'postgresql+asyncpg://postgres:natiss_natiss@localhost:5432/axis_db'

engine = create_async_engine(DATABASE_URL, future=True)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def get_session():
    async with AsyncSessionLocal() as session:
        yield session
