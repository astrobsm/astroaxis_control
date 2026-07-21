"""Fixed asset register and depreciation.

Depreciation here is a periodic EVENT, recorded once and never recomputed.
The previous implementation derived a figure on every read from fractional
years elapsed, which meant the answer changed depending on when you asked and
there was nothing to post to the ledger or defend in an audit.

Two rules govern every calculation:

  * an asset is never depreciated below its residual value -- the residual is
    what it is expected to be worth at the end of its life, not zero;
  * the final period's charge is whatever remains, not the formula amount.
    Rounding across dozens of periods otherwise leaves a few kobo stranded,
    and an asset that never quite reaches its residual value never closes.
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
MONTHS_PER_YEAR = Decimal("12")

STRAIGHT_LINE = "STRAIGHT_LINE"
REDUCING_BALANCE = "REDUCING_BALANCE"


def money(value) -> Decimal:
    if value is None:
        return Decimal("0.00")
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


def month_end(d: date) -> date:
    if d.month == 12:
        return date(d.year, 12, 31)
    return date(d.year, d.month + 1, 1).fromordinal(
        date(d.year, d.month + 1, 1).toordinal() - 1)


def monthly_charge(
    *,
    method: str,
    cost: Decimal,
    residual: Decimal,
    useful_life_months: int,
    annual_rate_percent: Optional[Decimal],
    accumulated: Decimal,
    periods_charged: int = 0,
) -> Decimal:
    """The charge for one month, floored so carrying never dips below residual.

    `accumulated` is depreciation already charged. `periods_charged` is how
    many periods have been charged so far, which is what identifies the FINAL
    period of a straight-line schedule -- see the stub-sweep note below.
    """
    cost = money(cost)
    residual = money(residual)
    accumulated = money(accumulated)

    depreciable = money(cost - residual)
    remaining = money(depreciable - accumulated)
    if remaining <= 0:
        return Decimal("0.00")

    if method == STRAIGHT_LINE:
        charge = money(depreciable / Decimal(useful_life_months))
    elif method == REDUCING_BALANCE:
        if not annual_rate_percent or Decimal(str(annual_rate_percent)) <= 0:
            raise HTTPException(
                status_code=400,
                detail="Reducing-balance depreciation requires an annual rate.")
        carrying = money(cost - accumulated)
        monthly_rate = (Decimal(str(annual_rate_percent)) / Decimal("100")
                        / MONTHS_PER_YEAR)
        charge = money(carrying * monthly_rate)
    else:
        raise HTTPException(
            status_code=400, detail=f"Unknown depreciation method {method!r}.")

    # Never overshoot the residual.
    if charge >= remaining:
        return remaining

    # Sweep the final stub. A formula charge that does not divide evenly
    # leaves a remainder: 1,000,000 over 3 months rounds to 333,333.33 each
    # time, and three of those is 999,999.99 -- one kobo short, forever. The
    # asset would never reach its residual value and never close.
    #
    # The sweep must fire on the LAST scheduled period and no earlier. An
    # earlier heuristic ("sweep when what remains is less than one charge")
    # fired a period early whenever rounding made the penultimate remainder
    # slightly under two charges -- a 60-month asset finished in 59, pulling
    # the final month's expense into the wrong period.
    if method == STRAIGHT_LINE:
        if periods_charged >= useful_life_months - 1:
            return remaining
        return charge

    # Reducing balance approaches the residual asymptotically and never
    # actually reaches it, so it needs a termination rule rather than a
    # period count: once one more charge would leave less than a further
    # charge behind, take the remainder and close the schedule.
    if remaining - charge < charge:
        return remaining
    return charge


async def accumulated_depreciation(
    session: AsyncSession, *, asset_id: UUID) -> Decimal:
    """Total charged to date, from the recorded charges."""
    return money((await session.execute(
        text("""SELECT COALESCE(SUM(amount), 0)
                  FROM asset_depreciation_charges WHERE asset_id = :a"""),
        {"a": str(asset_id)},
    )).scalar())


async def carrying_amount(
    session: AsyncSession, *, asset_id: UUID) -> Decimal:
    row = (await session.execute(
        text("SELECT cost FROM fixed_assets WHERE id = :a"),
        {"a": str(asset_id)},
    )).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Asset not found.")
    return money(Decimal(str(row.cost))
                 - await accumulated_depreciation(session, asset_id=asset_id))


# ---------------------------------------------------------------------------
# Depreciation run
# ---------------------------------------------------------------------------

async def run_depreciation(
    session: AsyncSession,
    *,
    period_start: date,
    period_end: date,
    created_by: Optional[UUID] = None,
) -> dict:
    """Charge one period's depreciation across every eligible asset.

    Idempotent by construction: the unique constraint on
    (asset_id, period_start) means a retried or duplicated run cannot
    double-charge a period.
    """
    if period_end < period_start:
        raise HTTPException(
            status_code=400, detail="period_end cannot precede period_start.")

    existing = (await session.execute(
        text("""SELECT run_number, status FROM depreciation_runs
                 WHERE period_start = :s AND period_end = :e"""),
        {"s": period_start, "e": period_end},
    )).first()
    if existing and existing.status == 'POSTED':
        raise HTTPException(
            status_code=400,
            detail=(f"Depreciation for this period has already been posted "
                    f"({existing.run_number})."))

    assets = (await session.execute(
        text("""
            SELECT id, asset_number, name, cost, residual_value,
                   useful_life_months, method, annual_rate_percent,
                   acquisition_date, expense_account, accumulated_account,
                   cost_centre
              FROM fixed_assets
             WHERE status = 'ACTIVE'
               -- Depreciation starts in the month of acquisition and stops
               -- at disposal. An asset bought after the period ended has
               -- nothing to charge yet.
               AND acquisition_date <= :pe
               AND (disposal_date IS NULL OR disposal_date > :pe)
             ORDER BY asset_number
        """),
        {"pe": period_end},
    )).fetchall()

    run_id = uuid4()
    run_number = f"DEP-{period_start.strftime('%Y%m')}-{uuid4().hex[:6].upper()}"

    charges = []
    total = Decimal("0.00")
    by_account = {}

    for a in assets:
        accumulated = await accumulated_depreciation(session, asset_id=a.id)
        # Count of periods already charged, so the engine knows when it has
        # reached the final scheduled period and should sweep the remainder.
        periods_charged = (await session.execute(
            text("""SELECT COUNT(*) FROM asset_depreciation_charges
                     WHERE asset_id = :a"""),
            {"a": str(a.id)},
        )).scalar() or 0

        charge = monthly_charge(
            method=a.method,
            cost=Decimal(str(a.cost)),
            residual=Decimal(str(a.residual_value)),
            useful_life_months=a.useful_life_months,
            annual_rate_percent=a.annual_rate_percent,
            accumulated=accumulated,
            periods_charged=periods_charged,
        )
        if charge <= 0:
            # Fully depreciated: mark it so, and stop considering it.
            await session.execute(
                text("""UPDATE fixed_assets SET status = 'FULLY_DEPRECIATED'
                         WHERE id = :a AND status = 'ACTIVE'"""),
                {"a": str(a.id)},
            )
            continue

        opening = money(Decimal(str(a.cost)) - accumulated)
        closing = money(opening - charge)

        await session.execute(
            text("""
                INSERT INTO asset_depreciation_charges
                    (asset_id, period_start, period_end, amount,
                     opening_carrying_amount, closing_carrying_amount, run_id)
                VALUES (:a, :ps, :pe, :amt, :open, :close, :run)
                ON CONFLICT (asset_id, period_start) DO NOTHING
            """),
            {"a": str(a.id), "ps": period_start, "pe": period_end,
             "amt": str(charge), "open": str(opening), "close": str(closing),
             "run": str(run_id)},
        )

        total += charge
        key = (a.expense_account, a.accumulated_account, a.cost_centre)
        by_account[key] = by_account.get(key, Decimal("0.00")) + charge
        charges.append({
            "asset_number": a.asset_number, "name": a.name,
            "charge": charge, "closing_carrying_amount": closing,
        })

    entry_id = None
    if total > 0:
        from app.services.ledger import Line, post_entry
        lines = []
        for (expense_acct, accum_acct, cost_centre), amount in by_account.items():
            lines.append(Line(expense_acct, debit=amount,
                              description="Depreciation charge",
                              cost_centre=cost_centre))
            lines.append(Line(accum_acct, credit=amount,
                              description="Accumulated depreciation",
                              cost_centre=cost_centre))
        entry_id = await post_entry(
            session,
            entry_date=period_end,
            description=f"Depreciation {run_number}",
            source_module="assets.depreciation",
            source_reference=run_number,
            lines=lines,
            created_by=created_by,
        )

    await session.execute(
        text("""
            INSERT INTO depreciation_runs
                (id, run_number, period_start, period_end, asset_count,
                 total_charge, journal_entry_id, created_by)
            VALUES (:id, :num, :ps, :pe, :n, :t, :je, :by)
            ON CONFLICT (period_start, period_end) DO UPDATE
                SET status = 'POSTED', asset_count = EXCLUDED.asset_count,
                    total_charge = EXCLUDED.total_charge,
                    journal_entry_id = EXCLUDED.journal_entry_id
        """),
        {"id": str(run_id), "num": run_number, "ps": period_start,
         "pe": period_end, "n": len(charges), "t": str(total),
         "je": str(entry_id) if entry_id else None,
         "by": str(created_by) if created_by else None},
    )

    return {
        "run_id": run_id, "run_number": run_number,
        "asset_count": len(charges), "total_charge": total,
        "journal_entry_id": entry_id, "charges": charges,
    }


# ---------------------------------------------------------------------------
# Disposal
# ---------------------------------------------------------------------------

async def dispose_asset(
    session: AsyncSession,
    *,
    asset_id: UUID,
    disposal_date: date,
    proceeds,
    notes: Optional[str] = None,
    created_by: Optional[UUID] = None,
) -> dict:
    """Remove an asset from the books, recognising any gain or loss.

    The journal removes BOTH the original cost and the accumulated
    depreciation, because carrying either forward misstates the balance
    sheet. The difference between proceeds and carrying amount is a gain or
    a loss, and must be recognised rather than absorbed:

        Dr Bank                       (proceeds)
        Dr Accumulated Depreciation   (reverse what was charged)
        Cr Asset at cost              (remove the original cost)
        Dr/Cr Loss or Gain on disposal
    """
    asset = (await session.execute(
        text("""SELECT id, asset_number, name, cost, status, asset_account,
                       accumulated_account
                  FROM fixed_assets WHERE id = :a FOR UPDATE"""),
        {"a": str(asset_id)},
    )).first()
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found.")
    if asset.status in ('DISPOSED', 'WRITTEN_OFF'):
        raise HTTPException(
            status_code=400,
            detail=f"{asset.asset_number} has already been disposed of.")

    proceeds = money(proceeds)
    if proceeds < 0:
        raise HTTPException(
            status_code=400, detail="Disposal proceeds cannot be negative.")

    cost = money(asset.cost)
    accumulated = await accumulated_depreciation(session, asset_id=asset_id)
    carrying = money(cost - accumulated)
    gain_or_loss = money(proceeds - carrying)

    from app.services.ledger import Line, post_entry
    lines = []
    if proceeds > 0:
        lines.append(Line("1200", debit=proceeds,
                          description=f"Proceeds from {asset.asset_number}"))
    if accumulated > 0:
        lines.append(Line(asset.accumulated_account, debit=accumulated,
                          description="Reverse accumulated depreciation"))
    lines.append(Line(asset.asset_account, credit=cost,
                      description=f"Remove {asset.asset_number} at cost"))

    if gain_or_loss > 0:
        lines.append(Line("4300", credit=gain_or_loss,
                          description="Gain on disposal"))
    elif gain_or_loss < 0:
        lines.append(Line("6800", debit=-gain_or_loss,
                          description="Loss on disposal"))

    entry_id = await post_entry(
        session,
        entry_date=disposal_date,
        description=f"Disposal of {asset.asset_number} - {asset.name}",
        source_module="assets.disposal",
        source_reference=asset.asset_number,
        lines=lines,
        created_by=created_by,
    )

    await session.execute(
        text("""UPDATE fixed_assets
                   SET status = 'DISPOSED', disposal_date = :d,
                       disposal_proceeds = :p, disposal_notes = :n
                 WHERE id = :a"""),
        {"d": disposal_date, "p": str(proceeds), "n": notes,
         "a": str(asset_id)},
    )

    return {
        "asset_number": asset.asset_number,
        "cost": cost, "accumulated_depreciation": accumulated,
        "carrying_amount": carrying, "proceeds": proceeds,
        "gain_or_loss": gain_or_loss,
        "outcome": ("gain" if gain_or_loss > 0
                    else "loss" if gain_or_loss < 0 else "break-even"),
        "journal_entry_id": entry_id,
    }


async def asset_register(
    session: AsyncSession, *, include_disposed: bool = False) -> dict:
    """The register, with carrying amounts derived from recorded charges."""
    where = "" if include_disposed else "WHERE fa.status <> 'DISPOSED'"
    rows = (await session.execute(
        text(f"""
            SELECT fa.id, fa.asset_number, fa.name, fa.category, fa.status,
                   fa.acquisition_date, fa.cost, fa.residual_value,
                   fa.useful_life_months, fa.method, fa.location,
                   COALESCE((SELECT SUM(c.amount)
                               FROM asset_depreciation_charges c
                              WHERE c.asset_id = fa.id), 0) AS accumulated
              FROM fixed_assets fa {where}
             ORDER BY fa.asset_number
        """)
    )).fetchall()

    assets, total_cost, total_accum = [], Decimal("0.00"), Decimal("0.00")
    for r in rows:
        cost = money(r.cost)
        accum = money(r.accumulated)
        assets.append({
            "id": str(r.id), "asset_number": r.asset_number, "name": r.name,
            "category": r.category, "status": r.status,
            "acquisition_date": str(r.acquisition_date),
            "cost": float(cost), "residual_value": float(money(r.residual_value)),
            "useful_life_months": r.useful_life_months, "method": r.method,
            "location": r.location,
            "accumulated_depreciation": float(accum),
            "carrying_amount": float(money(cost - accum)),
        })
        if r.status != 'DISPOSED':
            total_cost += cost
            total_accum += accum

    return {
        "assets": assets,
        "total_cost": float(total_cost),
        "total_accumulated_depreciation": float(total_accum),
        "total_carrying_amount": float(money(total_cost - total_accum)),
    }
