#!/usr/bin/env python
"""Post an opening inventory balance so the balance sheet shows the stock asset.

The revenue backfill recorded sales, cash and receivables but deliberately left
the cost/inventory side out. This posts the ONE thing that can be established
defensibly from the data: the value of stock currently on hand.

  Dr Finished Goods (1430)      value of product stock on hand
  Dr Raw Materials  (1410)      value of raw-material stock on hand
     Cr Opening Balance Equity (3400)   the balancing figure

WHAT THIS DELIBERATELY DOES NOT DO, and why:
  * It does NOT post the historical COGS. That figure is a backfill ESTIMATE and
    works out to ~101% of revenue -- i.e. it claims every sale lost money, which
    is not credible. Posting it would write a fake loss into permanent books.
    Gross profit becomes reliable GOING FORWARD, where COGS is real.
  * Stock is valued at ESTIMATED cost (the per-product average of the sale-line
    cost snapshots, and raw_materials.unit_cost). It is the best estimate
    available, not an audited stock-take. Replace it with a real valuation when
    one exists -- reverse this entry and post the true figure.

Idempotent: refuses to post twice (keyed on source_reference OPENING-INVENTORY).

Usage:
    DATABASE_URL=... python scripts/opening_balances.py            # report
    DATABASE_URL=... python scripts/opening_balances.py --commit   # write
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text                                   # noqa: E402
from sqlalchemy.ext.asyncio import (                          # noqa: E402
    create_async_engine, AsyncSession)
from sqlalchemy.orm import sessionmaker                       # noqa: E402

from app.services.ledger import (                             # noqa: E402
    Line, money, post_entry, balance_sheet)

REF = "OPENING-INVENTORY"


async def run(commit: bool) -> None:
    url = os.getenv("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL not set.")
    engine = create_async_engine(url, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with maker() as s:
        exists = (await s.execute(text(
            "SELECT 1 FROM gl_journal_entries "
            "WHERE source_reference = :r AND status <> 'REVERSED'"),
            {"r": REF})).first()
        if exists:
            print("Opening inventory already posted. Nothing to do "
                  "(reverse it first if you want to re-post).")
            await engine.dispose()
            return

        # Finished goods: current stock x per-product average snapshot cost.
        fg = money((await s.execute(text("""
            WITH pc AS (
                SELECT product_id, AVG(unit_cost) uc
                  FROM sales_order_lines
                 WHERE unit_cost > 0 GROUP BY product_id)
            SELECT COALESCE(SUM(sl.current_stock * pc.uc), 0)
              FROM stock_levels sl
              JOIN pc ON pc.product_id = sl.product_id
             WHERE sl.product_id IS NOT NULL AND sl.current_stock > 0
        """))).scalar())

        # Raw materials: current stock x unit_cost from the raw_materials master.
        rm = money((await s.execute(text("""
            SELECT COALESCE(SUM(sl.current_stock * rm.unit_cost), 0)
              FROM stock_levels sl
              JOIN raw_materials rm ON rm.id = sl.raw_material_id
             WHERE sl.raw_material_id IS NOT NULL AND sl.current_stock > 0
               AND rm.unit_cost IS NOT NULL AND rm.unit_cost > 0
        """))).scalar())

        total = money(fg + rm)
        print(f"\nFinished goods on hand (est. cost): {fg:,.2f}")
        print(f"Raw materials on hand (est. cost):  {rm:,.2f}")
        print(f"Opening inventory total:            {total:,.2f}")

        if total <= 0:
            print("\nNothing to post (no valuable stock found).")
            await engine.dispose()
            return

        lines = []
        if fg > 0:
            lines.append(Line("1430", debit=fg,
                              description="Opening finished-goods inventory (est.)"))
        if rm > 0:
            lines.append(Line("1410", debit=rm,
                              description="Opening raw-material inventory (est.)"))
        lines.append(Line("3400", credit=total,
                          description="Opening inventory brought in as equity"))

        await post_entry(
            s,
            entry_date=__import__("datetime").date(2026, 3, 1),
            description="Opening inventory balance (estimated cost) — see "
                        "scripts/opening_balances.py; replace with a real "
                        "stock-take when available.",
            source_module="opening_balance",
            source_reference=REF,
            lines=lines,
        )

        bs = await balance_sheet(s)
        print("\n--- Balance sheet after opening inventory ---")
        print(f"  assets       {bs['assets']:,.2f}")
        print(f"  liabilities  {bs['liabilities']:,.2f}")
        print(f"  equity       {bs['total_equity']:,.2f}")
        print(f"  balanced: {bs['balanced']}")

        if commit:
            await s.commit()
            print("\nCOMMITTED.")
        else:
            await s.rollback()
            print("\nDRY RUN (rolled back).")

    await engine.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()
    asyncio.run(run(args.commit))
