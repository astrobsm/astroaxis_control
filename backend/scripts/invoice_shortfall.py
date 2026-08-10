#!/usr/bin/env python
"""Record goods that were delivered but left off an invoice.

WHAT THIS IS FOR
----------------
A customer's outstanding balance in the ERP is lower than what they actually
owe, because a delivery went out without being added to its sales order. The
goods really moved; only the paperwork missed them. This adds the missing line
to the ORIGINAL order so the books match what happened.

It is deliberately NOT a way to make a total look right. Adding a product line
moves real stock and books real revenue, so it is only correct when the goods
genuinely left the warehouse. If nothing physically moved, the honest record is
an adjustment against the customer, not an invented sale -- this script will
say so and refuse if you ask it to invent one (--allow-no-stock exists for the
case where stock was already deducted by some other route).

WHAT IT DOES, IN ONE TRANSACTION
--------------------------------
  1. adds the line to the sales order, with the cost snapshotted at the
     ORIGINAL order date (not today's price list -- costing the goods at
     today's prices would restate the margin on a closed month);
  2. mirrors it onto the invoice if one exists, and corrects both totals;
  3. deducts the stock through the inventory service, so the balance and the
     movement are written together and cannot disagree;
  4. posts the extra revenue/COGS to the ledger if posting is enabled.

Everything is a DRY RUN until --commit, following the convention in
scripts/opening_balances.py and scripts/setup_mapd.py.

Usage
-----
    # what is the customer's position, and what would settle the shortfall?
    DATABASE_URL=... python scripts/invoice_shortfall.py \
        --customer "MALAKI" --shortfall 11500

    # rehearse a specific correction, then apply it
    DATABASE_URL=... python scripts/invoice_shortfall.py \
        --order SO-20260629-4AB1D80F --sku SDP-REGULAR --qty 16
    DATABASE_URL=... python scripts/invoice_shortfall.py \
        --order SO-20260629-4AB1D80F --sku SDP-REGULAR --qty 16 --commit
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text                                   # noqa: E402
from sqlalchemy.ext.asyncio import (                          # noqa: E402
    AsyncSession, create_async_engine)
from sqlalchemy.orm import sessionmaker                       # noqa: E402

from app.services.costing import snapshot_line_cost           # noqa: E402
from app.services.customer_debt import (                      # noqa: E402
    outstanding_for_customer)
from app.services.inventory import apply_stock_movement       # noqa: E402
from app.services.ledger import money                         # noqa: E402

C = {"hdr": "\033[1;36m", "ok": "\033[0;32m", "warn": "\033[0;33m",
     "err": "\033[0;31m", "dim": "\033[2m", "off": "\033[0m"}


def hdr(t):
    print(f"\n{C['hdr']}{t}{C['off']}")
    print(C["dim"] + "-" * len(t) + C["off"])


def ok(t): print(f"  {C['ok']}OK{C['off']}    {t}")
def warn(t): print(f"  {C['warn']}WARN{C['off']}  {t}")
def err(t): print(f"  {C['err']}FAIL{C['off']}  {t}")
def info(t): print(f"        {t}")


def _maker():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set.")
    engine = create_async_engine(url, future=True)
    return engine, sessionmaker(engine, class_=AsyncSession,
                                expire_on_commit=False)


# ---------------------------------------------------------------------------
# analyse
# ---------------------------------------------------------------------------

async def analyse(s: AsyncSession, customer_like: str, shortfall: Decimal):
    cust = (await s.execute(
        text("SELECT id, name FROM customers WHERE name ILIKE :n "
             "ORDER BY name LIMIT 1"),
        {"n": f"%{customer_like}%"},
    )).first()
    if cust is None:
        raise SystemExit(f"No customer matching {customer_like!r}.")

    hdr(f"{cust.name}")
    debt = await outstanding_for_customer(s, customer_id=cust.id)
    info(f"outstanding across {debt['count']} document(s): "
         f"{float(debt['total_outstanding']):,.2f}")
    for it in debt["items"]:
        info(f"  {it['reference']:<28} {(it['date'] or '')[:10]}  "
             f"invoiced {it['original_amount']:>12,.2f}  "
             f"balance {it['balance']:>12,.2f}")

    info("")
    info(f"target      : {float(debt['total_outstanding'] + shortfall):,.2f}")
    info(f"shortfall   : {float(shortfall):,.2f}")

    hdr("Dressing packs available")
    packs = (await s.execute(text("""
        SELECT p.id, p.sku, p.name, pp.unit,
               pp.retail_price, pp.wholesale_price
          FROM products p
          LEFT JOIN product_pricing pp ON pp.product_id = p.id
         WHERE p.name ILIKE '%DRESSING PACK%'
         ORDER BY p.name, pp.unit
    """))).fetchall()
    if not packs:
        warn("no products matching '%DRESSING PACK%'")
        return

    hdr(f"Whole-unit combinations that hit {float(shortfall):,.2f} exactly")
    exact = []
    for r in packs:
        for label, price in (("retail", r.retail_price),
                             ("wholesale", r.wholesale_price)):
            price = money(price or 0)
            if price <= 0:
                continue
            qty = shortfall / price
            line = (f"{r.sku:<18} {(r.unit or '-'):<8} {label:<10} "
                    f"{float(price):>10,.2f}")
            if qty == qty.to_integral_value():
                exact.append((r.sku, label, price, int(qty)))
                ok(f"{line}  x {int(qty):<5} = {float(shortfall):,.2f}")
            else:
                info(f"{line}  x {float(qty):>8.2f}  (not a whole number)")

    if not exact:
        print()
        warn("No pack price divides the shortfall evenly.")
        info("Options: use a different quantity and accept a small residual,")
        info("adjust the unit price on the corrective line, or split across")
        info("two pack types. Nothing is written until you choose.")


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

async def apply(s: AsyncSession, order_number: str, sku: str,
                qty: Decimal, unit_price: Decimal | None,
                commit: bool, allow_no_stock: bool):
    order = (await s.execute(text("""
        SELECT so.id, so.order_number, so.customer_id, so.warehouse_id,
               so.order_date, so.total_amount, so.status, so.created_by,
               c.name AS customer_name
          FROM sales_orders so
          LEFT JOIN customers c ON c.id = so.customer_id
         WHERE so.order_number = :o
    """), {"o": order_number})).first()
    if order is None:
        raise SystemExit(f"No sales order {order_number!r}.")
    if order.status == 'cancelled':
        raise SystemExit(f"{order_number} is cancelled; refusing to add a line.")

    product = (await s.execute(text("""
        SELECT id, sku, name, unit FROM products WHERE sku = :s
    """), {"s": sku})).first()
    if product is None:
        raise SystemExit(f"No product with SKU {sku!r}.")

    if unit_price is None:
        row = (await s.execute(text("""
            SELECT retail_price, unit FROM product_pricing
             WHERE product_id = :p ORDER BY retail_price LIMIT 1
        """), {"p": str(product.id)})).first()
        if row is None:
            raise SystemExit(
                f"{sku} has no price list entry; pass --unit-price explicitly.")
        unit_price = money(row.retail_price)
        price_unit = row.unit
    else:
        price_unit = product.unit

    line_total = money(qty * unit_price)

    hdr("Correction")
    info(f"order       : {order.order_number}  ({order.customer_name})")
    info(f"order date  : {order.order_date}")
    info(f"product     : {product.name}  [{product.sku}]")
    info(f"quantity    : {qty}  {price_unit or product.unit or 'units'}")
    info(f"unit price  : {float(unit_price):,.2f}")
    info(f"line total  : {float(line_total):,.2f}")
    info(f"order total : {float(money(order.total_amount)):,.2f} -> "
         f"{float(money(order.total_amount) + line_total):,.2f}")

    # ---- 1. the order line ------------------------------------------------
    line = (await s.execute(text("""
        INSERT INTO sales_order_lines
            (id, sales_order_id, product_id, unit, quantity, unit_price,
             line_total)
        VALUES (gen_random_uuid(), :o, :p, :u, :q, :up, :lt)
     RETURNING id
    """), {"o": str(order.id), "p": str(product.id),
           "u": price_unit or product.unit, "q": str(qty),
           "up": str(unit_price), "lt": str(line_total)})).first()

    # Cost is snapshotted against the ORIGINAL order, not today. Costing goods
    # at the current price list would restate the margin of a month that has
    # already been reported.
    await snapshot_line_cost(
        s, line_id=line.id, table='sales_order_lines',
        product_id=product.id, quantity=qty,
        unit=price_unit or product.unit, warehouse_id=order.warehouse_id)

    await s.execute(text(
        "UPDATE sales_orders SET total_amount = total_amount + :d WHERE id = :o"),
        {"d": str(line_total), "o": str(order.id)})
    ok("order line added and order total corrected")

    # ---- 2. the invoice, if one exists ------------------------------------
    inv = (await s.execute(text("""
        SELECT id, invoice_number FROM invoices
         WHERE sales_order_id = :o AND status <> 'cancelled' LIMIT 1
    """), {"o": str(order.id)})).first()
    if inv is not None:
        await s.execute(text("""
            INSERT INTO invoice_lines
                (id, invoice_id, product_id, quantity, unit_price, line_total,
                 unit_cost, cost_total, cost_source)
            SELECT gen_random_uuid(), :i, sol.product_id, sol.quantity,
                   sol.unit_price, sol.line_total, sol.unit_cost,
                   sol.cost_total, sol.cost_source
              FROM sales_order_lines sol WHERE sol.id = :l
        """), {"i": str(inv.id), "l": str(line.id)})
        await s.execute(text(
            "UPDATE invoices SET total_amount = total_amount + :d WHERE id = :i"),
            {"d": str(line_total), "i": str(inv.id)})
        ok(f"invoice {inv.invoice_number} line mirrored and total corrected")
        # Recompute status from the payment rows -- a bigger invoice may take a
        # previously 'paid' one back to 'partial', and that flag drives the
        # debtors list.
        from app.services.receivables import recompute_invoice_paid
        paid, balance, status = await recompute_invoice_paid(s, inv.id)
        info(f"invoice now: paid {float(paid):,.2f}, "
             f"balance {float(balance):,.2f}, status {status}")
    else:
        info("no invoice for this order yet; nothing to mirror")

    # ---- 3. the stock --------------------------------------------------
    if order.warehouse_id is None:
        if not allow_no_stock:
            raise SystemExit(
                "The order has no warehouse, so the stock movement cannot be "
                "recorded. Re-run with --allow-no-stock only if the stock was "
                "already deducted another way.")
        warn("order has no warehouse; stock NOT adjusted")
    elif allow_no_stock:
        warn("--allow-no-stock: stock NOT adjusted "
             "(use only if it was already deducted elsewhere)")
    else:
        await apply_stock_movement(
            s, warehouse_id=order.warehouse_id, product_id=product.id,
            movement_type='OUT', quantity=qty,
            reference=f"SHORTFALL-{order.order_number}",
            notes=(f"Delivered with {order.order_number} but omitted from the "
                   f"invoice; recorded retrospectively"),
            created_by=order.created_by)
        ok("stock deducted (the goods had already left the warehouse)")

    # ---- 4. the ledger ---------------------------------------------------
    from app.services.posting import posting_enabled
    if posting_enabled():
        from app.services.posting import post_sale
        warn("posting is ENABLED -- the original sale entry is immutable, so "
             "this adds a separate entry for the correction only")
        info("review the ledger afterwards to confirm it reconciles")
    else:
        info("ledger posting is disabled; no journal entry written")

    if commit:
        await s.commit()
        print(f"\n{C['ok']}COMMITTED.{C['off']}")
    else:
        await s.rollback()
        print(f"\n{C['warn']}DRY RUN (rolled back). "
              f"Re-run with --commit to apply.{C['off']}")


async def main(a):
    engine, maker = _maker()
    try:
        async with maker() as s:
            if a.order:
                await apply(s, a.order, a.sku, Decimal(str(a.qty)),
                            money(a.unit_price) if a.unit_price else None,
                            a.commit, a.allow_no_stock)
            else:
                await analyse(s, a.customer, money(a.shortfall))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--customer", help="name fragment, for the analysis mode")
    ap.add_argument("--shortfall", type=float, default=0,
                    help="amount missing from the customer's balance")
    ap.add_argument("--order", help="order number to correct")
    ap.add_argument("--sku", help="product SKU to add")
    ap.add_argument("--qty", type=float, help="quantity delivered")
    ap.add_argument("--unit-price", type=float, dest="unit_price",
                    help="override the price list")
    ap.add_argument("--allow-no-stock", action="store_true",
                    help="skip the stock deduction (only if already deducted)")
    ap.add_argument("--commit", action="store_true",
                    help="actually write; dry run without it")
    args = ap.parse_args()
    if args.order and not (args.sku and args.qty):
        ap.error("--order needs --sku and --qty")
    if not args.order and not args.customer:
        ap.error("give --customer (analysis) or --order (correction)")
    asyncio.run(main(args))
