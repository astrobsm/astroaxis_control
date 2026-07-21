"""
Profits Module — Admin-only

Computes real-time profit on every payment received against an invoice/sale.

Profit model
────────────
For each invoice line we know:
   line_total      = quantity * unit_price            (revenue contribution)
   line_cost       = quantity * product.cost_price    (cost contribution)
   line_profit     = line_total - line_cost
Invoice profit = SUM(line_profit) across all invoice_lines.
Invoice profit margin = invoice_profit / invoice_total.

When a payment of `amount` is recorded against an invoice, the profit it
realises is proportional to its share of the invoice total:

   payment_profit = amount * (invoice_profit / invoice_total)
                  = amount * margin

Settlement workflow
───────────────────
   payment.status (virtual, derived from profit_settlement_payments table):
      * pending  — not yet part of any settlement
      * approved — included in a settlement that is approved (awaiting payout)
      * settled  — settlement has been marked as settled / paid out

Weekly settlements: one settlement covers one ISO week (Mon→Sun).

Tables auto-created on first request:
   profit_settlements
   profit_settlement_payments  (link table: settlement_id, payment_id)
"""
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Optional, List
from pydantic import BaseModel, Field
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal
import uuid

from app.db import get_session
from app.api.auth import decode_token

router = APIRouter(prefix="/api/profits")

ADMIN_ROLES = {"admin"}


# ─── SCHEMA ──────────────────────────────────────────────────────────────────
CREATE_SETTLEMENTS_SQL = """
CREATE TABLE IF NOT EXISTS profit_settlements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    settlement_number VARCHAR(64) UNIQUE NOT NULL,
    week_start DATE NOT NULL,
    week_end DATE NOT NULL,
    total_revenue NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_cost NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_profit NUMERIC(18,2) NOT NULL DEFAULT 0,
    payment_count INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL DEFAULT 'approved',
    notes TEXT,
    approved_by UUID REFERENCES users(id),
    approved_by_name VARCHAR(255),
    approved_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    settled_by UUID REFERENCES users(id),
    settled_by_name VARCHAR(255),
    settled_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
)
"""

CREATE_LINK_SQL = """
CREATE TABLE IF NOT EXISTS profit_settlement_payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    settlement_id UUID NOT NULL REFERENCES profit_settlements(id) ON DELETE CASCADE,
    payment_id UUID NOT NULL REFERENCES payments(id) ON DELETE CASCADE,
    profit_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
    revenue_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(payment_id)
)
"""

CREATE_INDEX_SQLS = [
    "CREATE INDEX IF NOT EXISTS idx_profit_settlements_week ON profit_settlements(week_start)",
    "CREATE INDEX IF NOT EXISTS idx_profit_settlements_status ON profit_settlements(status)",
    "CREATE INDEX IF NOT EXISTS idx_psp_settlement ON profit_settlement_payments(settlement_id)",
    "CREATE INDEX IF NOT EXISTS idx_psp_payment ON profit_settlement_payments(payment_id)",
]


async def _ensure_tables(session: AsyncSession):
    await session.execute(text(CREATE_SETTLEMENTS_SQL))
    await session.execute(text(CREATE_LINK_SQL))
    for s in CREATE_INDEX_SQLS:
        await session.execute(text(s))
    await session.commit()


# ─── AUTH HELPERS ────────────────────────────────────────────────────────────
async def _admin_only(authorization: Optional[str]):
    """Verify Bearer token belongs to an admin. Returns (user_id, name)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")
    role = (payload.get("role") or "").lower()
    if role not in ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Admin access only")
    return payload.get("sub"), payload.get("full_name") or payload.get("username") or "Admin"


# ─── CORE QUERY: payments + per-payment profit ───────────────────────────────
# For every payment we compute the realised profit using:
#   payment_profit = payment.amount * (invoice_profit / invoice_total)
#
# Profit per line = line_total - cost_total, where BOTH figures were recorded
# when the sale happened:
#   • line_total is the price actually charged (retail or wholesale, in
#     whatever unit was sold).
#   • cost_total is the cost snapshotted onto the line at that moment by
#     app.services.costing, derived from the movement ledger's weighted
#     average where available.
#
# Neither is re-derived at query time. The previous implementation resolved
# cost by joining product_pricing live, which meant a cost-price change today
# restated the profit of every period already closed, reported and paid out.
#
# Lines carry cost_source so a reader can distinguish a ledger-derived cost
# from a price-list fallback or a migration backfill estimate. Lines whose
# cost was never determined ('unknown') contribute zero profit and have their
# revenue reported in `costs_unknown_revenue`, so the page never silently
# claims 100% margin on goods of unknown cost.
PAYMENTS_PROFIT_SQL = """
WITH invoice_economics AS (
    -- COGS comes from the cost snapshotted onto the line when the sale was
    -- made (sales_order_lines.cost_total / invoice_lines.cost_total), NOT
    -- from a live join against product_pricing.
    --
    -- The old query joined the current price list, which meant raising a cost
    -- price today silently restated the profit of every month already closed
    -- and paid out, leaving persisted profit_settlements irreconcilable with
    -- the query they came from. The join also fanned out on duplicate
    -- (product_id, unit) pricing rows, doubling invoice_cost while
    -- invoice_total stayed put -- reporting more profit than cash received.
    --
    -- Lines whose cost was never determined carry cost_source = 'unknown';
    -- their revenue is reported separately rather than booked at 100% margin.
    SELECT
        i.id              AS invoice_id,
        i.total_amount    AS invoice_total,
        COALESCE(SUM(
            CASE WHEN i.sales_order_id IS NOT NULL
                 THEN COALESCE(sol.cost_total, 0)
                 ELSE COALESCE(il.cost_total, 0) END
        ), 0) AS invoice_cost,
        COALESCE(SUM(
            CASE
                WHEN i.sales_order_id IS NOT NULL THEN
                    CASE WHEN COALESCE(sol.cost_source, 'unknown') = 'unknown'
                         THEN 0
                         ELSE sol.line_total - COALESCE(sol.cost_total, 0) END
                ELSE
                    CASE WHEN COALESCE(il.cost_source, 'unknown') = 'unknown'
                         THEN 0
                         ELSE il.line_total - COALESCE(il.cost_total, 0) END
            END
        ), 0) AS invoice_profit,
        -- Revenue from lines with no known cost, surfaced rather than hidden.
        COALESCE(SUM(
            CASE
                WHEN i.sales_order_id IS NOT NULL
                     AND COALESCE(sol.cost_source, 'unknown') = 'unknown'
                    THEN sol.line_total
                WHEN i.sales_order_id IS NULL
                     AND COALESCE(il.cost_source, 'unknown') = 'unknown'
                    THEN il.line_total
                ELSE 0
            END
        ), 0) AS invoice_unknown_cost_revenue
    FROM invoices i
    LEFT JOIN sales_order_lines sol
           ON sol.sales_order_id = i.sales_order_id
    LEFT JOIN invoice_lines il
           ON il.invoice_id = i.id
          AND i.sales_order_id IS NULL
    GROUP BY i.id, i.total_amount
)
SELECT
    pay.id                                  AS payment_id,
    pay.invoice_id                          AS invoice_id,
    pay.amount                              AS payment_amount,
    pay.payment_method                      AS payment_method,
    pay.payment_date                        AS payment_date,
    pay.reference                           AS reference,
    pay.created_at                          AS created_at,
    inv.invoice_number                      AS invoice_number,
    so.order_number                         AS order_number,
    c.name                                  AS customer_name,
    ie.invoice_total                        AS invoice_total,
    ie.invoice_cost                         AS invoice_cost,
    ie.invoice_profit                       AS invoice_profit,
    ie.invoice_unknown_cost_revenue         AS invoice_unknown_cost_revenue,
    CASE WHEN ie.invoice_total > 0
         THEN pay.amount * (ie.invoice_profit / ie.invoice_total)
         ELSE 0
    END                                     AS payment_profit,
    CASE WHEN ie.invoice_total > 0
         THEN pay.amount * (ie.invoice_unknown_cost_revenue / ie.invoice_total)
         ELSE 0
    END                                     AS payment_unknown_cost_revenue,
    CASE WHEN ie.invoice_total > 0
         THEN (ie.invoice_profit / ie.invoice_total) * 100.0
         ELSE 0
    END                                     AS profit_margin_pct,
    psp.settlement_id                       AS settlement_id,
    s.settlement_number                     AS settlement_number,
    s.status                                AS settlement_status
FROM payments pay
JOIN invoices inv ON inv.id = pay.invoice_id
JOIN invoice_economics ie ON ie.invoice_id = pay.invoice_id
LEFT JOIN sales_orders so ON so.id = inv.sales_order_id
LEFT JOIN customers c ON c.id = inv.customer_id
LEFT JOIN profit_settlement_payments psp ON psp.payment_id = pay.id
LEFT JOIN profit_settlements s ON s.id = psp.settlement_id
"""


def _iso_week_bounds(d: date):
    """Return (week_start, week_end) for ISO week containing `d` (Mon..Sun)."""
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


# ─── ENDPOINTS ───────────────────────────────────────────────────────────────
@router.get("/realtime")
async def realtime_profits(
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
    authorization: Optional[str] = Header(None),
):
    """List most recent payments with realised profit per payment."""
    await _admin_only(authorization)
    await _ensure_tables(session)

    sql = text(PAYMENTS_PROFIT_SQL + " ORDER BY pay.payment_date DESC, pay.created_at DESC LIMIT :lim")
    res = await session.execute(sql, {"lim": int(limit)})
    rows = res.fetchall()
    items = []
    for r in rows:
        amount = float(r.payment_amount or 0)
        profit = float(r.payment_profit or 0)
        # Capital = portion of the cash that recoups cost of goods sold.
        # Cannot exceed the cash received and cannot be negative.
        capital = max(0.0, min(amount, amount - profit))
        items.append({
            "payment_id": str(r.payment_id),
            "invoice_id": str(r.invoice_id),
            "invoice_number": r.invoice_number,
            "order_number": r.order_number,
            "customer_name": r.customer_name,
            "payment_amount": amount,
            "payment_method": r.payment_method,
            "payment_date": r.payment_date.isoformat() if r.payment_date else None,
            "reference": r.reference,
            "invoice_total": float(r.invoice_total or 0),
            "invoice_cost": float(r.invoice_cost or 0),
            "invoice_profit": float(r.invoice_profit or 0),
            "payment_capital": capital,
            "payment_profit": profit,
            "payment_unknown_cost_revenue": float(r.payment_unknown_cost_revenue or 0),
            "cost_unknown": float(r.payment_unknown_cost_revenue or 0) > 0.01,
            "profit_margin_pct": float(r.profit_margin_pct or 0),
            "settlement_id": str(r.settlement_id) if r.settlement_id else None,
            "settlement_number": r.settlement_number,
            "settlement_status": r.settlement_status,
            "status": r.settlement_status or "pending",
        })
    return {"items": items, "total": len(items)}


@router.get("/missing-costs")
async def profits_missing_costs(
    session: AsyncSession = Depends(get_session),
    authorization: Optional[str] = Header(None),
):
    """List products that have been sold but have no cost price set.

    These products distort profit reporting (they appear as 100% margin).
    Admin should populate cost_price on the product or on product_pricing
    for the relevant unit.
    """
    await _admin_only(authorization)
    sql = text("""
        WITH sold AS (
            SELECT DISTINCT sol.product_id, LOWER(COALESCE(sol.unit, '')) AS unit_l, sol.unit AS unit
            FROM sales_order_lines sol
            UNION
            SELECT DISTINCT il.product_id, ''::text AS unit_l, NULL AS unit
            FROM invoice_lines il
        ),
        product_any_cost AS (
            SELECT product_id, MAX(NULLIF(cost_price, 0)) AS any_cost
            FROM product_pricing GROUP BY product_id
        )
        SELECT
            p.id           AS product_id,
            p.sku          AS sku,
            p.name         AS name,
            sold.unit      AS sold_as_unit,
            p.cost_price   AS product_cost_price,
            pp.cost_price  AS unit_cost_price,
            pac.any_cost   AS any_unit_cost
        FROM sold
        JOIN products p ON p.id = sold.product_id
        LEFT JOIN product_pricing pp
               ON pp.product_id = sold.product_id
              AND LOWER(pp.unit) = sold.unit_l
        LEFT JOIN product_any_cost pac ON pac.product_id = sold.product_id
        WHERE COALESCE(NULLIF(pp.cost_price, 0), pac.any_cost, NULLIF(p.cost_price, 0), 0) = 0
        ORDER BY p.name, sold.unit
    """)
    res = await session.execute(sql)
    items = [{
        "product_id": str(r.product_id),
        "sku": r.sku,
        "name": r.name,
        "sold_as_unit": r.sold_as_unit,
        "product_cost_price": float(r.product_cost_price or 0),
        "unit_cost_price": float(r.unit_cost_price or 0),
        "any_unit_cost": float(r.any_unit_cost or 0),
    } for r in res.fetchall()]
    return {"items": items, "total": len(items)}


@router.get("/summary")
async def profits_summary(
    session: AsyncSession = Depends(get_session),
    authorization: Optional[str] = Header(None),
):
    """KPI summary: today, this week, this month, all-time pending vs settled."""
    await _admin_only(authorization)
    await _ensure_tables(session)

    today = date.today()
    week_start, week_end = _iso_week_bounds(today)
    month_start = today.replace(day=1)

    sql = text(
        PAYMENTS_PROFIT_SQL
    )
    res = await session.execute(sql)
    rows = res.fetchall()

    def bucket(predicate):
        rev = sum(float(r.payment_amount or 0) for r in rows if predicate(r))
        prof = sum(float(r.payment_profit or 0) for r in rows if predicate(r))
        # Capital = cash collected that recoups COGS (clamped to [0, revenue])
        cap = max(0.0, min(rev, rev - prof))
        return {
            "revenue": rev,
            "capital": cap,
            "profit": prof,
            "margin_pct": (prof / rev * 100) if rev else 0,
        }

    today_b = bucket(lambda r: r.payment_date and r.payment_date.date() == today)
    week_b = bucket(lambda r: r.payment_date and week_start <= r.payment_date.date() <= week_end)
    month_b = bucket(lambda r: r.payment_date and r.payment_date.date() >= month_start)
    all_b = bucket(lambda r: True)

    def split(predicate):
        rev = sum(float(r.payment_amount or 0) for r in rows if predicate(r))
        prof = sum(float(r.payment_profit or 0) for r in rows if predicate(r))
        cap = max(0.0, min(rev, rev - prof))
        return rev, cap, prof

    pending_rev, pending_capital, pending_profit = split(lambda r: not r.settlement_id)
    approved_rev, approved_capital, approved_profit = split(
        lambda r: r.settlement_id and r.settlement_status == "approved"
    )
    settled_rev, settled_capital, settled_profit = split(
        lambda r: r.settlement_id and r.settlement_status == "settled"
    )

    return {
        "today": today_b,
        "week": {**week_b, "week_start": week_start.isoformat(), "week_end": week_end.isoformat()},
        "month": month_b,
        "all_time": all_b,
        "pending_revenue": pending_rev,
        "pending_capital": pending_capital,
        "pending_profit": pending_profit,
        "approved_revenue": approved_rev,
        "approved_capital": approved_capital,
        "approved_profit": approved_profit,
        "settled_revenue": settled_rev,
        "settled_capital": settled_capital,
        "settled_profit": settled_profit,
        "unknown_cost_revenue": sum(
            float(r.payment_unknown_cost_revenue or 0) for r in rows
        ),
        "unknown_cost_payment_count": sum(
            1 for r in rows if float(r.payment_unknown_cost_revenue or 0) > 0.01
        ),
        "payment_count": len(rows),
    }


@router.get("/weeks")
async def profits_by_week(
    weeks_back: int = 12,
    session: AsyncSession = Depends(get_session),
    authorization: Optional[str] = Header(None),
):
    """Aggregate payments by ISO week (last N weeks). Each week aggregates its
    own status: settled if all payments settled, approved if any approved and
    none pending, otherwise pending."""
    await _admin_only(authorization)
    await _ensure_tables(session)

    today = date.today()
    earliest = today - timedelta(weeks=int(weeks_back))

    sql = text(PAYMENTS_PROFIT_SQL + " WHERE pay.payment_date >= :earliest ORDER BY pay.payment_date ASC")
    res = await session.execute(sql, {"earliest": earliest})
    rows = res.fetchall()

    weeks = {}
    for r in rows:
        if not r.payment_date:
            continue
        w_start, w_end = _iso_week_bounds(r.payment_date.date())
        key = w_start.isoformat()
        w = weeks.setdefault(key, {
            "week_start": w_start.isoformat(),
            "week_end": w_end.isoformat(),
            "revenue": 0.0,
            "capital": 0.0,
            "profit": 0.0,
            "payment_count": 0,
            "pending_count": 0,
            "approved_count": 0,
            "settled_count": 0,
        })
        w["revenue"] += float(r.payment_amount or 0)
        w["profit"] += float(r.payment_profit or 0)
        w["payment_count"] += 1
        st = r.settlement_status or "pending"
        if not r.settlement_id:
            w["pending_count"] += 1
        elif st == "settled":
            w["settled_count"] += 1
        else:
            w["approved_count"] += 1

    items = []
    for w in weeks.values():
        if w["settled_count"] == w["payment_count"] and w["payment_count"] > 0:
            w["status"] = "settled"
        elif w["pending_count"] == 0 and w["approved_count"] > 0:
            w["status"] = "approved"
        elif w["pending_count"] > 0 and w["approved_count"] + w["settled_count"] > 0:
            w["status"] = "partial"
        else:
            w["status"] = "pending"
        # Capital = revenue minus profit, clamped to [0, revenue]
        w["capital"] = max(0.0, min(w["revenue"], w["revenue"] - w["profit"]))
        w["margin_pct"] = (w["profit"] / w["revenue"] * 100) if w["revenue"] else 0
        items.append(w)
    items.sort(key=lambda x: x["week_start"], reverse=True)
    return {"items": items, "total": len(items)}


# ─── SETTLEMENTS ─────────────────────────────────────────────────────────────
class ApproveWeekBody(BaseModel):
    week_start: date = Field(..., description="ISO date (Monday) of the week to approve")
    notes: Optional[str] = None


@router.post("/settlements/approve-week")
async def approve_week(
    body: ApproveWeekBody,
    session: AsyncSession = Depends(get_session),
    authorization: Optional[str] = Header(None),
):
    """Create a settlement covering all currently-pending payments inside the
    given ISO week (Mon→Sun). Status = 'approved' (ready for distribution)."""
    user_id, user_name = await _admin_only(authorization)
    await _ensure_tables(session)

    w_start, w_end = _iso_week_bounds(body.week_start)

    # Find pending payments in that week
    sql = text(
        PAYMENTS_PROFIT_SQL
        + " WHERE pay.payment_date::date BETWEEN :ws AND :we AND psp.settlement_id IS NULL"
    )
    res = await session.execute(sql, {"ws": w_start, "we": w_end})
    rows = res.fetchall()
    if not rows:
        raise HTTPException(
            status_code=400,
            detail=f"No pending payments to approve for week {w_start} → {w_end}",
        )

    total_rev = sum(float(r.payment_amount or 0) for r in rows)
    total_profit = sum(float(r.payment_profit or 0) for r in rows)
    total_cost = total_rev - total_profit

    settlement_id = uuid.uuid4()
    settlement_number = f"PS-{w_start.strftime('%Y%m%d')}-{str(settlement_id)[:8].upper()}"

    # user_id may be a stringified UUID or null → coerce safely
    uid_param = None
    try:
        if user_id:
            uid_param = str(uuid.UUID(str(user_id)))
    except Exception:
        uid_param = None

    await session.execute(
        text(
            """
            INSERT INTO profit_settlements
              (id, settlement_number, week_start, week_end,
               total_revenue, total_cost, total_profit, payment_count,
               status, notes, approved_by, approved_by_name, approved_at)
            VALUES
              (CAST(:id AS uuid), :num, :ws, :we,
               :rev, :cost, :prof, :cnt,
               'approved', :notes, CAST(:uid AS uuid), :uname, NOW())
            """
        ),
        {
            "id": str(settlement_id), "num": settlement_number,
            "ws": w_start, "we": w_end,
            "rev": total_rev, "cost": total_cost, "prof": total_profit,
            "cnt": len(rows), "notes": body.notes,
            "uid": uid_param, "uname": user_name,
        },
    )

    # Link payments
    for r in rows:
        await session.execute(
            text(
                """
                INSERT INTO profit_settlement_payments
                  (settlement_id, payment_id, profit_amount, revenue_amount)
                VALUES
                  (CAST(:sid AS uuid), CAST(:pid AS uuid), :prof, :rev)
                ON CONFLICT (payment_id) DO NOTHING
                """
            ),
            {
                "sid": str(settlement_id),
                "pid": str(r.payment_id),
                "prof": float(r.payment_profit or 0),
                "rev": float(r.payment_amount or 0),
            },
        )

    await session.commit()
    return {
        "message": f"Week {w_start} → {w_end} approved. {len(rows)} payments, profit ₦{total_profit:,.2f}.",
        "settlement": {
            "id": str(settlement_id),
            "settlement_number": settlement_number,
            "week_start": w_start.isoformat(),
            "week_end": w_end.isoformat(),
            "total_revenue": total_rev,
            "total_profit": total_profit,
            "payment_count": len(rows),
            "status": "approved",
        },
    }


@router.post("/settlements/{settlement_id}/mark-settled")
async def mark_settlement_settled(
    settlement_id: str,
    session: AsyncSession = Depends(get_session),
    authorization: Optional[str] = Header(None),
):
    """Mark a previously-approved settlement as fully distributed/settled."""
    user_id, user_name = await _admin_only(authorization)
    await _ensure_tables(session)

    res = await session.execute(
        text("SELECT id, status FROM profit_settlements WHERE id = CAST(:id AS uuid)"),
        {"id": settlement_id},
    )
    row = res.first()
    if not row:
        raise HTTPException(status_code=404, detail="Settlement not found")
    if row.status == "settled":
        raise HTTPException(status_code=400, detail="Already settled")

    uid_param = None
    try:
        if user_id:
            uid_param = str(uuid.UUID(str(user_id)))
    except Exception:
        uid_param = None

    await session.execute(
        text(
            """UPDATE profit_settlements
               SET status='settled',
                   settled_by = CAST(:uid AS uuid),
                   settled_by_name = :uname,
                   settled_at = NOW()
               WHERE id = CAST(:id AS uuid)"""
        ),
        {"id": settlement_id, "uid": uid_param, "uname": user_name},
    )
    await session.commit()
    return {"message": "Settlement marked as settled."}


@router.delete("/settlements/{settlement_id}")
async def revoke_settlement(
    settlement_id: str,
    session: AsyncSession = Depends(get_session),
    authorization: Optional[str] = Header(None),
):
    """Delete a settlement. Linked payments revert to 'pending'."""
    await _admin_only(authorization)
    await _ensure_tables(session)
    res = await session.execute(
        text("SELECT id FROM profit_settlements WHERE id = CAST(:id AS uuid)"),
        {"id": settlement_id},
    )
    if not res.first():
        raise HTTPException(status_code=404, detail="Settlement not found")
    await session.execute(
        text("DELETE FROM profit_settlements WHERE id = CAST(:id AS uuid)"),
        {"id": settlement_id},
    )
    await session.commit()
    return {"message": "Settlement revoked. Payments returned to pending."}


@router.get("/settlements")
async def list_settlements(
    session: AsyncSession = Depends(get_session),
    authorization: Optional[str] = Header(None),
):
    await _admin_only(authorization)
    await _ensure_tables(session)
    res = await session.execute(
        text(
            """SELECT id, settlement_number, week_start, week_end,
                      total_revenue, total_cost, total_profit, payment_count,
                      status, notes,
                      approved_by_name, approved_at,
                      settled_by_name, settled_at, created_at
               FROM profit_settlements
               ORDER BY week_start DESC, created_at DESC"""
        )
    )
    items = []
    for r in res.fetchall():
        items.append({
            "id": str(r.id),
            "settlement_number": r.settlement_number,
            "week_start": r.week_start.isoformat() if r.week_start else None,
            "week_end": r.week_end.isoformat() if r.week_end else None,
            "total_revenue": float(r.total_revenue or 0),
            "total_cost": float(r.total_cost or 0),
            "total_profit": float(r.total_profit or 0),
            "payment_count": r.payment_count or 0,
            "status": r.status,
            "notes": r.notes,
            "approved_by_name": r.approved_by_name,
            "approved_at": r.approved_at.isoformat() if r.approved_at else None,
            "settled_by_name": r.settled_by_name,
            "settled_at": r.settled_at.isoformat() if r.settled_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return {"items": items, "total": len(items)}


@router.get("/settlements/{settlement_id}")
async def get_settlement(
    settlement_id: str,
    session: AsyncSession = Depends(get_session),
    authorization: Optional[str] = Header(None),
):
    """Detail: settlement header + every payment included."""
    await _admin_only(authorization)
    await _ensure_tables(session)

    head_res = await session.execute(
        text(
            """SELECT id, settlement_number, week_start, week_end,
                      total_revenue, total_cost, total_profit, payment_count,
                      status, notes,
                      approved_by_name, approved_at,
                      settled_by_name, settled_at, created_at
               FROM profit_settlements WHERE id = CAST(:id AS uuid)"""
        ),
        {"id": settlement_id},
    )
    s = head_res.first()
    if not s:
        raise HTTPException(status_code=404, detail="Settlement not found")

    pay_res = await session.execute(
        text(
            """SELECT psp.payment_id, psp.profit_amount, psp.revenue_amount,
                      pay.payment_date, pay.payment_method, pay.reference,
                      inv.invoice_number, c.name AS customer_name,
                      so.order_number
               FROM profit_settlement_payments psp
               JOIN payments pay ON pay.id = psp.payment_id
               JOIN invoices inv ON inv.id = pay.invoice_id
               LEFT JOIN sales_orders so ON so.id = inv.sales_order_id
               LEFT JOIN customers c ON c.id = inv.customer_id
               WHERE psp.settlement_id = CAST(:id AS uuid)
               ORDER BY pay.payment_date DESC"""
        ),
        {"id": settlement_id},
    )
    payments = []
    for r in pay_res.fetchall():
        rev = float(r.revenue_amount or 0)
        prof = float(r.profit_amount or 0)
        cap = max(0.0, min(rev, rev - prof))
        payments.append({
            "payment_id": str(r.payment_id),
            "invoice_number": r.invoice_number,
            "order_number": r.order_number,
            "customer_name": r.customer_name,
            "revenue": rev,
            "capital": cap,
            "profit": prof,
            "payment_method": r.payment_method,
            "reference": r.reference,
            "payment_date": r.payment_date.isoformat() if r.payment_date else None,
        })

    return {
        "settlement": {
            "id": str(s.id),
            "settlement_number": s.settlement_number,
            "week_start": s.week_start.isoformat() if s.week_start else None,
            "week_end": s.week_end.isoformat() if s.week_end else None,
            "total_revenue": float(s.total_revenue or 0),
            "total_cost": float(s.total_cost or 0),
            "total_profit": float(s.total_profit or 0),
            "payment_count": s.payment_count or 0,
            "status": s.status,
            "notes": s.notes,
            "approved_by_name": s.approved_by_name,
            "approved_at": s.approved_at.isoformat() if s.approved_at else None,
            "settled_by_name": s.settled_by_name,
            "settled_at": s.settled_at.isoformat() if s.settled_at else None,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        },
        "payments": payments,
    }
