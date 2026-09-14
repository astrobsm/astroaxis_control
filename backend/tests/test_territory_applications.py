"""Territory applications, and exclusivity enforced on the ground.

The claim this file exists to defend is the one in the migration docstring:

    Two exclusive territories can cover the same LGA. Each satisfies the
    per-territory uniqueness index. Together they promise the same ground to two
    distributors, and the company has said so twice in writing.

So the tests below assert that the database -- not a service check, not the UI --
refuses that, by every route into it: granting an assignment, and widening a
territory's coverage after it is already assigned. They also cover the
application trail that a grant should normally come from, and the release of
territory when a distributor is terminated.
"""
import importlib.util
import os
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.services import applications as apps
from app.services import distributors as dsvc
from app.services import geography as geo

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")
SYNC_DB = (TEST_DB or "").replace("+asyncpg", "")

BASE_SCHEMA = """
DROP TABLE IF EXISTS distributor_agreement_signatures CASCADE;
DROP TABLE IF EXISTS distributor_agreements CASCADE;
DROP TABLE IF EXISTS facility_corrective_actions CASCADE;
DROP TABLE IF EXISTS facility_assessment_items CASCADE;
DROP TABLE IF EXISTS facility_assessments CASCADE;
DROP TABLE IF EXISTS distributor_facilities CASCADE;
DROP TABLE IF EXISTS facility_checklist_items CASCADE;
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
DROP TABLE IF EXISTS sales_orders CASCADE;
DROP TABLE IF EXISTS stock_levels CASCADE;
DROP TABLE IF EXISTS warehouses CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS users CASCADE;

CREATE TABLE users (
    -- Mirrors app.models.User in full. A cut-down users table here poisons the
    -- shared test database for every later test that goes through the ORM, and
    -- which test that is depends on collection order, so it is spelled out.
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    full_name VARCHAR(255) NOT NULL,
    hashed_password VARCHAR(255) NOT NULL DEFAULT 'x',
    role VARCHAR(50) NOT NULL DEFAULT 'admin',
    is_active BOOLEAN DEFAULT TRUE,
    is_locked BOOLEAN DEFAULT FALSE,
    failed_login_attempts INTEGER DEFAULT 0,
    last_login TIMESTAMPTZ,
    two_factor_enabled BOOLEAN DEFAULT FALSE,
    two_factor_secret VARCHAR(255),
    phone VARCHAR(20),
    department VARCHAR(100),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ
);
CREATE TABLE customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_code VARCHAR(32) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL, email VARCHAR(255), phone VARCHAR(50),
    address TEXT, credit_limit NUMERIC(12,2) DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sku VARCHAR(64) UNIQUE NOT NULL, name VARCHAR(255) NOT NULL
);
CREATE TABLE warehouses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(32) UNIQUE NOT NULL, name VARCHAR(255) NOT NULL,
    location VARCHAR(255), is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE stock_levels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    warehouse_id UUID NOT NULL REFERENCES warehouses(id),
    product_id UUID REFERENCES products(id), raw_material_id UUID,
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

MIGRATIONS = [
    ("m4_dist", "x3456789012w_distributor_foundation.py"),
    ("m4_comp", "y4567890123x_distributor_compliance.py"),
    ("m4_apps", "z5678901234y_territory_applications.py"),
]


def _load(name, filename):
    path = (Path(__file__).resolve().parents[1] / "alembic" / "versions"
            / filename)
    spec = importlib.util.spec_from_file_location(name, path)
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
    for name, fn in MIGRATIONS:
        mod = _load(name, fn)
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

async def _admin(db, name="Territory Manager"):
    uid = uuid.uuid4()
    await db.execute(
        text("INSERT INTO users (id, email, full_name, role) "
             "VALUES (:i, :e, :n, 'admin')"),
        {"i": str(uid), "e": f"{uid}@t.test", "n": name})
    await db.commit()
    return FakeUser(uid, full_name=name)


async def _ground(db, n=4):
    """A state, and `n` LGAs that belong to this test alone.

    Freshly created rather than borrowed from the Lagos seed: these tests hand
    ground to distributors and the assignments stay on the record afterwards
    (that is the point of them), so two tests sharing an LGA would make the
    second one fail for reasons that have nothing to do with what it asserts.
    """
    state = (await db.execute(
        text("SELECT id FROM states WHERE code = 'LA'"))).scalar()
    tag = uuid.uuid4().hex[:8]
    lgas = []
    for i in range(n):
        lid = uuid.uuid4()
        name = f"Testland {tag} {i}"
        await db.execute(
            text("""INSERT INTO lgas (id, state_id, name, code)
                    VALUES (:i, :s, :n, :c)"""),
            {"i": str(lid), "s": str(state), "n": name,
             "c": f"TL{tag[:4]}{i}".upper()})
        lgas.append({"id": lid, "name": name})
    await db.commit()
    return state, lgas


async def _territory(db, admin, state_id, lga_ids, *, code=None,
                     exclusive=True):
    r = await geo.create_territory(
        db, code=code or f"T{uuid.uuid4().hex[:6].upper()}",
        name=f"Territory {uuid.uuid4().hex[:4]}", state_id=state_id,
        lga_ids=lga_ids, is_exclusive=exclusive, actor=admin)
    await db.commit()
    return uuid.UUID(r["id"])


async def _distributor(db, admin, *, approve=True, name=None):
    r = await dsvc.create_distributor(
        db, legal_name=name or f"Holder {uuid.uuid4().hex[:6]}", actor=admin,
        acknowledge_duplicates=True)
    await db.commit()
    did = uuid.UUID(r["id"])
    if approve:
        for status, reason in (("APPLIED", "Application submitted"),
                               ("UNDER_REVIEW", "Taken up for review"),
                               ("APPROVED", "Meets the criteria")):
            await dsvc.set_status(db, distributor_id=did, new_status=status,
                                  reason=reason, actor=admin)
        await db.commit()
    return did


# ---------------------------------------------------------------------------
# The guarantee: exclusivity is about ground, not about rows
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_two_exclusive_territories_cannot_promise_the_same_lga(db):
    """The whole point of this phase.

    Both territories are exclusive. Each has exactly one holder, so the phase 1
    per-territory index is satisfied by both. Together they promise the same LGA
    to two different distributors -- which is the dispute exclusivity exists to
    prevent.
    """
    admin = await _admin(db)
    state, lgas = await _ground(db)
    shared, only_a, only_b = lgas[0], lgas[1], lgas[2]

    t_a = await _territory(db, admin, state, [shared["id"], only_a["id"]])
    t_b = await _territory(db, admin, state, [shared["id"], only_b["id"]])

    d_a = await _distributor(db, admin)
    d_b = await _distributor(db, admin)

    await geo.assign_territory(db, territory_id=t_a, distributor_id=d_a,
                               reason="First grant", actor=admin)
    await db.commit()

    with pytest.raises(Exception) as exc:
        await geo.assign_territory(db, territory_id=t_b, distributor_id=d_b,
                                   reason="Second grant", actor=admin)
        await db.commit()
    await db.rollback()

    message = str(exc.value)
    assert "two" in message and "at once" in message
    assert shared["name"] in message, (
        "the error must name the LGA actually in dispute")


@pytest.mark.asyncio
async def test_one_distributor_may_hold_overlapping_territories(db):
    """Overlap with yourself is untidy, not a contradiction."""
    admin = await _admin(db)
    state, lgas = await _ground(db)
    shared = lgas[0]

    t_a = await _territory(db, admin, state, [shared["id"], lgas[1]["id"]])
    t_b = await _territory(db, admin, state, [shared["id"], lgas[2]["id"]])
    holder = await _distributor(db, admin)

    await geo.assign_territory(db, territory_id=t_a, distributor_id=holder,
                               reason="First", actor=admin)
    await geo.assign_territory(db, territory_id=t_b, distributor_id=holder,
                               reason="Second, same holder", actor=admin)
    await db.commit()

    held = await apps.distributor_territories(db, holder)
    assert len(held["current"]) == 2


@pytest.mark.asyncio
async def test_a_non_exclusive_overlap_is_allowed(db):
    """Two distributors may share an LGA when neither holds it exclusively."""
    admin = await _admin(db)
    state, lgas = await _ground(db)
    shared = lgas[0]

    t_a = await _territory(db, admin, state, [shared["id"], lgas[1]["id"]],
                           exclusive=False)
    t_b = await _territory(db, admin, state, [shared["id"], lgas[2]["id"]],
                           exclusive=False)
    d_a = await _distributor(db, admin)
    d_b = await _distributor(db, admin)

    await geo.assign_territory(db, territory_id=t_a, distributor_id=d_a,
                               reason="Shared coverage", actor=admin)
    await geo.assign_territory(db, territory_id=t_b, distributor_id=d_b,
                               reason="Shared coverage", actor=admin)
    await db.commit()

    conflicts = await apps.territory_conflicts(db, territory_id=t_b,
                                               distributor_id=d_b)
    assert conflicts, "the overlap is still reported"
    assert all(not c["blocking"] for c in conflicts), (
        "but it does not block, because neither side is exclusive")


@pytest.mark.asyncio
async def test_widening_coverage_cannot_create_the_clash_by_the_back_door(db):
    """Adding an LGA to an assigned territory is the other route in.

    Without a guard on territory_lgas you can promise the same ground twice
    without ever touching territory_assignments.
    """
    admin = await _admin(db)
    state, lgas = await _ground(db)
    contested = lgas[0]

    t_a = await _territory(db, admin, state, [contested["id"]])
    t_b = await _territory(db, admin, state, [lgas[1]["id"]])
    d_a = await _distributor(db, admin)
    d_b = await _distributor(db, admin)

    await geo.assign_territory(db, territory_id=t_a, distributor_id=d_a,
                               reason="Holds the contested LGA", actor=admin)
    await geo.assign_territory(db, territory_id=t_b, distributor_id=d_b,
                               reason="Holds elsewhere", actor=admin)
    await db.commit()

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("""INSERT INTO territory_lgas (territory_id, lga_id)
                    VALUES (:t, :l)"""),
            {"t": str(t_b), "l": str(contested["id"])})
        await db.commit()
    await db.rollback()
    assert contested["name"] in str(exc.value)
    assert "two distributors at once" in str(exc.value)


@pytest.mark.asyncio
async def test_an_exclusive_territory_refuses_a_second_holder_either_way(db):
    """Closes the gap the phase 1 index leaves.

    That index only covers rows where is_exclusive is true. A non-exclusive
    assignment row on an exclusive territory slipped past it.
    """
    admin = await _admin(db)
    state, lgas = await _ground(db)
    t = await _territory(db, admin, state, [lgas[0]["id"]])
    d_a = await _distributor(db, admin)
    d_b = await _distributor(db, admin)

    await geo.assign_territory(db, territory_id=t, distributor_id=d_a,
                               reason="First holder", actor=admin)
    await db.commit()

    # Straight through the table, with is_exclusive FALSE, which is what the
    # phase 1 partial index does not see.
    with pytest.raises(Exception) as exc:
        await db.execute(
            text("""INSERT INTO territory_assignments
                        (id, territory_id, distributor_id, assigned_from,
                         is_exclusive, status)
                    VALUES (gen_random_uuid(), :t, :d, CURRENT_DATE,
                            FALSE, 'ACTIVE')"""),
            {"t": str(t), "d": str(d_b)})
        await db.commit()
    await db.rollback()
    assert "exclusive" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_ground_is_free_again_once_an_assignment_ends(db):
    admin = await _admin(db)
    state, lgas = await _ground(db)
    shared = lgas[0]

    t_a = await _territory(db, admin, state, [shared["id"]])
    t_b = await _territory(db, admin, state, [shared["id"]])
    d_a = await _distributor(db, admin)
    d_b = await _distributor(db, admin)

    first = await geo.assign_territory(db, territory_id=t_a,
                                       distributor_id=d_a,
                                       reason="Initial", actor=admin)
    await db.commit()

    await geo.end_assignment(db, assignment_id=uuid.UUID(first["id"]),
                             reason="Distributor stepped back", actor=admin)
    await db.commit()

    # Now the same ground can be granted to someone else.
    await geo.assign_territory(db, territory_id=t_b, distributor_id=d_b,
                               reason="Reassigned", actor=admin)
    await db.commit()

    history = await geo.assignment_history(db, t_a)
    assert len(history) == 1
    assert history[0]["assigned_to"] is not None, "the row stays, closed"
    assert history[0]["end_reason"] == "Distributor stepped back"


@pytest.mark.asyncio
async def test_terminating_a_distributor_frees_its_territory(db):
    """Otherwise the ground is blocked forever with nothing saying why."""
    admin = await _admin(db)
    state, lgas = await _ground(db)
    t = await _territory(db, admin, state, [lgas[0]["id"]])
    leaving = await _distributor(db, admin)
    await dsvc.set_status(db, distributor_id=leaving, new_status="ACTIVE",
                          reason="Trading", actor=admin)
    await db.commit()

    await geo.assign_territory(db, territory_id=t, distributor_id=leaving,
                               reason="Granted", actor=admin)
    await db.commit()

    result = await dsvc.set_status(
        db, distributor_id=leaving, new_status="TERMINATED",
        reason="Contract ended by mutual agreement", actor=admin)
    await db.commit()

    assert len(result["territories_released"]) == 1
    assert await geo.current_holder(db, t) is None

    # And the ground is grantable again.
    replacement = await _distributor(db, admin)
    await geo.assign_territory(db, territory_id=t, distributor_id=replacement,
                               reason="New holder", actor=admin)
    await db.commit()
    holder = await geo.current_holder(db, t)
    assert str(holder["id"]) == str(replacement)


# ---------------------------------------------------------------------------
# The application trail
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_approved_application_creates_the_assignment_it_authorised(db):
    admin = await _admin(db)
    reviewer = await _admin(db, name="Regional Director")
    state, lgas = await _ground(db)
    t = await _territory(db, admin, state, [lgas[0]["id"]])
    applicant = await _distributor(db, admin)

    applied = await apps.apply_for_territory(
        db, distributor_id=applicant, territory_id=t,
        statement="We have a warehouse and six marketers in the area.",
        actor=admin)
    await db.commit()
    aid = uuid.UUID(applied["id"])
    assert applied["status"] == "SUBMITTED"

    packet = await apps.review_packet(db, application_id=aid)
    assert packet["can_be_granted"] is True
    assert packet["conflicts"] == []
    # Eligibility and conflicts are separate judgements, never one number.
    assert "eligibility" in packet and "blocking_conditions" in packet

    await apps.begin_review(db, application_id=aid, actor=reviewer)
    await db.commit()

    decided = await apps.decide(
        db, application_id=aid, approve=True,
        note="Territory granted following review of capacity.", actor=reviewer)
    await db.commit()

    assert decided["status"] == "APPROVED"
    assert decided["assignment"] is not None

    row = await apps.get_application(db, aid)
    assert row["assignment_id"] == decided["assignment"]["id"], (
        "the application must point at the assignment it produced")
    assert row["eligibility_score"] is not None, (
        "the score is snapshotted at review, not recomputed later")

    holder = await geo.current_holder(db, t)
    assert str(holder["id"]) == str(applicant)


@pytest.mark.asyncio
async def test_an_application_over_contested_ground_cannot_be_approved(db):
    """And the refusal is not offered as an override, because it cannot be."""
    admin = await _admin(db)
    state, lgas = await _ground(db)
    shared = lgas[0]

    held = await _territory(db, admin, state, [shared["id"]])
    wanted = await _territory(db, admin, state, [shared["id"], lgas[1]["id"]])
    incumbent = await _distributor(db, admin)
    challenger = await _distributor(db, admin)

    await geo.assign_territory(db, territory_id=held,
                               distributor_id=incumbent,
                               reason="Existing holder", actor=admin)
    await db.commit()

    applied = await apps.apply_for_territory(
        db, distributor_id=challenger, territory_id=wanted, actor=admin)
    await db.commit()
    aid = uuid.UUID(applied["id"])

    packet = await apps.review_packet(db, application_id=aid)
    assert packet["can_be_granted"] is False
    assert packet["recommendation"] == "CANNOT_GRANT"
    assert packet["blocking_conflicts"], "the reviewer is shown the clash"

    # Acknowledging does not help: the database refuses it, so offering an
    # override would be a button that cannot work.
    for ack in (False, True):
        with pytest.raises(HTTPException) as exc:
            await apps.decide(db, application_id=aid, approve=True,
                              note="Trying to grant anyway",
                              acknowledge_conflicts=ack, actor=admin)
        await db.rollback()
        assert exc.value.status_code == 409
        assert "cannot be granted" in exc.value.detail

    # Refusing it works, and records why.
    refused = await apps.decide(
        db, application_id=aid, approve=False,
        note="Ground already held exclusively by the incumbent.", actor=admin)
    await db.commit()
    assert refused["status"] == "REJECTED"

    row = await apps.get_application(db, aid)
    assert row["conflicts_at_decision"], (
        "what the reviewer was shown is part of the record")


@pytest.mark.asyncio
async def test_an_advisory_overlap_must_be_acknowledged_but_can_be_granted(db):
    admin = await _admin(db)
    state, lgas = await _ground(db)
    shared = lgas[0]

    held = await _territory(db, admin, state, [shared["id"]], exclusive=False)
    wanted = await _territory(db, admin, state, [shared["id"]],
                              exclusive=False)
    incumbent = await _distributor(db, admin)
    applicant = await _distributor(db, admin)

    await geo.assign_territory(db, territory_id=held, distributor_id=incumbent,
                               reason="Non-exclusive holder", actor=admin)
    await db.commit()

    applied = await apps.apply_for_territory(
        db, distributor_id=applicant, territory_id=wanted, actor=admin)
    await db.commit()
    aid = uuid.UUID(applied["id"])

    with pytest.raises(HTTPException) as exc:
        await apps.decide(db, application_id=aid, approve=True,
                          note="Granting without looking", actor=admin)
    await db.rollback()
    assert exc.value.status_code == 409
    assert "confirm you intend it" in exc.value.detail

    granted = await apps.decide(
        db, application_id=aid, approve=True,
        note="Overlap is intended; both sell different product lines here.",
        acknowledge_conflicts=True, actor=admin)
    await db.commit()
    assert granted["status"] == "APPROVED"
    assert granted["conflicts_acknowledged"], (
        "what was acknowledged is recorded, not just that something was")


@pytest.mark.asyncio
async def test_a_decided_application_cannot_be_rewritten(db):
    admin = await _admin(db)
    state, lgas = await _ground(db)
    t = await _territory(db, admin, state, [lgas[0]["id"]])
    applicant = await _distributor(db, admin)

    applied = await apps.apply_for_territory(
        db, distributor_id=applicant, territory_id=t, actor=admin)
    await db.commit()
    aid = uuid.UUID(applied["id"])
    await apps.decide(db, application_id=aid, approve=False,
                      note="Insufficient storage capacity at this time.",
                      actor=admin)
    await db.commit()

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("""UPDATE distributor_applications
                       SET status = 'APPROVED' WHERE id = :a"""),
            {"a": str(aid)})
        await db.commit()
    await db.rollback()
    assert "already rejected" in str(exc.value)

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("DELETE FROM distributor_applications WHERE id = :a"),
            {"a": str(aid)})
        await db.commit()
    await db.rollback()
    assert "not deletable" in str(exc.value)

    # Deciding twice is refused by the service with a readable message too.
    with pytest.raises(HTTPException) as exc:
        await apps.decide(db, application_id=aid, approve=True,
                          note="Changed my mind", actor=admin)
    await db.rollback()
    assert "already been decided" in exc.value.detail


@pytest.mark.asyncio
async def test_one_open_application_per_distributor_per_territory(db):
    admin = await _admin(db)
    state, lgas = await _ground(db)
    t = await _territory(db, admin, state, [lgas[0]["id"]])
    applicant = await _distributor(db, admin)

    await apps.apply_for_territory(db, distributor_id=applicant,
                                   territory_id=t, actor=admin)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await apps.apply_for_territory(db, distributor_id=applicant,
                                       territory_id=t, actor=admin)
    await db.rollback()
    assert exc.value.status_code == 409
    assert "already open" in exc.value.detail


@pytest.mark.asyncio
async def test_a_withdrawn_application_frees_the_applicant_to_reapply(db):
    admin = await _admin(db)
    state, lgas = await _ground(db)
    t = await _territory(db, admin, state, [lgas[0]["id"]])
    applicant = await _distributor(db, admin)

    first = await apps.apply_for_territory(
        db, distributor_id=applicant, territory_id=t, actor=admin)
    await db.commit()

    await apps.withdraw(db, application_id=uuid.UUID(first["id"]),
                        reason="Storage not ready until next quarter.",
                        actor=admin)
    await db.commit()

    again = await apps.apply_for_territory(
        db, distributor_id=applicant, territory_id=t, actor=admin)
    await db.commit()
    assert again["status"] == "SUBMITTED"

    # Both attempts remain visible; nothing was deleted.
    rows = await apps.list_applications(db, distributor_id=applicant,
                                        territory_id=t)
    assert len(rows) == 2
    assert {r["status"] for r in rows} == {"WITHDRAWN", "SUBMITTED"}


@pytest.mark.asyncio
async def test_a_terminated_distributor_cannot_apply(db):
    admin = await _admin(db)
    state, lgas = await _ground(db)
    t = await _territory(db, admin, state, [lgas[0]["id"]])
    gone = await _distributor(db, admin)
    await dsvc.set_status(db, distributor_id=gone, new_status="TERMINATED",
                          reason="Ceased trading", actor=admin)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await apps.apply_for_territory(db, distributor_id=gone,
                                       territory_id=t, actor=admin)
    await db.rollback()
    assert "terminated" in exc.value.detail


@pytest.mark.asyncio
async def test_review_snapshots_the_score_the_reviewer_worked_from(db):
    """Recomputing it later would rewrite why the decision was made."""
    admin = await _admin(db)
    state, lgas = await _ground(db)
    t = await _territory(db, admin, state, [lgas[0]["id"]])
    applicant = await _distributor(db, admin)

    applied = await apps.apply_for_territory(
        db, distributor_id=applicant, territory_id=t, actor=admin)
    await db.commit()
    aid = uuid.UUID(applied["id"])

    await apps.begin_review(db, application_id=aid, actor=admin)
    await db.commit()
    at_review = (await apps.get_application(db, aid))["eligibility_score"]

    # Verify a document, which would raise a fresh assessment.
    doc = await dsvc.attach_document(
        db, distributor_id=applicant, doc_type="CAC_CERTIFICATE",
        filename="cac.pdf", content_type="application/pdf",
        content=b"certificate", actor=admin)
    await db.commit()
    checker = await _admin(db, name="Document Checker")
    await dsvc.verify_document(db, document_id=uuid.UUID(doc["id"]),
                               verified=True, user=checker)
    await db.commit()

    after = (await apps.get_application(db, aid))["eligibility_score"]
    assert after == at_review, (
        "the figure on the application is the one the reviewer saw")


@pytest.mark.asyncio
async def test_the_queue_shows_only_what_is_still_open(db):
    admin = await _admin(db)
    state, lgas = await _ground(db)
    t_open = await _territory(db, admin, state, [lgas[0]["id"]])
    t_done = await _territory(db, admin, state, [lgas[1]["id"]])
    applicant = await _distributor(db, admin)

    await apps.apply_for_territory(db, distributor_id=applicant,
                                   territory_id=t_open, actor=admin)
    decided = await apps.apply_for_territory(
        db, distributor_id=applicant, territory_id=t_done, actor=admin)
    await db.commit()
    await apps.decide(db, application_id=uuid.UUID(decided["id"]),
                      approve=False, note="Not this one.", actor=admin)
    await db.commit()

    queue = await apps.list_applications(db, distributor_id=applicant,
                                         open_only=True)
    assert len(queue) == 1
    assert queue[0]["territory_code"]
