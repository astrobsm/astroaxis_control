"""Reading a customer's transactions for a chosen period.

The screen used to fetch "the most recent hundred orders, whenever they were".
For a quiet customer that looks like the whole history; for an active one it is
a silent truncation, and the totals printed above the table -- orders, value,
how many unpaid -- are then totals of an arbitrary slice nobody chose.

So the period is now explicit, and these tests hold the two things that make it
trustworthy: that both ends of the range are inclusive, and that the `total` the
endpoint reports is the total of the SAME filtered set the caller is being
shown. A count that ignores the filter is worse than no count -- it makes paging
walk off the end of a result that is not there.
"""
import os
import sys
import uuid
from datetime import date, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from conftest import ensure_orm_schema

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import db as db_mod          # noqa: E402
from app import main as main_mod      # noqa: E402
from app import models as models_mod  # noqa: E402
from app.api import auth as auth_mod  # noqa: E402

app = main_mod.app
engine = db_mod.engine

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")

# Ages in days of the orders this customer will have.
AGES = [1, 45, 200, 400]


async def _headers(role: str = "admin") -> dict:
    email = f"period_{uuid.uuid4().hex}@example.com"
    async with db_mod.AsyncSessionLocal() as session:
        user_id = uuid.uuid4()
        session.add(models_mod.User(
            id=user_id, email=email,
            hashed_password=auth_mod.hash_password("TestPass123!"),
            full_name="Period Test", role=role, is_active=True, is_locked=False))
        await session.commit()
    token = auth_mod.create_access_token(
        data={"sub": str(user_id), "email": email, "role": role})
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def customer_with_history():
    """One customer, orders at known ages, isolated from every other test."""
    await ensure_orm_schema(engine)
    customer_id = uuid.uuid4()
    warehouse_id = uuid.uuid4()
    async with db_mod.AsyncSessionLocal() as session:
        await session.execute(
            text("""INSERT INTO customers (id, customer_code, name)
                    VALUES (:i, :c, :n)"""),
            {"i": str(customer_id), "c": f"C{uuid.uuid4().hex[:8].upper()}",
             "n": f"Period Customer {uuid.uuid4().hex[:6]}"})
        await session.execute(
            text("""INSERT INTO warehouses (id, code, name)
                    VALUES (:i, :c, 'Period WH')"""),
            {"i": str(warehouse_id), "c": f"W{uuid.uuid4().hex[:8].upper()}"})
        for age in AGES:
            await session.execute(
                # payment_status explicitly: earlier modules in the suite drop
                # and rebuild sales_orders, and the rebuilt table has no server
                # default, so relying on one makes this module pass alone and
                # fail in the full run -- with a response-validation error that
                # points at the endpoint rather than at the fixture.
                text("""INSERT INTO sales_orders
                            (id, order_number, customer_id, warehouse_id,
                             status, payment_status, total_amount, order_date)
                        VALUES (gen_random_uuid(), :num, :c, :w, 'delivered',
                                'unpaid', 50000,
                                NOW() - (:age || ' days')::interval)"""),
                {"num": f"SO-{uuid.uuid4().hex[:8].upper()}",
                 "c": str(customer_id), "w": str(warehouse_id),
                 "age": str(age)})
        await session.commit()
    yield customer_id


async def _orders(client, customer_id, **params):
    response = await client.get(
        "/api/sales/orders",
        params={"customer_id": str(customer_id), "limit": 1000, **params})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_without_a_period_the_whole_history_is_returned(
        customer_with_history):
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test",
                           headers=await _headers()) as client:
        body = await _orders(client, customer_with_history)
    assert len(body["items"]) == len(AGES)


@pytest.mark.asyncio
async def test_a_period_returns_only_what_falls_inside_it(
        customer_with_history):
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test",
                           headers=await _headers()) as client:
        year = await _orders(
            client, customer_with_history,
            date_from=str(date.today() - timedelta(days=365)))
        quarter = await _orders(
            client, customer_with_history,
            date_from=str(date.today() - timedelta(days=90)))

    assert len(year["items"]) == 3, "1, 45 and 200 days old"
    assert len(quarter["items"]) == 2, "1 and 45 days old"


@pytest.mark.asyncio
async def test_the_reported_total_matches_the_filter(customer_with_history):
    """Or the screen shows one number and lists another.

    The count query used to be built separately from the query. Filters added
    to one and not the other is the classic way a list of three rows announces
    itself as four.
    """
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test",
                           headers=await _headers()) as client:
        body = await _orders(
            client, customer_with_history,
            date_from=str(date.today() - timedelta(days=90)))

    assert body["total"] == len(body["items"]) == 2


@pytest.mark.asyncio
async def test_both_ends_of_the_range_include_their_own_day(
        customer_with_history):
    """An order placed at 4pm on the closing day is inside the period.

    order_date is a timestamp. Comparing it against a bare date would put
    everything after midnight on the last day outside the range -- the sort of
    off-by-one that quietly loses a day's trading from a report.
    """
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test",
                           headers=await _headers()) as client:
        # A window whose closing day is the day the newest order was placed.
        newest_day = date.today() - timedelta(days=AGES[0])
        body = await _orders(
            client, customer_with_history,
            date_from=str(newest_day), date_to=str(newest_day))

    assert len(body["items"]) == 1, (
        "the order placed on the closing day itself must be included")


@pytest.mark.asyncio
async def test_a_period_with_nothing_in_it_is_empty_not_everything(
        customer_with_history):
    """A filter that matches nothing must return nothing.

    If an unmatched range fell through to "no filter", a user asking about a
    quiet month would be shown the whole history and believe it all happened
    then.
    """
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test",
                           headers=await _headers()) as client:
        body = await _orders(
            client, customer_with_history,
            date_from=str(date.today() - timedelta(days=395)),
            date_to=str(date.today() - timedelta(days=370)))

    assert body["items"] == []
    assert body["total"] == 0
