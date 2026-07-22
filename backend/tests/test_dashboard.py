"""Executive dashboard.

Properties guarded:
  * every headline figure agrees with the report it summarises (the dashboard
    originates nothing);
  * profit is split into month and year-to-date correctly;
  * the dashboard surfaces its own uncertainty -- unallocated cost, overdue
    receivables, a missing budget -- as warnings rather than hiding it.
"""
import os
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.services.dashboard import executive_summary
from app.services.ledger import Line, post_entry, profit_and_loss

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")


def _apply(conn, name):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / name
    spec = importlib.util.spec_from_file_location(f"m_{name[:6]}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with Operations.context(MigrationContext.configure(conn)):
        mod.upgrade()


@pytest_asyncio.fixture
async def engine():
    seng = create_engine(TEST_DB.replace("+asyncpg", ""), future=True)
    with seng.connect() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        for t in ("budget_lines", "budgets", "cost_centres", "invoices",
                  "gl_journal_lines", "gl_journal_entries", "gl_periods",
                  "gl_accounts"):
            c.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        c.commit()
        _apply(c, "m2345678901l_general_ledger.py")
        _apply(c, "q6789012345p_budgeting.py")
        # Minimal invoices for the receivables figure.
        c.execute(text("""
            CREATE TABLE invoices (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                invoice_number VARCHAR(100),
                total_amount NUMERIC(18,2) DEFAULT 0,
                paid_amount NUMERIC(18,2) DEFAULT 0,
                due_date DATE, status VARCHAR(30) DEFAULT 'unpaid')
        """))
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


@pytest.fixture(autouse=True)
def _posting_on(monkeypatch):
    monkeypatch.setenv("ACCOUNTING_POSTING_ENABLED", "true")
    monkeypatch.delenv("ACCOUNTING_CUTOVER_DATE", raising=False)


async def _sale(session, revenue, on):
    await post_entry(
        session, entry_date=on, description="Sale", source_module="sales",
        lines=[Line("1300", debit=revenue), Line("4100", credit=revenue)])


async def _expense(session, code, amount, on, cc=None):
    await post_entry(
        session, entry_date=on, description="Cost", source_module="test",
        lines=[Line(code, debit=amount, cost_centre=cc),
               Line("1200", credit=amount)])


@pytest.mark.asyncio
async def test_profit_matches_the_pnl_report(session):
    as_at = date(2026, 7, 20)
    await _sale(session, "500000", date(2026, 7, 5))
    await _expense(session, "6100", "120000", date(2026, 7, 6), cc="ADMIN")
    await session.commit()

    dash = await executive_summary(session, as_at=as_at)
    pnl = await profit_and_loss(
        session, start=date(2026, 7, 1), end=date(2026, 7, 31))
    assert dash["profitability"]["month"]["net_profit"] == pnl["net_profit"]
    assert dash["profitability"]["month"]["net_profit"] == 380000.00


@pytest.mark.asyncio
async def test_ytd_includes_earlier_months_month_does_not(session):
    as_at = date(2026, 7, 20)
    await _sale(session, "100000", date(2026, 3, 5))   # earlier month
    await _sale(session, "200000", date(2026, 7, 5))   # this month
    await session.commit()

    dash = await executive_summary(session, as_at=as_at)
    assert dash["profitability"]["month"]["income"] == 200000.00
    assert dash["profitability"]["year_to_date"]["income"] == 300000.00


@pytest.mark.asyncio
async def test_receivables_and_overdue_are_surfaced(session):
    as_at = date(2026, 7, 20)
    await session.execute(text("""
        INSERT INTO invoices (invoice_number, total_amount, paid_amount,
                              due_date, status)
        VALUES ('INV-1', 100000, 20000, '2026-07-01', 'partial'),
               ('INV-2', 50000, 50000, '2026-07-01', 'paid')
    """))
    await session.commit()

    dash = await executive_summary(session, as_at=as_at)
    # 80000 outstanding on INV-1; INV-2 fully paid
    assert dash["working_capital"]["receivables"] == 80000.00
    assert dash["working_capital"]["overdue_receivables"]["amount"] == 80000.00
    assert any("overdue" in w for w in dash["warnings"])


@pytest.mark.asyncio
async def test_unallocated_spend_and_missing_budget_warn(session):
    as_at = date(2026, 7, 20)
    await _expense(session, "6600", "50000", date(2026, 7, 6))  # no cost centre
    await session.commit()

    dash = await executive_summary(session, as_at=as_at)
    assert dash["has_approved_budget"] is False
    assert any("no cost" in w.lower() for w in dash["warnings"])
    assert any("budget" in w.lower() for w in dash["warnings"])


@pytest.mark.asyncio
async def test_top_cost_centres_ranked_by_spend(session):
    as_at = date(2026, 7, 20)
    await _expense(session, "5410", "150000", date(2026, 7, 6), cc="PROD")
    await _expense(session, "6300", "90000", date(2026, 7, 7), cc="MKT")
    await session.commit()

    dash = await executive_summary(session, as_at=as_at)
    ccs = dash["top_cost_centres"]
    assert ccs[0]["cost_centre"] == "PROD"
    assert ccs[0]["spend"] == 150000.00
