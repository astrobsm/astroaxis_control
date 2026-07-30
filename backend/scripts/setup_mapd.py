#!/usr/bin/env python
"""Bring the payment distribution module (MAPD) live on a database.

Going live is four things, and this script does all of them so none is done by
hand against production:

  1. STATUS      -- is the schema installed, what is configured, what is not,
                    and how much money is sitting undistributed. Read-only.
  2. TEMPLATE    -- write a starter configuration file pre-filled with THIS
                    database's real products, ready to fill in.
  3. BOOTSTRAP   -- apply that file: business units, destination accounts,
                    product mappings, settlement rules. Idempotent.
  4. CUTOVER     -- decide what happens to payments taken BEFORE the module
                    existed: leave them alone, mark them out of scope, or
                    distribute them retroactively.

Everything is a DRY RUN until --commit is passed, following the same rule as
scripts/opening_balances.py: nothing writes to production because a script was
run with the wrong flag.

Usage
-----
    # what is the current state? writes nothing
    DATABASE_URL=... python scripts/setup_mapd.py --status

    # produce mapd-config.json listing every real product
    DATABASE_URL=... python scripts/setup_mapd.py --template > mapd-config.json

    # rehearse the configuration, then apply it
    DATABASE_URL=... python scripts/setup_mapd.py --bootstrap mapd-config.json
    DATABASE_URL=... python scripts/setup_mapd.py --bootstrap mapd-config.json --commit

    # payments taken before today are out of scope; record that and stop them
    # cluttering the exceptions list
    DATABASE_URL=... python scripts/setup_mapd.py --mark-historical 2026-07-27 --commit

    # OR distribute historical payments for real (posts back-dated ledger
    # entries -- read the note on --backfill before using it)
    DATABASE_URL=... python scripts/setup_mapd.py --backfill 2026-07-01 --commit

Configuration file
------------------
    {
      "business_units": [
        {"code": "WOUNDCARE", "name": "Wound Care"}
      ],
      "financial_accounts": [
        {"code": "HERA", "name": "Hera Account", "gl_account_code": "1200",
         "account_kind": "BANK", "business_unit": "WOUNDCARE",
         "bank_name": "Access Bank", "account_number": "0123456789"}
      ],
      "product_mappings": [
        {"sku": "P-HERA", "account": "HERA", "business_unit": "WOUNDCARE",
         "tax_group": "VAT"}
      ],
      "rules": [
        {"code": "HERA-SPLIT", "name": "Hera revenue share", "scope": "PRODUCT",
         "sku": "P-HERA",
         "splits": [{"account": "MFG", "percentage": 70},
                    {"account": "SALES", "percentage": 20},
                    {"account": "MKT", "percentage": 10}]}
      ]
    }

Only the sections you supply are touched. Accounts referenced by a mapping or a
rule must exist in the file or already in the database; the script refuses
rather than inventing one.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text                                   # noqa: E402
from sqlalchemy.ext.asyncio import (                          # noqa: E402
    AsyncSession, create_async_engine)
from sqlalchemy.orm import sessionmaker                       # noqa: E402

from app.services.ledger import money                         # noqa: E402

C = {
    "hdr": "\033[1;36m", "ok": "\033[0;32m", "warn": "\033[0;33m",
    "err": "\033[0;31m", "dim": "\033[2m", "off": "\033[0m",
}


def hdr(text_):
    print(f"\n{C['hdr']}{text_}{C['off']}")
    print(C["dim"] + "-" * len(text_) + C["off"])


def ok(t): print(f"  {C['ok']}OK{C['off']}    {t}")
def warn(t): print(f"  {C['warn']}WARN{C['off']}  {t}")
def err(t): print(f"  {C['err']}FAIL{C['off']}  {t}")
def info(t): print(f"        {t}")


def _session_maker():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set.")
    engine = create_async_engine(url, future=True)
    return engine, sessionmaker(engine, class_=AsyncSession,
                                expire_on_commit=False)


async def _schema_installed(s: AsyncSession) -> bool:
    return bool((await s.execute(
        text("SELECT to_regclass('public.settlements') IS NOT NULL"))).scalar())


# ---------------------------------------------------------------------------
# 1. status
# ---------------------------------------------------------------------------

async def status(s: AsyncSession) -> bool:
    """Report readiness. Returns True when the module can settle a payment."""
    hdr("Schema")
    if not await _schema_installed(s):
        err("MAPD tables are not present. Run: alembic upgrade head")
        return False
    ok("MAPD tables present")

    version = (await s.execute(
        text("SELECT version_num FROM alembic_version"))).scalar()
    info(f"alembic version_num = {version}")

    hdr("Configuration")
    units = (await s.execute(
        text("SELECT COUNT(*) FROM business_units"))).scalar()
    accounts = (await s.execute(
        text("""SELECT COUNT(*) FILTER (WHERE status = 'ACTIVE') AS active,
                       COUNT(*) AS total FROM financial_accounts"""))).first()
    rules = (await s.execute(
        text("SELECT COUNT(*) FROM settlement_rules WHERE is_active"))).scalar()

    (ok if units else warn)(f"{units} business unit(s)")
    (ok if accounts.active else err)(
        f"{accounts.active} active destination account(s) "
        f"of {accounts.total} defined")
    info(f"{rules} active settlement rule(s)")

    hdr("Product coverage")
    coverage = (await s.execute(text("""
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (
                   WHERE pa.default_financial_account_id IS NOT NULL
                      OR EXISTS (SELECT 1 FROM settlement_rules r
                                  WHERE r.product_id = p.id AND r.is_active)
               ) AS settleable
          FROM products p
          LEFT JOIN product_accounts pa ON pa.product_id = p.id
    """))).first()
    unmapped = coverage.total - coverage.settleable
    (ok if unmapped == 0 else warn)(
        f"{coverage.settleable} of {coverage.total} products can be settled")

    if unmapped:
        rows = (await s.execute(text("""
            SELECT p.sku, p.name FROM products p
              LEFT JOIN product_accounts pa ON pa.product_id = p.id
             WHERE pa.default_financial_account_id IS NULL
               AND NOT EXISTS (SELECT 1 FROM settlement_rules r
                                WHERE r.product_id = p.id AND r.is_active)
             ORDER BY p.name LIMIT 25
        """))).fetchall()
        for r in rows:
            info(f"unmapped: {r.sku or '(no sku)':<20} {r.name}")
        if unmapped > len(rows):
            info(f"... and {unmapped - len(rows)} more")

    hdr("Money")
    undistributed = (await s.execute(text("""
        SELECT COUNT(*) AS n, COALESCE(SUM(p.amount), 0) AS total
          FROM payments p
         WHERE NOT EXISTS (SELECT 1 FROM settlements s
                            WHERE s.payment_id = p.id
                              AND s.status IN ('PENDING','COMPLETED','SKIPPED'))
    """))).first()
    settled = (await s.execute(text("""
        SELECT COUNT(*) AS n, COALESCE(SUM(allocated_amount), 0) AS total
          FROM settlements WHERE status = 'COMPLETED'
    """))).first()

    info(f"{settled.n} settlement(s) completed, "
         f"{money(settled.total):,.2f} distributed")
    (ok if undistributed.n == 0 else warn)(
        f"{undistributed.n} payment(s) undistributed, "
        f"{money(undistributed.total):,.2f}")

    ready = bool(accounts.active) and coverage.settleable > 0
    hdr("Verdict")
    if ready and unmapped == 0:
        ok("Ready. Every product has a destination; set MAPD_STRICT=true.")
    elif ready:
        warn(f"Partly ready. {unmapped} product(s) still have nowhere to send "
             f"money; payments covering them will be recorded but SKIPPED.")
    else:
        err("Not ready. Define at least one active account and map a product.")
    return ready


# ---------------------------------------------------------------------------
# 2. template
# ---------------------------------------------------------------------------

async def template(s: AsyncSession) -> None:
    """Emit a starter config listing this database's real products."""
    products = (await s.execute(
        text("SELECT sku, name FROM products ORDER BY name"))).fetchall()
    gl_bank = (await s.execute(
        text("""SELECT code, name FROM gl_accounts
                 WHERE code IN ('1100','1200','1250') AND is_postable
                 ORDER BY code"""))).fetchall()

    doc = {
        "_readme": [
            "Fill in the accounts, then point each product at one.",
            "gl_account_code must be an existing postable ledger account; "
            f"cash/bank options here are: "
            f"{', '.join(f'{g.code} ({g.name})' for g in gl_bank)}.",
            "Delete the product_mappings you do not want to configure yet -- "
            "an unmapped product is recorded as SKIPPED, never guessed.",
            "account_number is encrypted at rest and only ever shown masked.",
        ],
        "business_units": [
            {"code": "WOUNDCARE", "name": "Wound Care"},
            {"code": "DRESSINGS", "name": "Dressings"},
        ],
        "financial_accounts": [
            {"code": "MAIN", "name": "Main Collections Account",
             "account_kind": "BANK", "gl_account_code": "1200",
             "business_unit": "WOUNDCARE", "bank_name": "",
             "account_number": ""},
        ],
        "product_mappings": [
            {"sku": p.sku, "product_name": p.name, "account": "MAIN",
             "business_unit": "WOUNDCARE", "tax_group": "VAT"}
            for p in products
        ],
        "rules": [],
    }
    print(json.dumps(doc, indent=2))


# ---------------------------------------------------------------------------
# 3. bootstrap
# ---------------------------------------------------------------------------

async def bootstrap(s: AsyncSession, config: dict, commit: bool) -> None:
    """Apply a configuration file. Idempotent: safe to re-run after edits."""
    if not await _schema_installed(s):
        raise SystemExit(
            "MAPD tables are not present. Run `alembic upgrade head` first.")

    created = {"units": 0, "accounts": 0, "mappings": 0, "rules": 0}
    skipped = {"units": 0, "accounts": 0, "mappings": 0, "rules": 0}
    problems: list[str] = []

    # -- business units -----------------------------------------------------
    hdr("Business units")
    for u in config.get("business_units", []):
        existing = (await s.execute(
            text("SELECT id FROM business_units WHERE code = :c"),
            {"c": u["code"]})).first()
        if existing:
            skipped["units"] += 1
            info(f"exists: {u['code']}")
            continue
        await s.execute(text("""
            INSERT INTO business_units (id, code, name, description)
            VALUES (gen_random_uuid(), :c, :n, :d)
        """), {"c": u["code"], "n": u["name"], "d": u.get("description")})
        created["units"] += 1
        ok(f"create {u['code']} -- {u['name']}")

    unit_ids = {r.code: r.id for r in (await s.execute(
        text("SELECT code, id FROM business_units"))).fetchall()}

    # -- destination accounts ----------------------------------------------
    hdr("Destination accounts")
    for a in config.get("financial_accounts", []):
        existing = (await s.execute(
            text("SELECT id FROM financial_accounts WHERE code = :c"),
            {"c": a["code"]})).first()
        if existing:
            skipped["accounts"] += 1
            info(f"exists: {a['code']}")
            continue

        gl = (await s.execute(
            text("""SELECT is_postable, is_active FROM gl_accounts
                     WHERE code = :c"""), {"c": a["gl_account_code"]})).first()
        if gl is None or not gl.is_postable or not gl.is_active:
            problems.append(
                f"account {a['code']}: ledger account "
                f"{a['gl_account_code']!r} is missing or cannot be posted to")
            continue

        kind = a.get("account_kind", "BANK")
        if kind == "OBLIGATION" and not a.get("contra_gl_account_code"):
            problems.append(
                f"account {a['code']}: an OBLIGATION account needs "
                f"contra_gl_account_code (the liability credited)")
            continue

        bu = a.get("business_unit")
        if bu and bu not in unit_ids:
            problems.append(
                f"account {a['code']}: business unit {bu!r} is not defined")
            continue

        # Imported lazily so this script runs without the encryption key set
        # when only --status or --template is used.
        from app.services.encryption import encrypt_secret
        await s.execute(text("""
            INSERT INTO financial_accounts
                (id, code, name, account_kind, gl_account_code,
                 contra_gl_account_code, bank_name, account_number_enc,
                 account_name, currency, business_unit_id, status, description)
            VALUES (gen_random_uuid(), :c, :n, :k, :gl, :contra, :bank,
                    :acctno, :acctname, :cur, :bu, 'ACTIVE', :desc)
        """), {
            "c": a["code"], "n": a["name"], "k": kind,
            "gl": a["gl_account_code"],
            "contra": a.get("contra_gl_account_code"),
            "bank": a.get("bank_name"),
            "acctno": encrypt_secret(a.get("account_number") or None),
            "acctname": a.get("account_name"),
            "cur": a.get("currency", "NGN"),
            "bu": str(unit_ids[bu]) if bu else None,
            "desc": a.get("description"),
        })
        created["accounts"] += 1
        ok(f"create {a['code']} -- {a['name']} -> ledger {a['gl_account_code']}")

    account_ids = {r.code: r.id for r in (await s.execute(
        text("SELECT code, id FROM financial_accounts"))).fetchall()}

    # -- product mappings ---------------------------------------------------
    hdr("Product mappings")
    for m in config.get("product_mappings", []):
        sku = m.get("sku")
        product = (await s.execute(
            text("SELECT id, name FROM products WHERE sku = :s"),
            {"s": sku})).first()
        if product is None:
            problems.append(f"mapping {sku!r}: no product with that SKU")
            continue

        acct = m.get("account")
        if acct and acct not in account_ids:
            problems.append(
                f"mapping {sku!r}: destination account {acct!r} is not defined")
            continue
        bu = m.get("business_unit")
        if bu and bu not in unit_ids:
            problems.append(f"mapping {sku!r}: business unit {bu!r} is not defined")
            continue

        await s.execute(text("""
            INSERT INTO product_accounts
                (id, product_id, tax_group, business_unit_id,
                 settlement_priority, default_financial_account_id, notes)
            VALUES (gen_random_uuid(), :p, :tax, :bu, :prio, :acct, :notes)
            ON CONFLICT (product_id) DO UPDATE SET
                tax_group = EXCLUDED.tax_group,
                business_unit_id = EXCLUDED.business_unit_id,
                settlement_priority = EXCLUDED.settlement_priority,
                default_financial_account_id =
                    EXCLUDED.default_financial_account_id,
                updated_at = NOW()
        """), {
            "p": str(product.id), "tax": m.get("tax_group"),
            "bu": str(unit_ids[bu]) if bu else None,
            "prio": m.get("settlement_priority", 100),
            "acct": str(account_ids[acct]) if acct else None,
            "notes": m.get("notes"),
        })
        created["mappings"] += 1
        ok(f"map {product.name} -> {acct or '(no account)'}")

    # -- rules --------------------------------------------------------------
    hdr("Settlement rules")
    for r in config.get("rules", []):
        existing = (await s.execute(
            text("SELECT id FROM settlement_rules WHERE code = :c"),
            {"c": r["code"]})).first()
        if existing:
            skipped["rules"] += 1
            info(f"exists: {r['code']}")
            continue

        scope = r.get("scope", "PRODUCT")
        product_id = business_unit_id = None
        if scope == "PRODUCT":
            product = (await s.execute(
                text("SELECT id FROM products WHERE sku = :s"),
                {"s": r.get("sku")})).first()
            if product is None:
                problems.append(f"rule {r['code']}: no product with SKU {r.get('sku')!r}")
                continue
            product_id = product.id
        elif scope == "BUSINESS_UNIT":
            bu = r.get("business_unit")
            if bu not in unit_ids:
                problems.append(f"rule {r['code']}: business unit {bu!r} is not defined")
                continue
            business_unit_id = unit_ids[bu]

        splits = r.get("splits", [])
        cash = [x for x in splits if x.get("allocation_type", "CASH") == "CASH"]
        if not cash:
            problems.append(f"rule {r['code']}: no CASH split, so money received "
                            f"would have no destination")
            continue
        residual = [x for x in cash if x.get("is_residual")]
        pct_total = sum(Decimal(str(x.get("percentage", 0)))
                        for x in cash if not x.get("is_residual"))
        if not residual and abs(pct_total - 100) > Decimal("0.0001"):
            problems.append(
                f"rule {r['code']}: CASH percentages total {pct_total}%, not "
                f"100%. Add a residual split or correct the percentages.")
            continue
        missing = [x["account"] for x in splits if x["account"] not in account_ids]
        if missing:
            problems.append(
                f"rule {r['code']}: destination account(s) not defined: "
                f"{', '.join(missing)}")
            continue

        rule_row = (await s.execute(text("""
            INSERT INTO settlement_rules
                (id, code, name, scope, product_id, business_unit_id, basis,
                 priority, effective_from, is_active, description)
            VALUES (gen_random_uuid(), :c, :n, :scope, :p, :bu, :basis, :prio,
                    COALESCE(CAST(:from_ AS date), CURRENT_DATE), TRUE, :desc)
         RETURNING id
        """), {
            "c": r["code"], "n": r["name"], "scope": scope,
            "p": str(product_id) if product_id else None,
            "bu": str(business_unit_id) if business_unit_id else None,
            "basis": r.get("basis", "PERCENTAGE"),
            "prio": r.get("priority", 100),
            "from_": r.get("effective_from"),
            "desc": r.get("description"),
        })).first()

        for i, sp in enumerate(splits):
            await s.execute(text("""
                INSERT INTO settlement_rule_splits
                    (id, rule_id, financial_account_id, allocation_type,
                     percentage, fixed_amount, rate_per_unit, is_residual,
                     sort_order, description)
                VALUES (gen_random_uuid(), :r, :a, :type, :pct, :fixed, :rate,
                        :resid, :sort, :desc)
            """), {
                "r": str(rule_row.id), "a": str(account_ids[sp["account"]]),
                "type": sp.get("allocation_type", "CASH"),
                "pct": sp.get("percentage"),
                "fixed": sp.get("fixed_amount"),
                "rate": sp.get("rate_per_unit"),
                "resid": bool(sp.get("is_residual")),
                "sort": sp.get("sort_order", i),
                "desc": sp.get("description"),
            })
        created["rules"] += 1
        ok(f"create rule {r['code']} -- {len(splits)} split(s)")

    hdr("Summary")
    for key in ("units", "accounts", "mappings", "rules"):
        info(f"{key:<10} created {created[key]:<4} unchanged {skipped[key]}")

    if problems:
        print()
        for p in problems:
            err(p)
        # A partly-applied configuration is worse than none: the operator would
        # believe products are mapped that are not.
        await s.rollback()
        raise SystemExit(
            f"\n{len(problems)} problem(s) in the configuration. Nothing was "
            f"written. Fix the file and run again.")

    if commit:
        await s.commit()
        print(f"\n{C['ok']}COMMITTED.{C['off']}")
    else:
        await s.rollback()
        print(f"\n{C['warn']}DRY RUN (rolled back). "
              f"Re-run with --commit to apply.{C['off']}")


# ---------------------------------------------------------------------------
# 4. cutover
# ---------------------------------------------------------------------------

async def mark_historical(s: AsyncSession, before: date, commit: bool) -> None:
    """Record pre-cutover payments as out of scope, rather than distributing.

    Payments taken before the module existed were banked into whatever account
    the business actually used at the time. Distributing them now would post
    back-dated ledger entries for money movements that never happened, so the
    honest treatment is the same one the accounting engine uses: a cutover
    date, with everything before it recorded as deliberately out of scope.

    Writes a SKIPPED settlement per payment, which both explains the decision
    and stops those payments sitting in the exceptions list forever.
    """
    if not await _schema_installed(s):
        raise SystemExit("MAPD tables are not present.")

    rows = (await s.execute(text("""
        SELECT p.id, p.invoice_id, p.amount
          FROM payments p
         WHERE p.payment_date < :before
           AND NOT EXISTS (SELECT 1 FROM settlements s
                            WHERE s.payment_id = p.id
                              AND s.status IN ('PENDING','COMPLETED','SKIPPED'))
         ORDER BY p.payment_date
    """), {"before": before})).fetchall()

    hdr(f"Payments before {before}")
    total = sum(money(r.amount) for r in rows) if rows else Decimal("0.00")
    info(f"{len(rows)} payment(s), {total:,.2f}")
    if not rows:
        ok("Nothing to mark.")
        return

    from uuid import uuid4
    from app.services.settlement import mapd_audit
    reason = (f"Predates the MAPD cutover of {before}; banked under the "
              f"arrangement in force at the time and deliberately not "
              f"redistributed.")

    for r in rows:
        settlement_id = uuid4()
        await s.execute(text("""
            INSERT INTO settlements
                (id, settlement_reference, payment_id, invoice_id,
                 gross_amount, allocated_amount, obligation_amount, status,
                 failure_reason, attempt_number)
            VALUES (:id, :ref, :pid, :iid, :gross, 0, 0, 'SKIPPED', :why, 1)
        """), {"id": str(settlement_id),
               "ref": f"STL-{uuid4().hex[:12].upper()}",
               "pid": str(r.id), "iid": str(r.invoice_id),
               "gross": str(money(r.amount)), "why": reason})
        await mapd_audit(
            s, event_type="SETTLEMENT_SKIPPED", entity_type="settlement",
            entity_id=settlement_id, payment_id=r.id,
            settlement_id=settlement_id, actor_label="setup_mapd cutover",
            detail={"reason": reason, "cutover": str(before)})

    ok(f"{len(rows)} payment(s) marked out of scope")
    if commit:
        await s.commit()
        print(f"\n{C['ok']}COMMITTED.{C['off']}")
    else:
        await s.rollback()
        print(f"\n{C['warn']}DRY RUN (rolled back).{C['off']}")


async def backfill(s: AsyncSession, since: date, commit: bool) -> None:
    """Distribute historical payments for real.

    READ THIS FIRST. This posts ledger entries dated to the ORIGINAL payment
    dates, describing money moving between accounts on days when it did not
    actually move. That is defensible only if the destination accounts are
    internal divisions of one balance rather than separate banks, and only for
    a period whose books are still open. If in doubt use --mark-historical.
    """
    if not await _schema_installed(s):
        raise SystemExit("MAPD tables are not present.")

    from app.services.settlement import distribute_payment

    rows = (await s.execute(text("""
        SELECT p.id, p.amount FROM payments p
         WHERE p.payment_date >= :since
           AND NOT EXISTS (SELECT 1 FROM settlements s
                            WHERE s.payment_id = p.id
                              AND s.status IN ('PENDING','COMPLETED','SKIPPED'))
         ORDER BY p.payment_date
    """), {"since": since})).fetchall()

    hdr(f"Distributing payments from {since}")
    info(f"{len(rows)} candidate payment(s)")

    counts = {"COMPLETED": 0, "SKIPPED": 0, "FAILED": 0}
    for r in rows:
        result = await distribute_payment(
            s, payment_id=r.id, actor_label="setup_mapd backfill")
        counts[result["status"]] = counts.get(result["status"], 0) + 1
        if result["status"] == "FAILED":
            err(f"{r.id}: {result.get('reason')}")

    for status_, n in counts.items():
        (ok if status_ == "COMPLETED" else warn)(f"{status_}: {n}")

    if commit:
        await s.commit()
        print(f"\n{C['ok']}COMMITTED.{C['off']}")
    else:
        await s.rollback()
        print(f"\n{C['warn']}DRY RUN (rolled back).{C['off']}")


# ---------------------------------------------------------------------------

async def main(args) -> None:
    engine, maker = _session_maker()
    try:
        async with maker() as s:
            if args.template:
                await template(s)
            elif args.bootstrap:
                with open(args.bootstrap, "r", encoding="utf-8") as fh:
                    config = json.load(fh)
                await bootstrap(s, config, args.commit)
            elif args.mark_historical:
                await mark_historical(
                    s, date.fromisoformat(args.mark_historical), args.commit)
            elif args.backfill:
                await backfill(
                    s, date.fromisoformat(args.backfill), args.commit)
            else:
                await status(s)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Bring the MAPD payment distribution module live.")
    ap.add_argument("--status", action="store_true",
                    help="report readiness (default; writes nothing)")
    ap.add_argument("--template", action="store_true",
                    help="emit a starter config listing this database's products")
    ap.add_argument("--bootstrap", metavar="CONFIG.json",
                    help="apply a configuration file")
    ap.add_argument("--mark-historical", metavar="YYYY-MM-DD",
                    help="record payments before this date as out of scope")
    ap.add_argument("--backfill", metavar="YYYY-MM-DD",
                    help="distribute payments from this date onward "
                         "(posts back-dated ledger entries -- read the docstring)")
    ap.add_argument("--commit", action="store_true",
                    help="actually write; everything is a dry run without it")
    asyncio.run(main(ap.parse_args()))
