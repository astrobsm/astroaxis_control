"""What distributors sold onward, and how well that is actually known.

THE RULE THIS MODULE EXISTS TO ENFORCE
======================================
A self-reported figure is never returned as a fact. Every function that adds
these numbers up returns the REPORTED and VERIFIED totals separately, and there
is deliberately no helper that returns one combined figure -- because the moment
such a helper exists, some screen will call it and "the distributor claims" will
have quietly become "the company knows".

That is the same discipline the call log applies to durations and the wallet
applies to receipts, and it matters more here: these numbers drive performance
reviews, territory decisions and who keeps a distributorship.

NO JOURNAL ENTRY, EVER
======================
This module imports nothing from `app.services.ledger`, and that is load-bearing
rather than incidental. `sales_orders` records company -> customer; a distributor
selling to a pharmacy is a transaction the company is not party to and already
recognised revenue on when it shipped to the distributor. Posting anything here
would double-count revenue in a live general ledger -- `ACCOUNTING_POSTING_ENABLED`
is true in production.

STOCK, AND WHAT HAPPENS WHEN THE NUMBERS DISAGREE
=================================================
A downstream sale reduces stock in the DISTRIBUTOR's warehouse only, through
`inventory.apply_stock_movement` like every other stock change, carrying the
batch so a recall can reach the pharmacy that bought it.

When a distributor reports selling more than the company recorded shipping, the
sale is STILL RECORDED and `stock_discrepancy` is set. Refusing the report would
discard a fact about the world in order to protect a number, and the mismatch is
itself the useful finding -- either the shipment record is incomplete or the
sales report is inflated, and both are worth knowing. What is not done is
quietly driving the warehouse negative to make the books balance.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import inventory as inv
from app.services.geography import audit

ALLOWED_EVIDENCE_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif",
    "application/pdf",
}
OUTLET_TYPES = ("HOSPITAL", "PHARMACY", "CLINIC", "RETAILER", "WHOLESALER",
                "OTHER")


def money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"),
                                             rounding=ROUND_HALF_UP)


def _reference() -> str:
    return f"DS-{date.today():%Y%m}-{secrets.token_hex(3).upper()}"


# ---------------------------------------------------------------------------
# Marketers and outlets
# ---------------------------------------------------------------------------

async def add_marketer(
    session: AsyncSession, *, distributor_id: UUID, full_name: str,
    phone: Optional[str] = None, employee_reference: Optional[str] = None,
    territory_id: Optional[UUID] = None, actor=None,
) -> dict:
    """Record one of the distributor's field staff.

    They work for the distributor. No `users` row is created, no login, no
    access to anything -- naming someone here must never become a way to grant
    a person access to the company's systems.
    """
    if not full_name or len(full_name.strip()) < 2:
        raise HTTPException(status_code=400, detail="A name is required.")

    exists = (await session.execute(
        text("SELECT 1 FROM distributors WHERE id = :d"),
        {"d": str(distributor_id)})).first()
    if exists is None:
        raise HTTPException(status_code=404, detail="Distributor not found.")

    marketer_id = uuid4()
    await session.execute(
        text("""INSERT INTO distributor_marketers
                    (id, distributor_id, full_name, phone, employee_reference,
                     territory_id, created_by)
                VALUES (:id, :d, :n, :p, :ref, CAST(:t AS uuid), :by)"""),
        {"id": str(marketer_id), "d": str(distributor_id),
         "n": full_name.strip(), "p": phone, "ref": employee_reference,
         "t": str(territory_id) if territory_id else None,
         "by": str(actor.id) if actor else None},
    )
    return {"id": str(marketer_id), "full_name": full_name.strip()}


async def end_marketer(
    session: AsyncSession, *, marketer_id: UUID, reason: str, actor=None,
) -> dict:
    """Mark a marketer as no longer working for the distributor.

    Never deleted: sales they reported stay attached to them, which is the
    point of recording who reported what.
    """
    if not reason or len(reason.strip()) < 3:
        raise HTTPException(status_code=400, detail="A reason is required.")
    result = await session.execute(
        text("""UPDATE distributor_marketers
                   SET is_active = FALSE, ended_on = CURRENT_DATE,
                       end_reason = :r
                 WHERE id = :m AND is_active"""),
        {"r": reason.strip(), "m": str(marketer_id)})
    if result.rowcount == 0:
        raise HTTPException(
            status_code=404, detail="No active marketer with that id.")
    return {"id": str(marketer_id), "is_active": False}


async def list_marketers(
    session: AsyncSession, *, distributor_id: UUID, include_former: bool = False,
) -> list[dict]:
    clause = "" if include_former else "AND m.is_active"
    rows = (await session.execute(
        text(f"""SELECT m.id, m.full_name, m.phone, m.employee_reference,
                        m.is_active, m.ended_on, m.end_reason,
                        t.code AS territory,
                        (SELECT COUNT(*) FROM distributor_sales s
                          WHERE s.marketer_id = m.id) AS sales_reported
                   FROM distributor_marketers m
                   LEFT JOIN territories t ON t.id = m.territory_id
                  WHERE m.distributor_id = :d {clause}
                  ORDER BY m.is_active DESC, m.full_name"""),
        {"d": str(distributor_id)})).mappings().all()
    return [dict(r) | {"id": str(r["id"])} for r in rows]


async def add_outlet(
    session: AsyncSession, *, distributor_id: UUID, name: str,
    outlet_type: str = "OTHER", phone: Optional[str] = None,
    address: Optional[str] = None, state_id: Optional[UUID] = None,
    lga_id: Optional[UUID] = None, town: Optional[str] = None, actor=None,
) -> dict:
    if outlet_type not in OUTLET_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Outlet type must be one of: {', '.join(OUTLET_TYPES)}.")
    if not name or len(name.strip()) < 2:
        raise HTTPException(status_code=400, detail="A name is required.")

    clash = (await session.execute(
        text("""SELECT id FROM distributor_outlets
                 WHERE distributor_id = :d AND name = :n"""),
        {"d": str(distributor_id), "n": name.strip()})).first()
    if clash:
        return {"id": str(clash.id), "name": name.strip(), "existing": True}

    outlet_id = uuid4()
    await session.execute(
        text("""INSERT INTO distributor_outlets
                    (id, distributor_id, name, outlet_type, phone, address,
                     state_id, lga_id, town, created_by)
                VALUES (:id, :d, :n, :t, :p, :a, CAST(:s AS uuid),
                        CAST(:l AS uuid), :town, :by)"""),
        {"id": str(outlet_id), "d": str(distributor_id), "n": name.strip(),
         "t": outlet_type, "p": phone, "a": address,
         "s": str(state_id) if state_id else None,
         "l": str(lga_id) if lga_id else None, "town": town,
         "by": str(actor.id) if actor else None},
    )
    return {"id": str(outlet_id), "name": name.strip(), "existing": False}


async def list_outlets(
    session: AsyncSession, *, distributor_id: UUID,
) -> list[dict]:
    rows = (await session.execute(
        text("""SELECT o.id, o.name, o.outlet_type, o.phone, o.town,
                       l.name AS lga, s.name AS state,
                       (SELECT COUNT(*) FROM distributor_sales ds
                         WHERE ds.outlet_id = o.id) AS purchases
                  FROM distributor_outlets o
                  LEFT JOIN lgas l ON l.id = o.lga_id
                  LEFT JOIN states s ON s.id = o.state_id
                 WHERE o.distributor_id = :d
                 ORDER BY o.name"""),
        {"d": str(distributor_id)})).mappings().all()
    return [dict(r) | {"id": str(r["id"])} for r in rows]


# ---------------------------------------------------------------------------
# Recording a sale
# ---------------------------------------------------------------------------

async def record_sale(
    session: AsyncSession, *, distributor_id: UUID, sold_on: date, lines: list,
    marketer_id: Optional[UUID] = None, outlet_id: Optional[UUID] = None,
    territory_id: Optional[UUID] = None, notes: Optional[str] = None,
    actor=None,
) -> dict:
    """Record a sale the distributor says they made. REPORTED, nothing more.

    It arrives as a claim and is stored as one. Nothing here marks it verified,
    and no argument from the caller can: verification is a separate act by a
    different person with evidence attached.
    """
    if not lines:
        raise HTTPException(
            status_code=400, detail="A sale needs at least one line.")
    if sold_on > date.today():
        raise HTTPException(
            status_code=400,
            detail="A sale cannot be dated in the future.")

    dist = (await session.execute(
        text("""SELECT id, distributor_code, legal_name, status, warehouse_id
                  FROM distributors WHERE id = :d"""),
        {"d": str(distributor_id)})).mappings().first()
    if dist is None:
        raise HTTPException(status_code=404, detail="Distributor not found.")

    sale_id = uuid4()
    reference = _reference()

    await session.execute(
        text("""
            INSERT INTO distributor_sales
                (id, sale_reference, distributor_id, marketer_id, outlet_id,
                 territory_id, sold_on, total_amount, provenance, reported_by,
                 reported_by_label, notes)
            VALUES (:id, :ref, :d, CAST(:m AS uuid), CAST(:o AS uuid),
                    CAST(:t AS uuid), :on, 0, 'REPORTED', :by, :label, :notes)
        """),
        {"id": str(sale_id), "ref": reference, "d": str(distributor_id),
         "m": str(marketer_id) if marketer_id else None,
         "o": str(outlet_id) if outlet_id else None,
         "t": str(territory_id) if territory_id else None,
         "on": sold_on, "by": str(actor.id) if actor else None,
         "label": getattr(actor, "full_name", None), "notes": notes},
    )

    total = Decimal("0")
    shortfalls: list[str] = []

    for line in lines:
        product = (await session.execute(
            text("SELECT id, name FROM products WHERE id = :p"),
            {"p": str(line.product_id)})).mappings().first()
        if product is None:
            raise HTTPException(
                status_code=404,
                detail=f"Product not found: {line.product_id}")

        quantity = Decimal(str(line.quantity))
        if quantity <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"Quantity for {product['name']} must be positive.")

        unit_price = (Decimal(str(line.unit_price))
                      if getattr(line, "unit_price", None) is not None else None)
        line_total = money(unit_price * quantity) if unit_price is not None else None
        if line_total is not None:
            total += line_total

        movement_id = None
        if dist["warehouse_id"]:
            # Only the distributor's own warehouse. Company stock left when the
            # goods were shipped to them.
            #
            # INSIDE A SAVEPOINT. The movement is allowed to fail -- that is
            # the discrepancy case below -- and a failed statement poisons the
            # whole PostgreSQL transaction, which would silently roll back the
            # sale we are in the middle of writing. The portal's credit lookup
            # was bitten by exactly this in phase 5.
            try:
                async with session.begin_nested():
                    movement_id = await inv.apply_stock_movement(
                        session, warehouse_id=dist["warehouse_id"],
                        movement_type="OUT", quantity=quantity,
                        product_id=product["id"],
                        batch_id=getattr(line, "batch_id", None),
                        reference=reference,
                        notes=(f"Downstream sale reported by "
                               f"{dist['distributor_code']}"),
                        created_by=getattr(actor, "id", None))
            except HTTPException as exc:
                # The distributor says this sale happened. Recording the claim
                # and flagging the mismatch beats discarding a fact about the
                # world to protect a number -- and beats driving the warehouse
                # negative to make it balance.
                movement_id = None
                shortfalls.append(f"{product['name']}: {exc.detail}")
            except Exception as exc:  # noqa: BLE001 - reported, never hidden
                movement_id = None
                shortfalls.append(
                    f"{product['name']}: stock not adjusted "
                    f"({type(exc).__name__})")

        await session.execute(
            text("""INSERT INTO distributor_sale_lines
                        (id, sale_id, product_id, batch_id, unit, quantity,
                         unit_price, line_total, stock_movement_id)
                    VALUES (gen_random_uuid(), :s, :p, CAST(:b AS uuid), :u,
                            :q, :price, :lt, CAST(:mv AS uuid))"""),
            {"s": str(sale_id), "p": str(product["id"]),
             "b": (str(line.batch_id)
                   if getattr(line, "batch_id", None) else None),
             "u": getattr(line, "unit", None), "q": str(quantity),
             "price": str(unit_price) if unit_price is not None else None,
             "lt": str(line_total) if line_total is not None else None,
             "mv": str(movement_id) if movement_id else None},
        )

    await session.execute(
        text("""UPDATE distributor_sales
                   SET total_amount = :total, stock_discrepancy = :flag,
                       discrepancy_note = :note, updated_at = NOW()
                 WHERE id = :s"""),
        {"total": str(money(total)), "flag": bool(shortfalls),
         "note": ("; ".join(shortfalls)[:2000] if shortfalls else None),
         "s": str(sale_id)},
    )

    await audit(session, event_type="DOWNSTREAM_SALE_REPORTED",
                entity_type="distributor_sale", entity_id=sale_id,
                distributor_id=distributor_id, territory_id=territory_id,
                actor=actor,
                new_value={"reference": reference, "total": str(money(total)),
                           "lines": len(lines),
                           "stock_discrepancy": bool(shortfalls)})

    return {
        "id": str(sale_id),
        "sale_reference": reference,
        # Said plainly in the response, so no caller can mistake it.
        "provenance": "REPORTED",
        "provenance_note": ("Recorded as reported by the distributor. Nobody "
                            "has checked it, and it does not count toward any "
                            "performance figure until somebody does."),
        "total_amount": str(money(total)),
        "line_count": len(lines),
        "stock_discrepancy": bool(shortfalls),
        "discrepancy_note": ("; ".join(shortfalls) if shortfalls else None),
    }


async def attach_evidence(
    session: AsyncSession, *, sale_id: UUID, evidence_type: str, filename: str,
    content_type: str, content: bytes, note: Optional[str] = None, actor=None,
) -> dict:
    if content_type not in ALLOWED_EVIDENCE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(f"{content_type} is not accepted. Upload a photograph or "
                    f"a PDF."))
    if not content:
        raise HTTPException(status_code=400, detail="The file is empty.")
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(
            status_code=400, detail="Evidence must be 10 MB or smaller.")

    sale = (await session.execute(
        text("SELECT id, distributor_id FROM distributor_sales WHERE id = :s"),
        {"s": str(sale_id)})).mappings().first()
    if sale is None:
        raise HTTPException(status_code=404, detail="Sale not found.")

    evidence_id = uuid4()
    await session.execute(
        text("""INSERT INTO distributor_sale_evidence
                    (id, sale_id, evidence_type, filename, content_type,
                     byte_size, sha256, content, note, uploaded_by)
                VALUES (:id, :s, :t, :f, :ct, :size, :sha, :content, :note,
                        :by)"""),
        {"id": str(evidence_id), "s": str(sale_id), "t": evidence_type,
         "f": filename[:255], "ct": content_type, "size": len(content),
         "sha": hashlib.sha256(content).hexdigest(), "content": content,
         "note": note, "by": str(actor.id) if actor else None},
    )
    return {"id": str(evidence_id), "filename": filename,
            "byte_size": len(content)}


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

async def verify_sale(
    session: AsyncSession, *, sale_id: UUID, note: Optional[str] = None,
    actor=None,
) -> dict:
    """Confirm a reported sale against evidence.

    Requires evidence on the record and somebody other than the reporter. A
    verification with nothing behind it and nobody's name on it is a status
    change dressed up as a check -- and this is the flag that decides whether a
    figure counts toward a distributor's performance.
    """
    if actor is None:
        raise HTTPException(
            status_code=403, detail="Verification requires a named user.")

    sale = (await session.execute(
        text("""SELECT id, sale_reference, provenance, reported_by,
                       distributor_id
                  FROM distributor_sales WHERE id = :s FOR UPDATE"""),
        {"s": str(sale_id)})).mappings().first()
    if sale is None:
        raise HTTPException(status_code=404, detail="Sale not found.")
    if sale["provenance"] == "VERIFIED":
        raise HTTPException(
            status_code=400, detail="That sale is already verified.")

    if sale["reported_by"] and str(sale["reported_by"]) == str(actor.id):
        raise HTTPException(
            status_code=403,
            detail=("You reported this sale, so you cannot verify it. "
                    "Verification is a second pair of eyes or it is nothing."))

    evidence = (await session.execute(
        text("""SELECT COUNT(*) FROM distributor_sale_evidence
                 WHERE sale_id = :s"""), {"s": str(sale_id)})).scalar()
    if not evidence:
        raise HTTPException(
            status_code=400,
            detail=("Attach the invoice, receipt or delivery note this was "
                    "checked against first. Verified with nothing behind it "
                    "means the same as reported."))

    await session.execute(
        text("""UPDATE distributor_sales
                   SET provenance = 'VERIFIED', verified_by = :by,
                       verified_at = NOW(), verification_note = :note,
                       updated_at = NOW()
                 WHERE id = :s"""),
        {"by": str(actor.id), "note": note, "s": str(sale_id)})

    await audit(session, event_type="DOWNSTREAM_SALE_VERIFIED",
                entity_type="distributor_sale", entity_id=sale_id,
                distributor_id=sale["distributor_id"], actor=actor, reason=note,
                old_value={"provenance": sale["provenance"]},
                new_value={"provenance": "VERIFIED",
                           "evidence_items": evidence})
    return {"id": str(sale_id), "provenance": "VERIFIED",
            "evidence_items": evidence}


async def dispute_sale(
    session: AsyncSession, *, sale_id: UUID, reason: str, actor=None,
) -> dict:
    """Record that a reported sale was checked and found wrong.

    The row stays. Deleting it would remove the evidence that a claim was made,
    which is exactly what a pattern of inflated reporting looks like.
    """
    if not reason or len(reason.strip()) < 3:
        raise HTTPException(
            status_code=400,
            detail="Say what was found. This is the record of the dispute.")

    sale = (await session.execute(
        text("""SELECT id, provenance, distributor_id
                  FROM distributor_sales WHERE id = :s FOR UPDATE"""),
        {"s": str(sale_id)})).mappings().first()
    if sale is None:
        raise HTTPException(status_code=404, detail="Sale not found.")

    await session.execute(
        text("""UPDATE distributor_sales
                   SET provenance = 'DISPUTED', verification_note = :r,
                       verified_by = :by, verified_at = NOW(),
                       updated_at = NOW()
                 WHERE id = :s"""),
        {"r": reason.strip(), "by": str(actor.id) if actor else None,
         "s": str(sale_id)})
    await audit(session, event_type="DOWNSTREAM_SALE_DISPUTED",
                entity_type="distributor_sale", entity_id=sale_id,
                distributor_id=sale["distributor_id"], actor=actor,
                reason=reason.strip(),
                old_value={"provenance": sale["provenance"]},
                new_value={"provenance": "DISPUTED"})
    return {"id": str(sale_id), "provenance": "DISPUTED"}


# ---------------------------------------------------------------------------
# Reporting -- always split by provenance
# ---------------------------------------------------------------------------

async def sell_through(
    session: AsyncSession, *, distributor_id: Optional[UUID] = None,
    territory_id: Optional[UUID] = None, since: Optional[date] = None,
    until: Optional[date] = None,
) -> dict:
    """Downstream sales, with REPORTED and VERIFIED reported separately.

    There is no combined total in the return value, and adding one would defeat
    the module. `verified_amount` is the only figure that should drive a
    decision about a distributor; `reported_amount` is what they claim.
    """
    clauses, params = ["1 = 1"], {}
    if distributor_id:
        clauses.append("s.distributor_id = :d")
        params["d"] = str(distributor_id)
    if territory_id:
        clauses.append("s.territory_id = :t")
        params["t"] = str(territory_id)
    if since:
        clauses.append("s.sold_on >= :since")
        params["since"] = since
    if until:
        clauses.append("s.sold_on <= :until")
        params["until"] = until

    row = (await session.execute(
        text(f"""
            SELECT
              COUNT(*) FILTER (WHERE s.provenance = 'REPORTED') AS reported_n,
              COUNT(*) FILTER (WHERE s.provenance = 'VERIFIED') AS verified_n,
              COUNT(*) FILTER (WHERE s.provenance = 'DISPUTED') AS disputed_n,
              COALESCE(SUM(s.total_amount)
                       FILTER (WHERE s.provenance = 'REPORTED'), 0)
                  AS reported_amount,
              COALESCE(SUM(s.total_amount)
                       FILTER (WHERE s.provenance = 'VERIFIED'), 0)
                  AS verified_amount,
              COALESCE(SUM(s.total_amount)
                       FILTER (WHERE s.provenance = 'DISPUTED'), 0)
                  AS disputed_amount,
              COUNT(*) FILTER (WHERE s.stock_discrepancy) AS discrepancies
              FROM distributor_sales s
             WHERE {' AND '.join(clauses)}
        """), params)).mappings().first()

    reported = money(row["reported_amount"])
    verified = money(row["verified_amount"])

    return {
        "verified_amount": str(verified),
        "verified_count": row["verified_n"],
        "reported_amount": str(reported),
        "reported_count": row["reported_n"],
        "disputed_amount": str(money(row["disputed_amount"])),
        "disputed_count": row["disputed_n"],
        "stock_discrepancies": row["discrepancies"],
        "counts_toward_performance": "verified_amount",
        "note": (
            "REPORTED is what the distributor says they sold; nobody has "
            "checked it. VERIFIED has been checked against evidence by someone "
            "other than the person who reported it. These are not added "
            "together, because a claim and a confirmed fact are different "
            "things and only the second should decide anything."),
    }


async def by_marketer(
    session: AsyncSession, *, distributor_id: UUID,
    since: Optional[date] = None,
) -> list[dict]:
    """Per-marketer totals, provenance kept apart.

    A marketer ranked on unverified self-reported figures is a marketer
    rewarded for optimistic paperwork.
    """
    clause = "AND s.sold_on >= :since" if since else ""
    params = {"d": str(distributor_id)}
    if since:
        params["since"] = since

    rows = (await session.execute(
        text(f"""
            SELECT m.id, m.full_name, m.is_active,
                   COUNT(s.id) AS sales,
                   COALESCE(SUM(s.total_amount)
                            FILTER (WHERE s.provenance = 'VERIFIED'), 0)
                       AS verified_amount,
                   COALESCE(SUM(s.total_amount)
                            FILTER (WHERE s.provenance = 'REPORTED'), 0)
                       AS reported_amount,
                   COUNT(*) FILTER (WHERE s.provenance = 'DISPUTED')
                       AS disputed_count
              FROM distributor_marketers m
              LEFT JOIN distributor_sales s ON s.marketer_id = m.id {clause}
             WHERE m.distributor_id = :d
             GROUP BY m.id, m.full_name, m.is_active
             ORDER BY verified_amount DESC, reported_amount DESC
        """), params)).mappings().all()

    return [
        dict(r) | {"id": str(r["id"]),
                   "verified_amount": str(money(r["verified_amount"])),
                   "reported_amount": str(money(r["reported_amount"]))}
        for r in rows
    ]


async def list_sales(
    session: AsyncSession, *, distributor_id: Optional[UUID] = None,
    provenance: Optional[str] = None, discrepancies_only: bool = False,
    limit: int = 200,
) -> list[dict]:
    clauses, params = ["1 = 1"], {"lim": limit}
    if distributor_id:
        clauses.append("s.distributor_id = :d")
        params["d"] = str(distributor_id)
    if provenance:
        clauses.append("s.provenance = :p")
        params["p"] = provenance
    if discrepancies_only:
        clauses.append("s.stock_discrepancy")

    rows = (await session.execute(
        text(f"""SELECT s.id, s.sale_reference, s.sold_on, s.total_amount,
                        s.provenance, s.stock_discrepancy, s.discrepancy_note,
                        s.verification_note,
                        d.distributor_code, d.legal_name,
                        m.full_name AS marketer, o.name AS outlet,
                        o.outlet_type, t.code AS territory,
                        v.full_name AS verified_by_name,
                        (SELECT COUNT(*) FROM distributor_sale_lines l
                          WHERE l.sale_id = s.id) AS line_count,
                        (SELECT COUNT(*) FROM distributor_sale_evidence e
                          WHERE e.sale_id = s.id) AS evidence_count
                   FROM distributor_sales s
                   JOIN distributors d ON d.id = s.distributor_id
                   LEFT JOIN distributor_marketers m ON m.id = s.marketer_id
                   LEFT JOIN distributor_outlets o ON o.id = s.outlet_id
                   LEFT JOIN territories t ON t.id = s.territory_id
                   LEFT JOIN users v ON v.id = s.verified_by
                  WHERE {' AND '.join(clauses)}
                  ORDER BY s.sold_on DESC, s.created_at DESC
                  LIMIT :lim"""),
        params)).mappings().all()
    return [dict(r) | {"id": str(r["id"])} for r in rows]


async def sale_detail(session: AsyncSession, sale_id: UUID) -> dict:
    head = (await session.execute(
        text("""SELECT s.*, d.distributor_code, d.legal_name,
                       m.full_name AS marketer, o.name AS outlet,
                       o.outlet_type, o.phone AS outlet_phone,
                       t.code AS territory,
                       r.full_name AS reported_by_name,
                       v.full_name AS verified_by_name
                  FROM distributor_sales s
                  JOIN distributors d ON d.id = s.distributor_id
                  LEFT JOIN distributor_marketers m ON m.id = s.marketer_id
                  LEFT JOIN distributor_outlets o ON o.id = s.outlet_id
                  LEFT JOIN territories t ON t.id = s.territory_id
                  LEFT JOIN users r ON r.id = s.reported_by
                  LEFT JOIN users v ON v.id = s.verified_by
                 WHERE s.id = :s"""),
        {"s": str(sale_id)})).mappings().first()
    if head is None:
        raise HTTPException(status_code=404, detail="Sale not found.")

    lines = (await session.execute(
        text("""SELECT l.quantity, l.unit, l.unit_price, l.line_total,
                       l.stock_movement_id, p.name AS product, p.sku,
                       b.batch_number, b.status AS batch_status
                  FROM distributor_sale_lines l
                  JOIN products p ON p.id = l.product_id
                  LEFT JOIN product_batches b ON b.id = l.batch_id
                 WHERE l.sale_id = :s"""),
        {"s": str(sale_id)})).mappings().all()

    evidence = (await session.execute(
        text("""SELECT e.id, e.evidence_type, e.filename, e.byte_size,
                       e.created_at, u.full_name AS uploaded_by
                  FROM distributor_sale_evidence e
                  LEFT JOIN users u ON u.id = e.uploaded_by
                 WHERE e.sale_id = :s ORDER BY e.created_at"""),
        {"s": str(sale_id)})).mappings().all()

    out = dict(head)
    for key in ("id", "distributor_id", "marketer_id", "outlet_id",
                "territory_id", "reported_by", "verified_by"):
        if out.get(key) is not None:
            out[key] = str(out[key])

    return {
        "sale": out,
        "lines": [dict(r) | {"stock_movement_id": (str(r["stock_movement_id"])
                                                   if r["stock_movement_id"]
                                                   else None)}
                  for r in lines],
        "evidence": [dict(r) | {"id": str(r["id"])} for r in evidence],
        "provenance_note": {
            "REPORTED": ("The distributor says this happened. Nobody has "
                         "checked it, and it counts toward nothing."),
            "VERIFIED": ("Checked against the attached evidence by somebody "
                         "other than the person who reported it."),
            "DISPUTED": ("Checked and found wrong. Kept on the record so the "
                         "claim remains visible."),
        }[head["provenance"]],
    }
