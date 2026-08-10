"""BOM unit-cost endpoint.

Integration test: it drives the real app against the real DATABASE_URL, so
that must point at a throwaway database. tests/conftest.py refuses to let the
suite run otherwise.
"""
import os
import sys
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import db as db_mod                                    # noqa: E402
from app import main as main_mod                                # noqa: E402
from app import models as models_mod                            # noqa: E402
from app.api import auth as auth_mod                            # noqa: E402

engine = db_mod.engine
Base = models_mod.Base
app = main_mod.app


async def _auth_headers(role="admin"):
    """A bearer token for a freshly created user.

    /api/bom is in main.py's authenticated router group, so an anonymous
    request is a 401 and never reaches the cost calculation being tested.
    """
    user_id = uuid.uuid4()
    email = f"bomtest_{uuid.uuid4().hex}@example.com"
    async with db_mod.AsyncSessionLocal() as session:
        session.add(models_mod.User(
            id=user_id,
            email=email,
            hashed_password=auth_mod.hash_password("TestPass123!"),
            full_name="BOM Cost Tester",
            role=role,
            is_active=True,
            is_locked=False,
        ))
        await session.commit()
    token = auth_mod.create_access_token(
        data={"sub": str(user_id), "email": email, "role": role})
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_bom_cost_endpoint():
    # ensure db tables exist
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Check if we have existing BOM data, otherwise seed it.
    #
    # The seed MUST run after this connection is closed. Seeding TRUNCATEs
    # boms, which needs an ACCESS EXCLUSIVE lock, while this SELECT holds
    # ACCESS SHARE inside an open transaction. Calling seed_run() from inside
    # the `async with` therefore deadlocked: the TRUNCATE waited for a lock
    # held by the very transaction that was waiting for seed_run() to return.
    # Postgres does not break it -- the outer session is idle-in-transaction,
    # not part of a detectable cycle -- so the test simply hung forever.
    async with engine.connect() as conn:
        bom_count = (await conn.execute(text("select count(*) from boms"))).scalar()

    if bom_count == 0:
        from scripts.seed_data import run as seed_run
        await seed_run()


    # find bom id
    async with engine.connect() as conn:
        res = await conn.execute(text("select id from boms limit 1"))
        row = res.first()
        bom_id = str(row[0])
    
    # Test the BOM cost endpoint.
    #
    # The path is /api/bom/... (singular) -- this asked for /api/boms/... and
    # got a 404 that read as "the cost calculation is broken" rather than "that
    # URL does not exist". The router is also in main.py's authenticated group,
    # so the request needs a bearer token; without one it is a 401 and the cost
    # assertion below is never reached.
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as ac:
        r = await ac.get(f'/api/bom/{bom_id}/cost', headers=await _auth_headers())
        assert r.status_code == 200, f"{r.status_code}: {r.text[:200]}"
        data = r.json()
        # the expected material cost = 2*0.15 + 1*0.05 = 0.35
        # PostgreSQL numeric may return extra precision, so check decimal equivalence
        from decimal import Decimal
        assert Decimal(data['unit_cost']) == Decimal('0.35')
