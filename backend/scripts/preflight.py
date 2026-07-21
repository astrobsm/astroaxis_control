#!/usr/bin/env python
"""Pre-flight check before deploying the remediation work to production.

Runs three things that were previously manual:

  1. RECONCILIATION AUDIT  -- read-only against production. Reports revenue
     that no profit report has ever seen, and caches that disagree with the
     payment rows.
  2. MIGRATION REHEARSAL   -- clones production into a scratch database and
     runs the migrations there, reporting exactly what they would change.
     Production is never written to.
  3. TEST SUITE            -- runs the invariant tests against the scratch db.

Usage
-----
    # read-only audit of production, nothing is written anywhere
    python scripts/preflight.py --audit-only

    # full rehearsal: clone prod -> migrate the clone -> report -> test
    python scripts/preflight.py --rehearse

    # after rehearsal looks right, apply for real (asks for confirmation)
    python scripts/preflight.py --apply

Environment
-----------
    DATABASE_URL       production (or staging) connection string
    SCRATCH_DB_URL     optional; where the rehearsal clone is built.
                       Defaults to the same server, database `astro_rehearsal`.

The rehearsal DROPS AND RECREATES the scratch database. It refuses to run if
the scratch URL and DATABASE_URL point at the same database.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from decimal import Decimal
from urllib.parse import urlparse, urlunparse

# Allow running as `python scripts/preflight.py` from backend/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# Last revision that existed BEFORE this remediation work. A database built
# outside Alembic is stamped here so only the new migrations run.
PRE_REMEDIATION_HEAD = "j9012345678i"

C = {
    "hdr": "\033[1;36m", "ok": "\033[0;32m", "warn": "\033[0;33m",
    "err": "\033[0;31m", "dim": "\033[2m", "off": "\033[0m",
}
if os.name == "nt" and not os.getenv("ANSICON"):
    try:                       # enable ANSI on modern Windows terminals
        import ctypes
        ctypes.windll.kernel32.SetConsoleMode(
            ctypes.windll.kernel32.GetStdHandle(-11), 7)
    except Exception:
        C = {k: "" for k in C}


def say(kind, msg):
    print(f"{C[kind]}{msg}{C['off']}")


def header(msg):
    print()
    say("hdr", f"{'=' * 70}\n{msg}\n{'=' * 70}")


def _sync_url(url: str) -> str:
    return url.replace("+asyncpg", "")


def _db_name(url: str) -> str:
    return urlparse(_sync_url(url)).path.lstrip("/")


def _with_db(url: str, dbname: str) -> str:
    p = urlparse(_sync_url(url))
    return urlunparse(p._replace(path=f"/{dbname}"))


# ---------------------------------------------------------------------------
# 1. Reconciliation audit (READ ONLY)
# ---------------------------------------------------------------------------

async def run_audit(database_url: str) -> dict:
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from app.services.receivables import reconciliation_report

    async_url = database_url if "+asyncpg" in database_url else \
        database_url.replace("postgresql://", "postgresql+asyncpg://")
    eng = create_async_engine(async_url, future=True)
    maker = sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as s:
            return await reconciliation_report(s)
    finally:
        await eng.dispose()


def print_audit(report: dict) -> bool:
    """Returns True if clean."""
    header("1. RECONCILIATION AUDIT (read-only)")

    drifted = report["drifted_count"]
    orphans = report["orphan_count"]
    unrecognised = report["unrecognised_revenue"]

    if drifted == 0 and orphans == 0:
        say("ok", "  All invoices reconcile with their payment rows.")
        return True

    if orphans:
        say("warn",
            f"  {orphans} order(s) flagged paid/partial with NO invoice.")
        say("warn",
            f"  N{unrecognised:,.2f} of revenue that no profit report has "
            f"ever seen.")
        print(f"{C['dim']}  These were settled through the old mark-paid path, "
              f"which wrote\n  no Payment row. Decide whether to recognise "
              f"them at their original\n  order date or as an opening balance "
              f"at cutover.{C['off']}")
        for r in report["orders_flagged_paid_without_invoice"][:10]:
            print(f"      {r['order_number']:<22} N{r['total_amount']:>14,.2f}"
                  f"  {r['payment_status']}")
        if orphans > 10:
            print(f"      ... and {orphans - 10} more")

    if drifted:
        print()
        say("warn",
            f"  {drifted} invoice(s) whose cached paid_amount disagrees with "
            f"their payments.")
        for r in report["invoices_with_drifted_cache"][:10]:
            print(f"      {r['invoice_number']:<22} "
                  f"cached N{r['cached_paid_amount']:>12,.2f}  "
                  f"actual N{r['actual_payments_sum']:>12,.2f}  "
                  f"diff N{r['difference']:>12,.2f}")
        if drifted > 10:
            print(f"      ... and {drifted - 10} more")
        print(f"{C['dim']}  Deploying the remediation recomputes these from "
              f"the payment rows.{C['off']}")

    return False


# ---------------------------------------------------------------------------
# 2. Migration rehearsal (clone -> migrate the clone)
# ---------------------------------------------------------------------------

def _psql_env(url: str) -> dict:
    p = urlparse(_sync_url(url))
    env = dict(os.environ)
    if p.password:
        env["PGPASSWORD"] = p.password
    return env


def _pg_bin(name: str) -> str:
    """Locate a postgres binary, falling back to a default Windows install."""
    from shutil import which
    found = which(name)
    if found:
        return found
    for base in (r"C:\Program Files\PostgreSQL",):
        if os.path.isdir(base):
            for ver in sorted(os.listdir(base), reverse=True):
                cand = os.path.join(base, ver, "bin", f"{name}.exe")
                if os.path.exists(cand):
                    return cand
    return name  # let it fail loudly with a clear message


def clone_database(source_url: str, scratch_url: str) -> None:
    src_db, dst_db = _db_name(source_url), _db_name(scratch_url)
    if _sync_url(source_url) == _sync_url(scratch_url) or src_db == dst_db:
        raise SystemExit(
            "Refusing to run: scratch database is the same as DATABASE_URL.")

    p = urlparse(_sync_url(source_url))
    admin = _with_db(scratch_url, "postgres")
    env = _psql_env(scratch_url)

    say("dim", f"  dropping and recreating scratch database `{dst_db}` ...")
    for sql in (f'DROP DATABASE IF EXISTS "{dst_db}"',
                f'CREATE DATABASE "{dst_db}"'):
        subprocess.run([_pg_bin("psql"), admin, "-v", "ON_ERROR_STOP=1",
                        "-c", sql],
                       check=True, env=env, stdout=subprocess.DEVNULL)

    say("dim", f"  cloning `{src_db}` -> `{dst_db}` (this may take a while) ...")
    dump = subprocess.Popen(
        [_pg_bin("pg_dump"), _sync_url(source_url), "--no-owner", "--no-acl"],
        stdout=subprocess.PIPE, env=_psql_env(source_url))
    restore = subprocess.Popen(
        [_pg_bin("psql"), _sync_url(scratch_url), "-q", "-o", os.devnull],
        stdin=dump.stdout, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    dump.stdout.close()
    _, err = restore.communicate()
    if restore.returncode != 0:
        raise SystemExit(f"clone failed:\n{err.decode(errors='replace')[:2000]}")
    say("ok", f"  clone ready: {dst_db}")


async def snapshot_counts(url: str) -> dict:
    """Figures that must not change unexpectedly across the migration."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text

    async_url = url if "+asyncpg" in url else \
        url.replace("postgresql://", "postgresql+asyncpg://")
    eng = create_async_engine(async_url, future=True)
    out = {}
    queries = {
        "stock_levels rows": "SELECT COUNT(*) FROM stock_levels",
        "stock_movements rows": "SELECT COUNT(*) FROM stock_movements",
        "total stock on hand": "SELECT COALESCE(SUM(current_stock),0) FROM stock_levels",
        "sales_order_lines": "SELECT COUNT(*) FROM sales_order_lines",
        "payments total": "SELECT COALESCE(SUM(amount),0) FROM payments",
        "invoices": "SELECT COUNT(*) FROM invoices",
    }
    try:
        async with eng.connect() as c:
            for label, q in queries.items():
                try:
                    out[label] = (await c.execute(text(q))).scalar()
                except Exception:
                    out[label] = None
    finally:
        await eng.dispose()
    return out


async def check_alembic_stamp(url: str) -> str | None:
    """Return the recorded revision, or None if the db was never stamped.

    A database built by `Base.metadata.create_all` (or restored from a dump of
    one) has no alembic_version row, so `alembic upgrade head` would try to
    replay the INITIAL migration and fail with "relation already exists". That
    failure looks alarming and is easy to misread as data corruption, so
    detect it up front and say what it actually means.
    """
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text
    async_url = url if "+asyncpg" in url else \
        url.replace("postgresql://", "postgresql+asyncpg://")
    eng = create_async_engine(async_url, future=True)
    try:
        async with eng.connect() as c:
            exists = (await c.execute(text(
                "SELECT to_regclass('public.alembic_version')"))).scalar()
            if not exists:
                return None
            return (await c.execute(text(
                "SELECT version_num FROM alembic_version LIMIT 1"))).scalar()
    finally:
        await eng.dispose()


def stamp_alembic(url: str, revision: str) -> None:
    env = dict(os.environ)
    env["DATABASE_URL"] = url if "+asyncpg" in url else \
        url.replace("postgresql://", "postgresql+asyncpg://")
    env.setdefault("SECRET_KEY", "preflight-not-a-real-secret")
    r = subprocess.run([sys.executable, "-m", "alembic", "stamp", revision],
                       env=env, capture_output=True, text=True)
    if r.returncode != 0:
        say("err", f"  could not stamp revision {revision}:")
        print(r.stdout[-1500:], r.stderr[-1500:])
        raise SystemExit(1)
    say("ok", f"  clone stamped at {revision}")


def run_alembic(url: str) -> None:
    env = dict(os.environ)
    env["DATABASE_URL"] = url if "+asyncpg" in url else \
        url.replace("postgresql://", "postgresql+asyncpg://")
    env.setdefault("SECRET_KEY", "preflight-not-a-real-secret")
    r = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                       env=env, capture_output=True, text=True)
    if r.returncode != 0:
        say("err", "  MIGRATION FAILED on the clone:")
        print(r.stdout[-3000:])
        print(r.stderr[-3000:])
        raise SystemExit(1)
    say("ok", "  migrations applied to the clone successfully")


def print_delta(before: dict, after: dict) -> None:
    header("Migration effect (measured on the clone)")
    print(f"  {'metric':<26}{'before':>18}{'after':>18}")
    print(f"  {'-'*62}")
    for k in before:
        b, a = before.get(k), after.get(k)
        fb = "n/a" if b is None else f"{Decimal(str(b)):,.2f}" if isinstance(b, Decimal) else f"{b:,}"
        fa = "n/a" if a is None else f"{Decimal(str(a)):,.2f}" if isinstance(a, Decimal) else f"{a:,}"
        changed = (b != a)
        colour = C["warn"] if changed else ""
        print(f"  {colour}{k:<26}{fb:>18}{fa:>18}{C['off']}")
    print(f"\n{C['dim']}  How to read this:\n"
          f"  * stock_levels row count SHOULD fall -- duplicate balance rows "
          f"are merged\n    into one, and unrepairable rows are moved to "
          f"quarantine.\n"
          f"  * total stock on hand falls by exactly the quantity held on "
          f"QUARANTINED\n    rows (those referencing neither a product nor a "
          f"raw material, which no\n    report could attribute anyway). "
          f"Merging itself sums and loses nothing.\n"
          f"    Cross-check that drop against the quarantine table below "
          f"before go-live.\n"
          f"  * payments total and invoice count MUST NOT change. If they do, "
          f"stop.{C['off']}")


async def report_quarantine(url: str) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text
    async_url = url if "+asyncpg" in url else \
        url.replace("postgresql://", "postgresql+asyncpg://")
    eng = create_async_engine(async_url, future=True)
    try:
        async with eng.connect() as c:
            for tbl in ("stock_levels_quarantine", "stock_movements_quarantine"):
                try:
                    n = (await c.execute(
                        text(f"SELECT COUNT(*) FROM {tbl}"))).scalar()
                except Exception:
                    continue
                if n:
                    say("warn", f"  {n} row(s) quarantined in {tbl} "
                                f"-- review before go-live")
                else:
                    say("ok", f"  nothing quarantined in {tbl}")

            try:
                n = (await c.execute(text(
                    "SELECT COUNT(*) FROM sales_order_lines "
                    "WHERE cost_source = 'unknown'"))).scalar()
                est = (await c.execute(text(
                    "SELECT COUNT(*) FROM sales_order_lines "
                    "WHERE cost_source = 'backfill_estimate'"))).scalar()
                if n:
                    say("warn", f"  {n} sale line(s) have NO determinable cost "
                                f"(cost_source='unknown')")
                if est:
                    say("warn", f"  {est} sale line(s) costed by BACKFILL "
                                f"ESTIMATE -- not audited figures")
            except Exception:
                pass
    finally:
        await eng.dispose()


def run_tests(scratch_url: str) -> bool:
    header("3. INVARIANT TEST SUITE (against the clone)")
    env = dict(os.environ)
    env["TEST_DATABASE_URL"] = scratch_url if "+asyncpg" in scratch_url else \
        scratch_url.replace("postgresql://", "postgresql+asyncpg://")
    env["DATABASE_URL"] = env["TEST_DATABASE_URL"]
    env.setdefault("SECRET_KEY", "preflight-not-a-real-secret")
    say("dim", "  note: these tests create and drop their own tables in the "
               "scratch db")
    r = subprocess.run(
        [sys.executable, "-m", "pytest",
         "tests/test_inventory_ledger.py", "tests/test_stock_migration.py",
         "tests/test_receivables.py", "tests/test_costing.py",
         "tests/test_cogs_migration.py", "tests/test_ledger.py",
         "tests/test_posting.py", "tests/test_posting_gate.py",
         "tests/test_payables.py", "tests/test_ap_migration.py",
         "tests/test_payroll.py", "tests/test_assets.py", "tests/test_budgeting.py",
         "-q"],
        env=env, capture_output=True, text=True)
    print(r.stdout[-2500:])
    if r.returncode != 0:
        say("err", "  TESTS FAILED -- do not deploy")
        print(r.stderr[-2000:])
        return False
    say("ok", "  all invariant tests passed")
    return True


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--audit-only", action="store_true",
                   help="read-only reconciliation audit of DATABASE_URL")
    g.add_argument("--rehearse", action="store_true",
                   help="clone the database and migrate the clone")
    g.add_argument("--apply", action="store_true",
                   help="run migrations against DATABASE_URL for real")
    args = ap.parse_args()

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is not set.")

    say("hdr", f"\nASTROBSM pre-flight -- target database: "
               f"{_db_name(database_url)}")

    report = asyncio.run(run_audit(database_url))
    clean = print_audit(report)

    if args.audit_only:
        header("RESULT")
        if clean:
            say("ok", "  Reconciliation is clean. Safe to rehearse the "
                      "migrations.")
        else:
            say("warn", "  Reconciliation found historical drift (see above).")
            say("warn", "  This does NOT block deployment -- the remediation "
                        "repairs most of it --")
            say("warn", "  but decide how to treat orphaned revenue before "
                        "the first ledger close.")
        return

    if args.rehearse:
        scratch = os.getenv("SCRATCH_DB_URL") or _with_db(
            database_url, "astro_rehearsal")
        header("2. MIGRATION REHEARSAL (production is not written to)")
        clone_database(database_url, scratch)

        stamp = asyncio.run(check_alembic_stamp(scratch))
        if stamp is None:
            say("warn", "  This database has no alembic_version row, so "
                        "Alembic does not know")
            say("warn", "  which migrations it already has. Left alone, "
                        "`upgrade head` would try")
            say("warn", "  to replay the INITIAL migration and fail with "
                        "'relation already exists'.")
            print(f"{C['dim']}  Stamping the CLONE at the last "
                  f"pre-remediation revision so only the\n  new migrations "
                  f"run. Production must be stamped the same way before\n"
                  f"  --apply. This does not alter any data.{C['off']}")
            stamp_alembic(scratch, PRE_REMEDIATION_HEAD)
        else:
            say("ok", f"  clone is stamped at revision {stamp}")

        before = asyncio.run(snapshot_counts(scratch))
        run_alembic(scratch)
        after = asyncio.run(snapshot_counts(scratch))
        print_delta(before, after)
        header("Data-quality findings on the migrated clone")
        asyncio.run(report_quarantine(scratch))
        ok = run_tests(scratch)
        header("RESULT")
        if ok:
            say("ok", "  Rehearsal succeeded. Review the deltas above, then "
                      "run --apply.")
            say("dim", f"  The clone `{_db_name(scratch)}` is left in place "
                       f"for inspection.")
        else:
            say("err", "  Rehearsal FAILED. Do not deploy.")
            sys.exit(1)
        return

    if args.apply:
        header("APPLYING MIGRATIONS TO " + _db_name(database_url).upper())
        say("warn", "  This writes to the target database.")
        say("warn", "  Confirm you have a restorable backup AND have run "
                    "--rehearse first.")
        if input("  Type the database name to proceed: ").strip() != \
                _db_name(database_url):
            say("err", "  Name did not match. Aborted; nothing was changed.")
            sys.exit(1)

        stamp = asyncio.run(check_alembic_stamp(database_url))
        if stamp is None:
            say("err", "  This database has no alembic_version row.")
            say("err", "  Stamp it first so Alembic does not replay the "
                       "initial migration:")
            say("err", f"    python -m alembic stamp {PRE_REMEDIATION_HEAD}")
            sys.exit(1)
        say("ok", f"  target is stamped at revision {stamp}")

        run_alembic(database_url)
        header("Post-migration data-quality findings")
        asyncio.run(report_quarantine(database_url))
        say("ok", "\n  Migrations applied. Deploy the application code now "
                  "(frontend + backend together).")


if __name__ == "__main__":
    main()
