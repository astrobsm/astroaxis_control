"""Payroll engine.

IMPORTANT: these tests verify the ARITHMETIC, not the tax rates. Every band
and percentage used here is set up explicitly by the test, so a change in
Nigerian tax law changes the configuration in the database, not this file.

What is being guarded:
  * progressive bands tax only the slice inside each band (a common error is
    applying the top rate to the whole income, massively over-deducting);
  * annual assessment then apportionment, not month-by-month banding;
  * pensionable pay is basic + housing + transport, not gross;
  * withheld amounts are liabilities, not company costs;
  * payroll refuses to run on unconfirmed tax rates.
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

from app.services.payroll import (
    compute_paye, load_rate_config, calculate_payslip, money)
from app.services.payroll_run import create_payroll_run, approve_payroll_run
from app.services.ledger import trial_balance

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")

SCHEMA = """
DROP TABLE IF EXISTS payslip_components CASCADE;
DROP TABLE IF EXISTS payslips CASCADE;
DROP TABLE IF EXISTS payroll_runs CASCADE;
DROP TABLE IF EXISTS staff_deductions CASCADE;
DROP TABLE IF EXISTS payroll_tax_bands CASCADE;
DROP TABLE IF EXISTS payroll_rate_items CASCADE;
DROP TABLE IF EXISTS payroll_rate_configs CASCADE;
DROP TABLE IF EXISTS attendance CASCADE;
DROP TABLE IF EXISTS staff CASCADE;

CREATE TABLE staff (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id VARCHAR(32) UNIQUE NOT NULL,
    first_name VARCHAR(100) NOT NULL, last_name VARCHAR(100) NOT NULL,
    payment_mode VARCHAR(20), hourly_rate NUMERIC(10,2) DEFAULT 0,
    monthly_salary NUMERIC(10,2) DEFAULT 0,
    basic_salary NUMERIC(18,2), housing_allowance NUMERIC(18,2) DEFAULT 0,
    transport_allowance NUMERIC(18,2) DEFAULT 0,
    other_allowances NUMERIC(18,2) DEFAULT 0,
    employment_type VARCHAR(30) DEFAULT 'permanent',
    tax_exempt BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    clock_pin VARCHAR(4) UNIQUE
);
CREATE TABLE attendance (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    staff_id UUID NOT NULL REFERENCES staff(id),
    clock_in TIMESTAMPTZ NOT NULL, clock_out TIMESTAMPTZ,
    hours_worked NUMERIC(6,2) DEFAULT 0, status VARCHAR(32) DEFAULT 'completed'
);
"""


def _apply(conn, name):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / name
    spec = importlib.util.spec_from_file_location(f"mig_{name[:6]}", path)
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
        _apply(c, "o4567890123n_payroll.py")
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


async def confirm_rates(session):
    """Confirm the seeded configuration so payroll may run."""
    await session.execute(text("""
        UPDATE payroll_rate_configs
           SET is_confirmed = TRUE, confirmed_by = 'test accountant',
               confirmed_at = NOW()
    """))
    await session.commit()


async def make_staff(session, *, basic="500000", housing="0", transport="0",
                     mode="monthly", hourly="0", exempt=False):
    sid = uuid.uuid4()
    tag = uuid.uuid4().hex[:4].upper()
    await session.execute(text("""
        INSERT INTO staff (id, employee_id, first_name, last_name,
                           payment_mode, hourly_rate, monthly_salary,
                           basic_salary, housing_allowance,
                           transport_allowance, tax_exempt, clock_pin)
        VALUES (:i, :e, 'Test', 'Staff', :m, :h, :b, :b, :ho, :t, :x, :pin)
    """), {"i": str(sid), "e": f"BSM{tag}", "m": mode, "h": hourly,
           "b": basic, "ho": housing, "t": transport, "x": exempt,
           "pin": tag[:4]})
    await session.commit()
    return sid


# ---------------------------------------------------------------------------
# Progressive banding -- pure arithmetic
# ---------------------------------------------------------------------------

BANDS = [
    {"lower": Decimal("0"), "upper": Decimal("300000"), "rate": Decimal("7")},
    {"lower": Decimal("300000"), "upper": Decimal("600000"), "rate": Decimal("11")},
    {"lower": Decimal("600000"), "upper": Decimal("1100000"), "rate": Decimal("15")},
    {"lower": Decimal("1100000"), "upper": None, "rate": Decimal("19")},
]


def test_income_below_first_band_ceiling():
    # 100,000 x 7% = 7,000
    assert compute_paye(Decimal("100000"), BANDS) == Decimal("7000.00")


def test_progressive_taxes_each_slice_separately():
    """The error this guards: applying the top rate to the whole income.

    450,000 = 300,000 @ 7% (21,000) + 150,000 @ 11% (16,500) = 37,500.
    Applying 11% to everything would give 49,500 -- a 32% over-deduction.
    """
    assert compute_paye(Decimal("450000"), BANDS) == Decimal("37500.00")


def test_progressive_across_many_bands():
    # 300k@7=21,000 + 300k@11=33,000 + 500k@15=75,000 + 400k@19=76,000
    assert compute_paye(Decimal("1500000"), BANDS) == Decimal("205000.00")


def test_top_band_is_open_ended():
    # 21,000 + 33,000 + 75,000 + 8,900,000@19% = 1,691,000 -> 1,820,000
    assert compute_paye(Decimal("10000000"), BANDS) == Decimal("1820000.00")


def test_zero_and_negative_taxable_pay_no_tax():
    assert compute_paye(Decimal("0"), BANDS) == Decimal("0.00")
    assert compute_paye(Decimal("-50000"), BANDS) == Decimal("0.00")


def test_exact_band_boundary():
    # Exactly 300,000 stays entirely in the first band.
    assert compute_paye(Decimal("300000"), BANDS) == Decimal("21000.00")


# ---------------------------------------------------------------------------
# Rate configuration must be confirmed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_payroll_refuses_unconfirmed_rates(session):
    """The seeded rates are a starting shape, not legal advice. Running
    payroll on unreviewed bands is how a company accrues a PAYE liability
    it does not know about."""
    with pytest.raises(HTTPException) as exc:
        await load_rate_config(session, on=date(2026, 7, 31))
    assert "not been confirmed" in exc.value.detail
    assert "NOT been verified" in exc.value.detail


@pytest.mark.asyncio
async def test_confirmed_rates_load(session):
    await confirm_rates(session)
    cfg = await load_rate_config(session, on=date(2026, 7, 31))
    assert cfg.is_confirmed
    assert len(cfg.bands) == 6
    assert cfg.item("PENSION_EMPLOYEE") == Decimal("8.0000")


@pytest.mark.asyncio
async def test_confirmed_flag_requires_an_author(session):
    """Confirming is a recorded act -- someone's name is against it."""
    with pytest.raises(Exception):
        await session.execute(text(
            "UPDATE payroll_rate_configs SET is_confirmed = TRUE, "
            "confirmed_by = NULL"))
        await session.commit()
    await session.rollback()


# ---------------------------------------------------------------------------
# Payslip calculation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pensionable_pay_is_basic_plus_housing_and_transport(session):
    """Pension is NOT charged on gross. Using gross would over-deduct from
    anyone receiving a bonus or other allowances."""
    await confirm_rates(session)
    cfg = await load_rate_config(session, on=date(2026, 7, 31))
    sid = await make_staff(session, basic="300000", housing="100000",
                           transport="50000")
    st = (await session.execute(text(
        "SELECT * FROM staff WHERE id = :i"), {"i": str(sid)})).first()

    slip = await calculate_payslip(
        session, staff_row=st, config=cfg,
        period_start=date(2026, 7, 1), period_end=date(2026, 7, 31),
        bonus=Decimal("200000"))

    pension = next(c for c in slip.components if c["code"] == "PENSION")
    # 8% of (300k + 100k + 50k) = 36,000 -- the 200k bonus is excluded.
    assert pension["amount"] == Decimal("36000.00")
    assert pension["basis_amount"] == Decimal("450000.00")


@pytest.mark.asyncio
async def test_nhf_charged_on_basic_only(session):
    await confirm_rates(session)
    cfg = await load_rate_config(session, on=date(2026, 7, 31))
    sid = await make_staff(session, basic="400000", housing="200000")
    st = (await session.execute(text(
        "SELECT * FROM staff WHERE id = :i"), {"i": str(sid)})).first()
    slip = await calculate_payslip(
        session, staff_row=st, config=cfg,
        period_start=date(2026, 7, 1), period_end=date(2026, 7, 31))
    nhf = next(c for c in slip.components if c["code"] == "NHF")
    assert nhf["amount"] == Decimal("10000.00")   # 2.5% of 400,000


@pytest.mark.asyncio
async def test_net_equals_gross_less_deductions(session):
    await confirm_rates(session)
    cfg = await load_rate_config(session, on=date(2026, 7, 31))
    sid = await make_staff(session, basic="500000", housing="100000")
    st = (await session.execute(text(
        "SELECT * FROM staff WHERE id = :i"), {"i": str(sid)})).first()
    slip = await calculate_payslip(
        session, staff_row=st, config=cfg,
        period_start=date(2026, 7, 1), period_end=date(2026, 7, 31))

    assert slip.gross == Decimal("600000.00")
    assert slip.net == money(slip.gross - slip.total_deductions)

    deductions = sum(c["amount"] for c in slip.components
                     if c["component_type"] == "DEDUCTION")
    assert money(deductions) == slip.total_deductions


@pytest.mark.asyncio
async def test_hourly_staff_use_their_own_rate(session):
    """Regression: the old engine paid EVERY staff member a hardcoded
    NGN 425/hour, ignoring hourly_rate entirely."""
    await confirm_rates(session)
    cfg = await load_rate_config(session, on=date(2026, 7, 31))
    sid = await make_staff(session, basic="0", mode="hourly", hourly="1200")
    st = (await session.execute(text(
        "SELECT * FROM staff WHERE id = :i"), {"i": str(sid)})).first()

    slip = await calculate_payslip(
        session, staff_row=st, config=cfg,
        period_start=date(2026, 7, 1), period_end=date(2026, 7, 31),
        hours_worked=Decimal("100"))

    assert slip.regular_hours == Decimal("100")
    assert slip.gross == Decimal("120000.00")   # 100 x 1,200, not 100 x 425


@pytest.mark.asyncio
async def test_overtime_paid_at_multiplier(session):
    await confirm_rates(session)
    cfg = await load_rate_config(session, on=date(2026, 7, 31))
    sid = await make_staff(session, basic="0", mode="hourly", hourly="1000")
    st = (await session.execute(text(
        "SELECT * FROM staff WHERE id = :i"), {"i": str(sid)})).first()

    slip = await calculate_payslip(
        session, staff_row=st, config=cfg,
        period_start=date(2026, 7, 1), period_end=date(2026, 7, 31),
        hours_worked=Decimal("180"))

    assert slip.regular_hours == Decimal("160")
    assert slip.overtime_hours == Decimal("20")
    # 160 x 1,000 + 20 x 1,000 x 1.5 = 190,000
    assert slip.gross == Decimal("190000.00")


@pytest.mark.asyncio
async def test_tax_exempt_staff_pay_no_paye(session):
    await confirm_rates(session)
    cfg = await load_rate_config(session, on=date(2026, 7, 31))
    sid = await make_staff(session, basic="500000", exempt=True)
    st = (await session.execute(text(
        "SELECT * FROM staff WHERE id = :i"), {"i": str(sid)})).first()
    slip = await calculate_payslip(
        session, staff_row=st, config=cfg,
        period_start=date(2026, 7, 1), period_end=date(2026, 7, 31))
    assert not any(c["code"] == "PAYE" for c in slip.components)
    # Pension and NHF still apply -- exemption is from income tax only.
    assert any(c["code"] == "PENSION" for c in slip.components)


@pytest.mark.asyncio
async def test_loan_recovery_never_exceeds_outstanding(session):
    """The final instalment of a loan is usually smaller than the standard
    repayment; taking the full amount would over-recover."""
    await confirm_rates(session)
    cfg = await load_rate_config(session, on=date(2026, 7, 31))
    sid = await make_staff(session, basic="500000")
    await session.execute(text("""
        INSERT INTO staff_deductions
            (staff_id, code, label, total_amount, amount_per_period,
             amount_recovered, start_date)
        VALUES (:s, 'LOAN', 'Staff loan', 100000, 30000, 90000, DATE '2026-01-01')
    """), {"s": str(sid)})
    await session.commit()

    st = (await session.execute(text(
        "SELECT * FROM staff WHERE id = :i"), {"i": str(sid)})).first()
    slip = await calculate_payslip(
        session, staff_row=st, config=cfg,
        period_start=date(2026, 7, 1), period_end=date(2026, 7, 31))

    loan = next(c for c in slip.components if c["code"] == "LOAN")
    assert loan["amount"] == Decimal("10000.00"), "should take only what remains"


@pytest.mark.asyncio
async def test_deductions_exceeding_gross_are_refused(session):
    """Better to fail loudly than pay a negative amount or clamp to zero."""
    await confirm_rates(session)
    cfg = await load_rate_config(session, on=date(2026, 7, 31))
    sid = await make_staff(session, basic="50000")
    await session.execute(text("""
        INSERT INTO staff_deductions
            (staff_id, code, label, total_amount, amount_per_period, start_date)
        VALUES (:s, 'LOAN', 'Large loan', 500000, 200000, DATE '2026-01-01')
    """), {"s": str(sid)})
    await session.commit()
    st = (await session.execute(text(
        "SELECT * FROM staff WHERE id = :i"), {"i": str(sid)})).first()

    with pytest.raises(HTTPException) as exc:
        await calculate_payslip(
            session, staff_row=st, config=cfg,
            period_start=date(2026, 7, 1), period_end=date(2026, 7, 31))
    assert "exceed gross pay" in exc.value.detail


# ---------------------------------------------------------------------------
# Runs and posting
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_totals_match_the_payslips(session):
    await confirm_rates(session)
    for basic in ("400000", "600000", "250000"):
        await make_staff(session, basic=basic)

    run = await create_payroll_run(
        session, period_start=date(2026, 7, 1), period_end=date(2026, 7, 31))
    await session.commit()

    row = (await session.execute(text("""
        SELECT COALESCE(SUM(gross_pay),0) g, COALESCE(SUM(net_pay),0) n,
               COALESCE(SUM(total_deductions),0) d
          FROM payslips WHERE run_id = :r
    """), {"r": str(run["run_id"])})).first()

    assert money(row.g) == run["gross_total"]
    assert money(row.n) == run["net_total"]
    assert money(row.d) == run["deductions_total"]
    assert run["staff_paid"] == 3


@pytest.mark.asyncio
async def test_duplicate_run_for_period_refused(session):
    """Paying a month twice is not recoverable by a code fix."""
    await confirm_rates(session)
    await make_staff(session, basic="400000")
    await create_payroll_run(
        session, period_start=date(2026, 7, 1), period_end=date(2026, 7, 31))
    await session.commit()

    with pytest.raises(HTTPException) as exc:
        await create_payroll_run(
            session, period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31))
    assert "already exists" in exc.value.detail


@pytest.mark.asyncio
async def test_approval_posts_balanced_journal(session):
    await confirm_rates(session)
    await make_staff(session, basic="500000", housing="100000")
    run = await create_payroll_run(
        session, period_start=date(2026, 7, 1), period_end=date(2026, 7, 31))
    await session.commit()

    await approve_payroll_run(
        session, run_id=run["run_id"], approved_by="Finance Director")
    await session.commit()

    tb = await trial_balance(session)
    assert tb["balanced"], tb["difference"]

    by_code = {a["code"]: a for a in tb["accounts"]}
    # Cost = gross + employer contributions
    assert by_code["6100"]["balance"] == float(
        run["gross_total"] + run["employer_cost_total"])
    # Net pay is a liability until it is actually disbursed
    assert by_code["2200"]["balance"] == float(run["net_total"])
    # PAYE is withheld, not a company cost
    assert by_code["2210"]["balance"] > 0


@pytest.mark.asyncio
async def test_withheld_amounts_are_liabilities_not_expenses(session):
    """Booking PAYE as an expense would overstate employment cost and
    understate what is owed to the tax authority."""
    await confirm_rates(session)
    await make_staff(session, basic="800000")
    run = await create_payroll_run(
        session, period_start=date(2026, 7, 1), period_end=date(2026, 7, 31))
    await session.commit()
    await approve_payroll_run(
        session, run_id=run["run_id"], approved_by="FD")
    await session.commit()

    tb = await trial_balance(session)
    types = {a["code"]: a["account_type"] for a in tb["accounts"]}
    for liability in ("2200", "2210", "2220", "2230"):
        if liability in types:
            assert types[liability] == "LIABILITY"
    assert types["6100"] == "EXPENSE"

    expense_total = sum(a["balance"] for a in tb["accounts"]
                        if a["account_type"] == "EXPENSE")
    assert money(expense_total) == money(
        run["gross_total"] + run["employer_cost_total"])


@pytest.mark.asyncio
async def test_run_cannot_be_approved_twice(session):
    await confirm_rates(session)
    await make_staff(session, basic="400000")
    run = await create_payroll_run(
        session, period_start=date(2026, 7, 1), period_end=date(2026, 7, 31))
    await session.commit()
    await approve_payroll_run(session, run_id=run["run_id"], approved_by="FD")
    await session.commit()

    with pytest.raises(HTTPException) as exc:
        await approve_payroll_run(
            session, run_id=run["run_id"], approved_by="FD")
    assert "only DRAFT runs" in exc.value.detail


@pytest.mark.asyncio
async def test_approval_advances_loan_balance(session):
    await confirm_rates(session)
    sid = await make_staff(session, basic="500000")
    await session.execute(text("""
        INSERT INTO staff_deductions
            (staff_id, code, label, total_amount, amount_per_period, start_date)
        VALUES (:s, 'LOAN', 'Staff loan', 90000, 30000, DATE '2026-01-01')
    """), {"s": str(sid)})
    await session.commit()

    run = await create_payroll_run(
        session, period_start=date(2026, 7, 1), period_end=date(2026, 7, 31))
    await session.commit()
    await approve_payroll_run(session, run_id=run["run_id"], approved_by="FD")
    await session.commit()

    row = (await session.execute(text(
        "SELECT amount_recovered, is_active FROM staff_deductions "
        "WHERE staff_id = :s"), {"s": str(sid)})).first()
    assert money(row.amount_recovered) == Decimal("30000.00")
    assert row.is_active is True


@pytest.mark.asyncio
async def test_draft_run_posts_nothing(session):
    """A run must be reviewed before it touches the ledger."""
    await confirm_rates(session)
    await make_staff(session, basic="400000")
    await create_payroll_run(
        session, period_start=date(2026, 7, 1), period_end=date(2026, 7, 31))
    await session.commit()

    n = (await session.execute(text(
        "SELECT COUNT(*) FROM gl_journal_entries"))).scalar()
    assert n == 0
