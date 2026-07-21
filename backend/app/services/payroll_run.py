"""Payroll run orchestration: calculate, persist, approve, post.

Separated from the calculation engine (app.services.payroll) so the
arithmetic can be reviewed and tested independently of the workflow.

A run is created as DRAFT and pays nobody. It must be approved before it
posts to the ledger, because payroll is the one place where an error is both
expensive and immediately visible to every member of staff.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.payroll import (
    calculate_payslip, load_rate_config, money)


async def create_payroll_run(
    session: AsyncSession,
    *,
    period_start: date,
    period_end: date,
    staff_ids: Optional[list] = None,
    bonuses: Optional[dict] = None,
    created_by: Optional[UUID] = None,
) -> dict:
    """Calculate a payroll run and persist it as DRAFT."""
    if period_end < period_start:
        raise HTTPException(
            status_code=400, detail="period_end cannot precede period_start.")

    # Refuse a second run for the same period unless the earlier one was
    # cancelled. Paying a month twice is not recoverable by a code fix.
    clash = (await session.execute(
        text("""SELECT run_number, status FROM payroll_runs
                 WHERE period_start = :s AND period_end = :e
                   AND status <> 'CANCELLED' LIMIT 1"""),
        {"s": period_start, "e": period_end},
    )).first()
    if clash:
        raise HTTPException(
            status_code=400,
            detail=(f"Payroll run {clash.run_number} already exists for this "
                    f"period (status {clash.status}). Cancel it before "
                    f"creating another."),
        )

    config = await load_rate_config(session, on=period_end)

    where = "WHERE s.is_active = TRUE"
    params = {}
    if staff_ids:
        where += " AND s.id = ANY(CAST(:ids AS uuid[]))"
        params["ids"] = [str(s) for s in staff_ids]

    staff = (await session.execute(
        text(f"""
            SELECT s.id, s.employee_id, s.first_name, s.last_name,
                   s.payment_mode, s.hourly_rate, s.monthly_salary,
                   s.basic_salary, s.housing_allowance, s.transport_allowance,
                   s.other_allowances, s.tax_exempt
              FROM staff s {where}
             ORDER BY s.employee_id
        """),
        params,
    )).fetchall()

    if not staff:
        raise HTTPException(
            status_code=400, detail="No active staff to run payroll for.")

    run_id = uuid4()
    run_number = f"PR-{period_start.strftime('%Y%m')}-{uuid4().hex[:6].upper()}"
    await session.execute(
        text("""
            INSERT INTO payroll_runs
                (id, run_number, period_start, period_end, config_id,
                 status, created_by)
            VALUES (:id, :num, :s, :e, :cfg, 'DRAFT', :by)
        """),
        {"id": str(run_id), "num": run_number, "s": period_start,
         "e": period_end, "cfg": str(config.config_id),
         "by": str(created_by) if created_by else None},
    )

    bonuses = bonuses or {}
    gross_total = Decimal("0.00")
    deductions_total = Decimal("0.00")
    net_total = Decimal("0.00")
    employer_total = Decimal("0.00")
    skipped = []

    for st in staff:
        hours = None
        if (st.payment_mode or "").lower() == "hourly":
            hours = (await session.execute(
                text("""
                    SELECT COALESCE(SUM(hours_worked), 0) FROM attendance
                     WHERE staff_id = :s
                       AND DATE(clock_in) BETWEEN :ps AND :pe
                       AND status = 'completed'
                """),
                {"s": str(st.id), "ps": period_start, "pe": period_end},
            )).scalar()

        slip = await calculate_payslip(
            session, staff_row=st, config=config,
            period_start=period_start, period_end=period_end,
            hours_worked=hours,
            bonus=Decimal(str(bonuses.get(str(st.id), 0))),
        )

        if slip.gross <= 0:
            # Not an error (an hourly worker with no attendance), but
            # reported so nobody is silently omitted from a payroll run.
            skipped.append(
                f"{st.employee_id} {st.first_name} {st.last_name}")
            continue

        payslip_id = uuid4()
        await session.execute(
            text("""
                INSERT INTO payslips
                    (id, run_id, staff_id, payslip_number, basic_salary,
                     gross_pay, taxable_income, total_deductions, net_pay,
                     employer_contributions, regular_hours, overtime_hours)
                VALUES (:id, :run, :staff, :num, :basic, :gross, :taxable,
                        :ded, :net, :emp, :rh, :oh)
            """),
            {"id": str(payslip_id), "run": str(run_id), "staff": str(st.id),
             "num": f"PS-{run_number}-{st.employee_id}",
             "basic": str(slip.basic), "gross": str(slip.gross),
             "taxable": str(slip.taxable),
             "ded": str(slip.total_deductions), "net": str(slip.net),
             "emp": str(slip.employer_contributions),
             "rh": str(slip.regular_hours), "oh": str(slip.overtime_hours)},
        )
        for c in slip.components:
            await session.execute(
                text("""
                    INSERT INTO payslip_components
                        (payslip_id, component_type, code, label, amount,
                         basis_amount, rate_applied, sequence)
                    VALUES (:p, :t, :c, :l, :a, :b, :r, :seq)
                """),
                {"p": str(payslip_id), "t": c["component_type"],
                 "c": c["code"], "l": c["label"], "a": str(c["amount"]),
                 "b": (str(c["basis_amount"])
                       if c["basis_amount"] is not None else None),
                 "r": (str(c["rate_applied"])
                       if c["rate_applied"] is not None else None),
                 "seq": c["sequence"]},
            )

        gross_total += slip.gross
        deductions_total += slip.total_deductions
        net_total += slip.net
        employer_total += slip.employer_contributions

    await session.execute(
        text("""UPDATE payroll_runs
                   SET gross_total = :g, deductions_total = :d,
                       net_total = :n, employer_cost_total = :e
                 WHERE id = :id"""),
        {"g": str(gross_total), "d": str(deductions_total),
         "n": str(net_total), "e": str(employer_total), "id": str(run_id)},
    )

    return {
        "run_id": run_id, "run_number": run_number,
        "rate_config": config.name,
        "staff_paid": len(staff) - len(skipped),
        "staff_skipped": skipped,
        "gross_total": gross_total,
        "deductions_total": deductions_total,
        "net_total": net_total,
        "employer_cost_total": employer_total,
    }


async def approve_payroll_run(
    session: AsyncSession, *, run_id: UUID, approved_by: str,
    created_by: Optional[UUID] = None,
) -> dict:
    """Approve a run and post it to the general ledger.

    The journal recognises the full employment cost and splits the credits
    between what staff receive and what is withheld on their behalf:

        Dr Salaries & Wages       (gross + employer contributions)
        Cr Staff Salary Payable   (net pay -- what staff actually receive)
        Cr PAYE Payable           (withheld, owed to the tax authority)
        Cr Pension Payable        (employee + employer portions)
        Cr NHF / NHIA Payable

    Withheld amounts are LIABILITIES, not costs: the company holds them on
    behalf of others until remitted. Booking PAYE as an expense would
    overstate the cost of employment and understate what is owed.
    """
    run = (await session.execute(
        text("""SELECT id, run_number, period_end, status, gross_total,
                       net_total, employer_cost_total
                  FROM payroll_runs WHERE id = :i FOR UPDATE"""),
        {"i": str(run_id)},
    )).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Payroll run not found.")
    if run.status != 'DRAFT':
        raise HTTPException(
            status_code=400,
            detail=(f"Run {run.run_number} is {run.status}; only DRAFT runs "
                    f"can be approved."))

    totals = {
        r.code: money(r.total) for r in (await session.execute(
            text("""
                SELECT pc.code, SUM(pc.amount) AS total
                  FROM payslip_components pc
                  JOIN payslips p ON p.id = pc.payslip_id
                 WHERE p.run_id = :r AND pc.component_type IN
                       ('DEDUCTION','EMPLOYER_CONTRIBUTION')
                 GROUP BY pc.code
            """), {"r": str(run_id)})).fetchall()
    }

    from app.services.ledger import Line, post_entry

    gross = money(run.gross_total)
    employer = money(run.employer_cost_total)
    net = money(run.net_total)

    lines = [
        Line("6100", debit=money(gross + employer),
             description=f"Payroll {run.run_number}: gross + employer cost"),
        Line("2200", credit=net, description="Net pay due to staff"),
    ]
    for code, account, label in [
        ("PAYE", "2210", "PAYE withheld"),
        ("NHF", "2230", "NHF withheld"),
        ("NHIA", "2240", "NHIA withheld"),
    ]:
        if totals.get(code):
            lines.append(
                Line(account, credit=totals[code], description=label))

    pension_total = money(totals.get("PENSION", Decimal(0))
                          + totals.get("PENSION_EMPLOYER", Decimal(0)))
    if pension_total > 0:
        lines.append(Line("2220", credit=pension_total,
                          description="Pension payable (employee + employer)"))

    if totals.get("NHIA_EMPLOYER"):
        lines.append(Line("2240", credit=totals["NHIA_EMPLOYER"],
                          description="Employer health insurance payable"))

    # Loan and advance recoveries reduce what staff receive but are not a
    # cost -- they repay an amount the company already advanced.
    statutory = {"PAYE", "PENSION", "NHF", "NHIA",
                 "PENSION_EMPLOYER", "NHIA_EMPLOYER"}
    other_recoveries = money(sum(
        (v for k, v in totals.items() if k not in statutory),
        Decimal("0.00")))
    if other_recoveries > 0:
        lines.append(Line("1300", credit=other_recoveries,
                          description="Loan / advance recovered via payroll"))

    entry_id = await post_entry(
        session,
        entry_date=run.period_end,
        description=f"Payroll {run.run_number}",
        source_module="payroll",
        source_reference=run.run_number,
        lines=lines,
        created_by=created_by,
    )

    # Advance loan balances only now the run is approved.
    await session.execute(
        text("""
            UPDATE staff_deductions sd
               SET amount_recovered = sd.amount_recovered + x.taken,
                   is_active = CASE
                       WHEN sd.amount_recovered + x.taken >= sd.total_amount
                       THEN FALSE ELSE sd.is_active END
              FROM (
                  SELECT p.staff_id, pc.code, SUM(pc.amount) AS taken
                    FROM payslip_components pc
                    JOIN payslips p ON p.id = pc.payslip_id
                   WHERE p.run_id = :r AND pc.component_type = 'DEDUCTION'
                     AND pc.code NOT IN ('PAYE','PENSION','NHF','NHIA')
                   GROUP BY p.staff_id, pc.code
              ) x
             WHERE sd.staff_id = x.staff_id AND sd.code = x.code
               AND sd.is_active = TRUE
        """),
        {"r": str(run_id)},
    )

    await session.execute(
        text("""UPDATE payroll_runs
                   SET status = 'APPROVED', approved_by = :by,
                       approved_at = NOW(), journal_entry_id = :je
                 WHERE id = :i"""),
        {"by": approved_by, "je": str(entry_id) if entry_id else None,
         "i": str(run_id)},
    )

    return {"run_number": run.run_number, "journal_entry_id": entry_id,
            "posted": entry_id is not None}
