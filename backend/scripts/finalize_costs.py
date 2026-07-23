#!/usr/bin/env python
"""Bring gross profit and inventory to life from the REAL registered costs.

Supersedes the revenue-only backfill. Three steps, in one transaction:

  1. Recompute each historical sale line's cost from product_pricing. The sale
     lines do not reliably record which UNIT was sold, so a quantity x unit-cost
     figure is unsafe. But the cost/retail RATIO is consistent across a
     product's units, so COGS = line revenue x (cost_price / retail_price) is
     both unit-independent and grounded in the registered prices.

  2. Post the opening inventory that those goods came from, as Opening Balance
     Equity — finished goods = period COGS + current finished-goods on hand
     (so the account nets to the real closing value), and raw materials on
     hand. This is a simplification (it books goods the business produced over
     the period as opening capital rather than reconstructing every production
     run), chosen so the books show a correct GROSS PROFIT and a correct
     CLOSING INVENTORY without inventing production costs we do not have.

  3. Post the period COGS: Dr Cost of Goods Sold / Cr Finished Goods.

Result: revenue (already posted) minus real COGS = real gross profit, and the
balance sheet shows real stock. Idempotent per source_reference. Dry-run by
default; --commit to write.

  --exclude-water   leave the RAW-MATERIAL 'water' rows out of the opening
                    valuation (its 3,000/kg cost values to ~15M, almost
                    certainly a unit/quantity error; on by default).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text                                    # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession  # noqa: E402
from sqlalchemy.orm import sessionmaker                        # noqa: E402
from app.services.ledger import (                              # noqa: E402
    Line, money, post_entry, profit_and_loss, balance_sheet, account_ledger)

REF_OPENING = "OPENING-INVENTORY-V2"
REF_COGS = "HISTORICAL-COGS-V2"
PERIOD_END = date(2026, 7, 21)


async def already(session, ref) -> bool:
    return (await session.execute(text(
        "SELECT 1 FROM gl_journal_entries WHERE source_reference=:r "
        "AND status<>'REVERSED'"), {"r": ref})).first() is not None


async def run(commit: bool, exclude_water: bool) -> None:
    url = os.getenv("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL not set.")
    if os.getenv("ACCOUNTING_POSTING_ENABLED", "").lower() not in ("1", "true", "yes", "on"):
        sys.exit("ACCOUNTING_POSTING_ENABLED must be true.")
    eng = create_async_engine(url, future=True)
    mk = sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)

    async with mk() as s:
        if await already(s, REF_OPENING) or await already(s, REF_COGS):
            print("Already finalized (opening inventory / COGS posted). Nothing to do.")
            await eng.dispose(); return

        # 1. Recompute sale-line cost snapshots from the cost/retail ratio.
        await s.execute(text("""
            WITH ratio AS (
                SELECT product_id, AVG(cost_price / NULLIF(retail_price, 0)) r
                  FROM product_pricing
                 WHERE retail_price > 0 AND cost_price > 0
                 GROUP BY product_id)
            UPDATE sales_order_lines sol
               SET cost_total = ROUND(sol.line_total * ratio.r, 2),
                   unit_cost  = CASE WHEN sol.quantity > 0
                                     THEN ROUND(sol.line_total * ratio.r / sol.quantity, 6)
                                     ELSE 0 END,
                   cost_source = 'price_list_ratio'
              FROM ratio
             WHERE ratio.product_id = sol.product_id
        """))

        cogs = money((await s.execute(text(
            "SELECT COALESCE(SUM(cost_total),0) FROM sales_order_lines "
            "WHERE cost_source='price_list_ratio'"))).scalar())

        fg_close = money((await s.execute(text("""
            WITH pc AS (SELECT product_id, MIN(cost_price) uc FROM product_pricing
                         WHERE cost_price>0 GROUP BY product_id)
            SELECT COALESCE(SUM(sl.current_stock*pc.uc),0) FROM stock_levels sl
              JOIN pc ON pc.product_id=sl.product_id
             WHERE sl.product_id IS NOT NULL AND sl.current_stock>0"""))).scalar())

        water_clause = "AND rm.name NOT ILIKE '%water%'" if exclude_water else ""
        rm_val = money((await s.execute(text(f"""
            SELECT COALESCE(SUM(sl.current_stock*rm.unit_cost),0)
              FROM stock_levels sl JOIN raw_materials rm ON rm.id=sl.raw_material_id
             WHERE sl.raw_material_id IS NOT NULL AND sl.current_stock>0
               AND rm.unit_cost>0 {water_clause}"""))).scalar())

        fg_open = money(cogs + fg_close)

        # 2. Opening inventory brought in as equity.
        opening_lines = [Line("1430", debit=fg_open,
                              description="Opening finished-goods inventory (COGS + closing, at registered cost)")]
        if rm_val > 0:
            opening_lines.append(Line("1410", debit=rm_val,
                                     description="Opening raw-material inventory (registered cost"
                                                 + (", excl. water outlier)" if exclude_water else ")")))
        opening_lines.append(Line("3400", credit=money(fg_open + rm_val),
                                  description="Opening inventory brought in as equity"))
        await post_entry(s, entry_date=date(2026, 3, 1),
                         description="Opening inventory at registered cost (finalize_costs.py)",
                         source_module="opening_balance", source_reference=REF_OPENING,
                         lines=opening_lines)

        # 3. Period cost of goods sold.
        await post_entry(s, entry_date=PERIOD_END,
                         description="Cost of goods sold, Mar-Jul 2026 (registered cost, ratio basis)",
                         source_module="sales.cogs", source_reference=REF_COGS,
                         lines=[Line("5100", debit=cogs, description="Historical COGS"),
                                Line("1430", credit=cogs, description="Goods sold, drawn from finished goods")])

        # Report
        pnl = await profit_and_loss(s)
        bs = await balance_sheet(s)
        fg_bal = (await account_ledger(s, account_code="1430"))["closing_balance"]
        print(f"\nRecomputed COGS (ratio basis): {cogs:,.2f}")
        print(f"Finished-goods closing value : {fg_close:,.2f}")
        print(f"Opening FG inventory posted  : {fg_open:,.2f}  (= COGS + closing)")
        print(f"Opening RM inventory posted  : {rm_val:,.2f}"
              + ("  (water excluded)" if exclude_water else ""))
        print("\n--- Profit & Loss ---")
        print(f"  Income (revenue) {pnl['total_income']:,.2f}")
        print(f"  Expenses (COGS)  {pnl['total_expenses']:,.2f}")
        print(f"  NET/GROSS PROFIT {pnl['net_profit']:,.2f}  "
              f"({pnl['net_profit']/pnl['total_income']*100:.1f}% margin)")
        print("\n--- Balance sheet ---")
        print(f"  Assets       {bs['assets']:,.2f}")
        print(f"  Equity       {bs['total_equity']:,.2f}")
        print(f"  Balanced     {bs['balanced']}  (finished goods now {fg_bal:,.2f} = real closing stock)")

        if commit:
            await s.commit(); print("\nCOMMITTED.")
        else:
            await s.rollback(); print("\nDRY RUN (rolled back).")
    await eng.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--include-water", action="store_true",
                    help="include the raw-material 'water' rows in the opening valuation")
    args = ap.parse_args()
    asyncio.run(run(args.commit, exclude_water=not args.include_water))
