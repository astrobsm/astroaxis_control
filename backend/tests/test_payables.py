"""Accounts Payable invariants.

Mirrors test_receivables.py. The defects being guarded against are the same
ones AR had: a mutable paid_amount with no payment rows behind it, float
money, and an overpayment guard reading unlocked data.
"""
import os
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.services.payables import (
    get_or_create_supplier, pay_supplier, recompute_po_paid,
    supplier_balance, outstanding_payables, supplier_aging,
    supplier_statement, money)

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")

SCHEMA = """
DROP TABLE IF EXISTS supplier_payments CASCADE;
DROP TABLE IF EXISTS purchase_order_items CASCADE;
DROP TABLE IF EXISTS purchase_orders CASCADE;
DROP TABLE IF EXISTS suppliers CASCADE;

CREATE TABLE suppliers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_code VARCHAR(32) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    classification VARCHAR(50) DEFAULT 'general',
    contact_person VARCHAR(255), phone VARCHAR(50), email VARCHAR(255),
    address TEXT, tax_id VARCHAR(64),
    bank_name VARCHAR(128), bank_account_number VARCHAR(64),
    bank_account_name VARCHAR(128),
    credit_limit NUMERIC(18,2) DEFAULT 0,
    payment_terms_days INTEGER DEFAULT 30,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    notes TEXT, created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX uq_suppliers_name_ci ON suppliers (LOWER(name));

CREATE TABLE purchase_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    po_number VARCHAR(64) UNIQUE NOT NULL,
    supplier_id UUID REFERENCES suppliers(id),
    vendor_name VARCHAR(255) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'ordered',
    order_date TIMESTAMPTZ DEFAULT NOW(),
    total_amount NUMERIC(18,2) DEFAULT 0,
    paid_amount NUMERIC(18,2) DEFAULT 0,
    payment_status VARCHAR(30) DEFAULT 'unpaid',
    payment_method VARCHAR(50), payment_reference VARCHAR(255),
    payment_date TIMESTAMPTZ, updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE supplier_payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    payment_number VARCHAR(64) UNIQUE NOT NULL,
    supplier_id UUID REFERENCES suppliers(id),
    po_id UUID REFERENCES purchase_orders(id),
    purchase_invoice_id UUID,
    amount NUMERIC(18,2) NOT NULL,
    payment_method VARCHAR(50) NOT NULL,
    payment_reference VARCHAR(255),
    payment_date DATE NOT NULL DEFAULT CURRENT_DATE,
    notes TEXT, created_by UUID, created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT ck_supplier_payments_positive CHECK (amount > 0)
);
"""


@pytest_asyncio.fixture
async def engine():
    seng = create_engine(TEST_DB.replace("+asyncpg", ""), future=True)
    with seng.connect() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        for stmt in SCHEMA.strip().split(";"):
            if stmt.strip():
                c.execute(text(stmt))
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


async def make_po(session, *, supplier_id=None, total="100000.00",
                  order_date=None, vendor="Acme Ltd"):
    pid = uuid.uuid4()
    num = f"PO-{uuid.uuid4().hex[:8].upper()}"
    await session.execute(text("""
        INSERT INTO purchase_orders
            (id, po_number, supplier_id, vendor_name, total_amount, order_date)
        VALUES (:i, :n, :s, :v, :t, COALESCE(:d, NOW()))
    """), {"i": str(pid), "n": num,
           "s": str(supplier_id) if supplier_id else None,
           "v": vendor, "t": total, "d": order_date})
    await session.commit()
    return pid, num


# ---------------------------------------------------------------------------
# Supplier master
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_supplier_names_collapse_case_insensitively(session):
    """Regression: vendors were free-text, so 'Acme Ltd', 'acme ltd' and
    'ACME LTD ' were three suppliers with three separate balances."""
    a = await get_or_create_supplier(session, name="Acme Ltd")
    b = await get_or_create_supplier(session, name="acme ltd")
    c = await get_or_create_supplier(session, name="  ACME LTD  ")
    await session.commit()
    assert a == b == c

    n = (await session.execute(
        text("SELECT COUNT(*) FROM suppliers"))).scalar()
    assert n == 1


@pytest.mark.asyncio
async def test_blank_supplier_name_rejected(session):
    with pytest.raises(HTTPException):
        await get_or_create_supplier(session, name="   ")


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_payment_updates_cache_from_rows(session):
    sup = await get_or_create_supplier(session, name="Acme Ltd")
    po, num = await make_po(session, supplier_id=sup, total="100000.00")

    r = await pay_supplier(session, po_id=po, amount="40000.00",
                           payment_method="bank")
    await session.commit()

    assert r["total_paid"] == Decimal("40000.00")
    assert r["balance"] == Decimal("60000.00")
    assert r["payment_status"] == "partial"

    cached = money((await session.execute(text(
        "SELECT paid_amount FROM purchase_orders WHERE id = :p"),
        {"p": str(po)})).scalar())
    actual = money((await session.execute(text(
        "SELECT COALESCE(SUM(amount),0) FROM supplier_payments WHERE po_id = :p"),
        {"p": str(po)})).scalar())
    assert cached == actual == Decimal("40000.00")


@pytest.mark.asyncio
async def test_full_payment_marks_paid(session):
    sup = await get_or_create_supplier(session, name="Acme Ltd")
    po, _ = await make_po(session, supplier_id=sup, total="1000.00")
    await pay_supplier(session, po_id=po, amount="600.00",
                       payment_method="bank")
    r = await pay_supplier(session, po_id=po, amount="400.00",
                           payment_method="cash")
    await session.commit()
    assert r["payment_status"] == "paid"
    assert r["balance"] == Decimal("0.00")


@pytest.mark.asyncio
async def test_overpayment_rejected(session):
    sup = await get_or_create_supplier(session, name="Acme Ltd")
    po, num = await make_po(session, supplier_id=sup, total="1000.00")
    with pytest.raises(HTTPException) as exc:
        await pay_supplier(session, po_id=po, amount="1500.00",
                           payment_method="bank")
    assert "exceeds the outstanding balance" in exc.value.detail


@pytest.mark.asyncio
async def test_zero_and_negative_rejected(session):
    sup = await get_or_create_supplier(session, name="Acme Ltd")
    po, _ = await make_po(session, supplier_id=sup, total="1000.00")
    for bad in ["0", "-100.00"]:
        with pytest.raises(HTTPException):
            await pay_supplier(session, po_id=po, amount=bad,
                               payment_method="bank")


@pytest.mark.asyncio
async def test_cancelled_po_cannot_be_paid(session):
    sup = await get_or_create_supplier(session, name="Acme Ltd")
    po, _ = await make_po(session, supplier_id=sup, total="1000.00")
    await session.execute(text(
        "UPDATE purchase_orders SET status='cancelled' WHERE id=:p"),
        {"p": str(po)})
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await pay_supplier(session, po_id=po, amount="100.00",
                           payment_method="bank")
    assert "cancelled" in exc.value.detail


@pytest.mark.asyncio
async def test_final_instalment_not_rejected_by_float_drift(session):
    """Regression: float arithmetic made the last instalment land a hair
    above the total, so it was rejected and the PO could never be closed."""
    sup = await get_or_create_supplier(session, name="Acme Ltd")
    po, _ = await make_po(session, supplier_id=sup, total="0.30")
    await pay_supplier(session, po_id=po, amount="0.10",
                       payment_method="cash")
    r = await pay_supplier(session, po_id=po, amount="0.20",
                           payment_method="cash")
    await session.commit()
    assert r["payment_status"] == "paid", "PO stuck open due to float drift"


@pytest.mark.asyncio
async def test_po_row_lock_blocks_concurrent_payer(engine):
    """Deterministic proof the overpayment guard is enforceable.

    Asserted by showing a second writer BLOCKS while the lock is held --
    racing async tasks does not reliably interleave on this platform (see
    the note in test_receivables.py).
    """
    import asyncio
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        sup = await get_or_create_supplier(s, name="Acme Ltd")
        po, _ = await make_po(s, supplier_id=sup, total="1000.00")

    a = maker()
    await a.begin()
    await pay_supplier(a, po_id=po, amount="500.00", payment_method="bank")

    async def b_pays():
        async with maker() as s:
            await pay_supplier(s, po_id=po, amount="500.00",
                               payment_method="bank")
            await s.commit()

    task = asyncio.create_task(b_pays())
    done, _ = await asyncio.wait({task}, timeout=1.5)
    assert not done, "second payer did not block; FOR UPDATE is not engaged"

    await a.commit()
    await a.close()
    await asyncio.wait_for(task, timeout=10)

    async with maker() as s:
        total = money((await s.execute(text(
            "SELECT COALESCE(SUM(amount),0) FROM supplier_payments "
            "WHERE po_id=:p"), {"p": str(po)})).scalar())
    assert total == Decimal("1000.00")


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_outstanding_excludes_cancelled_and_draft(session):
    sup = await get_or_create_supplier(session, name="Acme Ltd")
    await make_po(session, supplier_id=sup, total="1000.00")
    p2, _ = await make_po(session, supplier_id=sup, total="5000.00")
    await session.execute(text(
        "UPDATE purchase_orders SET status='cancelled' WHERE id=:p"),
        {"p": str(p2)})
    await session.commit()
    assert await outstanding_payables(session) == Decimal("1000.00")


@pytest.mark.asyncio
async def test_aging_runs_from_due_date_not_order_date(session):
    """A PO on 60-day terms is NOT overdue at 45 days. Ageing from the order
    date would report it as overdue and misstate the position."""
    sup = await get_or_create_supplier(
        session, name="Slow Terms Ltd", payment_terms_days=60)
    await make_po(session, supplier_id=sup, total="1000.00",
                  order_date=date.today() - timedelta(days=45))
    await session.commit()

    aging = await supplier_aging(session)
    assert aging["buckets"]["current"] == 1000.00
    assert aging["buckets"]["1_30"] == 0
    assert aging["items"][0]["days_overdue"] == 0


@pytest.mark.asyncio
async def test_aging_buckets_by_overdue_age(session):
    sup = await get_or_create_supplier(
        session, name="Net30 Ltd", payment_terms_days=30)
    # 40 days old on 30-day terms -> 10 days overdue -> 1_30 bucket
    await make_po(session, supplier_id=sup, total="100.00",
                  order_date=date.today() - timedelta(days=40))
    # 140 days old -> 110 days overdue -> over_90
    await make_po(session, supplier_id=sup, total="700.00",
                  order_date=date.today() - timedelta(days=140))
    await session.commit()

    aging = await supplier_aging(session)
    assert aging["buckets"]["1_30"] == 100.00
    assert aging["buckets"]["over_90"] == 700.00
    assert aging["total_outstanding"] == 800.00


@pytest.mark.asyncio
async def test_aging_excludes_settled_orders(session):
    sup = await get_or_create_supplier(session, name="Acme Ltd")
    po, _ = await make_po(session, supplier_id=sup, total="1000.00",
                          order_date=date.today() - timedelta(days=200))
    await pay_supplier(session, po_id=po, amount="1000.00",
                       payment_method="bank")
    await session.commit()
    aging = await supplier_aging(session)
    assert aging["total_outstanding"] == 0
    assert aging["items"] == []


@pytest.mark.asyncio
async def test_supplier_statement_running_balance(session):
    sup = await get_or_create_supplier(session, name="Acme Ltd")
    po, num = await make_po(session, supplier_id=sup, total="1000.00",
                            order_date=date.today() - timedelta(days=10))
    await pay_supplier(session, po_id=po, amount="400.00",
                       payment_method="bank",
                       payment_date=date.today() - timedelta(days=5))
    await session.commit()

    stmt = await supplier_statement(session, supplier_id=sup)
    assert [ln["type"] for ln in stmt["lines"]] == ["PURCHASE", "PAYMENT"]
    assert [ln["balance"] for ln in stmt["lines"]] == [1000.00, 600.00]
    assert stmt["closing_balance"] == 600.00


@pytest.mark.asyncio
async def test_supplier_balance_matches_statement(session):
    sup = await get_or_create_supplier(session, name="Acme Ltd")
    po, _ = await make_po(session, supplier_id=sup, total="2500.00")
    await pay_supplier(session, po_id=po, amount="1000.00",
                       payment_method="bank")
    await session.commit()

    bal = await supplier_balance(session, supplier_id=sup)
    stmt = await supplier_statement(session, supplier_id=sup)
    assert bal == Decimal("1500.00")
    assert float(bal) == stmt["closing_balance"], \
        "balance disagrees with the statement it should summarise"
