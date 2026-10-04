"""Finding and merging duplicate customers.

The expensive mistakes this guards against, in order:

  * a merge that loses order history, or leaves it pointing at a record
    nobody can open;
  * a merge that happens without a person deciding;
  * detection that misses the case that actually exists in the live book --
    the same name written in the other order.

Column types are copied from production, not chosen.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.services import customer_merge as svc

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")

NOW = datetime.now(timezone.utc)

# These suites share ONE database and each file rebuilds the tables it
# needs. A stub narrower than the real table therefore degrades it for
# every file that runs afterwards: a minimal legacy_debts here broke
# test_portal's credit lookup on a missing debt_number. Where another
# test file owns a table, its columns are copied rather than guessed.
SCHEMA = """
DROP TABLE IF EXISTS customer_merges CASCADE;
DROP TABLE IF EXISTS customer_opportunity_actions CASCADE;
DROP TABLE IF EXISTS distributor_registrations CASCADE;
DROP TABLE IF EXISTS wallet_expenses CASCADE;
DROP TABLE IF EXISTS field_visits CASCADE;
DROP TABLE IF EXISTS recall_notifications CASCADE;
DROP TABLE IF EXISTS returned_stock CASCADE;
DROP TABLE IF EXISTS legacy_debts CASCADE;
DROP TABLE IF EXISTS call_logs CASCADE;
DROP TABLE IF EXISTS distributors CASCADE;
DROP TABLE IF EXISTS payments CASCADE;
DROP TABLE IF EXISTS invoices CASCADE;
DROP TABLE IF EXISTS sales_orders CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS users CASCADE;

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(100) UNIQUE, email VARCHAR(255) UNIQUE NOT NULL,
    full_name VARCHAR(255), role VARCHAR(50) DEFAULT 'admin',
    hashed_password VARCHAR(255) DEFAULT 'x', is_active BOOLEAN DEFAULT TRUE
);
CREATE TABLE customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_code VARCHAR(32) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL, phone VARCHAR(40), email VARCHAR(255),
    address TEXT, is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE sales_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_number VARCHAR(64) UNIQUE NOT NULL,
    customer_id UUID REFERENCES customers(id),
    status VARCHAR(32) DEFAULT 'confirmed',
    order_date TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    total_amount NUMERIC(18,2) DEFAULT 0
);
CREATE TABLE invoices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_number VARCHAR(64) UNIQUE NOT NULL,
    sales_order_id UUID REFERENCES sales_orders(id),
    customer_id UUID REFERENCES customers(id),
    due_date TIMESTAMPTZ, total_amount NUMERIC(18,2) DEFAULT 0,
    paid_amount NUMERIC(18,2) DEFAULT 0,
    amount_paid NUMERIC(18,2) DEFAULT 0,
    issue_date DATE DEFAULT CURRENT_DATE,
    status VARCHAR(32) DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id UUID REFERENCES invoices(id),
    amount NUMERIC(18,2) NOT NULL DEFAULT 0,
    payment_date TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE call_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID REFERENCES customers(id)
);
CREATE TABLE legacy_debts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    debt_number VARCHAR(64) UNIQUE NOT NULL,
    customer_id UUID NOT NULL REFERENCES customers(id),
    description TEXT NOT NULL,
    original_amount NUMERIC(18,2) NOT NULL,
    paid_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    debt_date DATE NOT NULL,
    due_date DATE,
    notes TEXT
);
CREATE TABLE returned_stock (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID REFERENCES customers(id)
);
CREATE TABLE recall_notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID REFERENCES customers(id)
);
CREATE TABLE field_visits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID REFERENCES customers(id)
);
CREATE TABLE wallet_expenses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID REFERENCES customers(id)
);
CREATE TABLE distributors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    distributor_code VARCHAR(32) UNIQUE NOT NULL,
    customer_id UUID REFERENCES customers(id)
);
CREATE TABLE distributor_registrations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    claimed_customer_id UUID REFERENCES customers(id),
    linked_customer_id UUID REFERENCES customers(id)
);
CREATE TABLE customer_opportunity_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    opportunity_key VARCHAR(160) NOT NULL,
    opportunity_type VARCHAR(40) NOT NULL,
    customer_id UUID NOT NULL REFERENCES customers(id),
    outcome VARCHAR(24) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
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
        _apply(c, "p0123456789o_customer_merge.py")
        c.commit()
    seng.dispose()
    eng = create_async_engine(TEST_DB, future=True)
    maker = sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await eng.dispose()


class FakeUser:
    def __init__(self, uid):
        self.id = uid
        self.full_name = "Admin One"
        self.username = "admin"


async def _user(db):
    uid = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO users (id, username, email, full_name) "
        "VALUES (:i, :u, :e, 'Admin One')"),
        {"i": str(uid), "u": f"u{uid.hex[:8]}", "e": f"{uid.hex[:8]}@t.local"})
    return FakeUser(uid)


async def _customer(db, name, *, phone=None, email=None):
    cid = uuid.uuid4()
    await db.execute(text("""
        INSERT INTO customers (id, customer_code, name, phone, email)
        VALUES (:i, :c, :n, :p, :e)
    """), {"i": str(cid), "c": f"CUS{uuid.uuid4().hex[:8].upper()}",
           "n": name, "p": phone, "e": email})
    return cid


async def _order(db, customer_id, *, amount=50000):
    await db.execute(text("""
        INSERT INTO sales_orders (id, order_number, customer_id, total_amount)
        VALUES (:i, :n, :c, :a)
    """), {"i": str(uuid.uuid4()), "n": f"SO{uuid.uuid4().hex[:8].upper()}",
           "c": str(customer_id), "a": amount})


def _group_with(result, customer_id):
    for g in result["groups"]:
        if any(m["customer_id"] == str(customer_id) for m in g["members"]):
            return g
    return None


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def test_a_nigerian_number_matches_however_it_was_typed():
    assert (svc.normalise_phone("+234 803 123 4567")
            == svc.normalise_phone("08031234567")
            == svc.normalise_phone("0803-123-4567")
            == "8031234567")


def test_a_number_too_short_to_identify_anyone_is_ignored():
    assert svc.normalise_phone("1234") is None
    assert svc.normalise_phone("") is None
    assert svc.normalise_phone(None) is None


def test_a_name_matches_in_either_word_order():
    """The case that exists seven times in the live customer book."""
    assert (svc.normalise_name("Anyeneh Gloria")
            == svc.normalise_name("Gloria Anyeneh")
            == "anyeneh gloria")


def test_punctuation_and_case_do_not_make_a_different_customer():
    assert (svc.normalise_name("ST MARY'S HOSPITAL")
            == svc.normalise_name("St Mary's Hospital"))


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_records_sharing_a_phone_are_offered_together(db):
    a = await _customer(db, "St Mary's Hospital", phone="08056122957")
    b = await _customer(db, "Anyeneh Gloria", phone="0805 612 2957")
    result = await svc.candidates(db)
    g = _group_with(result, a)
    assert g is not None
    assert {m["customer_id"] for m in g["members"]} == {str(a), str(b)}
    assert "same phone number" in g["reasons"]


@pytest.mark.asyncio
async def test_the_same_name_reversed_is_found(db):
    a = await _customer(db, "Anyeneh Gloria")
    b = await _customer(db, "Gloria Anyeneh")
    g = _group_with(await svc.candidates(db), a)
    assert g is not None
    assert "same name" in g["reasons"]
    assert len(g["members"]) == 2


@pytest.mark.asyncio
async def test_a_shared_email_is_found(db):
    a = await _customer(db, "Pharmacy One", email="orders@clinic.test")
    await _customer(db, "Clinic Pharmacy", email="ORDERS@CLINIC.TEST")
    g = _group_with(await svc.candidates(db), a)
    assert g is not None
    assert "same email address" in g["reasons"]


@pytest.mark.asyncio
async def test_overlapping_matches_produce_one_group_not_two(db):
    """Matching on phone AND name must offer the pair once."""
    a = await _customer(db, "Anyeneh Gloria", phone="08056122957")
    b = await _customer(db, "Gloria Anyeneh", phone="08056122957")
    result = await svc.candidates(db)
    groups = [g for g in result["groups"]
              if any(m["customer_id"] in (str(a), str(b))
                     for m in g["members"])]
    assert len(groups) == 1
    assert set(groups[0]["reasons"]) == {"same phone number", "same name"}


@pytest.mark.asyncio
async def test_unrelated_customers_are_not_offered(db):
    await _customer(db, "Alpha Clinic", phone="08011111111")
    await _customer(db, "Beta Hospital", phone="08022222222")
    assert (await svc.candidates(db))["group_count"] == 0


@pytest.mark.asyncio
async def test_the_record_with_the_most_trade_is_suggested_to_survive(db):
    quiet = await _customer(db, "Gloria Anyeneh", phone="08056122957")
    busy = await _customer(db, "Anyeneh Gloria", phone="08056122957")
    for _ in range(4):
        await _order(db, busy)

    g = _group_with(await svc.candidates(db), busy)
    assert g["suggested_survivor_id"] == str(busy)
    assert g["members"][0]["orders"] == 4
    assert str(quiet) in {m["customer_id"] for m in g["members"]}


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_merge_moves_every_kind_of_history(db):
    """Twelve tables carry a customer_id. Missing one orphans that history."""
    user = await _user(db)
    keep = await _customer(db, "Anyeneh Gloria", phone="08056122957")
    gone = await _customer(db, "Gloria Anyeneh", phone="08056122957")

    await _order(db, gone)
    await db.execute(text(
        "INSERT INTO invoices (id, invoice_number, customer_id, total_amount) "
        "VALUES (:i, :n, :c, 1000)"),
        {"i": str(uuid.uuid4()), "n": f"INV{uuid.uuid4().hex[:8]}",
         "c": str(gone)})
    for table in ("call_logs", "returned_stock", "recall_notifications",
                  "field_visits", "wallet_expenses"):
        await db.execute(text(
            f"INSERT INTO {table} (id, customer_id) VALUES (:i, :c)"),
            {"i": str(uuid.uuid4()), "c": str(gone)})
    await db.execute(text(
        "INSERT INTO legacy_debts (id, debt_number, customer_id, description, "
        "original_amount, debt_date) "
        "VALUES (:i, :d, :c, 'Old balance', 5000, CURRENT_DATE)"),
        {"i": str(uuid.uuid4()), "d": f"LD{uuid.uuid4().hex[:8].upper()}",
         "c": str(gone)})

    result = await svc.merge(db, surviving_id=keep, merged_ids=[gone],
                             reason="Same person, name reversed", actor=user)

    assert result["rows_moved"] == 8
    for table in ("sales_orders", "invoices", "call_logs", "legacy_debts",
                  "returned_stock", "recall_notifications", "field_visits",
                  "wallet_expenses"):
        left = (await db.execute(text(
            f"SELECT COUNT(*) FROM {table} WHERE customer_id = :c"),
            {"c": str(gone)})).scalar()
        assert left == 0, f"{table} still points at the absorbed record"
        moved = (await db.execute(text(
            f"SELECT COUNT(*) FROM {table} WHERE customer_id = :c"),
            {"c": str(keep)})).scalar()
        assert moved == 1, f"{table} did not move"


@pytest.mark.asyncio
async def test_every_table_with_a_customer_id_is_in_the_merge_list(db):
    """A table added later and forgotten here would orphan its history."""
    actual = {(r[0], r[1]) for r in (await db.execute(text("""
        SELECT tc.table_name, kcu.column_name
          FROM information_schema.table_constraints tc
          JOIN information_schema.key_column_usage kcu
            ON kcu.constraint_name = tc.constraint_name
          JOIN information_schema.constraint_column_usage ccu
            ON ccu.constraint_name = tc.constraint_name
         WHERE tc.constraint_type = 'FOREIGN KEY'
           AND ccu.table_name = 'customers'
           AND tc.table_name <> 'customers'
           AND tc.table_name <> 'customer_merges'
    """))).fetchall()}
    declared = set(svc.CUSTOMER_REFERENCES)
    assert actual - declared == set(), (
        f"these reference customers but are not merged: {actual - declared}")


@pytest.mark.asyncio
async def test_the_absorbed_record_is_kept_and_marked(db):
    """An old invoice or saved link must still resolve."""
    user = await _user(db)
    keep = await _customer(db, "Keep", phone="08056122957")
    gone = await _customer(db, "Gone", phone="08056122957")
    await svc.merge(db, surviving_id=keep, merged_ids=[gone],
                    reason="Duplicate", actor=user)

    row = (await db.execute(text(
        "SELECT merged_into_id, merged_at, is_active FROM customers "
        "WHERE id = :i"), {"i": str(gone)})).mappings().first()
    assert row is not None, "the record must not be deleted"
    assert str(row["merged_into_id"]) == str(keep)
    assert row["merged_at"] is not None
    assert row["is_active"] is False


@pytest.mark.asyncio
async def test_a_merged_record_leaves_the_candidate_list(db):
    user = await _user(db)
    keep = await _customer(db, "Anyeneh Gloria", phone="08056122957")
    gone = await _customer(db, "Gloria Anyeneh", phone="08056122957")
    assert (await svc.candidates(db))["group_count"] == 1

    await svc.merge(db, surviving_id=keep, merged_ids=[gone],
                    reason="Duplicate", actor=user)
    assert (await svc.candidates(db))["group_count"] == 0


@pytest.mark.asyncio
async def test_several_records_merge_at_once(db):
    """The live book has one person seven times."""
    user = await _user(db)
    keep = await _customer(db, "Anyeneh Gloria", phone="08056122957")
    others = [await _customer(db, "Anyeneh Gloria", phone="08056122957")
              for _ in range(6)]
    result = await svc.merge(db, surviving_id=keep, merged_ids=others,
                             reason="Seven records, one person", actor=user)
    assert result["merged_count"] == 6
    assert (await svc.candidates(db))["group_count"] == 0


@pytest.mark.asyncio
async def test_a_merge_records_what_moved_and_who_decided(db):
    user = await _user(db)
    keep = await _customer(db, "Keep")
    gone = await _customer(db, "Gone")
    await _order(db, gone)
    await _order(db, gone)

    await svc.merge(db, surviving_id=keep, merged_ids=[gone],
                    reason="Same hospital, two spellings", actor=user)

    hist = await svc.history(db, customer_id=keep)
    assert len(hist) == 1
    assert hist[0]["merged_name"] == "Gone"
    assert hist[0]["reason"] == "Same hospital, two spellings"
    assert hist[0]["actor_name"] == "Admin One"
    assert hist[0]["moved"]["sales_orders.customer_id"] == 2


@pytest.mark.asyncio
async def test_a_reason_is_required(db):
    user = await _user(db)
    keep = await _customer(db, "Keep")
    gone = await _customer(db, "Gone")
    with pytest.raises(HTTPException) as e:
        await svc.merge(db, surviving_id=keep, merged_ids=[gone],
                        reason="  ", actor=user)
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_a_customer_cannot_be_merged_into_itself(db):
    user = await _user(db)
    c = await _customer(db, "Only one")
    with pytest.raises(HTTPException) as e:
        await svc.merge(db, surviving_id=c, merged_ids=[c],
                        reason="Mistake", actor=user)
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_merging_an_already_merged_record_is_refused(db):
    """Chained merges leave history pointing at a record nobody can open."""
    user = await _user(db)
    keep = await _customer(db, "Keep")
    gone = await _customer(db, "Gone")
    third = await _customer(db, "Third")
    await svc.merge(db, surviving_id=keep, merged_ids=[gone],
                    reason="Duplicate", actor=user)

    with pytest.raises(HTTPException) as e:
        await svc.merge(db, surviving_id=third, merged_ids=[gone],
                        reason="Again", actor=user)
    assert e.value.status_code == 400
    assert "Already merged" in str(e.value.detail)


@pytest.mark.asyncio
async def test_merging_into_a_record_that_was_itself_absorbed_is_refused(db):
    user = await _user(db)
    keep = await _customer(db, "Keep")
    gone = await _customer(db, "Gone")
    other = await _customer(db, "Other")
    await svc.merge(db, surviving_id=keep, merged_ids=[gone],
                    reason="Duplicate", actor=user)

    with pytest.raises(HTTPException) as e:
        await svc.merge(db, surviving_id=gone, merged_ids=[other],
                        reason="Wrong direction", actor=user)
    assert e.value.status_code == 400
    assert "already been merged" in str(e.value.detail)


@pytest.mark.asyncio
async def test_an_unknown_record_is_a_404(db):
    user = await _user(db)
    keep = await _customer(db, "Keep")
    with pytest.raises(HTTPException) as e:
        await svc.merge(db, surviving_id=keep, merged_ids=[uuid.uuid4()],
                        reason="Duplicate", actor=user)
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_the_merge_log_cannot_be_rewritten(db):
    user = await _user(db)
    keep = await _customer(db, "Keep")
    gone = await _customer(db, "Gone")
    await svc.merge(db, surviving_id=keep, merged_ids=[gone],
                    reason="Duplicate", actor=user)
    with pytest.raises(Exception) as e:
        await db.execute(text(
            "UPDATE customer_merges SET reason = 'something else'"))
    assert "append-only" in str(e.value).lower()


@pytest.mark.asyncio
async def test_the_database_refuses_a_self_merge(db):
    """Enforced by CHECK, so an API that forgets cannot create one."""
    c = await _customer(db, "Only one")
    with pytest.raises(Exception) as e:
        await db.execute(text(
            "UPDATE customers SET merged_into_id = id, merged_at = NOW() "
            "WHERE id = :i"), {"i": str(c)})
    assert "ck_customer_merge" in str(e.value)
