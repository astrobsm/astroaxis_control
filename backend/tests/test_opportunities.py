"""The opportunity queue.

What these guard, in order of how much it would cost to get wrong:

  * a customer who has just ordered must NOT be told they are overdue;
  * the reorder interval is each customer's own, not a company-wide number;
  * a cross-sell suggestion comes from the order book, never invented;
  * nothing is stored, so a finding disappears when its cause does;
  * the record of what staff DID is append-only.
"""
from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.services import opportunities as svc

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")

NOW = datetime.now(timezone.utc)

SCHEMA = """
DROP TABLE IF EXISTS customer_opportunity_actions CASCADE;
DROP TABLE IF EXISTS logistics_deliveries CASCADE;
DROP TABLE IF EXISTS sales_order_lines CASCADE;
DROP TABLE IF EXISTS sales_orders CASCADE;
DROP TABLE IF EXISTS invoices CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS users CASCADE;

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(100) UNIQUE, email VARCHAR(255) UNIQUE NOT NULL,
    full_name VARCHAR(255), role VARCHAR(50) DEFAULT 'sales_staff',
    hashed_password VARCHAR(255) DEFAULT 'x', is_active BOOLEAN DEFAULT TRUE
);
CREATE TABLE customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_code VARCHAR(32) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL, phone VARCHAR(40), email VARCHAR(255),
    is_active BOOLEAN DEFAULT TRUE
);
CREATE TABLE products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sku VARCHAR(64) UNIQUE NOT NULL, name VARCHAR(255) NOT NULL
);
CREATE TABLE sales_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_number VARCHAR(64) UNIQUE NOT NULL,
    customer_id UUID REFERENCES customers(id),
    status VARCHAR(32) DEFAULT 'confirmed',
    order_date TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    total_amount NUMERIC(18,2) DEFAULT 0
);
CREATE TABLE sales_order_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sales_order_id UUID REFERENCES sales_orders(id) ON DELETE CASCADE,
    product_id UUID REFERENCES products(id),
    quantity NUMERIC(18,2) DEFAULT 1, line_total NUMERIC(18,2) DEFAULT 0
);
CREATE TABLE invoices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_number VARCHAR(64) UNIQUE NOT NULL,
    customer_id UUID REFERENCES customers(id),
    due_date DATE, total_amount NUMERIC(18,2) DEFAULT 0,
    paid_amount NUMERIC(18,2) DEFAULT 0, status VARCHAR(32) DEFAULT 'pending'
);
CREATE TABLE logistics_deliveries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    delivery_number VARCHAR(64) UNIQUE NOT NULL,
    sales_order_id UUID REFERENCES sales_orders(id),
    delivery_date TIMESTAMPTZ, status VARCHAR(32) DEFAULT 'delivered'
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
async def db():
    seng = create_engine(TEST_DB.replace("+asyncpg", ""), future=True)
    with seng.connect() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        for stmt in SCHEMA.strip().split(";"):
            if stmt.strip():
                c.execute(text(stmt))
        c.commit()
        _apply(c, "o0123456789n_opportunity_queue.py")
        c.commit()
    seng.dispose()
    eng = create_async_engine(TEST_DB, future=True)
    maker = sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await eng.dispose()


class FakeUser:
    def __init__(self, uid, name="Sales Officer"):
        self.id = uid
        self.full_name = name
        self.username = "officer"


async def _user(db):
    uid = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO users (id, username, email, full_name, role) "
        "VALUES (:i, :u, :e, 'Sales Officer', 'sales_staff')"),
        {"i": str(uid), "u": f"u{uid.hex[:8]}", "e": f"{uid.hex[:8]}@t.local"})
    return FakeUser(uid)


async def _customer(db, name="Hospital A"):
    cid = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO customers (id, customer_code, name, phone) "
        "VALUES (:i, :c, :n, '08030000000')"),
        {"i": str(cid), "c": f"CUS{uuid.uuid4().hex[:6].upper()}", "n": name})
    return cid


async def _product(db, name="Hera Wound Gel"):
    pid = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO products (id, sku, name) VALUES (:i, :s, :n)"),
        {"i": str(pid), "s": uuid.uuid4().hex[:8].upper(), "n": name})
    return pid


async def _order(db, customer_id, *, days_ago, amount=100000,
                 status="confirmed", product_id=None):
    oid = uuid.uuid4()
    await db.execute(text("""
        INSERT INTO sales_orders
            (id, order_number, customer_id, status, order_date, total_amount)
        VALUES (:i, :n, :c, :s, :d, :a)
    """), {"i": str(oid), "n": f"SO{uuid.uuid4().hex[:8].upper()}",
           "c": str(customer_id), "s": status,
           "d": NOW - timedelta(days=days_ago), "a": amount})
    if product_id:
        await db.execute(text("""
            INSERT INTO sales_order_lines
                (id, sales_order_id, product_id, quantity, line_total)
            VALUES (:i, :o, :p, 1, :a)
        """), {"i": str(uuid.uuid4()), "o": str(oid), "p": str(product_id),
               "a": amount})
    return oid


async def _invoice(db, customer_id, *, due_days_ago, total=50000, paid=0,
                   status="pending"):
    await db.execute(text("""
        INSERT INTO invoices
            (id, invoice_number, customer_id, due_date, total_amount,
             paid_amount, status)
        VALUES (:i, :n, :c, :d, :t, :p, :s)
    """), {"i": str(uuid.uuid4()),
           "n": f"INV{uuid.uuid4().hex[:8].upper()}", "c": str(customer_id),
           "d": (NOW - timedelta(days=due_days_ago)).date(),
           "t": total, "p": paid, "s": status})


def _find(result, type_, customer_id=None):
    for f in result["items"]:
        if f["type"] == type_ and (
                customer_id is None or f["customer_id"] == str(customer_id)):
            return f
    return None


# ---------------------------------------------------------------------------
# Reorder: the finding that must never be wrong
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_predictable_buyer_past_their_interval_is_surfaced(db):
    c = await _customer(db)
    for d in (90, 60, 30, 0):
        await _order(db, c, days_ago=d + 37)   # every 30 days, last 37 ago
    # 37 days against a 30-day average is a week late: past the 1.15 tolerance

    result = await svc.queue(db, limit=50)
    f = _find(result, "REORDER_DUE", c)
    assert f is not None
    assert "30 days" in f["reason"]
    assert "37 days" in f["reason"]
    assert f["recommended_action"] == "Reorder reminder"


@pytest.mark.asyncio
async def test_a_customer_who_just_ordered_is_not_told_they_are_overdue(db):
    """The single most damaging thing this list could get wrong."""
    c = await _customer(db)
    for d in (91, 61, 31, 1):
        await _order(db, c, days_ago=d)

    result = await svc.queue(db, limit=50)
    assert _find(result, "REORDER_DUE", c) is None
    assert _find(result, "DORMANT", c) is None


@pytest.mark.asyncio
async def test_slightly_late_is_not_an_opportunity(db):
    """A 30-day buyer at 32 days is noise, not news."""
    c = await _customer(db)
    for d in (92, 62, 32, 2):
        await _order(db, c, days_ago=d)
    result = await svc.queue(db, limit=50)
    assert _find(result, "REORDER_DUE", c) is None


@pytest.mark.asyncio
async def test_the_interval_is_the_customers_own_not_a_fixed_number(db):
    """A weekly buyer and a monthly buyer are both normal."""
    weekly = await _customer(db, "Pharmacy W")
    for d in (28, 21, 14, 11):           # every 7 days, 11 since last
        await _order(db, weekly, days_ago=d)

    monthly = await _customer(db, "Hospital M")
    for d in (90, 60, 30, 11):           # every 30 days, 11 since last
        await _order(db, monthly, days_ago=d)

    result = await svc.queue(db, limit=50)
    assert _find(result, "REORDER_DUE", weekly) is not None, \
        "11 days is overdue for a weekly buyer"
    assert _find(result, "REORDER_DUE", monthly) is None, \
        "11 days is not overdue for a monthly buyer"


@pytest.mark.asyncio
async def test_two_orders_are_not_a_pattern(db):
    c = await _customer(db)
    await _order(db, c, days_ago=60)
    await _order(db, c, days_ago=45)
    result = await svc.queue(db, limit=50)
    assert _find(result, "REORDER_DUE", c) is None


@pytest.mark.asyncio
async def test_cancelled_orders_do_not_count_as_buying(db):
    c = await _customer(db)
    for d in (90, 60, 30):
        await _order(db, c, days_ago=d, status="cancelled")
    result = await svc.queue(db, limit=50)
    assert _find(result, "REORDER_DUE", c) is None
    assert _find(result, "DORMANT", c) is None


# ---------------------------------------------------------------------------
# Dormant and quiet
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_lapsed_customer_is_dormant_not_a_reorder_reminder(db):
    """Listing the same relationship twice wastes the morning."""
    c = await _customer(db)
    for d in (210, 180, 150, 120):
        await _order(db, c, days_ago=d)

    result = await svc.queue(db, limit=50)
    assert _find(result, "DORMANT", c) is not None
    assert _find(result, "REORDER_DUE", c) is None


@pytest.mark.asyncio
async def test_a_quiet_customer_with_no_pattern_is_still_surfaced(db):
    c = await _customer(db)
    await _order(db, c, days_ago=40, amount=500000)
    result = await svc.queue(db, limit=50)
    f = _find(result, "HIGH_VALUE_QUIET", c)
    assert f is not None
    assert "40 days" in f["reason"]


# ---------------------------------------------------------------------------
# Money already owed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_overdue_invoice_outranks_everything_else(db):
    debtor = await _customer(db, "Owes money")
    await _invoice(db, debtor, due_days_ago=20, total=80000)

    lapsed = await _customer(db, "Gone quiet")
    for d in (210, 180, 150, 120):
        await _order(db, lapsed, days_ago=d, amount=900000)

    result = await svc.queue(db, limit=50)
    assert result["items"][0]["type"] == "UNPAID_INVOICE", \
        "the company's own late cash comes first"


@pytest.mark.asyncio
async def test_a_paid_invoice_is_not_an_opportunity(db):
    c = await _customer(db)
    await _invoice(db, c, due_days_ago=20, total=80000, paid=80000,
                   status="paid")
    result = await svc.queue(db, limit=50)
    assert _find(result, "UNPAID_INVOICE", c) is None


@pytest.mark.asyncio
async def test_a_part_paid_invoice_shows_only_the_balance(db):
    c = await _customer(db)
    await _invoice(db, c, due_days_ago=10, total=100000, paid=70000,
                   status="partial")
    result = await svc.queue(db, limit=50)
    f = _find(result, "UNPAID_INVOICE", c)
    assert f["potential_value"] == 30000.0


@pytest.mark.asyncio
async def test_an_invoice_not_yet_due_is_not_overdue(db):
    c = await _customer(db)
    await _invoice(db, c, due_days_ago=-10, total=50000)
    result = await svc.queue(db, limit=50)
    assert _find(result, "UNPAID_INVOICE", c) is None


# ---------------------------------------------------------------------------
# Cross-sell, from the order book only
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cross_sell_suggests_what_similar_customers_actually_buy(db):
    gel = await _product(db, "Hera Wound Gel")
    gauze = await _product(db, "Wound-Care Honey Gauze")

    narrow = await _customer(db, "Buys one thing")
    await _order(db, narrow, days_ago=10, product_id=gel)
    await _order(db, narrow, days_ago=5, product_id=gel)

    for i in range(2):
        both = await _customer(db, f"Buys both {i}")
        await _order(db, both, days_ago=20, product_id=gel)
        await _order(db, both, days_ago=10, product_id=gauze)

    result = await svc.queue(db, limit=50)
    f = _find(result, "CROSS_SELL", narrow)
    assert f is not None
    assert "Wound-Care Honey Gauze" in f["recommended_action"]
    assert "Hera Wound Gel" in f["reason"]
    assert f["detail"]["basis"] == \
        "purchasing history, not a clinical indication"


@pytest.mark.asyncio
async def test_cross_sell_is_silent_without_evidence(db):
    """No co-purchase history, no suggestion. Nothing is invented."""
    gel = await _product(db, "Hera Wound Gel")
    await _product(db, "Something Else")
    narrow = await _customer(db)
    await _order(db, narrow, days_ago=10, product_id=gel)
    await _order(db, narrow, days_ago=5, product_id=gel)

    result = await svc.queue(db, limit=50)
    assert _find(result, "CROSS_SELL", narrow) is None


@pytest.mark.asyncio
async def test_a_customer_buying_several_products_is_not_a_cross_sell(db):
    gel = await _product(db, "Gel")
    gauze = await _product(db, "Gauze")
    c = await _customer(db)
    await _order(db, c, days_ago=20, product_id=gel)
    await _order(db, c, days_ago=10, product_id=gauze)
    result = await svc.queue(db, limit=50)
    assert _find(result, "CROSS_SELL", c) is None


# ---------------------------------------------------------------------------
# Satisfaction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_recent_delivery_asks_how_it_went(db):
    c = await _customer(db)
    o = await _order(db, c, days_ago=5)
    await db.execute(text("""
        INSERT INTO logistics_deliveries
            (id, delivery_number, sales_order_id, delivery_date, status)
        VALUES (:i, :n, :o, :d, 'delivered')
    """), {"i": str(uuid.uuid4()), "n": f"DEL{uuid.uuid4().hex[:6]}",
           "o": str(o), "d": NOW - timedelta(days=3)})

    result = await svc.queue(db, limit=50)
    f = _find(result, "SATISFACTION_CHECK", c)
    assert f is not None
    assert f["priority"] == "RELATIONSHIP"
    assert f["potential_value"] == 0.0


@pytest.mark.asyncio
async def test_an_old_delivery_is_too_late_to_ask_about(db):
    c = await _customer(db)
    o = await _order(db, c, days_ago=40)
    await db.execute(text("""
        INSERT INTO logistics_deliveries
            (id, delivery_number, sales_order_id, delivery_date, status)
        VALUES (:i, :n, :o, :d, 'delivered')
    """), {"i": str(uuid.uuid4()), "n": f"DEL{uuid.uuid4().hex[:6]}",
           "o": str(o), "d": NOW - timedelta(days=30)})
    result = await svc.queue(db, limit=50)
    assert _find(result, "SATISFACTION_CHECK", c) is None


# ---------------------------------------------------------------------------
# The queue itself
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_limit_says_how_much_was_left_out(db):
    for i in range(8):
        c = await _customer(db, f"Debtor {i}")
        await _invoice(db, c, due_days_ago=10 + i, total=10000 * (i + 1))

    result = await svc.queue(db, limit=3)
    assert result["shown"] == 3
    assert result["total"] == 8
    assert result["not_shown"] == 5


@pytest.mark.asyncio
async def test_findings_are_grouped_by_priority_with_their_value(db):
    c = await _customer(db)
    await _invoice(db, c, due_days_ago=10, total=25000)
    result = await svc.queue(db, limit=50)
    assert result["by_priority"]["COMMERCIAL"]["count"] >= 1
    assert result["by_priority"]["COMMERCIAL"]["value"] >= 25000.0


@pytest.mark.asyncio
async def test_nothing_is_stored_so_a_fixed_problem_disappears(db):
    c = await _customer(db)
    await _invoice(db, c, due_days_ago=15, total=40000)
    assert _find(await svc.queue(db, limit=50), "UNPAID_INVOICE", c)

    await db.execute(text(
        "UPDATE invoices SET paid_amount = total_amount, status = 'paid' "
        "WHERE customer_id = :c"), {"c": str(c)})

    assert _find(await svc.queue(db, limit=50), "UNPAID_INVOICE", c) is None


# ---------------------------------------------------------------------------
# Recording what was done
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_contacted_opportunity_drops_off_the_list(db):
    user = await _user(db)
    c = await _customer(db)
    await _invoice(db, c, due_days_ago=10, total=40000)
    key = f"UNPAID_INVOICE:{c}"

    await svc.record_action(db, opportunity_key=key, outcome="CONTACTED",
                            actor=user)
    assert _find(await svc.queue(db, limit=50), "UNPAID_INVOICE", c) is None


@pytest.mark.asyncio
async def test_the_action_stores_what_the_system_said_at_the_time(db):
    user = await _user(db)
    c = await _customer(db)
    await _invoice(db, c, due_days_ago=12, total=65000)

    result = await svc.record_action(
        db, opportunity_key=f"UNPAID_INVOICE:{c}", outcome="CONVERTED",
        actor=user)
    assert result["recorded_value"] == 65000.0

    row = (await db.execute(text(
        "SELECT reason_at_action, potential_value, actor_name "
        "FROM customer_opportunity_actions WHERE customer_id = :c"),
        {"c": str(c)})).mappings().first()
    assert "overdue" in row["reason_at_action"]
    assert row["actor_name"] == "Sales Officer"


@pytest.mark.asyncio
async def test_a_snooze_needs_a_length(db):
    user = await _user(db)
    c = await _customer(db)
    await _invoice(db, c, due_days_ago=10)
    with pytest.raises(HTTPException) as e:
        await svc.record_action(db, opportunity_key=f"UNPAID_INVOICE:{c}",
                                outcome="SNOOZED", actor=user)
    assert e.value.status_code == 400
    assert "dismiss-forever" in str(e.value.detail)


@pytest.mark.asyncio
async def test_a_snoozed_opportunity_is_hidden_but_recoverable(db):
    user = await _user(db)
    c = await _customer(db)
    await _invoice(db, c, due_days_ago=10, total=40000)

    await svc.record_action(db, opportunity_key=f"UNPAID_INVOICE:{c}",
                            outcome="SNOOZED", snooze_days=14, actor=user)

    hidden = await svc.queue(db, limit=50)
    assert _find(hidden, "UNPAID_INVOICE", c) is None
    assert hidden["snoozed_hidden"] >= 1

    shown = await svc.queue(db, limit=50, include_snoozed=True)
    assert _find(shown, "UNPAID_INVOICE", c) is not None


@pytest.mark.asyncio
async def test_the_database_refuses_a_snooze_with_no_expiry(db):
    """Enforced by CHECK, so an API that forgets cannot create one."""
    c = await _customer(db)
    with pytest.raises(Exception) as e:
        await db.execute(text("""
            INSERT INTO customer_opportunity_actions
                (opportunity_key, opportunity_type, customer_id, outcome)
            VALUES (:k, 'DORMANT', :c, 'SNOOZED')
        """), {"k": f"DORMANT:{c}", "c": str(c)})
    assert "ck_coa_snooze" in str(e.value)


@pytest.mark.asyncio
async def test_the_action_log_cannot_be_rewritten(db):
    user = await _user(db)
    c = await _customer(db)
    await _invoice(db, c, due_days_ago=10)
    await svc.record_action(db, opportunity_key=f"UNPAID_INVOICE:{c}",
                            outcome="DECLINED", actor=user)
    with pytest.raises(Exception) as e:
        await db.execute(text(
            "UPDATE customer_opportunity_actions SET outcome = 'CONVERTED'"))
    assert "append-only" in str(e.value).lower()


@pytest.mark.asyncio
async def test_an_unknown_outcome_is_refused(db):
    c = await _customer(db)
    with pytest.raises(HTTPException) as e:
        await svc.record_action(db, opportunity_key=f"DORMANT:{c}",
                                outcome="MAYBE")
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_a_malformed_key_is_refused(db):
    with pytest.raises(HTTPException) as e:
        await svc.record_action(db, opportunity_key="nonsense",
                                outcome="CONTACTED")
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_an_unknown_opportunity_type_is_refused(db):
    c = await _customer(db)
    with pytest.raises(HTTPException) as e:
        await svc.record_action(db, opportunity_key=f"INVENTED:{c}",
                                outcome="CONTACTED")
    assert e.value.status_code == 400


# ---------------------------------------------------------------------------
# Was it worth building
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_performance_reports_conversion_including_the_bad_news(db):
    user = await _user(db)
    won = await _customer(db, "Converted")
    lost = await _customer(db, "Declined")
    await _invoice(db, won, due_days_ago=10, total=100000)
    await _invoice(db, lost, due_days_ago=10, total=50000)

    await svc.record_action(db, opportunity_key=f"UNPAID_INVOICE:{won}",
                            outcome="CONVERTED", actor=user)
    await svc.record_action(db, opportunity_key=f"UNPAID_INVOICE:{lost}",
                            outcome="DECLINED", actor=user)

    perf = await svc.performance(db, days=30)
    assert perf["total_actioned"] == 2
    assert perf["total_converted"] == 1
    assert perf["conversion_rate"] == 50.0
    assert perf["by_type"]["UNPAID_INVOICE"]["declined"] == 1


@pytest.mark.asyncio
async def test_performance_is_empty_rather_than_wrong_with_no_actions(db):
    perf = await svc.performance(db, days=30)
    assert perf["total_actioned"] == 0
    assert perf["conversion_rate"] == 0.0
