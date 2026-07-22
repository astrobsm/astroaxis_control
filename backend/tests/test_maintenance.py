"""Maintenance and QC costing via app.services.opex.

Properties guarded:
  * an operating cost lands as Dr expense (to a cost centre) / Cr cash|bank|AP;
  * the cost centre is validated, not trusted as free text;
  * posting is idempotent on the reference and gated by the posting switch;
  * paid_from selects the correct credit account.
"""
import os
from datetime import date

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.services.opex import post_operational_cost
from app.services.ledger import account_ledger
from app.services.budgeting import cost_centre_report

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
        for t in ("budget_lines", "budgets", "cost_centres", "qc_inspections",
                  "vat_returns", "gl_journal_lines", "gl_journal_entries",
                  "gl_periods", "gl_accounts"):
            c.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        c.commit()
        _apply(c, "m2345678901l_general_ledger.py")
        _apply(c, "q6789012345p_budgeting.py")   # cost centre master
        _apply(c, "r7890123456q_tax_qc_costing.py")
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


@pytest.mark.asyncio
async def test_maintenance_cost_lands_on_expense_and_cost_centre(session):
    await post_operational_cost(
        session, expense_account="5450", amount="45000",
        reference="MAINT-1", description="Mixer service",
        cost_centre="MAINT", source_module="maintenance", paid_from="bank",
        on=date(2026, 7, 10))
    await session.commit()

    led = await account_ledger(session, account_code="5450")
    assert led["closing_balance"] == 45000.00

    rep = await cost_centre_report(
        session, start=date(2026, 7, 1), end=date(2026, 7, 31))
    maint = {c["cost_centre"]: c for c in rep["cost_centres"]}["MAINT"]
    assert maint["expenditure"] == 45000.00


@pytest.mark.asyncio
async def test_qc_cost_lands_on_qc_account_and_centre(session):
    await post_operational_cost(
        session, expense_account="6950", amount="12000",
        reference="QC-1", description="Batch sterility test",
        cost_centre="QC", source_module="qc", paid_from="cash",
        on=date(2026, 7, 12))
    await session.commit()

    led = await account_ledger(session, account_code="6950")
    assert led["closing_balance"] == 12000.00
    # paid in cash, so cash (1100) was credited
    cash = await account_ledger(session, account_code="1100")
    assert cash["closing_balance"] == -12000.00


@pytest.mark.asyncio
async def test_unknown_cost_centre_is_refused(session):
    with pytest.raises(HTTPException) as exc:
        await post_operational_cost(
            session, expense_account="5450", amount="1000",
            reference="MAINT-X", description="x", cost_centre="NOPE",
            source_module="maintenance")
    assert "not defined" in exc.value.detail


@pytest.mark.asyncio
async def test_posting_is_idempotent_on_reference(session):
    for _ in range(2):
        await post_operational_cost(
            session, expense_account="5450", amount="45000",
            reference="MAINT-DUP", description="Mixer service",
            cost_centre="MAINT", source_module="maintenance",
            on=date(2026, 7, 10))
    await session.commit()
    led = await account_ledger(session, account_code="5450")
    # posted once despite two calls
    assert led["closing_balance"] == 45000.00


@pytest.mark.asyncio
async def test_nothing_posts_when_disabled(session, monkeypatch):
    monkeypatch.setenv("ACCOUNTING_POSTING_ENABLED", "false")
    entry = await post_operational_cost(
        session, expense_account="5450", amount="45000",
        reference="MAINT-2", description="x", cost_centre="MAINT",
        source_module="maintenance")
    await session.commit()
    assert entry is None
    led = await account_ledger(session, account_code="5450")
    assert led["closing_balance"] == 0.0


@pytest.mark.asyncio
async def test_bad_paid_from_is_rejected(session):
    with pytest.raises(HTTPException) as exc:
        await post_operational_cost(
            session, expense_account="5450", amount="1000",
            reference="MAINT-Y", description="x", cost_centre="MAINT",
            source_module="maintenance", paid_from="cheque")
    assert "paid_from" in exc.value.detail


@pytest.mark.asyncio
async def test_zero_cost_posts_nothing(session):
    entry = await post_operational_cost(
        session, expense_account="5450", amount=0,
        reference="MAINT-0", description="x", cost_centre="MAINT",
        source_module="maintenance")
    assert entry is None
