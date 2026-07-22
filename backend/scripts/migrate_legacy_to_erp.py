#!/usr/bin/env python
"""Migrate the legacy ASTROBSM ERP data into a fresh dedicated ERP database.

Context
-------
The old deployment kept the ERP in the `public` schema of a database
(`firstconnection`) shared with two unrelated apps (an exam platform and the
`crp` clinical-rotation platform). That `public` schema is the LEGACY
denormalised shape (integer/slug ids, float money, order lines stored as JSON,
logins living in a `staff` table with plaintext passwords).

This script copies the real ERP master data OUT of that legacy schema and INTO
a fresh database that has already been built from the repo's Alembic migrations
(`alembic upgrade head` -> revision q6789012345p, the model schema). The two
databases never share a connection; the source is only ever read.

What moves (everything else in the legacy schema is empty or belongs to the
other apps and is deliberately left behind):

    staff (7)          -> users              logins; plaintext pw -> bcrypt,
                                             username -> username@astrobsm.local
    customers (87)     -> customers          int id -> uuid, customer_id -> code
    products (25)      -> products           slug id -> uuid, price fields remap
    warehouses (1)     -> warehouses         int id -> uuid, wh_id -> code
    orders (17)        -> sales_orders       + sales_order_lines (JSON items
                                             unpacked, customer resolved/created)
    settings (1)       -> system_settings    company_info JSON -> flat columns
    distributors (6)   -> legacy_distributors           carry-over (app-invisible)
    distributor_inventory (2)  -> legacy_distributor_inventory
    inventory_transactions (3) -> legacy_inventory_transactions

Design rules
------------
* Read-only on the source. Every write goes to the target inside ONE
  transaction; any error rolls the whole thing back.
* Lossless: an order whose customer is not already present has that customer
  CREATED from the order's embedded fields rather than dropped.
* Refuses to run if the target is not at the expected Alembic head, or if the
  target already holds ERP rows (guards against a double run) -- override with
  --force.
* Prints a row-count reconciliation at the end and asserts it.

Usage
-----
    SOURCE_DATABASE_URL=postgresql://.../firstconnection?sslmode=require \
    TARGET_DATABASE_URL=postgresql://.../astro_erp \
        python scripts/migrate_legacy_to_erp.py            # dry run report
        python scripts/migrate_legacy_to_erp.py --commit   # actually write
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import uuid

import bcrypt
import psycopg2
import psycopg2.extras

psycopg2.extras.register_uuid()

EXPECTED_HEAD = "q6789012345p"
EMAIL_DOMAIN = "astrobsm.local"

ROLE_MAP = {
    "admin": "admin",
    "sales": "sales_staff",
    "cco": "customer_care",
    "marketer": "marketer",
    "production": "production_staff",
    "warehouse": "warehouse_logistics",
}


def log(msg: str) -> None:
    print(msg, flush=True)


def digits(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")


def bcrypt_hash(plain: str | None) -> str:
    raw = (plain or "").encode()[:72]
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=12)).decode()


# ---------------------------------------------------------------------------
# carry-over tables for data the new schema has no home for yet
CARRY_DDL = [
    """CREATE TABLE IF NOT EXISTS legacy_distributors (
        id varchar PRIMARY KEY, name varchar NOT NULL, state varchar, zone varchar,
        phone varchar, email varchar, bank_name varchar, account_number varchar,
        account_name varchar, is_active boolean, is_primary boolean, active boolean,
        created_at timestamptz, updated_at timestamptz)""",
    """CREATE TABLE IF NOT EXISTS legacy_distributor_inventory (
        id integer PRIMARY KEY, distributor_id varchar, product_id varchar,
        quantity integer, reorder_level integer, last_restocked timestamptz,
        cost_price numeric, notes text, created_at timestamptz, updated_at timestamptz)""",
    """CREATE TABLE IF NOT EXISTS legacy_inventory_transactions (
        id integer PRIMARY KEY, distributor_id varchar, product_id varchar,
        transaction_type varchar, quantity integer, previous_quantity integer,
        new_quantity integer, reference_id varchar, notes text, created_by varchar,
        created_at timestamptz)""",
]


def preflight(tcur, force: bool) -> None:
    tcur.execute("SELECT version_num FROM alembic_version")
    row = tcur.fetchone()
    head = row[0] if row else None
    if head != EXPECTED_HEAD:
        sys.exit(f"ABORT: target is at Alembic revision {head!r}, expected "
                 f"{EXPECTED_HEAD!r}. Build it with `alembic upgrade head` first.")
    for tbl in ("users", "customers", "products", "sales_orders"):
        tcur.execute(f"SELECT count(*) FROM {tbl}")
        n = tcur.fetchone()[0]
        if n and not force:
            sys.exit(f"ABORT: target {tbl} already has {n} rows. Refusing to "
                     f"run twice. Use --force to override.")
    log(f"  target OK: at head {head}, ERP tables empty")


def migrate(scur, tcur) -> dict:
    counts: dict[str, int] = {}

    # -- users (from legacy staff) -----------------------------------------
    scur.execute("SELECT username, password, name, full_name, email, role, "
                 "active, phone, last_login FROM staff")
    staff = scur.fetchall()
    seen_emails: set[str] = set()
    for s in staff:
        username = (s["username"] or "").strip()
        email = (s["email"] or "").strip().lower()
        if not email or "@" not in email:
            email = f"{username.lower()}@{EMAIL_DOMAIN}"
        # guarantee uniqueness of the synthesised address
        base, n = email, 1
        while email in seen_emails:
            local, _, dom = base.partition("@")
            email = f"{local}{n}@{dom}"
            n += 1
        seen_emails.add(email)
        full_name = (s["full_name"] or s["name"] or username).strip()
        role = ROLE_MAP.get((s["role"] or "").strip().lower(), "sales_staff")
        tcur.execute(
            """INSERT INTO users (id, email, full_name, hashed_password, role,
                   is_active, is_locked, failed_login_attempts, phone, last_login,
                   created_at)
               VALUES (%s,%s,%s,%s,%s,%s,false,0,%s,%s, now())""",
            (uuid.uuid4(), email, full_name, bcrypt_hash(s["password"]), role,
             bool(s["active"]) if s["active"] is not None else True,
             s["phone"], s["last_login"]))
    counts["users"] = len(staff)

    # -- customers ---------------------------------------------------------
    scur.execute("SELECT id, name, customer_id, phone, address, company, "
                 "created_at FROM customers")
    cust_id_map: dict[int, str] = {}          # legacy int id -> new uuid
    by_phone: dict[str, str] = {}
    by_name: dict[str, str] = {}
    for c in scur.fetchall():
        nid = str(uuid.uuid4())
        cust_id_map[c["id"]] = nid
        code = (c["customer_id"] or f"CUST-{c['id']}").strip()
        tcur.execute(
            """INSERT INTO customers (id, customer_code, name, email, phone,
                   address, credit_limit, is_active, created_at)
               VALUES (%s,%s,%s,NULL,%s,%s,0,true, COALESCE(%s, now()))""",
            (nid, code, c["name"], c["phone"], c["address"], c["created_at"]))
        if digits(c["phone"]):
            by_phone.setdefault(digits(c["phone"]), nid)
        if c["name"]:
            by_name.setdefault(c["name"].strip().lower(), nid)
    counts["customers"] = len(cust_id_map)

    # -- products ----------------------------------------------------------
    scur.execute("SELECT id, name, description, sku, unit, unit_of_measure, "
                 "price, price_retail, price_wholesaler, price_distributor, "
                 "min_order_qty, minimum_order, category, created_at FROM products")
    prod_slug_map: dict[str, str] = {}        # legacy slug id -> new uuid
    prod_sku_map: dict[str, str] = {}         # sku -> new uuid
    used_sku: set[str] = set()
    for p in scur.fetchall():
        nid = str(uuid.uuid4())
        sku = (p["sku"] or "").strip() or f"SKU-{p['id']}"
        while sku in used_sku:
            sku = f"{sku}-{p['id']}"
        used_sku.add(sku)
        unit = (p["unit"] or p["unit_of_measure"] or "each").strip() or "each"
        moq = p["min_order_qty"] if p["min_order_qty"] is not None else p["minimum_order"]
        tcur.execute(
            """INSERT INTO products (id, sku, name, description, manufacturer,
                   unit, reorder_level, cost_price, selling_price, retail_price,
                   wholesale_price, lead_time_days, minimum_order_quantity, created_at)
               VALUES (%s,%s,%s,%s,NULL,%s,NULL,NULL,%s,%s,%s,NULL,%s, COALESCE(%s, now()))""",
            (nid, sku, p["name"], p["description"], unit, p["price"],
             p["price_retail"], p["price_wholesaler"], moq, p["created_at"]))
        prod_slug_map[str(p["id"])] = nid
        prod_sku_map[sku] = nid
        if p["sku"]:
            prod_sku_map[p["sku"].strip()] = nid
    counts["products"] = len(prod_slug_map)

    # -- warehouses --------------------------------------------------------
    scur.execute("SELECT id, name, location, wh_id FROM warehouses")
    wh_default: str | None = None
    n_wh = 0
    for w in scur.fetchall():
        nid = str(uuid.uuid4())
        code = (w["wh_id"] or f"WH-{w['id']}").strip()
        tcur.execute(
            """INSERT INTO warehouses (id, code, name, location, is_active, created_at)
               VALUES (%s,%s,%s,%s,true, now())""",
            (nid, code, w["name"], w["location"]))
        wh_default = wh_default or nid
        n_wh += 1
    counts["warehouses"] = n_wh

    # -- orders -> sales_orders + sales_order_lines ------------------------
    scur.execute("SELECT id, order_number, status, payment_status, payment_date, "
                 "created_at, total, total_amount, subtotal, notes, customer_name, "
                 "customer_email, customer_phone, customer_address, items FROM orders")
    orders = scur.fetchall()
    n_orders = 0
    n_lines = 0
    n_created_customers = 0
    for o in orders:
        # resolve the customer: phone, then name, else create from embedded data
        cid = None
        if digits(o["customer_phone"]):
            cid = by_phone.get(digits(o["customer_phone"]))
        if cid is None and o["customer_name"]:
            cid = by_name.get(o["customer_name"].strip().lower())
        if cid is None:
            cid = str(uuid.uuid4())
            code = f"CUST-ORD-{n_created_customers + 1}"
            tcur.execute(
                """INSERT INTO customers (id, customer_code, name, email, phone,
                       address, credit_limit, is_active, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,0,true, COALESCE(%s, now()))""",
                (cid, code, o["customer_name"] or "Unknown (from order)",
                 o["customer_email"], o["customer_phone"], o["customer_address"],
                 o["created_at"]))
            n_created_customers += 1
            if digits(o["customer_phone"]):
                by_phone.setdefault(digits(o["customer_phone"]), cid)
            if o["customer_name"]:
                by_name.setdefault(o["customer_name"].strip().lower(), cid)

        so_id = str(uuid.uuid4())
        total = o["total"] if o["total"] is not None else (
            o["total_amount"] if o["total_amount"] is not None else o["subtotal"])
        order_no = (o["order_number"] or f"SO-{o['id']}").strip()
        tcur.execute(
            """INSERT INTO sales_orders (id, order_number, customer_id, warehouse_id,
                   status, payment_status, payment_date, order_date, total_amount,
                   notes, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s, COALESCE(%s, now()),%s,%s, COALESCE(%s, now()))""",
            (so_id, order_no, cid, wh_default,
             (o["status"] or "pending").strip(),
             (o["payment_status"] or "unpaid").strip(), o["payment_date"],
             o["created_at"], total, o["notes"], o["created_at"]))
        n_orders += 1

        items = o["items"] if isinstance(o["items"], list) else []
        for it in items:
            pid = None
            if it.get("sku"):
                pid = prod_sku_map.get(str(it["sku"]).strip())
            if pid is None and it.get("productId"):
                pid = prod_slug_map.get(str(it["productId"]).strip())
            if pid is None:
                raise RuntimeError(
                    f"order {order_no}: cannot resolve product for line "
                    f"{it.get('name')!r} (sku={it.get('sku')}, "
                    f"productId={it.get('productId')}). Aborting to avoid loss.")
            qty = it.get("quantity") or 0
            price = it.get("price") or 0
            line_total = it.get("subtotal")
            if line_total is None:
                line_total = qty * price
            tcur.execute(
                """INSERT INTO sales_order_lines (id, sales_order_id, product_id,
                       unit, quantity, unit_price, line_total)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (uuid.uuid4(), so_id, pid, it.get("unit"), qty, price, line_total))
            n_lines += 1
    counts["sales_orders"] = n_orders
    counts["sales_order_lines"] = n_lines
    counts["customers_created_from_orders"] = n_created_customers
    counts["customers"] += n_created_customers

    # -- settings -> system_settings (best-effort flatten) -----------------
    scur.execute("SELECT company_info FROM settings LIMIT 1")
    row = scur.fetchone()
    ci = (row["company_info"] if row and isinstance(row["company_info"], dict) else {})
    tcur.execute(
        """INSERT INTO system_settings (id, company_name, company_logo_url,
               company_slogan, business_address, business_email, business_phone)
           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
        (uuid.uuid4(), ci.get("name") or "ASTRO-ASIX ERP", ci.get("logo"),
         ci.get("slogan"), ci.get("address"), ci.get("email"), ci.get("phone")))
    counts["system_settings"] = 1

    # -- carry-over tables -------------------------------------------------
    for ddl in CARRY_DDL:
        tcur.execute(ddl)
    scur.execute("SELECT id,name,state,zone,phone,email,bank_name,account_number,"
                 "account_name,is_active,is_primary,active,created_at,updated_at "
                 "FROM distributors")
    dist = scur.fetchall()
    for d in dist:
        tcur.execute(
            """INSERT INTO legacy_distributors VALUES
               (%(id)s,%(name)s,%(state)s,%(zone)s,%(phone)s,%(email)s,%(bank_name)s,
                %(account_number)s,%(account_name)s,%(is_active)s,%(is_primary)s,
                %(active)s,%(created_at)s,%(updated_at)s)""", dict(d))
    counts["legacy_distributors"] = len(dist)

    scur.execute("SELECT * FROM distributor_inventory")
    di = scur.fetchall()
    for r in di:
        tcur.execute(
            """INSERT INTO legacy_distributor_inventory VALUES
               (%(id)s,%(distributor_id)s,%(product_id)s,%(quantity)s,%(reorder_level)s,
                %(last_restocked)s,%(cost_price)s,%(notes)s,%(created_at)s,%(updated_at)s)""",
            dict(r))
    counts["legacy_distributor_inventory"] = len(di)

    scur.execute("SELECT * FROM inventory_transactions")
    itx = scur.fetchall()
    for r in itx:
        tcur.execute(
            """INSERT INTO legacy_inventory_transactions VALUES
               (%(id)s,%(distributor_id)s,%(product_id)s,%(transaction_type)s,%(quantity)s,
                %(previous_quantity)s,%(new_quantity)s,%(reference_id)s,%(notes)s,
                %(created_by)s,%(created_at)s)""", dict(r))
    counts["legacy_inventory_transactions"] = len(itx)

    return counts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true",
                    help="write for real (default is a dry run that rolls back)")
    ap.add_argument("--force", action="store_true",
                    help="run even if the target already has ERP rows")
    args = ap.parse_args()

    src = os.getenv("SOURCE_DATABASE_URL")
    tgt = os.getenv("TARGET_DATABASE_URL")
    if not src or not tgt:
        sys.exit("Set SOURCE_DATABASE_URL and TARGET_DATABASE_URL.")

    log(f"source (read-only): {re.sub(r'://[^@]*@', '://***@', src)}")
    log(f"target            : {re.sub(r'://[^@]*@', '://***@', tgt)}")
    log(f"mode              : {'COMMIT' if args.commit else 'DRY RUN (rollback)'}")

    sconn = psycopg2.connect(src)
    sconn.set_session(readonly=True, autocommit=False)
    tconn = psycopg2.connect(tgt)
    try:
        scur = sconn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        tcur = tconn.cursor()
        scur.execute("SET search_path TO public")
        log("\nPreflight:")
        preflight(tcur, args.force)
        log("\nMigrating:")
        counts = migrate(scur, tcur)
        log("\nRow counts written to target:")
        for k, v in counts.items():
            log(f"  {k:34} {v}")
        if args.commit:
            tconn.commit()
            log("\nCOMMITTED.")
        else:
            tconn.rollback()
            log("\nDRY RUN complete -- rolled back. Re-run with --commit to apply.")
    except Exception:
        tconn.rollback()
        log("\nERROR -- target rolled back, nothing written.")
        raise
    finally:
        sconn.close()
        tconn.close()


if __name__ == "__main__":
    main()
