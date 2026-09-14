"""The distribution command centre: aggregates, coverage, ranking, exports.

THE DISTINCTION THIS WHOLE MODULE IS BUILT AROUND
=================================================
**Zero is not the same as unknown, and a dashboard is where that difference
usually dies.**

A map showing NGN 0 across most of Nigeria looks like a sales problem. What it
actually means here is that 748 of the country's 774 LGAs have never been loaded,
so the company has no way to record a sale there at all. Those are opposite
findings -- one says sell harder, the other says finish the data entry -- and a
single grey-to-green colour ramp renders them identically.

So every figure in this module carries its own basis:

  * `coverage_map` reports `lgas_loaded` per state and marks a state with none
    as NO_COVERAGE_DATA rather than as zero sales.
  * Territories with no target in force are counted as NOT MEASURABLE, never as
    failing.
  * Verified and reported sales are returned separately, as everywhere else.
  * Untraceable stock is reported as a quantity, not folded into a percentage.

NO NUMBER IS STORED
===================
There is no migration for this phase and no dashboard cache. A cached aggregate
is a copy of a number the system already has, and it is wrong for exactly as
long as nobody notices. These queries are slower than reading a summary table
and they cannot disagree with the data.

EXPORTS ARE LOGGED, IN THE TABLE THAT ALREADY EXISTS
====================================================
Exporting the distributor register takes names, phone numbers and trading
history out of the building on somebody's laptop. That is worth a record, and
`distributor_audit_logs` is already the immutable trail for this domain -- so
the export writes there rather than to a new table of its own.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import performance as perf
from app.services.geography import audit

# Nigeria has 774 local government areas. This is the one national figure worth
# hard-coding, because the gap between it and what is loaded is the single most
# important caveat on every geographic number in this module.
NIGERIA_LGA_TOTAL = 774


def money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# The top-level view
# ---------------------------------------------------------------------------

async def command_centre(
    session: AsyncSession, *, on: Optional[date] = None,
) -> dict:
    """Counts that are counts. No health score, no composite index.

    Every temptation on a screen like this is to produce one number somebody can
    put in a board pack. That number would have to average a compliance failure
    against a good sales month, which is the thing every other module in this
    system refuses to do.
    """
    on = on or date.today()

    distributors = (await session.execute(
        text("""SELECT
                  COUNT(*) FILTER (WHERE status = 'ACTIVE') AS active,
                  COUNT(*) FILTER (WHERE status = 'SUSPENDED') AS suspended,
                  COUNT(*) FILTER (WHERE status IN ('APPLIED','UNDER_REVIEW'))
                      AS in_review,
                  COUNT(*) FILTER (WHERE status = 'TERMINATED') AS terminated
                  FROM distributors"""))).mappings().first()

    territories = (await session.execute(
        text("""
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE t.status = 'ASSIGNED') AS assigned,
                   COUNT(*) FILTER (WHERE t.status = 'AVAILABLE') AS available,
                   COUNT(*) FILTER (WHERE NOT EXISTS (
                       SELECT 1 FROM territory_targets tt
                        WHERE tt.territory_id = t.id
                          AND tt.effective_from <= :on
                          AND (tt.effective_to IS NULL
                               OR tt.effective_to >= :on))) AS without_target
              FROM territories t
             WHERE t.status <> 'RETIRED'
        """), {"on": on})).mappings().first()

    month_start = on.replace(day=1)
    sales = (await session.execute(
        text("""SELECT
                  COALESCE(SUM(total_amount)
                           FILTER (WHERE provenance = 'VERIFIED'), 0) AS verified,
                  COALESCE(SUM(total_amount)
                           FILTER (WHERE provenance = 'REPORTED'), 0) AS reported,
                  COUNT(*) FILTER (WHERE stock_discrepancy) AS discrepancies
                  FROM distributor_sales
                 WHERE sold_on >= :since AND sold_on <= :until"""),
        {"since": month_start, "until": on})).mappings().first()

    compliance = (await session.execute(
        text("""SELECT
                  (SELECT COUNT(*) FROM facility_corrective_actions
                    WHERE status <> 'CLOSED' AND severity = 'CRITICAL')
                      AS critical_actions,
                  (SELECT COUNT(*) FROM distributors d
                    WHERE d.status = 'ACTIVE' AND NOT EXISTS (
                        SELECT 1 FROM distributor_agreements a
                         WHERE a.distributor_id = d.id
                           AND a.status = 'ACTIVE')) AS trading_unsigned,
                  (SELECT COUNT(*) FROM product_batches
                    WHERE status = 'RECALLED') AS recalled_batches,
                  (SELECT COUNT(*) FROM distributor_performance_reviews
                    WHERE status IN ('OPEN','IN_PROGRESS')) AS open_reviews
        """))).mappings().first()

    coverage = await coverage_summary(session)

    return {
        "as_at": str(on),
        "distributors": dict(distributors),
        "territories": {
            **dict(territories),
            # Never counted as failing. A territory with no target is not a
            # territory doing badly; it is one nobody has set a target for.
            "not_measurable": territories["without_target"],
            "not_measurable_note": (
                "These have no target in force, so nothing about their "
                "performance can be stated either way."),
        },
        "this_month": {
            "verified_amount": str(money(sales["verified"])),
            "reported_amount": str(money(sales["reported"])),
            "stock_discrepancies": sales["discrepancies"],
            "note": ("Verified is what somebody checked against evidence. "
                     "Reported is what distributors claim. They are not added "
                     "together."),
        },
        "attention": dict(compliance),
        "coverage": coverage,
        "note": ("There is deliberately no overall health score here. Producing "
                 "one would mean averaging a compliance failure against a good "
                 "sales month, and the two are not commensurable."),
    }


# ---------------------------------------------------------------------------
# Coverage, and what is simply not known
# ---------------------------------------------------------------------------

async def coverage_summary(session: AsyncSession) -> dict:
    row = (await session.execute(
        text("""SELECT
                  (SELECT COUNT(*) FROM lgas) AS lgas_loaded,
                  (SELECT COUNT(DISTINCT state_id) FROM lgas) AS states_with_lgas,
                  (SELECT COUNT(*) FROM states) AS states_total,
                  (SELECT COUNT(DISTINCT lga_id) FROM territory_lgas)
                      AS lgas_in_a_territory
        """))).mappings().first()

    loaded = row["lgas_loaded"]
    return {
        "lgas_loaded": loaded,
        "lgas_nationally": NIGERIA_LGA_TOTAL,
        "lgas_not_loaded": NIGERIA_LGA_TOTAL - loaded,
        "lgas_in_a_territory": row["lgas_in_a_territory"],
        "states_with_any_lga": row["states_with_lgas"],
        "states_total": row["states_total"],
        # The caveat that governs every geographic figure in this module.
        "caveat": (
            f"{NIGERIA_LGA_TOTAL - loaded} of Nigeria's {NIGERIA_LGA_TOTAL} "
            f"LGAs have not been loaded. A sale cannot be recorded against an "
            f"LGA that does not exist in the system, so anywhere outside the "
            f"{loaded} loaded is NOT KNOWN rather than zero. Treat every map "
            f"and regional total accordingly."
            if loaded < NIGERIA_LGA_TOTAL else
            "All of Nigeria's LGAs are loaded."),
    }


async def coverage_map(
    session: AsyncSession, *, since: Optional[date] = None,
    until: Optional[date] = None,
) -> dict:
    """Per state: coverage, distributors, and verified sales.

    A state with no LGAs loaded is returned with `status = 'NO_COVERAGE_DATA'`
    and NULL sales -- not zero. The caller must render those differently, or the
    map says "nobody is selling in Kano" when the truth is "Kano's LGAs were
    never entered".
    """
    until = until or date.today()
    since = since or until.replace(day=1)

    rows = (await session.execute(
        text("""
            -- geopolitical_zone is a COLUMN on states (a reporting
            -- dimension, per migration x3456789012w); region_id lives on
            -- territories, not here.
            SELECT s.id, s.code, s.name, s.geopolitical_zone AS region,
                   (SELECT COUNT(*) FROM lgas l WHERE l.state_id = s.id)
                       AS lgas_loaded,
                   (SELECT COUNT(*) FROM territories t
                     WHERE t.state_id = s.id AND t.status <> 'RETIRED')
                       AS territories,
                   (SELECT COUNT(DISTINCT ta.distributor_id)
                      FROM territory_assignments ta
                      JOIN territories t2 ON t2.id = ta.territory_id
                     WHERE t2.state_id = s.id AND ta.assigned_to IS NULL
                       AND ta.status = 'ACTIVE') AS distributors,
                   COALESCE((SELECT SUM(ds.total_amount)
                      FROM distributor_sales ds
                      JOIN territories t3 ON t3.id = ds.territory_id
                     WHERE t3.state_id = s.id
                       AND ds.provenance = 'VERIFIED'
                       AND ds.sold_on BETWEEN :since AND :until), 0)
                       AS verified_amount
              FROM states s
             ORDER BY s.name
        """), {"since": since, "until": until})).mappings().all()

    out = []
    for r in rows:
        if r["lgas_loaded"] == 0:
            status = "NO_COVERAGE_DATA"
            verified = None
        elif r["distributors"] == 0:
            status = "NO_DISTRIBUTOR"
            verified = str(money(r["verified_amount"]))
        else:
            status = "COVERED"
            verified = str(money(r["verified_amount"]))

        out.append({
            "state_id": str(r["id"]), "code": r["code"], "name": r["name"],
            "region": r["region"],
            "lgas_loaded": r["lgas_loaded"],
            "territories": r["territories"],
            "distributors": r["distributors"],
            # NULL, not 0, where nothing could have been recorded.
            "verified_amount": verified,
            "status": status,
        })

    return {
        "period": {"since": str(since), "until": str(until)},
        "states": out,
        "legend": {
            "COVERED": "LGAs loaded and at least one distributor holds ground.",
            "NO_DISTRIBUTOR": ("LGAs are loaded but nobody holds a territory. "
                               "Zero sales here is a real zero."),
            "NO_COVERAGE_DATA": ("No LGAs loaded for this state. Sales are "
                                 "reported as unknown, not as zero -- nothing "
                                 "could have been recorded here."),
        },
        "coverage": await coverage_summary(session),
    }


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

async def ranking(
    session: AsyncSession, *, year: int, month: int,
) -> dict:
    """Distributors for one month, ranked on verified sales.

    Reuses `performance.leaderboard` rather than re-aggregating: a second
    implementation of "how did they do" would eventually disagree with the
    first, and the first is the one the review process uses.
    """
    rows = await perf.leaderboard(session, year=year, month=month)
    measurable = [r for r in rows if r["band"] != "NO_TARGET"]

    return {
        "month": f"{year:04d}-{month:02d}",
        "distributors": rows,
        "ranked_on": "verified_amount",
        "measurable": len(measurable),
        "not_measurable": len(rows) - len(measurable),
        "note": ("Ranked on verified sales only. Distributors with no target "
                 "in force appear at their actual sales value but carry no "
                 "band -- they are not bottom of the table, they are "
                 "unmeasured."),
    }


async def territory_rollup(
    session: AsyncSession, *, year: int, month: int,
) -> dict:
    """Territories with their target and what was verified against it."""
    start = date(year, month, 1)
    end = (date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)) \
        - timedelta(days=1)

    rows = (await session.execute(
        text("""
            SELECT t.id, t.code, t.name, s.name AS state, t.status,
                   d.legal_name AS holder,
                   (SELECT tt.monthly_target FROM territory_targets tt
                     WHERE tt.territory_id = t.id
                       AND tt.effective_from <= :end
                       AND (tt.effective_to IS NULL OR tt.effective_to >= :end)
                     ORDER BY tt.effective_from DESC LIMIT 1) AS target,
                   COALESCE((SELECT SUM(ds.total_amount)
                      FROM distributor_sales ds
                     WHERE ds.territory_id = t.id
                       AND ds.provenance = 'VERIFIED'
                       AND ds.sold_on BETWEEN :start AND :end), 0) AS verified
              FROM territories t
              LEFT JOIN states s ON s.id = t.state_id
              LEFT JOIN territory_assignments ta ON ta.territory_id = t.id
                   AND ta.assigned_to IS NULL AND ta.status = 'ACTIVE'
              LEFT JOIN distributors d ON d.id = ta.distributor_id
             WHERE t.status <> 'RETIRED'
             ORDER BY s.name, t.code
        """), {"start": start, "end": end})).mappings().all()

    out = []
    for r in rows:
        target = money(r["target"]) if r["target"] is not None else None
        verified = money(r["verified"])
        achievement = (None if not target or target <= 0
                       else (verified / target * 100).quantize(Decimal("0.01")))
        out.append({
            "territory_id": str(r["id"]), "code": r["code"], "name": r["name"],
            "state": r["state"], "status": r["status"], "holder": r["holder"],
            "target_amount": str(target) if target is not None else None,
            "verified_amount": str(verified),
            "achievement_pct": str(achievement) if achievement is not None else None,
            "band": perf.band_for(achievement),
        })

    return {"month": f"{year:04d}-{month:02d}", "territories": out}


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

EXPORTS = {
    "distributors": "The distributor register, with contact details.",
    "territories": "Territories, their targets and who holds them.",
    "downstream_sales": "Reported and verified downstream sales.",
    "batches": "Batches with quantity on hand and status.",
}


async def export_csv(
    session: AsyncSession, *, dataset: str, year: Optional[int] = None,
    month: Optional[int] = None, actor=None, ip_address: str = "",
    user_agent: str = "",
) -> tuple:
    """Produce a CSV, and record that it left the building.

    PROVENANCE COLUMNS TRAVEL WITH THE DATA. A spreadsheet is where a verified
    figure and a claimed one become the same column, and once it is in somebody
    else's hands nothing here can put the distinction back. So the sales export
    has separate verified and reported columns rather than one total, and the
    batch export carries the status that says whether the stock may be sold.
    """
    if dataset not in EXPORTS:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=404,
            detail=f"Unknown export {dataset!r}. Available: "
                   f"{', '.join(sorted(EXPORTS))}.")

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")

    if dataset == "distributors":
        writer.writerow([
            "distributor_code", "legal_name", "trading_name", "status",
            "tier", "phone", "email", "state", "lga", "customer_code",
            "warehouse_code", "territories_held", "agreement_in_force"])
        rows = (await session.execute(
            text("""
                SELECT d.distributor_code, d.legal_name, d.trading_name,
                       d.status, d.tier, d.phone, d.email,
                       s.name AS state, l.name AS lga,
                       c.customer_code, w.code AS warehouse_code,
                       (SELECT COUNT(*) FROM territory_assignments ta
                         WHERE ta.distributor_id = d.id
                           AND ta.assigned_to IS NULL
                           AND ta.status = 'ACTIVE') AS territories_held,
                       EXISTS (SELECT 1 FROM distributor_agreements a
                                WHERE a.distributor_id = d.id
                                  AND a.status = 'ACTIVE') AS agreement
                  FROM distributors d
                  LEFT JOIN states s ON s.id = d.state_id
                  LEFT JOIN lgas l ON l.id = d.lga_id
                  LEFT JOIN customers c ON c.id = d.customer_id
                  LEFT JOIN warehouses w ON w.id = d.warehouse_id
                 ORDER BY d.legal_name"""))).mappings().all()
        for r in rows:
            writer.writerow([
                r["distributor_code"], r["legal_name"], r["trading_name"] or "",
                r["status"], r["tier"], r["phone"] or "", r["email"] or "",
                r["state"] or "", r["lga"] or "", r["customer_code"] or "",
                r["warehouse_code"] or "", r["territories_held"],
                "yes" if r["agreement"] else "NO"])

    elif dataset == "territories":
        writer.writerow([
            "code", "name", "state", "status", "exclusive", "lgas_covered",
            "holder", "held_since", "monthly_target"])
        rows = (await session.execute(
            text("""
                SELECT t.code, t.name, s.name AS state, t.status,
                       t.is_exclusive,
                       (SELECT COUNT(*) FROM territory_lgas tl
                         WHERE tl.territory_id = t.id) AS lgas,
                       d.legal_name AS holder, ta.assigned_from,
                       (SELECT tt.monthly_target FROM territory_targets tt
                         WHERE tt.territory_id = t.id
                           AND tt.effective_to IS NULL
                         LIMIT 1) AS target
                  FROM territories t
                  LEFT JOIN states s ON s.id = t.state_id
                  LEFT JOIN territory_assignments ta ON ta.territory_id = t.id
                       AND ta.assigned_to IS NULL AND ta.status = 'ACTIVE'
                  LEFT JOIN distributors d ON d.id = ta.distributor_id
                 WHERE t.status <> 'RETIRED'
                 ORDER BY s.name, t.code"""))).mappings().all()
        for r in rows:
            writer.writerow([
                r["code"], r["name"], r["state"] or "", r["status"],
                "yes" if r["is_exclusive"] else "no", r["lgas"],
                r["holder"] or "", r["assigned_from"] or "",
                r["target"] if r["target"] is not None else ""])

    elif dataset == "downstream_sales":
        # Two amount columns on purpose. One "total" column is how a claim
        # becomes a fact the moment it reaches a spreadsheet.
        writer.writerow([
            "sale_reference", "sold_on", "distributor_code", "distributor",
            "territory", "outlet", "marketer", "provenance",
            "verified_amount", "reported_amount", "stock_discrepancy",
            "verified_by"])
        clause = ""
        params: dict = {}
        if year and month:
            start = date(year, month, 1)
            end = (date(year + 1, 1, 1) if month == 12
                   else date(year, month + 1, 1)) - timedelta(days=1)
            clause = "WHERE ds.sold_on BETWEEN :start AND :end"
            params = {"start": start, "end": end}
        rows = (await session.execute(
            text(f"""
                SELECT ds.sale_reference, ds.sold_on, ds.provenance,
                       ds.total_amount, ds.stock_discrepancy,
                       d.distributor_code, d.legal_name,
                       t.code AS territory, o.name AS outlet,
                       m.full_name AS marketer, u.full_name AS verified_by
                  FROM distributor_sales ds
                  JOIN distributors d ON d.id = ds.distributor_id
                  LEFT JOIN territories t ON t.id = ds.territory_id
                  LEFT JOIN distributor_outlets o ON o.id = ds.outlet_id
                  LEFT JOIN distributor_marketers m ON m.id = ds.marketer_id
                  LEFT JOIN users u ON u.id = ds.verified_by
                  {clause}
                 ORDER BY ds.sold_on DESC"""), params)).mappings().all()
        for r in rows:
            verified = (r["total_amount"] if r["provenance"] == "VERIFIED"
                        else "")
            reported = (r["total_amount"] if r["provenance"] == "REPORTED"
                        else "")
            writer.writerow([
                r["sale_reference"], r["sold_on"], r["distributor_code"],
                r["legal_name"], r["territory"] or "", r["outlet"] or "",
                r["marketer"] or "", r["provenance"], verified, reported,
                "YES" if r["stock_discrepancy"] else "", r["verified_by"] or ""])

    else:  # batches
        writer.writerow([
            "batch_number", "product", "sku", "status", "expiry_date",
            "manufactured_on", "origin", "quantity_on_hand", "dispatchable"])
        rows = (await session.execute(
            text("""
                SELECT b.batch_number, p.name AS product, p.sku, b.status,
                       b.expiry_date, b.manufactured_on, b.origin,
                       COALESCE(SUM(CASE WHEN sm.movement_type IN
                           ('IN','RETURN','TRANSFER_IN','PRODUCTION_IN',
                            'DAMAGE_TRANSFER_IN','ADJUST_IN')
                           THEN sm.quantity ELSE -sm.quantity END), 0) AS on_hand
                  FROM product_batches b
                  JOIN products p ON p.id = b.product_id
                  LEFT JOIN stock_movements sm ON sm.batch_id = b.id
                 GROUP BY b.id, b.batch_number, p.name, p.sku, b.status,
                          b.expiry_date, b.manufactured_on, b.origin
                 ORDER BY b.expiry_date NULLS LAST"""))).mappings().all()
        for r in rows:
            dispatchable = (
                r["status"] == "AVAILABLE"
                and (r["expiry_date"] is None or r["expiry_date"] >= date.today()))
            writer.writerow([
                r["batch_number"], r["product"], r["sku"], r["status"],
                r["expiry_date"] or "", r["manufactured_on"] or "",
                r["origin"], r["on_hand"], "yes" if dispatchable else "NO"])

    content = buffer.getvalue()
    line_count = max(0, content.count("\n") - 1)

    # Contact details and trading history are leaving on somebody's laptop.
    # distributor_audit_logs is already this domain's immutable trail, so the
    # record goes there rather than into a table of its own.
    await audit(session, event_type="DATA_EXPORTED",
                entity_type="export", actor=actor,
                new_value={"dataset": dataset, "rows": line_count,
                           "period": (f"{year:04d}-{month:02d}"
                                      if year and month else "all")},
                ip_address=ip_address, user_agent=user_agent)

    filename = (f"{dataset}_{year:04d}-{month:02d}.csv" if year and month
                else f"{dataset}_{date.today():%Y%m%d}.csv")
    return content, filename, line_count
