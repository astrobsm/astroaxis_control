"""Prove the three revenue definitions now agree.

The ERP had three non-communicating notions of "revenue":
  sales_orders.payment_status, invoices.paid_amount, and payments rows.
These tests assert they can no longer diverge, because the latter two are
derived from the payment rows rather than assigned independently.

Requires real PostgreSQL (row locking, FOR UPDATE):

    export TEST_DATABASE_URL='postgresql+asyncpg://postgres:pw@localhost:5432/astro_test'
    cd backend && pytest tests/test_receivables.py -v
"""
import asyncio
import os
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.services.receivables import (
    record_payment, delete_payment, recompute_invoice_paid,
    revenue_between, outstanding_receivables, reconciliation_report, money)

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")

SCHEMA = """
DROP TABLE IF EXISTS payments CASCADE;
DROP TABLE IF EXISTS invoice_lines CASCADE;
DROP TABLE IF EXISTS invoices CASCADE;
DROP TABLE IF EXISTS sales_order_lines CASCADE;
DROP TABLE IF EXISTS sales_orders CASCADE;

CREATE TABLE sales_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_number VARCHAR(64) UNIQUE NOT NULL,
    customer_id UUID NOT NULL,
    payment_status VARCHAR(32) NOT NULL DEFAULT 'unpaid',
    payment_date TIMESTAMPTZ,
    total_amount NUMERIC(18,2) DEFAULT 0
);
CREATE TABLE sales_order_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sales_order_id UUID NOT NULL REFERENCES sales_orders(id),
    product_id UUID NOT NULL,
    quantity NUMERIC(18,6) NOT NULL,
    unit_price NUMERIC(18,6) NOT NULL,
    line_total NUMERIC(18,2) NOT NULL
);
CREATE TABLE invoices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_number VARCHAR(64) UNIQUE NOT NULL,
    customer_id UUID NOT NULL,
    sales_order_id UUID REFERENCES sales_orders(id),
    invoice_date TIMESTAMPTZ DEFAULT NOW(),
    due_date TIMESTAMPTZ,
    total_amount NUMERIC(18,2) DEFAULT 0,
    paid_amount NUMERIC(18,2) DEFAULT 0,
    status VARCHAR(32) DEFAULT 'pending',
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE invoice_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id UUID NOT NULL REFERENCES invoices(id),
    product_id UUID NOT NULL,
    quantity NUMERIC(18,6) NOT NULL,
    unit_price NUMERIC(18,6) NOT NULL,
    line_total NUMERIC(18,2) NOT NULL
);
CREATE TABLE payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id UUID NOT NULL REFERENCES invoices(id),
    payment_method VARCHAR(50) NOT NULL,
    amount NUMERIC(18,2) NOT NULL,
    payment_date TIMESTAMPTZ DEFAULT NOW(),
    reference VARCHAR(255),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
"""


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(TEST_DB, future=True)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        for stmt in SCHEMA.strip().split(";"):
            if stmt.strip():
                await conn.execute(text(stmt))
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine):
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s


async def make_invoice(session, total="1000.00"):
    oid, iid, cid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    n = uuid.uuid4().hex[:8]
    await session.execute(text("""
        INSERT INTO sales_orders (id, order_number, customer_id, total_amount)
        VALUES (:id, :num, :cid, :t)
    """), {"id": str(oid), "num": f"SO-{n}", "cid": str(cid), "t": total})
    await session.execute(text("""
        INSERT INTO invoices (id, invoice_number, customer_id,
                              sales_order_id, total_amount)
        VALUES (:id, :num, :cid, :oid, :t)
    """), {"id": str(iid), "num": f"INV-{n}", "cid": str(cid),
           "oid": str(oid), "t": total})
    await session.commit()
    return oid, iid


async def three_sources(session, order_id, invoice_id):
    """Read revenue as each of the three subsystems would."""
    order_flag = (await session.execute(text(
        "SELECT payment_status FROM sales_orders WHERE id = :id"),
        {"id": str(order_id)})).scalar()
    cached = (await session.execute(text(
        "SELECT paid_amount FROM invoices WHERE id = :id"),
        {"id": str(invoice_id)})).scalar()
    actual = (await session.execute(text(
        "SELECT COALESCE(SUM(amount),0) FROM payments WHERE invoice_id = :id"),
        {"id": str(invoice_id)})).scalar()
    return order_flag, money(cached), money(actual)


# ---------------------------------------------------------------------------
# The core invariant
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_partial_payment_agrees_across_all_three(session):
    oid, iid = await make_invoice(session, "1000.00")
    await record_payment(session, invoice_id=iid, amount="400.00",
                         payment_method="cash")
    await session.commit()

    flag, cached, actual = await three_sources(session, oid, iid)
    assert cached == actual == Decimal("400.00")
    assert flag == 'partial'


@pytest.mark.asyncio
async def test_full_payment_agrees_across_all_three(session):
    oid, iid = await make_invoice(session, "1000.00")
    await record_payment(session, invoice_id=iid, amount="600.00",
                         payment_method="cash")
    await record_payment(session, invoice_id=iid, amount="400.00",
                         payment_method="transfer")
    await session.commit()

    flag, cached, actual = await three_sources(session, oid, iid)
    assert cached == actual == Decimal("1000.00")
    assert flag == 'paid'

    status = (await session.execute(text(
        "SELECT status FROM invoices WHERE id = :id"), {"id": str(iid)})).scalar()
    assert status == 'paid'


@pytest.mark.asyncio
async def test_deleting_payment_resyncs_all_three(session):
    """Regression: deletion used to subtract from a cached total rather than
    recomputing from the rows that remain."""
    oid, iid = await make_invoice(session, "1000.00")
    r1 = await record_payment(session, invoice_id=iid, amount="600.00",
                              payment_method="cash")
    await record_payment(session, invoice_id=iid, amount="400.00",
                         payment_method="cash")
    await session.commit()

    await delete_payment(session, payment_id=r1["payment_id"])
    await session.commit()

    flag, cached, actual = await three_sources(session, oid, iid)
    assert cached == actual == Decimal("400.00")
    assert flag == 'partial'


@pytest.mark.asyncio
async def test_deleting_all_payments_returns_to_unpaid(session):
    oid, iid = await make_invoice(session, "500.00")
    r = await record_payment(session, invoice_id=iid, amount="500.00",
                             payment_method="cash")
    await session.commit()
    await delete_payment(session, payment_id=r["payment_id"])
    await session.commit()

    flag, cached, actual = await three_sources(session, oid, iid)
    assert cached == actual == Decimal("0.00")
    assert flag == 'unpaid'
    pdate = (await session.execute(text(
        "SELECT payment_date FROM sales_orders WHERE id = :id"),
        {"id": str(oid)})).scalar()
    assert pdate is None, "payment_date must clear when the money is removed"


# ---------------------------------------------------------------------------
# Overpayment, including the concurrency case
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_overpayment_rejected(session):
    _, iid = await make_invoice(session, "100.00")
    with pytest.raises(HTTPException) as exc:
        await record_payment(session, invoice_id=iid, amount="150.00",
                             payment_method="cash")
    assert exc.value.status_code == 400
    assert "exceeds" in exc.value.detail


@pytest.mark.asyncio
async def test_zero_and_negative_payments_rejected(session):
    _, iid = await make_invoice(session, "100.00")
    for bad in ["0", "-50.00"]:
        with pytest.raises(HTTPException):
            await record_payment(session, invoice_id=iid, amount=bad,
                                 payment_method="cash")


@pytest.mark.asyncio
async def test_invoice_row_lock_blocks_concurrent_writer(engine):
    """Deterministically assert the FOR UPDATE lock on the invoice.

    This is the test that actually proves the overpayment race is fixed.
    While transaction A holds the invoice lock, B must block; if B completed
    immediately, the guard would still be a read-then-write on stale data and
    two operators recording the final payment could both insert -- leaving
    twice the invoice total in `payments` while paid_amount showed the correct
    figure. profits.py reads payments, so revenue would silently double.

    Asserted directly rather than by racing tasks: see the note on
    test_concurrent_final_payments_do_not_overpay below.
    """
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        _, iid = await make_invoice(s, "500.00")

    a = maker()
    await a.begin()
    await record_payment(a, invoice_id=iid, amount="100.00",
                         payment_method="cash")  # holds the lock, uncommitted

    async def b_pays():
        async with maker() as s:
            await record_payment(s, invoice_id=iid, amount="100.00",
                                 payment_method="cash")
            await s.commit()

    task = asyncio.create_task(b_pays())
    done, _ = await asyncio.wait({task}, timeout=1.5)
    assert not done, "second writer did not block; FOR UPDATE is not engaged"

    await a.commit()
    await a.close()
    await asyncio.wait_for(task, timeout=10)

    async with maker() as s:
        total = money((await s.execute(text(
            "SELECT COALESCE(SUM(amount),0) FROM payments WHERE invoice_id=:i"),
            {"i": str(iid)})).scalar())
        cached = money((await s.execute(text(
            "SELECT paid_amount FROM invoices WHERE id=:i"),
            {"i": str(iid)})).scalar())
    assert total == Decimal("200.00")
    assert cached == total


@pytest.mark.asyncio
async def test_concurrent_final_payments_do_not_overpay(engine):
    """Invariant check under concurrent load.

    HONEST NOTE ON TEST STRENGTH: this test does NOT reliably discriminate.
    Verified experimentally -- the original unlocked implementation also
    passes it, because asyncio tasks sharing one SQLAlchemy async engine do
    not achieve true parallelism on this platform (at 5, 20 and even 50
    concurrent tasks, every task's SELECT ran after the previous task had
    already committed). Real parallelism requires separate processes, as a
    multi-worker uvicorn deployment has.

    It is kept as a cheap invariant guard, but the actual proof that the race
    is fixed is test_invoice_row_lock_blocks_concurrent_writer above, which
    asserts the lock directly. Do not read a pass here as evidence on its own.
    """
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        _, iid = await make_invoice(s, "500.00")

    async def pay():
        async with maker() as s:
            try:
                await record_payment(s, invoice_id=iid, amount="500.00",
                                     payment_method="cash")
                await s.commit()
                return "ok"
            except HTTPException:
                await s.rollback()
                return "rejected"

    results = await asyncio.gather(*[pay() for _ in range(5)])

    async with maker() as s:
        total = money((await s.execute(text(
            "SELECT COALESCE(SUM(amount),0) FROM payments WHERE invoice_id=:i"),
            {"i": str(iid)})).scalar())
        cached = money((await s.execute(text(
            "SELECT paid_amount FROM invoices WHERE id=:i"),
            {"i": str(iid)})).scalar())

    assert results.count("ok") == 1, f"{results.count('ok')} payments landed"
    assert total == Decimal("500.00"), f"payments sum to {total}"
    assert cached == total, "cache disagrees with payment rows"


# ---------------------------------------------------------------------------
# Decimal correctness -- the float bugs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_final_settling_payment_not_rejected_by_float_error(session):
    """Regression: float arithmetic (0.1 + 0.2 != 0.3) made the last
    instalment land a hair above the stored total, so it was rejected as an
    overpayment and the invoice could never be closed."""
    _, iid = await make_invoice(session, "0.30")
    await record_payment(session, invoice_id=iid, amount="0.10",
                         payment_method="cash")
    await record_payment(session, invoice_id=iid, amount="0.20",
                         payment_method="cash")
    await session.commit()

    status = (await session.execute(text(
        "SELECT status FROM invoices WHERE id=:i"), {"i": str(iid)})).scalar()
    assert status == 'paid', "invoice stuck open due to float drift"


@pytest.mark.asyncio
async def test_many_small_payments_sum_exactly(session):
    _, iid = await make_invoice(session, "10.00")
    for _ in range(100):
        await record_payment(session, invoice_id=iid, amount="0.10",
                             payment_method="cash")
    await session.commit()
    cached = money((await session.execute(text(
        "SELECT paid_amount FROM invoices WHERE id=:i"),
        {"i": str(iid)})).scalar())
    assert cached == Decimal("10.00")


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_revenue_counts_partial_collections(session):
    """Regression: financial.py counted a 99%-collected order as ZERO revenue
    and its FULL value as outstanding, double-counting the error."""
    _, iid = await make_invoice(session, "1000.00")
    await record_payment(session, invoice_id=iid, amount="990.00",
                         payment_method="cash")
    await session.commit()

    assert await revenue_between(session) == Decimal("990.00")
    assert await outstanding_receivables(session) == Decimal("10.00")


@pytest.mark.asyncio
async def test_reconciliation_detects_drifted_cache(session):
    """The audit endpoint must actually catch a tampered cache."""
    _, iid = await make_invoice(session, "1000.00")
    await record_payment(session, invoice_id=iid, amount="400.00",
                         payment_method="cash")
    await session.commit()

    report = await reconciliation_report(session)
    assert report["drifted_count"] == 0

    # Simulate the historical damage: cache says paid, payments say otherwise.
    await session.execute(text(
        "UPDATE invoices SET paid_amount = 1000 WHERE id = :i"), {"i": str(iid)})
    await session.commit()

    report = await reconciliation_report(session)
    assert report["drifted_count"] == 1
    row = report["invoices_with_drifted_cache"][0]
    assert row["cached_paid_amount"] == 1000.0
    assert row["actual_payments_sum"] == 400.0
    assert row["difference"] == 600.0


@pytest.mark.asyncio
async def test_reconciliation_detects_order_paid_without_invoice(session):
    """The sales.py mark-paid legacy path: order flagged paid, no invoice,
    revenue no profit report has ever seen."""
    oid = uuid.uuid4()
    await session.execute(text("""
        INSERT INTO sales_orders (id, order_number, customer_id,
                                  payment_status, total_amount)
        VALUES (:id, :num, :cid, 'paid', 750.00)
    """), {"id": str(oid), "num": f"SO-{uuid.uuid4().hex[:8]}",
           "cid": str(uuid.uuid4())})
    await session.commit()

    report = await reconciliation_report(session)
    assert report["orphan_count"] == 1
    assert report["unrecognised_revenue"] == 750.0


@pytest.mark.asyncio
async def test_recompute_is_idempotent(session):
    oid, iid = await make_invoice(session, "800.00")
    await record_payment(session, invoice_id=iid, amount="800.00",
                         payment_method="cash")
    await session.commit()

    for _ in range(3):
        await recompute_invoice_paid(session, iid)
    await session.commit()

    flag, cached, actual = await three_sources(session, oid, iid)
    assert cached == actual == Decimal("800.00")
    assert flag == 'paid'
