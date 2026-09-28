"""The field portal: one distributor's marketers, and nothing else.

WHAT THESE TESTS DEFEND
=======================
People employed by a distributor now hold credentials against this system.
That is a deliberate decision and it is only safe while two things hold:

  1. A field token reaches nothing outside /api/field.
  2. Inside /api/field, a marketer reaches nothing belonging to another
     distributor.

Everything else here is detail. Those two are the reason the portal exists in
this shape rather than as extra rows in `users`, so the tests that assert them
come first and are written to fail loudly.

The tenancy tests use TWO distributors throughout, because a scoping bug is
invisible when there is only one tenant to see.
"""
import importlib.util
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.services import field_portal as svc

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")
SYNC_DB = (TEST_DB or "").replace("+asyncpg", "")

DROP_FIRST = (
    "field_audit_logs", "field_marketer_locations", "field_visits",
    "distributor_price_list", "field_marketer_invites",
    "field_marketer_accounts", "distributor_marketers", "distributor_outlets",
    "customers", "products", "distributors", "users",
)

BASE_TABLES = [
    """CREATE TABLE users (
           id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
           email VARCHAR(255) UNIQUE NOT NULL,
           full_name VARCHAR(255) NOT NULL,
           hashed_password VARCHAR(255) NOT NULL DEFAULT 'x',
           role VARCHAR(50) NOT NULL DEFAULT 'admin',
           is_active BOOLEAN DEFAULT TRUE,
           is_locked BOOLEAN DEFAULT FALSE,
           created_at TIMESTAMPTZ DEFAULT NOW())""",
    """CREATE TABLE distributors (
           id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
           distributor_code VARCHAR(32) UNIQUE NOT NULL,
           legal_name VARCHAR(255) NOT NULL,
           status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE')""",
    """CREATE TABLE products (
           id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
           sku VARCHAR(64) UNIQUE NOT NULL,
           name VARCHAR(255) NOT NULL,
           description TEXT,
           unit VARCHAR(32) DEFAULT 'unit')""",
    """CREATE TABLE customers (
           id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
           customer_code VARCHAR(32) UNIQUE NOT NULL,
           name VARCHAR(255) NOT NULL,
           phone VARCHAR(40), email VARCHAR(255), address TEXT,
           created_at TIMESTAMPTZ DEFAULT NOW())""",
    """CREATE TABLE distributor_marketers (
           id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
           distributor_id UUID NOT NULL REFERENCES distributors(id),
           full_name VARCHAR(160) NOT NULL,
           phone VARCHAR(40),
           is_active BOOLEAN NOT NULL DEFAULT TRUE)""",
    """CREATE TABLE distributor_outlets (
           id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
           distributor_id UUID NOT NULL REFERENCES distributors(id),
           name VARCHAR(255) NOT NULL)""",
]

MIGRATIONS = ["l7890123456k_field_portal.py"]


def _apply():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = create_engine(SYNC_DB, future=True)
    for filename in MIGRATIONS:
        path = (Path(__file__).resolve().parents[1] / "alembic" / "versions"
                / filename)
        spec = importlib.util.spec_from_file_location(
            f"f_{uuid.uuid4().hex[:6]}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with engine.begin() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                mod.upgrade()
    engine.dispose()


@pytest.fixture(scope="module")
def schema():
    engine = create_engine(SYNC_DB, future=True)
    with engine.begin() as conn:
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        for table in DROP_FIRST:
            conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
        for ddl in BASE_TABLES:
            conn.execute(text(ddl))
    engine.dispose()
    _apply()
    yield


@pytest_asyncio.fixture
async def db(schema):
    os.environ.setdefault("FIELD_PORTAL_SECRET", "test-field-secret-not-real")
    engine = create_async_engine(TEST_DB, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

async def _distributor(db, name):
    did = uuid.uuid4()
    await db.execute(
        text("""INSERT INTO distributors (id, distributor_code, legal_name)
                VALUES (:i, :c, :n)"""),
        {"i": str(did), "c": f"D{uuid.uuid4().hex[:8].upper()}", "n": name})
    await db.commit()
    return did


async def _product(db, name):
    pid = uuid.uuid4()
    await db.execute(
        text("INSERT INTO products (id, sku, name) VALUES (:i, :s, :n)"),
        {"i": str(pid), "s": f"SKU-{uuid.uuid4().hex[:8].upper()}", "n": name})
    await db.commit()
    return pid


async def _onboard(db, distributor_id, name="Field Rep", phone=None):
    """An invite, used, giving a live account and its resolved session."""
    invite = await svc.issue_invite(
        db, distributor_id=distributor_id, label="Test team")
    await db.commit()
    token = invite["join_url"].rsplit("/", 1)[-1]

    phone = phone or f"+23480{uuid.uuid4().int % 10**8:08d}"
    created = await svc.register_from_invite(
        db, token=token, full_name=name, phone=phone, password="FieldPass123")
    await db.commit()

    me = await svc.resolve_session(db, created["token"])
    return me, created, phone


# ---------------------------------------------------------------------------
# 1. A field token reaches nothing outside the portal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_field_token_is_not_a_staff_session(db):
    """The whole reason these people do not get a users row.

    If a field token were ever accepted by the ERP, a distributor's rep would
    be inside the system holding this company's payroll and formulations.
    """
    from app.api.auth import decode_token

    did = await _distributor(db, "Penacea Services Limited")
    _, created, _ = await _onboard(db, did)

    with pytest.raises(HTTPException) as exc:
        decode_token(created["token"])
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_the_staff_guard_refuses_a_field_token_even_if_keys_matched(db):
    """Defence in depth, asserted rather than assumed.

    A deployment that set FIELD_PORTAL_SECRET to the same value as SECRET_KEY
    would make the signature check pass. The typ claim is what still refuses.
    """
    import inspect
    from app.api import auth as auth_mod

    source = inspect.getsource(auth_mod.require_authenticated_user)
    assert 'payload.get("typ")' in source, (
        "require_authenticated_user must inspect the token type, or a field "
        "token becomes a staff session the moment two secrets match")


@pytest.mark.asyncio
async def test_a_staff_session_is_not_a_field_token(db):
    """And the other direction: the portal must not accept an ERP login."""
    from app.api.auth import create_access_token

    staff_token = create_access_token(data={"sub": str(uuid.uuid4())})
    with pytest.raises(HTTPException) as exc:
        svc.decode_session(staff_token)
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# 2. A marketer reaches nothing belonging to another distributor
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_marketer_sees_only_their_own_distributors_catalogue(db):
    """The tenancy boundary. Two distributors, two price lists, no leakage."""
    penacea = await _distributor(db, "Penacea Services Limited")
    triple = await _distributor(db, "Tripleluminance Resources Nig. Ltd")
    gauze = await _product(db, "WOUNDCARE HONEY GAUZE -BIG")
    gel = await _product(db, "HERA-WOUND GEL 100G")

    await svc.set_price(db, distributor_id=penacea, product_id=gauze,
                        unit="carton", price=Decimal("18000"))
    await svc.set_price(db, distributor_id=triple, product_id=gel,
                        unit="carton", price=Decimal("25000"))
    await db.commit()

    me, _, _ = await _onboard(db, penacea, name="Penacea Rep")
    listing = await svc.catalogue(db, me=me)

    names = {i["name"] for i in listing["items"]}
    assert names == {"WOUNDCARE HONEY GAUZE -BIG"}
    assert "HERA-WOUND GEL 100G" not in names, (
        "a marketer must not see another distributor's product range")
    # Decimal, not string: the column is NUMERIC(18,2) so it renders as
    # "18000.00", and comparing the text asserts the formatting rather
    # than the price.
    assert Decimal(listing["items"][0]["price"]) == Decimal("18000")


@pytest.mark.asyncio
async def test_each_distributor_sets_its_own_price_for_the_same_product(db):
    penacea = await _distributor(db, "Penacea")
    triple = await _distributor(db, "Tripleluminance")
    gauze = await _product(db, "Shared Product")

    await svc.set_price(db, distributor_id=penacea, product_id=gauze,
                        unit="carton", price=Decimal("18000"))
    await svc.set_price(db, distributor_id=triple, product_id=gauze,
                        unit="carton", price=Decimal("21500"))
    await db.commit()

    p_me, _, _ = await _onboard(db, penacea, name="P Rep")
    t_me, _, _ = await _onboard(db, triple, name="T Rep")

    p_price = (await svc.catalogue(db, me=p_me))["items"][0]["price"]
    t_price = (await svc.catalogue(db, me=t_me))["items"][0]["price"]
    assert Decimal(p_price) == Decimal("18000")
    assert Decimal(t_price) == Decimal("21500")


@pytest.mark.asyncio
async def test_a_marketer_sees_only_the_customers_they_registered(db):
    """Scoped to the marketer, not the distributor.

    Handing every rep their distributor's whole customer list is handing it to
    whoever leaves next.
    """
    did = await _distributor(db, "One Distributor")
    mine, _, _ = await _onboard(db, did, name="Rep One")
    theirs, _, _ = await _onboard(db, did, name="Rep Two")

    await svc.register_customer(db, me=mine, name="My Pharmacy",
                                phone="+2348030000001")
    await svc.register_customer(db, me=theirs, name="Their Pharmacy",
                                phone="+2348030000002")
    await db.commit()

    names = {c["name"] for c in await svc.my_customers(db, me=mine)}
    assert names == {"My Pharmacy"}


@pytest.mark.asyncio
async def test_a_visit_cannot_be_logged_against_somebody_elses_customer(db):
    """Otherwise a rep could attach themselves to any customer in the company
    by pasting an id.
    """
    did = await _distributor(db, "One Distributor")
    mine, _, _ = await _onboard(db, did, name="Rep One")
    theirs, _, _ = await _onboard(db, did, name="Rep Two")

    other = await svc.register_customer(db, me=theirs, name="Not Mine",
                                        phone="+2348030000003")
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.log_visit(db, me=mine, place_name="Somewhere",
                            customer_id=uuid.UUID(other["id"]))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_a_marketer_sees_only_their_own_visits(db):
    did = await _distributor(db, "One Distributor")
    mine, _, _ = await _onboard(db, did, name="Rep One")
    theirs, _, _ = await _onboard(db, did, name="Rep Two")

    await svc.log_visit(db, me=mine, place_name="My Hospital")
    await svc.log_visit(db, me=theirs, place_name="Their Hospital")
    await db.commit()

    places = {v["place_name"] for v in await svc.my_visits(db, me=mine)}
    assert places == {"My Hospital"}


@pytest.mark.asyncio
async def test_the_tenant_comes_from_the_token_not_from_a_parameter(db):
    """Stated as a test because it is the design, not an implementation detail.

    Every portal-side function takes `me`. None takes a distributor_id, so
    there is nothing for a caller to forge.
    """
    import inspect

    for name in ("catalogue", "register_customer", "my_customers", "log_visit",
                 "my_visits", "record_location", "my_trail", "my_performance"):
        fn = getattr(svc, name)
        params = set(inspect.signature(fn).parameters)
        assert "me" in params, f"{name} does not take a resolved session"
        assert "distributor_id" not in params, (
            f"{name} takes distributor_id -- the tenant must come from the "
            f"signed token, never from the caller")


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_deactivated_marketer_stops_working_immediately(db):
    """Not when their token happens to expire. A rep who left this morning
    should not be pulling the price list this afternoon.
    """
    did = await _distributor(db, "One Distributor")
    me, created, _ = await _onboard(db, did)

    await db.execute(
        text("UPDATE distributor_marketers SET is_active = FALSE WHERE id = :m"),
        {"m": str(me["marketer_id"])})
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.resolve_session(db, created["token"])
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_sign_in_answers_the_same_for_a_bad_number_and_a_bad_password(db):
    """Distinguishing them tells somebody probing which of your distributors'
    staff exist.
    """
    did = await _distributor(db, "One Distributor")
    _, _, phone = await _onboard(db, did)

    with pytest.raises(HTTPException) as unknown:
        await svc.sign_in(db, phone="+2348039999999", password="whatever")
    with pytest.raises(HTTPException) as wrong:
        await svc.sign_in(db, phone=phone, password="notthepassword")

    assert unknown.value.status_code == wrong.value.status_code == 401
    assert unknown.value.detail == wrong.value.detail


@pytest.mark.asyncio
async def test_repeated_failures_lock_the_account(db):
    did = await _distributor(db, "One Distributor")
    _, _, phone = await _onboard(db, did)

    for _ in range(svc.LOCKOUT_ATTEMPTS):
        with pytest.raises(HTTPException):
            await svc.sign_in(db, phone=phone, password="wrong")
        await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.sign_in(db, phone=phone, password="FieldPass123")
    assert exc.value.status_code == 403
    assert "locked" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_a_revoked_invite_cannot_create_accounts(db):
    did = await _distributor(db, "One Distributor")
    invite = await svc.issue_invite(db, distributor_id=did, label="Team")
    await db.commit()
    token = invite["join_url"].rsplit("/", 1)[-1]

    await db.execute(
        text("UPDATE field_marketer_invites SET revoked_at = NOW() WHERE id = :i"),
        {"i": invite["id"]})
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.resolve_invite(db, token=token)
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Customers reach the company, not a copy of it
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_registered_customer_lands_in_the_company_customer_table(db):
    """Not a parallel list. Two customer tables is two answers to who the
    company sells to, and the second is the one nobody reconciles.
    """
    did = await _distributor(db, "One Distributor")
    me, _, _ = await _onboard(db, did, name="The Rep")

    created = await svc.register_customer(
        db, me=me, name="New Pharmacy", phone="+2348031112222")
    await db.commit()

    row = (await db.execute(
        text("""SELECT name, registered_by_marketer_id,
                       introduced_by_distributor_id
                  FROM customers WHERE id = :c"""),
        {"c": created["id"]})).mappings().first()

    assert row["name"] == "New Pharmacy"
    assert row["registered_by_marketer_id"] == me["marketer_id"]
    assert row["introduced_by_distributor_id"] == me["distributor_id"]


@pytest.mark.asyncio
async def test_a_customer_already_on_the_books_is_not_added_twice(db):
    did = await _distributor(db, "One Distributor")
    me, _, _ = await _onboard(db, did)

    await svc.register_customer(db, me=me, name="Existing Pharmacy",
                                phone="+2348034445555")
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.register_customer(db, me=me, name="Existing Pharmacy Again",
                                    phone="0803 444 5555")
    assert exc.value.status_code == 409


# ---------------------------------------------------------------------------
# Location is personal data
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_location_is_stored_without_consent(db):
    """The check is in the only function that writes to the table, so a route
    added later inherits it rather than having to remember it.
    """
    did = await _distributor(db, "One Distributor")
    me, _, _ = await _onboard(db, did)
    assert me["consented_at"] is None

    with pytest.raises(HTTPException) as exc:
        await svc.record_location(db, me=me, latitude=6.45, longitude=3.39)
    assert exc.value.status_code == 403

    stored = (await db.execute(
        text("SELECT COUNT(*) FROM field_marketer_locations"))).scalar()
    assert stored == 0


@pytest.mark.asyncio
async def test_consent_can_be_withdrawn_and_recording_stops(db):
    did = await _distributor(db, "One Distributor")
    me, created, _ = await _onboard(db, did)

    await svc.set_consent(db, me=me, agreed=True)
    await db.commit()
    me = await svc.resolve_session(db, created["token"])

    assert (await svc.record_location(
        db, me=me, latitude=6.45, longitude=3.39))["stored"] is True
    await db.commit()

    await svc.set_consent(db, me=me, agreed=False)
    await db.commit()
    me = await svc.resolve_session(db, created["token"])

    with pytest.raises(HTTPException):
        await svc.record_location(db, me=me, latitude=6.46, longitude=3.40)


@pytest.mark.asyncio
async def test_every_position_carries_its_own_deletion_date(db):
    """A retention limit on the row cannot be forgotten by a later maintainer
    who did not read the policy.
    """
    did = await _distributor(db, "One Distributor")
    me, created, _ = await _onboard(db, did)
    await svc.set_consent(db, me=me, agreed=True)
    await db.commit()
    me = await svc.resolve_session(db, created["token"])

    await svc.record_location(db, me=me, latitude=6.45, longitude=3.39)
    await db.commit()

    purge = (await db.execute(
        text("SELECT purge_after FROM field_marketer_locations LIMIT 1"))).scalar()
    expected = date.today() + timedelta(days=svc.LOCATION_RETENTION_DAYS)
    assert purge == expected


@pytest.mark.asyncio
async def test_expired_positions_are_purged(db):
    did = await _distributor(db, "One Distributor")
    me, created, _ = await _onboard(db, did)
    await svc.set_consent(db, me=me, agreed=True)
    await db.commit()
    me = await svc.resolve_session(db, created["token"])

    await svc.record_location(db, me=me, latitude=6.45, longitude=3.39)
    await db.execute(
        text("UPDATE field_marketer_locations "
             "SET purge_after = CURRENT_DATE - 1"))
    await db.commit()

    result = await svc.purge_expired_locations(db)
    await db.commit()
    assert result["deleted"] >= 1
    assert (await db.execute(
        text("SELECT COUNT(*) FROM field_marketer_locations"))).scalar() == 0


@pytest.mark.asyncio
async def test_a_phone_reporting_constantly_is_throttled(db):
    """A ping every few seconds fills the table and says nothing the row
    before it did not.
    """
    did = await _distributor(db, "One Distributor")
    me, created, _ = await _onboard(db, did)
    await svc.set_consent(db, me=me, agreed=True)
    await db.commit()
    me = await svc.resolve_session(db, created["token"])

    first = await svc.record_location(db, me=me, latitude=6.45, longitude=3.39)
    await db.commit()
    second = await svc.record_location(db, me=me, latitude=6.46, longitude=3.40)
    await db.commit()

    assert first["stored"] is True
    assert second["stored"] is False


# ---------------------------------------------------------------------------
# Prices are history, not a current value
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_changing_a_price_ends_the_old_one_rather_than_overwriting(db):
    """A visit logged last month can still be read against the price quoted at
    the time, which an UPDATE would have destroyed.
    """
    did = await _distributor(db, "One Distributor")
    product = await _product(db, "Priced Product")

    await svc.set_price(db, distributor_id=did, product_id=product,
                        unit="carton", price=Decimal("18000"))
    await svc.set_price(db, distributor_id=did, product_id=product,
                        unit="carton", price=Decimal("19500"))
    await db.commit()

    rows = await svc.price_list(db, distributor_id=did, include_history=True)
    assert len(rows) == 2
    live = [r for r in rows if r["effective_to"] is None]
    assert len(live) == 1
    assert Decimal(live[0]["price"]) == Decimal("19500")


@pytest.mark.asyncio
async def test_performance_counts_rather_than_scores(db):
    """Forty visits with no orders and four visits with four orders are
    different situations; one blended index hides which you are looking at.
    """
    did = await _distributor(db, "One Distributor")
    me, _, _ = await _onboard(db, did)

    await svc.log_visit(db, me=me, place_name="Ogui Road Pharmacy",
                        order_value=Decimal("50000"))
    await svc.log_visit(db, me=me, place_name="Trans Ekulu Clinic")
    await svc.register_customer(db, me=me, name="Brought In",
                                phone="+2348036667777")
    await db.commit()

    report = await svc.my_performance(db, me=me, days=30)
    assert report["visits"] == 2
    assert report["visits_with_order"] == 1
    assert Decimal(report["order_value"]) == Decimal("50000")
    assert report["customers_registered"] == 1
    assert "score" not in report and "rating" not in report
