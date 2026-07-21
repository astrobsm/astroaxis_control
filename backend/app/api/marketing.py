"""
Marketer Module API
- Weekly marketing plans (CRUD)
- Daily activity logs (CRUD)
- Marketing proposals with execution tracking (CRUD)
- Read-only product/price viewing
- Place orders for customers (delegates to sales)
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Optional
from datetime import date, datetime, time as time_type

from app.db import get_session

router = APIRouter(prefix='/api/marketing', tags=['Marketing'])


# ======================== HELPERS ========================

def _plan_to_dict(r):
    return {
        "id": str(r.id), "marketer_staff_id": str(r.marketer_staff_id) if r.marketer_staff_id else None,
        "week_start": str(r.week_start), "week_end": str(r.week_end),
        "title": r.title, "objectives": r.objectives or '',
        "target_areas": r.target_areas or '', "target_customers": r.target_customers or '',
        "planned_visits": r.planned_visits, "planned_calls": r.planned_calls,
        "budget_requested": float(r.budget_requested or 0), "budget_approved": float(r.budget_approved or 0),
        "status": r.status, "manager_notes": r.manager_notes or '',
        "created_at": str(r.created_at), "updated_at": str(r.updated_at),
    }

def _log_to_dict(r):
    return {
        "id": str(r.id), "marketer_staff_id": str(r.marketer_staff_id) if r.marketer_staff_id else None,
        "log_date": str(r.log_date),
        "start_time": str(r.start_time) if r.start_time else '',
        "end_time": str(r.end_time) if r.end_time else '',
        "location_visited": r.location_visited or '',
        "customer_contacted": r.customer_contacted or '',
        "contact_type": r.contact_type or 'visit',
        "objective": r.objective or '',
        "activities_performed": r.activities_performed,
        "outcome": r.outcome or '',
        "products_discussed": r.products_discussed or '',
        "samples_distributed": r.samples_distributed or 0,
        "orders_generated": r.orders_generated or 0,
        "order_value": float(r.order_value or 0),
        "challenges": r.challenges or '',
        "follow_up_required": r.follow_up_required or False,
        "follow_up_date": str(r.follow_up_date) if r.follow_up_date else '',
        "follow_up_notes": r.follow_up_notes or '',
        "mood_rating": r.mood_rating or 3,
        "created_at": str(r.created_at),
    }

def _proposal_to_dict(r):
    return {
        "id": str(r.id), "marketer_staff_id": str(r.marketer_staff_id) if r.marketer_staff_id else None,
        "title": r.title, "proposal_type": r.proposal_type or 'campaign',
        "target_audience": r.target_audience or '',
        "description": r.description, "strategy": r.strategy or '',
        "expected_outcome": r.expected_outcome or '',
        "budget_estimate": float(r.budget_estimate or 0),
        "timeline_start": str(r.timeline_start) if r.timeline_start else '',
        "timeline_end": str(r.timeline_end) if r.timeline_end else '',
        "products_involved": r.products_involved or '',
        "channels": r.channels or '', "kpi_metrics": r.kpi_metrics or '',
        "status": r.status, "approval_notes": r.approval_notes or '',
        "execution_progress": r.execution_progress or 0,
        "execution_notes": r.execution_notes or '',
        "actual_spend": float(r.actual_spend or 0),
        "actual_outcome": r.actual_outcome or '',
        "created_at": str(r.created_at), "updated_at": str(r.updated_at),
    }


# ======================== DASHBOARD SUMMARY ========================

@router.get('/dashboard')
async def marketer_dashboard(session: AsyncSession = Depends(get_session)):
    """Get marketer dashboard summary - plans this week, logs today, active proposals."""
    today = date.today()
    
    # Active plans count
    r1 = await session.execute(text(
        "SELECT COUNT(*) FROM marketing_plans WHERE status IN ('submitted','approved','in_progress') AND week_end >= :today"
    ), {"today": today})
    active_plans = r1.scalar_one()

    # Today's logs
    r2 = await session.execute(text(
        "SELECT COUNT(*) FROM marketing_daily_logs WHERE log_date = :today"
    ), {"today": today})
    today_logs = r2.scalar_one()

    # Active proposals
    r3 = await session.execute(text(
        "SELECT COUNT(*) FROM marketing_proposals WHERE status IN ('submitted','approved','in_progress')"
    ))
    active_proposals = r3.scalar_one()

    # Pending follow-ups
    r4 = await session.execute(text(
        "SELECT COUNT(*) FROM marketing_daily_logs WHERE follow_up_required = true AND (follow_up_date IS NULL OR follow_up_date <= :today)"
    ), {"today": today})
    pending_followups = r4.scalar_one()

    # This week stats
    from datetime import timedelta
    week_start = today - timedelta(days=today.weekday())
    r5 = await session.execute(text(
        "SELECT COUNT(*), COALESCE(SUM(orders_generated),0), COALESCE(SUM(order_value),0) FROM marketing_daily_logs WHERE log_date >= :ws AND log_date <= :today"
    ), {"ws": week_start, "today": today})
    row = r5.fetchone()

    # Unique customers contacted this week
    r6 = await session.execute(text(
        "SELECT COUNT(DISTINCT customer_contacted) FROM marketing_daily_logs WHERE log_date >= :ws AND log_date <= :today AND customer_contacted IS NOT NULL AND customer_contacted != ''"
    ), {"ws": week_start, "today": today})
    customers_contacted = r6.scalar_one()

    # Samples given this week
    r7 = await session.execute(text(
        "SELECT COALESCE(SUM(samples_distributed),0) FROM marketing_daily_logs WHERE log_date >= :ws AND log_date <= :today"
    ), {"ws": week_start, "today": today})
    samples_given = r7.scalar_one()

    # Recent logs (last 5)
    r8 = await session.execute(text("""
        SELECT ml.*, s.first_name || ' ' || s.last_name as marketer_name
        FROM marketing_daily_logs ml
        LEFT JOIN staff s ON ml.marketer_staff_id = s.id
        ORDER BY ml.log_date DESC, ml.created_at DESC LIMIT 5
    """))
    recent_logs = []
    for rl in r8.fetchall():
        recent_logs.append({
            "log_date": str(rl.log_date), "marketer_name": rl.marketer_name or '',
            "location_visited": rl.location_visited or '', "customer_contacted": rl.customer_contacted or '',
            "outcome": rl.outcome or '', "orders_generated": rl.orders_generated or 0,
        })
    
    return {
        "active_plans": active_plans,
        "today_logs": today_logs,
        "active_proposals": active_proposals,
        "pending_followups": pending_followups,
        "week_stats": {
            "total_logs": row[0],
            "total_orders": row[1],
            "total_order_value": float(row[2]),
            "customers_contacted": customers_contacted,
            "samples_given": samples_given,
        },
        "recent_logs": recent_logs,
    }


# ======================== WEEKLY PLANS ========================

@router.post('/plans')
async def create_plan(data: dict, session: AsyncSession = Depends(get_session)):
    ws = data.get('week_start', '')
    we = data.get('week_end', '')
    if not ws or not we or not data.get('title'):
        raise HTTPException(400, "week_start, week_end and title are required")
    result = await session.execute(text("""
        INSERT INTO marketing_plans (id, marketer_staff_id, week_start, week_end, title, objectives,
            target_areas, target_customers, planned_visits, planned_calls, budget_requested, status, created_at, updated_at)
        VALUES (gen_random_uuid(), :staff_id, :ws, :we, :title, :objectives, :target_areas, :target_customers,
            :planned_visits, :planned_calls, :budget_requested, :status, NOW(), NOW())
        RETURNING *
    """), {
        "staff_id": data.get('marketer_staff_id') or None,
        "ws": date.fromisoformat(ws), "we": date.fromisoformat(we),
        "title": data['title'], "objectives": data.get('objectives', ''),
        "target_areas": data.get('target_areas', ''), "target_customers": data.get('target_customers', ''),
        "planned_visits": int(data.get('planned_visits', 0)),
        "planned_calls": int(data.get('planned_calls', 0)),
        "budget_requested": float(data.get('budget_requested', 0)),
        "status": data.get('status', 'submitted'),
    })
    row = result.fetchone()
    await session.commit()
    return {"success": True, "data": _plan_to_dict(row)}

@router.get('/plans')
async def list_plans(page: int = Query(1, ge=1), size: int = Query(50), status: Optional[str] = None,
                     session: AsyncSession = Depends(get_session)):
    where = ""
    params = {"limit": size, "offset": (page - 1) * size}
    if status:
        where = "WHERE status = :status"
        params["status"] = status
    count_r = await session.execute(text(f"SELECT COUNT(*) FROM marketing_plans {where}"), params)
    total = count_r.scalar_one()
    result = await session.execute(text(f"""
        SELECT mp.*, s.first_name, s.last_name FROM marketing_plans mp
        LEFT JOIN staff s ON mp.marketer_staff_id = s.id
        {where} ORDER BY mp.week_start DESC LIMIT :limit OFFSET :offset
    """), params)
    items = []
    for r in result.fetchall():
        d = _plan_to_dict(r)
        d['marketer_name'] = f"{r.first_name} {r.last_name}" if r.first_name else 'Unassigned'
        items.append(d)
    return {"items": items, "total": total}

@router.put('/plans/{plan_id}')
async def update_plan(plan_id: str, data: dict, session: AsyncSession = Depends(get_session)):
    fields, params = [], {"id": plan_id}
    updatable = ['title','objectives','target_areas','target_customers','planned_visits','planned_calls',
                 'budget_requested','budget_approved','status','manager_notes','week_start','week_end']
    date_fields = {'week_start','week_end'}
    numeric_fields = {'planned_visits','planned_calls','budget_requested','budget_approved'}
    for f in updatable:
        if f in data:
            val = data[f]
            if f in date_fields:
                val = date.fromisoformat(val) if val else None
            elif f in numeric_fields:
                val = float(val) if val not in ('', None) else 0
            fields.append(f"{f} = :{f}")
            params[f] = val
    if not fields:
        raise HTTPException(400, "No fields to update")
    fields.append("updated_at = NOW()")
    result = await session.execute(
        text(f"UPDATE marketing_plans SET {', '.join(fields)} WHERE id = :id RETURNING *"), params)
    row = result.fetchone()
    if not row:
        raise HTTPException(404, "Plan not found")
    await session.commit()
    return {"success": True, "data": _plan_to_dict(row)}

@router.post('/plans/{plan_id}/approve')
async def approve_plan(plan_id: str, data: dict = {}, session: AsyncSession = Depends(get_session)):
    """Approve a marketing plan and automatically add its budget to expenses for the month."""
    # Get plan details
    result = await session.execute(text(
        "SELECT * FROM marketing_plans WHERE id = :id"
    ), {"id": plan_id})
    plan = result.fetchone()
    if not plan:
        raise HTTPException(404, "Plan not found")
    if plan.status == 'approved':
        raise HTTPException(400, "Plan is already approved")

    budget = float(plan.budget_requested or 0)
    budget_approved = float(data.get('budget_approved', budget))
    manager_notes = data.get('manager_notes', '')

    # Update plan status to approved
    await session.execute(text("""
        UPDATE marketing_plans
        SET status = 'approved', budget_approved = :ba, manager_notes = :mn, updated_at = NOW()
        WHERE id = :id
    """), {"id": plan_id, "ba": budget_approved, "mn": manager_notes})

    # Auto-create expense record for the approved marketing budget
    import uuid as _uuid
    exp_num = f"EXP-{date.today().strftime('%Y%m%d')}-{str(_uuid.uuid4())[:8].upper()}"
    await session.execute(text("""
        INSERT INTO expense_records
            (expense_number, category, subcategory, description, amount,
             payment_method, payment_reference, payment_date, recipient, approved_by, notes)
        VALUES (:en, 'marketing', 'marketing_plan_budget', :desc, :amt,
                'budget_allocation', :pr, :pd, :recv, :ab, :notes)
    """), {
        "en": exp_num,
        "desc": f"Marketing Plan: {plan.title} ({plan.week_start} to {plan.week_end})",
        "amt": budget_approved,
        "pr": f"Plan-{plan_id[:8]}",
        "pd": date.today(),
        "recv": 'Marketing Department',
        "ab": data.get('approved_by', 'Admin'),
        "notes": f"Auto-generated from approved marketing plan: {plan.title}. Budget requested: {budget}, approved: {budget_approved}"
    })

    await session.commit()
    return {
        "success": True,
        "message": f"Plan approved. Budget of {budget_approved:,.2f} added to expenses.",
        "expense_number": exp_num
    }


@router.delete('/plans/{plan_id}')
async def delete_plan(plan_id: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(text("DELETE FROM marketing_plans WHERE id = :id RETURNING id"), {"id": plan_id})
    if not result.fetchone():
        raise HTTPException(404, "Plan not found")
    await session.commit()
    return {"success": True, "message": "Plan deleted"}


# ======================== DAILY ACTIVITY LOGS ========================

@router.post('/logs')
async def create_log(data: dict, session: AsyncSession = Depends(get_session)):
    if not data.get('log_date') or not data.get('activities_performed'):
        raise HTTPException(400, "log_date and activities_performed are required")
    ld = date.fromisoformat(data['log_date'])
    fu_date = data.get('follow_up_date', '') or None
    if fu_date:
        fu_date = date.fromisoformat(fu_date)
    result = await session.execute(text("""
        INSERT INTO marketing_daily_logs (id, marketer_staff_id, log_date, start_time, end_time,
            location_visited, customer_contacted, contact_type, objective, activities_performed,
            outcome, products_discussed, samples_distributed, orders_generated, order_value,
            challenges, follow_up_required, follow_up_date, follow_up_notes, mood_rating, created_at)
        VALUES (gen_random_uuid(), :staff_id, :log_date, :start_time, :end_time,
            :location_visited, :customer_contacted, :contact_type, :objective, :activities_performed,
            :outcome, :products_discussed, :samples_distributed, :orders_generated, :order_value,
            :challenges, :follow_up_required, :follow_up_date, :follow_up_notes, :mood_rating, NOW())
        RETURNING *
    """), {
        "staff_id": data.get('marketer_staff_id') or None,
        "log_date": ld,
        "start_time": datetime.strptime(data['start_time'], '%H:%M').time() if isinstance(data.get('start_time'), str) and data.get('start_time') else (data.get('start_time') if isinstance(data.get('start_time'), time_type) else None),
        "end_time": datetime.strptime(data['end_time'], '%H:%M').time() if isinstance(data.get('end_time'), str) and data.get('end_time') else (data.get('end_time') if isinstance(data.get('end_time'), time_type) else None),
        "location_visited": data.get('location_visited', ''),
        "customer_contacted": data.get('customer_contacted', ''),
        "contact_type": data.get('contact_type', 'visit'),
        "objective": data.get('objective', ''),
        "activities_performed": data['activities_performed'],
        "outcome": data.get('outcome', ''),
        "products_discussed": data.get('products_discussed', ''),
        "samples_distributed": int(data.get('samples_distributed', 0)),
        "orders_generated": int(data.get('orders_generated', 0)),
        "order_value": float(data.get('order_value', 0)),
        "challenges": data.get('challenges', ''),
        "follow_up_required": bool(data.get('follow_up_required', False)),
        "follow_up_date": fu_date,
        "follow_up_notes": data.get('follow_up_notes', ''),
        "mood_rating": int(data.get('mood_rating', 3)),
    })
    row = result.fetchone()
    await session.commit()
    return {"success": True, "data": _log_to_dict(row)}

@router.get('/logs')
async def list_logs(page: int = Query(1, ge=1), size: int = Query(50),
                    start_date: Optional[str] = None, end_date: Optional[str] = None,
                    staff_id: Optional[str] = None,
                    session: AsyncSession = Depends(get_session)):
    where_parts = []
    params = {"limit": size, "offset": (page - 1) * size}
    if start_date:
        where_parts.append("ml.log_date >= :start_date")
        params["start_date"] = date.fromisoformat(start_date)
    if end_date:
        where_parts.append("ml.log_date <= :end_date")
        params["end_date"] = date.fromisoformat(end_date)
    if staff_id:
        where_parts.append("ml.marketer_staff_id = :staff_id")
        params["staff_id"] = staff_id
    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    count_r = await session.execute(text(f"SELECT COUNT(*) FROM marketing_daily_logs ml {where}"), params)
    total = count_r.scalar_one()
    result = await session.execute(text(f"""
        SELECT ml.*, s.first_name, s.last_name FROM marketing_daily_logs ml
        LEFT JOIN staff s ON ml.marketer_staff_id = s.id
        {where} ORDER BY ml.log_date DESC, ml.created_at DESC LIMIT :limit OFFSET :offset
    """), params)
    items = []
    for r in result.fetchall():
        d = _log_to_dict(r)
        d['marketer_name'] = f"{r.first_name} {r.last_name}" if r.first_name else 'Unassigned'
        items.append(d)
    return {"items": items, "total": total}

@router.get('/logs/follow-ups')
async def get_pending_followups(session: AsyncSession = Depends(get_session)):
    """Get all logs with pending follow-ups."""
    result = await session.execute(text("""
        SELECT ml.*, s.first_name, s.last_name FROM marketing_daily_logs ml
        LEFT JOIN staff s ON ml.marketer_staff_id = s.id
        WHERE ml.follow_up_required = true
        ORDER BY ml.follow_up_date ASC NULLS FIRST, ml.log_date DESC
    """))
    items = []
    for r in result.fetchall():
        d = _log_to_dict(r)
        d['marketer_name'] = f"{r.first_name} {r.last_name}" if r.first_name else 'Unassigned'
        items.append(d)
    return {"items": items, "count": len(items)}

@router.put('/logs/{log_id}')
async def update_log(log_id: str, data: dict, session: AsyncSession = Depends(get_session)):
    fields, params = [], {"id": log_id}
    updatable = ['log_date','start_time','end_time','location_visited','customer_contacted','contact_type',
                 'objective','activities_performed','outcome','products_discussed','samples_distributed',
                 'orders_generated','order_value','challenges','follow_up_required','follow_up_date',
                 'follow_up_notes','mood_rating']
    date_fields = {'log_date', 'follow_up_date'}
    numeric_fields = {'samples_distributed','orders_generated','order_value','mood_rating'}
    for f in updatable:
        if f in data:
            val = data[f]
            if f in date_fields:
                val = date.fromisoformat(val) if val else None
            elif f in numeric_fields:
                val = float(val) if val not in ('', None) else 0
            elif f == 'follow_up_required':
                val = bool(val)
            fields.append(f"{f} = :{f}")
            params[f] = val
    if not fields:
        raise HTTPException(400, "No fields")
    result = await session.execute(text(f"UPDATE marketing_daily_logs SET {', '.join(fields)} WHERE id = :id RETURNING *"), params)
    row = result.fetchone()
    if not row:
        raise HTTPException(404, "Log not found")
    await session.commit()
    return {"success": True, "data": _log_to_dict(row)}

@router.delete('/logs/{log_id}')
async def delete_log(log_id: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(text("DELETE FROM marketing_daily_logs WHERE id = :id RETURNING id"), {"id": log_id})
    if not result.fetchone():
        raise HTTPException(404, "Log not found")
    await session.commit()
    return {"success": True, "message": "Log deleted"}


# ======================== MARKETING PROPOSALS ========================

@router.post('/proposals')
async def create_proposal(data: dict, session: AsyncSession = Depends(get_session)):
    if not data.get('title') or not data.get('description'):
        raise HTTPException(400, "title and description required")
    ts = data.get('timeline_start', '') or None
    te = data.get('timeline_end', '') or None
    if ts: ts = date.fromisoformat(ts)
    if te: te = date.fromisoformat(te)
    result = await session.execute(text("""
        INSERT INTO marketing_proposals (id, marketer_staff_id, title, proposal_type, target_audience,
            description, strategy, expected_outcome, budget_estimate, timeline_start, timeline_end,
            products_involved, channels, kpi_metrics, status, created_at, updated_at)
        VALUES (gen_random_uuid(), :staff_id, :title, :type, :audience, :desc, :strategy, :outcome,
            :budget, :ts, :te, :products, :channels, :kpi, :status, NOW(), NOW())
        RETURNING *
    """), {
        "staff_id": data.get('marketer_staff_id') or None,
        "title": data['title'], "type": data.get('proposal_type', 'campaign'),
        "audience": data.get('target_audience', ''), "desc": data['description'],
        "strategy": data.get('strategy', ''), "outcome": data.get('expected_outcome', ''),
        "budget": float(data.get('budget_estimate', 0)),
        "ts": ts, "te": te,
        "products": data.get('products_involved', ''), "channels": data.get('channels', ''),
        "kpi": data.get('kpi_metrics', ''), "status": data.get('status', 'draft'),
    })
    row = result.fetchone()
    await session.commit()
    return {"success": True, "data": _proposal_to_dict(row)}

@router.get('/proposals')
async def list_proposals(page: int = Query(1, ge=1), size: int = Query(50), status: Optional[str] = None,
                         session: AsyncSession = Depends(get_session)):
    where = ""
    params = {"limit": size, "offset": (page - 1) * size}
    if status:
        where = "WHERE mp.status = :status"
        params["status"] = status
    count_r = await session.execute(text(f"SELECT COUNT(*) FROM marketing_proposals mp {where}"), params)
    total = count_r.scalar_one()
    result = await session.execute(text(f"""
        SELECT mp.*, s.first_name, s.last_name FROM marketing_proposals mp
        LEFT JOIN staff s ON mp.marketer_staff_id = s.id
        {where} ORDER BY mp.created_at DESC LIMIT :limit OFFSET :offset
    """), params)
    items = []
    for r in result.fetchall():
        d = _proposal_to_dict(r)
        d['marketer_name'] = f"{r.first_name} {r.last_name}" if r.first_name else 'Unassigned'
        items.append(d)
    return {"items": items, "total": total}

@router.put('/proposals/{proposal_id}')
async def update_proposal(proposal_id: str, data: dict, session: AsyncSession = Depends(get_session)):
    fields, params = [], {"id": proposal_id}
    updatable = ['title','proposal_type','target_audience','description','strategy','expected_outcome',
                 'budget_estimate','timeline_start','timeline_end','products_involved','channels',
                 'kpi_metrics','status','approval_notes','execution_progress','execution_notes',
                 'actual_spend','actual_outcome']
    date_fields = {'timeline_start','timeline_end'}
    numeric_fields = {'budget_estimate','execution_progress','actual_spend'}
    for f in updatable:
        if f in data:
            val = data[f]
            if f in date_fields:
                val = date.fromisoformat(val) if val else None
            elif f in numeric_fields:
                val = float(val) if val not in ('', None) else 0
            fields.append(f"{f} = :{f}")
            params[f] = val
    if not fields:
        raise HTTPException(400, "No fields")
    fields.append("updated_at = NOW()")
    result = await session.execute(text(f"UPDATE marketing_proposals SET {', '.join(fields)} WHERE id = :id RETURNING *"), params)
    row = result.fetchone()
    if not row:
        raise HTTPException(404, "Proposal not found")
    await session.commit()
    return {"success": True, "data": _proposal_to_dict(row)}

@router.delete('/proposals/{proposal_id}')
async def delete_proposal(proposal_id: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(text("DELETE FROM marketing_proposals WHERE id = :id RETURNING id"), {"id": proposal_id})
    if not result.fetchone():
        raise HTTPException(404, "Proposal not found")
    await session.commit()
    return {"success": True, "message": "Proposal deleted"}


# ======================== READ-ONLY PRODUCTS/PRICES ========================

@router.get('/products-catalog')
async def view_products_catalog(session: AsyncSession = Depends(get_session)):
    """Read-only product catalog with pricing for marketers."""
    result = await session.execute(text("""
        SELECT p.id, p.name, p.sku, p.description, p.manufacturer,
            p.unit as product_unit, p.selling_price as p_selling, p.retail_price as p_retail,
            p.wholesale_price as p_wholesale,
            pp.unit as pp_unit, pp.retail_price as pp_retail, pp.wholesale_price as pp_wholesale
        FROM products p
        LEFT JOIN product_pricing pp ON p.id = pp.product_id
        ORDER BY p.name
    """))
    products = {}
    for r in result.fetchall():
        pid = str(r.id)
        if pid not in products:
            products[pid] = {
                "id": pid, "product_name": r.name, "sku": r.sku or '',
                "description": r.description or '', "category": r.manufacturer or '',
                "selling_price": float(r.p_selling or r.p_retail or 0),
                "wholesale_price": float(r.p_wholesale or 0),
                "unit_of_measure": r.product_unit or '',
                "pricing": []
            }
        if r.pp_unit:
            products[pid]["pricing"].append({
                "unit": r.pp_unit,
                "retail_price": float(r.pp_retail or 0),
                "wholesale_price": float(r.pp_wholesale or 0),
            })
    return {"items": list(products.values()), "total": len(products)}

# ======================== HEALTHCARE FACILITIES ========================

_FACILITIES_TABLE_READY = False

async def _ensure_facilities_table(session: AsyncSession):
    """Create marketing_facilities table on first use (idempotent)."""
    global _FACILITIES_TABLE_READY
    if _FACILITIES_TABLE_READY:
        return
    await session.execute(text("""
        CREATE TABLE IF NOT EXISTS marketing_facilities (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            marketer_staff_id UUID REFERENCES staff(id) ON DELETE SET NULL,
            facility_name TEXT NOT NULL,
            facility_type TEXT,
            address TEXT,
            city TEXT,
            state TEXT,
            contact_person TEXT,
            contact_title TEXT,
            contact_phone TEXT,
            contact_email TEXT,
            secondary_contact TEXT,
            secondary_phone TEXT,
            relationship_status TEXT DEFAULT 'prospect',
            products_of_interest TEXT,
            last_visit_date DATE,
            next_visit_date DATE,
            visit_frequency TEXT,
            estimated_monthly_value NUMERIC(14,2) DEFAULT 0,
            notes TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """))
    await session.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_mkt_fac_marketer ON marketing_facilities(marketer_staff_id)"
    ))
    await session.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_mkt_fac_status ON marketing_facilities(relationship_status)"
    ))
    await session.commit()
    _FACILITIES_TABLE_READY = True


def _facility_to_dict(r):
    return {
        "id": str(r.id),
        "marketer_staff_id": str(r.marketer_staff_id) if r.marketer_staff_id else None,
        "marketer_name": (
            f"{r.first_name} {r.last_name}".strip()
            if hasattr(r, 'first_name') and r.first_name else ''
        ),
        "facility_name": r.facility_name,
        "facility_type": r.facility_type or '',
        "address": r.address or '',
        "city": r.city or '',
        "state": r.state or '',
        "contact_person": r.contact_person or '',
        "contact_title": r.contact_title or '',
        "contact_phone": r.contact_phone or '',
        "contact_email": r.contact_email or '',
        "secondary_contact": r.secondary_contact or '',
        "secondary_phone": r.secondary_phone or '',
        "relationship_status": r.relationship_status or 'prospect',
        "products_of_interest": r.products_of_interest or '',
        "last_visit_date": str(r.last_visit_date) if r.last_visit_date else '',
        "next_visit_date": str(r.next_visit_date) if r.next_visit_date else '',
        "visit_frequency": r.visit_frequency or '',
        "estimated_monthly_value": float(r.estimated_monthly_value or 0),
        "notes": r.notes or '',
        "created_at": str(r.created_at),
        "updated_at": str(r.updated_at),
    }


@router.post('/facilities')
async def create_facility(data: dict, session: AsyncSession = Depends(get_session)):
    await _ensure_facilities_table(session)
    if not data.get('facility_name'):
        raise HTTPException(400, "facility_name is required")
    cols = [
        'marketer_staff_id', 'facility_name', 'facility_type', 'address', 'city', 'state',
        'contact_person', 'contact_title', 'contact_phone', 'contact_email',
        'secondary_contact', 'secondary_phone', 'relationship_status',
        'products_of_interest', 'last_visit_date', 'next_visit_date',
        'visit_frequency', 'estimated_monthly_value', 'notes'
    ]
    params = {}
    for c in cols:
        v = data.get(c)
        if c in ('last_visit_date', 'next_visit_date'):
            params[c] = date.fromisoformat(v) if v else None
        elif c == 'estimated_monthly_value':
            params[c] = float(v) if v not in ('', None) else 0
        else:
            params[c] = v if v not in ('',) else None
    sql = f"""
        INSERT INTO marketing_facilities ({', '.join(cols)})
        VALUES ({', '.join(':' + c for c in cols)})
        RETURNING *
    """
    result = await session.execute(text(sql), params)
    row = result.fetchone()
    await session.commit()
    return {"success": True, "data": _facility_to_dict(row)}


@router.get('/facilities')
async def list_facilities(
    page: int = Query(1, ge=1),
    size: int = Query(100),
    marketer_staff_id: Optional[str] = None,
    status: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    await _ensure_facilities_table(session)
    where = []
    params = {}
    if marketer_staff_id:
        where.append("f.marketer_staff_id = :mid")
        params['mid'] = marketer_staff_id
    if status:
        where.append("f.relationship_status = :st")
        params['st'] = status
    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''
    count_r = await session.execute(
        text(f"SELECT COUNT(*) FROM marketing_facilities f {where_sql}"), params
    )
    total = count_r.scalar() or 0
    params['limit'] = size
    params['offset'] = (page - 1) * size
    sql = f"""
        SELECT f.*, s.first_name, s.last_name
        FROM marketing_facilities f
        LEFT JOIN staff s ON s.id = f.marketer_staff_id
        {where_sql}
        ORDER BY f.facility_name ASC
        LIMIT :limit OFFSET :offset
    """
    result = await session.execute(text(sql), params)
    items = [_facility_to_dict(r) for r in result.fetchall()]
    return {"items": items, "total": total, "page": page, "size": size}


@router.put('/facilities/{facility_id}')
async def update_facility(facility_id: str, data: dict, session: AsyncSession = Depends(get_session)):
    await _ensure_facilities_table(session)
    updatable = {
        'marketer_staff_id', 'facility_name', 'facility_type', 'address', 'city', 'state',
        'contact_person', 'contact_title', 'contact_phone', 'contact_email',
        'secondary_contact', 'secondary_phone', 'relationship_status',
        'products_of_interest', 'last_visit_date', 'next_visit_date',
        'visit_frequency', 'estimated_monthly_value', 'notes'
    }
    fields = []
    params = {"id": facility_id}
    for f in updatable:
        if f in data:
            v = data[f]
            if f in ('last_visit_date', 'next_visit_date'):
                v = date.fromisoformat(v) if v else None
            elif f == 'estimated_monthly_value':
                v = float(v) if v not in ('', None) else 0
            fields.append(f"{f} = :{f}")
            params[f] = v
    if not fields:
        raise HTTPException(400, "No fields")
    fields.append("updated_at = NOW()")
    result = await session.execute(
        text(f"UPDATE marketing_facilities SET {', '.join(fields)} WHERE id = :id RETURNING *"),
        params,
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(404, "Facility not found")
    await session.commit()
    return {"success": True, "data": _facility_to_dict(row)}


@router.delete('/facilities/{facility_id}')
async def delete_facility(facility_id: str, session: AsyncSession = Depends(get_session)):
    await _ensure_facilities_table(session)
    result = await session.execute(
        text("DELETE FROM marketing_facilities WHERE id = :id RETURNING id"),
        {"id": facility_id},
    )
    if not result.fetchone():
        raise HTTPException(404, "Facility not found")
    await session.commit()
    return {"success": True, "message": "Facility deleted"}


# ======================== PERFORMANCE / NEW CUSTOMERS & SALES ========================

@router.get('/performance')
async def marketer_performance(
    marketer_staff_id: Optional[str] = None,
    days: int = Query(30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
):
    """Aggregate marketer performance: new customers attracted, sales volume,
    facility coverage, visit productivity over the trailing window."""
    where_log = ["log_date >= CURRENT_DATE - INTERVAL ':days days'"]
    # NB: SQL interval cannot bind a parameter directly, so embed safely
    days_int = int(days)
    base_where = f"log_date >= CURRENT_DATE - INTERVAL '{days_int} days'"
    params = {}
    mfilter = ""
    if marketer_staff_id:
        mfilter = " AND marketer_staff_id = :mid"
        params['mid'] = marketer_staff_id

    # Per-marketer aggregates
    sql = f"""
        SELECT
            ml.marketer_staff_id,
            s.first_name, s.last_name,
            COUNT(*) AS total_logs,
            COUNT(DISTINCT ml.customer_contacted)
                FILTER (WHERE ml.customer_contacted IS NOT NULL AND ml.customer_contacted <> '')
                AS unique_customers_contacted,
            COUNT(DISTINCT ml.location_visited)
                FILTER (WHERE ml.location_visited IS NOT NULL AND ml.location_visited <> '')
                AS unique_locations,
            COALESCE(SUM(ml.orders_generated), 0) AS total_orders,
            COALESCE(SUM(ml.order_value), 0) AS total_order_value,
            COALESCE(SUM(ml.samples_distributed), 0) AS total_samples,
            COUNT(*) FILTER (WHERE ml.follow_up_required = true) AS pending_followups,
            COALESCE(AVG(NULLIF(ml.mood_rating, 0)), 0) AS avg_mood
        FROM marketing_daily_logs ml
        LEFT JOIN staff s ON s.id = ml.marketer_staff_id
        WHERE {base_where} {mfilter}
        GROUP BY ml.marketer_staff_id, s.first_name, s.last_name
        ORDER BY total_order_value DESC
    """
    result = await session.execute(text(sql), params)
    per_marketer = []
    for r in result.fetchall():
        per_marketer.append({
            "marketer_staff_id": str(r.marketer_staff_id) if r.marketer_staff_id else None,
            "marketer_name": (
                f"{r.first_name or ''} {r.last_name or ''}".strip() or 'Unassigned'
            ),
            "total_logs": int(r.total_logs or 0),
            "unique_customers_contacted": int(r.unique_customers_contacted or 0),
            "unique_locations": int(r.unique_locations or 0),
            "total_orders": int(r.total_orders or 0),
            "total_order_value": float(r.total_order_value or 0),
            "total_samples": int(r.total_samples or 0),
            "pending_followups": int(r.pending_followups or 0),
            "avg_mood": round(float(r.avg_mood or 0), 2),
        })

    # New customers attracted (customers whose name appears in logs and were
    # created in the customers table within the window)
    new_cust_sql = f"""
        SELECT c.id, c.name, c.phone, c.email, c.created_at,
               ml.marketer_staff_id, s.first_name, s.last_name
        FROM customers c
        LEFT JOIN LATERAL (
            SELECT marketer_staff_id, log_date
            FROM marketing_daily_logs
            WHERE customer_contacted ILIKE c.name
              AND {base_where}
            ORDER BY log_date ASC
            LIMIT 1
        ) ml ON TRUE
        LEFT JOIN staff s ON s.id = ml.marketer_staff_id
        WHERE c.created_at >= NOW() - INTERVAL '{days_int} days'
        ORDER BY c.created_at DESC
        LIMIT 100
    """
    new_cust = await session.execute(text(new_cust_sql))
    new_customers = []
    for r in new_cust.fetchall():
        new_customers.append({
            "id": str(r.id),
            "name": r.name,
            "phone": r.phone or '',
            "email": r.email or '',
            "created_at": str(r.created_at),
            "attributed_marketer": (
                f"{r.first_name or ''} {r.last_name or ''}".strip()
                if r.first_name else ''
            ),
        })

    # Top facilities visited
    await _ensure_facilities_table(session)
    top_fac = await session.execute(text(f"""
        SELECT f.facility_name, f.facility_type, f.city, f.relationship_status,
               COUNT(ml.id) AS visit_count,
               COALESCE(SUM(ml.orders_generated), 0) AS orders,
               COALESCE(SUM(ml.order_value), 0) AS value
        FROM marketing_facilities f
        LEFT JOIN marketing_daily_logs ml
            ON ml.location_visited ILIKE f.facility_name
           AND ml.log_date >= CURRENT_DATE - INTERVAL '{days_int} days'
        GROUP BY f.id, f.facility_name, f.facility_type, f.city, f.relationship_status
        ORDER BY value DESC, visit_count DESC
        LIMIT 20
    """))
    top_facilities = []
    for r in top_fac.fetchall():
        top_facilities.append({
            "facility_name": r.facility_name,
            "facility_type": r.facility_type or '',
            "city": r.city or '',
            "relationship_status": r.relationship_status or 'prospect',
            "visit_count": int(r.visit_count or 0),
            "orders": int(r.orders or 0),
            "value": float(r.value or 0),
        })

    return {
        "days": days_int,
        "marketer_filter": marketer_staff_id,
        "per_marketer": per_marketer,
        "new_customers": new_customers,
        "top_facilities": top_facilities,
        "totals": {
            "marketers_active": len(per_marketer),
            "total_orders": sum(m['total_orders'] for m in per_marketer),
            "total_order_value": sum(m['total_order_value'] for m in per_marketer),
            "new_customers_count": len(new_customers),
        }
    }
