"""Fixed assets, depreciation and cash flow.

The properties being guarded:
  * an asset never depreciates below its residual value;
  * the total charged over an asset's life equals cost minus residual
    EXACTLY -- rounding must not strand kobo or overshoot;
  * a depreciation run is idempotent (a retry cannot double-charge);
  * disposal removes both cost and accumulated depreciation, and recognises
    the gain or loss rather than absorbing it;
  * the cash flow statement reconciles to the actual cash balance.
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

from app.services.assets import (
    monthly_charge, run_depreciation, dispose_asset, asset_register,
    accumulated_depreciation, carrying_amount, money,
    STRAIGHT_LINE, REDUCING_BALANCE)
from app.services.ledger import (
    Line, post_entry, trial_balance, balance_sheet, cash_flow_statement,
    cash_position)

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")

SCHEMA = """
DROP TABLE IF EXISTS asset_depreciation_charges CASCADE;
DROP TABLE IF EXISTS depreciation_runs CASCADE;
DROP TABLE IF EXISTS fixed_assets CASCADE;
DROP TABLE IF EXISTS machines_equipment CASCADE;
CREATE TABLE machines_equipment (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255), purchase_date DATE, purchase_cost NUMERIC(18,2),
    current_value NUMERIC(18,2), depreciation_rate NUMERIC(10,2),
    depreciation_method VARCHAR(50), location VARCHAR(255),
    serial_number VARCHAR(128)
);
"""


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
        for t in ("gl_journal_lines", "gl_journal_entries", "gl_periods",
                  "gl_accounts"):
            c.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        for stmt in SCHEMA.strip().split(";"):
            if stmt.strip():
                c.execute(text(stmt))
        c.commit()
        _apply(c, "m2345678901l_general_ledger.py")
        _apply(c, "p5678901234o_fixed_assets.py")
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


async def make_asset(session, *, cost="1200000", residual="0", life=60,
                     method=STRAIGHT_LINE, rate=None,
                     acq=date(2026, 1, 1)):
    aid = uuid.uuid4()
    num = f"FA-{uuid.uuid4().hex[:8].upper()}"
    await session.execute(text("""
        INSERT INTO fixed_assets
            (id, asset_number, name, acquisition_date, cost, residual_value,
             useful_life_months, method, annual_rate_percent)
        VALUES (:i, :n, 'Test Asset', :a, :c, :r, :l, :m, :rate)
    """), {"i": str(aid), "n": num, "a": acq, "c": cost, "r": residual,
           "l": life, "m": method, "rate": rate})
    await session.commit()
    return aid, num


# ---------------------------------------------------------------------------
# Depreciation arithmetic
# ---------------------------------------------------------------------------

def test_straight_line_monthly_charge():
    # (1,200,000 - 0) / 60 = 20,000 a month
    assert monthly_charge(
        method=STRAIGHT_LINE, cost=Decimal("1200000"),
        residual=Decimal("0"), useful_life_months=60,
        annual_rate_percent=None, accumulated=Decimal("0")
    ) == Decimal("20000.00")


def test_residual_value_is_excluded_from_the_depreciable_amount():
    """Depreciating the full cost when a residual is expected over-charges."""
    # (1,200,000 - 200,000) / 60 = 16,666.67
    assert monthly_charge(
        method=STRAIGHT_LINE, cost=Decimal("1200000"),
        residual=Decimal("200000"), useful_life_months=60,
        annual_rate_percent=None, accumulated=Decimal("0")
    ) == Decimal("16666.67")


def test_never_depreciates_below_residual():
    """The floor that stops an asset going to zero when it should not."""
    charge = monthly_charge(
        method=STRAIGHT_LINE, cost=Decimal("100000"),
        residual=Decimal("10000"), useful_life_months=10,
        annual_rate_percent=None,
        accumulated=Decimal("85000"),     # only 5,000 of 90,000 remains
    )
    assert charge == Decimal("5000.00"), "must charge only what remains"


def test_fully_depreciated_asset_charges_nothing():
    assert monthly_charge(
        method=STRAIGHT_LINE, cost=Decimal("100000"),
        residual=Decimal("0"), useful_life_months=10,
        annual_rate_percent=None, accumulated=Decimal("100000")
    ) == Decimal("0.00")


def test_reducing_balance_charges_on_carrying_amount():
    # 24% annual = 2% monthly on 1,000,000 carrying = 20,000
    assert monthly_charge(
        method=REDUCING_BALANCE, cost=Decimal("1000000"),
        residual=Decimal("0"), useful_life_months=60,
        annual_rate_percent=Decimal("24"), accumulated=Decimal("0")
    ) == Decimal("20000.00")
    # After 200,000 charged, carrying is 800,000 -> 16,000
    assert monthly_charge(
        method=REDUCING_BALANCE, cost=Decimal("1000000"),
        residual=Decimal("0"), useful_life_months=60,
        annual_rate_percent=Decimal("24"), accumulated=Decimal("200000")
    ) == Decimal("16000.00")


def test_reducing_balance_without_a_rate_is_refused():
    with pytest.raises(HTTPException):
        monthly_charge(
            method=REDUCING_BALANCE, cost=Decimal("100000"),
            residual=Decimal("0"), useful_life_months=60,
            annual_rate_percent=None, accumulated=Decimal("0"))


def _run_schedule(cost, residual, life):
    """Run a full straight-line schedule; return (periods, total charged)."""
    accumulated, periods = Decimal("0"), 0
    while periods < life * 6 + 40:      # generous bound to catch runaways
        charge = monthly_charge(
            method=STRAIGHT_LINE, cost=Decimal(cost),
            residual=Decimal(residual), useful_life_months=life,
            annual_rate_percent=None, accumulated=accumulated,
            periods_charged=periods)
        if charge == 0:
            break
        accumulated += charge
        periods += 1
    return periods, accumulated


@pytest.mark.parametrize("cost,residual,life", [
    ("1200000", "0", 60),          # divides evenly
    ("1000000", "0", 3),           # 333,333.33 x 3 leaves a kobo
    ("1000000", "0", 7),
    ("999999", "0", 60),
    ("1200000", "200000", 60),     # with a residual value
    ("1000000", "123457", 36),     # awkward residual
    ("100", "0", 3),               # tiny amounts
    ("0.05", "0", 3),              # sub-kobo per period
])
def test_schedule_runs_full_term_and_lands_exactly_on_residual(
        cost, residual, life):
    """Two failure modes, both caught here.

    Under-sweeping: 1,000,000 over 3 months rounds to 333,333.33 each time
    and totals 999,999.99 -- one kobo short forever, so the asset never
    closes.

    Over-sweeping: an earlier fix swept whenever the remainder fell below one
    charge, which fired a period EARLY on the residual case -- a 60-month
    asset finished in 59, moving the final month's expense into the wrong
    period. Both are wrong; the schedule must run its exact term.
    """
    periods, total = _run_schedule(cost, residual, life)
    assert periods == life, f"schedule ran {periods} periods, expected {life}"
    assert total == Decimal(cost) - Decimal(residual)


def test_reducing_balance_terminates_at_residual():
    cost, residual = Decimal("1000000"), Decimal("100000")
    accumulated = Decimal("0")
    for _ in range(600):          # 50 years; must converge, not oscillate
        c = monthly_charge(
            method=REDUCING_BALANCE, cost=cost, residual=residual,
            useful_life_months=60, annual_rate_percent=Decimal("24"),
            accumulated=accumulated)
        if c == 0:
            break
        accumulated += c
    assert money(cost - accumulated) == residual


# ---------------------------------------------------------------------------
# Depreciation runs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_records_charge_and_posts_journal(session):
    aid, num = await make_asset(session, cost="1200000", life=60)
    result = await run_depreciation(
        session, period_start=date(2026, 7, 1), period_end=date(2026, 7, 31))
    await session.commit()

    assert result["asset_count"] == 1
    assert result["total_charge"] == Decimal("20000.00")

    tb = await trial_balance(session)
    assert tb["balanced"]
    by_code = {a["code"]: a for a in tb["accounts"]}
    assert by_code["6800"]["balance"] == 20000.00    # expense
    assert by_code["1590"]["balance"] == 20000.00    # accumulated (contra)

    assert await accumulated_depreciation(
        session, asset_id=aid) == Decimal("20000.00")
    assert await carrying_amount(
        session, asset_id=aid) == Decimal("1180000.00")


@pytest.mark.asyncio
async def test_rerunning_a_period_is_refused(session):
    """Charging a month twice would overstate expense and understate assets."""
    await make_asset(session)
    await run_depreciation(
        session, period_start=date(2026, 7, 1), period_end=date(2026, 7, 31))
    await session.commit()

    with pytest.raises(HTTPException) as exc:
        await run_depreciation(
            session, period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31))
    assert "already been posted" in exc.value.detail


@pytest.mark.asyncio
async def test_duplicate_charge_blocked_at_database_level(session):
    """Belt and braces: the unique constraint, not just the service check."""
    aid, _ = await make_asset(session)
    await run_depreciation(
        session, period_start=date(2026, 7, 1), period_end=date(2026, 7, 31))
    await session.commit()

    with pytest.raises(Exception):
        await session.execute(text("""
            INSERT INTO asset_depreciation_charges
                (asset_id, period_start, period_end, amount,
                 opening_carrying_amount, closing_carrying_amount)
            VALUES (:a, '2026-07-01', '2026-07-31', 999,
                    1000000, 999001)
        """), {"a": str(aid)})
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_assets_acquired_after_the_period_are_not_charged(session):
    await make_asset(session, acq=date(2026, 9, 1))
    result = await run_depreciation(
        session, period_start=date(2026, 7, 1), period_end=date(2026, 7, 31))
    await session.commit()
    assert result["asset_count"] == 0
    assert result["total_charge"] == Decimal("0.00")


@pytest.mark.asyncio
async def test_asset_marked_fully_depreciated_when_exhausted(session):
    aid, _ = await make_asset(session, cost="30000", life=3)
    for m in (7, 8, 9, 10):
        await run_depreciation(
            session, period_start=date(2026, m, 1),
            period_end=date(2026, m, 28))
        await session.commit()

    status = (await session.execute(text(
        "SELECT status FROM fixed_assets WHERE id = :a"),
        {"a": str(aid)})).scalar()
    assert status == 'FULLY_DEPRECIATED'
    assert await carrying_amount(session, asset_id=aid) == Decimal("0.00")


# ---------------------------------------------------------------------------
# Disposal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_disposal_at_a_gain(session):
    aid, num = await make_asset(session, cost="1200000", life=60)
    await run_depreciation(
        session, period_start=date(2026, 7, 1), period_end=date(2026, 7, 31))
    await session.commit()

    r = await dispose_asset(
        session, asset_id=aid, disposal_date=date(2026, 8, 15),
        proceeds="1300000")
    await session.commit()

    assert r["carrying_amount"] == Decimal("1180000.00")
    assert r["gain_or_loss"] == Decimal("120000.00")
    assert r["outcome"] == "gain"

    tb = await trial_balance(session)
    assert tb["balanced"], tb["difference"]
    by_code = {a["code"]: a for a in tb["accounts"]}
    # Cost and accumulated depreciation both removed from the balance sheet.
    assert by_code["1520"]["balance"] == -1200000.00
    assert by_code.get("1590", {"balance": 0})["balance"] == 0
    assert by_code["4300"]["balance"] == 120000.00


@pytest.mark.asyncio
async def test_disposal_at_a_loss(session):
    aid, _ = await make_asset(session, cost="1200000", life=60)
    await run_depreciation(
        session, period_start=date(2026, 7, 1), period_end=date(2026, 7, 31))
    await session.commit()

    r = await dispose_asset(
        session, asset_id=aid, disposal_date=date(2026, 8, 15),
        proceeds="900000")
    await session.commit()

    assert r["gain_or_loss"] == Decimal("-280000.00")
    assert r["outcome"] == "loss"
    tb = await trial_balance(session)
    assert tb["balanced"]


@pytest.mark.asyncio
async def test_disposal_for_nil_proceeds_writes_off_carrying_amount(session):
    aid, _ = await make_asset(session, cost="500000", life=60)
    r = await dispose_asset(
        session, asset_id=aid, disposal_date=date(2026, 8, 1), proceeds="0")
    await session.commit()
    assert r["gain_or_loss"] == Decimal("-500000.00")
    tb = await trial_balance(session)
    assert tb["balanced"]


@pytest.mark.asyncio
async def test_cannot_dispose_twice(session):
    aid, _ = await make_asset(session)
    await dispose_asset(session, asset_id=aid,
                        disposal_date=date(2026, 8, 1), proceeds="100")
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await dispose_asset(session, asset_id=aid,
                            disposal_date=date(2026, 8, 2), proceeds="100")
    assert "already been disposed" in exc.value.detail


@pytest.mark.asyncio
async def test_disposed_asset_stops_depreciating(session):
    aid, _ = await make_asset(session)
    await dispose_asset(session, asset_id=aid,
                        disposal_date=date(2026, 7, 15), proceeds="100")
    await session.commit()
    result = await run_depreciation(
        session, period_start=date(2026, 8, 1), period_end=date(2026, 8, 31))
    await session.commit()
    assert result["asset_count"] == 0


@pytest.mark.asyncio
async def test_register_carrying_amounts(session):
    await make_asset(session, cost="1200000", life=60)
    await make_asset(session, cost="600000", life=60)
    await run_depreciation(
        session, period_start=date(2026, 7, 1), period_end=date(2026, 7, 31))
    await session.commit()

    reg = await asset_register(session)
    assert reg["total_cost"] == 1800000.00
    assert reg["total_accumulated_depreciation"] == 30000.00
    assert reg["total_carrying_amount"] == 1770000.00


# ---------------------------------------------------------------------------
# Cash flow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cash_flow_classifies_and_reconciles(session):
    # Opening capital (financing)
    await post_entry(
        session, entry_date=date(2026, 6, 1), description="Share capital",
        source_module="test",
        lines=[Line("1200", debit="5000000"), Line("3100", credit="5000000")])
    # Buy equipment (investing)
    await post_entry(
        session, entry_date=date(2026, 7, 5), description="Buy machine",
        source_module="test",
        lines=[Line("1520", debit="1200000"), Line("1200", credit="1200000")])
    # Collect from a customer (operating)
    await post_entry(
        session, entry_date=date(2026, 7, 10), description="Customer payment",
        source_module="test",
        lines=[Line("1200", debit="800000"), Line("1300", credit="800000")])
    # Pay an expense (operating)
    await post_entry(
        session, entry_date=date(2026, 7, 20), description="Electricity",
        source_module="test",
        lines=[Line("5410", debit="150000"), Line("1200", credit="150000")])
    await session.commit()

    cf = await cash_flow_statement(
        session, start=date(2026, 7, 1), end=date(2026, 7, 31))

    assert cf["opening_cash"] == 5000000.00
    assert cf["investing"]["total"] == -1200000.00
    assert cf["operating"]["total"] == 650000.00   # 800,000 in, 150,000 out
    assert cf["net_movement"] == -550000.00
    assert cf["closing_cash"] == 4450000.00
    assert cf["reconciles"], cf["reconciliation_difference"]


@pytest.mark.asyncio
async def test_cash_flow_reconciles_after_depreciation(session):
    """Depreciation is a non-cash charge. It must not appear as a cash
    movement, and the statement must still reconcile."""
    await post_entry(
        session, entry_date=date(2026, 6, 1), description="Capital",
        source_module="test",
        lines=[Line("1200", debit="2000000"), Line("3100", credit="2000000")])
    await session.commit()
    await make_asset(session, cost="1200000", life=60)
    await run_depreciation(
        session, period_start=date(2026, 7, 1), period_end=date(2026, 7, 31))
    await session.commit()

    cf = await cash_flow_statement(
        session, start=date(2026, 7, 1), end=date(2026, 7, 31))
    assert cf["net_movement"] == 0.0, "depreciation moved no cash"
    assert cf["closing_cash"] == 2000000.00
    assert cf["reconciles"]


@pytest.mark.asyncio
async def test_cash_position_sums_all_cash_accounts(session):
    await post_entry(
        session, entry_date=date(2026, 7, 1), description="Bank",
        source_module="test",
        lines=[Line("1200", debit="500000"), Line("3100", credit="500000")])
    await post_entry(
        session, entry_date=date(2026, 7, 2), description="Petty cash",
        source_module="test",
        lines=[Line("1110", debit="50000"), Line("1200", credit="50000")])
    await session.commit()

    pos = await cash_position(session)
    assert pos["total"] == 500000.00
    by_code = {a["code"]: a["balance"] for a in pos["accounts"]}
    assert by_code["1200"] == 450000.00
    assert by_code["1110"] == 50000.00


@pytest.mark.asyncio
async def test_cash_flow_agrees_with_balance_sheet_cash(session):
    """The statement's closing figure must equal the balance sheet's cash."""
    await post_entry(
        session, entry_date=date(2026, 7, 1), description="Capital",
        source_module="test",
        lines=[Line("1200", debit="1000000"), Line("3100", credit="1000000")])
    await post_entry(
        session, entry_date=date(2026, 7, 15), description="Buy asset",
        source_module="test",
        lines=[Line("1520", debit="400000"), Line("1200", credit="400000")])
    await session.commit()

    cf = await cash_flow_statement(
        session, start=date(2026, 7, 1), end=date(2026, 7, 31))
    pos = await cash_position(session)
    bs = await balance_sheet(session)

    assert cf["closing_cash"] == pos["total"] == 600000.00
    assert bs["balanced"]
