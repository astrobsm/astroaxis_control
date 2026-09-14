"""Refuse to run the test suite against a database that is not a test database.

WHY THIS EXISTS
---------------
`backend/.env` sets DATABASE_URL to the LIVE DigitalOcean database. Three test
modules -- test_wifi.py, test_crud_apis.py, test_bom_cost.py -- do not read
TEST_DATABASE_URL at all. They do:

    from app import db as db_mod
    engine = db_mod.engine

which is the application engine, built from that same DATABASE_URL. Their
fixtures then run `Base.metadata.create_all(engine)` and INSERT user rows.

So `cd backend && pytest tests/` aimed schema creation and inserts at
production. Nothing has been damaged only because those connections happen to
fail from this machine -- that is luck, not design, and it would stop being
true the moment the network path worked.

The rest of the suite is already safe: it reads TEST_DATABASE_URL and skips
when it is unset.

WHAT THIS DOES
--------------
Fails the session immediately -- before any fixture opens a connection -- if
DATABASE_URL points somewhere that is not plainly a test target. A host on
localhost, or a database whose name contains "test", is allowed. Anything else
stops the run with an explanation.

If you genuinely mean to run against a remote database, set
ALLOW_TESTS_AGAINST_DATABASE_URL=1 for that invocation. It is deliberately
awkward: nobody should reach for it by habit.

Reads the environment directly rather than importing app.db, so this check has
no side effects and works even when DATABASE_URL is unset (app.db raises on
import in that case).
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

import pytest
import pytest_asyncio

_SAFE_HOSTS = {"localhost", "127.0.0.1", "::1", "db", "postgres", ""}


def _looks_like_a_test_database(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    name = (parsed.path or "").lstrip("/").split("?")[0].lower()
    if host in _SAFE_HOSTS:
        return True
    # A remote host is acceptable only if the database is named as a test one.
    return "test" in name or "scratch" in name or "rehearsal" in name


def _resolve_database_url() -> str:
    """The URL the app WILL use, resolved the same way app.db resolves it.

    app.db calls load_dotenv() at import time, which happens during collection
    -- after this hook. Reading os.environ alone therefore sees nothing and the
    guard would wave production through, which is the one case it exists to
    catch. Load backend/.env here first, without overriding anything already
    exported in the shell (dotenv's own precedence).
    """
    env = os.getenv("DATABASE_URL", "")
    if env:
        return env
    try:
        from dotenv import dotenv_values
    except ImportError:
        return ""
    backend_env = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    return (dotenv_values(backend_env) or {}).get("DATABASE_URL", "") or ""


def pytest_sessionstart(session):
    if os.getenv("ALLOW_TESTS_AGAINST_DATABASE_URL") == "1":
        return

    url = _resolve_database_url()
    if not url or _looks_like_a_test_database(url):
        return

    parsed = urlparse(url)
    host = parsed.hostname or "?"
    name = (parsed.path or "").lstrip("/").split("?")[0] or "?"

    raise pytest.UsageError(
        "\n"
        "==============================================================\n"
        " REFUSING TO RUN: DATABASE_URL is not a test database.\n"
        "==============================================================\n"
        f"  host : {host}\n"
        f"  db   : {name}\n"
        "\n"
        "  Some test modules (test_wifi, test_crud_apis, test_bom_cost) use\n"
        "  the APPLICATION engine from app.db, which is built from\n"
        "  DATABASE_URL. Their fixtures run Base.metadata.create_all() and\n"
        "  insert rows, so running them here would write to that database.\n"
        "\n"
        "  Point DATABASE_URL at a throwaway database for the run, e.g.\n"
        "\n"
        "    docker run -d --name astro_test_db -e POSTGRES_PASSWORD=testpw \\\n"
        "      -e POSTGRES_DB=astro_test -p 55432:5432 postgres:16-alpine\n"
        "\n"
        "    DATABASE_URL='postgresql+asyncpg://postgres:testpw@127.0.0.1:55432/astro_test' \\\n"
        "    TEST_DATABASE_URL='postgresql+asyncpg://postgres:testpw@127.0.0.1:55432/astro_test' \\\n"
        "    pytest tests/\n"
        "\n"
        "  If you really intend to use the current DATABASE_URL, re-run with\n"
        "  ALLOW_TESTS_AGAINST_DATABASE_URL=1.\n"
    )


@pytest_asyncio.fixture(autouse=True)
async def _dispose_app_engine():
    """Drop the application engine's pooled connections after every test.

    `app.db.engine` is created once at import. pytest-asyncio runs each test on
    a FRESH event loop, so any asyncpg connection left in the pool by one test
    belongs to a loop that is closed by the time the next test borrows it. The
    result is a cascade of

        sqlalchemy.exc.InterfaceError: connection is closed

    across every test after the first -- which reads like a dozen unrelated
    failures rather than one lifecycle bug. Disposing between tests means each
    one opens connections on its own loop.

    Only affects the integration tests that use the app engine (test_wifi,
    test_crud_apis, test_bom_cost). The rest build their own engine from
    TEST_DATABASE_URL and are unaffected; disposing an already-idle pool is a
    no-op.
    """
    yield
    try:
        from app import db as db_mod
    except Exception:
        return
    await db_mod.engine.dispose()


# ---------------------------------------------------------------------------
# Reconciling tables the ORM tests share with the migration tests
# ---------------------------------------------------------------------------

def reconcile_orm_columns(sync_conn) -> list[str]:
    """Add any ORM column missing from a table that already exists.

    WHY THIS IS NEEDED
    ------------------
    `Base.metadata.create_all` creates tables that are absent and does NOTHING
    to tables that are present -- including when the present table is the wrong
    shape. It never adds a missing column.

    The suite shares one database between two kinds of test. The migration
    tests (test_distributors, test_wallet, test_settlement and friends) build
    core tables like `products` and `users` by hand, each declaring only the
    columns that module needs. The ORM tests (test_crud_apis, test_bom_cost,
    test_wifi) then call create_all, find those tables already there, and are
    handed a `products` with no `description` column.

    The result was five failures that depended entirely on collection order and
    passed in isolation -- the worst kind, because the tests look flaky rather
    than wrong and the real fault is invisible in the failure message.

    Rewriting every hand-built schema to match the ORM was tried and is the
    wrong fix: those tables are deliberately minimal, some carry columns of
    their own, and keeping a dozen copies in step by hand is exactly the
    duplication that caused this. Reconciling once, here, fixes the class.

    Every added column is NULLABLE regardless of what the ORM says, because the
    table may already hold rows and this is only ever making a test database
    usable -- never a statement about what production should look like. The
    real schema is the migration chain.

    Server defaults and indexes are reconciled too, and both matter. A
    `created_at` added without its DEFAULT NOW() is silently NULL, which fails
    later in response validation rather than at the insert. And create_all skips
    a table's indexes along with the table, so the partial unique indexes on
    `stock_levels` went missing -- turning an ON CONFLICT upsert into
    "no unique or exclusion constraint matching the ON CONFLICT specification",
    an error that says nothing about the real cause.
    """
    import sqlalchemy as sa
    from sqlalchemy.schema import CreateIndex
    from app.models import Base

    inspector = sa.inspect(sync_conn)
    existing_tables = set(inspector.get_table_names())
    added: list[str] = []

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue  # create_all will make it, correctly and in full

        present = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present:
                continue
            ddl = column.type.compile(dialect=sync_conn.dialect)
            default = ""
            if column.server_default is not None:
                arg = column.server_default.arg
                default = f" DEFAULT {getattr(arg, 'text', None) or arg}"
            sync_conn.exec_driver_sql(
                f'ALTER TABLE "{table.name}" '
                f'ADD COLUMN IF NOT EXISTS "{column.name}" {ddl}{default}')
            added.append(f"{table.name}.{column.name}")

        existing_indexes = {i["name"] for i in inspector.get_indexes(table.name)}
        existing_indexes |= {
            c["name"] for c in inspector.get_unique_constraints(table.name)}
        for index in table.indexes:
            if index.name in existing_indexes:
                continue
            try:
                sync_conn.execute(CreateIndex(index, if_not_exists=True))
            except Exception:
                # An index over a column this table does not have is not worth
                # failing the run for; the column reconciliation above is what
                # most tests actually need.
                continue
            added.append(f"{table.name}::{index.name}")

    return added


async def ensure_orm_schema(engine) -> None:
    """create_all, then reconcile. Use this instead of create_all alone."""
    from app.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(reconcile_orm_columns)
