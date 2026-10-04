"""Finding duplicate customers, and combining them without losing history.

DETECTION IS DERIVED, MERGING IS PERMANENT
==========================================
The candidate list is computed from the customer book every time it is opened,
so it shrinks as duplicates are resolved and nothing goes stale. The merge
itself is a deliberate act by a named person, recorded forever.

That asymmetry is the whole design. Two records on one phone number may be a
husband and wife, a hospital and the nurse who orders for it, or a shared
office line -- and merging those destroys a distinction no undo restores. The
system is confident enough to ask, never confident enough to decide.

HOW DUPLICATES ARE FOUND
========================
Three rules, each a fact rather than a guess:

  * **Same phone.** The last ten digits, which is what matches a Nigerian
    number however it was typed: +234 803..., 0803..., with spaces or dashes.
  * **Same email**, case-insensitive.
  * **Same name, in any word order.** Lowercased, stripped of punctuation,
    words sorted. This is what catches "Anyeneh Gloria" and "Gloria Anyeneh"
    as one person -- a case that exists seven times in the live book and that
    no exact-match rule would find.

Deliberately NOT fuzzy matching. "Zenith medical laboratory" and "Zennt
medical laboratory" are almost certainly the same business, but almost is the
wrong standard for an operation that rewrites order history. That pair is
caught by the phone rule instead, which is a fact.
"""
from __future__ import annotations

import json
import re
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Every table carrying a customer_id, taken from the foreign keys in the live
# database. A merge repoints all of them; one missing entry orphans that
# table's history against a customer nobody can open any more.
#
# Named explicitly rather than discovered at runtime so that adding a table
# with a customer_id and forgetting it here is caught by a test, not by a
# support call.
CUSTOMER_REFERENCES = [
    ("sales_orders", "customer_id"),
    ("invoices", "customer_id"),
    ("call_logs", "customer_id"),
    ("legacy_debts", "customer_id"),
    ("returned_stock", "customer_id"),
    ("recall_notifications", "customer_id"),
    ("field_visits", "customer_id"),
    ("wallet_expenses", "customer_id"),
    ("distributors", "customer_id"),
    ("customer_opportunity_actions", "customer_id"),
    ("distributor_registrations", "claimed_customer_id"),
    ("distributor_registrations", "linked_customer_id"),
]

MIN_REASON = 3


def normalise_phone(value: Optional[str]) -> Optional[str]:
    digits = re.sub(r"[^0-9]", "", value or "")
    return digits[-10:] if len(digits) >= 10 else None


def normalise_name(value: Optional[str]) -> Optional[str]:
    """Lowercase, punctuation stripped, words sorted.

    Sorting is what makes "Anyeneh Gloria" and "Gloria Anyeneh" the same key.
    """
    words = re.findall(r"[a-z0-9]+", (value or "").lower())
    return " ".join(sorted(words)) if words else None


async def candidates(session: AsyncSession) -> dict:
    """Groups of customer records that look like one customer.

    Computed live. Each group carries enough trade history for somebody to
    decide which record should survive -- normally the one with the orders on
    it.
    """
    rows = (await session.execute(text("""
        SELECT c.id, c.customer_code, c.name, c.phone, c.email, c.address,
               c.is_active, c.created_at,
               COALESCE(o.orders, 0)  AS orders,
               COALESCE(o.value, 0)   AS value,
               o.last_order,
               COALESCE(i.invoices, 0) AS invoices
          FROM customers c
     LEFT JOIN (SELECT customer_id, COUNT(*) AS orders,
                       SUM(total_amount) AS value, MAX(order_date) AS last_order
                  FROM sales_orders WHERE status <> 'cancelled'
                 GROUP BY customer_id) o ON o.customer_id = c.id
     LEFT JOIN (SELECT customer_id, COUNT(*) AS invoices
                  FROM invoices GROUP BY customer_id) i ON i.customer_id = c.id
         WHERE c.merged_into_id IS NULL
    """))).mappings().all()

    buckets: dict = {}
    for r in rows:
        keys = []
        phone = normalise_phone(r["phone"])
        if phone:
            keys.append(("phone", phone))
        if r["email"]:
            keys.append(("email", r["email"].strip().lower()))
        name = normalise_name(r["name"])
        if name and len(name) > 3:
            keys.append(("name", name))
        for k in keys:
            buckets.setdefault(k, []).append(r)

    # A record can match on phone AND name. Merge the overlapping buckets so
    # one real duplicate is offered once, not twice.
    groups: list = []
    placed: dict = {}
    for (kind, value), members in buckets.items():
        if len(members) < 2:
            continue
        ids = {str(m["id"]) for m in members}
        target = next((g for g in groups if g["ids"] & ids), None)
        if target is None:
            groups.append({"ids": set(ids), "reasons": {kind},
                           "rows": {str(m["id"]): m for m in members}})
        else:
            target["ids"] |= ids
            target["reasons"].add(kind)
            target["rows"].update({str(m["id"]): m for m in members})

    REASON_TEXT = {
        "phone": "same phone number",
        "email": "same email address",
        "name": "same name",
    }

    out = []
    for g in groups:
        members = sorted(g["rows"].values(),
                         key=lambda m: (-int(m["orders"] or 0),
                                        m["created_at"]))
        # The suggested survivor is the record with the most trade on it.
        # Suggested, not chosen: a person confirms.
        out.append({
            "reasons": sorted(REASON_TEXT[r] for r in g["reasons"]),
            "suggested_survivor_id": str(members[0]["id"]),
            "total_orders": sum(int(m["orders"] or 0) for m in members),
            "members": [{
                "customer_id": str(m["id"]),
                "customer_code": m["customer_code"],
                "name": m["name"],
                "phone": m["phone"],
                "email": m["email"],
                "address": m["address"],
                "is_active": bool(m["is_active"]),
                "orders": int(m["orders"] or 0),
                "invoices": int(m["invoices"] or 0),
                "value": float(m["value"] or 0),
                "last_order": (str(m["last_order"])[:10]
                               if m["last_order"] else None),
                "created_at": str(m["created_at"])[:10],
            } for m in members],
        })

    out.sort(key=lambda g: (-len(g["members"]), -g["total_orders"]))
    return {
        "groups": out,
        "group_count": len(out),
        "record_count": sum(len(g["members"]) for g in out),
        "note": (
            "Computed from the customer book every time this is opened, so a "
            "group disappears once it is merged. Nothing is merged "
            "automatically: two records on one phone number may be a hospital "
            "and the nurse who orders for it, and merging those cannot be "
            "undone cleanly. The suggested survivor is simply the record "
            "carrying the most orders."),
    }


async def merge(
    session: AsyncSession, *, surviving_id: UUID, merged_ids: list,
    reason: str, actor=None,
) -> dict:
    """Move every reference from the absorbed records onto the survivor.

    Refuses rather than guesses:
      * merging a record into itself;
      * a record that has already been absorbed, which would chain merges and
        leave history pointing at a customer nobody can open;
      * a survivor that is itself already merged away.
    """
    reason = (reason or "").strip()
    if len(reason) < MIN_REASON:
        raise HTTPException(
            status_code=400,
            detail=("A reason is required and kept on the record. Say why "
                    "these are the same customer."))
    if not merged_ids:
        raise HTTPException(
            status_code=400, detail="Select at least one record to merge in.")

    wanted = [str(m) for m in merged_ids]
    if str(surviving_id) in wanted:
        raise HTTPException(
            status_code=400,
            detail="A customer cannot be merged into itself.")

    survivor = (await session.execute(
        text("""SELECT id, name, merged_into_id FROM customers
                 WHERE id = :i FOR UPDATE"""),
        {"i": str(surviving_id)})).mappings().first()
    if survivor is None:
        raise HTTPException(status_code=404, detail="Surviving customer not found.")
    if survivor["merged_into_id"]:
        raise HTTPException(
            status_code=400,
            detail=("That record has itself already been merged into another "
                    "customer. Merge into the surviving one instead."))

    absorbed = (await session.execute(
        text("""SELECT id, name, customer_code, merged_into_id FROM customers
                 WHERE id = ANY(CAST(:ids AS uuid[])) FOR UPDATE"""),
        {"ids": wanted})).mappings().all()

    found = {str(a["id"]) for a in absorbed}
    missing = [m for m in wanted if m not in found]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"{len(missing)} customer record(s) not found.")

    already = [a["name"] for a in absorbed if a["merged_into_id"]]
    if already:
        raise HTTPException(
            status_code=400,
            detail=(f"Already merged, so nothing was changed: "
                    f"{'; '.join(already)}."))

    results = []
    for a in absorbed:
        moved: dict = {}
        for table, column in CUSTOMER_REFERENCES:
            res = await session.execute(
                text(f"UPDATE {table} SET {column} = :s WHERE {column} = :m"),
                {"s": str(surviving_id), "m": str(a["id"])})
            if res.rowcount:
                moved[f"{table}.{column}"] = res.rowcount

        await session.execute(
            text("""UPDATE customers
                       SET merged_into_id = :s, merged_at = NOW(),
                           merged_by = :by, is_active = FALSE
                     WHERE id = :m"""),
            {"s": str(surviving_id), "m": str(a["id"]),
             "by": str(actor.id) if actor is not None else None})

        await session.execute(
            text("""INSERT INTO customer_merges
                        (surviving_id, merged_id, surviving_name, merged_name,
                         merged_code, moved, reason, actor_id, actor_name)
                    VALUES (:s, :m, :sn, :mn, :mc, CAST(:moved AS jsonb), :r,
                            :ai, :an)"""),
            {"s": str(surviving_id), "m": str(a["id"]),
             "sn": survivor["name"], "mn": a["name"], "mc": a["customer_code"],
             "moved": json.dumps(moved), "r": reason,
             "ai": str(actor.id) if actor is not None else None,
             "an": ((getattr(actor, "full_name", None)
                     or getattr(actor, "username", None))
                    if actor is not None else None)})

        results.append({"customer_id": str(a["id"]), "name": a["name"],
                        "moved": moved,
                        "rows_moved": sum(moved.values())})

    total = sum(r["rows_moved"] for r in results)
    return {
        "surviving_id": str(surviving_id),
        "surviving_name": survivor["name"],
        "merged": results,
        "merged_count": len(results),
        "rows_moved": total,
        "note": (f"{len(results)} record(s) merged into {survivor['name']}, "
                 f"moving {total} rows of history. The absorbed records are "
                 f"kept and marked as merged, so an old invoice or link still "
                 f"resolves."),
    }


async def history(session: AsyncSession, *, customer_id: UUID) -> list:
    """Every merge that fed into this customer."""
    rows = (await session.execute(
        text("""SELECT merged_name, merged_code, moved, reason, actor_name,
                       created_at
                  FROM customer_merges WHERE surviving_id = :i
                 ORDER BY created_at DESC"""),
        {"i": str(customer_id)})).mappings().all()
    return [{"merged_name": r["merged_name"], "merged_code": r["merged_code"],
             "moved": r["moved"], "reason": r["reason"],
             "actor_name": r["actor_name"],
             "at": r["created_at"].isoformat() if r["created_at"] else None}
            for r in rows]
