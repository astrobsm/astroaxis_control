"""Payroll calculation engine.

WHAT THIS MODULE DOES AND DOES NOT GUARANTEE
--------------------------------------------
It guarantees the ARITHMETIC: progressive bands are applied correctly,
reliefs are deducted in the right order, contributions use the right basis,
money is Decimal, and every figure on a payslip is itemised and reproducible.

It does NOT guarantee the RATES. Every band and percentage is read from
`payroll_rate_configs` and its child tables, which an accountant maintains.
The engine refuses to run against a configuration that has not been
explicitly confirmed, because the seeded values are a starting shape rather
than legal advice.

The previous implementation paid every staff member a hardcoded NGN 425/hour
and deducted nothing at all -- no PAYE, no pension, no NHF, no NHIA. Since
under-deducted PAYE is recoverable from the employer rather than the
employee, that was an accruing liability, not a display bug.

ORDER OF CALCULATION
--------------------
    gross            = basic + housing + transport + other + overtime + bonus
    pensionable      = basic + housing + transport      (statutory basis)
    pension employee = rate x pensionable
    NHF              = rate x basic
    NHIA employee    = rate x basic
    consolidated relief = max(fixed, min% x gross) + percent% x gross
    taxable          = gross - relief - pension - NHF - NHIA
    PAYE             = progressive bands applied to ANNUAL taxable, / 12
    net              = gross - PAYE - pension - NHF - NHIA - loans/advances

Tax is computed annually then apportioned because Nigerian personal income
tax is assessed on annual income; applying the bands to a single month's pay
gives a different (and wrong) answer for anyone whose pay varies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

CENT = Decimal("0.01")
MONTHS = Decimal("12")


def money(value) -> Decimal:
    if value is None:
        return Decimal("0.00")
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


def _pct(value) -> Decimal:
    """A percentage as a multiplier: 8.0 -> 0.08."""
    return (Decimal(str(value)) / Decimal("100"))


@dataclass
class RateConfig:
    config_id: UUID
    name: str
    items: dict
    bands: list
    is_confirmed: bool

    def item(self, code: str, default=None) -> Decimal:
        row = self.items.get(code)
        if row is None:
            if default is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"Payroll rate {code!r} is not configured.")
            return Decimal(str(default))
        return Decimal(str(row["value"]))


@dataclass
class PayslipResult:
    staff_id: UUID
    basic: Decimal = Decimal("0.00")
    gross: Decimal = Decimal("0.00")
    taxable: Decimal = Decimal("0.00")
    total_deductions: Decimal = Decimal("0.00")
    net: Decimal = Decimal("0.00")
    employer_contributions: Decimal = Decimal("0.00")
    regular_hours: Decimal = Decimal("0.00")
    overtime_hours: Decimal = Decimal("0.00")
    components: list = field(default_factory=list)

    def add(self, ctype, code, label, amount, basis=None, rate=None):
        self.components.append({
            "component_type": ctype, "code": code, "label": label,
            "amount": money(amount),
            "basis_amount": money(basis) if basis is not None else None,
            "rate_applied": Decimal(str(rate)) if rate is not None else None,
            "sequence": len(self.components) + 1,
        })


# ---------------------------------------------------------------------------
# Rate configuration
# ---------------------------------------------------------------------------

async def load_rate_config(
    session: AsyncSession, *, on: date, require_confirmed: bool = True
) -> RateConfig:
    """Load the rate configuration in force on a date.

    Refuses unconfirmed configurations unless explicitly overridden (which
    only the preview endpoint does, clearly labelled). Running real payroll
    on unreviewed tax rates is how a company accrues a PAYE liability it does
    not know about.
    """
    row = (await session.execute(
        text("""
            SELECT id, name, is_confirmed, confirmed_by
              FROM payroll_rate_configs
             WHERE effective_from <= :d
               AND (effective_to IS NULL OR effective_to >= :d)
             ORDER BY effective_from DESC
             LIMIT 1
        """),
        {"d": on},
    )).first()

    if row is None:
        raise HTTPException(
            status_code=400,
            detail=f"No payroll rate configuration is effective on {on}.")

    if require_confirmed and not row.is_confirmed:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Payroll rate configuration {row.name!r} has not been "
                f"confirmed. The seeded tax bands and contribution rates are "
                f"a starting shape only and have NOT been verified against "
                f"current Nigerian tax law. An accountant must review and "
                f"confirm them (POST /api/payroll/rate-configs/{{id}}/confirm) "
                f"before payroll can be run."
            ),
        )

    items = {
        r.code: {"value": r.value, "basis": r.basis}
        for r in (await session.execute(
            text("SELECT code, value, basis FROM payroll_rate_items "
                 "WHERE config_id = :c"), {"c": str(row.id)})).fetchall()
    }
    bands = [
        {"lower": Decimal(str(b.lower_bound)),
         "upper": Decimal(str(b.upper_bound)) if b.upper_bound is not None else None,
         "rate": Decimal(str(b.rate_percent))}
        for b in (await session.execute(
            text("SELECT lower_bound, upper_bound, rate_percent "
                 "FROM payroll_tax_bands WHERE config_id = :c "
                 "ORDER BY sequence"), {"c": str(row.id)})).fetchall()
    ]

    if not bands:
        raise HTTPException(
            status_code=400,
            detail=f"Rate configuration {row.name!r} has no tax bands defined.")

    return RateConfig(config_id=row.id, name=row.name, items=items,
                      bands=bands, is_confirmed=row.is_confirmed)


def compute_paye(annual_taxable: Decimal, bands: list) -> Decimal:
    """Progressive tax across the bands.

    Each band taxes only the slice of income that falls inside it -- a common
    error is applying the top band's rate to the whole amount, which
    massively over-deducts.
    """
    if annual_taxable <= 0:
        return Decimal("0.00")

    tax = Decimal("0.00")
    for band in bands:
        lower = band["lower"]
        upper = band["upper"]
        if annual_taxable <= lower:
            break
        slice_top = annual_taxable if upper is None else min(annual_taxable, upper)
        slice_amount = slice_top - lower
        if slice_amount > 0:
            tax += slice_amount * _pct(band["rate"])
    return money(tax)


# ---------------------------------------------------------------------------
# Payslip calculation
# ---------------------------------------------------------------------------

async def calculate_payslip(
    session: AsyncSession,
    *,
    staff_row,
    config: RateConfig,
    period_start: date,
    period_end: date,
    hours_worked: Optional[Decimal] = None,
    bonus: Decimal = Decimal("0.00"),
) -> PayslipResult:
    """Compute one staff member's pay for a period.

    `staff_row` must expose basic_salary, housing_allowance,
    transport_allowance, other_allowances, hourly_rate, payment_mode,
    tax_exempt.
    """
    res = PayslipResult(staff_id=staff_row.id)

    # ---- Earnings ----
    basic = money(staff_row.basic_salary or staff_row.monthly_salary or 0)
    housing = money(getattr(staff_row, "housing_allowance", 0) or 0)
    transport = money(getattr(staff_row, "transport_allowance", 0) or 0)
    other = money(getattr(staff_row, "other_allowances", 0) or 0)

    overtime_pay = Decimal("0.00")
    if (staff_row.payment_mode or "").lower() == "hourly":
        # Hourly staff: pay follows actual attendance, using THEIR rate.
        # The old engine ignored hourly_rate entirely and paid everyone the
        # same hardcoded figure.
        rate = money(staff_row.hourly_rate or 0)
        std = config.item("STANDARD_MONTHLY_HOURS", 160)
        mult = config.item("OVERTIME_MULTIPLIER", 1.5)
        worked = Decimal(str(hours_worked or 0))
        res.regular_hours = min(worked, std)
        res.overtime_hours = max(Decimal("0"), worked - std)
        basic = money(res.regular_hours * rate)
        overtime_pay = money(res.overtime_hours * rate * mult)
        res.add("EARNING", "BASIC", "Basic pay (hours worked)", basic,
                basis=res.regular_hours, rate=rate)
        if overtime_pay > 0:
            res.add("EARNING", "OVERTIME", "Overtime", overtime_pay,
                    basis=res.overtime_hours, rate=rate * mult)
    else:
        res.add("EARNING", "BASIC", "Basic salary", basic)

    for code, label, amount in [("HOUSING", "Housing allowance", housing),
                                ("TRANSPORT", "Transport allowance", transport),
                                ("OTHER", "Other allowances", other)]:
        if amount > 0:
            res.add("EARNING", code, label, amount)

    bonus = money(bonus)
    if bonus > 0:
        res.add("EARNING", "BONUS", "Bonus", bonus)

    gross = money(basic + housing + transport + other + overtime_pay + bonus)
    res.basic = basic
    res.gross = gross

    if gross <= 0:
        return res  # nothing earned, nothing to deduct

    # ---- Statutory contributions ----
    # Pensionable pay is basic + housing + transport, not total gross.
    pensionable = money(basic + housing + transport)

    pension_rate = config.item("PENSION_EMPLOYEE")
    pension = money(pensionable * _pct(pension_rate))

    nhf_rate = config.item("NHF")
    nhf = money(basic * _pct(nhf_rate))

    nhia_rate = config.item("NHIA_EMPLOYEE", 0)
    nhia = money(basic * _pct(nhia_rate))

    # ---- PAYE ----
    if getattr(staff_row, "tax_exempt", False):
        paye = Decimal("0.00")
        taxable = Decimal("0.00")
    else:
        annual_gross = gross * MONTHS
        cra_fixed = config.item("CRA_FIXED")
        cra_min_pct = config.item("CRA_MIN_PERCENT_GROSS")
        cra_pct = config.item("CRA_PERCENT_GROSS")

        relief = money(
            max(cra_fixed, annual_gross * _pct(cra_min_pct))
            + annual_gross * _pct(cra_pct)
        )
        annual_statutory = money((pension + nhf + nhia) * MONTHS)
        annual_taxable = money(max(
            Decimal("0"), annual_gross - relief - annual_statutory))

        annual_paye = compute_paye(annual_taxable, config.bands)

        # Minimum tax: where the computed liability is trivially small but
        # income is not, statute applies a floor.
        min_pct = config.item("MINIMUM_TAX_PERCENT", 0)
        if min_pct > 0 and annual_taxable > 0:
            annual_paye = max(annual_paye,
                              money(annual_gross * _pct(min_pct)))

        paye = money(annual_paye / MONTHS)
        taxable = money(annual_taxable / MONTHS)

    res.taxable = taxable

    for code, label, amount, basis, rate in [
        ("PAYE", "PAYE tax", paye, taxable, None),
        ("PENSION", "Pension contribution", pension, pensionable, pension_rate),
        ("NHF", "National Housing Fund", nhf, basic, nhf_rate),
        ("NHIA", "Health insurance", nhia, basic, nhia_rate),
    ]:
        if amount > 0:
            res.add("DEDUCTION", code, label, amount, basis=basis, rate=rate)

    statutory_deductions = money(paye + pension + nhf + nhia)

    # ---- Loans and salary advances ----
    loans = (await session.execute(
        text("""
            SELECT id, code, label, amount_per_period,
                   total_amount - amount_recovered AS remaining
              FROM staff_deductions
             WHERE staff_id = :s AND is_active = TRUE
               AND start_date <= :d
               AND amount_recovered < total_amount
             ORDER BY created_at
        """),
        {"s": str(staff_row.id), "d": period_end},
    )).fetchall()

    other_deductions = Decimal("0.00")
    for ln in loans:
        # Never recover more than is outstanding -- the final instalment of a
        # loan is usually smaller than the standard amount.
        take = money(min(Decimal(str(ln.amount_per_period)),
                         Decimal(str(ln.remaining))))
        if take <= 0:
            continue
        other_deductions += take
        res.add("DEDUCTION", ln.code, ln.label, take)
        res.components[-1]["_deduction_id"] = ln.id

    res.total_deductions = money(statutory_deductions + other_deductions)
    res.net = money(gross - res.total_deductions)

    # A negative net means deductions exceed earnings. Surface it rather than
    # paying a negative amount or silently clamping to zero.
    if res.net < 0:
        raise HTTPException(
            status_code=400,
            detail=(f"Deductions ({res.total_deductions:,.2f}) exceed gross "
                    f"pay ({gross:,.2f}) for this staff member. Review their "
                    f"loan repayment schedule."),
        )

    # ---- Employer contributions (company cost, not deducted from staff) ----
    emp_pension = money(pensionable * _pct(config.item("PENSION_EMPLOYER", 0)))
    emp_nhia = money(basic * _pct(config.item("NHIA_EMPLOYER", 0)))
    for code, label, amount in [
        ("PENSION_EMPLOYER", "Employer pension contribution", emp_pension),
        ("NHIA_EMPLOYER", "Employer health insurance", emp_nhia),
    ]:
        if amount > 0:
            res.add("EMPLOYER_CONTRIBUTION", code, label, amount)
    res.employer_contributions = money(emp_pension + emp_nhia)

    return res
