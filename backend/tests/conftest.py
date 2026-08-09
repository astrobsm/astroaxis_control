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
