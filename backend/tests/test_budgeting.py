"""Cost centres, budgets and variance.

The properties being guarded:
  * cost centre codes collapse case-insensitively (the free-text defect);
  * variance is interpreted, not just signed -- under-spending is good,
    under-earning is bad, and both are "negative";
  * only an APPROVED budget is reported against, and only one per year;
  * unbudgeted spend and unallocated postings are surfaced, not dropped.
"""
import os
import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.services.budgeting import (
    classify_variance, resolve_cost_centre, cost_centre_report,
    approve_budget, budget_variance, money)
from app.services.ledger import Line, post_entry

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
        for t in ("budget_lines", "budgets", "cost_centres",
                  "gl_journal_lines", "gl_journal_entries", "gl_periods",
                  "gl_accounts"):
            c.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        c.commit()
        _apply(c, "m2345678901l_general_ledger.py")
        _apply(c, "q6789012345p_budgeting.py")
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


async def make_budget(session, *, year=2026, status='DRAFT', lines=None):
    bid = uuid.uuid4()
    await session.execute(text("""
        INSERT INTO budgets (id, name, fiscal_year, period_start, period_end,
                             status, approved_by)
        VALUES (:i, :n, :y, :s, :e, :st, :by)
    """), {"i": str(bid), "n": f"Budget {year}", "y": year,
           "s": date(year, 1, 1), "e": date(year, 12, 31), "st": status,
           "by": "test" if status == 'APPROVED' else None})
    for acct, cc, month, amt in (lines or []):
        await session.execute(text("""
            INSERT INTO budget_lines
                (budget_id, account_code, cost_centre, period_month, amount)
            VALUES (:b, :a, :c, :m, :amt)
        """), {"b": str(bid), "a": acct, "c": cc, "m": month, "amt": amt})
    await session.commit()
    return bid


# ---------------------------------------------------------------------------
# Variance interpretation
# ---------------------------------------------------------------------------

def test_overspending_is_adverse():
    v, verdict = classify_variance("EXPENSE", Decimal("100000"),
                                   Decimal("130000"))
    assert v == Decimal("30000.00")
    assert verdict == "ADVERSE"


def test_underspending_is_favourable():
    v, verdict = classify_variance("EXPENSE", Decimal("100000"),
                                   Decimal("80000"))
    assert v == Decimal("-20000.00")
    assert verdict == "FAVOURABLE"


def test_under_earning_is_adverse_despite_same_sign_as_underspending():
    """The reason verdicts exist: both are negative variances, but one is
    good news and the other is bad. A bare minus sign cannot say which."""
    spend_v, spend_verdict = classify_variance(
        "EXPENSE", Decimal("100000"), Decimal("80000"))
    earn_v, earn_verdict = classify_variance(
        "INCOME", Decimal("100000"), Decimal("80000"))
    assert spend_v == earn_v == Decimal("-20000.00")
    assert spend_verdict == "FAVOURABLE"
    assert earn_verdict == "ADVERSE"


def test_over_earning_is_favourable():
    _, verdict = classify_variance("INCOME", Decimal("100000"),
                                   Decimal("150000"))
    assert verdict == "FAVOURABLE"


def test_exact_match_is_on_budget():
    v, verdict = classify_variance("EXPENSE", Decimal("100000"),
                                   Decimal("100000"))
    assert v == Decimal("0.00")
    assert verdict == "ON_BUDGET"


# ---------------------------------------------------------------------------
# Cost centre master
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cost_centre_codes_resolve_case_insensitively(session):
    """Regression: free text meant 'PROD', 'prod' and ' Prod ' reported as
    three separate cost centres, silently splitting departmental costs."""
    for variant in ("PROD", "prod", "  Prod  "):
        assert await resolve_cost_centre(session, code=variant) == "PROD"


@pytest.mark.asyncio
async def test_unknown_cost_centre_is_refused(session):
    with pytest.raises(HTTPException) as exc:
        await resolve_cost_centre(session, code="NOT_A_CENTRE")
    assert "not defined" in exc.value.detail


@pytest.mark.asyncio
async def test_blank_cost_centre_is_refused(session):
    with pytest.raises(HTTPException):
        await resolve_cost_centre(session, code="   ")


@pytest.mark.asyncio
async def test_cost_centre_report_splits_by_centre(session):
    await post_entry(
        session, entry_date=date(2026, 7, 5), description="Factory power",
        source_module="test",
        lines=[Line("5410", debit="150000", cost_centre="PROD"),
               Line("1200", credit="150000")])
    await post_entry(
        session, entry_date=date(2026, 7, 6), description="Marketing spend",
        source_module="test",
        lines=[Line("6300", debit="90000", cost_centre="MKT"),
               Line("1200", credit="90000")])
    await session.commit()

    rep = await cost_centre_report(
        session, start=date(2026, 7, 1), end=date(2026, 7, 31))
    by_cc = {c["cost_centre"]: c for c in rep["cost_centres"]}
    assert by_cc["PROD"]["expenditure"] == 150000.00
    assert by_cc["MKT"]["expenditure"] == 90000.00
    assert rep["total_expenditure"] == 240000.00


@pytest.mark.asyncio
async def test_unallocated_postings_are_surfaced_not_dropped(session):
    """Cost with no centre is a real finding: somebody posted without saying
    which department bore it."""
    await post_entry(
        session, entry_date=date(2026, 7, 5), description="Unattributed",
        source_module="test",
        lines=[Line("6600", debit="50000"), Line("1200", credit="50000")])
    await session.commit()

    rep = await cost_centre_report(
        session, start=date(2026, 7, 1), end=date(2026, 7, 31))
    by_cc = {c["cost_centre"]: c for c in rep["cost_centres"]}
    assert "UNALLOCATED" in by_cc
    assert by_cc["UNALLOCATED"]["expenditure"] == 50000.00
    assert rep["unallocated_warning"] is not None
    assert "cannot be attributed" in rep["unallocated_warning"]


# ---------------------------------------------------------------------------
# Budget approval
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_draft_budget_is_not_reported_against(session):
    """Reporting against a draft would let the comparison shift while
    somebody is still editing it."""
    await make_budget(session, year=2026, status='DRAFT',
                      lines=[("5410", "PROD", date(2026, 7, 1), "100000")])
    with pytest.raises(HTTPException) as exc:
        await budget_variance(session, fiscal_year=2026)
    assert "No approved budget" in exc.value.detail


@pytest.mark.asyncio
async def test_approving_supersedes_the_previous_budget(session):
    """Two approved budgets would make 'are we over budget?' unanswerable."""
    first = await make_budget(session, year=2026, status='APPROVED')
    second = await make_budget(session, year=2026, status='DRAFT')

    result = await approve_budget(
        session, budget_id=second, approved_by="Finance Director")
    await session.commit()

    assert len(result["superseded"]) == 1
    statuses = {
        str(r.id): r.status for r in (await session.execute(
            text("SELECT id, status FROM budgets"))).fetchall()}
    assert statuses[str(first)] == 'SUPERSEDED'
    assert statuses[str(second)] == 'APPROVED'


@pytest.mark.asyncio
async def test_only_one_approved_budget_per_year_at_database_level(session):
    await make_budget(session, year=2026, status='APPROVED')
    with pytest.raises(Exception):
        await session.execute(text("""
            INSERT INTO budgets (name, fiscal_year, period_start, period_end,
                                 status, approved_by)
            VALUES ('Second', 2026, '2026-01-01', '2026-12-31',
                    'APPROVED', 'x')
        """))
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_approved_budget_requires_an_author(session):
    with pytest.raises(Exception):
        await session.execute(text("""
            INSERT INTO budgets (name, fiscal_year, period_start, period_end,
                                 status)
            VALUES ('No author', 2027, '2027-01-01', '2027-12-31', 'APPROVED')
        """))
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_cannot_approve_twice(session):
    bid = await make_budget(session, year=2026, status='APPROVED')
    with pytest.raises(HTTPException) as exc:
        await approve_budget(session, budget_id=bid, approved_by="x")
    assert "already approved" in exc.value.detail


# ---------------------------------------------------------------------------
# Variance reporting
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_variance_against_actuals(session):
    await make_budget(session, year=2026, status='APPROVED', lines=[
        ("5410", "PROD", date(2026, 7, 1), "100000"),
        ("6300", "MKT", date(2026, 7, 1), "80000"),
    ])
    await post_entry(
        session, entry_date=date(2026, 7, 10), description="Power",
        source_module="test",
        lines=[Line("5410", debit="130000", cost_centre="PROD"),
               Line("1200", credit="130000")])
    await post_entry(
        session, entry_date=date(2026, 7, 11), description="Ads",
        source_module="test",
        lines=[Line("6300", debit="60000", cost_centre="MKT"),
               Line("1200", credit="60000")])
    await session.commit()

    var = await budget_variance(
        session, fiscal_year=2026,
        start=date(2026, 7, 1), end=date(2026, 7, 31))

    by_key = {(ln["account_code"], ln["cost_centre"]): ln
              for ln in var["lines"]}
    power = by_key[("5410", "PROD")]
    assert power["budget"] == 100000.00
    assert power["actual"] == 130000.00
    assert power["variance"] == 30000.00
    assert power["verdict"] == "ADVERSE"

    ads = by_key[("6300", "MKT")]
    assert ads["variance"] == -20000.00
    assert ads["verdict"] == "FAVOURABLE"

    assert var["adverse_count"] == 1


@pytest.mark.asyncio
async def test_unbudgeted_spend_is_surfaced(session):
    """Spend with no budget line is exactly what a variance report exists
    to catch -- it must not be silently omitted."""
    await make_budget(session, year=2026, status='APPROVED', lines=[
        ("5410", "PROD", date(2026, 7, 1), "100000")])
    await post_entry(
        session, entry_date=date(2026, 7, 10), description="Unplanned legal",
        source_module="test",
        lines=[Line("6700", debit="250000", cost_centre="ADMIN"),
               Line("1200", credit="250000")])
    await session.commit()

    var = await budget_variance(
        session, fiscal_year=2026,
        start=date(2026, 7, 1), end=date(2026, 7, 31))

    unbudgeted = var["unbudgeted_spend"]
    assert len(unbudgeted) == 1
    assert unbudgeted[0]["amount"] == 250000.00
    assert var["unbudgeted_warning"] is not None


@pytest.mark.asyncio
async def test_budget_line_with_no_spend_still_reported(session):
    """A budget nobody spent against is as informative as overspend."""
    await make_budget(session, year=2026, status='APPROVED', lines=[
        ("6900", "RND", date(2026, 7, 1), "500000")])
    await session.commit()

    var = await budget_variance(
        session, fiscal_year=2026,
        start=date(2026, 7, 1), end=date(2026, 7, 31))
    line = var["lines"][0]
    assert line["budget"] == 500000.00
    assert line["actual"] == 0.0
    assert line["verdict"] == "FAVOURABLE"


@pytest.mark.asyncio
async def test_variance_filtered_by_cost_centre(session):
    await make_budget(session, year=2026, status='APPROVED', lines=[
        ("5410", "PROD", date(2026, 7, 1), "100000"),
        ("6300", "MKT", date(2026, 7, 1), "80000"),
    ])
    await post_entry(
        session, entry_date=date(2026, 7, 10), description="Power",
        source_module="test",
        lines=[Line("5410", debit="130000", cost_centre="PROD"),
               Line("1200", credit="130000")])
    await session.commit()

    var = await budget_variance(
        session, fiscal_year=2026, start=date(2026, 7, 1),
        end=date(2026, 7, 31), cost_centre="PROD")
    assert all(ln["cost_centre"] == "PROD" for ln in var["lines"])
    assert var["total_budget"] == 100000.00


@pytest.mark.asyncio
async def test_variance_only_covers_the_requested_months(session):
    await make_budget(session, year=2026, status='APPROVED', lines=[
        ("5410", "PROD", date(2026, 7, 1), "100000"),
        ("5410", "PROD", date(2026, 8, 1), "100000"),
    ])
    await session.commit()

    july = await budget_variance(
        session, fiscal_year=2026,
        start=date(2026, 7, 1), end=date(2026, 7, 31))
    assert july["total_budget"] == 100000.00

    both = await budget_variance(
        session, fiscal_year=2026,
        start=date(2026, 7, 1), end=date(2026, 8, 31))
    assert both["total_budget"] == 200000.00
