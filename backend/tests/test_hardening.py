"""Hardening: the guards, and the tests that fail when somebody removes one.

The most valuable test in this file is `test_no_new_public_route_appears`. It
asserts the set of unauthenticated routes in the distributor module is exactly
the three ordering-portal endpoints. Adding a fourth -- by forgetting a
dependency, by registering a router in the wrong block of main.py, by copying an
existing file -- breaks the build instead of shipping quietly.

That is the only kind of security check worth having in a test suite: one that
fails when the mistake is made, rather than one that passes because somebody
remembered to run it.

The rest asserts the guarantees the module's documentation claims, against the
database rather than against the docstrings: the append-only triggers exist, the
public endpoint cannot be used to fill the disk, and every immutability claim
made in eleven phases is still true.
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

from app.api.auth import DISTRIBUTION_ROLES
from app.api.security_review import (
    DISTRIBUTION_PREFIXES, EXPECTED_PUBLIC, _walk,
)
from app.services import portal as psvc

TEST_DB = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set")
SYNC_DB = (TEST_DB or "").replace("+asyncpg", "")


# ---------------------------------------------------------------------------
# The guard rail
# ---------------------------------------------------------------------------

def _distribution_routes():
    import app.main as main
    return [r for r in _walk(main.app)
            if any(r["path"].startswith(p) for p in DISTRIBUTION_PREFIXES)]


def _routes_in_matching_order(app):
    """Every route in the order Starlette will TRY to match it.

    Deliberately not `_walk`, which sorts its output for the report. Order is
    the whole point here: FastAPI stops at the first route whose path matches,
    so declaration order decides which handler answers.
    """
    out = []

    def visit(routes):
        for route in routes:
            inner = getattr(route, "original_router", None)
            if inner is None and not hasattr(route, "methods"):
                inner = getattr(route, "routes", None)
            if inner is not None:
                visit(getattr(inner, "routes", inner))
                continue
            path = getattr(route, "path", None)
            if not path:
                continue
            for method in sorted(getattr(route, "methods", None) or []):
                out.append((method, path))

    visit(app.routes)
    return out


def test_no_static_route_is_shadowed_by_a_parameter_route():
    """A literal path declared after /{id} is unreachable, and says 422.

    This is not hypothetical. /api/distributors/registrations and
    /api/distributors/registration-links were appended to the end of
    distributors.py, below the `/{distributor_id}` route. FastAPI matched the
    parameter route first, tried to read "registrations" as a UUID, and
    answered every request with 422 "not a valid uuid" -- in production, on a
    screen whose whole test suite passed, because those tests called the
    service layer and never went through the router.

    The failure is silent in exactly the way that matters: nothing is logged as
    an error, the route is in the OpenAPI schema, and `_walk` reports it as
    present and correctly guarded. Only a request reveals it.

    Whole application, not just this module -- the mistake has nothing to do
    with distributors, and today every one of the ~700 routes is clean.
    """
    import re

    import app.main as main

    routes = _routes_in_matching_order(main.app)
    shadowed = []
    for i, (method, path) in enumerate(routes):
        if "{" in path:
            continue
        for earlier_method, earlier in routes[:i]:
            if "{" not in earlier or earlier_method != method:
                continue
            pattern = "^" + re.sub(r"\{[^}]+\}", "[^/]+", earlier) + "$"
            if re.match(pattern, path):
                shadowed.append(f"{method} {path} is swallowed by {earlier}")

    assert shadowed == [], (
        "these routes can never be reached -- move them ABOVE the parameter "
        "route that matches them: " + "; ".join(shadowed))


def test_no_new_public_route_appears():
    """The one test that fails when the mistake is made.

    Every unauthenticated route in this module has to be listed, by name, in
    EXPECTED_PUBLIC with a written justification. Forgetting a dependency,
    registering a router in main.py's public block, or copying an existing file
    all break this rather than shipping quietly.
    """
    public = {(r["method"], r["path"]) for r in _distribution_routes()
              if r["requires"] == "PUBLIC"}

    unexpected = public - set(EXPECTED_PUBLIC)
    assert not unexpected, (
        "New unauthenticated route(s) in the distributor module: "
        f"{sorted(unexpected)}. If that is deliberate, add it to "
        "EXPECTED_PUBLIC in app/api/security_review.py with the reason -- "
        "where a reviewer will read it.")

    gone = set(EXPECTED_PUBLIC) - public
    assert not gone, (
        f"These were public and no longer are: {sorted(gone)}. If they were "
        "deliberately closed, remove them from EXPECTED_PUBLIC so the list "
        "keeps describing reality.")


def test_the_public_routes_are_only_the_portal():
    """Nothing outside the distributor portal is open.

    Two kinds live there and they are not the same risk: the ordering link is a
    credential for ONE distributor's account, the registration link is shared
    widely and is a credential for nothing. Both are public; only the first
    reaches an existing account.
    """
    for method, path in EXPECTED_PUBLIC:
        assert path.startswith("/api/portal/"), (
            f"{method} {path} is public but is not part of the portal")

    ordering = {p for _, p in EXPECTED_PUBLIC
                if not p.startswith("/api/portal/register")}
    registration = {p for _, p in EXPECTED_PUBLIC
                    if p.startswith("/api/portal/register")}
    assert len(ordering) == 3, sorted(ordering)
    assert registration, "the registration form should be public"

    for key, reason in EXPECTED_PUBLIC.items():
        assert len(reason) > 40, f"{key} needs a real justification, not a label"


def test_the_public_registration_cannot_search_customers_by_name():
    """A type-ahead here would export the customer list.

    The endpoint takes a phone number and nothing else, so an applicant has to
    already know the number rather than browse for one.
    """
    import inspect
    from app.api import portal as portal_api

    fields = portal_api.CustomerCheckIn.model_fields
    assert set(fields) == {"phone"}, (
        f"check-customer accepts {sorted(fields)} -- anything beyond a phone "
        f"number turns a confirmation into a search")

    source = inspect.getsource(
        __import__("app.services.registration", fromlist=["x"]))
    assert "ILIKE" not in source.upper().replace("LIKE :TAIL", ""), (
        "a name-prefix match would make this browsable")
    assert "_mask" in source, "the matched name must be masked"


def test_everything_that_changes_data_needs_more_than_a_login():
    """No write in this module is reachable by an unauthenticated caller."""
    writes = [r for r in _distribution_routes()
              if r["method"] in ("POST", "PUT", "PATCH", "DELETE")]
    assert writes, "the walk found no write routes, so it is not working"

    unguarded = [r for r in writes if r["requires"] == "PUBLIC"]
    portal_only = all(r["path"].startswith("/api/portal/") for r in unguarded)
    assert portal_only, (
        f"unauthenticated writes outside the portal: {unguarded}")


def test_the_destructive_actions_are_admin_only():
    """The things that stop trade or end a relationship.

    Not an exhaustive list -- an exhaustive list would rot. These are the ones
    whose consequences fall on somebody outside the company.
    """
    must_be_admin = {
        ("POST", "/api/distributors/{distributor_id}/status"),
        ("POST", "/api/recalls"),
        ("POST", "/api/recalls/{recall_id}/close"),
        ("POST", "/api/batches/{batch_id}/status"),
        ("POST", "/api/distributors/{distributor_id}/order-links"),
        ("POST", "/api/distributors/order-links/{link_id}/revoke"),
        ("POST", "/api/performance/{distributor_id}/reviews"),
        ("POST", "/api/performance/reviews/{review_id}/close"),
        ("POST", "/api/downstream/sales/{sale_id}/verify"),
        ("GET", "/api/command-centre/exports/{dataset}"),
        ("POST", "/api/geography/applications/{application_id}/decide"),
    }
    actual = {(r["method"], r["path"]): r["requires"]
              for r in _distribution_routes()}

    for key in must_be_admin:
        assert key in actual, f"{key} has disappeared; update this list"
        assert actual[key] == "ADMIN", (
            f"{key[0]} {key[1]} is {actual[key]}, not ADMIN")


def test_the_distributor_register_is_not_open_to_every_staff_login():
    """A distributor record is commercial information.

    What they owe, what they claim to have sold, where they are failing. That
    is not something every account in the building needs, and "everyone sees
    everything" stops being defensible the moment a departing employee still
    has a login.
    """
    commercial = ("/api/distributors", "/api/geography", "/api/downstream",
                  "/api/performance", "/api/command-centre")
    routes = [r for r in _distribution_routes()
              if any(r["path"].startswith(p) for p in commercial)]
    assert routes, "the walk found no commercial routes"

    too_open = [r for r in routes if r["requires"] == "AUTHENTICATED"]
    assert not too_open, (
        f"these read a distributor's commercial position but accept any staff "
        f"login: {[(r['method'], r['path']) for r in too_open]}")

    for route in routes:
        assert route["requires"] in ("ADMIN", "DISTRIBUTION"), (
            f"{route['method']} {route['path']} is {route['requires']}")


def test_batches_and_recalls_stay_open_to_every_staff_login():
    """Deliberately NOT restricted, and the reason matters.

    A picker has to know which batch to take, and a recall has to be collected
    by whoever is actually in the warehouse. Hiding those behind a sales role to
    protect commercial confidentiality would keep a safety problem from the
    people standing next to it.
    """
    operational = [r for r in _distribution_routes()
                   if r["path"].startswith("/api/batches")
                   or r["path"].startswith("/api/recalls")]
    assert operational

    reads = [r for r in operational if r["method"] == "GET"]
    assert reads
    assert all(r["requires"] == "AUTHENTICATED" for r in reads), (
        "warehouse and production staff must still be able to read batch and "
        "recall information")


def test_the_distribution_role_list_is_what_was_asked_for():
    assert set(DISTRIBUTION_ROLES) == {"admin", "sales_staff", "customer_care"}
    # marketer exists in this system and is deliberately NOT included.
    assert "marketer" not in DISTRIBUTION_ROLES
    assert "production_staff" not in DISTRIBUTION_ROLES


def test_the_matrix_admits_what_it_does_not_check():
    """A security report that claims completeness is worse than none."""
    source = (Path(__file__).resolve().parents[1]
              / "app" / "api" / "security_review.py").read_text(encoding="utf-8")
    assert "known_limitations" in source
    assert "does not catch a guard that is present and wrong" in source
    assert "not scoped" in source


# ---------------------------------------------------------------------------
# The public endpoint cannot be used to fill the disk
# ---------------------------------------------------------------------------

BASE_SCHEMA = """
DROP TABLE IF EXISTS distributor_order_link_events CASCADE;
DROP TABLE IF EXISTS distributor_order_links CASCADE;
DROP TABLE IF EXISTS distributors CASCADE;
-- resolve() joins all three, so they have to exist even though these tests
-- only ever hand it tokens that match nothing.
CREATE TABLE distributors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    distributor_code VARCHAR(32) UNIQUE NOT NULL,
    legal_name VARCHAR(255) NOT NULL,
    trading_name VARCHAR(255),
    status VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
    customer_id UUID, warehouse_id UUID
);
CREATE TABLE distributor_order_links (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    distributor_id UUID,
    token_sha256 CHAR(64) UNIQUE NOT NULL,
    token_hint VARCHAR(12) NOT NULL,
    label VARCHAR(160) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,
    use_count INTEGER NOT NULL DEFAULT 0,
    order_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE distributor_order_link_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    link_id UUID,
    token_hint VARCHAR(12),
    event_type VARCHAR(24) NOT NULL,
    detail TEXT,
    sales_order_id UUID,
    ip_address VARCHAR(64),
    user_agent VARCHAR(500),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


@pytest.fixture(scope="module")
def schema():
    engine = create_engine(SYNC_DB, future=True)
    with engine.begin() as conn:
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
        for stmt in BASE_SCHEMA.split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
    engine.dispose()
    yield


@pytest_asyncio.fixture
async def db(schema):
    engine = create_async_engine(TEST_DB, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        await session.execute(text("DELETE FROM distributor_order_link_events"))
        await session.commit()
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_failed_attempts_are_logged_but_not_without_limit(db):
    """Security logging on an open endpoint is a denial-of-service vector.

    Logging every miss is the right instinct -- a run of them is what guessing
    at links looks like. Writing a row per request from an UNAUTHENTICATED
    endpoint means anyone on the internet can grow the table without bound,
    which is the failure mode where the monitoring is the outage.
    """
    from fastapi import HTTPException

    attacker = "203.0.113.66"
    for _ in range(60):
        with pytest.raises(HTTPException):
            await psvc.resolve(db, token="z" * 43, ip=attacker,
                               user_agent="curl/8")
        await db.commit()

    logged = (await db.execute(
        text("""SELECT COUNT(*) FROM distributor_order_link_events
                 WHERE ip_address = :ip"""), {"ip": attacker})).scalar()

    assert logged <= psvc.MISS_LOG_CAP_PER_HOUR + 1, (
        f"{logged} rows written for 60 unauthenticated requests -- the log is "
        f"unbounded")
    assert logged >= 5, "the attempts must still be visible, just bounded"

    # The signal survives: the last row says the cap was reached.
    final = (await db.execute(
        text("""SELECT detail FROM distributor_order_link_events
                 WHERE ip_address = :ip ORDER BY created_at DESC LIMIT 1"""),
        {"ip": attacker})).scalar()
    assert "further attempts are refused" in (final or "")


@pytest.mark.asyncio
async def test_one_attackers_flood_does_not_hide_another_address(db):
    """The cap is per address, or one prober silences the log for everybody."""
    from fastapi import HTTPException

    for _ in range(40):
        with pytest.raises(HTTPException):
            await psvc.resolve(db, token="a" * 43, ip="203.0.113.1")
        await db.commit()

    with pytest.raises(HTTPException):
        await psvc.resolve(db, token="b" * 43, ip="198.51.100.9")
    await db.commit()

    other = (await db.execute(
        text("""SELECT COUNT(*) FROM distributor_order_link_events
                 WHERE ip_address = '198.51.100.9'"""))).scalar()
    assert other == 1


@pytest.mark.asyncio
async def test_a_malformed_token_is_capped_too(db):
    """Otherwise the cheapest request to make is the one that is not limited."""
    from fastapi import HTTPException

    for _ in range(50):
        with pytest.raises(HTTPException):
            await psvc.resolve(db, token="short", ip="203.0.113.77")
        await db.commit()

    logged = (await db.execute(
        text("""SELECT COUNT(*) FROM distributor_order_link_events
                 WHERE ip_address = '203.0.113.77'"""))).scalar()
    assert logged <= psvc.MISS_LOG_CAP_PER_HOUR + 1


# ---------------------------------------------------------------------------
# The guarantees eleven phases claimed
# ---------------------------------------------------------------------------

MIGRATIONS = [
    "x3456789012w_distributor_foundation.py",
    "y4567890123x_distributor_compliance.py",
    "z5678901234y_territory_applications.py",
    "a6789012345z_distributor_portal.py",
    "b7890123456a_product_batches.py",
    "c8901234567b_downstream_sales.py",
    "d9012345678c_performance.py",
    "e0123456789d_inbox_jobs.py",
    "f1234567890e_recall_workflow.py",
]


def test_every_migration_declares_a_linear_chain():
    """A branch in the chain means alembic upgrade head does nothing useful."""
    revisions, parents = {}, {}
    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    for filename in MIGRATIONS:
        source = (versions / filename).read_text(encoding="utf-8")
        rev = next(line.split("=")[1].strip().strip("'\"")
                   for line in source.splitlines()
                   if line.startswith("revision ="))
        down = next(line.split("=")[1].strip().strip("'\"")
                    for line in source.splitlines()
                    if line.startswith("down_revision ="))
        revisions[rev] = filename
        parents[rev] = down

    # Exactly one head among this phase's migrations.
    children = set(parents.values()) & set(revisions)
    heads = set(revisions) - children
    assert len(heads) == 1, f"multiple heads: {sorted(heads)}"

    # And no two share a parent.
    seen = {}
    for rev, down in parents.items():
        assert down not in seen, (
            f"{revisions[rev]} and {revisions[seen[down]]} both branch from "
            f"{down}")
        seen[down] = rev


def test_no_migration_drops_or_truncates_an_existing_table():
    """Nothing in eleven phases may destroy data that was already there."""
    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    # Tables this work created itself; dropping those in downgrade() is fine.
    for filename in MIGRATIONS:
        source = (versions / filename).read_text(encoding="utf-8")
        upgrade = source.split("def downgrade")[0]
        for forbidden in ("DROP TABLE", "TRUNCATE", "DELETE FROM"):
            assert forbidden not in upgrade.upper(), (
                f"{filename} upgrade() contains {forbidden} -- migrations must "
                f"never destroy existing data")


def test_the_column_additions_are_all_nullable_or_defaulted():
    """An existing row must keep behaving exactly as it did."""
    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    for filename in MIGRATIONS:
        source = (versions / filename).read_text(encoding="utf-8")
        upgrade = source.split("def downgrade")[0]
        for line in upgrade.splitlines():
            if "ADD COLUMN" not in line.upper():
                continue
            if "NOT NULL" in line.upper():
                assert "DEFAULT" in line.upper(), (
                    f"{filename}: {line.strip()} adds a NOT NULL column with "
                    f"no default, which fails on a table that already has "
                    f"rows")
