"""Who to contact today, and why -- derived live from the order book.

This answers one question: *which twenty customers should somebody ring this
morning, and what should they say?* Every finding carries the numbers that
produced it, because a sales officer who is told to ring a hospital and not
told why will ring it once and then stop reading the list.

NOTHING HERE IS STORED
======================
See migration o0123456789n. An opportunity is a statement about the order book
("37 days since the last order, usual interval 30"), and the order book
changes. A stored copy is wrong the moment the customer orders, and a list
that is wrong twice a week is a list people stop opening.

So this runs queries. At 102 customers and 242 orders that is a few
milliseconds; at a hundred times that it would need rethinking, and the note
returned with the results says so rather than letting it degrade quietly.

NO INVENTED RELATIONSHIPS
=========================
The cross-sell finding suggests a product, and the suggestion comes from THIS
COMPANY'S OWN ORDER HISTORY: customers who bought X also bought Y. That is a
commercial fact about what has been sold, and it is labelled as such.

It is deliberately NOT a clinical recommendation. Bonnesante sells wound-care
products with clinical applications, and a system that said "patients on X
should also have Y" would be making a medical claim nobody authorised. The
wording is about purchasing, and it stays that way.

WHAT THIS MODULE DOES NOT DO
============================
It does not message anybody. It produces a list for a human to work. There is
no send path here and there should not be one until the questions in the
engagement plan have been answered.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# How overdue a predictable buyer must be before it is worth a call,
# as a multiple of their own average interval.
#
# 1.15 was chosen against the two cases that matter: a 30-day buyer is
# surfaced at 35 days (a week late on a monthly cycle is worth a call) but
# not at 32 (two days is noise -- the interval is an average, and treating
# every variance as an opportunity buries the genuine ones).
OVERDUE_TOLERANCE = Decimal("1.15")

# Below this many orders there is no pattern to be overdue against. Two orders
# give one interval, which is not an average of anything.
MIN_ORDERS_FOR_PATTERN = 3

# No order in this long and the relationship has lapsed rather than slipped.
DORMANT_DAYS = 90

# A high-value customer who has gone quiet, but not yet dormant.
QUIET_DAYS = 30
HIGH_VALUE_PERCENTILE = Decimal("0.8")

# How long after delivery it is still sensible to ask how it went.
SATISFACTION_WINDOW_DAYS = 10

PRIORITIES = ("COMMERCIAL", "RELATIONSHIP")

TYPES = {
    "UNPAID_INVOICE": "COMMERCIAL",
    "REORDER_DUE": "COMMERCIAL",
    "DORMANT": "COMMERCIAL",
    "HIGH_VALUE_QUIET": "COMMERCIAL",
    "CROSS_SELL": "COMMERCIAL",
    "SATISFACTION_CHECK": "RELATIONSHIP",
}

# Ranking within a priority. Money already owed outranks money that might be
# earned, because it is the company's cash and it is already late.
RANK = {
    "UNPAID_INVOICE": 0,
    "REORDER_DUE": 1,
    "DORMANT": 2,
    "HIGH_VALUE_QUIET": 3,
    "CROSS_SELL": 4,
    "SATISFACTION_CHECK": 5,
}

# Orders that count as real trade. A cancelled order is not evidence of a
# buying pattern, and a pending one has not been accepted yet.
REAL_ORDERS = "('confirmed','completed')"


def _money(v) -> float:
    return float(Decimal(str(v or 0)).quantize(Decimal("0.01")))


def _finding(*, type_, customer, reason, action, value=0, detail=None,
             due_days=None) -> dict:
    """One row of the queue.

    `key` is stable across recomputation so that a snooze, and later a
    conversion rate, mean something.
    """
    return {
        "key": f"{type_}:{customer['customer_id']}",
        "type": type_,
        "priority": TYPES[type_],
        "customer_id": str(customer["customer_id"]),
        "customer_name": customer["name"],
        "customer_code": customer.get("customer_code"),
        "phone": customer.get("phone"),
        "reason": reason,
        "recommended_action": action,
        "potential_value": _money(value),
        "detail": detail or {},
        "days_overdue": due_days,
    }


# ---------------------------------------------------------------------------
# The sources
# ---------------------------------------------------------------------------

async def _unpaid_invoices(session: AsyncSession) -> list[dict]:
    """Invoices past their due date with money still on them.

    First in the ranking: this is the company's own cash, already late.
    """
    rows = (await session.execute(text("""
        SELECT c.id AS customer_id, c.name, c.customer_code, c.phone,
               COUNT(i.id) AS invoice_count,
               SUM(i.total_amount - COALESCE(i.paid_amount, 0)) AS outstanding,
               MIN(i.due_date::date) AS oldest_due,
               -- ::date on every side. due_date is TIMESTAMPTZ in this
               -- database, and date minus timestamptz yields an INTERVAL, not
               -- a number of days -- which reached production as a 500
               -- because the test schema declared the column DATE and the
               -- subtraction therefore returned an integer there.
               (CURRENT_DATE - MIN(i.due_date::date)) AS days_late
          FROM invoices i
          JOIN customers c ON c.id = i.customer_id
         WHERE i.status IN ('pending', 'partial')
           AND i.due_date IS NOT NULL
           AND i.due_date::date < CURRENT_DATE
           AND (i.total_amount - COALESCE(i.paid_amount, 0)) > 0
         GROUP BY c.id, c.name, c.customer_code, c.phone
    """))).mappings().all()

    out = []
    for r in rows:
        late = int(r["days_late"] or 0)
        plural = "s" if r["invoice_count"] > 1 else ""
        out.append(_finding(
            type_="UNPAID_INVOICE", customer=r,
            reason=(f"{r['invoice_count']} invoice{plural} overdue, oldest by "
                    f"{late} days"),
            action="Payment reminder",
            value=r["outstanding"], due_days=late,
            detail={"outstanding": _money(r["outstanding"]),
                    "invoice_count": int(r["invoice_count"]),
                    "oldest_due": str(r["oldest_due"])}))
    return out


async def _reorder_and_dormant(session: AsyncSession) -> list[dict]:
    """Customers measured against their own buying rhythm.

    Both findings come from one query because they are the same measurement
    read at two distances: a customer past their usual interval needs a
    reminder, and one who is far past it has lapsed.

    The interval is each customer's OWN average, not a company-wide number.
    A hospital ordering monthly and a pharmacy ordering weekly are both
    normal; a fixed threshold would nag one and miss the other.
    """
    rows = (await session.execute(text(f"""
        WITH ordered AS (
            SELECT customer_id, order_date,
                   LAG(order_date) OVER (PARTITION BY customer_id
                                         ORDER BY order_date) AS prev_date
              FROM sales_orders
             WHERE status IN {REAL_ORDERS} AND customer_id IS NOT NULL
        ),
        gaps AS (
            SELECT customer_id,
                   AVG(EXTRACT(EPOCH FROM (order_date - prev_date)) / 86400)
                       AS mean_days
              FROM ordered WHERE prev_date IS NOT NULL
             GROUP BY customer_id
        ),
        totals AS (
            SELECT o.customer_id,
                   COUNT(*) AS order_count,
                   MAX(o.order_date) AS last_order,
                   AVG(o.total_amount) AS mean_value,
                   SUM(o.total_amount) AS lifetime_value
              FROM sales_orders o
             WHERE o.status IN {REAL_ORDERS} AND o.customer_id IS NOT NULL
             GROUP BY o.customer_id
        )
        SELECT c.id AS customer_id, c.name, c.customer_code, c.phone,
               t.order_count, t.mean_value, t.lifetime_value,
               g.mean_days,
               DATE_PART('day', NOW() - t.last_order)::int AS days_since,
               t.last_order
          FROM totals t
          JOIN customers c ON c.id = t.customer_id
     LEFT JOIN gaps g ON g.customer_id = t.customer_id
         WHERE c.is_active
    """))).mappings().all()

    out = []
    for r in rows:
        days_since = int(r["days_since"] or 0)
        orders = int(r["order_count"] or 0)
        mean_days = (Decimal(str(r["mean_days"])) if r["mean_days"] is not None
                     else None)

        # Lapsed entirely. Checked first: a dormant customer is not also a
        # reorder reminder, and listing them twice would waste the officer's
        # morning on one relationship.
        if days_since >= DORMANT_DAYS:
            out.append(_finding(
                type_="DORMANT", customer=r,
                reason=(f"No order in {days_since} days "
                        f"({orders} orders before that)"),
                action="Reactivation call",
                value=r["mean_value"], due_days=days_since,
                detail={"orders": orders,
                        "lifetime_value": _money(r["lifetime_value"]),
                        "last_order": str(r["last_order"])[:10]}))
            continue

        if orders >= MIN_ORDERS_FOR_PATTERN and mean_days and mean_days > 0:
            threshold = mean_days * OVERDUE_TOLERANCE
            if days_since > threshold:
                overdue_by = days_since - int(mean_days)
                out.append(_finding(
                    type_="REORDER_DUE", customer=r,
                    reason=(f"Usually orders every {int(mean_days)} days; "
                            f"{days_since} days since the last one"),
                    action="Reorder reminder",
                    value=r["mean_value"], due_days=overdue_by,
                    detail={"mean_interval_days": int(mean_days),
                            "orders": orders,
                            "last_order": str(r["last_order"])[:10]}))
                continue

        # Valuable and quiet, but with no pattern to be overdue against --
        # which is most of a small customer base. Worth a call on value alone.
        if days_since >= QUIET_DAYS:
            out.append(_finding(
                type_="HIGH_VALUE_QUIET", customer=r,
                reason=(f"{_money(r['lifetime_value']):,.0f} lifetime, quiet "
                        f"for {days_since} days"),
                action="Relationship call",
                value=r["mean_value"], due_days=days_since,
                detail={"orders": orders,
                        "lifetime_value": _money(r["lifetime_value"])}))
    return out


async def _cross_sell(session: AsyncSession) -> list[dict]:
    """Customers buying a narrow range, with a suggestion from the order book.

    The suggestion is the product most often bought by OTHER customers who buy
    what this one buys. That is a statement about purchasing, derived from this
    company's own sales -- not a clinical recommendation, and the wording
    returned to the caller keeps it that way.
    """
    rows = (await session.execute(text(f"""
        WITH bought AS (
            SELECT DISTINCT o.customer_id, l.product_id
              FROM sales_order_lines l
              JOIN sales_orders o ON o.id = l.sales_order_id
             WHERE o.status IN {REAL_ORDERS} AND o.customer_id IS NOT NULL
        ),
        narrow AS (
            -- array_agg, not MIN: Postgres has no MIN() for uuid. The HAVING
            -- guarantees exactly one row, so the first element is the one.
            SELECT customer_id, (array_agg(product_id))[1] AS only_product
              FROM bought GROUP BY customer_id HAVING COUNT(*) = 1
        ),
        -- Of the customers who buy that same product, what else do they buy?
        companions AS (
            SELECT n.customer_id, b2.product_id, COUNT(*) AS buyers
              FROM narrow n
              JOIN bought b1 ON b1.product_id = n.only_product
              JOIN bought b2 ON b2.customer_id = b1.customer_id
                           AND b2.product_id <> n.only_product
             GROUP BY n.customer_id, b2.product_id
        ),
        best AS (
            SELECT DISTINCT ON (customer_id)
                   customer_id, product_id, buyers
              FROM companions ORDER BY customer_id, buyers DESC, product_id
        )
        SELECT c.id AS customer_id, c.name, c.customer_code, c.phone,
               p_have.name AS buys, p_want.name AS suggest, b.buyers,
               t.order_count, t.mean_value
          FROM best b
          JOIN narrow n ON n.customer_id = b.customer_id
          JOIN customers c ON c.id = b.customer_id
          JOIN products p_have ON p_have.id = n.only_product
          JOIN products p_want ON p_want.id = b.product_id
          JOIN (SELECT customer_id, COUNT(*) AS order_count,
                       AVG(total_amount) AS mean_value
                  FROM sales_orders
                 WHERE status IN {REAL_ORDERS} AND customer_id IS NOT NULL
                 GROUP BY customer_id) t ON t.customer_id = b.customer_id
         WHERE c.is_active AND t.order_count >= 2 AND b.buyers >= 2
    """))).mappings().all()

    out = []
    for r in rows:
        out.append(_finding(
            type_="CROSS_SELL", customer=r,
            reason=(f"Buys only {r['buys']}. {r['buyers']} other customers who "
                    f"buy it also buy {r['suggest']}"),
            action=f"Introduce {r['suggest']}",
            # A first order of a new line is worth less than their usual
            # basket; half is a deliberately conservative placeholder rather
            # than a forecast, and it is labelled as an estimate.
            value=Decimal(str(r["mean_value"] or 0)) / 2,
            detail={"currently_buys": r["buys"],
                    "suggested": r["suggest"],
                    "other_buyers": int(r["buyers"]),
                    "basis": "purchasing history, not a clinical indication"}))
    return out


async def _satisfaction(session: AsyncSession) -> list[dict]:
    """Deliveries completed recently enough that asking still makes sense."""
    rows = (await session.execute(text("""
        SELECT c.id AS customer_id, c.name, c.customer_code, c.phone,
               d.delivery_number, d.delivery_date,
               DATE_PART('day', NOW() - d.delivery_date)::int AS days_ago
          FROM logistics_deliveries d
          JOIN sales_orders o ON o.id = d.sales_order_id
          JOIN customers c ON c.id = o.customer_id
         WHERE LOWER(d.status) IN ('delivered', 'completed')
           AND d.delivery_date IS NOT NULL
           AND d.delivery_date >= NOW() - CAST(:w || ' days' AS interval)
         ORDER BY d.delivery_date DESC
    """), {"w": str(SATISFACTION_WINDOW_DAYS)})).mappings().all()

    seen, out = set(), []
    for r in rows:
        if r["customer_id"] in seen:
            continue
        seen.add(r["customer_id"])
        out.append(_finding(
            type_="SATISFACTION_CHECK", customer=r,
            reason=f"Delivered {int(r['days_ago'])} days ago",
            action="Ask how it went",
            value=0,
            detail={"delivery_number": r["delivery_number"],
                    "delivered_on": str(r["delivery_date"])[:10]}))
    return out


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------

async def queue(
    session: AsyncSession, *, limit: int = 20, priority: Optional[str] = None,
    types: Optional[list] = None, include_snoozed: bool = False,
) -> dict:
    """The ranked list, computed now.

    `limit` is what makes this usable: twenty customers is a morning's work,
    and a list of two hundred is the same as no list at all. The totals
    returned alongside say how much was left out, so nobody mistakes the top
    twenty for everything.
    """
    groups = [
        await _unpaid_invoices(session),
        await _reorder_and_dormant(session),
        await _cross_sell(session),
        await _satisfaction(session),
    ]
    findings = [f for group in groups for f in group]

    # Snoozes, and anything already acted on today. A customer contacted this
    # morning must not still be at the top of the list this afternoon.
    acted = {
        r["opportunity_key"]: r for r in (await session.execute(text("""
            SELECT DISTINCT ON (opportunity_key)
                   opportunity_key, outcome, snoozed_until, actor_name,
                   created_at
              FROM customer_opportunity_actions
             WHERE snoozed_until > NOW()
                OR created_at > NOW() - INTERVAL '7 days'
             ORDER BY opportunity_key, created_at DESC
        """))).mappings().all()
    }

    shown, snoozed_hidden, recently_done = [], 0, 0
    for f in findings:
        prior = acted.get(f["key"])
        if prior:
            if prior["snoozed_until"] and not include_snoozed:
                snoozed_hidden += 1
                continue
            if prior["outcome"] in ("CONTACTED", "CONVERTED", "DECLINED",
                                    "NOT_RELEVANT"):
                recently_done += 1
                continue
            if prior["snoozed_until"]:
                f = {**f, "snoozed_until": prior["snoozed_until"].isoformat()}
        shown.append(f)

    if priority:
        shown = [f for f in shown if f["priority"] == priority]
    if types:
        shown = [f for f in shown if f["type"] in types]

    # Rank: kind of opportunity first, then value within it. Money already
    # owed before money that might be earned.
    shown.sort(key=lambda f: (RANK[f["type"]], -f["potential_value"]))

    total = len(shown)
    top = shown[:limit]

    by_priority = {}
    for p in PRIORITIES:
        rows = [f for f in shown if f["priority"] == p]
        by_priority[p] = {
            "count": len(rows),
            "value": _money(sum(Decimal(str(f["potential_value"]))
                                for f in rows)),
        }

    by_type = {}
    for t in TYPES:
        rows = [f for f in shown if f["type"] == t]
        if rows:
            by_type[t] = {
                "count": len(rows),
                "value": _money(sum(Decimal(str(f["potential_value"]))
                                    for f in rows)),
            }

    return {
        "items": top,
        "shown": len(top),
        "total": total,
        "not_shown": max(0, total - len(top)),
        "by_priority": by_priority,
        "by_type": by_type,
        "snoozed_hidden": snoozed_hidden,
        "recently_actioned": recently_done,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Computed live from orders, invoices and deliveries every time "
            "this is opened, so nothing can be out of date -- an item "
            "disappears as soon as the customer orders or pays. Values are "
            "estimates from past order sizes, not forecasts. Nothing here is "
            "sent to any customer; this is a list for a person to work."),
    }


async def record_action(
    session: AsyncSession, *, opportunity_key: str, outcome: str,
    note: Optional[str] = None, snooze_days: Optional[int] = None,
    actor=None,
) -> dict:
    """Record what a person did about a finding.

    Takes the finding's own numbers from a fresh computation rather than from
    the caller, so what is stored is what the system actually said -- not what
    a browser claimed it said.
    """
    outcome = (outcome or "").upper()
    valid = ("CONTACTED", "CONVERTED", "DECLINED", "SNOOZED", "NOT_RELEVANT")
    if outcome not in valid:
        raise HTTPException(
            status_code=400,
            detail=f"Outcome must be one of {', '.join(valid)}.")

    if ":" not in opportunity_key:
        raise HTTPException(status_code=400, detail="Malformed opportunity key.")
    type_, _, customer_id = opportunity_key.partition(":")
    if type_ not in TYPES:
        raise HTTPException(
            status_code=400, detail=f"Unknown opportunity type {type_}.")

    if outcome == "SNOOZED":
        if not snooze_days or snooze_days < 1 or snooze_days > 90:
            raise HTTPException(
                status_code=400,
                detail=("A snooze needs a length between 1 and 90 days. There "
                        "is no dismiss-forever: an opportunity that is still "
                        "true has to come back."))

    # Find the finding as it stands, so the stored row carries the real
    # numbers. A finding that has already resolved itself is still recordable
    # -- somebody may be reporting a call they made this morning -- but it is
    # stored without a value rather than with an invented one.
    current = await queue(session, limit=10_000, include_snoozed=True)
    finding = next((f for f in current["items"]
                    if f["key"] == opportunity_key), None)

    exists = (await session.execute(
        text("SELECT 1 FROM customers WHERE id = :i"),
        {"i": customer_id})).first()
    if exists is None:
        raise HTTPException(status_code=404, detail="Customer not found.")

    row = (await session.execute(text("""
        INSERT INTO customer_opportunity_actions
            (opportunity_key, opportunity_type, customer_id, outcome,
             potential_value, reason_at_action, note, snoozed_until,
             actor_id, actor_name)
        VALUES (:k, :t, :c, :o, :v, :r, :n, :su, :ai, :an)
        RETURNING id, snoozed_until
    """), {
        "k": opportunity_key, "t": type_, "c": customer_id, "o": outcome,
        "v": str(finding["potential_value"]) if finding else None,
        "r": finding["reason"] if finding else None,
        "n": note,
        # Computed here rather than in SQL: binding `outcome` both as a
        # column value and inside a CASE leaves asyncpg unable to deduce one
        # type for the parameter, and it refuses the statement.
        "su": (datetime.now(timezone.utc) + timedelta(days=int(snooze_days))
               if outcome == "SNOOZED" else None),
        "ai": str(actor.id) if actor is not None else None,
        "an": (getattr(actor, "full_name", None)
               or getattr(actor, "username", None)) if actor is not None else None,
    })).mappings().first()

    return {
        "id": str(row["id"]),
        "opportunity_key": opportunity_key,
        "outcome": outcome,
        "snoozed_until": (row["snoozed_until"].isoformat()
                          if row["snoozed_until"] else None),
        "recorded_value": finding["potential_value"] if finding else None,
        "note": ("Recorded. This opportunity will not reappear for seven days."
                 if outcome != "SNOOZED"
                 else f"Snoozed for {snooze_days} days, then it comes back if "
                      f"it is still true."),
    }


async def performance(session: AsyncSession, *, days: int = 90) -> dict:
    """Was any of this worth building?

    The honest measure of a recommendation engine is not how many findings it
    produced but how many were acted on and what came of them. Both numbers
    are here, including the uncomfortable one.
    """
    rows = (await session.execute(text("""
        SELECT opportunity_type, outcome, COUNT(*) AS n,
               SUM(potential_value) AS value
          FROM customer_opportunity_actions
         WHERE created_at >= NOW() - CAST(:d || ' days' AS interval)
         GROUP BY opportunity_type, outcome
    """), {"d": str(int(days))})).mappings().all()

    by_type: dict = {}
    for r in rows:
        t = by_type.setdefault(r["opportunity_type"],
                               {"actioned": 0, "converted": 0,
                                "declined": 0, "value_converted": 0.0})
        t["actioned"] += int(r["n"])
        if r["outcome"] == "CONVERTED":
            t["converted"] += int(r["n"])
            t["value_converted"] += _money(r["value"])
        elif r["outcome"] in ("DECLINED", "NOT_RELEVANT"):
            t["declined"] += int(r["n"])

    for t in by_type.values():
        t["conversion_rate"] = (round(t["converted"] / t["actioned"] * 100, 1)
                                if t["actioned"] else 0.0)

    actors = (await session.execute(text("""
        SELECT actor_name, COUNT(*) AS n,
               COUNT(*) FILTER (WHERE outcome = 'CONVERTED') AS converted
          FROM customer_opportunity_actions
         WHERE created_at >= NOW() - CAST(:d || ' days' AS interval)
           AND actor_name IS NOT NULL
         GROUP BY actor_name ORDER BY n DESC
    """), {"d": str(int(days))})).mappings().all()

    total_actioned = sum(t["actioned"] for t in by_type.values())
    total_converted = sum(t["converted"] for t in by_type.values())

    return {
        "days": days,
        "by_type": by_type,
        "by_actor": [{"name": a["actor_name"], "actioned": int(a["n"]),
                      "converted": int(a["converted"])} for a in actors],
        "total_actioned": total_actioned,
        "total_converted": total_converted,
        "conversion_rate": (round(total_converted / total_actioned * 100, 1)
                            if total_actioned else 0.0),
        "value_converted": _money(sum(t["value_converted"]
                                      for t in by_type.values())),
        "note": ("Counts actions recorded by staff, not findings produced. A "
                 "low conversion rate on a type means that type is not worth "
                 "surfacing -- which is a useful answer, not a failure."),
    }
