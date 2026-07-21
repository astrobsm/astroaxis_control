"""
Company Radio API

Aggregates a unified, time-ordered feed of business events that the frontend
"Company Radio" service polls and announces aloud (TTS or attached audio):

  * staff clock-in / clock-out
  * new sales orders / invoices / payments
  * new production orders & completions
  * new raw-material entries (stock IN movements)
  * scheduled announcements that are due now

Endpoint:
  GET /api/radio/feed?since=<iso8601>&limit=50
    -> { now: <iso>, events: [ {id,type,title,message,audio_url?,
                                 priority,created_at} ... ] }

Events are deterministic & idempotent (id = "<type>:<row_id>") so the client
can de-duplicate across polls / multiple tabs.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from app.db import get_session

router = APIRouter(prefix='/api/radio')

LAGOS_TZ = timezone(timedelta(hours=1))


def _parse_since(since: Optional[str]) -> datetime:
    if since:
        try:
            s = since.replace('Z', '+00:00')
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            pass
    # default: last 5 minutes
    return datetime.now(timezone.utc) - timedelta(minutes=5)


def _money(v) -> str:
    try:
        return f"\u20a6{float(v):,.2f}"
    except Exception:
        return str(v or 0)


async def _table_exists(session: AsyncSession, name: str) -> bool:
    r = await session.execute(text(
        "SELECT 1 FROM information_schema.tables WHERE table_name = :n LIMIT 1"
    ), {"n": name})
    return r.first() is not None


@router.get('/feed')
async def feed(
    since: Optional[str] = Query(None, description="ISO timestamp; only events after this"),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    since_dt = _parse_since(since)
    now = datetime.now(timezone.utc)
    events: List[Dict[str, Any]] = []

    # ── Clock-in events ──────────────────────────────────────────────
    if await _table_exists(session, 'attendance') and await _table_exists(session, 'staff'):
        try:
            rows = (await session.execute(text("""
                SELECT a.id::text AS id, a.clock_in, s.first_name, s.last_name, s.position
                FROM attendance a JOIN staff s ON s.id = a.staff_id
                WHERE a.clock_in > :since
                ORDER BY a.clock_in DESC LIMIT :lim
            """), {"since": since_dt, "lim": limit})).mappings().all()
            for r in rows:
                name = f"{r['first_name']} {r['last_name']}".strip()
                events.append({
                    "id": f"clockin:{r['id']}",
                    "type": "clock_in",
                    "title": "Clock In",
                    "message": f"{name} has clocked in.",
                    "priority": 2,
                    "created_at": r['clock_in'].isoformat() if r['clock_in'] else now.isoformat(),
                })
        except Exception:
            pass

        # ── Clock-out events ─────────────────────────────────────────
        try:
            rows = (await session.execute(text("""
                SELECT a.id::text AS id, a.clock_out, a.hours_worked,
                       s.first_name, s.last_name
                FROM attendance a JOIN staff s ON s.id = a.staff_id
                WHERE a.clock_out IS NOT NULL AND a.clock_out > :since
                ORDER BY a.clock_out DESC LIMIT :lim
            """), {"since": since_dt, "lim": limit})).mappings().all()
            for r in rows:
                name = f"{r['first_name']} {r['last_name']}".strip()
                hrs = float(r['hours_worked'] or 0)
                events.append({
                    "id": f"clockout:{r['id']}",
                    "type": "clock_out",
                    "title": "Clock Out",
                    "message": f"{name} has clocked out after {hrs:.1f} hours.",
                    "priority": 2,
                    "created_at": r['clock_out'].isoformat(),
                })
        except Exception:
            pass

    # ── New sales orders ─────────────────────────────────────────────
    if await _table_exists(session, 'sales_orders'):
        try:
            rows = (await session.execute(text("""
                SELECT so.id::text AS id, so.order_number, so.total_amount,
                       so.created_at, c.name AS customer
                FROM sales_orders so
                LEFT JOIN customers c ON c.id = so.customer_id
                WHERE so.created_at > :since
                ORDER BY so.created_at DESC LIMIT :lim
            """), {"since": since_dt, "lim": limit})).mappings().all()
            for r in rows:
                events.append({
                    "id": f"sales:{r['id']}",
                    "type": "sales_order",
                    "title": "New Sales Order",
                    "message": f"Sales order {r['order_number']} created for {r['customer'] or 'customer'}, total {_money(r['total_amount'])}.",
                    "priority": 3,
                    "created_at": r['created_at'].isoformat(),
                })
        except Exception:
            pass

    # ── New invoices ─────────────────────────────────────────────────
    if await _table_exists(session, 'invoices'):
        try:
            rows = (await session.execute(text("""
                SELECT inv.id::text AS id, inv.invoice_number, inv.total_amount,
                       inv.created_at, c.name AS customer
                FROM invoices inv
                LEFT JOIN customers c ON c.id = inv.customer_id
                WHERE inv.created_at > :since
                ORDER BY inv.created_at DESC LIMIT :lim
            """), {"since": since_dt, "lim": limit})).mappings().all()
            for r in rows:
                events.append({
                    "id": f"invoice:{r['id']}",
                    "type": "invoice",
                    "title": "New Invoice",
                    "message": f"Invoice {r['invoice_number']} issued to {r['customer'] or 'customer'} for {_money(r['total_amount'])}.",
                    "priority": 4,
                    "created_at": r['created_at'].isoformat(),
                })
        except Exception:
            pass

    # ── New payments ─────────────────────────────────────────────────
    if await _table_exists(session, 'payments'):
        try:
            rows = (await session.execute(text("""
                SELECT id::text AS id, amount, payment_date, created_at
                FROM payments WHERE created_at > :since
                ORDER BY created_at DESC LIMIT :lim
            """), {"since": since_dt, "lim": limit})).mappings().all()
            for r in rows:
                events.append({
                    "id": f"payment:{r['id']}",
                    "type": "payment",
                    "title": "Payment Received",
                    "message": f"Payment of {_money(r['amount'])} received.",
                    "priority": 3,
                    "created_at": (r['created_at'] or r['payment_date']).isoformat(),
                })
        except Exception:
            pass

    # ── New production orders ────────────────────────────────────────
    if await _table_exists(session, 'production_orders'):
        try:
            rows = (await session.execute(text("""
                SELECT po.id::text AS id, po.order_number, po.quantity_planned,
                       po.created_at, p.name AS product
                FROM production_orders po
                LEFT JOIN products p ON p.id = po.product_id
                WHERE po.created_at > :since
                ORDER BY po.created_at DESC LIMIT :lim
            """), {"since": since_dt, "lim": limit})).mappings().all()
            for r in rows:
                qty = float(r['quantity_planned'] or 0)
                events.append({
                    "id": f"production:{r['id']}",
                    "type": "production_order",
                    "title": "New Production Order",
                    "message": f"Production order {r['order_number']} scheduled: {qty:g} units of {r['product'] or 'product'}.",
                    "priority": 3,
                    "created_at": r['created_at'].isoformat(),
                })
        except Exception:
            pass

    # ── Stock movements (raw material entries / IN) ──────────────────
    if await _table_exists(session, 'stock_movements'):
        try:
            rows = (await session.execute(text("""
                SELECT sm.id::text AS id, sm.movement_type, sm.quantity, sm.reference,
                       sm.created_at,
                       COALESCE(rm.name, p.name) AS item_name,
                       w.name AS warehouse
                FROM stock_movements sm
                LEFT JOIN raw_materials rm ON rm.id = sm.raw_material_id
                LEFT JOIN products p ON p.id = sm.product_id
                LEFT JOIN warehouses w ON w.id = sm.warehouse_id
                WHERE sm.created_at > :since AND sm.movement_type = 'IN'
                ORDER BY sm.created_at DESC LIMIT :lim
            """), {"since": since_dt, "lim": limit})).mappings().all()
            for r in rows:
                qty = float(r['quantity'] or 0)
                events.append({
                    "id": f"stockin:{r['id']}",
                    "type": "stock_in",
                    "title": "Stock Entry",
                    "message": f"Stock IN: {qty:g} of {r['item_name'] or 'item'} into {r['warehouse'] or 'warehouse'}.",
                    "priority": 2,
                    "created_at": r['created_at'].isoformat(),
                })
        except Exception:
            pass

    # ── Due scheduled announcements ──────────────────────────────────
    if await _table_exists(session, 'announcements'):
        try:
            now_local = datetime.now(LAGOS_TZ)
            today_local = now_local.date()
            current_min = now_local.hour * 60 + now_local.minute
            window = 2  # ± minutes
            rows = (await session.execute(text("""
                SELECT id::text AS id, title, message, audio_filename,
                       scheduled_date, scheduled_time, repeat_type, repeat_days
                FROM announcements WHERE is_active = true
            """))).mappings().all()
            weekday = today_local.weekday()
            for a in rows:
                t = a['scheduled_time']
                if t is None:
                    continue
                t_min = t.hour * 60 + t.minute
                if abs(t_min - current_min) > window:
                    continue
                rt = (a['repeat_type'] or 'once').lower()
                ok = False
                if rt == 'once':
                    if a['scheduled_date'] and a['scheduled_date'] == today_local:
                        ok = True
                elif rt == 'daily':
                    ok = True
                elif rt == 'weekly':
                    days = [int(x) for x in (a['repeat_days'] or '').split(',') if x.strip().isdigit()]
                    ok = weekday in days
                elif rt == 'monthly':
                    if a['scheduled_date']:
                        ok = a['scheduled_date'].day == today_local.day
                if not ok:
                    continue
                audio_url = f"/api/announcements/audio/{a['audio_filename']}" if a['audio_filename'] else None
                # Bucket by minute so each due slot fires once per minute window
                bucket = now_local.strftime('%Y%m%d%H%M')
                events.append({
                    "id": f"announce:{a['id']}:{bucket}",
                    "type": "announcement",
                    "title": a['title'] or "Announcement",
                    "message": a['message'] or "",
                    "audio_url": audio_url,
                    "priority": 5,
                    "created_at": now.isoformat(),
                })
        except Exception:
            pass

    # ── Sort newest first, cap to limit ──────────────────────────────
    events.sort(key=lambda e: e['created_at'], reverse=True)
    return {"now": now.isoformat(), "since": since_dt.isoformat(), "events": events[:limit]}
