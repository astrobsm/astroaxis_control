#!/usr/bin/env python
"""Backfill the general ledger from existing operational history.

Posts a journal entry for every historical sale and customer payment already
in the database, so the ledger reflects the trading that happened before the
accounting engine was switched on. Idempotent: the ledger's own guard
(already_posted on source_reference) means re-running posts nothing twice.

IMPORTANT -- read before running against production:
  * Historical COGS is a BACKFILL ESTIMATE (cost_source='backfill_estimate'),
    not an audited figure. Posting it makes the ledger's gross-profit an
    estimate too. That is stated in each entry's provenance, not hidden.
  * Posting credits Finished Goods for COGS. Without an opening inventory
    balance the inventory account will go negative -- which is why this script
    also reports the resulting trial balance and balance sheet, so the operator
    can see whether opening balances are needed before trusting the books.
  * Requires ACCOUNTING_POSTING_ENABLED=true and an ACCOUNTING_CUTOVER_DATE at
    or before the earliest transaction, or nothing posts.

Usage:
    ACCOUNTING_POSTING_ENABLED=true ACCOUNTING_CUTOVER_DATE=2026-01-01 \
    DATABASE_URL=... python scripts/backfill_ledger.py            # report only
    ... python scripts/backfill_ledger.py --commit                # write
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

from app.services.posting import post_sale, post_customer_payment  # noqa: E402
from app.services.ledger import (                             # noqa: E402
    trial_balance, profit_and_loss, balance_sheet, cash_position)


async def run(commit: bool, include_cogs: bool) -> None:
    url = os.getenv("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL not set.")
    if os.getenv("ACCOUNTING_POSTING_ENABLED", "").lower() not in (
            "1", "true", "yes", "on"):
        sys.exit("ACCOUNTING_POSTING_ENABLED must be true to backfill.")

    engine = create_async_engine(url, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    posted_sales = skipped_sales = 0
    posted_pays = skipped_pays = 0

    async with maker() as s:
        orders = (await s.execute(text(
            "SELECT id FROM sales_orders ORDER BY order_date, id"))).fetchall()
        for (oid,) in orders:
            entry = await post_sale(s, order_id=oid, include_cogs=include_cogs)
            if entry:
                posted_sales += 1
            else:
                skipped_sales += 1
        # Payments reference invoices; post each as cash received.
        pays = (await s.execute(text(
            "SELECT id FROM payments ORDER BY payment_date, id"))).fetchall()
        for (pid,) in pays:
            entry = await post_customer_payment(s, payment_id=pid)
            if entry:
                posted_pays += 1
            else:
                skipped_pays += 1

        print(f"\nSales   : posted {posted_sales}, skipped {skipped_sales}")
        print(f"Payments: posted {posted_pays}, skipped {skipped_pays}")
        print("  (skipped = disabled/before-cutover/already-posted/zero-value)")

        # Report the resulting books from the SAME (uncommitted or committed)
        # session so a dry run still shows what would happen.
        tb = await trial_balance(s)
        pnl = await profit_and_loss(s)
        bs = await balance_sheet(s)
        cash = await cash_position(s)

        print("\n--- Trial balance ---")
        print(f"  total debit  {tb['total_debit']:,.2f}")
        print(f"  total credit {tb['total_credit']:,.2f}")
        print(f"  balanced: {tb['balanced']}")
        print("\n--- Profit & Loss ---")
        print(f"  income       {pnl['total_income']:,.2f}")
        print(f"  expenses     {pnl['total_expenses']:,.2f}")
        print(f"  net profit   {pnl['net_profit']:,.2f}  "
              f"(COGS is a BACKFILL ESTIMATE)")
        print("\n--- Balance sheet ---")
        print(f"  assets       {bs['assets']:,.2f}")
        print(f"  liabilities  {bs['liabilities']:,.2f}")
        print(f"  equity       {bs['total_equity']:,.2f}")
        print(f"  balanced: {bs['balanced']}  diff {bs['difference']:,.2f}")
        print("\n--- Key account balances ---")
        for a in tb["accounts"]:
            if a["code"] in ("1100", "1200", "1300", "1430", "4100", "5100"):
                print(f"  {a['code']} {a['name']:<24} {a['balance']:>16,.2f}")

        # Persist or discard AFTER reporting, so a dry run still shows the
        # books it would produce.
        if commit:
            await s.commit()
        else:
            await s.rollback()
        print(f"\n{'COMMITTED' if commit else 'DRY RUN (rolled back)'}")

    await engine.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--no-cogs", action="store_true",
                    help="post only the revenue leg of each sale (leave the "
                         "estimated cost/inventory side out)")
    args = ap.parse_args()
    asyncio.run(run(args.commit, include_cogs=not args.no_cogs))
