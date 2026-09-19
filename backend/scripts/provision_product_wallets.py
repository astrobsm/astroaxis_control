"""Give every product its own virtual wallet, then distribute what is waiting.

WHY THIS EXISTS
===============
MAPD routes each product's share of a payment to the account that product's
money belongs to. Until a product is mapped to an account it cannot be routed,
so the payment is recorded as SKIPPED -- the money is banked, but nothing knows
whose share is whose. On the production database every payment ever taken was
in that state.

This script does the configuration that was missing, in one pass:

  1. a VIRTUAL financial account per active product, named after the product;
  2. `product_accounts.default_financial_account_id` pointing at it;
  3. a retry, so the payments already taken flow through the same path any new
     payment would.

WHY VIRTUAL
===========
Nothing here is a bank account. The money is in the company's bank and stays
there; a virtual account records WHOSE SHARE it is. That is why these post
nothing to the general ledger -- see `_post_settlement_entry`. A wallet that
posted would credit the bank for cash still sitting in it.

SAFE TO RUN TWICE
=================
Every step is checked before it is taken: an existing account for a product is
reused, an existing mapping is left alone, and a payment that has already been
distributed is not distributed again -- the database refuses a second live
settlement per payment regardless of what this script thinks.

    python scripts/provision_product_wallets.py            # show the plan
    python scripts/provision_product_wallets.py --apply    # do it
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from decimal import Decimal

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text                                    # noqa: E402

from app.db import AsyncSessionLocal                           # noqa: E402
from app.services.settlement import (                          # noqa: E402
    retry_failed_settlements, settlement_health)

# Where the money actually is. A virtual account posts nothing, so this is
# documentation of where the cash sits rather than an instruction to move it --
# but it must be truthful, because changing the account to a real one later
# would start posting against whatever is recorded here.
CASH_AT_BANK = "1200"


def account_code(name: str, taken: set[str]) -> str:
    """A readable code from the product name, unique among those taken.

    Readable because it appears in every settlement report and rule listing;
    a person should not have to look up what FA-7 was.
    """
    words = [w for w in re.split(r"[^A-Za-z0-9]+", name.upper()) if w]
    base = ("".join(w[:4] for w in words[:3])[:12]) if words else "PROD"
    if base not in taken:
        taken.add(base)
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    taken.add(f"{base}-{n}")
    return f"{base}-{n}"


async def main(apply: bool) -> int:
    async with AsyncSessionLocal() as session:
        products = (await session.execute(text("""
            SELECT p.id, p.sku, p.name,
                   pa.default_financial_account_id AS mapped_to,
                   fa.id AS existing_account_id
              FROM products p
              LEFT JOIN product_accounts pa ON pa.product_id = p.id
              LEFT JOIN financial_accounts fa
                     ON upper(fa.name) = upper(p.name)
             WHERE COALESCE(p.is_active, TRUE)
             ORDER BY p.name
        """))).mappings().all()

        taken = set((await session.execute(
            text("SELECT code FROM financial_accounts"))).scalars().all())

        created, mapped, untouched = [], [], []

        for product in products:
            account_id = product["existing_account_id"]
            if account_id is None:
                code = account_code(product["name"], taken)
                if apply:
                    account_id = (await session.execute(text("""
                        INSERT INTO financial_accounts
                            (id, code, name, account_kind, gl_account_code,
                             status, description)
                        VALUES (gen_random_uuid(), :c, :n, 'VIRTUAL', :gl,
                                'ACTIVE',
                                'Auto-created: this product''s share of each '
                                'payment. Virtual -- the money stays in the '
                                'bank; this records whose share it is.')
                        RETURNING id
                    """), {"c": code, "n": product["name"],
                           "gl": CASH_AT_BANK})).scalar()
                created.append((code, product["name"]))
            else:
                untouched.append(product["name"])

            if product["mapped_to"] is not None:
                continue
            if apply:
                await session.execute(text("""
                    INSERT INTO product_accounts
                        (id, product_id, default_financial_account_id)
                    VALUES (gen_random_uuid(), :p, :a)
                    ON CONFLICT (product_id) DO UPDATE
                       SET default_financial_account_id = EXCLUDED
                           .default_financial_account_id
                """), {"p": str(product["id"]), "a": str(account_id)})
            mapped.append(product["name"])

        print(f"products:            {len(products)}")
        print(f"accounts to create:  {len(created)}")
        print(f"accounts reused:     {len(untouched)}")
        print(f"products to map:     {len(mapped)}")
        for code, name in created:
            print(f"   + {code:<14} {name}")

        if not apply:
            print("\nDry run. Nothing written. Re-run with --apply.")
            return 0

        await session.commit()
        print("\nConfiguration written.")

        # Now the backlog. In batches, because the retry takes a limit, and
        # until it stops finding work -- a payment whose products are still
        # unmapped writes nothing, so this cannot spin.
        settled = attempted = still = 0
        while True:
            outcome = await retry_failed_settlements(session, limit=100)
            await session.commit()
            if outcome["attempted"] == 0:
                break
            attempted += outcome["attempted"]
            settled += outcome["settled"]
            still += outcome["still_failing"]
            print(f"   ... attempted {attempted}, settled {settled}")
            if outcome["settled"] == 0:
                # Nothing moved this round; another pass would do the same.
                break

        print(f"\nretried:  {attempted}")
        print(f"settled:  {settled}")
        print(f"unsettled:{still}")

        health = await settlement_health(session)
        print(f"\nundistributed payments now: "
              f"{health['undistributed_payments']} "
              f"(value {Decimal(str(health['undistributed_value'])):,.2f})")
        print(f"healthy: {health['healthy']}")
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write the configuration and distribute")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.apply)))
