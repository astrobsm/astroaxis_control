import sys
import os
import asyncio
from httpx import AsyncClient
from sqlalchemy import text
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import main as main_mod
from app import db as db_mod
from app import models as models_mod

engine = db_mod.engine
Base = models_mod.Base
app = main_mod.app
import pytest

@pytest.mark.asyncio
async def test_bom_cost_endpoint():
    # ensure db tables exist
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Check if we have existing BOM data, otherwise seed it
    async with engine.connect() as conn:
        res = await conn.execute(text("select count(*) from boms"))
        bom_count = res.scalar()
        if bom_count == 0:
            # Only seed if no data exists
            from scripts.seed_data import run as seed_run
            await seed_run()
    
    # find bom id
    async with engine.connect() as conn:
        res = await conn.execute(text("select id from boms limit 1"))
        row = res.first()
        bom_id = str(row[0])
    
    # Test the BOM cost endpoint
    async with AsyncClient(app=app, base_url='http://test') as ac:
        r = await ac.get(f'/api/boms/{bom_id}/cost')
        assert r.status_code == 200
        data = r.json()
        # the expected material cost = 2*0.15 + 1*0.05 = 0.35
        # PostgreSQL numeric may return extra precision, so check decimal equivalence
        from decimal import Decimal
        assert Decimal(data['unit_cost']) == Decimal('0.35')
