"""VAT returns.

Properties guarded:
  * output VAT is what was CHARGED (credits to VAT Payable), not what is left
    owing after a remittance;
  * input VAT is taken from the purchase day book and reduces the net;
  * a period cannot be filed twice;
  * a filed return is a frozen snapshot -- a later ledger change does not
    rewrite it;
  * remittance clears the VAT liability and only posts when posting is enabled.
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

from app.services.tax import (
    compute_vat_return, file_vat_return, record_vat_payment, list_vat_returns)
from app.services.ledger import Line, post_entry, account_ledger

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
        for t in ("vat_returns", "qc_inspections", "purchase_invoices",
                  "gl_journal_lines", "gl_journal_entries", "gl_periods",
                  "gl_accounts"):
            c.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        c.commit()
        _apply(c, "m2345678901l_general_ledger.py")
        _apply(c, "r7890123456q_tax_qc_costing.py")
        # A minimal purchase_invoices, since it is a runtime table the VAT
        # return reads from for input VAT.
        c.execute(text("""
            CREATE TABLE purchase_invoices (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                invoice_number VARCHAR(100), vendor_name VARCHAR(255),
                invoice_date DATE, tax_amount NUMERIC(18,2) DEFAULT 0,
                total_amount NUMERIC(18,2) DEFAULT 0,
                paid_amount NUMERIC(18,2) DEFAULT 0,
                status VARCHAR(30) DEFAULT 'pending')
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


async def _charge_output_vat(session, amount, on=date(2026, 7, 10)):
    """Simulate a sale that charges VAT: Cr VAT Payable / Dr Receivable."""
    await post_entry(
        session, entry_date=on, description="Sale with VAT",
        source_module="sales",
        lines=[Line("1300", debit=amount), Line("2300", credit=amount)])


# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_output_vat_is_vat_charged(session):
    await _charge_output_vat(session, "7500")
    await session.commit()
    r = await compute_vat_return(
        session, start=date(2026, 7, 1), end=date(2026, 7, 31))
    assert r["output_vat"] == 7500.00
    assert r["input_vat"] == 0.0
    assert r["net_payable"] == 7500.00
    assert r["position"] == "PAYABLE"


@pytest.mark.asyncio
async def test_input_vat_from_purchase_invoices_reduces_net(session):
    await _charge_output_vat(session, "7500")
    await session.execute(text("""
        INSERT INTO purchase_invoices
            (invoice_number, vendor_name, invoice_date, tax_amount, total_amount)
        VALUES ('PI-1', 'Supplier', '2026-07-15', 2000, 30000)
    """))
    await session.commit()
    r = await compute_vat_return(
        session, start=date(2026, 7, 1), end=date(2026, 7, 31))
    assert r["output_vat"] == 7500.00
    assert r["input_vat"] == 2000.00
    assert r["input_vat_breakdown"]["from_purchase_invoices"] == 2000.00
    assert r["net_payable"] == 5500.00


@pytest.mark.asyncio
async def test_more_input_than_output_is_a_credit_position(session):
    await _charge_output_vat(session, "1000")
    await session.execute(text("""
        INSERT INTO purchase_invoices
            (invoice_number, vendor_name, invoice_date, tax_amount)
        VALUES ('PI-2', 'Supplier', '2026-07-15', 3000)
    """))
    await session.commit()
    r = await compute_vat_return(
        session, start=date(2026, 7, 1), end=date(2026, 7, 31))
    assert r["net_payable"] == -2000.00
    assert r["position"] == "CREDIT"


@pytest.mark.asyncio
async def test_remittance_is_not_counted_as_output_vat(session):
    """A Dr to VAT Payable is a payment, not VAT charged. It must not inflate
    the next period's output VAT."""
    await _charge_output_vat(session, "5000")
    # a remittance in the same period
    await post_entry(
        session, entry_date=date(2026, 7, 20), description="VAT paid",
        source_module="tax.remittance",
        lines=[Line("2300", debit="5000"), Line("1200", credit="5000")])
    await session.commit()
    r = await compute_vat_return(
        session, start=date(2026, 7, 1), end=date(2026, 7, 31))
    # output VAT is still the 5000 charged, not net of the remittance
    assert r["output_vat"] == 5000.00


@pytest.mark.asyncio
async def test_filing_snapshots_and_blocks_second_file(session):
    await _charge_output_vat(session, "7500")
    await session.commit()
    res = await file_vat_return(
        session, start=date(2026, 7, 1), end=date(2026, 7, 31),
        filed_by="Accountant", firs_reference="FIRS-123")
    await session.commit()
    assert res["status"] == "FILED"
    assert res["output_vat"] == 7500.00

    with pytest.raises(HTTPException) as exc:
        await file_vat_return(
            session, start=date(2026, 7, 1), end=date(2026, 7, 31),
            filed_by="Accountant")
    assert "already been filed" in exc.value.detail


@pytest.mark.asyncio
async def test_filed_return_is_frozen_against_later_ledger_change(session):
    await _charge_output_vat(session, "7500")
    await session.commit()
    await file_vat_return(
        session, start=date(2026, 7, 1), end=date(2026, 7, 31),
        filed_by="Accountant")
    await session.commit()

    # a later, back-dated sale in the same period
    await _charge_output_vat(session, "9999", on=date(2026, 7, 25))
    await session.commit()

    returns = await list_vat_returns(session)
    filed = returns[0]
    # the FILED snapshot still shows what was declared, not the new total
    assert filed["output_vat"] == 7500.00


@pytest.mark.asyncio
async def test_remittance_clears_the_liability_and_posts(session):
    await _charge_output_vat(session, "7500")
    await session.commit()
    filed = await file_vat_return(
        session, start=date(2026, 7, 1), end=date(2026, 7, 31),
        filed_by="Accountant")
    await session.commit()

    res = await record_vat_payment(
        session, vat_return_id=uuid.UUID(filed["vat_return_id"]),
        amount=7500, paid_from="bank", on=date(2026, 8, 1))
    await session.commit()
    assert res["status"] == "PAID"
    assert res["posted"] is True

    # VAT Payable nets to zero: 7500 charged, 7500 remitted
    led = await account_ledger(session, account_code="2300")
    assert led["closing_balance"] == 0.0


@pytest.mark.asyncio
async def test_payment_does_not_post_when_disabled(session, monkeypatch):
    await _charge_output_vat(session, "7500")
    await session.commit()
    filed = await file_vat_return(
        session, start=date(2026, 7, 1), end=date(2026, 7, 31),
        filed_by="Accountant")
    await session.commit()

    monkeypatch.setenv("ACCOUNTING_POSTING_ENABLED", "false")
    res = await record_vat_payment(
        session, vat_return_id=uuid.UUID(filed["vat_return_id"]), amount=7500)
    await session.commit()
    # still recorded as paid on the return, but nothing hit the ledger
    assert res["status"] == "PAID"
    assert res["posted"] is False


@pytest.mark.asyncio
async def test_cannot_pay_before_filing(session):
    # a DRAFT-only return id that was never filed
    rid = uuid.uuid4()
    await session.execute(text("""
        INSERT INTO vat_returns (id, period_start, period_end, status)
        VALUES (:id, '2026-07-01', '2026-07-31', 'DRAFT')
    """), {"id": str(rid)})
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await record_vat_payment(session, vat_return_id=rid, amount=100)
    assert "File the return" in exc.value.detail
