"""Roll-out control for automatic posting.

Wiring the hooks changes what happens on every sale. These tests cover the
three things that make that safe: posting is off unless explicitly enabled,
it never double-posts, and it never posts history it cannot defend.
"""
import os
import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

import app.services.posting as posting
from app.services.posting import (
    post_sale, post_purchase, already_posted, posting_enabled)
from app.services.ledger import trial_balance

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")

SCHEMA = """
DROP TABLE IF EXISTS sales_order_lines CASCADE;
DROP TABLE IF EXISTS sales_orders CASCADE;
CREATE TABLE sales_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_number VARCHAR(64) UNIQUE NOT NULL,
    order_date TIMESTAMPTZ DEFAULT NOW(),
    total_amount NUMERIC(18,2) DEFAULT 0
);
CREATE TABLE sales_order_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sales_order_id UUID NOT NULL REFERENCES sales_orders(id),
    product_id UUID NOT NULL,
    quantity NUMERIC(18,6) NOT NULL,
    unit_price NUMERIC(18,6) NOT NULL,
    line_total NUMERIC(18,2) NOT NULL,
    unit_cost NUMERIC(18,6), cost_total NUMERIC(18,2),
    cost_source VARCHAR(32)
);
"""


def _apply_gl(conn):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / \
        "m2345678901l_general_ledger.py"
    spec = importlib.util.spec_from_file_location("mig_m3", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with Operations.context(MigrationContext.configure(conn)):
        mod.upgrade()


@pytest_asyncio.fixture
async def engine():
    seng = create_engine(TEST_DB.replace("+asyncpg", ""), future=True)
    with seng.connect() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        for t in ("gl_journal_lines", "gl_journal_entries", "gl_periods",
                  "gl_accounts"):
            c.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        for stmt in SCHEMA.strip().split(";"):
            if stmt.strip():
                c.execute(text(stmt))
        c.commit()
        _apply_gl(c)
        c.commit()
    seng.dispose()
    eng = create_async_engine(TEST_DB, future=True)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine):
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s


@pytest.fixture
def posting_on(monkeypatch):
    monkeypatch.setenv("ACCOUNTING_POSTING_ENABLED", "true")
    monkeypatch.delenv("ACCOUNTING_CUTOVER_DATE", raising=False)


@pytest.fixture
def posting_off(monkeypatch):
    monkeypatch.delenv("ACCOUNTING_POSTING_ENABLED", raising=False)


async def make_order(session, *, on=date(2026, 7, 20), revenue="1000.00"):
    oid = uuid.uuid4()
    num = f"SO-{uuid.uuid4().hex[:8].upper()}"
    await session.execute(text("""
        INSERT INTO sales_orders (id, order_number, order_date, total_amount)
        VALUES (:i, :n, :d, :t)
    """), {"i": str(oid), "n": num, "d": on, "t": revenue})
    await session.execute(text("""
        INSERT INTO sales_order_lines
            (id, sales_order_id, product_id, quantity, unit_price,
             line_total, unit_cost, cost_total, cost_source)
        VALUES (gen_random_uuid(), :o, :p, 1, :t, :t, 400, 400, 'wac_global')
    """), {"o": str(oid), "p": str(uuid.uuid4()), "t": revenue})
    await session.commit()
    return oid, num


async def entry_count(session):
    return (await session.execute(
        text("SELECT COUNT(*) FROM gl_journal_entries"))).scalar()


# ---------------------------------------------------------------------------
# Default off
# ---------------------------------------------------------------------------

def test_posting_disabled_by_default(posting_off):
    assert posting_enabled() is False


@pytest.mark.asyncio
async def test_nothing_posts_when_disabled(session, posting_off):
    """Deploying this code must not start writing to the ledger on its own."""
    oid, _ = await make_order(session)
    result = await post_sale(session, order_id=oid)
    await session.commit()
    assert result is None
    assert await entry_count(session) == 0


@pytest.mark.asyncio
async def test_posts_when_enabled(session, posting_on):
    oid, _ = await make_order(session)
    result = await post_sale(session, order_id=oid)
    await session.commit()
    assert result is not None
    assert await entry_count(session) == 1


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_same_sale_does_not_post_twice(session, posting_on):
    """A retried request or double-clicked button must not double the books.

    Posted entries are immutable, so a duplicate is corrected by a manual
    reversal rather than a delete -- worth preventing at the source.
    """
    oid, num = await make_order(session)
    first = await post_sale(session, order_id=oid)
    await session.commit()
    second = await post_sale(session, order_id=oid)
    await session.commit()

    assert first is not None
    assert second is None, "second post should be suppressed"
    assert await entry_count(session) == 1

    tb = await trial_balance(session)
    assert tb["balanced"]
    by_code = {a["code"]: a for a in tb["accounts"]}
    assert by_code["4100"]["balance"] == 1000.00, "revenue must not double"


@pytest.mark.asyncio
async def test_already_posted_detects_existing_entry(session, posting_on):
    oid, num = await make_order(session)
    assert not await already_posted(
        session, source_module="sales", source_reference=num)
    await post_sale(session, order_id=oid)
    await session.commit()
    assert await already_posted(
        session, source_module="sales", source_reference=num)


@pytest.mark.asyncio
async def test_distinct_events_both_post(session, posting_on):
    """The guard must not be so broad that it blocks legitimate activity."""
    o1, _ = await make_order(session)
    o2, _ = await make_order(session)
    assert await post_sale(session, order_id=o1) is not None
    assert await post_sale(session, order_id=o2) is not None
    await session.commit()
    assert await entry_count(session) == 2


@pytest.mark.asyncio
async def test_repeated_purchases_with_distinct_refs_both_post(
        session, posting_on):
    await post_purchase(session, value="100.00", reference="GRN-1",
                        on=date(2026, 7, 20))
    await post_purchase(session, value="100.00", reference="GRN-2",
                        on=date(2026, 7, 20))
    await session.commit()
    assert await entry_count(session) == 2


# ---------------------------------------------------------------------------
# Cutover
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_events_before_cutover_are_not_posted(session, monkeypatch):
    """Historical orders carry backfilled cost ESTIMATES. Posting them would
    fill the ledger with figures nobody can defend."""
    monkeypatch.setenv("ACCOUNTING_POSTING_ENABLED", "true")
    monkeypatch.setenv("ACCOUNTING_CUTOVER_DATE", "2026-08-01")

    old, _ = await make_order(session, on=date(2026, 7, 20))
    assert await post_sale(session, order_id=old) is None
    await session.commit()
    assert await entry_count(session) == 0


@pytest.mark.asyncio
async def test_events_on_or_after_cutover_are_posted(session, monkeypatch):
    monkeypatch.setenv("ACCOUNTING_POSTING_ENABLED", "true")
    monkeypatch.setenv("ACCOUNTING_CUTOVER_DATE", "2026-08-01")

    on_day, _ = await make_order(session, on=date(2026, 8, 1))
    later, _ = await make_order(session, on=date(2026, 8, 15))
    assert await post_sale(session, order_id=on_day) is not None
    assert await post_sale(session, order_id=later) is not None
    await session.commit()
    assert await entry_count(session) == 2


def test_malformed_cutover_refuses_rather_than_posting_everything(monkeypatch):
    """A typo'd date must not silently degrade to 'post all history'."""
    monkeypatch.setenv("ACCOUNTING_POSTING_ENABLED", "true")
    monkeypatch.setenv("ACCOUNTING_CUTOVER_DATE", "01/08/2026")
    with pytest.raises(RuntimeError, match="not a valid YYYY-MM-DD"):
        posting._cutover_date()


# ---------------------------------------------------------------------------
# Atomicity
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_posting_failure_rolls_back_the_business_event(
        session, posting_on):
    """The journal shares the caller's transaction, so a posting failure must
    take the event with it. Stock must never move without the books knowing.
    """
    oid, num = await make_order(session)

    # Break the chart of accounts so posting cannot succeed.
    await session.execute(text(
        "UPDATE gl_accounts SET is_active = FALSE WHERE code = '4100'"))
    await session.commit()

    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        await post_sale(session, order_id=oid)
    await session.rollback()

    assert await entry_count(session) == 0
    # And the guard still reports it as unposted, so a retry can succeed
    # once the account is reinstated.
    assert not await already_posted(
        session, source_module="sales", source_reference=num)


# ---------------------------------------------------------------------------
# Business-day dating
# ---------------------------------------------------------------------------

def test_business_date_uses_local_day_not_utc():
    """Regression: a Lagos sale at 00:30 on 1 August is stored as 23:30 on
    31 July UTC. Taking .date() on that posted it to JULY -- the previous
    month, potentially a closed period. Accounting dates follow the business
    day, not the server clock.
    """
    from datetime import datetime, timezone as tz
    from app.services.posting import business_date

    just_after_midnight_lagos = datetime(2026, 7, 31, 23, 30, tzinfo=tz.utc)
    assert business_date(just_after_midnight_lagos) == date(2026, 8, 1)

    midday = datetime(2026, 8, 15, 12, 0, tzinfo=tz.utc)
    assert business_date(midday) == date(2026, 8, 15)


def test_business_date_passes_through_plain_dates():
    from app.services.posting import business_date
    assert business_date(date(2026, 8, 1)) == date(2026, 8, 1)
    assert business_date(None) is None


def test_unknown_business_timezone_refuses(monkeypatch):
    """Must not silently fall back to UTC dating."""
    import app.services.posting as p
    from datetime import datetime, timezone as tz
    monkeypatch.setattr(p, "BUSINESS_TZ", "Not/AZone")
    with pytest.raises(RuntimeError, match="not a valid timezone"):
        p.business_date(datetime(2026, 8, 1, 12, 0, tzinfo=tz.utc))
