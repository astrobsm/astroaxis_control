"""All 774 LGAs, and the guard that stops a wrong list reaching the database.

A misspelt or missing LGA does not announce itself -- it produces territory
reports that are quietly incomplete for years. So the migration verifies its own
data against the official per-state counts and refuses to apply if anything is
off, and these tests check that the guard actually works rather than trusting
that it does.
"""
import importlib.util
import os
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")
SYNC_DB = (TEST_DB or "").replace("+asyncpg", "")

BASE_SCHEMA = """
DROP TABLE IF EXISTS distributor_outlets CASCADE;
DROP TABLE IF EXISTS territory_lgas CASCADE;
DROP TABLE IF EXISTS distributors CASCADE;
DROP TABLE IF EXISTS lgas CASCADE;
DROP TABLE IF EXISTS states CASCADE;
DROP TABLE IF EXISTS countries CASCADE;
DROP TABLE IF EXISTS sales_orders CASCADE;
DROP TABLE IF EXISTS warehouses CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS users CASCADE;

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL, full_name VARCHAR(255) NOT NULL,
    hashed_password VARCHAR(255) NOT NULL DEFAULT 'x',
    role VARCHAR(50) NOT NULL DEFAULT 'admin',
    is_active BOOLEAN DEFAULT TRUE, is_locked BOOLEAN DEFAULT FALSE,
    failed_login_attempts INTEGER DEFAULT 0, last_login TIMESTAMPTZ,
    two_factor_enabled BOOLEAN DEFAULT FALSE, two_factor_secret VARCHAR(255),
    phone VARCHAR(20), department VARCHAR(100),
    created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ
);
CREATE TABLE customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_code VARCHAR(32) UNIQUE NOT NULL, name VARCHAR(255) NOT NULL
);
CREATE TABLE products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sku VARCHAR(64) UNIQUE NOT NULL, name VARCHAR(255) NOT NULL
);
CREATE TABLE warehouses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(32) UNIQUE NOT NULL, name VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE
);
CREATE TABLE sales_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_number VARCHAR(64) UNIQUE NOT NULL,
    customer_id UUID NOT NULL REFERENCES customers(id)
);
"""

MIGRATIONS = ["x3456789012w_distributor_foundation.py",
              "g2345678901f_all_lgas.py"]


def _load(filename, alias=None):
    path = (Path(__file__).resolve().parents[1] / "alembic" / "versions"
            / filename)
    spec = importlib.util.spec_from_file_location(
        alias or f"lga_{uuid.uuid4().hex[:6]}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _apply(filenames):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = create_engine(SYNC_DB, future=True)
    for filename in filenames:
        mod = _load(filename)
        with engine.begin() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                mod.upgrade()
    engine.dispose()


@pytest.fixture(scope="module")
def seeded():
    engine = create_engine(SYNC_DB, future=True)
    with engine.begin() as conn:
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        for stmt in BASE_SCHEMA.split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
    engine.dispose()
    _apply(MIGRATIONS)
    yield


@pytest_asyncio.fixture
async def db(seeded):
    engine = create_async_engine(TEST_DB, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------------------------
# The data
# ---------------------------------------------------------------------------

def test_the_seed_data_checks_itself():
    seed = _load("g2345678901f_all_lgas.py")
    assert seed._verify() == 774


def test_every_state_has_its_official_number():
    seed = _load("g2345678901f_all_lgas.py")
    for code, expected in seed.EXPECTED_COUNTS.items():
        assert len(seed.LGAS[code]) == expected, (
            f"{code}: {len(seed.LGAS[code])} listed, {expected} expected")
    assert sum(seed.EXPECTED_COUNTS.values()) == 774


def test_a_wrong_count_stops_the_migration_rather_than_loading(db):
    """The guard has to actually fire, or it is decoration."""
    seed = _load("g2345678901f_all_lgas.py", alias="lga_tamper")
    original = list(seed.LGAS["LA"])
    try:
        seed.LGAS["LA"] = original[:-1]          # one short
        with pytest.raises(RuntimeError) as exc:
            seed._verify()
        assert "19 LGAs listed, 20 expected" in str(exc.value)
        assert "NOT applied" in str(exc.value)

        seed.LGAS["LA"] = original + [original[0]]   # duplicate
        with pytest.raises(RuntimeError) as exc:
            seed._verify()
        assert "duplicated name" in str(exc.value)
    finally:
        seed.LGAS["LA"] = original


def test_no_lga_name_is_blank_or_padded():
    seed = _load("g2345678901f_all_lgas.py")
    for code, names in seed.LGAS.items():
        for name in names:
            assert name and name == name.strip(), f"{code}: {name!r}"
            assert len(name) >= 2, f"{code}: {name!r}"


# ---------------------------------------------------------------------------
# The database
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_all_774_are_loaded(db):
    total = (await db.execute(text("SELECT COUNT(*) FROM lgas"))).scalar()
    assert total == 774


@pytest.mark.asyncio
async def test_not_one_state_is_left_empty(db):
    """An empty state is a coverage map that says "unknown" forever."""
    empty = (await db.execute(
        text("""SELECT s.code, s.name FROM states s
                 WHERE NOT EXISTS (SELECT 1 FROM lgas l
                                    WHERE l.state_id = s.id)"""))).all()
    assert empty == [], f"states with no LGAs: {empty}"

    counts = (await db.execute(
        text("""SELECT s.code, COUNT(l.id) AS n FROM states s
                  LEFT JOIN lgas l ON l.state_id = s.id
                 GROUP BY s.code"""))).mappings().all()
    seed = _load("g2345678901f_all_lgas.py")
    for row in counts:
        assert row["n"] == seed.EXPECTED_COUNTS[row["code"]], (
            f"{row['code']} has {row['n']} in the database")


@pytest.mark.asyncio
async def test_running_it_twice_changes_nothing(db):
    """Lagos and the FCT were already seeded; re-running must not duplicate."""
    before = (await db.execute(text("SELECT COUNT(*) FROM lgas"))).scalar()
    lagos_before = (await db.execute(
        text("""SELECT id FROM lgas
                 WHERE name = 'Ikeja' ORDER BY id"""))).scalars().all()

    _apply(["g2345678901f_all_lgas.py"])

    after = (await db.execute(text("SELECT COUNT(*) FROM lgas"))).scalar()
    lagos_after = (await db.execute(
        text("""SELECT id FROM lgas
                 WHERE name = 'Ikeja' ORDER BY id"""))).scalars().all()

    assert after == before == 774
    assert lagos_after == lagos_before, (
        "an existing LGA's id changed; any territory built on it would now "
        "point at a different row")


@pytest.mark.asyncio
async def test_the_original_lagos_rows_survived(db):
    """They predate this migration and territories may already use them."""
    lagos = (await db.execute(
        text("""SELECT COUNT(*) FROM lgas l JOIN states s ON s.id = l.state_id
                 WHERE s.code = 'LA'"""))).scalar()
    assert lagos == 20

    ikeja = (await db.execute(
        text("SELECT COUNT(*) FROM lgas WHERE name = 'Ikeja'"))).scalar()
    assert ikeja == 1, "Ikeja was duplicated"


@pytest.mark.asyncio
async def test_the_corrections_made_by_hand_are_in_the_database(db):
    """The five the count check could not have caught."""
    for state, name in (("KT", "Mai'Adua"), ("TA", "Kurmi"), ("KW", "Patigi"),
                        ("OG", "Yewa North"), ("ZA", "Tsafe")):
        found = (await db.execute(
            text("""SELECT COUNT(*) FROM lgas l JOIN states s ON s.id = l.state_id
                     WHERE s.code = :s AND l.name = :n"""),
            {"s": state, "n": name})).scalar()
        assert found == 1, f"{name} missing from {state}"

    # And the wrong ones are not there.
    for wrong in ("Mabudi", "Kumi", "Pategi", "Egbado North", "Chafe"):
        found = (await db.execute(
            text("SELECT COUNT(*) FROM lgas WHERE name = :n"),
            {"n": wrong})).scalar()
        assert found == 0, f"{wrong} is still in the data"
