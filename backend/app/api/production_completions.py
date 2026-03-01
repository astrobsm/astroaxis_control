from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Optional
from datetime import datetime, date

from app.db import get_session

router = APIRouter(prefix='/api/production-completions', tags=['Production Completions'])


@router.get('/daily-staff-summary')
async def get_daily_staff_summary(
    production_date: str = Query(..., description="Date in YYYY-MM-DD format"),
    session: AsyncSession = Depends(get_session)
):
    """Auto-fetch staff count, total hours worked, and total wages for a given date.
    Regular hours: 9AM-5PM WAT (UTC+1) at hourly_rate
    Overtime: after 5PM WAT at overtime_rate (default 450)
    """
    # Parse string to date object for asyncpg compatibility
    try:
        pdate = date.fromisoformat(production_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    
    # Calculate wages with regular/overtime split
    # 5PM WAT = 4PM UTC (16:00 UTC). We use AT TIME ZONE to handle this.
    result = await session.execute(
        text("""
            WITH shift_data AS (
                SELECT
                    a.staff_id,
                    a.clock_in,
                    a.clock_out,
                    s.hourly_rate,
                    COALESCE(s.overtime_rate, 450) AS overtime_rate,
                    -- 5PM WAT cutoff for this attendance date
                    (a.clock_in::date || ' 17:00:00')::timestamp AT TIME ZONE 'Africa/Lagos' AT TIME ZONE 'UTC' AS cutoff_utc,
                    EXTRACT(EPOCH FROM (a.clock_out - a.clock_in)) / 3600.0 AS total_hrs
                FROM attendance a
                JOIN staff s ON s.id = a.staff_id
                WHERE a.clock_in::date = :pdate
                  AND a.clock_out IS NOT NULL
            )
            SELECT
                COUNT(DISTINCT staff_id) AS staff_count,
                COALESCE(SUM(total_hrs), 0) AS total_hours,
                COALESCE(SUM(
                    CASE
                        WHEN clock_out <= cutoff_utc THEN
                            -- Entirely regular hours
                            total_hrs * hourly_rate
                        WHEN clock_in >= cutoff_utc THEN
                            -- Entirely overtime
                            total_hrs * overtime_rate
                        ELSE
                            -- Split: regular up to cutoff, overtime after
                            (EXTRACT(EPOCH FROM (cutoff_utc - clock_in)) / 3600.0) * hourly_rate
                            + (EXTRACT(EPOCH FROM (clock_out - cutoff_utc)) / 3600.0) * overtime_rate
                    END
                ), 0) AS total_wages,
                COALESCE(SUM(
                    CASE WHEN clock_out > cutoff_utc THEN
                        GREATEST(EXTRACT(EPOCH FROM (clock_out - GREATEST(clock_in, cutoff_utc))) / 3600.0, 0)
                    ELSE 0 END
                ), 0) AS overtime_hours
            FROM shift_data
        """),
        {"pdate": pdate}
    )
    row = result.fetchone()
    return {
        "production_date": production_date,
        "staff_count": int(row.staff_count),
        "total_hours_worked": round(float(row.total_hours), 2),
        "overtime_hours": round(float(row.overtime_hours), 2),
        "total_wages_paid": round(float(row.total_wages), 2)
    }


@router.get('/product-bom-materials')
async def get_product_bom_materials(
    product_id: str = Query(...),
    quantity: int = Query(1, ge=1),
    session: AsyncSession = Depends(get_session)
):
    """Get BOM raw materials for a product with costs calculated for given quantity."""
    result = await session.execute(
        text("""
            SELECT bl.raw_material_id, rm.name, rm.sku, rm.unit, rm.unit_cost,
                   bl.qty_per_unit,
                   (bl.qty_per_unit * :qty) AS total_qty,
                   (bl.qty_per_unit * :qty * rm.unit_cost) AS total_cost
            FROM bom_lines bl
            JOIN boms b ON b.id = bl.bom_id
            JOIN raw_materials rm ON rm.id = bl.raw_material_id
            WHERE b.product_id = :pid
        """),
        {"pid": product_id, "qty": quantity}
    )
    materials = []
    total_rm_cost = 0.0
    for r in result.fetchall():
        cost = float(r.total_cost)
        total_rm_cost += cost
        materials.append({
            "raw_material_id": str(r.raw_material_id),
            "name": r.name, "sku": r.sku, "unit": r.unit,
            "unit_cost": float(r.unit_cost),
            "qty_per_unit": float(r.qty_per_unit),
            "total_qty": float(r.total_qty),
            "total_cost": round(cost, 2)
        })
    return {"materials": materials, "total_raw_material_cost": round(total_rm_cost, 2)}


@router.post('/')
async def create_production_completion(data: dict, session: AsyncSession = Depends(get_session)):
    """Record a production completion with full cost breakdown."""
    try:
        product_id = data['product_id']
        production_date_str = data.get('production_date', str(date.today()))
        try:
            production_date_val = date.fromisoformat(production_date_str)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
        qty_produced = int(data.get('qty_produced', 0))
        qty_damaged = int(data.get('qty_damaged', 0))
        damage_notes = data.get('damage_notes', '')
        staff_count = int(data.get('staff_count', 0))
        total_hours_worked = float(data.get('total_hours_worked', 0))
        total_wages_paid = float(data.get('total_wages_paid', 0))
        energy_cost = float(data.get('energy_cost', 0))
        lunch_cost = float(data.get('lunch_cost', 0))
        warehouse_id = data.get('warehouse_id')
        notes = data.get('notes', '')
        consumables_list = data.get('consumables', [])  # [{consumable_id, quantity}]
        materials_list = data.get('materials', [])  # [{raw_material_id, quantity}]

        # Calculate raw material cost from BOM or provided materials
        raw_material_cost = 0.0
        for mat in materials_list:
            rm_r = await session.execute(
                text("SELECT unit_cost FROM raw_materials WHERE id = :id"),
                {"id": mat['raw_material_id']}
            )
            rm = rm_r.fetchone()
            unit_cost = float(rm.unit_cost) if rm else 0
            qty = float(mat.get('quantity', 0))
            raw_material_cost += unit_cost * qty

        # Calculate consumables cost
        consumables_cost = 0.0
        for con in consumables_list:
            con_r = await session.execute(
                text("SELECT unit_cost FROM production_consumables WHERE id = :id"),
                {"id": con['consumable_id']}
            )
            c = con_r.fetchone()
            unit_cost = float(c.unit_cost) if c else 0
            qty = float(con.get('quantity', 0))
            consumables_cost += unit_cost * qty

        # Total production cost
        total_production_cost = raw_material_cost + consumables_cost + energy_cost + lunch_cost + total_wages_paid
        cost_per_unit = total_production_cost / qty_produced if qty_produced > 0 else 0

        # Insert main record
        result = await session.execute(
            text("""
                INSERT INTO production_completions (
                    id, product_id, production_date, qty_produced, qty_damaged, damage_notes,
                    staff_count, total_hours_worked, total_wages_paid,
                    raw_material_cost, consumables_cost, energy_cost, lunch_cost,
                    total_production_cost, cost_per_unit, warehouse_id, notes, status, created_at
                ) VALUES (
                    gen_random_uuid(), :product_id, :production_date, :qty_produced, :qty_damaged, :damage_notes,
                    :staff_count, :total_hours_worked, :total_wages_paid,
                    :raw_material_cost, :consumables_cost, :energy_cost, :lunch_cost,
                    :total_production_cost, :cost_per_unit, :warehouse_id, :notes, 'Completed', NOW()
                )
                RETURNING id
            """),
            {
                "product_id": product_id,
                "production_date": production_date_val,
                "qty_produced": qty_produced,
                "qty_damaged": qty_damaged,
                "damage_notes": damage_notes,
                "staff_count": staff_count,
                "total_hours_worked": total_hours_worked,
                "total_wages_paid": round(total_wages_paid, 2),
                "raw_material_cost": round(raw_material_cost, 2),
                "consumables_cost": round(consumables_cost, 2),
                "energy_cost": energy_cost,
                "lunch_cost": lunch_cost,
                "total_production_cost": round(total_production_cost, 2),
                "cost_per_unit": round(cost_per_unit, 2),
                "warehouse_id": warehouse_id if warehouse_id else None,
                "notes": notes,
            }
        )
        completion_id = str(result.fetchone().id)

        # Insert consumable line items and deduct stock
        for con in consumables_list:
            con_r = await session.execute(
                text("SELECT unit_cost FROM production_consumables WHERE id = :id"),
                {"id": con['consumable_id']}
            )
            c = con_r.fetchone()
            uc = float(c.unit_cost) if c else 0
            qty = float(con.get('quantity', 0))
            await session.execute(
                text("""
                    INSERT INTO production_completion_consumables (id, completion_id, consumable_id, quantity, unit_cost, total_cost)
                    VALUES (gen_random_uuid(), :cid, :con_id, :qty, :uc, :tc)
                """),
                {"cid": completion_id, "con_id": con['consumable_id'], "qty": qty, "uc": uc, "tc": round(uc * qty, 2)}
            )
            # Auto-deduct consumable stock
            if qty > 0:
                await session.execute(
                    text("""
                        UPDATE production_consumables
                        SET current_stock = GREATEST(current_stock - :qty, 0)
                        WHERE id = :id
                    """),
                    {"id": con['consumable_id'], "qty": qty}
                )

        # Insert raw material line items
        for mat in materials_list:
            rm_r = await session.execute(
                text("SELECT unit_cost FROM raw_materials WHERE id = :id"),
                {"id": mat['raw_material_id']}
            )
            rm = rm_r.fetchone()
            uc = float(rm.unit_cost) if rm else 0
            qty = float(mat.get('quantity', 0))
            await session.execute(
                text("""
                    INSERT INTO production_completion_materials (id, completion_id, raw_material_id, quantity, unit_cost, total_cost)
                    VALUES (gen_random_uuid(), :cid, :rm_id, :qty, :uc, :tc)
                """),
                {"cid": completion_id, "rm_id": mat['raw_material_id'], "qty": qty, "uc": uc, "tc": round(uc * qty, 2)}
            )

        await session.commit()

        return {
            "success": True,
            "message": f"Production completion recorded: {qty_produced} units produced",
            "data": {
                "id": completion_id,
                "qty_produced": qty_produced,
                "qty_damaged": qty_damaged,
                "raw_material_cost": round(raw_material_cost, 2),
                "consumables_cost": round(consumables_cost, 2),
                "energy_cost": energy_cost,
                "lunch_cost": lunch_cost,
                "total_wages_paid": round(total_wages_paid, 2),
                "total_production_cost": round(total_production_cost, 2),
                "cost_per_unit": round(cost_per_unit, 2),
            }
        }
    except KeyError as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Missing required field: {e}")
    except Exception as e:
        await session.rollback()
        import traceback
        print(f"PRODUCTION COMPLETION ERROR: {traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=f"Error: {str(e)}")


@router.get('/')
async def list_production_completions(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session)
):
    """List all production completion records with product name."""
    count_r = await session.execute(text("SELECT COUNT(*) FROM production_completions"))
    total = count_r.scalar_one()
    offset = (page - 1) * size
    result = await session.execute(
        text("""
            SELECT pc.*, p.name AS product_name, p.sku AS product_sku, w.name AS warehouse_name
            FROM production_completions pc
            JOIN products p ON p.id = pc.product_id
            LEFT JOIN warehouses w ON w.id = pc.warehouse_id
            ORDER BY pc.production_date DESC, pc.created_at DESC
            LIMIT :limit OFFSET :offset
        """),
        {"limit": size, "offset": offset}
    )
    items = []
    for r in result.fetchall():
        items.append({
            "id": str(r.id),
            "product_id": str(r.product_id),
            "product_name": r.product_name,
            "product_sku": r.product_sku,
            "production_date": str(r.production_date),
            "qty_produced": int(r.qty_produced),
            "qty_damaged": int(r.qty_damaged or 0),
            "damage_notes": r.damage_notes or '',
            "staff_count": int(r.staff_count or 0),
            "total_hours_worked": float(r.total_hours_worked or 0),
            "total_wages_paid": float(r.total_wages_paid or 0),
            "raw_material_cost": float(r.raw_material_cost or 0),
            "consumables_cost": float(r.consumables_cost or 0),
            "energy_cost": float(r.energy_cost or 0),
            "lunch_cost": float(r.lunch_cost or 0),
            "total_production_cost": float(r.total_production_cost or 0),
            "cost_per_unit": float(r.cost_per_unit or 0),
            "warehouse_name": r.warehouse_name or '',
            "notes": r.notes or '',
            "status": r.status or 'Completed',
        })
    return {"items": items, "total": total, "page": page, "size": size}


@router.get('/{completion_id}')
async def get_production_completion(completion_id: str, session: AsyncSession = Depends(get_session)):
    """Get a single production completion with all details."""
    result = await session.execute(
        text("""
            SELECT pc.*, p.name AS product_name, p.sku AS product_sku, w.name AS warehouse_name
            FROM production_completions pc
            JOIN products p ON p.id = pc.product_id
            LEFT JOIN warehouses w ON w.id = pc.warehouse_id
            WHERE pc.id = :id
        """),
        {"id": completion_id}
    )
    r = result.fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="Production completion not found")

    # Get consumable items
    con_r = await session.execute(
        text("""
            SELECT pcc.*, pc.name AS consumable_name, pc.unit AS consumable_unit
            FROM production_completion_consumables pcc
            JOIN production_consumables pc ON pc.id = pcc.consumable_id
            WHERE pcc.completion_id = :cid
        """),
        {"cid": completion_id}
    )
    consumables = [{"consumable_name": c.consumable_name, "unit": c.consumable_unit,
                     "quantity": float(c.quantity), "unit_cost": float(c.unit_cost),
                     "total_cost": float(c.total_cost)} for c in con_r.fetchall()]

    # Get material items
    mat_r = await session.execute(
        text("""
            SELECT pcm.*, rm.name AS material_name, rm.unit AS material_unit
            FROM production_completion_materials pcm
            JOIN raw_materials rm ON rm.id = pcm.raw_material_id
            WHERE pcm.completion_id = :cid
        """),
        {"cid": completion_id}
    )
    materials = [{"material_name": m.material_name, "unit": m.material_unit,
                   "quantity": float(m.quantity), "unit_cost": float(m.unit_cost),
                   "total_cost": float(m.total_cost)} for m in mat_r.fetchall()]

    return {
        "id": str(r.id),
        "product_name": r.product_name, "product_sku": r.product_sku,
        "production_date": str(r.production_date),
        "qty_produced": int(r.qty_produced), "qty_damaged": int(r.qty_damaged or 0),
        "damage_notes": r.damage_notes or '',
        "staff_count": int(r.staff_count or 0),
        "total_hours_worked": float(r.total_hours_worked or 0),
        "total_wages_paid": float(r.total_wages_paid or 0),
        "raw_material_cost": float(r.raw_material_cost or 0),
        "consumables_cost": float(r.consumables_cost or 0),
        "energy_cost": float(r.energy_cost or 0),
        "lunch_cost": float(r.lunch_cost or 0),
        "total_production_cost": float(r.total_production_cost or 0),
        "cost_per_unit": float(r.cost_per_unit or 0),
        "warehouse_name": r.warehouse_name or '',
        "notes": r.notes or '',
        "consumables": consumables,
        "materials": materials,
    }


@router.delete('/{completion_id}')
async def delete_production_completion(completion_id: str, session: AsyncSession = Depends(get_session)):
    """Delete a production completion record."""
    result = await session.execute(
        text("DELETE FROM production_completions WHERE id = :id RETURNING id"),
        {"id": completion_id}
    )
    if not result.fetchone():
        raise HTTPException(status_code=404, detail="Record not found")
    await session.commit()
    return {"message": "Production completion record deleted"}
