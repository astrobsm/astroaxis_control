"""Maintenance and QC costing.

Both are the same accounting act -- an operational cost incurred against a
department -- so they share app.services.opex. The point of the module is to
get maintenance and quality-control spend OUT of their operational tables and
INTO the ledger against a cost centre, where the P&L and the cost-centre report
can see it. Before this, a maintenance job's cost lived only on the
machine_maintenance row and never reached the books.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.api.auth import require_authenticated_user, require_admin
from app.models import User
from app.services.opex import post_operational_cost

router = APIRouter(prefix='/api/maintenance', tags=['Maintenance & QC'])

# Account defaults. Equipment maintenance is a factory overhead; QC is its own
# expense line. Both are overridable per request for the odd case that belongs
# elsewhere.
ACC_EQUIPMENT_MAINT = "5450"
ACC_FACTORY_MAINT = "5440"
ACC_QC = "6950"


class PostMaintenanceCostIn(BaseModel):
    cost_centre: str = 'MAINT'
    expense_account: str = ACC_EQUIPMENT_MAINT
    paid_from: str = 'bank'
    on: Optional[date] = None


class QcInspectionIn(BaseModel):
    subject: str = Field(..., min_length=1)
    inspection_type: str = 'in_process'
    result: str = 'pending'
    cost: float = Field(0, ge=0)
    cost_centre: str = 'QC'
    inspector: Optional[str] = None
    batch_number: Optional[str] = None
    inspection_date: Optional[date] = None
    notes: Optional[str] = None
    post_cost: bool = False
    paid_from: str = 'bank'


# ---------------------------------------------------------------------------
# Maintenance costing
# ---------------------------------------------------------------------------

@router.post('/machine-maintenance/{maintenance_id}/post-cost')
async def post_maintenance_cost(
    maintenance_id: UUID,
    body: PostMaintenanceCostIn,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Post a machine_maintenance record's cost to the ledger.

    Reads the cost off the maintenance row rather than trusting a body figure,
    so the books cannot disagree with the maintenance log. Idempotent: the
    ledger's own guard means a second call posts nothing.
    """
    row = (await session.execute(
        text("""SELECT id, machine_id, description, cost, completed_date,
                       scheduled_date
                  FROM machine_maintenance WHERE id = :id"""),
        {"id": str(maintenance_id)},
    )).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Maintenance record not found.")
    if (row.cost or 0) <= 0:
        raise HTTPException(
            status_code=400,
            detail="This maintenance record has no cost to post.")

    when = row.completed_date or row.scheduled_date
    try:
        entry_id = await post_operational_cost(
            session,
            expense_account=body.expense_account,
            amount=row.cost,
            reference=f"MAINT-{maintenance_id}",
            description=f"Equipment maintenance: {row.description or maintenance_id}",
            cost_centre=body.cost_centre,
            source_module="maintenance",
            paid_from=body.paid_from,
            on=body.on or (when if isinstance(when, date) else None),
            created_by=current_user.id,
        )
        await session.commit()
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "success": True,
        "posted": entry_id is not None,
        "journal_entry_id": str(entry_id) if entry_id else None,
        "note": (None if entry_id else
                 "Nothing posted -- accounting posting is disabled, before "
                 "the cutover date, or already posted for this record."),
    }


# ---------------------------------------------------------------------------
# QC inspections
# ---------------------------------------------------------------------------

@router.get('/qc-inspections')
async def list_qc(
    result: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    where, params = "", {}
    if result:
        where = "WHERE result = :r"
        params["r"] = result
    rows = (await session.execute(text(f"""
        SELECT id, inspection_number, inspection_date, subject, inspection_type,
               result, cost, cost_centre, inspector, batch_number, gl_entry_id,
               notes
          FROM qc_inspections {where}
         ORDER BY inspection_date DESC, created_at DESC
    """), params)).fetchall()
    return [
        {"id": str(r.id), "inspection_number": r.inspection_number,
         "inspection_date": str(r.inspection_date), "subject": r.subject,
         "inspection_type": r.inspection_type, "result": r.result,
         "cost": float(r.cost), "cost_centre": r.cost_centre,
         "inspector": r.inspector, "batch_number": r.batch_number,
         "posted": r.gl_entry_id is not None, "notes": r.notes}
        for r in rows
    ]


@router.post('/qc-inspections')
async def create_qc(
    body: QcInspectionIn,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Record a QC inspection, optionally posting its cost immediately."""
    insp_id = uuid.uuid4()
    number = f"QC-{date.today().strftime('%Y%m')}-{insp_id.hex[:6].upper()}"
    insp_date = body.inspection_date or date.today()
    try:
        await session.execute(text("""
            INSERT INTO qc_inspections
                (id, inspection_number, inspection_date, subject,
                 inspection_type, result, cost, cost_centre, inspector,
                 batch_number, notes)
            VALUES (:id, :num, :d, :subj, :typ, :res, :cost, :cc, :insp,
                    :batch, :notes)
        """), {"id": str(insp_id), "num": number, "d": insp_date,
               "subj": body.subject, "typ": body.inspection_type,
               "res": body.result, "cost": body.cost, "cc": body.cost_centre,
               "insp": body.inspector, "batch": body.batch_number,
               "notes": body.notes})

        entry_id = None
        if body.post_cost and body.cost > 0:
            entry_id = await post_operational_cost(
                session,
                expense_account=ACC_QC,
                amount=body.cost,
                reference=f"QC-{insp_id}",
                description=f"QC inspection {number}: {body.subject}",
                cost_centre=body.cost_centre,
                source_module="qc",
                paid_from=body.paid_from,
                on=insp_date,
                created_by=current_user.id,
            )
            if entry_id:
                await session.execute(
                    text("UPDATE qc_inspections SET gl_entry_id = :e "
                         "WHERE id = :id"),
                    {"e": str(entry_id), "id": str(insp_id)})

        await session.commit()
        return {"success": True, "id": str(insp_id),
                "inspection_number": number,
                "posted": entry_id is not None,
                "journal_entry_id": str(entry_id) if entry_id else None}
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.post('/qc-inspections/{inspection_id}/post-cost')
async def post_qc_cost(
    inspection_id: UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Post an existing QC inspection's cost to the ledger."""
    row = (await session.execute(
        text("""SELECT id, inspection_number, subject, cost, cost_centre,
                       inspection_date, gl_entry_id
                  FROM qc_inspections WHERE id = :id"""),
        {"id": str(inspection_id)},
    )).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Inspection not found.")
    if row.gl_entry_id is not None:
        raise HTTPException(
            status_code=400, detail="This inspection's cost is already posted.")
    if (row.cost or 0) <= 0:
        raise HTTPException(
            status_code=400, detail="This inspection has no cost to post.")

    try:
        entry_id = await post_operational_cost(
            session,
            expense_account=ACC_QC,
            amount=row.cost,
            reference=f"QC-{inspection_id}",
            description=f"QC inspection {row.inspection_number}: {row.subject}",
            cost_centre=row.cost_centre,
            source_module="qc",
            paid_from="bank",
            on=row.inspection_date if isinstance(row.inspection_date, date)
            else None,
            created_by=current_user.id,
        )
        if entry_id:
            await session.execute(
                text("UPDATE qc_inspections SET gl_entry_id = :e WHERE id = :id"),
                {"e": str(entry_id), "id": str(inspection_id)})
        await session.commit()
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e))

    return {"success": True, "posted": entry_id is not None,
            "journal_entry_id": str(entry_id) if entry_id else None,
            "note": (None if entry_id else
                     "Nothing posted -- posting is disabled or before cutover.")}


@router.get('/cost-summary')
async def cost_summary(
    start: date = Query(...),
    end: date = Query(...),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    """Maintenance and QC spend that reached the ledger, by cost centre.

    Reads the journal, so it reports what was actually posted -- not what the
    operational tables intended to spend.
    """
    if end < start:
        raise HTTPException(status_code=400, detail="end cannot precede start.")
    rows = (await session.execute(text("""
        SELECT e.source_module,
               COALESCE(NULLIF(TRIM(l.cost_centre), ''), 'UNALLOCATED') AS centre,
               COALESCE(SUM(l.debit - l.credit), 0) AS amount
          FROM gl_journal_lines l
          JOIN gl_journal_entries e ON e.id = l.entry_id
          JOIN gl_accounts a ON a.id = l.account_id
         WHERE e.status <> 'DRAFT'
           AND e.source_module IN ('maintenance', 'qc')
           AND a.account_type = 'EXPENSE'
           AND e.entry_date BETWEEN :s AND :e
         GROUP BY e.source_module, centre
         ORDER BY e.source_module, centre
    """), {"s": start, "e": end})).fetchall()
    return {
        "period": {"start": str(start), "end": str(end)},
        "lines": [
            {"category": r.source_module, "cost_centre": r.centre,
             "amount": float(r.amount)} for r in rows
        ],
    }
