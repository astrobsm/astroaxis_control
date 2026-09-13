"""Compliance: facility assessment, corrective actions, agreements.

The claims defended here are the two the specification is most insistent about,
plus the evidence discipline that makes a signature worth anything:

  * a company policy can never be recorded as law -- a REGULATORY checklist
    item without a named authority is refused by the database;
  * a critical or regulatory failure makes the outcome FAIL regardless of how
    good the weighted score looks;
  * an answer snapshots the question, so rewording the checklist next year
    cannot change what a past inspector is recorded as having found;
  * NOT_APPLICABLE is excluded from both sides of the score, so a facility is
    not marked down for lacking a cold chain it does not need;
  * every failure raises a tracked corrective action;
  * an agreement's text freezes at issue, and a signature is refused unless it
    echoes the hash of the text actually displayed;
  * both parties must sign before an agreement can be in force;
  * signatures are append-only, and agreements cannot be deleted.
"""
import hashlib
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

from app.services import compliance as csvc
from app.services import distributors as dsvc

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
    -- Mirrors app.models.Customer in full, for the reason given on users above.
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
    -- Mirrors app.models.Product in full, for the reason given on users above.
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sku VARCHAR(64) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    manufacturer VARCHAR(255),
    unit VARCHAR(32) NOT NULL DEFAULT 'each',
    reorder_level NUMERIC(18,6) DEFAULT 0,
    cost_price NUMERIC(18,2) DEFAULT 0,
    selling_price NUMERIC(18,2) DEFAULT 0,
    retail_price NUMERIC(18,2),
    wholesale_price NUMERIC(18,2),
    lead_time_days INTEGER DEFAULT 0,
    minimum_order_quantity NUMERIC(18,6) DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE warehouses (
    -- Mirrors app.models.Warehouse in full, for the reason given on users above.
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(32) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    location VARCHAR(255),
    manager_id UUID,
    is_active BOOLEAN DEFAULT TRUE,
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
    ("m_dist", "x3456789012w_distributor_foundation.py"),
    ("m_comp", "y4567890123x_distributor_compliance.py"),
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


async def _admin(session, name="Admin"):
    uid = uuid.uuid4()
    await session.execute(
        text("INSERT INTO users (id, email, full_name, role) "
             "VALUES (:i, :e, :n, 'admin')"),
        {"i": str(uid), "e": f"{uid}@t.test", "n": name})
    await session.commit()
    return FakeUser(uid, full_name=name)


async def _distributor(session, actor):
    r = await dsvc.create_distributor(
        session, legal_name=f"Compliance Test {uuid.uuid4().hex[:6]}",
        actor=actor, acknowledge_duplicates=True)
    await session.commit()
    return uuid.UUID(r["id"])


async def _facility(session, actor, distributor_id=None):
    did = distributor_id or await _distributor(session, actor)
    r = await csvc.create_facility(
        session, distributor_id=did, name="Main Store", town="Ikeja",
        actor=actor)
    await session.commit()
    return did, uuid.UUID(r["id"])


async def _item_by_code(session, code):
    row = (await session.execute(
        text("SELECT id, is_critical, requires_evidence "
             "FROM facility_checklist_items WHERE code = :c"),
        {"c": code})).mappings().first()
    return row


async def _answer_all(session, assessment_id, result="PASS", *, evidence=None,
                      skip=()):
    """Answer every checklist item, attaching evidence where one is required."""
    items = (await session.execute(
        text("""SELECT id, code, requires_evidence
                  FROM facility_checklist_items WHERE is_active
                 ORDER BY sort_order"""))).mappings().all()
    for it in items:
        if it["code"] in skip:
            continue
        await csvc.answer_item(
            session, assessment_id=assessment_id,
            checklist_item_id=it["id"], result=result,
            evidence_document_id=(evidence if it["requires_evidence"]
                                  and result in ("PASS", "REQUIRES_CORRECTION")
                                  else None))
    await session.commit()


async def _evidence_doc(session, distributor_id, actor):
    r = await dsvc.attach_document(
        session, distributor_id=distributor_id, doc_type="FACILITY_PHOTO",
        filename="store.jpg", content_type="image/jpeg",
        content=b"photo-of-the-store", actor=actor)
    await session.commit()
    return uuid.UUID(r["id"])


# ---------------------------------------------------------------------------
# Company policy is never law
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_seeded_checklist_claims_no_regulatory_authority(db):
    """Section 3: the system must not invent which laws apply.

    Everything shipped is a COMPANY requirement. Marking an item regulatory is
    a deliberate act by someone who knows which regulation they mean.
    """
    rows = (await db.execute(
        text("""SELECT requirement_kind, COUNT(*) AS n
                  FROM facility_checklist_items GROUP BY requirement_kind"""),
    )).mappings().all()
    kinds = {r["requirement_kind"]: r["n"] for r in rows}
    assert kinds.get("COMPANY", 0) >= 20
    assert "REGULATORY" not in kinds, (
        "no shipped item may assert that a law requires it")


@pytest.mark.asyncio
async def test_a_regulatory_item_must_name_its_authority(db):
    with pytest.raises(Exception) as exc:
        await db.execute(
            text("""INSERT INTO facility_checklist_items
                        (id, code, section, requirement, requirement_kind)
                    VALUES (gen_random_uuid(), 'BOGUS', 'Law',
                            'Claimed to be required by law', 'REGULATORY')"""))
        await db.commit()
    await db.rollback()
    assert "ck_chk_regulatory_has_authority" in str(exc.value)

    # With an authority named it is allowed.
    await db.execute(
        text("""INSERT INTO facility_checklist_items
                    (id, code, section, requirement, requirement_kind,
                     authority)
                VALUES (gen_random_uuid(), 'REAL_REG', 'Law',
                        'Premises registration is displayed', 'REGULATORY',
                        'NAFDAC')"""))
    await db.commit()
    await db.execute(text("DELETE FROM facility_checklist_items "
                          "WHERE code = 'REAL_REG'"))
    await db.commit()


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_fully_passing_facility_is_approved(db):
    admin = await _admin(db)
    did, fid = await _facility(db, admin)
    doc = await _evidence_doc(db, did, admin)

    started = await csvc.start_assessment(db, facility_id=fid, actor=admin)
    await db.commit()
    aid = uuid.UUID(started["id"])
    await _answer_all(db, aid, "PASS", evidence=doc)

    result = await csvc.submit_assessment(db, assessment_id=aid, actor=admin)
    await db.commit()

    assert Decimal(result["score"]) == Decimal("100.00")
    assert result["outcome"] == "PASS"
    assert result["facility_status"] == "APPROVED"
    assert result["corrective_actions_raised"] == 0


@pytest.mark.asyncio
async def test_one_critical_failure_fails_the_whole_assessment(db):
    """A 90% facility with no quarantine area is not a 90% facility."""
    admin = await _admin(db)
    did, fid = await _facility(db, admin)
    doc = await _evidence_doc(db, did, admin)

    started = await csvc.start_assessment(db, facility_id=fid, actor=admin)
    await db.commit()
    aid = uuid.UUID(started["id"])

    await _answer_all(db, aid, "PASS", evidence=doc, skip=("QUARANTINE",))
    quarantine = await _item_by_code(db, "QUARANTINE")
    assert quarantine["is_critical"] is True
    await csvc.answer_item(db, assessment_id=aid,
                           checklist_item_id=quarantine["id"], result="FAIL",
                           note="No quarantine area exists")
    await db.commit()

    scored = await csvc.score_assessment(db, aid)
    assert Decimal(scored["score"]) > Decimal("90"), (
        "the weighted score is still high")
    assert scored["outcome"] == "FAIL", (
        "but a critical failure decides the outcome, not the average")
    assert any(c["code"] == "QUARANTINE" for c in scored["critical_failures"])

    result = await csvc.submit_assessment(db, assessment_id=aid, actor=admin)
    await db.commit()
    assert result["facility_status"] == "REJECTED"
    assert result["corrective_actions_raised"] == 1


@pytest.mark.asyncio
async def test_a_regulatory_failure_is_reported_separately(db):
    """A legal failure must never be buried inside an average."""
    admin = await _admin(db)
    did, fid = await _facility(db, admin)
    doc = await _evidence_doc(db, did, admin)

    await db.execute(
        text("""INSERT INTO facility_checklist_items
                    (id, code, section, requirement, requirement_kind,
                     authority, weight, is_critical, sort_order)
                VALUES (gen_random_uuid(), 'PREMISES_REG', 'Law',
                        'Premises registration is current', 'REGULATORY',
                        'NAFDAC', 1, FALSE, 999)"""))
    await db.commit()

    started = await csvc.start_assessment(db, facility_id=fid, actor=admin)
    await db.commit()
    aid = uuid.UUID(started["id"])
    await _answer_all(db, aid, "PASS", evidence=doc, skip=("PREMISES_REG",))

    reg = await _item_by_code(db, "PREMISES_REG")
    await csvc.answer_item(db, assessment_id=aid, checklist_item_id=reg["id"],
                           result="FAIL", note="Registration lapsed")
    await db.commit()

    scored = await csvc.score_assessment(db, aid)
    assert scored["outcome"] == "FAIL"
    assert scored["critical_failures"] == [], "it was not marked critical"
    assert any(f["code"] == "PREMISES_REG"
               for f in scored["regulatory_failures"])
    assert scored["regulatory_failures"][0]["authority"] == "NAFDAC"

    await db.execute(text("DELETE FROM facility_assessment_items "
                          "WHERE assessment_id = :a"), {"a": str(aid)})
    await db.execute(text("DELETE FROM facility_checklist_items "
                          "WHERE code = 'PREMISES_REG'"))
    await db.commit()


@pytest.mark.asyncio
async def test_not_applicable_is_excluded_from_both_sides(db):
    """Marking a facility down for a cold chain it does not need is nonsense."""
    admin = await _admin(db)
    did, fid = await _facility(db, admin)
    doc = await _evidence_doc(db, did, admin)

    started = await csvc.start_assessment(db, facility_id=fid, actor=admin)
    await db.commit()
    aid = uuid.UUID(started["id"])

    await _answer_all(db, aid, "PASS", evidence=doc, skip=("HUMIDITY", "BACKUP_POWER"))
    for code in ("HUMIDITY", "BACKUP_POWER"):
        item = await _item_by_code(db, code)
        await csvc.answer_item(db, assessment_id=aid,
                               checklist_item_id=item["id"],
                               result="NOT_APPLICABLE")
    await db.commit()

    scored = await csvc.score_assessment(db, aid)
    assert Decimal(scored["score"]) == Decimal("100.00")
    assert scored["items_not_applicable"] == 2
    assert scored["outcome"] == "PASS"


@pytest.mark.asyncio
async def test_an_answer_snapshots_the_question(db):
    """Rewording the checklist must not rewrite what an inspector found."""
    admin = await _admin(db)
    did, fid = await _facility(db, admin)

    started = await csvc.start_assessment(db, facility_id=fid, actor=admin)
    await db.commit()
    aid = uuid.UUID(started["id"])

    item = await _item_by_code(db, "LIGHTING")
    await csvc.answer_item(db, assessment_id=aid, checklist_item_id=item["id"],
                           result="PASS")
    await db.commit()

    original = (await db.execute(
        text("""SELECT requirement_snapshot FROM facility_assessment_items
                 WHERE assessment_id = :a AND checklist_item_id = :i"""),
        {"a": str(aid), "i": str(item["id"])})).first()

    await db.execute(
        text("""UPDATE facility_checklist_items
                   SET requirement = 'Completely different wording now'
                 WHERE id = :i"""), {"i": str(item["id"])})
    await db.commit()

    after = (await db.execute(
        text("""SELECT requirement_snapshot FROM facility_assessment_items
                 WHERE assessment_id = :a AND checklist_item_id = :i"""),
        {"a": str(aid), "i": str(item["id"])})).first()
    assert after.requirement_snapshot == original.requirement_snapshot
    assert "Completely different" not in after.requirement_snapshot


@pytest.mark.asyncio
async def test_evidence_is_required_where_the_checklist_says_so(db):
    admin = await _admin(db)
    did, fid = await _facility(db, admin)

    started = await csvc.start_assessment(db, facility_id=fid, actor=admin)
    await db.commit()
    aid = uuid.UUID(started["id"])

    cleanliness = await _item_by_code(db, "CLEANLINESS")
    assert cleanliness["requires_evidence"] is True

    with pytest.raises(HTTPException) as exc:
        await csvc.answer_item(db, assessment_id=aid,
                               checklist_item_id=cleanliness["id"],
                               result="PASS")
    await db.rollback()
    assert "evidence" in exc.value.detail.lower()

    doc = await _evidence_doc(db, did, admin)
    await csvc.answer_item(db, assessment_id=aid,
                           checklist_item_id=cleanliness["id"], result="PASS",
                           evidence_document_id=doc)
    await db.commit()


@pytest.mark.asyncio
async def test_a_submitted_assessment_cannot_be_revised(db):
    admin = await _admin(db)
    did, fid = await _facility(db, admin)
    doc = await _evidence_doc(db, did, admin)

    started = await csvc.start_assessment(db, facility_id=fid, actor=admin)
    await db.commit()
    aid = uuid.UUID(started["id"])
    await _answer_all(db, aid, "PASS", evidence=doc)
    await csvc.submit_assessment(db, assessment_id=aid, actor=admin)
    await db.commit()

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("UPDATE facility_assessments SET score = 99 WHERE id = :a"),
            {"a": str(aid)})
        await db.commit()
    await db.rollback()
    assert "cannot be revised" in str(exc.value)

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("DELETE FROM facility_assessments WHERE id = :a"),
            {"a": str(aid)})
        await db.commit()
    await db.rollback()
    assert "not deletable" in str(exc.value)


# ---------------------------------------------------------------------------
# Corrective actions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_every_failure_raises_a_tracked_corrective_action(db):
    admin = await _admin(db)
    did, fid = await _facility(db, admin)
    doc = await _evidence_doc(db, did, admin)

    started = await csvc.start_assessment(db, facility_id=fid, actor=admin)
    await db.commit()
    aid = uuid.UUID(started["id"])

    await _answer_all(db, aid, "PASS", evidence=doc, skip=("PEST_CONTROL", "FIFO"))
    for code, result in (("PEST_CONTROL", "FAIL"),
                         ("FIFO", "REQUIRES_CORRECTION")):
        item = await _item_by_code(db, code)
        await csvc.answer_item(db, assessment_id=aid,
                               checklist_item_id=item["id"], result=result,
                               note=f"{code} finding")
    await db.commit()

    result = await csvc.submit_assessment(db, assessment_id=aid, actor=admin)
    await db.commit()
    assert result["corrective_actions_raised"] == 2

    actions = (await db.execute(
        text("""SELECT severity, status FROM facility_corrective_actions
                 WHERE assessment_id = :a"""), {"a": str(aid)})).mappings().all()
    assert all(a["status"] == "OPEN" for a in actions)
    # PEST_CONTROL is critical, so its finding is raised as CRITICAL.
    assert any(a["severity"] == "CRITICAL" for a in actions)


@pytest.mark.asyncio
async def test_a_finding_cannot_be_closed_without_verification(db):
    admin = await _admin(db)
    did, fid = await _facility(db, admin)
    doc = await _evidence_doc(db, did, admin)
    started = await csvc.start_assessment(db, facility_id=fid, actor=admin)
    await db.commit()
    aid = uuid.UUID(started["id"])
    await _answer_all(db, aid, "PASS", evidence=doc, skip=("LIGHTING",))
    item = await _item_by_code(db, "LIGHTING")
    await csvc.answer_item(db, assessment_id=aid, checklist_item_id=item["id"],
                           result="FAIL", note="Two bulbs out")
    await db.commit()
    await csvc.submit_assessment(db, assessment_id=aid, actor=admin)
    await db.commit()

    action = (await db.execute(
        text("SELECT id FROM facility_corrective_actions "
             "WHERE assessment_id = :a"), {"a": str(aid)})).first()

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("""UPDATE facility_corrective_actions
                       SET status = 'CLOSED', closed_at = NOW()
                     WHERE id = :i"""), {"i": str(action.id)})
        await db.commit()
    await db.rollback()
    assert "ck_ca_closed_verified" in str(exc.value)

    # Through the service, closing records who verified it.
    verifier = await _admin(db, name="Compliance Officer")
    await csvc.update_corrective_action(
        db, action_id=action.id, status="CLOSED", note="Bulbs replaced",
        user=verifier)
    await db.commit()
    row = (await db.execute(
        text("SELECT status, verified_by FROM facility_corrective_actions "
             "WHERE id = :i"), {"i": str(action.id)})).mappings().first()
    assert row["status"] == "CLOSED"
    assert str(row["verified_by"]) == str(verifier.id)


# ---------------------------------------------------------------------------
# Agreements
# ---------------------------------------------------------------------------

BODY = ("DISTRIBUTION AGREEMENT\n\nThis is placeholder text standing in for a "
        "contract that requires legal review before execution.\n"
        "1. Appointment\n2. Territory\n3. Payment terms\n")


@pytest.mark.asyncio
async def test_an_issued_agreement_cannot_be_edited(db):
    """Editing a contract after presenting it is the worst thing here."""
    admin = await _admin(db)
    did = await _distributor(db, admin)

    created = await csvc.create_agreement(
        db, distributor_id=did, title="Distribution Agreement", body=BODY,
        actor=admin)
    await db.commit()
    aid = uuid.UUID(created["id"])

    # Still a draft, so editing is allowed.
    await db.execute(
        text("UPDATE distributor_agreements SET body = :b WHERE id = :a"),
        {"b": BODY + "\n4. Added while still drafting", "a": str(aid)})
    await db.commit()

    await csvc.issue_agreement(db, agreement_id=aid, actor=admin)
    await db.commit()

    with pytest.raises(Exception) as exc:
        await db.execute(
            text("UPDATE distributor_agreements SET body = :b WHERE id = :a"),
            {"b": "Completely different terms", "a": str(aid)})
        await db.commit()
    await db.rollback()
    assert "has been " in str(exc.value)


@pytest.mark.asyncio
async def test_a_signature_must_match_the_text_that_was_shown(db):
    admin = await _admin(db)
    did = await _distributor(db, admin)
    created = await csvc.create_agreement(
        db, distributor_id=did, title="Distribution Agreement", body=BODY,
        actor=admin)
    await db.commit()
    aid = uuid.UUID(created["id"])
    await csvc.issue_agreement(db, agreement_id=aid, actor=admin)
    await db.commit()

    wrong = hashlib.sha256(b"some other document").hexdigest()
    with pytest.raises(HTTPException) as exc:
        await csvc.sign_agreement(
            db, agreement_id=aid, signer_name="A Distributor",
            signer_role="DISTRIBUTOR",
            meaning="I have read and accept these terms", body_sha256=wrong,
            user=admin)
    await db.rollback()
    assert exc.value.status_code == 409
    assert "does not match" in exc.value.detail

    right = created["body_sha256"]
    result = await csvc.sign_agreement(
        db, agreement_id=aid, signer_name="A Distributor",
        signer_role="DISTRIBUTOR",
        meaning="I have read and accept these terms", body_sha256=right,
        user=admin)
    await db.commit()
    assert result["status"] == "ACCEPTED"


@pytest.mark.asyncio
async def test_a_signature_must_state_what_it_means(db):
    admin = await _admin(db)
    did = await _distributor(db, admin)
    created = await csvc.create_agreement(
        db, distributor_id=did, title="Agreement", body=BODY, actor=admin)
    await db.commit()
    aid = uuid.UUID(created["id"])
    await csvc.issue_agreement(db, agreement_id=aid, actor=admin)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await csvc.sign_agreement(
            db, agreement_id=aid, signer_name="Someone",
            signer_role="DISTRIBUTOR", meaning="ok",
            body_sha256=created["body_sha256"], user=admin)
    await db.rollback()
    assert "what it means" in exc.value.detail


@pytest.mark.asyncio
async def test_both_parties_must_sign_before_it_is_in_force(db):
    admin = await _admin(db)
    company = await _admin(db, name="Managing Director")
    did = await _distributor(db, admin)
    created = await csvc.create_agreement(
        db, distributor_id=did, title="Agreement", body=BODY, actor=admin)
    await db.commit()
    aid = uuid.UUID(created["id"])
    h = created["body_sha256"]

    await csvc.issue_agreement(db, agreement_id=aid, actor=admin)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await csvc.activate_agreement(db, agreement_id=aid, actor=admin)
    await db.rollback()
    assert "countersigned" in exc.value.detail.lower()

    await csvc.sign_agreement(
        db, agreement_id=aid, signer_name="A Distributor",
        signer_role="DISTRIBUTOR", meaning="I accept these terms in full",
        body_sha256=h, user=admin)
    await db.commit()

    with pytest.raises(HTTPException):
        await csvc.activate_agreement(db, agreement_id=aid, actor=admin)
    await db.rollback()

    await csvc.sign_agreement(
        db, agreement_id=aid, signer_name="Managing Director",
        signer_role="COMPANY",
        meaning="I countersign on behalf of the company", body_sha256=h,
        user=company)
    await db.commit()

    result = await csvc.activate_agreement(db, agreement_id=aid, actor=admin)
    await db.commit()
    assert result["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_only_one_agreement_can_be_in_force(db):
    admin = await _admin(db)
    did = await _distributor(db, admin)
    first = await csvc.create_agreement(
        db, distributor_id=did, title="First", body=BODY, actor=admin)
    await db.commit()
    aid = uuid.UUID(first["id"])
    h = first["body_sha256"]
    await csvc.issue_agreement(db, agreement_id=aid, actor=admin)
    await csvc.sign_agreement(db, agreement_id=aid, signer_name="D",
                              signer_role="DISTRIBUTOR",
                              meaning="I accept these terms in full",
                              body_sha256=h, user=admin)
    other = await _admin(db, name="MD")
    await csvc.sign_agreement(db, agreement_id=aid, signer_name="MD",
                              signer_role="COMPANY",
                              meaning="I countersign for the company",
                              body_sha256=h, user=other)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await csvc.create_agreement(
            db, distributor_id=did, title="Second", body=BODY, actor=admin)
    await db.rollback()
    assert exc.value.status_code == 409
    assert "already in force" in exc.value.detail


@pytest.mark.asyncio
async def test_signatures_are_append_only(db):
    admin = await _admin(db)
    did = await _distributor(db, admin)
    created = await csvc.create_agreement(
        db, distributor_id=did, title="Agreement", body=BODY, actor=admin)
    await db.commit()
    aid = uuid.UUID(created["id"])
    await csvc.issue_agreement(db, agreement_id=aid, actor=admin)
    await csvc.sign_agreement(
        db, agreement_id=aid, signer_name="A Distributor",
        signer_role="DISTRIBUTOR", meaning="I accept these terms in full",
        body_sha256=created["body_sha256"], user=admin)
    await db.commit()

    for stmt in ("DELETE FROM distributor_agreement_signatures",
                 "UPDATE distributor_agreement_signatures SET meaning = 'x'"):
        with pytest.raises(Exception) as exc:
            await db.execute(text(stmt))
            await db.commit()
        await db.rollback()
        assert "append-only" in str(exc.value)


@pytest.mark.asyncio
async def test_an_agreement_cannot_be_deleted(db):
    admin = await _admin(db)
    did = await _distributor(db, admin)
    created = await csvc.create_agreement(
        db, distributor_id=did, title="Agreement", body=BODY, actor=admin)
    await db.commit()
    with pytest.raises(Exception) as exc:
        await db.execute(
            text("DELETE FROM distributor_agreements WHERE id = :a"),
            {"a": created["id"]})
        await db.commit()
    await db.rollback()
    assert "not deletable" in str(exc.value)


# ---------------------------------------------------------------------------
# Fit to trade
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compliance_summary_names_every_blocker(db):
    admin = await _admin(db)
    did = await _distributor(db, admin)

    summary = await csvc.compliance_summary(db, did)
    assert summary["fit_to_trade"] is False
    assert any("facility" in b.lower() for b in summary["blocking_conditions"])
    assert any("agreement" in b.lower() for b in summary["blocking_conditions"])

    # Assess the facility and put an agreement in force.
    _, fid = await _facility(db, admin, distributor_id=did)
    doc = await _evidence_doc(db, did, admin)
    started = await csvc.start_assessment(db, facility_id=fid, actor=admin)
    await db.commit()
    aid = uuid.UUID(started["id"])
    await _answer_all(db, aid, "PASS", evidence=doc)
    await csvc.submit_assessment(db, assessment_id=aid, actor=admin)
    await db.commit()

    created = await csvc.create_agreement(
        db, distributor_id=did, title="Agreement", body=BODY, actor=admin)
    await db.commit()
    agid = uuid.UUID(created["id"])
    h = created["body_sha256"]
    await csvc.issue_agreement(db, agreement_id=agid, actor=admin)
    await csvc.sign_agreement(db, agreement_id=agid, signer_name="D",
                              signer_role="DISTRIBUTOR",
                              meaning="I accept these terms in full",
                              body_sha256=h, user=admin)
    md = await _admin(db, name="MD2")
    await csvc.sign_agreement(db, agreement_id=agid, signer_name="MD",
                              signer_role="COMPANY",
                              meaning="I countersign for the company",
                              body_sha256=h, user=md)
    await db.commit()
    await csvc.activate_agreement(db, agreement_id=agid, actor=admin)
    await db.commit()

    summary = await csvc.compliance_summary(db, did)
    assert summary["fit_to_trade"] is True, summary["blocking_conditions"]
    assert summary["agreement"]["status"] == "ACTIVE"
