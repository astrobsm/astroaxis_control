"""What needs attention across the whole distributor module, derived live.

WHY NOTHING HERE IS STORED
==========================
Every item is computed from the tables that already hold the truth. A stored
notification is a copy of a fact that lives elsewhere, and it begins rotting the
moment it is written -- the corrective action gets closed, the licence gets
renewed, the batch gets released, and the stored row still says otherwise. A
list that is wrong twice a week is a list people stop reading, which is worse
than not having built it.

So this module runs queries. It is slower than reading a table and it cannot be
stale.

SEVERITY MEANS WHAT IT SAYS
===========================
CRITICAL is reserved for things that are actively unsafe or actively losing
money right now: recalled stock still sitting in the field, a critical
corrective action left open. If everything urgent-ish were critical, the word
would stop carrying information and the genuinely dangerous item would sit in a
list of forty red rows.

CRITICAL items cannot be snoozed at all. Everything else can be snoozed, but
only for a period -- `snoozed_until` is NOT NULL in the schema, so there is no
dismiss-forever, and an item that is still true comes back.

THIS MODULE SENDS NOTHING
=========================
See migration e0123456789d. Push subscriptions live in a file that is destroyed
on every deploy, are not tied to users, and the only send path broadcasts to
everyone. Surfacing things in the app is honest; a "notification sent" record
for a message nobody received would not be.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
_ORDER = {s: i for i, s in enumerate(SEVERITIES)}

MAX_SNOOZE_DAYS = 90


def _item(key, severity, title, detail, *, category, entity_id=None,
          due=None, action=None) -> dict:
    return {
        "key": key, "severity": severity, "title": title, "detail": detail,
        "category": category,
        "entity_id": str(entity_id) if entity_id else None,
        "due": str(due) if due else None,
        "action": action,
        "snoozeable": severity != "CRITICAL",
    }


# ---------------------------------------------------------------------------
# The sources
# ---------------------------------------------------------------------------

async def _recalled_stock_still_out(session: AsyncSession) -> list[dict]:
    """Recalled batches with stock somewhere other than a company shelf.

    The most serious thing this module reports. A recalled batch the app has
    blocked from despatch is contained; a recalled batch sitting in a
    distributor's store is not, and somebody has to go and get it.
    """
    rows = (await session.execute(
        text("""
            SELECT b.id, b.batch_number, p.name AS product,
                   COUNT(DISTINCT w.id) AS locations,
                   COALESCE(SUM(CASE WHEN sm.movement_type IN
                       ('IN','RETURN','TRANSFER_IN','PRODUCTION_IN',
                        'DAMAGE_TRANSFER_IN','ADJUST_IN')
                       THEN sm.quantity ELSE -sm.quantity END), 0) AS on_hand
              FROM product_batches b
              JOIN products p ON p.id = b.product_id
              JOIN stock_movements sm ON sm.batch_id = b.id
              JOIN warehouses w ON w.id = sm.warehouse_id
             WHERE b.status = 'RECALLED'
             GROUP BY b.id, b.batch_number, p.name
            HAVING COALESCE(SUM(CASE WHEN sm.movement_type IN
                       ('IN','RETURN','TRANSFER_IN','PRODUCTION_IN',
                        'DAMAGE_TRANSFER_IN','ADJUST_IN')
                       THEN sm.quantity ELSE -sm.quantity END), 0) > 0
        """))).mappings().all()

    return [
        _item(f"recalled_stock:{r['id']}", "CRITICAL",
              f"Recalled batch {r['batch_number']} still has stock on hand",
              f"{r['product']}: {r['on_hand']} units across {r['locations']} "
              f"location(s). Despatch is blocked, but the goods have to be "
              f"collected or destroyed.",
              category="RECALL", entity_id=r["id"],
              action="Open the batch and work the recall trace")
        for r in rows
    ]


async def _critical_corrective_actions(session: AsyncSession) -> list[dict]:
    rows = (await session.execute(
        text("""SELECT ca.id, ca.action_reference, ca.finding, ca.severity,
                       ca.deadline, d.legal_name
                  FROM facility_corrective_actions ca
                  JOIN distributors d ON d.id = ca.distributor_id
                 WHERE ca.status <> 'CLOSED'
                   AND (ca.severity = 'CRITICAL'
                        OR ca.deadline < CURRENT_DATE)"""))).mappings().all()

    out = []
    for r in rows:
        overdue = r["deadline"] and r["deadline"] < date.today()
        severity = "CRITICAL" if r["severity"] == "CRITICAL" else "HIGH"
        out.append(_item(
            f"corrective_action:{r['id']}", severity,
            f"{'Overdue' if overdue else 'Critical'} corrective action at "
            f"{r['legal_name']}",
            (r["finding"] or "")[:200],
            category="COMPLIANCE", entity_id=r["id"], due=r["deadline"],
            action="Close it with evidence of the fix"))
    return out


async def _expiring_documents(session: AsyncSession, within: int) -> list[dict]:
    rows = (await session.execute(
        text("""SELECT doc.id, doc.doc_type, doc.expiry_date, d.legal_name,
                       (doc.expiry_date < CURRENT_DATE) AS expired
                  FROM distributor_documents doc
                  JOIN distributors d ON d.id = doc.distributor_id
                 WHERE doc.expiry_date IS NOT NULL
                   AND doc.expiry_date <= CURRENT_DATE + CAST(:w AS integer)
                   AND doc.verification_status <> 'REJECTED'
                   AND d.status IN ('ACTIVE','APPROVED')"""),
        {"w": within})).mappings().all()

    return [
        _item(f"document_expiry:{r['id']}",
              "HIGH" if r["expired"] else "MEDIUM",
              f"{r['doc_type'].replace('_', ' ').title()} "
              f"{'has expired' if r['expired'] else 'expires soon'} — "
              f"{r['legal_name']}",
              f"Expiry {r['expiry_date']}.",
              category="COMPLIANCE", entity_id=r["id"], due=r["expiry_date"],
              action="Collect the renewal and verify it")
        for r in rows
    ]


async def _expiring_batches(session: AsyncSession, within: int) -> list[dict]:
    rows = (await session.execute(
        text("""
            SELECT b.id, b.batch_number, b.expiry_date, p.name AS product,
                   (b.expiry_date < CURRENT_DATE) AS expired,
                   COALESCE(SUM(CASE WHEN sm.movement_type IN
                       ('IN','RETURN','TRANSFER_IN','PRODUCTION_IN',
                        'DAMAGE_TRANSFER_IN','ADJUST_IN')
                       THEN sm.quantity ELSE -sm.quantity END), 0) AS on_hand
              FROM product_batches b
              JOIN products p ON p.id = b.product_id
              JOIN stock_movements sm ON sm.batch_id = b.id
             WHERE b.expiry_date IS NOT NULL
               AND b.expiry_date <= CURRENT_DATE + CAST(:w AS integer)
               AND b.status NOT IN ('CONSUMED','RECALLED')
             GROUP BY b.id, b.batch_number, b.expiry_date, p.name
            HAVING COALESCE(SUM(CASE WHEN sm.movement_type IN
                       ('IN','RETURN','TRANSFER_IN','PRODUCTION_IN',
                        'DAMAGE_TRANSFER_IN','ADJUST_IN')
                       THEN sm.quantity ELSE -sm.quantity END), 0) > 0
        """), {"w": within})).mappings().all()

    return [
        _item(f"batch_expiry:{r['id']}",
              "HIGH" if r["expired"] else "MEDIUM",
              f"Batch {r['batch_number']} "
              f"{'has expired' if r['expired'] else 'expires soon'} with stock "
              f"on hand",
              f"{r['product']}: {r['on_hand']} units, expiry "
              f"{r['expiry_date']}."
              + (" Despatch is already blocked." if r["expired"] else ""),
              category="STOCK", entity_id=r["id"], due=r["expiry_date"],
              action="Write off, return, or sell through first")
        for r in rows
    ]


async def _applications_waiting(session: AsyncSession) -> list[dict]:
    rows = (await session.execute(
        text("""SELECT a.id, a.application_number, a.submitted_at,
                       d.legal_name, t.code AS territory
                  FROM distributor_applications a
                  JOIN distributors d ON d.id = a.distributor_id
                  LEFT JOIN territories t ON t.id = a.territory_id
                 WHERE a.status IN ('SUBMITTED','UNDER_REVIEW')
                   AND a.kind = 'TERRITORY'"""))).mappings().all()

    out = []
    for r in rows:
        waiting = ((datetime.now(timezone.utc) - r["submitted_at"]).days
                   if r["submitted_at"] else 0)
        out.append(_item(
            f"application_waiting:{r['id']}",
            "HIGH" if waiting > 14 else "MEDIUM",
            f"{r['legal_name']} is waiting on {r['territory'] or 'a territory'}",
            f"{r['application_number']} submitted {waiting} day(s) ago.",
            category="TERRITORY", entity_id=r["id"],
            action="Review and decide it"))
    return out


async def _unverified_sales(session: AsyncSession) -> list[dict]:
    """Claims nobody has checked, and the discrepancies among them."""
    row = (await session.execute(
        text("""SELECT COUNT(*) AS n,
                       COUNT(*) FILTER (WHERE stock_discrepancy) AS mismatched,
                       COALESCE(SUM(total_amount), 0) AS amount
                  FROM distributor_sales
                 WHERE provenance = 'REPORTED'
                   AND sold_on >= CURRENT_DATE - 90"""))).mappings().first()

    out = []
    if row["n"]:
        out.append(_item(
            "unverified_sales:rolling90", "MEDIUM",
            f"{row['n']} reported sale(s) have not been checked",
            f"NGN {Decimal(row['amount']):,.2f} claimed in the last 90 days "
            f"and counting toward nothing until somebody verifies it.",
            category="SELL_THROUGH",
            action="Verify against invoices, or dispute"))
    if row["mismatched"]:
        out.append(_item(
            "sales_stock_mismatch:rolling90", "HIGH",
            f"{row['mismatched']} sale(s) report more sold than was shipped",
            "Either the shipment record is incomplete or the report is "
            "inflated. Both are worth finding out.",
            category="SELL_THROUGH",
            action="Compare shipments against the reported sales"))
    return out


async def _performance_reviews(session: AsyncSession) -> list[dict]:
    rows = (await session.execute(
        text("""SELECT r.id, r.review_reference, r.opened_on, d.legal_name
                  FROM distributor_performance_reviews r
                  JOIN distributors d ON d.id = r.distributor_id
                 WHERE r.status IN ('OPEN','IN_PROGRESS')"""))).mappings().all()

    return [
        _item(f"review_open:{r['id']}",
              "HIGH" if (date.today() - r["opened_on"]).days > 30 else "MEDIUM",
              f"Performance review open for {r['legal_name']}",
              f"{r['review_reference']} opened {r['opened_on']} "
              f"({(date.today() - r['opened_on']).days} days ago).",
              category="PERFORMANCE", entity_id=r["id"],
              action="Close it with an outcome")
        for r in rows
    ]


async def _expiring_order_links(session: AsyncSession) -> list[dict]:
    rows = (await session.execute(
        text("""SELECT l.id, l.label, l.expires_at, d.legal_name
                  FROM distributor_order_links l
                  JOIN distributors d ON d.id = l.distributor_id
                 WHERE l.revoked_at IS NULL
                   AND l.expires_at BETWEEN NOW() AND NOW() + INTERVAL '14 days'
                   AND d.status = 'ACTIVE'"""))).mappings().all()

    return [
        _item(f"link_expiring:{r['id']}", "LOW",
              f"Ordering link for {r['legal_name']} expires soon",
              f"\"{r['label']}\" expires "
              f"{r['expires_at']:%d %b %Y}. They will not be able to order "
              f"after that.",
              category="ORDERING", entity_id=r["id"],
              action="Issue a replacement before it lapses")
        for r in rows
    ]


async def _unfit_but_trading(session: AsyncSession) -> list[dict]:
    """Active distributors with no agreement in force.

    Trading without a signed agreement is a commercial exposure that nothing
    else on the screen would surface.
    """
    rows = (await session.execute(
        text("""SELECT d.id, d.legal_name
                  FROM distributors d
                 WHERE d.status = 'ACTIVE'
                   AND NOT EXISTS (
                       SELECT 1 FROM distributor_agreements a
                        WHERE a.distributor_id = d.id
                          AND a.status = 'ACTIVE')"""))).mappings().all()

    return [
        _item(f"no_agreement:{r['id']}", "HIGH",
              f"{r['legal_name']} is trading with no agreement in force",
              "There is no signed contract governing this relationship.",
              category="COMPLIANCE", entity_id=r["id"],
              action="Draft, issue and countersign an agreement")
        for r in rows
    ]


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------

async def attention_items(
    session: AsyncSession, *, user=None, within_days: int = 60,
    include_snoozed: bool = False, severity: Optional[str] = None,
) -> dict:
    """Everything needing attention, computed now.

    Nothing here is read from a notifications table, because there is not one.
    See the module docstring.
    """
    groups = [
        await _recalled_stock_still_out(session),
        await _critical_corrective_actions(session),
        await _expiring_documents(session, within_days),
        await _expiring_batches(session, within_days),
        await _applications_waiting(session),
        await _unverified_sales(session),
        await _performance_reviews(session),
        await _expiring_order_links(session),
        await _unfit_but_trading(session),
    ]
    items = [item for group in groups for item in group]

    snoozed_keys: dict = {}
    if user is not None:
        rows = (await session.execute(
            text("""SELECT item_key, snoozed_until FROM
                        attention_acknowledgements
                     WHERE user_id = :u AND snoozed_until > NOW()"""),
            {"u": str(user.id)})).mappings().all()
        snoozed_keys = {r["item_key"]: r["snoozed_until"] for r in rows}

    shown, hidden = [], 0
    for item in items:
        until = snoozed_keys.get(item["key"])
        # A CRITICAL item is shown even if it was snoozed while it was minor.
        # Severity is recomputed every time, so an item that has got worse
        # reappears rather than staying hidden under an old decision.
        if until and item["severity"] != "CRITICAL" and not include_snoozed:
            hidden += 1
            continue
        if until:
            item = {**item, "snoozed_until": until.isoformat()}
        if severity and item["severity"] != severity:
            continue
        shown.append(item)

    shown.sort(key=lambda i: (_ORDER[i["severity"]], i["due"] or "9999"))

    counts = {s: sum(1 for i in shown if i["severity"] == s)
              for s in SEVERITIES}

    return {
        "items": shown,
        "counts": counts,
        "total": len(shown),
        "snoozed_hidden": hidden,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "note": ("This list is computed from live data every time it is opened. "
                 "Nothing here is stored, so nothing can be out of date -- an "
                 "item disappears the moment the underlying problem is fixed. "
                 "The app does not send these anywhere; see the module notes on "
                 "why push notifications would reach the wrong people."),
    }


async def snooze(
    session: AsyncSession, *, item_key: str, days: int, reason: Optional[str],
    user,
) -> dict:
    """Hide one item from one person, for a bounded time.

    Refuses CRITICAL items: recalled stock in the field is not something one
    person gets to decide nobody else needs to see.
    """
    if user is None:
        raise HTTPException(status_code=403, detail="A user is required.")
    if not 1 <= days <= MAX_SNOOZE_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"Snooze between 1 and {MAX_SNOOZE_DAYS} days. There is no "
                   f"permanent dismissal: an item that is still true comes "
                   f"back.")

    current = await attention_items(session, user=user, include_snoozed=True)
    match = next((i for i in current["items"] if i["key"] == item_key), None)
    if match is None:
        raise HTTPException(
            status_code=404,
            detail="That item is not on the list; it may already be resolved.")
    if match["severity"] == "CRITICAL":
        raise HTTPException(
            status_code=403,
            detail=("Critical items cannot be snoozed. Fix the underlying "
                    "problem and it disappears on its own."))

    until = datetime.now(timezone.utc) + timedelta(days=days)
    await session.execute(
        text("""
            INSERT INTO attention_acknowledgements
                (id, item_key, user_id, snoozed_until, reason,
                 severity_at_snooze)
            VALUES (gen_random_uuid(), :k, :u, :until, :r, :sev)
            ON CONFLICT (item_key, user_id) DO UPDATE
               SET snoozed_until = EXCLUDED.snoozed_until,
                   reason = EXCLUDED.reason,
                   severity_at_snooze = EXCLUDED.severity_at_snooze
        """),
        {"k": item_key, "u": str(user.id), "until": until, "r": reason,
         "sev": match["severity"]},
    )
    return {"item_key": item_key, "snoozed_until": until.isoformat(),
            "note": "It will reappear then, or sooner if it becomes critical."}


async def unsnooze(session: AsyncSession, *, item_key: str, user) -> dict:
    await session.execute(
        text("""DELETE FROM attention_acknowledgements
                 WHERE item_key = :k AND user_id = :u"""),
        {"k": item_key, "u": str(user.id)})
    return {"item_key": item_key, "snoozed": False}
