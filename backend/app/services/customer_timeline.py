"""Everything that has ever happened with one customer, in order.

NO MIGRATION, NO NEW TABLE, NOTHING STORED
==========================================
This module adds no schema at all. Every event it shows already exists
somewhere in the database -- an order, an invoice, a payment, a quotation, a
delivery, a phone call, a consent change -- and the timeline is those sources
read together and sorted by time.

That is not a shortcut, it is the only correct build. A stored "customer
activity" table would be a copy of eleven other tables, and it would be wrong
within a week: an order gets cancelled, an invoice gets paid, a delivery
fails, and the copy still says otherwise. The same reasoning as the attention
list and the opportunity queue, both of which this codebase already settled.

WHAT IT IS FOR
==============
Today: a member of customer service answering "what has happened with this
hospital?" without opening six screens and still missing the phone call.

Later: it is the context an AI agent needs. An agent that can see a customer
ordered twice this month, has an invoice 40 days overdue, and asked us to stop
sending promotional messages will behave very differently from one that can
only see the message in front of it. Building it now means that context exists
before anything needs it, and can be read by a person first.

WHAT IT DELIBERATELY LEAVES OUT
===============================
Internal states nobody outside the company would recognise, and anything that
is about the company rather than the customer: stock reservations, opportunity
findings nobody acted on, draft quotations never sent. A timeline that
includes everything is a log; a timeline that includes what happened BETWEEN
the company and the customer is a history.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Shown when two events share a timestamp, so a sensible order survives
# same-day data: an order precedes the invoice for it, which precedes the
# payment.
KIND_ORDER = {
    "ORDER": 0, "QUOTATION": 1, "INVOICE": 2, "PAYMENT": 3,
    "DELIVERY": 4, "CALL": 5, "VISIT": 6, "MESSAGE": 7,
    "CONSENT": 8, "RECALL": 9, "MERGE": 10, "OUTREACH": 11,
}


def _money(v) -> float:
    return float(Decimal(str(v or 0)).quantize(Decimal("0.01")))


def _event(kind, at, title, *, detail=None, amount=None, actor=None,
           reference=None, tone="neutral") -> dict:
    return {
        "kind": kind,
        "at": at.isoformat() if hasattr(at, "isoformat") else str(at),
        "title": title,
        "detail": detail,
        "amount": _money(amount) if amount is not None else None,
        "actor": actor,
        "reference": reference,
        "tone": tone,
    }


async def _exists(session: AsyncSession, table: str, *columns: str) -> bool:
    """Whether a table is present WITH the columns a query needs.

    The timeline reads eleven tables, several of which arrived in recent
    migrations. A deployment part-way through the chain should show the
    history it has rather than a 500.

    Checking columns and not only the table matters: a table can exist from an
    earlier migration without the column a later one added, and that fails at
    query time with an error naming a column rather than a feature -- which
    reads like a bug rather than a part-applied migration.

    Every caller names EVERY column its query selects, not a sample. Naming a
    sample is how the first version of this guard still failed, on a different
    column, after being fixed for the first one.
    """
    if (await session.execute(
            text("SELECT to_regclass(:t)"), {"t": table})).scalar() is None:
        return False
    if not columns:
        return True
    found = {r[0] for r in (await session.execute(
        text("""SELECT column_name FROM information_schema.columns
                 WHERE table_name = :t"""), {"t": table})).fetchall()}
    return set(columns).issubset(found)


async def timeline(
    session: AsyncSession, *, customer_id: UUID, limit: int = 200,
) -> dict:
    customer = (await session.execute(text("""
        SELECT c.id, c.customer_code, c.name, c.phone, c.email, c.address,
               c.is_active, c.created_at, c.merged_into_id,
               c.marketing_consent, c.do_not_contact, c.whatsapp_number,
               c.customer_type, s.first_name AS rep_first, s.last_name AS rep_last
          FROM customers c
     LEFT JOIN staff s ON s.id = c.assigned_staff_id
         WHERE c.id = :i
    """), {"i": str(customer_id)})).mappings().first()
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found.")

    events: list = []

    # ---- orders ----------------------------------------------------------
    for r in (await session.execute(text("""
        SELECT order_number, order_date, status, total_amount, sales_channel
          FROM sales_orders WHERE customer_id = :i
    """), {"i": str(customer_id)})).mappings().all():
        events.append(_event(
            "ORDER", r["order_date"], f"Order {r['order_number']}",
            detail=(f"{r['status']}"
                    + (f" · {r['sales_channel'].lower()}"
                       if r["sales_channel"] else "")),
            amount=r["total_amount"], reference=r["order_number"],
            tone="danger" if r["status"] == "cancelled" else "info"))

    # ---- invoices and what was paid against them -------------------------
    for r in (await session.execute(text("""
        SELECT invoice_number, invoice_date, due_date, status, total_amount,
               COALESCE(paid_amount, 0) AS paid
          FROM invoices WHERE customer_id = :i
    """), {"i": str(customer_id)})).mappings().all():
        outstanding = Decimal(str(r["total_amount"] or 0)) - Decimal(str(r["paid"]))
        # due_date is TIMESTAMPTZ in this database, so it carries a .date().
        # Guarded anyway because this module reads eleven tables and a type
        # that changes under it should dim a badge, not raise.
        due = r["due_date"]
        overdue = bool(
            due is not None and outstanding > 0
            and getattr(due, "date", lambda: None)()
            and due.date() < datetime.now(timezone.utc).date())
        events.append(_event(
            "INVOICE", r["invoice_date"], f"Invoice {r['invoice_number']}",
            detail=(f"{r['status']}"
                    + (f" · {_money(outstanding):,.2f} outstanding"
                       if outstanding > 0 else "")),
            amount=r["total_amount"], reference=r["invoice_number"],
            tone="danger" if overdue else "info"))

    for r in (await session.execute(text("""
        SELECT p.payment_date, p.amount, p.payment_method, p.reference,
               i.invoice_number
          FROM payments p JOIN invoices i ON i.id = p.invoice_id
         WHERE i.customer_id = :i
    """), {"i": str(customer_id)})).mappings().all():
        events.append(_event(
            "PAYMENT", r["payment_date"],
            f"Paid against {r['invoice_number']}",
            detail=(r["payment_method"] or "").replace("_", " "),
            amount=r["amount"], reference=r["reference"], tone="success"))

    # ---- quotations -------------------------------------------------------
    if (await _exists(session, "quotations", "customer_id", "total_amount",
                             "quotation_number")
            and await _exists(session, "quotation_events", "quotation_id",
                             "to_status", "created_at", "note",
                             "actor_name")):
        for r in (await session.execute(text("""
            SELECT q.quotation_number, e.to_status, e.created_at, e.note,
                   e.actor_name, q.total_amount
              FROM quotation_events e
              JOIN quotations q ON q.id = e.quotation_id
             WHERE q.customer_id = :i AND e.to_status <> 'DRAFT'
        """), {"i": str(customer_id)})).mappings().all():
            events.append(_event(
                "QUOTATION", r["created_at"],
                f"Quotation {r['quotation_number']} {r['to_status'].lower()}",
                detail=r["note"], amount=r["total_amount"],
                actor=r["actor_name"], reference=r["quotation_number"],
                tone=("success" if r["to_status"] in ("ACCEPTED", "CONVERTED")
                      else "danger" if r["to_status"] == "DECLINED"
                      else "info")))

    # ---- deliveries -------------------------------------------------------
    for r in (await session.execute(text("""
        SELECT mc.status, mc.delivered_at, mc.failed_at, mc.failure_reason,
               mc.receiver_name, dm.manifest_number, dm.delivery_date
          FROM manifest_customers mc
          JOIN delivery_manifests dm ON dm.id = mc.manifest_id
         WHERE mc.customer_id = :i
    """), {"i": str(customer_id)})).mappings().all():
        when = r["delivered_at"] or r["failed_at"] or r["delivery_date"]
        if when is None:
            continue
        failed = r["status"] in ("failed", "returned")
        events.append(_event(
            "DELIVERY", when,
            ("Delivery failed" if failed
             else "Delivered" if r["status"] == "delivered"
             else f"Delivery {r['status'].replace('_', ' ')}"),
            detail=(r["failure_reason"] if failed
                    else (f"received by {r['receiver_name']}"
                          if r["receiver_name"] else None)),
            reference=r["manifest_number"],
            tone="danger" if failed else "success"))

    # ---- calls ------------------------------------------------------------
    if await _exists(session, "call_logs", "customer_id", "user_id",
                             "direction", "channel", "duration_seconds",
                             "notes", "outcome", "created_at"):
        for r in (await session.execute(text("""
            SELECT cl.created_at, cl.direction, cl.channel,
                   cl.duration_seconds, cl.notes, cl.outcome,
                   u.full_name AS staff_name
              FROM call_logs cl
         LEFT JOIN users u ON u.id = cl.user_id
             WHERE cl.customer_id = :i
        """), {"i": str(customer_id)})).mappings().all():
            mins = int((r["duration_seconds"] or 0) // 60)
            events.append(_event(
                "CALL", r["created_at"],
                f"{(r['direction'] or 'call').title()} "
                f"{(r['channel'] or '').lower() or 'call'}",
                detail=((f"{mins} min" if mins else None)
                        or r["outcome"] or r["notes"]),
                actor=r["staff_name"], tone="neutral"))

    # ---- field visits -----------------------------------------------------
    if await _exists(session, "field_visits", "customer_id", "marketer_id",
                             "started_at", "purpose", "outcome",
                             "order_value", "place_name"):
        for r in (await session.execute(text("""
            SELECT v.started_at, v.purpose, v.outcome, v.order_value,
                   v.place_name, m.full_name
              FROM field_visits v
         LEFT JOIN distributor_marketers m ON m.id = v.marketer_id
             WHERE v.customer_id = :i
        """), {"i": str(customer_id)})).mappings().all():
            events.append(_event(
                "VISIT", r["started_at"],
                f"{(r['purpose'] or 'visit').title()} — {r['place_name']}",
                detail=r["outcome"], amount=r["order_value"],
                actor=r["full_name"], tone="neutral"))

    # ---- what we sent, and whether it went --------------------------------
    if await _exists(session, "outbound_messages", "customer_id",
                             "created_at", "channel", "category",
                             "status", "blocked_reason", "reason",
                             "body", "created_by_name"):
        for r in (await session.execute(text("""
            SELECT created_at, channel, category, status, blocked_reason,
                   reason, body, created_by_name
              FROM outbound_messages WHERE customer_id = :i
        """), {"i": str(customer_id)})).mappings().all():
            blocked = r["status"] == "BLOCKED"
            events.append(_event(
                "MESSAGE", r["created_at"],
                (f"{r['channel'].title()} message "
                 + ("refused" if blocked else r["status"].lower())),
                detail=(r["blocked_reason"] if blocked else r["reason"]),
                actor=r["created_by_name"],
                tone="warning" if blocked else "neutral"))

    # ---- consent ----------------------------------------------------------
    if await _exists(session, "customer_consent_events", "customer_id",
                             "created_at", "consent", "source",
                             "evidence", "actor_name"):
        for r in (await session.execute(text("""
            SELECT created_at, consent, source, evidence, actor_name
              FROM customer_consent_events WHERE customer_id = :i
        """), {"i": str(customer_id)})).mappings().all():
            events.append(_event(
                "CONSENT", r["created_at"],
                f"Marketing consent: {r['consent'].replace('_', ' ').lower()}",
                detail=(f"{r['source'].replace('_', ' ').lower()}"
                        + (f" · {r['evidence']}" if r["evidence"] else "")),
                actor=r["actor_name"],
                tone="danger" if r["consent"] == "OPTED_OUT" else "success"))

    # ---- recalls ----------------------------------------------------------
    if await _exists(session, "recall_notifications", "customer_id",
                             "notified_at", "channel", "recall_id"):
        for r in (await session.execute(text("""
            SELECT rn.notified_at, rn.channel, pr.recall_reference, pr.reason
              FROM recall_notifications rn
         LEFT JOIN product_recalls pr ON pr.id = rn.recall_id
             WHERE rn.customer_id = :i
        """), {"i": str(customer_id)})).mappings().all():
            events.append(_event(
                "RECALL", r["notified_at"],
                f"Told about recall {r['recall_reference'] or ''}".strip(),
                detail=r["reason"], tone="warning"))

    # ---- records merged into this one -------------------------------------
    if await _exists(session, "customer_merges", "surviving_id",
                             "created_at", "merged_name", "merged_code",
                             "reason", "actor_name"):
        for r in (await session.execute(text("""
            SELECT created_at, merged_name, merged_code, reason, actor_name
              FROM customer_merges WHERE surviving_id = :i
        """), {"i": str(customer_id)})).mappings().all():
            events.append(_event(
                "MERGE", r["created_at"],
                f"Duplicate record merged in: {r['merged_name']}",
                detail=r["reason"], actor=r["actor_name"], tone="neutral"))

    # ---- what staff did about an opportunity ------------------------------
    if await _exists(session, "customer_opportunity_actions", "customer_id",
                             "created_at", "opportunity_type", "outcome",
                             "note", "actor_name", "reason_at_action"):
        for r in (await session.execute(text("""
            SELECT created_at, opportunity_type, outcome, note, actor_name,
                   reason_at_action
              FROM customer_opportunity_actions WHERE customer_id = :i
        """), {"i": str(customer_id)})).mappings().all():
            events.append(_event(
                "OUTREACH", r["created_at"],
                (f"{r['opportunity_type'].replace('_', ' ').title()} "
                 f"— {r['outcome'].replace('_', ' ').lower()}"),
                detail=r["note"] or r["reason_at_action"],
                actor=r["actor_name"],
                tone="success" if r["outcome"] == "CONVERTED" else "neutral"))

    events.sort(key=lambda e: (e["at"], KIND_ORDER.get(e["kind"], 99)),
                reverse=True)

    rep = " ".join(x for x in (customer["rep_first"], customer["rep_last"]) if x)

    return {
        "customer": {
            "id": str(customer["id"]),
            "customer_code": customer["customer_code"],
            "name": customer["name"],
            "phone": customer["phone"],
            "whatsapp": customer["whatsapp_number"],
            "email": customer["email"],
            "address": customer["address"],
            "type": customer["customer_type"],
            "is_active": bool(customer["is_active"]),
            "assigned_rep": rep or None,
            "marketing_consent": customer["marketing_consent"],
            "do_not_contact": bool(customer["do_not_contact"]),
            "merged_into": (str(customer["merged_into_id"])
                            if customer["merged_into_id"] else None),
            "customer_since": (str(customer["created_at"])[:10]
                               if customer["created_at"] else None),
        },
        "events": events[:limit],
        "total_events": len(events),
        "shown": min(len(events), limit),
        "counts": {k: sum(1 for e in events if e["kind"] == k)
                   for k in KIND_ORDER if any(e["kind"] == k for e in events)},
        "note": (
            "Assembled from orders, invoices, payments, quotations, "
            "deliveries, calls, visits, messages and consent records every "
            "time it is opened. Nothing is stored, so nothing can be out of "
            "date -- a cancelled order or a paid invoice changes here the "
            "moment it changes there."),
    }


async def summary(session: AsyncSession, *, customer_id: UUID) -> dict:
    """The figures somebody wants before picking up the phone.

    Derived, like the timeline. Stored totals on the customer record were
    considered and rejected for the reason given in the implementation plan:
    about twenty of them are computable from the order book and a stored copy
    drifts from the orders that produced it.
    """
    row = (await session.execute(text("""
        SELECT
          (SELECT COUNT(*) FROM sales_orders
            WHERE customer_id = :i AND status <> 'cancelled')        AS orders,
          (SELECT COALESCE(SUM(total_amount), 0) FROM sales_orders
            WHERE customer_id = :i AND status <> 'cancelled')        AS lifetime,
          (SELECT MAX(order_date) FROM sales_orders
            WHERE customer_id = :i AND status <> 'cancelled')        AS last_order,
          (SELECT MIN(order_date) FROM sales_orders
            WHERE customer_id = :i AND status <> 'cancelled')        AS first_order,
          (SELECT COALESCE(SUM(total_amount - COALESCE(paid_amount, 0)), 0)
             FROM invoices
            WHERE customer_id = :i AND status IN ('pending','partial'))
                                                                     AS outstanding
    """), {"i": str(customer_id)})).mappings().first()

    orders = int(row["orders"] or 0)
    lifetime = Decimal(str(row["lifetime"] or 0))
    average = (lifetime / orders) if orders else Decimal("0")

    days_since = None
    if row["last_order"] is not None:
        days_since = (datetime.now(timezone.utc) - row["last_order"]).days

    # The customer's own rhythm, not a company-wide figure -- the same
    # measurement the opportunity queue uses.
    interval = None
    if orders >= 2 and row["first_order"] and row["last_order"]:
        span = (row["last_order"] - row["first_order"]).days
        interval = round(span / (orders - 1)) if orders > 1 and span else None

    return {
        "orders": orders,
        "lifetime_value": _money(lifetime),
        "average_order": _money(average),
        "outstanding": _money(row["outstanding"]),
        "last_order": (str(row["last_order"])[:10]
                       if row["last_order"] else None),
        "days_since_last_order": days_since,
        "average_interval_days": interval,
        "note": ("Computed from the order book on every call. Figures like "
                 "these are deliberately not stored on the customer record: a "
                 "stored total drifts from the orders that produced it."),
    }
