"""Staff operational wallet: prove the money arithmetic and the controls.

The claims tested here are the ones a managing director would refuse to take
on trust, and the ones an auditor would ask about:

  * the section 42 worked example lands exactly: N100,000 issued, N70,000
    spent, N30,000 returned, N0 outstanding -- to the naira;
  * the balance is derived from the ledger, and the cached column can never
    silently disagree with it;
  * an employee cannot spend money they were never given;
  * NOBODY approves their own expense -- not a supervisor, not an admin;
  * an approver cannot exceed the authority they were granted;
  * one employee cannot see, spend from, or approve on another's wallet;
  * a rejected expense does not move the balance, and an approved one moves it
    exactly once even if the request is retried;
  * concurrent expenses against the same wallet cannot both spend the same
    money;
  * the ledger, the audit log and receipts genuinely cannot be edited or
    deleted -- enforced by the database, not by the application;
  * corrections are reversals that leave the original visible;
  * with accounting posting switched on, the journal entries are the right
    ones and they balance.

Requires real PostgreSQL: the module depends on triggers, partial unique
indexes, row locking and NUMERIC arithmetic, none of which SQLite has.

    export TEST_DATABASE_URL='postgresql+asyncpg://postgres:pw@localhost:5432/astro_test'
    cd backend && pytest tests/test_wallet.py -v
"""
import asyncio
import importlib.util
import os
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.services import wallet as svc
from app.services.ledger import money

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")
SYNC_DB = (TEST_DB or "").replace("+asyncpg", "")


# The tables the wallet migration hangs off. Deliberately minimal: a test that
# needs the whole 90-table ERP schema to demonstrate an advance is testing the
# wrong thing. The wallet tables themselves are created by running the REAL
# migration, so this suite also proves the migration applies and its triggers
# work.
BASE_SCHEMA = """
DROP TABLE IF EXISTS wallet_flags CASCADE;
DROP TABLE IF EXISTS wallet_audit_logs CASCADE;
DROP TABLE IF EXISTS wallet_category_limits CASCADE;
DROP TABLE IF EXISTS wallet_approvers CASCADE;
DROP TABLE IF EXISTS wallet_approval_rules CASCADE;
DROP TABLE IF EXISTS wallet_receipts CASCADE;
DROP TABLE IF EXISTS wallet_reconciliations CASCADE;
DROP TABLE IF EXISTS wallet_reimbursements CASCADE;
DROP TABLE IF EXISTS wallet_returns CASCADE;
DROP TABLE IF EXISTS wallet_expenses CASCADE;
DROP TABLE IF EXISTS wallet_fund_requests CASCADE;
DROP TABLE IF EXISTS wallet_fundings CASCADE;
DROP TABLE IF EXISTS wallet_ledger CASCADE;
DROP TABLE IF EXISTS wallets CASCADE;
DROP TABLE IF EXISTS wallet_expense_categories CASCADE;
DROP TABLE IF EXISTS gl_journal_lines CASCADE;
DROP TABLE IF EXISTS gl_journal_entries CASCADE;
DROP TABLE IF EXISTS gl_periods CASCADE;
DROP TABLE IF EXISTS gl_accounts CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS staff CASCADE;
DROP TABLE IF EXISTS users CASCADE;

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    full_name VARCHAR(255) NOT NULL,
    hashed_password VARCHAR(255) NOT NULL DEFAULT 'x',
    role VARCHAR(50) NOT NULL DEFAULT 'production_staff',
    is_active BOOLEAN DEFAULT TRUE,
    is_locked BOOLEAN DEFAULT FALSE,
    department VARCHAR(100),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE staff (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id VARCHAR(32) UNIQUE NOT NULL,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    clock_pin VARCHAR(4) UNIQUE NOT NULL
);

CREATE TABLE customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL
);

CREATE TABLE gl_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(20) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    account_type VARCHAR(20) NOT NULL,
    normal_balance VARCHAR(6) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_postable BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE gl_periods (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(50) UNIQUE NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'OPEN'
);

CREATE TABLE gl_journal_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    seq BIGSERIAL NOT NULL,
    entry_number VARCHAR(64) UNIQUE NOT NULL,
    entry_date DATE NOT NULL,
    description TEXT NOT NULL,
    source_module VARCHAR(50) NOT NULL,
    source_reference VARCHAR(128),
    status VARCHAR(16) NOT NULL DEFAULT 'POSTED',
    reverses_entry_id UUID REFERENCES gl_journal_entries(id),
    posted_at TIMESTAMPTZ DEFAULT NOW(),
    created_by UUID,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE gl_journal_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entry_id UUID NOT NULL REFERENCES gl_journal_entries(id) ON DELETE CASCADE,
    account_id UUID NOT NULL REFERENCES gl_accounts(id),
    debit NUMERIC(18,2) NOT NULL DEFAULT 0,
    credit NUMERIC(18,2) NOT NULL DEFAULT 0,
    description TEXT,
    cost_centre VARCHAR(50),
    line_number INTEGER NOT NULL DEFAULT 0
);
"""

# The accounts the seeded categories map onto. Without these the migration's
# category seed would violate its foreign key.
ACCOUNTS = [
    ("1100", "Cash", "ASSET", "DEBIT"),
    ("1200", "Bank Accounts", "ASSET", "DEBIT"),
    ("5410", "Electricity", "EXPENSE", "DEBIT"),
    ("5440", "Factory Maintenance", "EXPENSE", "DEBIT"),
    ("5450", "Equipment Maintenance", "EXPENSE", "DEBIT"),
    ("6110", "Staff Welfare", "EXPENSE", "DEBIT"),
    ("6200", "Transportation & Logistics", "EXPENSE", "DEBIT"),
    ("6210", "Fuel", "EXPENSE", "DEBIT"),
    ("6300", "Marketing & Advertising", "EXPENSE", "DEBIT"),
    ("6400", "Internet & Telephone", "EXPENSE", "DEBIT"),
    ("6510", "Security", "EXPENSE", "DEBIT"),
    ("6520", "Cleaning", "EXPENSE", "DEBIT"),
    ("6600", "Office Expenses", "EXPENSE", "DEBIT"),
]


def _load_migration():
    """Import the wallet migration module by path.

    Running the real `upgrade()` rather than a hand-copied schema means this
    suite fails if the migration and the code drift apart -- which is the
    failure worth catching, since production runs the migration.
    """
    path = (Path(__file__).resolve().parents[1] / "alembic" / "versions"
            / "t9012345678s_staff_wallet.py")
    spec = importlib.util.spec_from_file_location("wallet_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeUser:
    """The shape the service reads off a User row: id, role, full_name."""

    def __init__(self, user_id, role="production_staff", full_name="Test",
                 department=None):
        self.id = user_id
        self.role = role
        self.full_name = full_name
        self.department = department


@pytest.fixture(scope="module")
def schema():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = create_engine(SYNC_DB, future=True)
    with engine.begin() as conn:
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        for statement in BASE_SCHEMA.split(";"):
            if statement.strip():
                conn.execute(text(statement))
        for code, name, atype, normal in ACCOUNTS:
            conn.execute(
                text("""INSERT INTO gl_accounts
                            (id, code, name, account_type, normal_balance)
                        VALUES (gen_random_uuid(), :c, :n, :t, :b)"""),
                {"c": code, "n": name, "t": atype, "b": normal})

    migration = _load_migration()
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration.upgrade()
    engine.dispose()
    yield
    # Left in place deliberately: when a test fails, being able to open the
    # database and look at the rows is worth more than a tidy teardown.


@pytest_asyncio.fixture
async def db(schema):
    engine = create_async_engine(TEST_DB, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


async def _make_user(session, name, role="production_staff", department=None):
    user_id = uuid.uuid4()
    await session.execute(
        text("""INSERT INTO users (id, email, full_name, role, department)
                VALUES (:i, :e, :n, :r, :d)"""),
        {"i": str(user_id), "e": f"{user_id}@example.test", "n": name,
         "r": role, "d": department})
    await session.commit()
    return FakeUser(user_id, role=role, full_name=name, department=department)


async def _make_wallet(session, user, wallet_type="FACTORY",
                       department="Factory", **limits):
    wallet_id = uuid.uuid4()
    await session.execute(
        text("""INSERT INTO wallets
                    (id, wallet_number, user_id, wallet_type, department,
                     purpose, single_txn_limit, daily_limit, monthly_limit,
                     self_approve_limit)
                VALUES (:i, :num, :u, :t, :d, 'Factory operations',
                        :stl, :dl, :ml, :sal)"""),
        {"i": str(wallet_id), "num": f"WAL-{str(wallet_id)[:8]}",
         "u": str(user.id), "t": wallet_type, "d": department,
         "stl": limits.get("single_txn_limit"),
         "dl": limits.get("daily_limit"),
         "ml": limits.get("monthly_limit"),
         "sal": limits.get("self_approve_limit")})
    await session.commit()
    return wallet_id


async def _category(session, code="FUEL"):
    row = (await session.execute(
        text("SELECT id FROM wallet_expense_categories WHERE code = :c"),
        {"c": code})).first()
    return row.id


async def _grant(session, user, tier="SUPERVISOR", max_amount=None,
                 department=None, can_fund=False, can_reconcile=False):
    await session.execute(
        text("""INSERT INTO wallet_approvers
                    (id, user_id, tier, max_amount, department, can_fund,
                     can_reconcile)
                VALUES (gen_random_uuid(), :u, :t, :m, :d, :f, :r)"""),
        {"u": str(user.id), "t": tier, "m": max_amount, "d": department,
         "f": can_fund, "r": can_reconcile})
    await session.commit()


# ---------------------------------------------------------------------------
# The worked example from the specification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_factory_supervisor_worked_example(db):
    """Section 42, to the naira.

    N100,000 issued; fuel 20,000 + maintenance 15,000 + transport 10,000 +
    supplies 25,000 = 70,000 spent; 30,000 returned; nothing outstanding.
    """
    supervisor = await _make_user(db, "Factory Supervisor",
                                  department="Factory")
    director = await _make_user(db, "Managing Director", role="admin")
    wallet_id = await _make_wallet(db, supervisor,
                                   self_approve_limit=Decimal("100000"))

    await svc.fund_wallet(
        db, wallet_id=wallet_id, amount=Decimal("100000"),
        purpose="Factory operational expenses",
        disbursement_method="BANK_TRANSFER", funded_on=date.today(),
        issued_by=director.id)
    await db.commit()

    spend = [("FUEL", "20000", "Diesel for the generator"),
             ("MAINTENANCE", "15000", "Sealing machine repair"),
             ("TRANSPORT", "10000", "Material haulage"),
             ("FACTORY", "25000", "Gloves and cleaning supplies")]
    for code, amount, purpose in spend:
        await svc.submit_expense(
            db, wallet_id=wallet_id, category_id=await _category(db, code),
            amount=Decimal(amount), spent_on=date.today(), purpose=purpose,
            submitted_by=supervisor.id)
        await db.commit()

    totals = await svc.derive_totals(db, wallet_id)
    assert totals["total_funded"] == money("100000")
    assert totals["total_spent"] == money("70000")
    assert totals["balance"] == money("30000")

    ret = await svc.declare_return(
        db, wallet_id=wallet_id, amount=Decimal("30000"),
        returned_on=date.today(), method="CASH", declared_by=supervisor.id)
    await db.commit()
    await svc.confirm_return(
        db, return_id=uuid.UUID(ret["id"]), confirm=True, user=director)
    await db.commit()

    summary = await svc.wallet_summary(db, wallet_id)
    assert summary["total_funded"] == "100000.00"
    assert summary["total_spent"] == "70000.00"
    assert summary["total_returned"] == "30000.00"
    assert summary["balance"] == "0.00"
    assert summary["outstanding"] == "0.00"

    integrity = await svc.verify_wallet_integrity(db, wallet_id)
    assert integrity["consistent"], integrity["problems"]


@pytest.mark.asyncio
async def test_balance_identity_holds_through_reversals(db):
    """funded + adjusted - spent - returned == net ledger movement, always.

    Reversals are the case where a naive implementation drifts: the reversal
    of an expense must reduce total_spent, not increase total_funded.
    """
    holder = await _make_user(db, "Ledger Holder")
    admin = await _make_user(db, "Admin One", role="admin")
    wallet_id = await _make_wallet(db, holder,
                                   self_approve_limit=Decimal("100000"))

    await svc.fund_wallet(
        db, wallet_id=wallet_id, amount=Decimal("50000"), purpose="Float",
        disbursement_method="CASH", funded_on=date.today(),
        issued_by=admin.id)
    await db.commit()

    expense = await svc.submit_expense(
        db, wallet_id=wallet_id, category_id=await _category(db, "FUEL"),
        amount=Decimal("12500.55"), spent_on=date.today(), purpose="Fuel",
        submitted_by=holder.id)
    await db.commit()
    assert (await svc.derive_totals(db, wallet_id))["balance"] == money("37499.45")

    await svc.reverse_expense(
        db, expense_id=uuid.UUID(expense["id"]),
        reason="Wrong amount keyed", actor_id=admin.id)
    await db.commit()

    totals = await svc.derive_totals(db, wallet_id)
    assert totals["balance"] == money("50000")
    assert totals["total_spent"] == money("0")
    assert totals["total_funded"] == money("50000")

    # The original expense row and both ledger entries are still there.
    rows = (await db.execute(
        text("SELECT COUNT(*) AS c FROM wallet_ledger WHERE wallet_id = :w"),
        {"w": str(wallet_id)})).first()
    assert rows.c == 3, "funding + expense + reversal must all remain"
    status = (await db.execute(
        text("SELECT status FROM wallet_expenses WHERE id = :i"),
        {"i": expense["id"]})).first()
    assert status.status == "REVERSED"


@pytest.mark.asyncio
async def test_cannot_spend_more_than_entrusted(db):
    holder = await _make_user(db, "Overspender")
    admin = await _make_user(db, "Admin Two", role="admin")
    wallet_id = await _make_wallet(db, holder,
                                   self_approve_limit=Decimal("100000"))

    await svc.fund_wallet(
        db, wallet_id=wallet_id, amount=Decimal("10000"), purpose="Small float",
        disbursement_method="CASH", funded_on=date.today(), issued_by=admin.id)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.submit_expense(
            db, wallet_id=wallet_id, category_id=await _category(db, "FUEL"),
            amount=Decimal("10000.01"), spent_on=date.today(),
            purpose="One naira too far", submitted_by=holder.id)
    await db.rollback()
    assert exc.value.status_code == 400
    assert "reimbursement" in exc.value.detail.lower(), (
        "the error must point the employee at the right mechanism")


@pytest.mark.asyncio
async def test_pending_expenses_encumber_the_balance(db):
    """Two claims against the same money: the second must be refused.

    Without counting PENDING claims against the balance, an employee could
    submit the same N10,000 five times while all five awaited approval.
    """
    holder = await _make_user(db, "Double Claimer")
    admin = await _make_user(db, "Admin Three", role="admin")
    # No self-approval, so the first expense stays PENDING.
    wallet_id = await _make_wallet(db, holder,
                                   self_approve_limit=Decimal("0"))

    await svc.fund_wallet(
        db, wallet_id=wallet_id, amount=Decimal("10000"), purpose="Float",
        disbursement_method="CASH", funded_on=date.today(), issued_by=admin.id)
    await db.commit()

    await svc.submit_expense(
        db, wallet_id=wallet_id, category_id=await _category(db, "FUEL"),
        amount=Decimal("8000"), spent_on=date.today(), purpose="First",
        submitted_by=holder.id)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.submit_expense(
            db, wallet_id=wallet_id, category_id=await _category(db, "FUEL"),
            amount=Decimal("8000"), spent_on=date.today(), purpose="Second",
            submitted_by=holder.id)
    await db.rollback()
    assert "awaiting approval" in exc.value.detail


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_nobody_approves_their_own_expense_not_even_an_admin(db):
    """The single control the whole module rests on.

    An administrator who holds a wallet still needs a second person for
    anything above their self-approval band. That is the intended cost of
    segregation of duties, not an oversight.
    """
    admin_holder = await _make_user(db, "Admin Holder", role="admin")
    wallet_id = await _make_wallet(db, admin_holder,
                                   self_approve_limit=Decimal("0"))
    funder = await _make_user(db, "Other Admin", role="admin")

    await svc.fund_wallet(
        db, wallet_id=wallet_id, amount=Decimal("60000"), purpose="Float",
        disbursement_method="CASH", funded_on=date.today(),
        issued_by=funder.id)
    await db.commit()

    expense = await svc.submit_expense(
        db, wallet_id=wallet_id, category_id=await _category(db, "FUEL"),
        amount=Decimal("55000"), spent_on=date.today(), purpose="Diesel",
        submitted_by=admin_holder.id)
    await db.commit()
    assert expense["status"] == "PENDING"

    with pytest.raises(HTTPException) as exc:
        await svc.decide_expense(
            db, expense_id=uuid.UUID(expense["id"]), approve=True,
            user=admin_holder)
    await db.rollback()
    assert exc.value.status_code == 403
    assert "your own" in exc.value.detail

    # A different administrator can. (The receipt requirement is satisfied
    # first: FUEL is a receipt-mandatory category.)
    await svc.attach_receipt(
        db, filename="diesel.jpg", content_type="image/jpeg",
        content=b"diesel-receipt", uploaded_by=admin_holder.id,
        expense_id=uuid.UUID(expense["id"]))
    await db.commit()
    result = await svc.decide_expense(
        db, expense_id=uuid.UUID(expense["id"]), approve=True, user=funder)
    await db.commit()
    assert result["status"] == "APPROVED"


@pytest.mark.asyncio
async def test_approver_cannot_exceed_granted_authority(db):
    holder = await _make_user(db, "Spender", department="Factory")
    admin = await _make_user(db, "Admin Four", role="admin")
    supervisor = await _make_user(db, "Line Supervisor", department="Factory")
    await _grant(db, supervisor, tier="SUPERVISOR",
                 max_amount=Decimal("20000"), department="Factory")

    wallet_id = await _make_wallet(db, holder,
                                   self_approve_limit=Decimal("0"))
    await svc.fund_wallet(
        db, wallet_id=wallet_id, amount=Decimal("80000"), purpose="Float",
        disbursement_method="CASH", funded_on=date.today(), issued_by=admin.id)
    await db.commit()

    big = await svc.submit_expense(
        db, wallet_id=wallet_id, category_id=await _category(db, "FUEL"),
        amount=Decimal("45000"), spent_on=date.today(), purpose="Big fuel buy",
        submitted_by=holder.id)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.decide_expense(
            db, expense_id=uuid.UUID(big["id"]), approve=True,
            user=supervisor)
    await db.rollback()
    assert exc.value.status_code == 403
    assert "exceeds your authority" in exc.value.detail

    # Within the ceiling, the same supervisor can approve.
    small = await svc.submit_expense(
        db, wallet_id=wallet_id, category_id=await _category(db, "FUEL"),
        amount=Decimal("15000"), spent_on=date.today(), purpose="Small buy",
        submitted_by=holder.id)
    await db.commit()
    await svc.attach_receipt(
        db, filename="small.jpg", content_type="image/jpeg",
        content=b"small-buy-receipt", uploaded_by=holder.id,
        expense_id=uuid.UUID(small["id"]))
    await db.commit()
    assert (await svc.decide_expense(
        db, expense_id=uuid.UUID(small["id"]), approve=True,
        user=supervisor))["status"] == "APPROVED"
    await db.commit()


@pytest.mark.asyncio
async def test_staff_cannot_see_another_employees_wallet(db):
    owner = await _make_user(db, "Wallet Owner", department="Factory")
    stranger = await _make_user(db, "Nosy Colleague", department="Sales")
    wallet_id = await _make_wallet(db, owner)
    wallet = await svc.get_wallet(db, wallet_id)

    with pytest.raises(HTTPException) as exc:
        await svc.assert_can_view_wallet(db, stranger, wallet)
    assert exc.value.status_code == 403

    # The holder can, and so can an administrator.
    await svc.assert_can_view_wallet(db, owner, wallet)
    admin = await _make_user(db, "Admin Five", role="admin")
    await svc.assert_can_view_wallet(db, admin, wallet)


@pytest.mark.asyncio
async def test_approval_queue_never_offers_your_own_or_out_of_reach_work(db):
    holder = await _make_user(db, "Queue Holder", department="Factory")
    admin = await _make_user(db, "Admin Six", role="admin")
    supervisor = await _make_user(db, "Queue Supervisor", department="Factory")
    await _grant(db, supervisor, tier="SUPERVISOR",
                 max_amount=Decimal("20000"), department="Factory")

    wallet_id = await _make_wallet(db, holder, self_approve_limit=Decimal("0"))
    await svc.fund_wallet(
        db, wallet_id=wallet_id, amount=Decimal("90000"), purpose="Float",
        disbursement_method="CASH", funded_on=date.today(), issued_by=admin.id)
    await db.commit()

    for amount, purpose in (("5000", "In reach"), ("45000", "Out of reach")):
        await svc.submit_expense(
            db, wallet_id=wallet_id, category_id=await _category(db, "FUEL"),
            amount=Decimal(amount), spent_on=date.today(), purpose=purpose,
            submitted_by=holder.id)
        await db.commit()

    queue = await svc.approval_queue(db, supervisor)
    purposes = {q["purpose"] for q in queue}
    assert "In reach" in purposes
    assert "Out of reach" not in purposes, (
        "the queue must not show work that would be refused on click")

    # The holder sees none of their own submissions in their queue.
    assert not [q for q in await svc.approval_queue(db, holder)
                if q["wallet_id"] == str(wallet_id)]


# ---------------------------------------------------------------------------
# Approval ladder and limits
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_approval_tier_follows_the_configured_ladder(db):
    holder = await _make_user(db, "Tier Holder")
    wallet_id = await _make_wallet(db, holder)
    wallet = await svc.get_wallet(db, wallet_id)

    assert await svc.resolve_required_tier(db, wallet, money("5000")) == "SELF"
    assert await svc.resolve_required_tier(
        db, wallet, money("25000")) == "SUPERVISOR"
    assert await svc.resolve_required_tier(
        db, wallet, money("75000")) == "MANAGEMENT"

    # Boundaries: the seeded bands are [0,10000) [10000,50000) [50000,inf).
    assert await svc.resolve_required_tier(
        db, wallet, money("9999.99")) == "SELF"
    assert await svc.resolve_required_tier(
        db, wallet, money("10000")) == "SUPERVISOR"
    assert await svc.resolve_required_tier(
        db, wallet, money("50000")) == "MANAGEMENT"


@pytest.mark.asyncio
async def test_thresholds_are_data_not_code(db):
    """Management retunes the ladder; behaviour changes with no deployment."""
    holder = await _make_user(db, "Rule Holder")
    wallet_id = await _make_wallet(db, holder, wallet_type="MARKETING",
                                   department="Marketing")
    wallet = await svc.get_wallet(db, wallet_id)
    assert await svc.resolve_required_tier(db, wallet, money("5000")) == "SELF"

    await db.execute(
        text("""INSERT INTO wallet_approval_rules
                    (id, scope, wallet_type, min_amount, max_amount, tier,
                     priority)
                VALUES (gen_random_uuid(), 'WALLET_TYPE', 'MARKETING', 0,
                        2000, 'SELF', 10),
                       (gen_random_uuid(), 'WALLET_TYPE', 'MARKETING', 2000,
                        NULL, 'SUPERVISOR', 10)"""))
    await db.commit()

    assert await svc.resolve_required_tier(
        db, wallet, money("5000")) == "SUPERVISOR", (
        "the more specific WALLET_TYPE rule must win over the global ladder")


@pytest.mark.asyncio
async def test_limits_are_enforced_server_side(db):
    holder = await _make_user(db, "Limited Holder")
    admin = await _make_user(db, "Admin Seven", role="admin")
    wallet_id = await _make_wallet(
        db, holder, single_txn_limit=Decimal("20000"),
        daily_limit=Decimal("30000"), self_approve_limit=Decimal("100000"))

    await svc.fund_wallet(
        db, wallet_id=wallet_id, amount=Decimal("200000"), purpose="Float",
        disbursement_method="BANK_TRANSFER", funded_on=date.today(),
        issued_by=admin.id)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.submit_expense(
            db, wallet_id=wallet_id, category_id=await _category(db, "FUEL"),
            amount=Decimal("25000"), spent_on=date.today(),
            purpose="Over the single-transaction cap",
            submitted_by=holder.id)
    await db.rollback()
    assert "single-transaction limit" in exc.value.detail

    for purpose in ("Morning", "Afternoon"):
        await svc.submit_expense(
            db, wallet_id=wallet_id, category_id=await _category(db, "FUEL"),
            amount=Decimal("15000"), spent_on=date.today(), purpose=purpose,
            submitted_by=holder.id)
        await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.submit_expense(
            db, wallet_id=wallet_id, category_id=await _category(db, "FUEL"),
            amount=Decimal("1000"), spent_on=date.today(),
            purpose="Over the daily cap", submitted_by=holder.id)
    await db.rollback()
    assert "Daily limit" in exc.value.detail


@pytest.mark.asyncio
async def test_receipt_is_required_where_the_category_says_so(db):
    holder = await _make_user(db, "Receiptless")
    admin = await _make_user(db, "Admin Eight", role="admin")
    approver = await _make_user(db, "Receipt Approver")
    await _grant(db, approver, tier="MANAGEMENT")

    wallet_id = await _make_wallet(db, holder, self_approve_limit=Decimal("0"))
    await svc.fund_wallet(
        db, wallet_id=wallet_id, amount=Decimal("50000"), purpose="Float",
        disbursement_method="CASH", funded_on=date.today(), issued_by=admin.id)
    await db.commit()

    expense = await svc.submit_expense(
        db, wallet_id=wallet_id, category_id=await _category(db, "FUEL"),
        amount=Decimal("9000"), spent_on=date.today(), purpose="Diesel",
        submitted_by=holder.id)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.decide_expense(
            db, expense_id=uuid.UUID(expense["id"]), approve=True,
            user=approver)
    await db.rollback()
    assert "require a receipt" in exc.value.detail

    await svc.attach_receipt(
        db, filename="fuel.jpg", content_type="image/jpeg",
        content=b"receipt-image-bytes", uploaded_by=holder.id,
        expense_id=uuid.UUID(expense["id"]))
    await db.commit()

    assert (await svc.decide_expense(
        db, expense_id=uuid.UUID(expense["id"]), approve=True,
        user=approver))["status"] == "APPROVED"
    await db.commit()


# ---------------------------------------------------------------------------
# Rejection, returns, idempotency, concurrency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rejected_expense_leaves_the_balance_untouched(db):
    holder = await _make_user(db, "Rejected Holder")
    admin = await _make_user(db, "Admin Nine", role="admin")
    wallet_id = await _make_wallet(db, holder, self_approve_limit=Decimal("0"))

    await svc.fund_wallet(
        db, wallet_id=wallet_id, amount=Decimal("40000"), purpose="Float",
        disbursement_method="CASH", funded_on=date.today(), issued_by=admin.id)
    await db.commit()

    expense = await svc.submit_expense(
        db, wallet_id=wallet_id, category_id=await _category(db, "FUEL"),
        amount=Decimal("12000"), spent_on=date.today(), purpose="Questionable",
        submitted_by=holder.id)
    await db.commit()

    await svc.decide_expense(
        db, expense_id=uuid.UUID(expense["id"]), approve=False, user=admin,
        note="No receipt and no explanation")
    await db.commit()

    totals = await svc.derive_totals(db, wallet_id)
    assert totals["balance"] == money("40000"), (
        "a rejected expense discharges nothing -- the employee still holds "
        "the money and still answers for it")
    assert totals["total_spent"] == money("0")


@pytest.mark.asyncio
async def test_declared_return_only_moves_the_balance_once_confirmed(db):
    holder = await _make_user(db, "Returner")
    finance = await _make_user(db, "Finance Officer")
    await _grant(db, finance, tier="FINANCE", can_reconcile=True)
    admin = await _make_user(db, "Admin Ten", role="admin")

    wallet_id = await _make_wallet(db, holder)
    await svc.fund_wallet(
        db, wallet_id=wallet_id, amount=Decimal("25000"), purpose="Float",
        disbursement_method="CASH", funded_on=date.today(), issued_by=admin.id)
    await db.commit()

    ret = await svc.declare_return(
        db, wallet_id=wallet_id, amount=Decimal("25000"),
        returned_on=date.today(), method="CASH", declared_by=holder.id)
    await db.commit()
    assert (await svc.derive_totals(db, wallet_id))["balance"] == money("25000")

    # The person who declared it cannot confirm their own return.
    with pytest.raises(HTTPException) as exc:
        await svc.confirm_return(
            db, return_id=uuid.UUID(ret["id"]), confirm=True, user=holder)
    await db.rollback()
    assert exc.value.status_code == 403

    await svc.confirm_return(
        db, return_id=uuid.UUID(ret["id"]), confirm=True, user=finance)
    await db.commit()
    assert (await svc.derive_totals(db, wallet_id))["balance"] == money("0")


@pytest.mark.asyncio
async def test_retried_funding_with_the_same_key_is_recorded_once(db):
    """A double-tap on a bad connection must not issue the advance twice."""
    holder = await _make_user(db, "Idempotent Holder")
    admin = await _make_user(db, "Admin Eleven", role="admin")
    wallet_id = await _make_wallet(db, holder)
    key = f"fund-{uuid.uuid4()}"

    await svc.fund_wallet(
        db, wallet_id=wallet_id, amount=Decimal("30000"), purpose="Float",
        disbursement_method="BANK_TRANSFER", funded_on=date.today(),
        issued_by=admin.id, idempotency_key=key)
    await db.commit()

    with pytest.raises(Exception):
        await svc.fund_wallet(
            db, wallet_id=wallet_id, amount=Decimal("30000"),
            purpose="Float (retry)", disbursement_method="BANK_TRANSFER",
            funded_on=date.today(), issued_by=admin.id, idempotency_key=key)
        await db.commit()
    await db.rollback()

    assert (await svc.derive_totals(db, wallet_id))["balance"] == money("30000")


@pytest.mark.asyncio
async def test_concurrent_expenses_cannot_spend_the_same_money_twice(db):
    """Two phones, one balance, simultaneous submits.

    Both transactions read the balance and decide independently. Without the
    FOR UPDATE lock on the wallet row, both would see N10,000 available and
    both would be accepted, leaving the wallet N6,000 overdrawn -- an
    accountability figure that cannot be true.
    """
    holder = await _make_user(db, "Race Holder")
    admin = await _make_user(db, "Admin Twelve", role="admin")
    wallet_id = await _make_wallet(db, holder,
                                   self_approve_limit=Decimal("100000"))

    await svc.fund_wallet(
        db, wallet_id=wallet_id, amount=Decimal("10000"), purpose="Float",
        disbursement_method="CASH", funded_on=date.today(), issued_by=admin.id)
    await db.commit()

    category_id = await _category(db, "FUEL")
    engine = create_async_engine(TEST_DB, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def spend(purpose):
        async with maker() as session:
            try:
                await svc.submit_expense(
                    session, wallet_id=wallet_id, category_id=category_id,
                    amount=Decimal("8000"), spent_on=date.today(),
                    purpose=purpose, submitted_by=holder.id)
                await session.commit()
                return "accepted"
            except HTTPException:
                await session.rollback()
                return "refused"
            except Exception:
                await session.rollback()
                return "error"

    results = await asyncio.gather(spend("Phone A"), spend("Phone B"))
    await engine.dispose()

    assert results.count("accepted") == 1, (
        f"exactly one of the two N8,000 expenses may be accepted, got "
        f"{results}")
    totals = await svc.derive_totals(db, wallet_id)
    assert totals["balance"] == money("2000")
    assert totals["balance"] >= 0


# ---------------------------------------------------------------------------
# Immutability, enforced by the database
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ledger_rows_cannot_be_updated_or_deleted(db):
    holder = await _make_user(db, "Immutable Holder")
    admin = await _make_user(db, "Admin Thirteen", role="admin")
    wallet_id = await _make_wallet(db, holder)
    await svc.fund_wallet(
        db, wallet_id=wallet_id, amount=Decimal("15000"), purpose="Float",
        disbursement_method="CASH", funded_on=date.today(), issued_by=admin.id)
    await db.commit()

    entry = (await db.execute(
        text("SELECT id FROM wallet_ledger WHERE wallet_id = :w"),
        {"w": str(wallet_id)})).first()

    for statement in (
        "UPDATE wallet_ledger SET amount = 1 WHERE id = :i",
        "DELETE FROM wallet_ledger WHERE id = :i",
    ):
        with pytest.raises(Exception) as exc:
            await db.execute(text(statement), {"i": str(entry.id)})
            await db.commit()
        await db.rollback()
        assert "append-only" in str(exc.value)


@pytest.mark.asyncio
async def test_audit_log_and_receipts_cannot_be_rewritten(db):
    holder = await _make_user(db, "Audit Holder")
    admin = await _make_user(db, "Admin Fourteen", role="admin")
    wallet_id = await _make_wallet(db, holder,
                                   self_approve_limit=Decimal("100000"))
    await svc.fund_wallet(
        db, wallet_id=wallet_id, amount=Decimal("20000"), purpose="Float",
        disbursement_method="CASH", funded_on=date.today(), issued_by=admin.id)
    expense = await svc.submit_expense(
        db, wallet_id=wallet_id, category_id=await _category(db, "FUEL"),
        amount=Decimal("3000"), spent_on=date.today(), purpose="Diesel",
        submitted_by=holder.id)
    await svc.attach_receipt(
        db, filename="r.jpg", content_type="image/jpeg",
        content=b"evidence", uploaded_by=holder.id,
        expense_id=uuid.UUID(expense["id"]))
    await db.commit()

    with pytest.raises(Exception):
        await db.execute(text("DELETE FROM wallet_audit_logs"))
        await db.commit()
    await db.rollback()

    with pytest.raises(Exception):
        await db.execute(
            text("UPDATE wallet_receipts SET content = :c WHERE expense_id = :e"),
            {"c": b"tampered", "e": expense["id"]})
        await db.commit()
    await db.rollback()


@pytest.mark.asyncio
async def test_a_decided_expense_cannot_be_edited_back_into_the_queue(db):
    """The database refuses what the API would never ask for.

    Editing a rejected N80,000 claim down to N8,000 and re-submitting it is
    the attack this guard exists for.
    """
    holder = await _make_user(db, "Editor")
    admin = await _make_user(db, "Admin Fifteen", role="admin")
    wallet_id = await _make_wallet(db, holder, self_approve_limit=Decimal("0"))
    await svc.fund_wallet(
        db, wallet_id=wallet_id, amount=Decimal("100000"), purpose="Float",
        disbursement_method="CASH", funded_on=date.today(), issued_by=admin.id)
    expense = await svc.submit_expense(
        db, wallet_id=wallet_id, category_id=await _category(db, "FUEL"),
        amount=Decimal("80000"), spent_on=date.today(), purpose="Large",
        submitted_by=holder.id)
    await db.commit()
    await svc.decide_expense(
        db, expense_id=uuid.UUID(expense["id"]), approve=False, user=admin,
        note="Not justified")
    await db.commit()

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("UPDATE wallet_expenses SET amount = 8000 WHERE id = :i"),
            {"i": expense["id"]})
        await db.commit()
    await db.rollback()
    assert "immutable" in str(exc.value)

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("UPDATE wallet_expenses SET status = 'PENDING' WHERE id = :i"),
            {"i": expense["id"]})
        await db.commit()
    await db.rollback()
    assert "already been" in str(exc.value)


# ---------------------------------------------------------------------------
# Anomaly detection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_duplicate_receipt_image_is_flagged_not_blocked(db):
    holder = await _make_user(db, "Duplicate Holder")
    admin = await _make_user(db, "Admin Sixteen", role="admin")
    wallet_id = await _make_wallet(db, holder,
                                   self_approve_limit=Decimal("100000"))
    await svc.fund_wallet(
        db, wallet_id=wallet_id, amount=Decimal("50000"), purpose="Float",
        disbursement_method="CASH", funded_on=date.today(), issued_by=admin.id)

    image = b"the-very-same-photograph"
    first = await svc.submit_expense(
        db, wallet_id=wallet_id, category_id=await _category(db, "FUEL"),
        amount=Decimal("4000"), spent_on=date.today(), purpose="Fuel one",
        submitted_by=holder.id)
    await svc.attach_receipt(
        db, filename="a.jpg", content_type="image/jpeg", content=image,
        uploaded_by=holder.id, expense_id=uuid.UUID(first["id"]))
    await db.commit()

    second = await svc.submit_expense(
        db, wallet_id=wallet_id, category_id=await _category(db, "FUEL"),
        amount=Decimal("4000"), spent_on=date.today(), purpose="Fuel two",
        submitted_by=holder.id)
    result = await svc.attach_receipt(
        db, filename="b.jpg", content_type="image/jpeg", content=image,
        uploaded_by=holder.id, expense_id=uuid.UUID(second["id"]))
    await db.commit()

    assert result["duplicate_of"] == first["expense_reference"]
    flag = (await db.execute(
        text("""SELECT flag_type, severity FROM wallet_flags
                 WHERE expense_id = :e AND flag_type = 'DUPLICATE_RECEIPT'"""),
        {"e": second["id"]})).first()
    assert flag is not None and flag.severity == "HIGH"

    # Flagged, but the upload succeeded and the expense still stands: the
    # system raises a question, it does not pass judgement.
    status = (await db.execute(
        text("SELECT status FROM wallet_expenses WHERE id = :i"),
        {"i": second["id"]})).first()
    assert status.status == "APPROVED"


@pytest.mark.asyncio
async def test_threshold_splitting_is_flagged(db):
    """Section 22: repeated amounts just under the approval threshold."""
    holder = await _make_user(db, "Splitter")
    admin = await _make_user(db, "Admin Seventeen", role="admin")
    wallet_id = await _make_wallet(db, holder,
                                   self_approve_limit=Decimal("100000"))
    await svc.fund_wallet(
        db, wallet_id=wallet_id, amount=Decimal("300000"), purpose="Float",
        disbursement_method="BANK_TRANSFER", funded_on=date.today(),
        issued_by=admin.id)
    await db.commit()

    flags = []
    for amount in ("49500", "49700", "49800"):
        result = await svc.submit_expense(
            db, wallet_id=wallet_id, category_id=await _category(db, "FUEL"),
            amount=Decimal(amount), spent_on=date.today(),
            purpose=f"Purchase {amount}", submitted_by=holder.id)
        await db.commit()
        flags = result["flags"]

    assert "THRESHOLD_SPLITTING" in flags, (
        "three purchases at 49,500 / 49,700 / 49,800 against a 50,000 "
        "threshold is exactly the pattern section 22 asks about")


@pytest.mark.asyncio
async def test_frequent_topup_requests_are_flagged(db):
    holder = await _make_user(db, "Frequent Asker")
    wallet_id = await _make_wallet(db, holder)

    flags = []
    for i in range(4):
        result = await svc.request_funds(
            db, wallet_id=wallet_id, amount=Decimal("10000"),
            reason=f"Request {i}", urgency="NORMAL", requested_by=holder.id)
        await db.commit()
        flags = result["flags"]
    assert "FREQUENT_TOPUP" in flags


# ---------------------------------------------------------------------------
# Outstanding accountability
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_outstanding_counts_only_advances_past_their_due_date(db):
    """Money issued today with 30 days to account for it is not outstanding.

    Only once the deadline passes does it become an accountability failure.
    An advance with no deadline never becomes outstanding, because the system
    must not invent a deadline and then accuse someone of missing it.
    """
    holder = await _make_user(db, "Outstanding Holder")
    admin = await _make_user(db, "Admin Eighteen", role="admin")
    wallet_id = await _make_wallet(db, holder)

    await svc.fund_wallet(
        db, wallet_id=wallet_id, amount=Decimal("40000"),
        purpose="Due in the future", disbursement_method="CASH",
        funded_on=date.today(), account_by=date.today() + timedelta(days=30),
        issued_by=admin.id)
    await db.commit()
    assert await svc.outstanding_amount(db, wallet_id) == money("0")

    await svc.fund_wallet(
        db, wallet_id=wallet_id, amount=Decimal("25000"),
        purpose="Already overdue", disbursement_method="CASH",
        funded_on=date.today() - timedelta(days=60),
        account_by=date.today() - timedelta(days=30), issued_by=admin.id)
    await db.commit()
    assert await svc.outstanding_amount(db, wallet_id) == money("25000")

    await svc.fund_wallet(
        db, wallet_id=wallet_id, amount=Decimal("10000"),
        purpose="No deadline set", disbursement_method="CASH",
        funded_on=date.today(), issued_by=admin.id)
    await db.commit()
    assert await svc.outstanding_amount(db, wallet_id) == money("25000"), (
        "an advance with no accounting deadline is not overdue")


@pytest.mark.asyncio
async def test_reconciliation_reports_the_variance_the_holder_declares(db):
    holder = await _make_user(db, "Reconciler")
    admin = await _make_user(db, "Admin Nineteen", role="admin")
    wallet_id = await _make_wallet(db, holder,
                                   self_approve_limit=Decimal("100000"))

    await svc.fund_wallet(
        db, wallet_id=wallet_id, amount=Decimal("100000"),
        purpose="Month float", disbursement_method="BANK_TRANSFER",
        funded_on=date.today(), issued_by=admin.id)
    await svc.submit_expense(
        db, wallet_id=wallet_id, category_id=await _category(db, "FUEL"),
        amount=Decimal("70000"), spent_on=date.today(), purpose="Diesel",
        submitted_by=holder.id)
    await db.commit()

    result = await svc.submit_reconciliation(
        db, wallet_id=wallet_id,
        period_start=date.today() - timedelta(days=1),
        period_end=date.today(), declared_cash_on_hand=Decimal("28000"),
        submitted_by=holder.id)
    await db.commit()

    assert result["closing_balance"] == "30000.00"
    assert result["variance"] == "-2000.00", (
        "the holder says they hold 28,000 against a computed 30,000; the "
        "2,000 gap is exactly what management needs to see")

    # The submitter cannot settle their own reconciliation.
    with pytest.raises(HTTPException) as exc:
        await svc.review_reconciliation(
            db, reconciliation_id=uuid.UUID(result["id"]), accept=True,
            user=holder)
    await db.rollback()
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Reimbursements
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reimbursement_never_touches_a_wallet_balance(db):
    """The employee spent their own money; nothing was entrusted."""
    holder = await _make_user(db, "Out Of Pocket", department="Factory")
    admin = await _make_user(db, "Admin Twenty", role="admin")
    wallet_id = await _make_wallet(db, holder)

    await svc.fund_wallet(
        db, wallet_id=wallet_id, amount=Decimal("20000"), purpose="Float",
        disbursement_method="CASH", funded_on=date.today(), issued_by=admin.id)
    await db.commit()
    before = await svc.derive_totals(db, wallet_id)

    claim = await svc.submit_reimbursement(
        db, user_id=holder.id, category_id=await _category(db, "MAINTENANCE"),
        amount=Decimal("15000"), spent_on=date.today(),
        purpose="Emergency machine repair paid personally",
        wallet_id=wallet_id)
    await db.commit()
    await svc.decide_reimbursement(
        db, reimbursement_id=uuid.UUID(claim["id"]), approve=True, user=admin)
    await db.commit()

    after = await svc.derive_totals(db, wallet_id)
    assert after == before, (
        "approving a reimbursement must not change the wallet's funded, spent "
        "or balance figures")


@pytest.mark.asyncio
async def test_nobody_approves_their_own_reimbursement_claim(db):
    holder = await _make_user(db, "Self Claimer")
    await _grant(db, holder, tier="MANAGEMENT")
    claim = await svc.submit_reimbursement(
        db, user_id=holder.id, category_id=await _category(db, "TRANSPORT"),
        amount=Decimal("5000"), spent_on=date.today(),
        purpose="Taxi paid personally")
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.decide_reimbursement(
            db, reimbursement_id=uuid.UUID(claim["id"]), approve=True,
            user=holder)
    await db.rollback()
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# General ledger integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_journal_entries_are_correct_when_posting_is_enabled(
        db, monkeypatch):
    """With accounting on: advance is an asset, expense reclassifies it.

    This is the claim that makes the module trustworthy to a finance manager:
    funding must NOT hit the P&L, and the expense must credit the advance
    account rather than the bank a second time.
    """
    monkeypatch.setenv("ACCOUNTING_POSTING_ENABLED", "true")
    monkeypatch.delenv("ACCOUNTING_CUTOVER_DATE", raising=False)

    holder = await _make_user(db, "Posting Holder")
    admin = await _make_user(db, "Admin TwentyOne", role="admin")
    wallet_id = await _make_wallet(db, holder,
                                   self_approve_limit=Decimal("100000"))

    funding = await svc.fund_wallet(
        db, wallet_id=wallet_id, amount=Decimal("100000"),
        purpose="Factory operations", disbursement_method="BANK_TRANSFER",
        funded_on=date.today(), issued_by=admin.id)
    await db.commit()
    assert funding["journal_entry_id"] is not None

    lines = (await db.execute(
        text("""SELECT a.code, l.debit, l.credit
                  FROM gl_journal_lines l
                  JOIN gl_accounts a ON a.id = l.account_id
                 WHERE l.entry_id = :e ORDER BY a.code"""),
        {"e": funding["journal_entry_id"]})).mappings().all()
    booked = {r["code"]: (money(r["debit"]), money(r["credit"])) for r in lines}
    assert booked["1120"] == (money("100000"), money("0")), (
        "the advance is an asset: a claim on the employee")
    assert booked["1200"] == (money("0"), money("100000"))

    expense = await svc.submit_expense(
        db, wallet_id=wallet_id, category_id=await _category(db, "FUEL"),
        amount=Decimal("20000"), spent_on=date.today(), purpose="Diesel",
        submitted_by=holder.id)
    await db.commit()

    entry = (await db.execute(
        text("SELECT journal_entry_id FROM wallet_expenses WHERE id = :i"),
        {"i": expense["id"]})).first()
    assert entry.journal_entry_id is not None

    lines = (await db.execute(
        text("""SELECT a.code, l.debit, l.credit
                  FROM gl_journal_lines l
                  JOIN gl_accounts a ON a.id = l.account_id
                 WHERE l.entry_id = :e ORDER BY a.code"""),
        {"e": entry.journal_entry_id})).mappings().all()
    booked = {r["code"]: (money(r["debit"]), money(r["credit"])) for r in lines}
    assert booked["6210"] == (money("20000"), money("0")), "Fuel expense"
    assert booked["1120"] == (money("0"), money("20000")), (
        "the advance is discharged, not the bank debited twice")

    # Every entry this module produced balances.
    unbalanced = (await db.execute(
        text("""SELECT e.entry_number
                  FROM gl_journal_entries e
                  JOIN gl_journal_lines l ON l.entry_id = e.id
                 WHERE e.source_module = 'wallet'
                 GROUP BY e.id, e.entry_number
                HAVING SUM(l.debit) <> SUM(l.credit)"""))).all()
    assert not unbalanced, f"unbalanced journal entries: {unbalanced}"


@pytest.mark.asyncio
async def test_posting_is_off_by_default(db, monkeypatch):
    """Deploying this code must not start writing to the books by itself."""
    monkeypatch.delenv("ACCOUNTING_POSTING_ENABLED", raising=False)
    holder = await _make_user(db, "Unposted Holder")
    admin = await _make_user(db, "Admin TwentyTwo", role="admin")
    wallet_id = await _make_wallet(db, holder)

    funding = await svc.fund_wallet(
        db, wallet_id=wallet_id, amount=Decimal("10000"), purpose="Float",
        disbursement_method="CASH", funded_on=date.today(), issued_by=admin.id)
    await db.commit()

    assert funding["journal_entry_id"] is None
    assert (await svc.derive_totals(db, wallet_id))["balance"] == money("10000"), (
        "the wallet works fully with accounting posting switched off")
