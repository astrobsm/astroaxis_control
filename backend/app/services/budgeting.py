"""Budgets, variance analysis, and cost centre reporting.

A budget here is a plan to compare against, not a control that blocks
spending. Nothing in this module refuses a transaction for being over budget:
a system that stops a legitimate emergency purchase because a number in a
table says no is a system people learn to route around, and then the budget
stops reflecting reality entirely.

VARIANCE SIGN CONVENTION
------------------------
Variance is reported as FAVOURABLE or ADVERSE rather than as a bare positive
or negative number, because the sign alone is ambiguous: spending less than
budget is good, earning less than budget is bad, and both are "under". Each
figure carries its own interpretation so a reader cannot draw the wrong
conclusion from a minus sign.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

CENT = Decimal("0.01")

# Accounts where spending MORE than budget is bad.
SPEND_TYPES = ("EXPENSE",)
# Accounts where earning LESS than budget is bad.
EARN_TYPES = ("INCOME",)


def money(value) -> Decimal:
    if value is None:
        return Decimal("0.00")
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


def classify_variance(account_type: str, budget: Decimal,
                      actual: Decimal) -> tuple[Decimal, str]:
    """Return (variance, 'FAVOURABLE' | 'ADVERSE' | 'ON_BUDGET').

    Variance is always actual - budget. What that MEANS depends on whether
    the account is something you spend or something you earn.
    """
    variance = money(actual - budget)
    if variance == 0:
        return variance, "ON_BUDGET"
    if account_type in SPEND_TYPES:
        # Spending more than planned is adverse.
        return variance, ("ADVERSE" if variance > 0 else "FAVOURABLE")
    if account_type in EARN_TYPES:
        # Earning more than planned is favourable.
        return variance, ("FAVOURABLE" if variance > 0 else "ADVERSE")
    return variance, "ON_BUDGET"


# ---------------------------------------------------------------------------
# Cost centres
# ---------------------------------------------------------------------------

async def resolve_cost_centre(session: AsyncSession, *, code: str) -> str:
    """Return the canonical code, rejecting anything not in the master.

    Free text is what let 'Production', 'production' and 'PRODUCTION ' report
    as three separate cost centres.
    """
    clean = (code or "").strip()
    if not clean:
        raise HTTPException(
            status_code=400, detail="Cost centre code is required.")
    row = (await session.execute(
        text("""SELECT code FROM cost_centres
                 WHERE UPPER(TRIM(code)) = UPPER(TRIM(:c))
                   AND is_active = TRUE"""),
        {"c": clean},
    )).first()
    if row is None:
        raise HTTPException(
            status_code=400,
            detail=f"Cost centre {clean!r} is not defined. Add it to the cost "
                   f"centre master first so reporting stays consistent.")
    return row.code


async def cost_centre_report(
    session: AsyncSession, *, start: date, end: date,
    cost_centre: Optional[str] = None,
) -> dict:
    """Income and expenditure by cost centre for a period.

    Postings with no cost centre are reported explicitly as UNALLOCATED
    rather than dropped, because unallocated cost is a real finding: it means
    someone posted without saying which department bore it.
    """
    clauses = ["e.status <> 'DRAFT'", "e.entry_date BETWEEN :start AND :end",
               "a.account_type IN ('INCOME','EXPENSE')"]
    params = {"start": start, "end": end}
    if cost_centre:
        clauses.append("COALESCE(l.cost_centre, '') = :cc")
        params["cc"] = cost_centre

    rows = (await session.execute(
        text(f"""
            SELECT COALESCE(NULLIF(TRIM(l.cost_centre), ''), 'UNALLOCATED')
                       AS centre,
                   a.account_type,
                   SUM(CASE WHEN a.account_type = 'EXPENSE'
                            THEN l.debit - l.credit
                            ELSE l.credit - l.debit END) AS amount
              FROM gl_journal_lines l
              JOIN gl_journal_entries e ON e.id = l.entry_id
              JOIN gl_accounts a ON a.id = l.account_id
             WHERE {' AND '.join(clauses)}
             GROUP BY centre, a.account_type
        """),
        params,
    )).fetchall()

    centres = {}
    for r in rows:
        c = centres.setdefault(r.centre, {
            "cost_centre": r.centre, "income": Decimal("0.00"),
            "expenditure": Decimal("0.00")})
        if r.account_type == 'INCOME':
            c["income"] += money(r.amount)
        else:
            c["expenditure"] += money(r.amount)

    names = {
        r.code: r.name for r in (await session.execute(
            text("SELECT code, name FROM cost_centres"))).fetchall()
    }

    out = []
    for code, c in sorted(centres.items()):
        net = money(c["income"] - c["expenditure"])
        out.append({
            "cost_centre": code,
            "name": names.get(code, "Unallocated postings"
                              if code == "UNALLOCATED" else code),
            "income": float(c["income"]),
            "expenditure": float(c["expenditure"]),
            "net": float(net),
        })

    unallocated = next(
        (c for c in out if c["cost_centre"] == "UNALLOCATED"), None)

    return {
        "period": {"start": str(start), "end": str(end)},
        "cost_centres": out,
        "total_income": float(money(sum(
            Decimal(str(c["income"])) for c in out))),
        "total_expenditure": float(money(sum(
            Decimal(str(c["expenditure"])) for c in out))),
        "unallocated_warning": (
            f"{unallocated['expenditure']:,.2f} of expenditure and "
            f"{unallocated['income']:,.2f} of income were posted without a "
            f"cost centre and cannot be attributed to a department."
            if unallocated else None),
    }


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------

async def approve_budget(
    session: AsyncSession, *, budget_id: UUID, approved_by: str) -> dict:
    """Approve a budget, superseding any previously approved one for the year.

    Only one budget per fiscal year may be approved, otherwise "are we over
    budget?" has more than one answer.
    """
    budget = (await session.execute(
        text("""SELECT id, name, fiscal_year, status FROM budgets
                 WHERE id = :b FOR UPDATE"""),
        {"b": str(budget_id)},
    )).first()
    if budget is None:
        raise HTTPException(status_code=404, detail="Budget not found.")
    if budget.status == 'APPROVED':
        raise HTTPException(
            status_code=400, detail="Budget is already approved.")
    if budget.status == 'CLOSED':
        raise HTTPException(
            status_code=400, detail="A closed budget cannot be approved.")

    superseded = (await session.execute(
        text("""UPDATE budgets SET status = 'SUPERSEDED'
                 WHERE fiscal_year = :y AND status = 'APPROVED'
                   AND id <> :b
             RETURNING id, name"""),
        {"y": budget.fiscal_year, "b": str(budget_id)},
    )).fetchall()

    await session.execute(
        text("""UPDATE budgets
                   SET status = 'APPROVED', approved_by = :by,
                       approved_at = NOW(),
                       supersedes_id = :sup
                 WHERE id = :b"""),
        {"by": approved_by, "b": str(budget_id),
         "sup": str(superseded[0].id) if superseded else None},
    )

    return {
        "budget": budget.name,
        "fiscal_year": budget.fiscal_year,
        "superseded": [s.name for s in superseded],
    }


async def budget_variance(
    session: AsyncSession, *, fiscal_year: int,
    start: Optional[date] = None, end: Optional[date] = None,
    cost_centre: Optional[str] = None,
) -> dict:
    """Budget against actual for a period, with variance interpreted.

    Compares only against the APPROVED budget: reporting against a draft
    would let the comparison shift while someone is still editing it.
    """
    budget = (await session.execute(
        text("""SELECT id, name, period_start, period_end
                  FROM budgets
                 WHERE fiscal_year = :y AND status = 'APPROVED'
                 LIMIT 1"""),
        {"y": fiscal_year},
    )).first()
    if budget is None:
        raise HTTPException(
            status_code=400,
            detail=(f"No approved budget for {fiscal_year}. A draft budget is "
                    f"not reported against -- approve it first."))

    start = start or budget.period_start
    end = end or budget.period_end

    cc_filter = ""
    params = {"b": str(budget.id), "start": start, "end": end}
    if cost_centre:
        cc_filter = "AND COALESCE(bl.cost_centre, '') = :cc"
        params["cc"] = cost_centre

    budgeted = {
        (r.account_code, r.cost_centre or ''): money(r.total)
        for r in (await session.execute(
            text(f"""
                SELECT bl.account_code, bl.cost_centre, SUM(bl.amount) AS total
                  FROM budget_lines bl
                 WHERE bl.budget_id = :b
                   AND bl.period_month BETWEEN
                       DATE_TRUNC('month', CAST(:start AS date))
                       AND CAST(:end AS date)
                   {cc_filter}
                 GROUP BY bl.account_code, bl.cost_centre
            """), params)).fetchall()
    }

    actual_cc = ""
    aparams = {"start": start, "end": end}
    if cost_centre:
        actual_cc = "AND COALESCE(l.cost_centre, '') = :cc"
        aparams["cc"] = cost_centre

    actuals = {}
    types = {}
    names = {}
    for r in (await session.execute(
        text(f"""
            SELECT a.code, a.name, a.account_type,
                   COALESCE(l.cost_centre, '') AS cc,
                   SUM(CASE WHEN a.account_type = 'EXPENSE'
                            THEN l.debit - l.credit
                            ELSE l.credit - l.debit END) AS amount
              FROM gl_journal_lines l
              JOIN gl_journal_entries e ON e.id = l.entry_id
              JOIN gl_accounts a ON a.id = l.account_id
             WHERE e.status <> 'DRAFT'
               AND e.entry_date BETWEEN :start AND :end
               AND a.account_type IN ('INCOME','EXPENSE')
               {actual_cc}
             GROUP BY a.code, a.name, a.account_type, cc
        """), aparams)).fetchall():
        actuals[(r.code, r.cc)] = money(r.amount)
        types[r.code] = r.account_type
        names[r.code] = r.name

    # Report every key present in EITHER side. A budget line with no spend is
    # as informative as spend with no budget -- and unbudgeted spend is
    # exactly what a variance report exists to surface.
    for code in set(k[0] for k in budgeted) - set(types):
        row = (await session.execute(
            text("SELECT name, account_type FROM gl_accounts WHERE code = :c"),
            {"c": code})).first()
        if row:
            types[code], names[code] = row.account_type, row.name

    lines = []
    totals = {"budget": Decimal("0.00"), "actual": Decimal("0.00")}
    for key in sorted(set(budgeted) | set(actuals)):
        code, cc = key
        b, a = budgeted.get(key, Decimal("0.00")), actuals.get(key, Decimal("0.00"))
        atype = types.get(code, "EXPENSE")
        variance, verdict = classify_variance(atype, b, a)
        pct = (float(variance / b * 100) if b else None)
        lines.append({
            "account_code": code, "account_name": names.get(code, code),
            "account_type": atype,
            "cost_centre": cc or "UNALLOCATED",
            "budget": float(b), "actual": float(a),
            "variance": float(variance), "variance_percent": pct,
            "verdict": verdict,
            "unbudgeted": b == 0 and a != 0,
        })
        totals["budget"] += b
        totals["actual"] += a

    unbudgeted = [ln for ln in lines if ln["unbudgeted"]]

    return {
        "budget_name": budget.name, "fiscal_year": fiscal_year,
        "period": {"start": str(start), "end": str(end)},
        "lines": lines,
        "total_budget": float(totals["budget"]),
        "total_actual": float(totals["actual"]),
        "adverse_count": sum(1 for ln in lines if ln["verdict"] == "ADVERSE"),
        "unbudgeted_spend": [
            {"account": ln["account_name"], "cost_centre": ln["cost_centre"],
             "amount": ln["actual"]} for ln in unbudgeted],
        "unbudgeted_warning": (
            f"{len(unbudgeted)} account(s) had activity with no budget line."
            if unbudgeted else None),
    }
