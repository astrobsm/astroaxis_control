"""Double-entry ledger invariants.

The ledger is the system of record for the accounts, so its guarantees have to
hold absolutely: entries balance, posted entries are immutable, closed periods
reject posting, and the trial balance always balances.
"""
import os
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.services.ledger import (
    Line, post_entry, reverse_entry, trial_balance, profit_and_loss,
    balance_sheet, account_ledger, money)

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")


def _apply_migration(conn):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / \
        "m2345678901l_general_ledger.py"
    spec = importlib.util.spec_from_file_location("mig_m", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        mod.upgrade()


@pytest_asyncio.fixture
async def engine():
    from sqlalchemy import create_engine
    sync_url = TEST_DB.replace("+asyncpg", "")
    seng = create_engine(sync_url, future=True)
    with seng.connect() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        for t in ("gl_journal_lines", "gl_journal_entries",
                  "gl_periods", "gl_accounts"):
            c.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        c.commit()
        _apply_migration(c)
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


TODAY = date(2026, 7, 20)


# ---------------------------------------------------------------------------
# Balance by construction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_balanced_entry_posts(session):
    eid = await post_entry(
        session, entry_date=TODAY, description="Cash sale",
        source_module="test",
        lines=[Line("1100", debit="1000.00"),
               Line("4100", credit="1000.00")])
    await session.commit()
    assert eid is not None

    tb = await trial_balance(session)
    assert tb["balanced"]
    assert tb["total_debit"] == tb["total_credit"] == 1000.00


@pytest.mark.asyncio
async def test_unbalanced_entry_rejected(session):
    with pytest.raises(HTTPException) as exc:
        await post_entry(
            session, entry_date=TODAY, description="Broken",
            source_module="test",
            lines=[Line("1100", debit="1000.00"),
                   Line("4100", credit="900.00")])
    assert exc.value.status_code == 400
    assert "does not balance" in exc.value.detail
    await session.rollback()

    tb = await trial_balance(session)
    assert tb["total_debit"] == 0, "rejected entry must leave nothing behind"


@pytest.mark.asyncio
async def test_line_cannot_be_both_debit_and_credit(session):
    with pytest.raises(HTTPException) as exc:
        await post_entry(
            session, entry_date=TODAY, description="Both sides",
            source_module="test",
            lines=[Line("1100", debit="100.00", credit="100.00"),
                   Line("4100", credit="100.00")])
    assert "one or the other" in exc.value.detail


@pytest.mark.asyncio
async def test_negative_amounts_rejected(session):
    with pytest.raises(HTTPException) as exc:
        await post_entry(
            session, entry_date=TODAY, description="Negative",
            source_module="test",
            lines=[Line("1100", debit="-500.00"),
                   Line("4100", credit="-500.00")])
    assert "cannot be negative" in exc.value.detail


@pytest.mark.asyncio
async def test_unknown_account_rejected(session):
    with pytest.raises(HTTPException) as exc:
        await post_entry(
            session, entry_date=TODAY, description="Bad account",
            source_module="test",
            lines=[Line("9999", debit="10.00"), Line("4100", credit="10.00")])
    assert "does not exist" in exc.value.detail


@pytest.mark.asyncio
async def test_header_account_rejected(session):
    """Headers aggregate children; posting to them corrupts the hierarchy."""
    with pytest.raises(HTTPException) as exc:
        await post_entry(
            session, entry_date=TODAY, description="Post to header",
            source_module="test",
            lines=[Line("1000", debit="10.00"), Line("4100", credit="10.00")])
    assert "header" in exc.value.detail


@pytest.mark.asyncio
async def test_multi_line_entry_balances(session):
    """A sale with VAT: one debit, two credits."""
    await post_entry(
        session, entry_date=TODAY, description="Sale with VAT",
        source_module="test",
        lines=[Line("1300", debit="1075.00"),
               Line("4100", credit="1000.00"),
               Line("2300", credit="75.00")])
    await session.commit()
    tb = await trial_balance(session)
    assert tb["balanced"]
    assert tb["total_debit"] == 1075.00


# ---------------------------------------------------------------------------
# Immutability and reversal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reversal_mirrors_and_nets_to_zero(session):
    eid = await post_entry(
        session, entry_date=TODAY, description="Mistaken sale",
        source_module="test",
        lines=[Line("1100", debit="500.00"), Line("4100", credit="500.00")])
    await session.commit()

    await reverse_entry(session, entry_id=eid, reason="keyed twice", on=TODAY)
    await session.commit()

    tb = await trial_balance(session)
    assert tb["balanced"]
    # Every account nets to zero once reversed.
    for a in tb["accounts"]:
        assert a["balance"] == 0, f"{a['code']} did not net out"

    status = (await session.execute(text(
        "SELECT status FROM gl_journal_entries WHERE id = :i"),
        {"i": str(eid)})).scalar()
    assert status == 'REVERSED'


@pytest.mark.asyncio
async def test_original_entry_is_not_deleted_by_reversal(session):
    """Correcting the books must add history, never rewrite it."""
    eid = await post_entry(
        session, entry_date=TODAY, description="Original",
        source_module="test",
        lines=[Line("1100", debit="200.00"), Line("4100", credit="200.00")])
    await session.commit()
    await reverse_entry(session, entry_id=eid, reason="correction", on=TODAY)
    await session.commit()

    n = (await session.execute(text(
        "SELECT COUNT(*) FROM gl_journal_entries"))).scalar()
    lines = (await session.execute(text(
        "SELECT COUNT(*) FROM gl_journal_lines"))).scalar()
    assert n == 2, "reversal should ADD an entry, not replace one"
    assert lines == 4


@pytest.mark.asyncio
async def test_entry_cannot_be_reversed_twice(session):
    eid = await post_entry(
        session, entry_date=TODAY, description="Once only",
        source_module="test",
        lines=[Line("1100", debit="50.00"), Line("4100", credit="50.00")])
    await session.commit()
    await reverse_entry(session, entry_id=eid, reason="first", on=TODAY)
    await session.commit()

    with pytest.raises(HTTPException):
        await reverse_entry(session, entry_id=eid, reason="second", on=TODAY)
    await session.rollback()


@pytest.mark.asyncio
async def test_posted_lines_cannot_be_edited_to_unbalance(session):
    """The DB itself must reject a line with both sides set."""
    eid = await post_entry(
        session, entry_date=TODAY, description="Tamper target",
        source_module="test",
        lines=[Line("1100", debit="100.00"), Line("4100", credit="100.00")])
    await session.commit()

    with pytest.raises(Exception):
        await session.execute(text("""
            UPDATE gl_journal_lines SET credit = 50
             WHERE entry_id = :e AND debit > 0
        """), {"e": str(eid)})
        await session.commit()
    await session.rollback()


# ---------------------------------------------------------------------------
# Period locking
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_closed_period_rejects_posting(session):
    await session.execute(text("""
        INSERT INTO gl_periods (id, name, start_date, end_date, status)
        VALUES (gen_random_uuid(), 'Jul 2026', '2026-07-01', '2026-07-31',
                'CLOSED')
    """))
    await session.commit()

    with pytest.raises(HTTPException) as exc:
        await post_entry(
            session, entry_date=TODAY, description="Back-dated",
            source_module="test",
            lines=[Line("1100", debit="10.00"), Line("4100", credit="10.00")])
    assert "closed" in exc.value.detail
    await session.rollback()


@pytest.mark.asyncio
async def test_open_period_allows_posting(session):
    await session.execute(text("""
        INSERT INTO gl_periods (id, name, start_date, end_date, status)
        VALUES (gen_random_uuid(), 'Jul 2026', '2026-07-01', '2026-07-31',
                'OPEN')
    """))
    await session.commit()
    eid = await post_entry(
        session, entry_date=TODAY, description="In period",
        source_module="test",
        lines=[Line("1100", debit="10.00"), Line("4100", credit="10.00")])
    await session.commit()
    assert eid is not None


@pytest.mark.asyncio
async def test_periods_cannot_overlap(session):
    await session.execute(text("""
        INSERT INTO gl_periods (id, name, start_date, end_date)
        VALUES (gen_random_uuid(), 'P1', '2026-01-01', '2026-01-31')
    """))
    await session.commit()
    with pytest.raises(Exception):
        await session.execute(text("""
            INSERT INTO gl_periods (id, name, start_date, end_date)
            VALUES (gen_random_uuid(), 'P2', '2026-01-15', '2026-02-15')
        """))
        await session.commit()
    await session.rollback()


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_trading_cycle_reports_correctly(session):
    """Buy stock, sell it, collect cash -- then check the statements."""
    # 1. Buy 100 units of stock on credit for 10 each
    await post_entry(
        session, entry_date=TODAY, description="Purchase raw materials",
        source_module="procurement",
        lines=[Line("1410", debit="1000.00"), Line("2100", credit="1000.00")])
    # 2. Produce finished goods from them
    await post_entry(
        session, entry_date=TODAY, description="Production completion",
        source_module="production",
        lines=[Line("1430", debit="1000.00"), Line("1410", credit="1000.00")])
    # 3. Sell them for 2,500 on credit, recognising COGS
    await post_entry(
        session, entry_date=TODAY, description="Sale",
        source_module="sales",
        lines=[Line("1300", debit="2500.00"), Line("4100", credit="2500.00")])
    await post_entry(
        session, entry_date=TODAY, description="Cost of goods sold",
        source_module="sales",
        lines=[Line("5100", debit="1000.00"), Line("1430", credit="1000.00")])
    # 4. Customer pays
    await post_entry(
        session, entry_date=TODAY, description="Customer payment",
        source_module="payments",
        lines=[Line("1200", debit="2500.00"), Line("1300", credit="2500.00")])
    await session.commit()

    tb = await trial_balance(session)
    assert tb["balanced"], tb["difference"]

    pl = await profit_and_loss(session)
    assert pl["total_income"] == 2500.00
    assert pl["total_expenses"] == 1000.00
    assert pl["net_profit"] == 1500.00

    bs = await balance_sheet(session)
    # Bank 2,500; inventory fully consumed; payable 1,000; profit 1,500.
    assert bs["assets"] == 2500.00
    assert bs["liabilities"] == 1000.00
    assert bs["current_period_earnings"] == 1500.00
    assert bs["balanced"], bs["difference"]


@pytest.mark.asyncio
async def test_balance_sheet_equation_holds_after_reversal(session):
    eid = await post_entry(
        session, entry_date=TODAY, description="Sale",
        source_module="sales",
        lines=[Line("1300", debit="800.00"), Line("4100", credit="800.00")])
    await session.commit()
    await reverse_entry(session, entry_id=eid, reason="cancelled", on=TODAY)
    await session.commit()

    bs = await balance_sheet(session)
    assert bs["balanced"]
    assert bs["assets"] == 0
    assert bs["current_period_earnings"] == 0


@pytest.mark.asyncio
async def test_account_ledger_running_balance(session):
    for amt in ("100.00", "250.00", "50.00"):
        await post_entry(
            session, entry_date=TODAY, description=f"Sale {amt}",
            source_module="sales",
            lines=[Line("1100", debit=amt), Line("4100", credit=amt)])
    await session.commit()

    led = await account_ledger(session, account_code="1100")
    assert [e["balance"] for e in led["entries"]] == [100.0, 350.0, 400.0]
    assert led["closing_balance"] == 400.00


@pytest.mark.asyncio
async def test_period_filtered_reports(session):
    await post_entry(
        session, entry_date=date(2026, 6, 15), description="June sale",
        source_module="sales",
        lines=[Line("1100", debit="100.00"), Line("4100", credit="100.00")])
    await post_entry(
        session, entry_date=date(2026, 7, 15), description="July sale",
        source_module="sales",
        lines=[Line("1100", debit="300.00"), Line("4100", credit="300.00")])
    await session.commit()

    july = await profit_and_loss(
        session, start=date(2026, 7, 1), end=date(2026, 7, 31))
    assert july["total_income"] == 300.00

    both = await profit_and_loss(session)
    assert both["total_income"] == 400.00


@pytest.mark.asyncio
async def test_contra_account_reduces_income(session):
    """Sales returns is an INCOME account with a DEBIT normal balance."""
    await post_entry(
        session, entry_date=TODAY, description="Sale",
        source_module="sales",
        lines=[Line("1300", debit="1000.00"), Line("4100", credit="1000.00")])
    await post_entry(
        session, entry_date=TODAY, description="Customer return",
        source_module="sales",
        lines=[Line("4900", debit="200.00"), Line("1300", credit="200.00")])
    await session.commit()

    pl = await profit_and_loss(session)
    # 1000 credited to sales, 200 debited to returns -> net income 800.
    assert pl["total_income"] == 800.00
    tb = await trial_balance(session)
    assert tb["balanced"]


@pytest.mark.asyncio
async def test_rounding_is_exact_over_many_entries(session):
    """Float would drift here; Decimal must not."""
    for _ in range(100):
        await post_entry(
            session, entry_date=TODAY, description="Small sale",
            source_module="sales",
            lines=[Line("1100", debit="0.01"), Line("4100", credit="0.01")])
    await session.commit()

    tb = await trial_balance(session)
    assert tb["total_debit"] == 1.00
    assert tb["balanced"]
    assert tb["difference"] == 0.0
