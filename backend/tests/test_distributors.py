"""Distributor foundation: prove the integration is an integration.

The claims worth defending here are the ones that decide whether this module
orchestrates the existing systems or quietly becomes a parallel one:

  * provisioning creates a real `customers` row and a real `warehouses` row,
    and is idempotent -- a distributor can never acquire two accounting
    identities;
  * an ACTIVE distributor without those links is not a representable state;
  * an existing customer can be LINKED rather than duplicated;
  * a territory can have exactly one live exclusive holder, enforced by the
    database rather than by a check some code path can skip;
  * a target is superseded, never edited, so a past month keeps its own figure;
  * an assignment is closed, never rewritten, so historical sales stay attached
    to whoever made them;
  * duplicate applicants are detected across BOTH distributors and customers,
    through renaming, punctuation and phone formatting;
  * a high eligibility score cannot carry an applicant past a mandatory failure;
  * nobody verifies a document they uploaded themselves.

Requires real PostgreSQL: partial unique indexes, triggers and row locking.
"""
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

from app.services import distributors as dsvc
from app.services import geography as geo

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")
SYNC_DB = (TEST_DB or "").replace("+asyncpg", "")

# The pre-existing tables the distributor module hangs off. Shaped as the live
# schema shapes them, because the whole point is that distributors reuse these.
BASE_SCHEMA = """
DROP TABLE IF EXISTS distributor_audit_logs CASCADE;
DROP TABLE IF EXISTS distributor_qualifications CASCADE;
DROP TABLE IF EXISTS distributor_documents CASCADE;
DROP TABLE IF EXISTS distributor_applications CASCADE;
DROP TABLE IF EXISTS territory_assignments CASCADE;
DROP TABLE IF EXISTS territory_targets CASCADE;
DROP TABLE IF EXISTS territory_lgas CASCADE;
DROP TABLE IF EXISTS territories CASCADE;
DROP TABLE IF EXISTS distributors CASCADE;
DROP TABLE IF EXISTS regions CASCADE;
DROP TABLE IF EXISTS lgas CASCADE;
DROP TABLE IF EXISTS states CASCADE;
DROP TABLE IF EXISTS countries CASCADE;
DROP TABLE IF EXISTS sales_order_lines CASCADE;
DROP TABLE IF EXISTS sales_orders CASCADE;
DROP TABLE IF EXISTS stock_levels CASCADE;
DROP TABLE IF EXISTS warehouses CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS users CASCADE;

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    full_name VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL DEFAULT 'admin',
    is_active BOOLEAN DEFAULT TRUE,
    phone VARCHAR(20),
    department VARCHAR(100)
);
CREATE TABLE customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_code VARCHAR(32) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255),
    phone VARCHAR(50),
    address TEXT,
    credit_limit NUMERIC(12,2) DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sku VARCHAR(64) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL
);
CREATE TABLE warehouses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(32) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    location VARCHAR(255),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE stock_levels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    warehouse_id UUID NOT NULL REFERENCES warehouses(id),
    product_id UUID REFERENCES products(id),
    raw_material_id UUID,
    current_stock NUMERIC(18,6) NOT NULL DEFAULT 0,
    reserved_stock NUMERIC(18,6) DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE sales_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_number VARCHAR(64) UNIQUE NOT NULL,
    customer_id UUID NOT NULL REFERENCES customers(id),
    warehouse_id UUID REFERENCES warehouses(id),
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    payment_status VARCHAR(32) NOT NULL DEFAULT 'unpaid',
    total_amount NUMERIC(18,2) DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
"""


def _load_migration():
    path = (Path(__file__).resolve().parents[1] / "alembic" / "versions"
            / "x3456789012w_distributor_foundation.py")
    spec = importlib.util.spec_from_file_location("dist_migration", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeUser:
    def __init__(self, user_id, role="admin", full_name="Admin"):
        self.id = user_id
        self.role = role
        self.full_name = full_name


@pytest.fixture(scope="module")
def schema():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = create_engine(SYNC_DB, future=True)
    with engine.begin() as conn:
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        for stmt in BASE_SCHEMA.split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
    mod = _load_migration()
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            mod.upgrade()
    engine.dispose()
    yield


@pytest_asyncio.fixture
async def db(schema):
    engine = create_async_engine(TEST_DB, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


async def _admin(session, name="Admin"):
    uid = uuid.uuid4()
    await session.execute(
        text("INSERT INTO users (id, email, full_name, role) "
             "VALUES (:i, :e, :n, 'admin')"),
        {"i": str(uid), "e": f"{uid}@t.test", "n": name})
    await session.commit()
    return FakeUser(uid, full_name=name)


async def _lagos(session):
    row = (await session.execute(
        text("""SELECT s.id FROM states s JOIN countries c
                       ON c.id = s.country_id
                 WHERE c.iso2 = 'NG' AND s.code = 'LA'"""))).first()
    return row.id


async def _make_distributor(session, actor, name=None, **kw):
    result = await dsvc.create_distributor(
        session, legal_name=name or f"Test Dist {uuid.uuid4().hex[:6]}",
        actor=actor, acknowledge_duplicates=True, **kw)
    await session.commit()
    return uuid.UUID(result["id"])


# ---------------------------------------------------------------------------
# The seed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_nigeria_is_seeded_with_all_states(db):
    row = (await db.execute(
        text("""SELECT COUNT(*) AS n FROM states s JOIN countries c
                       ON c.id = s.country_id WHERE c.iso2 = 'NG'"""))).first()
    assert row.n == 37, "36 states plus the Federal Capital Territory"

    lagos = (await db.execute(
        text("""SELECT COUNT(*) AS n FROM lgas l JOIN states s
                       ON s.id = l.state_id WHERE s.code = 'LA'"""))).first()
    assert lagos.n == 20, "Lagos has 20 LGAs"

    fct = (await db.execute(
        text("""SELECT COUNT(*) AS n FROM lgas l JOIN states s
                       ON s.id = l.state_id WHERE s.code = 'FC'"""))).first()
    assert fct.n == 6, "the FCT has 6 area councils"


# ---------------------------------------------------------------------------
# Provisioning -- the integration itself
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_provisioning_creates_a_real_customer_and_warehouse(db):
    """The distributor becomes a customer to accounting, a warehouse to stock."""
    admin = await _admin(db)
    dist_id = await _make_distributor(db, admin, name="Bonne Stores Limited",
                                      email="bonne@example.test")

    result = await dsvc.provision_identities(
        db, distributor_id=dist_id, actor=admin)
    await db.commit()

    # Both rows exist in the EXISTING tables, not in a distributor-owned copy.
    cust = (await db.execute(
        text("SELECT customer_code, name, distributor_id FROM customers "
             "WHERE id = :c"), {"c": result["customer_id"]})).mappings().first()
    assert cust is not None
    assert cust["name"] == "Bonne Stores Limited"
    assert str(cust["distributor_id"]) == str(dist_id)

    wh = (await db.execute(
        text("SELECT code, warehouse_kind, distributor_id FROM warehouses "
             "WHERE id = :w"), {"w": result["warehouse_id"]})).mappings().first()
    assert wh["warehouse_kind"] == "DISTRIBUTOR"
    assert str(wh["distributor_id"]) == str(dist_id)


@pytest.mark.asyncio
async def test_provisioning_is_idempotent(db):
    """Calling it twice must not mint a second accounting identity."""
    admin = await _admin(db)
    dist_id = await _make_distributor(db, admin)

    first = await dsvc.provision_identities(db, distributor_id=dist_id,
                                            actor=admin)
    await db.commit()
    second = await dsvc.provision_identities(db, distributor_id=dist_id,
                                             actor=admin)
    await db.commit()

    assert first["customer_id"] == second["customer_id"]
    assert first["warehouse_id"] == second["warehouse_id"]
    assert second["created"] == ["already provisioned"]

    count = (await db.execute(
        text("SELECT COUNT(*) AS n FROM customers WHERE distributor_id = :d"),
        {"d": str(dist_id)})).first()
    assert count.n == 1


@pytest.mark.asyncio
async def test_an_existing_customer_can_be_linked_instead_of_duplicated(db):
    """An applicant who already trades with the company keeps one account.

    Creating a second would split their balance and their history in two, which
    is the exact duplicate-identity failure the directive prohibits.
    """
    admin = await _admin(db)
    existing = uuid.uuid4()
    await db.execute(
        text("""INSERT INTO customers (id, customer_code, name, phone)
                VALUES (:i, :c, 'Existing Trader Ltd', '08031112222')"""),
        {"i": str(existing), "c": f"CUST-{existing.hex[:6]}"})
    await db.commit()

    dist_id = await _make_distributor(db, admin, name="Existing Trader Ltd")
    result = await dsvc.provision_identities(
        db, distributor_id=dist_id, actor=admin, link_customer_id=existing)
    await db.commit()

    assert result["customer_id"] == str(existing)
    assert "linked existing customer" in result["created"]

    total = (await db.execute(
        text("SELECT COUNT(*) AS n FROM customers WHERE name = "
             "'Existing Trader Ltd'"))).first()
    assert total.n == 1, "no second account was created"


@pytest.mark.asyncio
async def test_one_customer_cannot_back_two_distributors(db):
    admin = await _admin(db)
    first = await _make_distributor(db, admin)
    result = await dsvc.provision_identities(db, distributor_id=first,
                                             actor=admin)
    await db.commit()

    second = await _make_distributor(db, admin)
    with pytest.raises(HTTPException) as exc:
        await dsvc.provision_identities(
            db, distributor_id=second, actor=admin,
            link_customer_id=uuid.UUID(result["customer_id"]))
    await db.rollback()
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_an_active_distributor_without_identities_is_unrepresentable(db):
    """The database refuses it, so a half-finished activation cannot hide."""
    admin = await _admin(db)
    dist_id = await _make_distributor(db, admin)
    with pytest.raises(Exception) as exc:
        await db.execute(
            text("UPDATE distributors SET status = 'ACTIVE' WHERE id = :d"),
            {"d": str(dist_id)})
        await db.commit()
    await db.rollback()
    assert "ck_dist_active_provisioned" in str(exc.value)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_approval_provisions_automatically(db):
    admin = await _admin(db)
    dist_id = await _make_distributor(db, admin)

    for status in ("APPLIED", "UNDER_REVIEW", "APPROVED"):
        await dsvc.set_status(db, distributor_id=dist_id, new_status=status,
                              reason="Progressing the application", actor=admin)
        await db.commit()

    dist = await dsvc.get_distributor(db, dist_id)
    assert dist["customer_id"] is not None
    assert dist["warehouse_id"] is not None

    await dsvc.set_status(db, distributor_id=dist_id, new_status="ACTIVE",
                          reason="Agreement signed", actor=admin)
    await db.commit()
    assert (await dsvc.get_distributor(db, dist_id))["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_illegal_lifecycle_transitions_are_refused(db):
    admin = await _admin(db)
    dist_id = await _make_distributor(db, admin)

    with pytest.raises(HTTPException) as exc:
        await dsvc.set_status(db, distributor_id=dist_id, new_status="ACTIVE",
                              reason="Skipping the queue", actor=admin)
    await db.rollback()
    assert "cannot become active" in exc.value.detail.lower()

    # And a terminated distributor is final.
    for status, reason in (("APPLIED", "applied"), ("UNDER_REVIEW", "review"),
                           ("APPROVED", "approved"),
                           ("TERMINATED", "Agreement ended")):
        await dsvc.set_status(db, distributor_id=dist_id, new_status=status,
                              reason=reason, actor=admin)
        await db.commit()

    with pytest.raises(HTTPException) as exc:
        await dsvc.set_status(db, distributor_id=dist_id, new_status="ACTIVE",
                              reason="Reinstating", actor=admin)
    await db.rollback()
    assert "final" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_suspension_closes_the_stock_location_but_keeps_the_balance(db):
    """Stock in a suspended distributor's hands must be recovered, not vanish."""
    admin = await _admin(db)
    dist_id = await _make_distributor(db, admin)
    for status, reason in (("APPLIED", "Application submitted"),
                           ("UNDER_REVIEW", "Under review"),
                           ("APPROVED", "Approved by management"),
                           ("ACTIVE", "Agreement signed")):
        await dsvc.set_status(db, distributor_id=dist_id, new_status=status,
                              reason=reason, actor=admin)
        await db.commit()

    dist = await dsvc.get_distributor(db, dist_id)
    product = uuid.uuid4()
    await db.execute(
        text("INSERT INTO products (id, sku, name) VALUES (:i, :s, 'Dressing')"),
        {"i": str(product), "s": f"SKU-{product.hex[:6]}"})
    await db.execute(
        text("""INSERT INTO stock_levels (id, warehouse_id, product_id,
                                          current_stock)
                VALUES (gen_random_uuid(), :w, :p, 45)"""),
        {"w": str(dist["warehouse_id"]), "p": str(product)})
    await db.commit()

    await dsvc.set_status(db, distributor_id=dist_id, new_status="SUSPENDED",
                          reason="Under investigation", actor=admin)
    await db.commit()

    wh = (await db.execute(
        text("SELECT is_active FROM warehouses WHERE id = :w"),
        {"w": str(dist["warehouse_id"])})).first()
    assert wh.is_active is False, "closed to new movement"

    level = (await db.execute(
        text("SELECT current_stock FROM stock_levels WHERE warehouse_id = :w"),
        {"w": str(dist["warehouse_id"])})).first()
    assert Decimal(str(level.current_stock)) == Decimal("45"), (
        "the balance survives; it has to be reconciled, not forgotten")


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_duplicates_are_found_through_renaming_and_formatting(db):
    admin = await _admin(db)
    await _make_distributor(db, admin, name="Bonne Care Nigeria Limited",
                            phone="08031234567", cac_number="RC123456")

    # Same business, written three different ways.
    for variant in ("Bonne Care Nig. Ltd", "BONNE CARE NIGERIA LTD",
                    "Bonne  Care   Nigeria"):
        found = await dsvc.find_possible_duplicates(db, legal_name=variant)
        assert any(c["strength"] >= 85 for c in found), variant

    # Same phone, formatted differently.
    for variant in ("+2348031234567", "0803 123 4567", "234-803-123-4567"):
        found = await dsvc.find_possible_duplicates(db, phone=variant)
        assert any("phone" in r.lower()
                   for c in found for r in c["reasons"]), variant

    found = await dsvc.find_possible_duplicates(db, cac_number="rc123456")
    assert any(c["strength"] == 100 for c in found)


@pytest.mark.asyncio
async def test_existing_customers_are_searched_too(db):
    """A duplicate check that only looked at distributors would miss the
    commonest case: the applicant is already a customer."""
    admin = await _admin(db)
    cid = uuid.uuid4()
    await db.execute(
        text("""INSERT INTO customers (id, customer_code, name, phone)
                VALUES (:i, :c, 'Ade Pharmacy Ventures', '08099887766')"""),
        {"i": str(cid), "c": f"CUST-{cid.hex[:6]}"})
    await db.commit()

    found = await dsvc.find_possible_duplicates(
        db, legal_name="Ade Pharmacy Ventures")
    assert any(c["kind"] == "customer" for c in found)


@pytest.mark.asyncio
async def test_creation_is_blocked_until_duplicates_are_acknowledged(db):
    admin = await _admin(db)
    await _make_distributor(db, admin, name="Zenith Medical Supplies",
                            phone="08055554444")

    with pytest.raises(HTTPException) as exc:
        await dsvc.create_distributor(
            db, legal_name="Zenith Medical Supplies", phone="08055554444",
            actor=admin)
    await db.rollback()
    assert exc.value.status_code == 409
    assert "candidates" in exc.value.detail

    # With acknowledgement it proceeds, and the warning is on the record.
    result = await dsvc.create_distributor(
        db, legal_name="Zenith Medical Supplies", phone="08055554444",
        actor=admin, acknowledge_duplicates=True)
    await db.commit()
    assert result["duplicate_warnings"]


@pytest.mark.asyncio
async def test_two_distributors_cannot_share_a_cac_number(db):
    admin = await _admin(db)
    await _make_distributor(db, admin, name="Alpha Ltd", cac_number="RC999001")
    with pytest.raises(Exception):
        await _make_distributor(db, admin, name="Beta Ltd",
                                cac_number="RC999001")
    await db.rollback()


# ---------------------------------------------------------------------------
# Territories
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_territory_opens_with_the_default_target(db):
    admin = await _admin(db)
    state = await _lagos(db)
    result = await geo.create_territory(
        db, code=f"LAG-Z{uuid.uuid4().hex[:4]}", name="Lagos Zone Test",
        state_id=state, actor=admin)
    await db.commit()

    target = await geo.target_on(db, uuid.UUID(result["id"]))
    assert target == Decimal("1000000.00")


@pytest.mark.asyncio
async def test_a_target_is_superseded_never_edited(db):
    """August keeps August's target after October's is raised."""
    admin = await _admin(db)
    state = await _lagos(db)
    created = await geo.create_territory(
        db, code=f"LAG-T{uuid.uuid4().hex[:4]}", name="Target Test",
        state_id=state, monthly_target=Decimal("1000000"), actor=admin)
    await db.commit()
    tid = uuid.UUID(created["id"])

    raise_from = date.today().replace(day=1) + timedelta(days=60)
    raise_from = raise_from.replace(day=1)
    await geo.set_target(db, territory_id=tid,
                         monthly_target=Decimal("1500000"),
                         effective_from=raise_from,
                         reason="Territory expanded", actor=admin)
    await db.commit()

    assert await geo.target_on(db, tid, on=date.today()) == Decimal("1000000.00")
    assert await geo.target_on(db, tid, on=raise_from) == Decimal("1500000.00")

    history = await geo.target_history(db, tid)
    assert len(history) == 2
    assert any(h["reason"] == "Territory expanded" for h in history)


@pytest.mark.asyncio
async def test_a_superseded_target_cannot_be_restated(db):
    admin = await _admin(db)
    state = await _lagos(db)
    created = await geo.create_territory(
        db, code=f"LAG-R{uuid.uuid4().hex[:4]}", name="Restate Test",
        state_id=state, actor=admin)
    await db.commit()
    tid = uuid.UUID(created["id"])

    await geo.set_target(db, territory_id=tid, monthly_target=Decimal("2000000"),
                         effective_from=date.today() + timedelta(days=40),
                         reason="Raised", actor=admin)
    await db.commit()

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("""UPDATE territory_targets SET monthly_target = 5
                     WHERE territory_id = :t AND effective_to IS NOT NULL"""),
            {"t": str(tid)})
        await db.commit()
    await db.rollback()
    assert "cannot be restated" in str(exc.value)


@pytest.mark.asyncio
async def test_only_one_distributor_can_hold_an_exclusive_territory(db):
    admin = await _admin(db)
    state = await _lagos(db)
    created = await geo.create_territory(
        db, code=f"LAG-X{uuid.uuid4().hex[:4]}", name="Exclusive Test",
        state_id=state, actor=admin)
    await db.commit()
    tid = uuid.UUID(created["id"])

    async def approved_distributor(name):
        did = await _make_distributor(db, admin, name=name)
        for s, r in (("APPLIED", "Application submitted"),
                     ("UNDER_REVIEW", "Under review"),
                     ("APPROVED", "Approved by management")):
            await dsvc.set_status(db, distributor_id=did, new_status=s,
                                  reason=r, actor=admin)
            await db.commit()
        return did

    first = await approved_distributor(f"Holder A {uuid.uuid4().hex[:4]}")
    second = await approved_distributor(f"Holder B {uuid.uuid4().hex[:4]}")

    await geo.assign_territory(db, territory_id=tid, distributor_id=first,
                               reason="Initial award", actor=admin)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await geo.assign_territory(db, territory_id=tid, distributor_id=second,
                                   reason="Second award", actor=admin)
    await db.rollback()
    assert exc.value.status_code == 409
    assert "held" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_reassignment_preserves_who_held_the_territory_and_when(db):
    """Specification section 60: historical ownership is never overwritten."""
    admin = await _admin(db)
    state = await _lagos(db)
    created = await geo.create_territory(
        db, code=f"LAG-H{uuid.uuid4().hex[:4]}", name="History Test",
        state_id=state, actor=admin)
    await db.commit()
    tid = uuid.UUID(created["id"])

    async def approved(name):
        did = await _make_distributor(db, admin, name=name)
        for s, r in (("APPLIED", "Application submitted"),
                     ("UNDER_REVIEW", "Under review"),
                     ("APPROVED", "Approved by management")):
            await dsvc.set_status(db, distributor_id=did, new_status=s,
                                  reason=r, actor=admin)
            await db.commit()
        return did

    a = await approved(f"Outgoing {uuid.uuid4().hex[:4]}")
    b = await approved(f"Incoming {uuid.uuid4().hex[:4]}")

    first = await geo.assign_territory(
        db, territory_id=tid, distributor_id=a,
        assigned_from=date.today() - timedelta(days=200),
        reason="Original award", actor=admin)
    await db.commit()

    await geo.end_assignment(db, assignment_id=uuid.UUID(first["id"]),
                             reason="Performance review outcome", actor=admin)
    await db.commit()

    await geo.assign_territory(db, territory_id=tid, distributor_id=b,
                               reason="Reassigned", actor=admin)
    await db.commit()

    history = await geo.assignment_history(db, tid)
    assert len(history) == 2, "both holders remain on the record"
    ended = [h for h in history if h["assigned_to"] is not None]
    assert len(ended) == 1
    assert ended[0]["end_reason"] == "Performance review outcome"

    holder = await geo.current_holder(db, tid)
    assert str(holder["id"]) == str(b)


@pytest.mark.asyncio
async def test_an_ended_assignment_cannot_be_rewritten(db):
    admin = await _admin(db)
    state = await _lagos(db)
    created = await geo.create_territory(
        db, code=f"LAG-E{uuid.uuid4().hex[:4]}", name="Immutable Test",
        state_id=state, actor=admin)
    await db.commit()
    tid = uuid.UUID(created["id"])

    did = await _make_distributor(db, admin)
    for s, r in (("APPLIED", "Application submitted"),
                     ("UNDER_REVIEW", "Under review"),
                     ("APPROVED", "Approved by management")):
        await dsvc.set_status(db, distributor_id=did, new_status=s, reason=r,
                              actor=admin)
        await db.commit()

    assignment = await geo.assign_territory(
        db, territory_id=tid, distributor_id=did, reason="Award", actor=admin)
    await db.commit()
    await geo.end_assignment(db, assignment_id=uuid.UUID(assignment["id"]),
                             reason="Ended", actor=admin)
    await db.commit()

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("DELETE FROM territory_assignments WHERE id = :i"),
            {"i": assignment["id"]})
        await db.commit()
    await db.rollback()
    assert "not deletable" in str(exc.value)


@pytest.mark.asyncio
async def test_only_an_approved_distributor_can_hold_a_territory(db):
    admin = await _admin(db)
    state = await _lagos(db)
    created = await geo.create_territory(
        db, code=f"LAG-D{uuid.uuid4().hex[:4]}", name="Draft Test",
        state_id=state, actor=admin)
    await db.commit()

    draft = await _make_distributor(db, admin)
    with pytest.raises(HTTPException) as exc:
        await geo.assign_territory(
            db, territory_id=uuid.UUID(created["id"]), distributor_id=draft,
            reason="Premature", actor=admin)
    await db.rollback()
    assert "approved or active" in exc.value.detail


@pytest.mark.asyncio
async def test_targets_roll_up_to_state_and_national(db):
    admin = await _admin(db)
    state = await _lagos(db)
    prefix = uuid.uuid4().hex[:4].upper()
    for n in range(3):
        await geo.create_territory(
            db, code=f"RU{prefix}-{n}", name=f"Rollup {n}", state_id=state,
            monthly_target=Decimal("1000000"), actor=admin)
        await db.commit()

    rollup = await geo.target_rollup(db)
    lagos = [s for s in rollup["by_state"] if s["state"] == "Lagos"]
    assert lagos, "Lagos must appear in the state roll-up"
    assert Decimal(lagos[0]["target"]) >= Decimal("3000000")
    assert Decimal(rollup["national_target"]) >= Decimal("3000000")


# ---------------------------------------------------------------------------
# Eligibility and verification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_high_score_cannot_carry_a_mandatory_failure(db):
    """Specification section 6 -- the score never outranks a blocking condition."""
    admin = await _admin(db)
    dist_id = await _make_distributor(db, admin, name="Perfect Scores Ltd")

    result = await dsvc.assess_eligibility(
        db, distributor_id=dist_id,
        scores={k: 100 for k in dsvc.DEFAULT_WEIGHTS})

    assert Decimal(result["score"]) == Decimal("100.00")
    assert result["band"] == "ELIGIBLE"
    # No CAC number and no verified document, so approval is still blocked.
    assert result["blocking_conditions"]
    assert result["may_be_approved"] is False, (
        "a perfect score must not approve an applicant with a mandatory gap")


@pytest.mark.asyncio
async def test_the_score_bands_follow_the_configured_thresholds(db):
    admin = await _admin(db)
    dist_id = await _make_distributor(db, admin)

    low = await dsvc.assess_eligibility(
        db, distributor_id=dist_id,
        scores={"regulatory_eligibility": 50})
    assert low["band"] == "NOT_ELIGIBLE"

    mid = await dsvc.assess_eligibility(
        db, distributor_id=dist_id,
        scores={k: 70 for k in dsvc.DEFAULT_WEIGHTS})
    assert mid["band"] == "CONDITIONALLY_ELIGIBLE"


@pytest.mark.asyncio
async def test_nobody_verifies_a_document_they_uploaded(db):
    admin = await _admin(db)
    dist_id = await _make_distributor(db, admin)

    doc = await dsvc.attach_document(
        db, distributor_id=dist_id, doc_type="CAC_CERTIFICATE",
        filename="cac.pdf", content_type="application/pdf",
        content=b"%PDF-1.4 fake certificate", actor=admin)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await dsvc.verify_document(
            db, document_id=uuid.UUID(doc["id"]), verified=True, user=admin)
    await db.rollback()
    assert exc.value.status_code == 403
    assert "second pair of eyes" in exc.value.detail

    other = await _admin(db, name="Compliance Officer")
    result = await dsvc.verify_document(
        db, document_id=uuid.UUID(doc["id"]), verified=True, user=other)
    await db.commit()
    assert result["verification_status"] == "VERIFIED"


@pytest.mark.asyncio
async def test_expiring_documents_are_reported_with_days_remaining(db):
    admin = await _admin(db)
    dist_id = await _make_distributor(db, admin)

    await dsvc.attach_document(
        db, distributor_id=dist_id, doc_type="LICENCE", filename="l.pdf",
        content_type="application/pdf", content=b"soon",
        expiry_date=date.today() + timedelta(days=20), actor=admin)
    await dsvc.attach_document(
        db, distributor_id=dist_id, doc_type="INSURANCE", filename="i.pdf",
        content_type="application/pdf", content=b"gone",
        expiry_date=date.today() - timedelta(days=5), actor=admin)
    await db.commit()

    items = await dsvc.expiring_documents(db, within_days=90)
    mine = [i for i in items if i["distributor_id"] == str(dist_id)]
    assert len(mine) == 2
    assert any(i["expired"] for i in mine)
    assert any(not i["expired"] for i in mine)


@pytest.mark.asyncio
async def test_the_audit_trail_cannot_be_edited(db):
    admin = await _admin(db)
    await _make_distributor(db, admin)

    for stmt in ("DELETE FROM distributor_audit_logs",
                 "UPDATE distributor_audit_logs SET event_type = 'X'"):
        with pytest.raises(Exception) as exc:
            await db.execute(text(stmt))
            await db.commit()
        await db.rollback()
        assert "append-only" in str(exc.value)


# ---------------------------------------------------------------------------
# Regression: existing behaviour is untouched
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_existing_orders_keep_working_and_default_to_direct(db):
    """The busiest table in the application gained three nullable columns.

    An order written the way the rest of the app writes one must still work,
    and must look exactly as it did before.
    """
    cid = uuid.uuid4()
    await db.execute(
        text("""INSERT INTO customers (id, customer_code, name)
                VALUES (:i, :c, 'Ordinary Customer')"""),
        {"i": str(cid), "c": f"CUST-{cid.hex[:6]}"})
    oid = uuid.uuid4()
    await db.execute(
        text("""INSERT INTO sales_orders
                    (id, order_number, customer_id, total_amount)
                VALUES (:i, :n, :c, 25000)"""),
        {"i": str(oid), "n": f"SO-{oid.hex[:8]}", "c": str(cid)})
    await db.commit()

    row = (await db.execute(
        text("""SELECT sales_channel, distributor_id, territory_id, status
                  FROM sales_orders WHERE id = :i"""),
        {"i": str(oid)})).mappings().first()
    assert row["sales_channel"] == "DIRECT"
    assert row["distributor_id"] is None
    assert row["territory_id"] is None
    assert row["status"] == "pending", "the existing vocabulary is unchanged"


@pytest.mark.asyncio
async def test_existing_warehouses_default_to_company(db):
    wid = uuid.uuid4()
    await db.execute(
        text("""INSERT INTO warehouses (id, code, name)
                VALUES (:i, :c, 'Main Store')"""),
        {"i": str(wid), "c": f"WH-{wid.hex[:6]}"})
    await db.commit()
    row = (await db.execute(
        text("SELECT warehouse_kind, distributor_id FROM warehouses "
             "WHERE id = :i"), {"i": str(wid)})).mappings().first()
    assert row["warehouse_kind"] == "COMPANY"
    assert row["distributor_id"] is None
