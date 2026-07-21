"""Payroll API: rate configuration, runs, payslips.

Rate configuration is admin-only and confirming a configuration is a recorded
act: the confirmer's name is stored against it, because that person is
asserting the tax rates are correct under current law.
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.api.auth import require_authenticated_user, require_admin
from app.models import User
from app.services.payroll import load_rate_config
from app.services.payroll_run import create_payroll_run, approve_payroll_run

router = APIRouter(prefix='/api/payroll', tags=['Payroll'])


class RunIn(BaseModel):
    period_start: date
    period_end: date
    staff_ids: Optional[List[UUID]] = None
    bonuses: Optional[dict] = None


class ApproveIn(BaseModel):
    approved_by: str = Field(..., min_length=1)


class ConfirmIn(BaseModel):
    confirmed_by: str = Field(..., min_length=1)
    source_reference: Optional[str] = None
    notes: Optional[str] = None


class BandIn(BaseModel):
    sequence: int
    lower_bound: float
    upper_bound: Optional[float] = None
    rate_percent: float


class DeductionIn(BaseModel):
    staff_id: UUID
    code: str
    label: str
    total_amount: float
    amount_per_period: float
    start_date: Optional[date] = None


# ---------------------------------------------------------------------------
# Rate configuration
# ---------------------------------------------------------------------------

@router.get('/rate-configs')
async def list_rate_configs(
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    configs = (await session.execute(text("""
        SELECT id, name, effective_from, effective_to, is_confirmed,
               confirmed_by, confirmed_at, source_reference, notes
          FROM payroll_rate_configs ORDER BY effective_from DESC
    """))).fetchall()
    out = []
    for c in configs:
        bands = (await session.execute(text("""
            SELECT sequence, lower_bound, upper_bound, rate_percent
              FROM payroll_tax_bands WHERE config_id = :c ORDER BY sequence
        """), {"c": str(c.id)})).fetchall()
        items = (await session.execute(text("""
            SELECT code, value, basis, description
              FROM payroll_rate_items WHERE config_id = :c ORDER BY code
        """), {"c": str(c.id)})).fetchall()
        out.append({
            "id": str(c.id), "name": c.name,
            "effective_from": str(c.effective_from),
            "effective_to": str(c.effective_to) if c.effective_to else None,
            "is_confirmed": c.is_confirmed,
            "confirmed_by": c.confirmed_by,
            "confirmed_at": c.confirmed_at.isoformat() if c.confirmed_at else None,
            "source_reference": c.source_reference,
            "notes": c.notes,
            "warning": (None if c.is_confirmed else
                        "UNCONFIRMED — these rates have not been verified "
                        "against current tax law. Payroll cannot be run until "
                        "an accountant reviews and confirms them."),
            "tax_bands": [
                {"sequence": b.sequence, "lower_bound": float(b.lower_bound),
                 "upper_bound": float(b.upper_bound) if b.upper_bound else None,
                 "rate_percent": float(b.rate_percent)} for b in bands],
            "rates": [
                {"code": i.code, "value": float(i.value), "basis": i.basis,
                 "description": i.description} for i in items],
        })
    return out


@router.put('/rate-configs/{config_id}/bands')
async def replace_tax_bands(
    config_id: UUID,
    bands: List[BandIn],
    session: AsyncSession = Depends(get_session),
    _admin: User = Depends(require_admin),
):
    """Replace the progressive tax bands on a configuration.

    Editing bands un-confirms the configuration: whoever confirmed the old
    figures did not review these, and payroll must not run on rates nobody
    has checked.
    """
    if not bands:
        raise HTTPException(
            status_code=400, detail="At least one tax band is required.")

    ordered = sorted(bands, key=lambda b: b.sequence)
    # Bands must tile the income range without gaps or overlaps, or some
    # income is either taxed twice or not at all.
    for prev, nxt in zip(ordered, ordered[1:]):
        if prev.upper_bound is None:
            raise HTTPException(
                status_code=400,
                detail=f"Band {prev.sequence} is open-ended but is not the "
                       f"last band.")
        if float(prev.upper_bound) != float(nxt.lower_bound):
            raise HTTPException(
                status_code=400,
                detail=(f"Gap or overlap between band {prev.sequence} "
                        f"(ends {prev.upper_bound}) and band {nxt.sequence} "
                        f"(starts {nxt.lower_bound})."))
    if ordered[-1].upper_bound is not None:
        raise HTTPException(
            status_code=400,
            detail="The highest band must be open-ended (no upper bound), "
                   "otherwise income above it is untaxed.")

    try:
        await session.execute(
            text("DELETE FROM payroll_tax_bands WHERE config_id = :c"),
            {"c": str(config_id)})
        for b in ordered:
            await session.execute(text("""
                INSERT INTO payroll_tax_bands
                    (config_id, sequence, lower_bound, upper_bound, rate_percent)
                VALUES (:c, :s, :lo, :hi, :r)
            """), {"c": str(config_id), "s": b.sequence, "lo": b.lower_bound,
                   "hi": b.upper_bound, "r": b.rate_percent})
        await session.execute(text("""
            UPDATE payroll_rate_configs
               SET is_confirmed = FALSE, confirmed_by = NULL,
                   confirmed_at = NULL
             WHERE id = :c
        """), {"c": str(config_id)})
        await session.commit()
        return {"success": True,
                "message": f"{len(ordered)} bands saved. The configuration is "
                           f"now UNCONFIRMED and must be re-confirmed before "
                           f"payroll can run."}
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.put('/rate-configs/{config_id}/rates/{code}')
async def set_rate_item(
    config_id: UUID, code: str, value: float = Query(...),
    basis: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_session),
    _admin: User = Depends(require_admin),
):
    """Set one contribution rate or relief parameter."""
    result = await session.execute(text("""
        UPDATE payroll_rate_items
           SET value = :v, basis = COALESCE(:b, basis)
         WHERE config_id = :c AND code = :code
     RETURNING code
    """), {"v": value, "b": basis, "c": str(config_id), "code": code})
    if result.first() is None:
        await session.rollback()
        raise HTTPException(
            status_code=404, detail=f"Rate {code!r} not found on this config.")
    await session.execute(text("""
        UPDATE payroll_rate_configs
           SET is_confirmed = FALSE, confirmed_by = NULL, confirmed_at = NULL
         WHERE id = :c
    """), {"c": str(config_id)})
    await session.commit()
    return {"success": True,
            "message": f"{code} updated. Configuration is now UNCONFIRMED."}


@router.post('/rate-configs/{config_id}/confirm')
async def confirm_rate_config(
    config_id: UUID,
    body: ConfirmIn,
    session: AsyncSession = Depends(get_session),
    _admin: User = Depends(require_admin),
):
    """Confirm that a rate configuration reflects current law.

    This is an assertion by a named person, recorded permanently. Payroll
    cannot run until it is made, and any subsequent edit revokes it.
    """
    result = await session.execute(text("""
        UPDATE payroll_rate_configs
           SET is_confirmed = TRUE, confirmed_by = :by, confirmed_at = NOW(),
               source_reference = COALESCE(:src, source_reference),
               notes = COALESCE(:notes, notes)
         WHERE id = :c
     RETURNING name
    """), {"by": body.confirmed_by, "src": body.source_reference,
           "notes": body.notes, "c": str(config_id)})
    row = result.first()
    if row is None:
        await session.rollback()
        raise HTTPException(status_code=404, detail="Configuration not found.")
    await session.commit()
    return {"success": True,
            "message": f"{row.name} confirmed by {body.confirmed_by}. "
                       f"Payroll can now be run against these rates."}


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

@router.post('/runs')
async def create_run(
    body: RunIn,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Calculate a payroll run. Creates a DRAFT; pays nobody, posts nothing."""
    try:
        result = await create_payroll_run(
            session,
            period_start=body.period_start,
            period_end=body.period_end,
            staff_ids=body.staff_ids,
            bonuses=body.bonuses,
            created_by=current_user.id,
        )
        await session.commit()
        return {
            "success": True,
            "run_id": str(result["run_id"]),
            "run_number": result["run_number"],
            "rate_config": result["rate_config"],
            "staff_paid": result["staff_paid"],
            "staff_skipped": result["staff_skipped"],
            "gross_total": float(result["gross_total"]),
            "deductions_total": float(result["deductions_total"]),
            "net_total": float(result["net_total"]),
            "employer_cost_total": float(result["employer_cost_total"]),
            "status": "DRAFT",
            "next_step": "Review the payslips, then approve to post to the "
                         "ledger.",
        }
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.post('/runs/{run_id}/approve')
async def approve_run(
    run_id: UUID,
    body: ApproveIn,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Approve a run and post it to the general ledger."""
    try:
        result = await approve_payroll_run(
            session, run_id=run_id, approved_by=body.approved_by,
            created_by=current_user.id)
        await session.commit()
        return {"success": True, **{
            k: (str(v) if k == "journal_entry_id" and v else v)
            for k, v in result.items()}}
    except HTTPException:
        await session.rollback()
        raise


@router.get('/runs')
async def list_runs(
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    rows = (await session.execute(text("""
        SELECT r.id, r.run_number, r.period_start, r.period_end, r.status,
               r.gross_total, r.deductions_total, r.net_total,
               r.employer_cost_total, r.approved_by, r.approved_at,
               COUNT(p.id) AS payslip_count
          FROM payroll_runs r
          LEFT JOIN payslips p ON p.run_id = r.id
         GROUP BY r.id
         ORDER BY r.period_start DESC
    """))).fetchall()
    return [
        {"id": str(r.id), "run_number": r.run_number,
         "period_start": str(r.period_start), "period_end": str(r.period_end),
         "status": r.status, "gross_total": float(r.gross_total or 0),
         "deductions_total": float(r.deductions_total or 0),
         "net_total": float(r.net_total or 0),
         "employer_cost_total": float(r.employer_cost_total or 0),
         "approved_by": r.approved_by,
         "approved_at": r.approved_at.isoformat() if r.approved_at else None,
         "payslip_count": r.payslip_count}
        for r in rows
    ]


@router.get('/runs/{run_id}/payslips')
async def list_payslips(
    run_id: UUID,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    """Payslips for a run, each with its full component breakdown."""
    slips = (await session.execute(text("""
        SELECT p.id, p.payslip_number, p.staff_id, p.basic_salary,
               p.gross_pay, p.taxable_income, p.total_deductions, p.net_pay,
               p.employer_contributions, p.regular_hours, p.overtime_hours,
               s.employee_id, s.first_name, s.last_name
          FROM payslips p JOIN staff s ON s.id = p.staff_id
         WHERE p.run_id = :r ORDER BY s.employee_id
    """), {"r": str(run_id)})).fetchall()

    out = []
    for sl in slips:
        comps = (await session.execute(text("""
            SELECT component_type, code, label, amount, basis_amount,
                   rate_applied
              FROM payslip_components WHERE payslip_id = :p ORDER BY sequence
        """), {"p": str(sl.id)})).fetchall()
        out.append({
            "payslip_number": sl.payslip_number,
            "staff": {"employee_id": sl.employee_id,
                      "name": f"{sl.first_name} {sl.last_name}"},
            "basic_salary": float(sl.basic_salary or 0),
            "gross_pay": float(sl.gross_pay),
            "taxable_income": float(sl.taxable_income or 0),
            "total_deductions": float(sl.total_deductions or 0),
            "net_pay": float(sl.net_pay),
            "employer_contributions": float(sl.employer_contributions or 0),
            "regular_hours": float(sl.regular_hours or 0),
            "overtime_hours": float(sl.overtime_hours or 0),
            "components": [
                {"type": c.component_type, "code": c.code, "label": c.label,
                 "amount": float(c.amount),
                 "basis_amount": float(c.basis_amount) if c.basis_amount else None,
                 "rate_applied": float(c.rate_applied) if c.rate_applied else None}
                for c in comps],
        })
    return out


@router.get('/statutory-liabilities')
async def statutory_liabilities(
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    """What has been withheld and is owed to statutory bodies.

    These are amounts the company holds on behalf of others. They are a
    liability until remitted, and remitting late attracts penalties.
    """
    rows = (await session.execute(text("""
        SELECT pc.code, SUM(pc.amount) AS total
          FROM payslip_components pc
          JOIN payslips p ON p.id = pc.payslip_id
          JOIN payroll_runs r ON r.id = p.run_id
         WHERE r.status IN ('APPROVED','PAID')
           AND pc.code IN ('PAYE','PENSION','NHF','NHIA','PENSION_EMPLOYER',
                           'NHIA_EMPLOYER')
         GROUP BY pc.code
    """))).fetchall()
    totals = {r.code: float(r.total) for r in rows}
    return {
        "paye_payable": totals.get("PAYE", 0.0),
        "pension_payable": (totals.get("PENSION", 0.0)
                            + totals.get("PENSION_EMPLOYER", 0.0)),
        "nhf_payable": totals.get("NHF", 0.0),
        "nhia_payable": (totals.get("NHIA", 0.0)
                         + totals.get("NHIA_EMPLOYER", 0.0)),
        "total": sum(totals.values()),
        "note": "Amounts withheld from staff and employer contributions due. "
                "These remain liabilities until remitted.",
    }


# ---------------------------------------------------------------------------
# Staff deductions (loans, advances)
# ---------------------------------------------------------------------------

@router.post('/deductions')
async def create_deduction(
    body: DeductionIn,
    session: AsyncSession = Depends(get_session),
    _admin: User = Depends(require_admin),
):
    """Register a loan or salary advance to be recovered over future runs."""
    if body.amount_per_period > body.total_amount:
        raise HTTPException(
            status_code=400,
            detail="Per-period recovery cannot exceed the total advanced.")
    try:
        await session.execute(text("""
            INSERT INTO staff_deductions
                (staff_id, code, label, total_amount, amount_per_period,
                 start_date)
            VALUES (:s, :c, :l, :t, :p, COALESCE(:d, CURRENT_DATE))
        """), {"s": str(body.staff_id), "c": body.code, "l": body.label,
               "t": body.total_amount, "p": body.amount_per_period,
               "d": body.start_date})
        await session.commit()
        return {"success": True}
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e))


@router.get('/deductions/{staff_id}')
async def list_deductions(
    staff_id: UUID,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    rows = (await session.execute(text("""
        SELECT code, label, total_amount, amount_per_period,
               amount_recovered, total_amount - amount_recovered AS remaining,
               is_active, start_date
          FROM staff_deductions WHERE staff_id = :s ORDER BY created_at DESC
    """), {"s": str(staff_id)})).fetchall()
    return [
        {"code": r.code, "label": r.label,
         "total_amount": float(r.total_amount),
         "amount_per_period": float(r.amount_per_period),
         "amount_recovered": float(r.amount_recovered),
         "remaining": float(r.remaining), "is_active": r.is_active,
         "start_date": str(r.start_date)}
        for r in rows
    ]
