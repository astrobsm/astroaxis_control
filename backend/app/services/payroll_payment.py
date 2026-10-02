"""Paying a payroll run, and knowing who is eligible to be in one.

`payroll_run.py` works out what each person is owed and books the debt.
This module records the money actually leaving, which is a separate event
with its own date, its own reference and its own journal entry:

    approve:  Dr Salaries & Wages   Cr Staff Salary Payable   (we owe you)
    pay:      Dr Staff Salary Payable   Cr Bank / Cash        (we have paid you)

Keeping them apart is what makes account 2200 mean something. A run that was
approved and never paid shows as a liability, which is exactly what it is.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ledger import Line, money, post_entry

# Where the money came from, by how it was paid. Cash leaves the cash account
# and a transfer leaves the bank; booking both against the bank would make
# the bank reconciliation disagree with the ledger by exactly the cash.
METHOD_ACCOUNTS = {
    "BANK_TRANSFER": "1200",
    "CHEQUE": "1200",
    "MOBILE_MONEY": "1200",
    "CASH": "1100",
    "OTHER": "1200",
}

MINIMUM_HOURS = Decimal("0.01")


async def eligibility(
    session: AsyncSession, *, period_start: date, period_end: date,
) -> dict:
    """Who worked during this period, and who did not.

    The payroll screen asks for only the people who worked the selected
    duration. This returns BOTH groups rather than silently dropping the
    second one: a member of staff missing from a payroll screen with no
    explanation is how somebody goes unpaid for a month.

    'Worked' means a completed attendance record inside the period. That is
    the only evidence this system holds that somebody was there.
    """
    if period_end < period_start:
        raise HTTPException(
            status_code=400, detail="period_end cannot precede period_start.")

    rows = (await session.execute(
        text("""
            SELECT s.id, s.employee_id, s.first_name, s.last_name,
                   s.position, s.payment_mode, s.hourly_rate,
                   s.monthly_salary, s.bank_name, s.bank_account_number,
                   s.bank_account_name, s.display_hidden, s.hidden_reason,
                   COALESCE(a.hours, 0)      AS hours_worked,
                   COALESCE(a.days, 0)       AS days_worked,
                   a.first_seen, a.last_seen
              FROM staff s
              LEFT JOIN (
                  SELECT staff_id,
                         SUM(hours_worked)        AS hours,
                         COUNT(*)                 AS days,
                         MIN(DATE(clock_in))      AS first_seen,
                         MAX(DATE(clock_in))      AS last_seen
                    FROM attendance
                   WHERE DATE(clock_in) BETWEEN :ps AND :pe
                     AND status = 'completed'
                   GROUP BY staff_id
              ) a ON a.staff_id = s.id
             WHERE s.is_active = TRUE
             ORDER BY s.employee_id
        """),
        {"ps": period_start, "pe": period_end},
    )).fetchall()

    # What has already been calculated for this period, so the screen can show
    # a person's state rather than offering to process somebody twice.
    slips = {
        str(r.staff_id): r for r in (await session.execute(
            text("""
                SELECT p.staff_id, p.id AS payslip_id, p.payslip_number,
                       p.gross_pay, p.total_deductions, p.net_pay,
                       p.paid_at, r.id AS run_id, r.run_number, r.status
                  FROM payslips p
                  JOIN payroll_runs r ON r.id = p.run_id
                 WHERE r.period_start = :ps AND r.period_end = :pe
                   AND r.status <> 'CANCELLED'
            """),
            {"ps": period_start, "pe": period_end},
        )).fetchall()
    }

    worked, not_worked = [], []
    for r in rows:
        slip = slips.get(str(r.id))
        entry = {
            "staff_id": str(r.id),
            "employee_id": r.employee_id,
            "first_name": r.first_name,
            "last_name": r.last_name,
            "position": r.position or "",
            "payment_mode": (r.payment_mode or "monthly").lower(),
            "hourly_rate": float(r.hourly_rate or 0),
            "monthly_salary": float(r.monthly_salary or 0),
            "hours_worked": float(r.hours_worked or 0),
            "days_worked": int(r.days_worked or 0),
            "first_seen": str(r.first_seen) if r.first_seen else None,
            "last_seen": str(r.last_seen) if r.last_seen else None,
            "bank_name": r.bank_name or "",
            "bank_account_number": r.bank_account_number or "",
            "bank_account_name": r.bank_account_name or "",
            # Carried so the payroll screen can MARK a hidden person rather
            # than omit them. Hiding is a display decision; being owed wages
            # is not.
            "display_hidden": bool(r.display_hidden),
            "hidden_reason": r.hidden_reason,
            "payslip_id": str(slip.payslip_id) if slip else None,
            "payslip_number": slip.payslip_number if slip else None,
            "run_id": str(slip.run_id) if slip else None,
            "run_number": slip.run_number if slip else None,
            "run_status": slip.status if slip else None,
            "gross_pay": float(slip.gross_pay) if slip else 0.0,
            "total_deductions": float(slip.total_deductions) if slip else 0.0,
            "net_pay": float(slip.net_pay) if slip else 0.0,
            "paid_at": slip.paid_at.isoformat() if slip and slip.paid_at else None,
            "state": (
                "PAID" if slip and slip.paid_at
                else slip.status if slip
                else "NOT_PROCESSED"),
        }
        (worked if Decimal(str(r.hours_worked or 0)) >= MINIMUM_HOURS
         else not_worked).append(entry)

    return {
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "worked": worked,
        "not_worked": not_worked,
        "worked_count": len(worked),
        "not_worked_count": len(not_worked),
        "note": (
            "'Worked' means at least one completed attendance record inside "
            "the selected dates. Staff with no attendance are listed "
            "separately rather than hidden, because a salaried employee with "
            "an unrecorded month is a timekeeping problem, not a reason to "
            "leave them off the payroll."),
    }


async def pay_payslips(
    session: AsyncSession,
    *,
    run_id: UUID,
    payslip_ids: list,
    paid_on: date,
    method: str = "BANK_TRANSFER",
    bank_reference: Optional[str] = None,
    notes: Optional[str] = None,
    paid_by: Optional[UUID] = None,
    paid_by_name: Optional[str] = None,
) -> dict:
    """Record that these payslips have been paid, and post the settlement.

    Refuses rather than guesses:
      * a run that is not APPROVED -- paying an unapproved run means money
        leaving against figures nobody signed off;
      * a payslip already paid -- the commonest way to pay somebody twice is
        a double-submitted form, so it is caught here rather than trusted to
        the browser;
      * a payslip belonging to a different run.
    """
    method = (method or "BANK_TRANSFER").upper()
    if method not in METHOD_ACCOUNTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown payment method {method}.")
    if not payslip_ids:
        raise HTTPException(
            status_code=400, detail="Select at least one payslip to pay.")

    run = (await session.execute(
        text("""SELECT id, run_number, status, period_end
                  FROM payroll_runs WHERE id = :i FOR UPDATE"""),
        {"i": str(run_id)},
    )).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Payroll run not found.")
    if run.status == 'PAID':
        raise HTTPException(
            status_code=400,
            detail=f"Run {run.run_number} is already fully paid.")
    if run.status != 'APPROVED':
        raise HTTPException(
            status_code=400,
            detail=(f"Run {run.run_number} is {run.status}. Only an APPROVED "
                    f"run can be paid -- approval is what signs off the "
                    f"figures the payment settles."))

    wanted = [str(p) for p in payslip_ids]
    slips = (await session.execute(
        text("""SELECT p.id, p.payslip_number, p.net_pay, p.paid_at,
                       p.run_id, s.employee_id, s.first_name, s.last_name
                  FROM payslips p JOIN staff s ON s.id = p.staff_id
                 WHERE p.id = ANY(CAST(:ids AS uuid[]))
                 FOR UPDATE OF p"""),
        {"ids": wanted},
    )).fetchall()

    found = {str(s.id) for s in slips}
    missing = [p for p in wanted if p not in found]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"{len(missing)} payslip(s) not found.")

    wrong_run = [s.payslip_number for s in slips if str(s.run_id) != str(run_id)]
    if wrong_run:
        raise HTTPException(
            status_code=400,
            detail=(f"These payslips belong to a different run: "
                    f"{', '.join(wrong_run)}."))

    already = [f"{s.employee_id} {s.first_name} {s.last_name}"
               for s in slips if s.paid_at is not None]
    if already:
        raise HTTPException(
            status_code=400,
            detail=(f"Already paid, so nothing was recorded: "
                    f"{'; '.join(already)}. Deselect them and try again."))

    total = money(sum((money(s.net_pay) for s in slips), Decimal("0.00")))
    if total <= 0:
        raise HTTPException(
            status_code=400,
            detail="The selected payslips total zero; nothing to pay.")

    batch_id = uuid4()
    batch_number = (f"PAY-{paid_on.strftime('%Y%m%d')}-"
                    f"{uuid4().hex[:6].upper()}")

    entry_id = await post_entry(
        session,
        entry_date=paid_on,
        description=(f"Payroll payment {batch_number} "
                     f"({len(slips)} staff, run {run.run_number})"),
        source_module="payroll",
        source_reference=batch_number,
        lines=[
            Line("2200", debit=total,
                 description=f"Salaries paid -- run {run.run_number}"),
            Line(METHOD_ACCOUNTS[method], credit=total,
                 description=f"{method.replace('_', ' ').title()}"
                             + (f" ref {bank_reference}" if bank_reference else "")),
        ],
        created_by=paid_by,
    )

    await session.execute(
        text("""
            INSERT INTO payroll_payment_batches
                (id, batch_number, run_id, paid_on, method, bank_reference,
                 staff_count, total_net, journal_entry_id, paid_by,
                 paid_by_name, notes)
            VALUES (:id, :num, :run, :on, :m, :ref, :cnt, :total, :je, :by,
                    :byname, :notes)
        """),
        {"id": str(batch_id), "num": batch_number, "run": str(run_id),
         "on": paid_on, "m": method, "ref": bank_reference,
         "cnt": len(slips), "total": str(total),
         "je": str(entry_id) if entry_id else None,
         "by": str(paid_by) if paid_by else None, "byname": paid_by_name,
         "notes": notes},
    )

    await session.execute(
        text("""UPDATE payslips
                   SET paid_at = NOW(), payment_batch_id = :b
                 WHERE id = ANY(CAST(:ids AS uuid[]))"""),
        {"b": str(batch_id), "ids": wanted},
    )

    # The run becomes PAID only when nobody on it is still owed. Anything
    # else and it stays APPROVED, which is the honest description of a run
    # where some people have their money and some have not.
    outstanding = (await session.execute(
        text("""SELECT COUNT(*) FROM payslips
                 WHERE run_id = :r AND paid_at IS NULL"""),
        {"r": str(run_id)},
    )).scalar() or 0

    if outstanding == 0:
        await session.execute(
            text("""UPDATE payroll_runs
                       SET status = 'PAID', paid_at = NOW()
                     WHERE id = :i"""),
            {"i": str(run_id)},
        )

    return {
        "batch_id": str(batch_id),
        "batch_number": batch_number,
        "run_number": run.run_number,
        "paid_on": paid_on.isoformat(),
        "method": method,
        "bank_reference": bank_reference,
        "staff_count": len(slips),
        "total_net": float(total),
        "journal_entry_id": str(entry_id) if entry_id else None,
        "outstanding_payslips": int(outstanding),
        "run_status": 'PAID' if outstanding == 0 else 'APPROVED',
        "paid": [
            {"payslip_number": s.payslip_number,
             "employee_id": s.employee_id,
             "name": f"{s.first_name} {s.last_name}",
             "net_pay": float(money(s.net_pay))}
            for s in slips],
    }


async def payment_report(
    session: AsyncSession, *, period_start: date, period_end: date,
) -> dict:
    """What this period costs, who has been paid, and who is still owed.

    One figure on this report is the one people actually ask for -- the total
    to be paid across every member of staff processed for the period -- and
    two more are the ones that stop it being misread: the employer's own
    contributions, which staff never see but the company still pays, and the
    amount still outstanding.
    """
    runs = (await session.execute(
        text("""SELECT id, run_number, status, gross_total, deductions_total,
                       net_total, employer_cost_total, approved_by,
                       approved_at, paid_at
                  FROM payroll_runs
                 WHERE period_start = :ps AND period_end = :pe
                   AND status <> 'CANCELLED'
                 ORDER BY created_at"""),
        {"ps": period_start, "pe": period_end},
    )).fetchall()

    if not runs:
        return {
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "runs": [], "staff_count": 0,
            "gross_total": 0.0, "deductions_total": 0.0, "net_total": 0.0,
            "employer_cost_total": 0.0, "paid_total": 0.0,
            "outstanding_total": 0.0, "paid_count": 0, "unpaid_count": 0,
            "deduction_breakdown": [], "batches": [], "lines": [],
            "note": "No payroll has been processed for this period.",
        }

    run_ids = [str(r.id) for r in runs]

    lines = (await session.execute(
        text("""SELECT p.id, p.payslip_number, p.gross_pay,
                       p.total_deductions, p.net_pay, p.paid_at,
                       s.employee_id, s.first_name, s.last_name, s.position,
                       s.bank_name, s.bank_account_number, s.bank_account_name,
                       r.run_number, b.batch_number, b.paid_on, b.method
                  FROM payslips p
                  JOIN staff s ON s.id = p.staff_id
                  JOIN payroll_runs r ON r.id = p.run_id
             LEFT JOIN payroll_payment_batches b ON b.id = p.payment_batch_id
                 WHERE p.run_id = ANY(CAST(:ids AS uuid[]))
                 ORDER BY s.employee_id"""),
        {"ids": run_ids},
    )).fetchall()

    breakdown = (await session.execute(
        text("""SELECT pc.code, pc.label, pc.component_type,
                       SUM(pc.amount) AS total
                  FROM payslip_components pc
                  JOIN payslips p ON p.id = pc.payslip_id
                 WHERE p.run_id = ANY(CAST(:ids AS uuid[]))
                   AND pc.component_type IN
                       ('DEDUCTION','EMPLOYER_CONTRIBUTION')
                 GROUP BY pc.code, pc.label, pc.component_type
                 ORDER BY pc.component_type, SUM(pc.amount) DESC"""),
        {"ids": run_ids},
    )).fetchall()

    batches = (await session.execute(
        text("""SELECT batch_number, paid_on, method, bank_reference,
                       staff_count, total_net, paid_by_name
                  FROM payroll_payment_batches
                 WHERE run_id = ANY(CAST(:ids AS uuid[]))
                 ORDER BY paid_on, batch_number"""),
        {"ids": run_ids},
    )).fetchall()

    paid_total = money(sum((money(l.net_pay) for l in lines if l.paid_at),
                           Decimal("0.00")))
    outstanding = money(sum((money(l.net_pay) for l in lines if not l.paid_at),
                            Decimal("0.00")))

    return {
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "runs": [
            {"run_number": r.run_number, "status": r.status,
             "approved_by": r.approved_by,
             "approved_at": r.approved_at.isoformat() if r.approved_at else None,
             "paid_at": r.paid_at.isoformat() if r.paid_at else None}
            for r in runs],
        "staff_count": len(lines),
        "gross_total": float(money(sum((money(r.gross_total) for r in runs),
                                       Decimal("0.00")))),
        "deductions_total": float(money(sum(
            (money(r.deductions_total) for r in runs), Decimal("0.00")))),
        "net_total": float(money(sum((money(r.net_total) for r in runs),
                                     Decimal("0.00")))),
        "employer_cost_total": float(money(sum(
            (money(r.employer_cost_total) for r in runs), Decimal("0.00")))),
        "paid_total": float(paid_total),
        "outstanding_total": float(outstanding),
        "paid_count": sum(1 for l in lines if l.paid_at),
        "unpaid_count": sum(1 for l in lines if not l.paid_at),
        "deduction_breakdown": [
            {"code": b.code, "label": b.label, "type": b.component_type,
             "total": float(money(b.total))}
            for b in breakdown],
        "batches": [
            {"batch_number": b.batch_number, "paid_on": str(b.paid_on),
             "method": b.method, "bank_reference": b.bank_reference,
             "staff_count": b.staff_count, "total_net": float(money(b.total_net)),
             "paid_by_name": b.paid_by_name}
            for b in batches],
        "lines": [
            {"payslip_id": str(l.id), "payslip_number": l.payslip_number,
             "employee_id": l.employee_id,
             "name": f"{l.first_name} {l.last_name}",
             "position": l.position or "",
             "gross_pay": float(money(l.gross_pay)),
             "total_deductions": float(money(l.total_deductions)),
             "net_pay": float(money(l.net_pay)),
             "paid": l.paid_at is not None,
             "paid_on": str(l.paid_on) if l.paid_on else None,
             "method": l.method,
             "batch_number": l.batch_number,
             "run_number": l.run_number,
             "bank_name": l.bank_name or "",
             "bank_account_number": l.bank_account_number or "",
             "bank_account_name": l.bank_account_name or ""}
            for l in lines],
        "note": (
            "Net total is what staff receive. Employer cost is what the "
            "company pays on top and never appears on a payslip. Outstanding "
            "is net pay approved but not yet paid, and it is the balance "
            "sitting in Staff Salary Payable."),
    }
