from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Optional
from datetime import date

from app.db import get_session

router = APIRouter(prefix='/api/machines', tags=['Machines & Equipment'])


# ========== EQUIPMENT CRUD ==========

@router.post('/')
async def create_machine(data: dict, session: AsyncSession = Depends(get_session)):
    """Register a new machine or equipment."""
    try:
        # Safely parse optional fields - empty strings become None/0
        pd = data.get('purchase_date', '') or None
        if pd:
            pd = date.fromisoformat(str(pd))
        pc = data.get('purchase_cost', '') or 0
        cv = data.get('current_value', '') or data.get('purchase_cost', '') or 0
        dr = data.get('depreciation_rate', '') or 0

        result = await session.execute(
            text("""
                INSERT INTO machines_equipment (
                    id, name, equipment_type, serial_number, model, manufacturer,
                    purchase_date, purchase_cost, current_value, depreciation_rate,
                    depreciation_method, location, status, notes, created_at
                ) VALUES (
                    gen_random_uuid(), :name, :equipment_type, :serial_number, :model, :manufacturer,
                    :purchase_date, :purchase_cost, :current_value, :depreciation_rate,
                    :depreciation_method, :location, :status, :notes, NOW()
                )
                RETURNING *
            """),
            {
                "name": data['name'],
                "equipment_type": data.get('equipment_type', ''),
                "serial_number": data.get('serial_number', ''),
                "model": data.get('model', ''),
                "manufacturer": data.get('manufacturer', ''),
                "purchase_date": pd,
                "purchase_cost": float(pc),
                "current_value": float(cv),
                "depreciation_rate": float(dr),
                "depreciation_method": data.get('depreciation_method', 'Straight-Line'),
                "location": data.get('location', ''),
                "status": data.get('status', 'Operational'),
                "notes": data.get('notes', ''),
            }
        )
        row = result.fetchone()
        await session.commit()
        return {"success": True, "message": f"Machine '{data['name']}' registered", "data": _machine_to_dict(row)}
    except KeyError as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Missing required field: {e}")
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.get('/')
async def list_machines(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_session)
):
    """List all machines/equipment."""
    where = ""
    params = {"limit": size, "offset": (page - 1) * size}
    if status:
        where = "WHERE status = :status"
        params["status"] = status

    count_r = await session.execute(text(f"SELECT COUNT(*) FROM machines_equipment {where}"), params)
    total = count_r.scalar_one()

    result = await session.execute(
        text(f"""
            SELECT * FROM machines_equipment {where}
            ORDER BY name ASC
            LIMIT :limit OFFSET :offset
        """), params
    )
    return {"items": [_machine_to_dict(r) for r in result.fetchall()], "total": total, "page": page, "size": size}


@router.get('/{machine_id}')
async def get_machine(machine_id: str, session: AsyncSession = Depends(get_session)):
    """Get machine details with maintenance records and faults."""
    r = await session.execute(text("SELECT * FROM machines_equipment WHERE id = :id"), {"id": machine_id})
    row = r.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Machine not found")

    # Get maintenance records
    maint_r = await session.execute(
        text("SELECT * FROM machine_maintenance WHERE machine_id = :id ORDER BY scheduled_date DESC"),
        {"id": machine_id}
    )
    maintenance = [_maint_to_dict(m) for m in maint_r.fetchall()]

    # Get fault records
    fault_r = await session.execute(
        text("SELECT * FROM machine_faults WHERE machine_id = :id ORDER BY fault_date DESC"),
        {"id": machine_id}
    )
    faults = [_fault_to_dict(f) for f in fault_r.fetchall()]

    data = _machine_to_dict(row)
    data['maintenance_records'] = maintenance
    data['faults'] = faults
    return data


@router.put('/{machine_id}')
async def update_machine(machine_id: str, data: dict, session: AsyncSession = Depends(get_session)):
    """Update machine/equipment details."""
    fields = []
    params = {"id": machine_id}
    updatable = ['name', 'equipment_type', 'serial_number', 'model', 'manufacturer',
                 'purchase_date', 'purchase_cost', 'current_value', 'depreciation_rate',
                 'depreciation_method', 'location', 'status', 'notes']
    date_fields = {'purchase_date'}
    numeric_fields = {'purchase_cost', 'current_value', 'depreciation_rate'}
    for f in updatable:
        if f in data:
            val = data[f]
            if f in date_fields:
                val = val or None
                if val:
                    val = date.fromisoformat(str(val))
            elif f in numeric_fields:
                val = float(val) if val not in ('', None) else 0
            fields.append(f"{f} = :{f}")
            params[f] = val
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    result = await session.execute(
        text(f"UPDATE machines_equipment SET {', '.join(fields)} WHERE id = :id RETURNING *"),
        params
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Machine not found")
    await session.commit()
    return {"success": True, "data": _machine_to_dict(row)}


@router.delete('/{machine_id}')
async def delete_machine(machine_id: str, session: AsyncSession = Depends(get_session)):
    """Delete a machine record."""
    result = await session.execute(
        text("DELETE FROM machines_equipment WHERE id = :id RETURNING id"),
        {"id": machine_id}
    )
    if not result.fetchone():
        raise HTTPException(status_code=404, detail="Machine not found")
    await session.commit()
    return {"message": "Machine deleted"}


# ========== MAINTENANCE CRUD ==========

@router.post('/{machine_id}/maintenance')
async def create_maintenance(machine_id: str, data: dict, session: AsyncSession = Depends(get_session)):
    """Record a maintenance entry for a machine."""
    try:
        result = await session.execute(
            text("""
                INSERT INTO machine_maintenance (
                    id, machine_id, maintenance_type, scheduled_date, completed_date,
                    description, cost, performed_by, next_scheduled, status, notes, created_at
                ) VALUES (
                    gen_random_uuid(), :machine_id, :mtype, :sched, :completed,
                    :descr, :cost, :performed_by, :next_sched, :status, :notes, NOW()
                )
                RETURNING *
            """),
            {
                "machine_id": machine_id,
                "mtype": data.get('maintenance_type', 'Routine'),
                "sched": data.get('scheduled_date'),
                "completed": data.get('completed_date'),
                "descr": data.get('description', ''),
                "cost": float(data.get('cost', 0)),
                "performed_by": data.get('performed_by', ''),
                "next_sched": data.get('next_scheduled'),
                "status": data.get('status', 'Scheduled'),
                "notes": data.get('notes', ''),
            }
        )
        row = result.fetchone()
        await session.commit()
        return {"success": True, "data": _maint_to_dict(row)}
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.get('/{machine_id}/maintenance')
async def list_maintenance(machine_id: str, session: AsyncSession = Depends(get_session)):
    """List all maintenance records for a machine."""
    result = await session.execute(
        text("SELECT * FROM machine_maintenance WHERE machine_id = :id ORDER BY scheduled_date DESC"),
        {"id": machine_id}
    )
    return [_maint_to_dict(r) for r in result.fetchall()]


@router.put('/maintenance/{record_id}')
async def update_maintenance(record_id: str, data: dict, session: AsyncSession = Depends(get_session)):
    """Update a maintenance record."""
    fields = []
    params = {"id": record_id}
    updatable = ['maintenance_type', 'scheduled_date', 'completed_date', 'description',
                 'cost', 'performed_by', 'next_scheduled', 'status', 'notes']
    for f in updatable:
        if f in data:
            fields.append(f"{f} = :{f}")
            params[f] = data[f]
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    result = await session.execute(
        text(f"UPDATE machine_maintenance SET {', '.join(fields)} WHERE id = :id RETURNING *"),
        params
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Record not found")
    await session.commit()
    return {"success": True, "data": _maint_to_dict(row)}


@router.delete('/maintenance/{record_id}')
async def delete_maintenance(record_id: str, session: AsyncSession = Depends(get_session)):
    """Delete a maintenance record."""
    result = await session.execute(
        text("DELETE FROM machine_maintenance WHERE id = :id RETURNING id"),
        {"id": record_id}
    )
    if not result.fetchone():
        raise HTTPException(status_code=404, detail="Record not found")
    await session.commit()
    return {"message": "Maintenance record deleted"}


# ========== FAULT CRUD ==========

@router.post('/{machine_id}/faults')
async def create_fault(machine_id: str, data: dict, session: AsyncSession = Depends(get_session)):
    """Record a fault for a machine."""
    try:
        result = await session.execute(
            text("""
                INSERT INTO machine_faults (
                    id, machine_id, fault_date, description, severity, status,
                    resolution, resolved_date, downtime_hours, repair_cost, reported_by, created_at
                ) VALUES (
                    gen_random_uuid(), :machine_id, :fault_date, :descr, :severity, :status,
                    :resolution, :resolved_date, :downtime, :repair_cost, :reported_by, NOW()
                )
                RETURNING *
            """),
            {
                "machine_id": machine_id,
                "fault_date": data.get('fault_date', str(date.today())),
                "descr": data.get('description', ''),
                "severity": data.get('severity', 'Medium'),
                "status": data.get('status', 'Open'),
                "resolution": data.get('resolution', ''),
                "resolved_date": data.get('resolved_date'),
                "downtime": float(data.get('downtime_hours', 0)),
                "repair_cost": float(data.get('repair_cost', 0)),
                "reported_by": data.get('reported_by', ''),
            }
        )
        row = result.fetchone()
        await session.commit()
        return {"success": True, "data": _fault_to_dict(row)}
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.get('/{machine_id}/faults')
async def list_faults(machine_id: str, session: AsyncSession = Depends(get_session)):
    """List all faults for a machine."""
    result = await session.execute(
        text("SELECT * FROM machine_faults WHERE machine_id = :id ORDER BY fault_date DESC"),
        {"id": machine_id}
    )
    return [_fault_to_dict(r) for r in result.fetchall()]


@router.put('/faults/{fault_id}')
async def update_fault(fault_id: str, data: dict, session: AsyncSession = Depends(get_session)):
    """Update a fault record."""
    fields = []
    params = {"id": fault_id}
    updatable = ['description', 'severity', 'status', 'resolution',
                 'resolved_date', 'downtime_hours', 'repair_cost', 'reported_by']
    for f in updatable:
        if f in data:
            fields.append(f"{f} = :{f}")
            params[f] = data[f]
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    result = await session.execute(
        text(f"UPDATE machine_faults SET {', '.join(fields)} WHERE id = :id RETURNING *"),
        params
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Record not found")
    await session.commit()
    return {"success": True, "data": _fault_to_dict(row)}


@router.delete('/faults/{fault_id}')
async def delete_fault(fault_id: str, session: AsyncSession = Depends(get_session)):
    """Delete a fault record."""
    result = await session.execute(
        text("DELETE FROM machine_faults WHERE id = :id RETURNING id"),
        {"id": fault_id}
    )
    if not result.fetchone():
        raise HTTPException(status_code=404, detail="Record not found")
    await session.commit()
    return {"message": "Fault record deleted"}


# ========== DEPRECIATION CALCULATOR ==========

@router.get('/{machine_id}/depreciation')
async def calculate_depreciation(machine_id: str, session: AsyncSession = Depends(get_session)):
    """Calculate current depreciation for a machine."""
    r = await session.execute(text("SELECT * FROM machines_equipment WHERE id = :id"), {"id": machine_id})
    row = r.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Machine not found")

    purchase_cost = float(row.purchase_cost or 0)
    depreciation_rate = float(row.depreciation_rate or 0)
    purchase_date = row.purchase_date

    if not purchase_date:
        return {"error": "No purchase date set", "current_value": purchase_cost}

    years_owned = (date.today() - purchase_date).days / 365.25
    method = row.depreciation_method or 'Straight-Line'

    if method == 'Straight-Line':
        annual_dep = purchase_cost * (depreciation_rate / 100)
        total_dep = annual_dep * years_owned
        current_value = max(0, purchase_cost - total_dep)
    else:  # Declining Balance
        current_value = purchase_cost * ((1 - depreciation_rate / 100) ** years_owned)
        total_dep = purchase_cost - current_value

    # Update current_value in DB
    await session.execute(
        text("UPDATE machines_equipment SET current_value = :val WHERE id = :id"),
        {"val": round(current_value, 2), "id": machine_id}
    )
    await session.commit()

    return {
        "machine_name": row.name,
        "purchase_cost": purchase_cost,
        "depreciation_method": method,
        "depreciation_rate": depreciation_rate,
        "years_owned": round(years_owned, 2),
        "total_depreciation": round(total_dep, 2),
        "current_value": round(current_value, 2),
    }


# ========== DASHBOARD SUMMARY ==========

@router.get('/dashboard/summary')
async def machines_dashboard(session: AsyncSession = Depends(get_session)):
    """Get summary stats for machines dashboard."""
    stats = await session.execute(text("""
        SELECT 
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE status = 'Operational') AS operational,
            COUNT(*) FILTER (WHERE status = 'Under Maintenance') AS under_maintenance,
            COUNT(*) FILTER (WHERE status = 'Out of Service') AS out_of_service,
            COALESCE(SUM(purchase_cost), 0) AS total_purchase_value,
            COALESCE(SUM(current_value), 0) AS total_current_value
        FROM machines_equipment
    """))
    s = stats.fetchone()

    open_faults = await session.execute(text(
        "SELECT COUNT(*) FROM machine_faults WHERE status = 'Open'"
    ))
    pending_maint = await session.execute(text(
        "SELECT COUNT(*) FROM machine_maintenance WHERE status = 'Scheduled'"
    ))

    return {
        "total_machines": int(s.total),
        "operational": int(s.operational),
        "under_maintenance": int(s.under_maintenance),
        "out_of_service": int(s.out_of_service),
        "total_purchase_value": float(s.total_purchase_value),
        "total_current_value": float(s.total_current_value),
        "open_faults": open_faults.scalar_one(),
        "pending_maintenance": pending_maint.scalar_one(),
    }


# ========== HELPERS ==========

def _machine_to_dict(r):
    return {
        "id": str(r.id), "name": r.name,
        "equipment_type": r.equipment_type or '',
        "serial_number": r.serial_number or '',
        "model": r.model or '',
        "manufacturer": r.manufacturer or '',
        "purchase_date": str(r.purchase_date) if r.purchase_date else '',
        "purchase_cost": float(r.purchase_cost or 0),
        "current_value": float(r.current_value or 0),
        "depreciation_rate": float(r.depreciation_rate or 0),
        "depreciation_method": r.depreciation_method or 'Straight-Line',
        "location": r.location or '',
        "status": r.status or 'Operational',
        "notes": r.notes or '',
    }


def _maint_to_dict(r):
    return {
        "id": str(r.id), "machine_id": str(r.machine_id),
        "maintenance_type": r.maintenance_type or 'Routine',
        "scheduled_date": str(r.scheduled_date) if r.scheduled_date else '',
        "completed_date": str(r.completed_date) if r.completed_date else '',
        "description": r.description or '',
        "cost": float(r.cost or 0),
        "performed_by": r.performed_by or '',
        "next_scheduled": str(r.next_scheduled) if r.next_scheduled else '',
        "status": r.status or 'Scheduled',
        "notes": r.notes or '',
    }


def _fault_to_dict(r):
    return {
        "id": str(r.id), "machine_id": str(r.machine_id),
        "fault_date": str(r.fault_date) if r.fault_date else '',
        "description": r.description or '',
        "severity": r.severity or 'Medium',
        "status": r.status or 'Open',
        "resolution": r.resolution or '',
        "resolved_date": str(r.resolved_date) if r.resolved_date else '',
        "downtime_hours": float(r.downtime_hours or 0),
        "repair_cost": float(r.repair_cost or 0),
        "reported_by": r.reported_by or '',
    }
