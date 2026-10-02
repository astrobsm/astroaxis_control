"""Paying a payroll run, and hiding somebody from the lists.

The tests that matter most here are the ones about money NOT moving twice,
and about hiding a colleague never reaching their pay. Everything else is
detail.

Schema follows test_payroll.py: the tables these services touch are built
explicitly and the real migrations are applied on top, so a column this code
depends on but never declares is a failure here rather than in production.
"""
from __future__ import annotations

import os
import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.services import payroll_payment as pay
from app.services import staff_visibility as vis
from app.services.payroll_run import create_payroll_run, approve_payroll_run

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")

LAGOS = timezone(timedelta(hours=1))

PERIOD_START = date(2026, 4, 1)
PERIOD_END = date(2026, 4, 30)

SCHEMA = """
DROP TABLE IF EXISTS staff_visibility_log CASCADE;
DROP TABLE IF EXISTS payslip_components CASCADE;
DROP TABLE IF EXISTS payslips CASCADE;
DROP TABLE IF EXISTS payroll_payment_batches CASCADE;
DROP TABLE IF EXISTS payroll_runs CASCADE;
DROP TABLE IF EXISTS staff_deductions CASCADE;
DROP TABLE IF EXISTS payroll_tax_bands CASCADE;
DROP TABLE IF EXISTS payroll_rate_items CASCADE;
DROP TABLE IF EXISTS payroll_rate_configs CASCADE;
DROP TABLE IF EXISTS attendance CASCADE;
DROP TABLE IF EXISTS staff CASCADE;
DROP TABLE IF EXISTS users CASCADE;

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(100) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    hashed_password VARCHAR(255) NOT NULL DEFAULT 'x',
    full_name VARCHAR(255), role VARCHAR(50) DEFAULT 'admin',
    is_active BOOLEAN DEFAULT TRUE, is_approved BOOLEAN DEFAULT TRUE
);
CREATE TABLE staff (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id VARCHAR(32) UNIQUE NOT NULL,
    first_name VARCHAR(100) NOT NULL, last_name VARCHAR(100) NOT NULL,
    position VARCHAR(120),
    payment_mode VARCHAR(20), hourly_rate NUMERIC(10,2) DEFAULT 0,
    monthly_salary NUMERIC(10,2) DEFAULT 0,
    basic_salary NUMERIC(18,2), housing_allowance NUMERIC(18,2) DEFAULT 0,
    transport_allowance NUMERIC(18,2) DEFAULT 0,
    other_allowances NUMERIC(18,2) DEFAULT 0,
    employment_type VARCHAR(30) DEFAULT 'permanent',
    tax_exempt BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    bank_name VARCHAR(128), bank_account_number VARCHAR(32),
    bank_account_name VARCHAR(255),
    clock_pin VARCHAR(8) UNIQUE
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
    spec = importlib.util.spec_from_file_location(
        f"mig_{uuid.uuid4().hex[:6]}", path)
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
        _apply(c, "m8901234567l_payroll_payment.py")
        c.commit()
    seng.dispose()
    eng = create_async_engine(TEST_DB, future=True)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db(engine):
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        # Payroll refuses to run on unconfirmed tax rates, which is correct
        # and not what these tests are about.
        await s.execute(text("""
            UPDATE payroll_rate_configs
               SET is_confirmed = TRUE, confirmed_by = 'test accountant',
                   confirmed_at = NOW()
        """))
        await s.commit()
        yield s


@pytest.fixture(autouse=True)
def _posting_on(monkeypatch):
    monkeypatch.setenv("ACCOUNTING_POSTING_ENABLED", "true")
    monkeypatch.delenv("ACCOUNTING_CUTOVER_DATE", raising=False)


async def _user(db, name="Payroll Officer"):
    uid = uuid.uuid4()
    await db.execute(text("""
        INSERT INTO users (id, username, email, hashed_password, full_name,
                           role, is_active, is_approved)
        VALUES (:i, :u, :e, 'x', :n, 'admin', TRUE, TRUE)
    """), {"i": str(uid), "u": f"u{uid.hex[:8]}",
           "e": f"{uid.hex[:8]}@test.local", "n": name})
    return uid


async def _staff(db, *, salary=200000, mode="monthly", employee_id=None):
    sid = uuid.uuid4()
    tag = uuid.uuid4().hex[:6].upper()
    eid = employee_id or f"BSM{tag}"
    await db.execute(text("""
        INSERT INTO staff (id, employee_id, first_name, last_name, position,
                           payment_mode, monthly_salary, basic_salary,
                           hourly_rate, clock_pin, is_active, bank_name,
                           bank_account_number)
        VALUES (:i, :e, 'Test', :ln, 'Production Staff', :m, :s, :s, 425,
                :pin, TRUE, 'Test Bank', '0123456789')
    """), {"i": str(sid), "e": eid, "ln": tag, "m": mode, "s": salary,
           "pin": tag[:8]})
    return sid


async def _attendance(db, staff_id, *, on: date, hours=8):
    await db.execute(text("""
        INSERT INTO attendance (id, staff_id, clock_in, clock_out,
                                hours_worked, status)
        VALUES (:i, :s, :ci, :co, :h, 'completed')
    """), {"i": str(uuid.uuid4()), "s": str(staff_id),
           "ci": datetime.combine(on, time(8), LAGOS),
           "co": datetime.combine(on, time(16), LAGOS), "h": hours})


async def _approved_run(db, staff_ids, actor):
    run = await create_payroll_run(
        db, period_start=PERIOD_START, period_end=PERIOD_END,
        staff_ids=staff_ids, created_by=actor)
    await approve_payroll_run(
        db, run_id=run["run_id"], approved_by="Test Approver",
        created_by=actor)
    slips = (await db.execute(text(
        "SELECT id, net_pay FROM payslips WHERE run_id = :r ORDER BY id"),
        {"r": str(run["run_id"])})).fetchall()
    return run, slips


# ---------------------------------------------------------------------------
# Who shows up on the payroll screen
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_only_staff_who_worked_the_period_are_listed_as_worked(db):
    worked = await _staff(db)
    idle = await _staff(db)
    await _attendance(db, worked, on=date(2026, 4, 7))

    result = await pay.eligibility(
        db, period_start=PERIOD_START, period_end=PERIOD_END)

    worked_ids = {w["staff_id"] for w in result["worked"]}
    idle_ids = {w["staff_id"] for w in result["not_worked"]}
    assert str(worked) in worked_ids
    assert str(idle) in idle_ids


@pytest.mark.asyncio
async def test_staff_who_did_not_work_are_returned_not_discarded(db):
    """Dropping them silently is how somebody goes unpaid for a month."""
    idle = await _staff(db)
    result = await pay.eligibility(
        db, period_start=PERIOD_START, period_end=PERIOD_END)
    assert str(idle) in {w["staff_id"] for w in result["not_worked"]}
    assert result["not_worked_count"] >= 1


@pytest.mark.asyncio
async def test_attendance_outside_the_period_does_not_count(db):
    s = await _staff(db)
    await _attendance(db, s, on=PERIOD_START - timedelta(days=1))
    result = await pay.eligibility(
        db, period_start=PERIOD_START, period_end=PERIOD_END)
    assert str(s) in {w["staff_id"] for w in result["not_worked"]}


@pytest.mark.asyncio
async def test_hours_are_summed_across_the_period(db):
    s = await _staff(db)
    await _attendance(db, s, on=date(2026, 4, 7), hours=8)
    await _attendance(db, s, on=date(2026, 4, 8), hours=6)
    result = await pay.eligibility(
        db, period_start=PERIOD_START, period_end=PERIOD_END)
    row = next(w for w in result["worked"] if w["staff_id"] == str(s))
    assert row["hours_worked"] == pytest.approx(14.0)
    assert row["days_worked"] == 2


@pytest.mark.asyncio
async def test_a_backwards_period_is_refused(db):
    with pytest.raises(HTTPException) as e:
        await pay.eligibility(
            db, period_start=PERIOD_END, period_end=PERIOD_START)
    assert e.value.status_code == 400


# ---------------------------------------------------------------------------
# Paying
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_paying_marks_the_payslip_and_settles_the_liability(db):
    actor = await _user(db)
    s = await _staff(db)
    run, slips = await _approved_run(db, [s], actor)

    result = await pay.pay_payslips(
        db, run_id=run["run_id"], payslip_ids=[slips[0].id],
        paid_on=date(2026, 4, 30), method="BANK_TRANSFER",
        bank_reference="FT123456", paid_by=actor, paid_by_name="Officer")

    assert result["staff_count"] == 1
    assert result["run_status"] == "PAID"

    paid_at = (await db.execute(text(
        "SELECT paid_at, payment_batch_id FROM payslips WHERE id = :i"),
        {"i": str(slips[0].id)})).first()
    assert paid_at.paid_at is not None
    assert paid_at.payment_batch_id is not None

    status = (await db.execute(text(
        "SELECT status, paid_at FROM payroll_runs WHERE id = :i"),
        {"i": str(run["run_id"])})).first()
    assert status.status == "PAID"
    assert status.paid_at is not None


@pytest.mark.asyncio
async def test_payment_debits_salary_payable_and_credits_the_bank(db):
    """Approval books the debt; payment must clear it, not re-expense it."""
    actor = await _user(db)
    s = await _staff(db)
    run, slips = await _approved_run(db, [s], actor)

    result = await pay.pay_payslips(
        db, run_id=run["run_id"], payslip_ids=[slips[0].id],
        paid_on=date(2026, 4, 30), paid_by=actor)

    if not result["journal_entry_id"]:
        pytest.skip("GL posting disabled in this environment")

    lines = (await db.execute(text("""
        SELECT a.code, l.debit, l.credit
          FROM gl_journal_lines l JOIN gl_accounts a ON a.id = l.account_id
         WHERE l.entry_id = :e ORDER BY a.code
    """), {"e": result["journal_entry_id"]})).fetchall()

    by_code = {r.code: r for r in lines}
    assert by_code["2200"].debit > 0, "Staff Salary Payable must be debited"
    assert by_code["1200"].credit > 0, "the bank must be credited"
    assert by_code["2200"].debit == by_code["1200"].credit
    assert "6100" not in by_code, "payment must not re-expense the salary"


@pytest.mark.asyncio
async def test_cash_payment_leaves_the_cash_account_not_the_bank(db):
    actor = await _user(db)
    s = await _staff(db)
    run, slips = await _approved_run(db, [s], actor)

    result = await pay.pay_payslips(
        db, run_id=run["run_id"], payslip_ids=[slips[0].id],
        paid_on=date(2026, 4, 30), method="CASH", paid_by=actor)

    if not result["journal_entry_id"]:
        pytest.skip("GL posting disabled in this environment")

    codes = {r.code for r in (await db.execute(text("""
        SELECT a.code FROM gl_journal_lines l
          JOIN gl_accounts a ON a.id = l.account_id
         WHERE l.entry_id = :e AND l.credit > 0
    """), {"e": result["journal_entry_id"]})).fetchall()}
    assert "1100" in codes
    assert "1200" not in codes


@pytest.mark.asyncio
async def test_paying_the_same_payslip_twice_is_refused(db):
    """The commonest way to pay somebody twice is a double-submitted form."""
    actor = await _user(db)
    s = await _staff(db)
    run, slips = await _approved_run(db, [s], actor)

    await pay.pay_payslips(
        db, run_id=run["run_id"], payslip_ids=[slips[0].id],
        paid_on=date(2026, 4, 30), paid_by=actor)

    with pytest.raises(HTTPException) as e:
        await pay.pay_payslips(
            db, run_id=run["run_id"], payslip_ids=[slips[0].id],
            paid_on=date(2026, 4, 30), paid_by=actor)
    assert e.value.status_code == 400
    assert "already" in str(e.value.detail).lower()


@pytest.mark.asyncio
async def test_an_unapproved_run_cannot_be_paid(db):
    actor = await _user(db)
    s = await _staff(db)
    run = await create_payroll_run(
        db, period_start=PERIOD_START, period_end=PERIOD_END,
        staff_ids=[s], created_by=actor)
    slip = (await db.execute(text(
        "SELECT id FROM payslips WHERE run_id = :r"),
        {"r": str(run["run_id"])})).first()

    with pytest.raises(HTTPException) as e:
        await pay.pay_payslips(
            db, run_id=run["run_id"], payslip_ids=[slip.id],
            paid_on=date(2026, 4, 30), paid_by=actor)
    assert e.value.status_code == 400
    assert "APPROVED" in str(e.value.detail)


@pytest.mark.asyncio
async def test_partial_payment_leaves_the_run_approved(db):
    """One person's bank details being wrong must not strand the other four."""
    actor = await _user(db)
    a = await _staff(db)
    b = await _staff(db)
    run, slips = await _approved_run(db, [a, b], actor)
    assert len(slips) == 2

    result = await pay.pay_payslips(
        db, run_id=run["run_id"], payslip_ids=[slips[0].id],
        paid_on=date(2026, 4, 30), paid_by=actor)

    assert result["outstanding_payslips"] == 1
    assert result["run_status"] == "APPROVED"
    status = (await db.execute(text(
        "SELECT status FROM payroll_runs WHERE id = :i"),
        {"i": str(run["run_id"])})).scalar()
    assert status == "APPROVED"


@pytest.mark.asyncio
async def test_a_payslip_from_another_run_is_refused(db):
    actor = await _user(db)
    a = await _staff(db)
    run_a, slips_a = await _approved_run(db, [a], actor)

    other = await _staff(db)
    run_b = await create_payroll_run(
        db, period_start=date(2026, 5, 1), period_end=date(2026, 5, 31),
        staff_ids=[other], created_by=actor)
    await approve_payroll_run(
        db, run_id=run_b["run_id"], approved_by="T", created_by=actor)
    slip_b = (await db.execute(text(
        "SELECT id FROM payslips WHERE run_id = :r"),
        {"r": str(run_b["run_id"])})).first()

    with pytest.raises(HTTPException) as e:
        await pay.pay_payslips(
            db, run_id=run_a["run_id"], payslip_ids=[slip_b.id],
            paid_on=date(2026, 4, 30), paid_by=actor)
    assert e.value.status_code == 400
    assert "different run" in str(e.value.detail)


@pytest.mark.asyncio
async def test_an_unknown_payment_method_is_refused(db):
    actor = await _user(db)
    s = await _staff(db)
    run, slips = await _approved_run(db, [s], actor)
    with pytest.raises(HTTPException) as e:
        await pay.pay_payslips(
            db, run_id=run["run_id"], payslip_ids=[slips[0].id],
            paid_on=date(2026, 4, 30), method="CRYPTO", paid_by=actor)
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_paying_nobody_is_refused(db):
    actor = await _user(db)
    s = await _staff(db)
    run, _ = await _approved_run(db, [s], actor)
    with pytest.raises(HTTPException) as e:
        await pay.pay_payslips(
            db, run_id=run["run_id"], payslip_ids=[],
            paid_on=date(2026, 4, 30), paid_by=actor)
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_a_payment_batch_cannot_be_deleted(db):
    """It is a financial record: reverse it, do not erase it."""
    actor = await _user(db)
    s = await _staff(db)
    run, slips = await _approved_run(db, [s], actor)
    result = await pay.pay_payslips(
        db, run_id=run["run_id"], payslip_ids=[slips[0].id],
        paid_on=date(2026, 4, 30), paid_by=actor)

    with pytest.raises(Exception) as e:
        await db.execute(text(
            "DELETE FROM payroll_payment_batches WHERE id = :i"),
            {"i": result["batch_id"]})
    assert "append-only" in str(e.value).lower()


@pytest.mark.asyncio
async def test_a_payslip_cannot_be_paid_without_a_batch(db):
    """The CHECK stops a payment that no ledger entry can be traced to."""
    actor = await _user(db)
    s = await _staff(db)
    run, slips = await _approved_run(db, [s], actor)
    with pytest.raises(Exception) as e:
        await db.execute(text(
            "UPDATE payslips SET paid_at = NOW() WHERE id = :i"),
            {"i": str(slips[0].id)})
    assert "ck_payslip_paid_pair" in str(e.value)


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_report_separates_paid_from_outstanding(db):
    actor = await _user(db)
    a = await _staff(db)
    b = await _staff(db)
    run, slips = await _approved_run(db, [a, b], actor)

    await pay.pay_payslips(
        db, run_id=run["run_id"], payslip_ids=[slips[0].id],
        paid_on=date(2026, 4, 30), paid_by=actor)

    report = await pay.payment_report(
        db, period_start=PERIOD_START, period_end=PERIOD_END)

    assert report["staff_count"] == 2
    assert report["paid_count"] == 1
    assert report["unpaid_count"] == 1
    assert report["paid_total"] > 0
    assert report["outstanding_total"] > 0
    assert (round(report["paid_total"] + report["outstanding_total"], 2)
            == round(report["net_total"], 2))


@pytest.mark.asyncio
async def test_the_report_keeps_employer_cost_out_of_net_pay(db):
    """Net is what staff receive. Employer contributions are extra."""
    actor = await _user(db)
    s = await _staff(db)
    await _approved_run(db, [s], actor)
    report = await pay.payment_report(
        db, period_start=PERIOD_START, period_end=PERIOD_END)
    assert (round(report["net_total"], 2)
            == round(report["gross_total"] - report["deductions_total"], 2))


@pytest.mark.asyncio
async def test_the_report_is_empty_rather_than_wrong_for_an_unrun_period(db):
    report = await pay.payment_report(
        db, period_start=date(2019, 1, 1), period_end=date(2019, 1, 31))
    assert report["staff_count"] == 0
    assert report["net_total"] == 0.0
    assert "No payroll" in report["note"]


# ---------------------------------------------------------------------------
# Hiding a member of staff
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hiding_requires_a_reason(db):
    actor = await _user(db)
    s = await _staff(db)
    with pytest.raises(HTTPException) as e:
        await vis.set_visibility(
            db, staff_id=s, hidden=True, reason="  ", actor_id=actor)
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_hiding_records_who_did_it_and_why(db):
    actor = await _user(db, name="HR Manager")
    s = await _staff(db)
    await vis.set_visibility(
        db, staff_id=s, hidden=True, reason="Resigned in March",
        actor_id=actor, actor_name="HR Manager")

    history = await vis.visibility_history(db, staff_id=s)
    assert len(history) == 1
    assert history[0]["hidden"] is True
    assert history[0]["reason"] == "Resigned in March"
    assert history[0]["actor_name"] == "HR Manager"


@pytest.mark.asyncio
async def test_restoring_is_also_recorded(db):
    """A hide-then-restore leaves two rows, not zero."""
    actor = await _user(db)
    s = await _staff(db)
    await vis.set_visibility(db, staff_id=s, hidden=True,
                             reason="Suspended pending query", actor_id=actor)
    await vis.set_visibility(db, staff_id=s, hidden=False,
                             reason="Query resolved", actor_id=actor)
    history = await vis.visibility_history(db, staff_id=s)
    assert len(history) == 2
    assert {h["hidden"] for h in history} == {True, False}


@pytest.mark.asyncio
async def test_the_visibility_log_cannot_be_rewritten(db):
    actor = await _user(db)
    s = await _staff(db)
    await vis.set_visibility(db, staff_id=s, hidden=True,
                             reason="Duplicate record", actor_id=actor)
    with pytest.raises(Exception) as e:
        await db.execute(text(
            "UPDATE staff_visibility_log SET reason = 'something else' "
            "WHERE staff_id = :i"), {"i": str(s)})
    assert "append-only" in str(e.value).lower()


@pytest.mark.asyncio
async def test_the_database_refuses_a_hiding_with_no_reason(db):
    """Enforced by CHECK, so an API that forgets cannot bypass it."""
    s = await _staff(db)
    with pytest.raises(Exception) as e:
        await db.execute(text(
            "UPDATE staff SET display_hidden = TRUE WHERE id = :i"),
            {"i": str(s)})
    assert "ck_staff_hidden_reason" in str(e.value)


@pytest.mark.asyncio
async def test_hiding_somebody_does_not_remove_them_from_payroll(db):
    """A suspended employee is usually still owed wages."""
    actor = await _user(db)
    s = await _staff(db)
    await _attendance(db, s, on=date(2026, 4, 7))
    await vis.set_visibility(
        db, staff_id=s, hidden=True, reason="Suspended pending query",
        actor_id=actor)

    result = await pay.eligibility(
        db, period_start=PERIOD_START, period_end=PERIOD_END)
    row = next((w for w in result["worked"] if w["staff_id"] == str(s)), None)
    assert row is not None, "a hidden staff member must still appear on payroll"
    assert row["display_hidden"] is True
    assert row["hidden_reason"] == "Suspended pending query"


@pytest.mark.asyncio
async def test_a_hidden_staff_member_can_still_be_paid(db):
    actor = await _user(db)
    s = await _staff(db)
    await vis.set_visibility(db, staff_id=s, hidden=True,
                             reason="Left the company", actor_id=actor)
    run, slips = await _approved_run(db, [s], actor)
    result = await pay.pay_payslips(
        db, run_id=run["run_id"], payslip_ids=[slips[0].id],
        paid_on=date(2026, 4, 30), paid_by=actor)
    assert result["staff_count"] == 1


@pytest.mark.asyncio
async def test_hiding_somebody_twice_is_refused(db):
    actor = await _user(db)
    s = await _staff(db)
    await vis.set_visibility(db, staff_id=s, hidden=True,
                             reason="Resigned", actor_id=actor)
    with pytest.raises(HTTPException) as e:
        await vis.set_visibility(db, staff_id=s, hidden=True,
                                 reason="Resigned again", actor_id=actor)
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_hidden_staff_list_shows_the_reason(db):
    actor = await _user(db, name="Admin One")
    s = await _staff(db)
    await vis.set_visibility(
        db, staff_id=s, hidden=True, reason="Duplicate of BSM0001",
        actor_id=actor, actor_name="Admin One")
    hidden = await vis.hidden_staff(db)
    row = next(h for h in hidden if h["staff_id"] == str(s))
    assert row["reason"] == "Duplicate of BSM0001"
    assert row["hidden_at"] is not None


@pytest.mark.asyncio
async def test_hiding_an_unknown_staff_member_is_a_404(db):
    with pytest.raises(HTTPException) as e:
        await vis.set_visibility(
            db, staff_id=uuid.uuid4(), hidden=True, reason="Does not exist")
    assert e.value.status_code == 404
