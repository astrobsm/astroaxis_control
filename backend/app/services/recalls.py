"""Running a recall, and being honest about what was never found.

THE FIGURE THIS MODULE EXISTS TO PROTECT
========================================
**Unaccounted.**

A recall reconciliation that shows everything neatly recovered is almost always
false. Some of the product was used before anyone was contacted. Some was thrown
away by whoever had it, without telling anybody. Some went to an outlet that was
never recorded. Those units are real and they are still out there, and the only
useful thing a system can do is state the number rather than let it round to
nothing.

So `reconciliation` reports four quantities against the amount at risk when the
recall was raised -- recovered, destroyed, still on a shelf we control -- and
whatever is left is UNACCOUNTED, as its own figure with its own name. Closing a
recall with unaccounted units requires somebody to write down what they believe
became of them.

WHAT IS DERIVED AND WHAT IS STORED
==================================
`at_risk_quantity` is STORED, because it is a point-in-time fact: stock keeps
moving after a recall is raised, and the denominator has to be the figure the
recall started from.

Everything else is DERIVED -- recovered from `returned_stock`, destroyed from
`stock_movements`, still-held from the batch balance. Storing a running
"recovered" total would be a second answer to a question the movement history
already answers, and the two would disagree during exactly the investigation
that needed them to agree.

RETURNS GO THROUGH THE EXISTING PATH
====================================
`returned_stock` and `app/api/returns.py` already record goods coming back and
restore stock. This module reads what they write and adds `batch_id` /
`recall_id` to the same rows. There is no second returns workflow.

ADVERSE EVENTS
==============
`flag_adverse_event` sets a flag and returns a warning. **It notifies nobody.**
A complaint about a medical product can carry a legal duty to inform a regulator
within a fixed period; this application cannot discharge that duty and does not
pretend to. The fields for a regulator reference record what a PERSON did.
"""
from __future__ import annotations

import secrets
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import batches as bsvc
from app.services.geography import audit
from app.services.inventory import INBOUND, OUTBOUND

CHANNELS = ("PHONE", "WHATSAPP", "EMAIL", "IN_PERSON", "LETTER")

_IN = ", ".join(f"'{t}'" for t in sorted(INBOUND))
_OUT = ", ".join(f"'{t}'" for t in sorted(OUTBOUND))

ADVERSE_EVENT_WARNING = (
    "This is recorded as a POSSIBLE ADVERSE EVENT. A complaint about a medical "
    "product can create a duty to notify a regulator within a fixed period. "
    "THIS APPLICATION HAS NOTIFIED NOBODY AND CANNOT. Someone must decide "
    "whether a report is required and make it; record what was done in the "
    "regulator fields on this complaint."
)


def qty(value) -> Decimal:
    return Decimal(str(value or 0))


def qty_str(value) -> str:
    """A quantity of goods, written the way a person would write it.

    Stock columns are NUMERIC(18,6), so a plain str() gives "300.000000" for
    three hundred cartons. On a recall reconciliation -- which people read under
    pressure and copy into emails -- six trailing zeros on every figure is noise
    that makes the numbers harder to compare at a glance. Fractions are kept
    where they exist.
    """
    d = qty(value).normalize()
    # normalize() turns 300 into 3E+2; expand it back.
    return f"{d:f}" if d == d.to_integral_value() else format(d, "f")


def _reference(prefix: str) -> str:
    return f"{prefix}-{date.today():%Y%m}-{secrets.token_hex(3).upper()}"


# ---------------------------------------------------------------------------
# Raising a recall
# ---------------------------------------------------------------------------

async def raise_recall(
    session: AsyncSession, *, batch_id: UUID, reason: str,
    severity: str = "URGENT", actor=None,
) -> dict:
    """Mark the batch recalled and open the process of getting it back.

    The batch status and the recall are one act: a RECALLED batch with no recall
    record is a blocked batch nobody is chasing, and a recall record without the
    block would let the goods keep shipping while somebody fills in a form.
    """
    if not reason or len(reason.strip()) < 10:
        raise HTTPException(
            status_code=400,
            detail=("Say what is wrong with the product, in enough detail that "
                    "somebody reading this in two years understands why it was "
                    "recalled."))
    if severity not in ("URGENT", "ROUTINE", "PRECAUTIONARY"):
        raise HTTPException(
            status_code=400,
            detail="Severity must be URGENT, ROUTINE or PRECAUTIONARY.")

    open_recall = (await session.execute(
        text("""SELECT recall_reference FROM product_recalls
                 WHERE batch_id = :b AND status <> 'CLOSED'"""),
        {"b": str(batch_id)})).first()
    if open_recall:
        raise HTTPException(
            status_code=409,
            detail=(f"{open_recall.recall_reference} is already running for "
                    f"this batch. Two recalls would split the reconciliation."))

    # The trace BEFORE anything changes: this is the denominator.
    trace = await bsvc.recall_trace(session, batch_id=batch_id)
    at_risk = qty(trace["quantity_still_held"]) + qty(trace["quantity_despatched"])

    # Block despatch first. If the status change fails, no recall is opened.
    batch = (await session.execute(
        text("SELECT status FROM product_batches WHERE id = :b"),
        {"b": str(batch_id)})).mappings().first()
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found.")
    if batch["status"] != "RECALLED":
        await bsvc.set_status(session, batch_id=batch_id, status="RECALLED",
                              reason=reason.strip(), actor=actor)

    recall_id = uuid4()
    reference = _reference("RC")
    await session.execute(
        text("""
            INSERT INTO product_recalls
                (id, recall_reference, batch_id, severity, reason,
                 at_risk_quantity, at_risk_locations, at_risk_recipients,
                 status, raised_by)
            VALUES (:id, :ref, :b, :sev, :reason, :atrisk, :locs, :recips,
                    'OPEN', :by)
        """),
        {"id": str(recall_id), "ref": reference, "b": str(batch_id),
         "sev": severity, "reason": reason.strip(), "atrisk": qty_str(at_risk),
         "locs": len(trace["still_held"]), "recips": trace["recipients"],
         "by": str(actor.id) if actor else None},
    )
    await audit(session, event_type="RECALL_RAISED",
                entity_type="product_recall", entity_id=recall_id,
                actor=actor, reason=reason.strip(),
                new_value={"reference": reference,
                           "batch": trace["batch_number"],
                           "at_risk": str(at_risk),
                           "recipients": trace["recipients"]})

    return {
        "id": str(recall_id), "recall_reference": reference,
        "batch_number": trace["batch_number"], "product": trace["product"],
        "severity": severity,
        "at_risk_quantity": qty_str(at_risk),
        "still_held": trace["still_held"],
        "despatched_to": trace["despatched_to"],
        "recipients_to_contact": trace["recipients"],
        "next": ("Despatch is now blocked everywhere. The work is contacting "
                 "the people on this list and recording what they say."),
    }


# ---------------------------------------------------------------------------
# Telling people
# ---------------------------------------------------------------------------

async def record_notification(
    session: AsyncSession, *, recall_id: UUID, channel: str,
    distributor_id: Optional[UUID] = None, customer_id: Optional[UUID] = None,
    outlet_id: Optional[UUID] = None, contact_name: Optional[str] = None,
    contact_phone: Optional[str] = None, acknowledged: bool = False,
    response: Optional[str] = None, quantity_reported_held=None, actor=None,
) -> dict:
    """Record that somebody was actually contacted, and what they said.

    `acknowledged` is separate from the notification existing, because an
    unanswered phone is an attempt and not a notification -- and the difference
    decides whether anyone has to try again.
    """
    if channel not in CHANNELS:
        raise HTTPException(
            status_code=400,
            detail=f"Channel must be one of: {', '.join(CHANNELS)}.")
    if not any([distributor_id, customer_id, outlet_id,
                (contact_name or "").strip()]):
        raise HTTPException(
            status_code=400,
            detail="Say who was contacted -- a distributor, an outlet, or a "
                   "name and number.")

    recall = (await session.execute(
        text("""SELECT id, recall_reference, status FROM product_recalls
                 WHERE id = :r"""), {"r": str(recall_id)})).mappings().first()
    if recall is None:
        raise HTTPException(status_code=404, detail="Recall not found.")
    if recall["status"] == "CLOSED":
        raise HTTPException(
            status_code=400,
            detail=f"{recall['recall_reference']} is closed.")

    notification_id = uuid4()
    await session.execute(
        text("""
            INSERT INTO recall_notifications
                (id, recall_id, distributor_id, customer_id, outlet_id,
                 contact_name, contact_phone, channel, notified_by,
                 acknowledged, response, quantity_reported_held)
            VALUES (:id, :r, CAST(:d AS uuid), CAST(:c AS uuid),
                    CAST(:o AS uuid), :name, :phone, :ch, :by, :ack, :resp,
                    CAST(:q AS numeric))
        """),
        {"id": str(notification_id), "r": str(recall_id),
         "d": str(distributor_id) if distributor_id else None,
         "c": str(customer_id) if customer_id else None,
         "o": str(outlet_id) if outlet_id else None,
         "name": contact_name, "phone": contact_phone, "ch": channel,
         "by": str(actor.id) if actor else None, "ack": acknowledged,
         "resp": response,
         "q": (str(quantity_reported_held)
               if quantity_reported_held is not None else None)},
    )

    if recall["status"] == "OPEN":
        await session.execute(
            text("""UPDATE product_recalls SET status = 'RECOVERING',
                           updated_at = NOW() WHERE id = :r"""),
            {"r": str(recall_id)})

    return {"id": str(notification_id), "acknowledged": acknowledged,
            "note": ("Recorded as contacted." if acknowledged else
                     "Recorded as an ATTEMPT. Nobody confirmed receiving it, "
                     "so this person still needs reaching.")}


async def outstanding_contacts(
    session: AsyncSession, *, recall_id: UUID,
) -> dict:
    """Who is on the list and has not acknowledged.

    Built from the trace rather than the notification log, so somebody who was
    never contacted at all appears -- a list of unacknowledged notifications
    would silently omit the people nobody has tried yet.
    """
    recall = (await session.execute(
        text("SELECT batch_id FROM product_recalls WHERE id = :r"),
        {"r": str(recall_id)})).mappings().first()
    if recall is None:
        raise HTTPException(status_code=404, detail="Recall not found.")

    trace = await bsvc.recall_trace(session, batch_id=recall["batch_id"])

    rows = (await session.execute(
        text("""SELECT distributor_id, customer_id FROM recall_notifications
                 WHERE recall_id = :r AND acknowledged"""),
        {"r": str(recall_id)})).mappings().all()
    acknowledged_distributors = {str(r["distributor_id"]) for r in rows
                                 if r["distributor_id"]}
    acknowledged_customers = {str(r["customer_id"]) for r in rows
                              if r["customer_id"]}

    pending = []
    for location in trace["still_held"]:
        holder = location.get("distributor_id")
        if holder and str(holder) not in acknowledged_distributors:
            pending.append({
                "kind": "DISTRIBUTOR", "name": location["distributor"],
                "holding": location["on_hand"],
                "distributor_id": str(holder),
            })
    for recipient in trace["despatched_to"]:
        # Despatched rows identify an end customer; a distributor id is there
        # only when the order went through one.
        holder = recipient.get("distributor_id")
        if holder and str(holder) in acknowledged_distributors:
            continue
        if recipient.get("customer_code") in acknowledged_customers:
            continue
        pending.append({
            "kind": "CUSTOMER", "name": recipient["customer"],
            "phone": recipient.get("phone"),
            "received": recipient["quantity"],
            "order_number": recipient["order_number"],
        })

    return {
        "still_to_contact": pending,
        "count": len(pending),
        "note": ("Built from who holds or received the batch, not from the "
                 "notification log -- otherwise somebody nobody has tried yet "
                 "would not appear at all."),
    }


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

async def reconciliation(session: AsyncSession, *, recall_id: UUID) -> dict:
    """Four quantities, and whatever is left over named as unaccounted.

    A recall that appears fully recovered is almost always a recall whose
    unaccounted units were rounded away. That figure gets its own name here.
    """
    recall = (await session.execute(
        text("""SELECT r.*, b.batch_number, p.name AS product
                  FROM product_recalls r
                  JOIN product_batches b ON b.id = r.batch_id
                  JOIN products p ON p.id = b.product_id
                 WHERE r.id = :r"""),
        {"r": str(recall_id)})).mappings().first()
    if recall is None:
        raise HTTPException(status_code=404, detail="Recall not found.")

    at_risk = qty(recall["at_risk_quantity"])

    # Recovered: what app/api/returns.py wrote. Not a separate counter.
    recovered = qty((await session.execute(
        text("""SELECT COALESCE(SUM(quantity), 0) FROM returned_stock
                 WHERE recall_id = :r"""), {"r": str(recall_id)})).scalar())

    # Destroyed: write-offs of this batch since the recall was raised. DAMAGE
    # is deliberately permitted for a recalled batch -- it is how a recall is
    # completed (see inventory.BLOCKED_FOR_BAD_BATCH).
    destroyed = qty((await session.execute(
        text("""SELECT COALESCE(SUM(quantity), 0) FROM stock_movements
                 WHERE batch_id = :b AND movement_type = 'DAMAGE'
                   AND created_at >= :since"""),
        {"b": str(recall["batch_id"]),
         "since": recall["created_at"]})).scalar())

    still_held = qty((await session.execute(
        text(f"""SELECT COALESCE(SUM(CASE WHEN movement_type IN ({_IN})
                                     THEN quantity ELSE -quantity END), 0)
                   FROM stock_movements WHERE batch_id = :b"""),
        {"b": str(recall["batch_id"])})).scalar())

    # Whatever the four do not account for. Never rounded, never hidden.
    unaccounted = at_risk - recovered - destroyed - still_held
    if unaccounted < 0:
        unaccounted = Decimal("0")

    notifications = (await session.execute(
        text("""SELECT COUNT(*) AS attempts,
                       COUNT(*) FILTER (WHERE acknowledged) AS acknowledged
                  FROM recall_notifications WHERE recall_id = :r"""),
        {"r": str(recall_id)})).mappings().first()

    return {
        "recall_reference": recall["recall_reference"],
        "batch_number": recall["batch_number"],
        "product": recall["product"],
        "status": recall["status"],
        "raised_on": str(recall["raised_on"]),
        "at_risk_quantity": qty_str(at_risk),
        "recovered_quantity": qty_str(recovered),
        "destroyed_quantity": qty_str(destroyed),
        "still_on_our_shelves": qty_str(still_held),
        # The figure the whole module exists to keep visible.
        "unaccounted_quantity": qty_str(unaccounted),
        "unaccounted_explanation": recall["unaccounted_explanation"],
        "contacts_attempted": notifications["attempts"],
        "contacts_acknowledged": notifications["acknowledged"],
        "note": (
            f"{qty_str(unaccounted)} unit(s) are not accounted for. They were not "
            f"returned, not written off, and are not on a shelf the company "
            f"controls -- they may have been used, discarded, or sold on "
            f"without record. This figure does not go away by being closed."
            if unaccounted > 0 else
            "Every unit at risk has been returned, written off, or is still on "
            "a shelf the company controls."),
    }


async def close_recall(
    session: AsyncSession, *, recall_id: UUID, closure_note: str,
    unaccounted_explanation: Optional[str] = None, actor=None,
) -> dict:
    """Close a recall. Unaccounted units must be explained, not ignored."""
    if not closure_note or len(closure_note.strip()) < 10:
        raise HTTPException(
            status_code=400,
            detail="Record what was done and what the outcome was.")

    figures = await reconciliation(session, recall_id=recall_id)
    if figures["status"] == "CLOSED":
        raise HTTPException(
            status_code=400,
            detail=f"{figures['recall_reference']} is already closed.")

    unaccounted = qty(figures["unaccounted_quantity"])
    if unaccounted > 0 and (not unaccounted_explanation
                            or len(unaccounted_explanation.strip()) < 10):
        raise HTTPException(
            status_code=400,
            detail=(f"{unaccounted} unit(s) of batch {figures['batch_number']} "
                    f"are unaccounted for. Say what is believed to have "
                    f"happened to them before closing -- a recall closed with "
                    f"that number unexplained reads as complete when product "
                    f"is still in use."))

    await session.execute(
        text("""UPDATE product_recalls
                   SET status = 'CLOSED', closed_on = CURRENT_DATE,
                       closed_by = :by, closure_note = :note,
                       unaccounted_explanation = :unacc, updated_at = NOW()
                 WHERE id = :r"""),
        {"by": str(actor.id) if actor else None, "note": closure_note.strip(),
         "unacc": (unaccounted_explanation.strip()
                   if unaccounted_explanation else None),
         "r": str(recall_id)},
    )
    await audit(session, event_type="RECALL_CLOSED",
                entity_type="product_recall", entity_id=recall_id,
                actor=actor, reason=closure_note.strip(),
                new_value={"reference": figures["recall_reference"],
                           "recovered": figures["recovered_quantity"],
                           "destroyed": figures["destroyed_quantity"],
                           "unaccounted": figures["unaccounted_quantity"]})
    return {**figures, "status": "CLOSED",
            "unaccounted_explanation": unaccounted_explanation}


async def list_recalls(
    session: AsyncSession, *, open_only: bool = False,
) -> list[dict]:
    clause = "WHERE r.status <> 'CLOSED'" if open_only else ""
    rows = (await session.execute(
        text(f"""SELECT r.id, r.recall_reference, r.severity, r.status,
                        r.raised_on, r.closed_on, r.at_risk_quantity,
                        r.reason, b.batch_number, p.name AS product,
                        u.full_name AS raised_by_name,
                        (SELECT COUNT(*) FROM recall_notifications n
                          WHERE n.recall_id = r.id) AS contacts,
                        (SELECT COALESCE(SUM(rs.quantity), 0)
                           FROM returned_stock rs
                          WHERE rs.recall_id = r.id) AS recovered
                   FROM product_recalls r
                   JOIN product_batches b ON b.id = r.batch_id
                   JOIN products p ON p.id = b.product_id
                   LEFT JOIN users u ON u.id = r.raised_by
                   {clause}
                  ORDER BY r.raised_on DESC"""))).mappings().all()
    return [dict(r) | {"id": str(r["id"])} for r in rows]


# ---------------------------------------------------------------------------
# Complaints
# ---------------------------------------------------------------------------

async def record_complaint(
    session: AsyncSession, *, description: str,
    product_id: Optional[UUID] = None, batch_id: Optional[UUID] = None,
    distributor_id: Optional[UUID] = None, outlet_id: Optional[UUID] = None,
    received_from: Optional[str] = None, contact_phone: Optional[str] = None,
    severity: str = "MEDIUM", potential_adverse_event: bool = False,
    received_on: Optional[date] = None, actor=None,
) -> dict:
    """Record a complaint exactly as it was made.

    The description cannot be edited afterwards -- what the complainant said is
    the thing being investigated, and an investigation that rewrites the
    complaint as it goes is not an investigation.
    """
    if not description or len(description.strip()) < 10:
        raise HTTPException(
            status_code=400,
            detail="Record what was actually said, in their words where you can.")
    if severity not in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
        raise HTTPException(status_code=400, detail="Unknown severity.")

    complaint_id = uuid4()
    reference = _reference("CP")
    await session.execute(
        text("""
            INSERT INTO product_complaints
                (id, complaint_reference, product_id, batch_id,
                 distributor_id, outlet_id, received_on, received_from,
                 contact_phone, description, severity,
                 potential_adverse_event, created_by)
            VALUES (:id, :ref, CAST(:p AS uuid), CAST(:b AS uuid),
                    CAST(:d AS uuid), CAST(:o AS uuid), :on, :from, :phone,
                    :desc, :sev, :ae, :by)
        """),
        {"id": str(complaint_id), "ref": reference,
         "p": str(product_id) if product_id else None,
         "b": str(batch_id) if batch_id else None,
         "d": str(distributor_id) if distributor_id else None,
         "o": str(outlet_id) if outlet_id else None,
         "on": received_on or date.today(), "from": received_from,
         "phone": contact_phone, "desc": description.strip(),
         "sev": severity, "ae": potential_adverse_event,
         "by": str(actor.id) if actor else None},
    )
    await audit(session, event_type="COMPLAINT_RECORDED",
                entity_type="product_complaint", entity_id=complaint_id,
                distributor_id=distributor_id, actor=actor,
                new_value={"reference": reference, "severity": severity,
                           "potential_adverse_event": potential_adverse_event})

    return {
        "id": str(complaint_id), "complaint_reference": reference,
        "status": "OPEN", "severity": severity,
        "potential_adverse_event": potential_adverse_event,
        # Said every time, not once in a settings page nobody reads.
        "warning": ADVERSE_EVENT_WARNING if potential_adverse_event else None,
    }


async def update_complaint(
    session: AsyncSession, *, complaint_id: UUID,
    investigation: Optional[str] = None, outcome: Optional[str] = None,
    status: Optional[str] = None, severity: Optional[str] = None,
    potential_adverse_event: Optional[bool] = None,
    regulator_notified_on: Optional[date] = None,
    regulator_reference: Optional[str] = None, actor=None,
) -> dict:
    row = (await session.execute(
        text("""SELECT * FROM product_complaints WHERE id = :c FOR UPDATE"""),
        {"c": str(complaint_id)})).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Complaint not found.")
    if row["status"] == "CLOSED":
        raise HTTPException(
            status_code=400,
            detail=f"{row['complaint_reference']} is closed.")

    if status == "CLOSED" and not (outcome or row["outcome"]):
        raise HTTPException(
            status_code=400,
            detail=("Record the outcome before closing. A complaint that was "
                    "investigated and found groundless is closed with that "
                    "finding -- the complainant is entitled to know it was "
                    "looked at."))

    sets, params = [], {"c": str(complaint_id)}
    for field, value in (("investigation", investigation),
                         ("outcome", outcome), ("severity", severity),
                         ("regulator_reference", regulator_reference)):
        if value is not None:
            sets.append(f"{field} = :{field}")
            params[field] = value
    if regulator_notified_on is not None:
        sets.append("regulator_notified_on = :rn")
        params["rn"] = regulator_notified_on
    if potential_adverse_event is not None:
        sets.append("potential_adverse_event = :ae")
        params["ae"] = potential_adverse_event
    if status is not None:
        sets.append("status = :st")
        params["st"] = status
        if status == "CLOSED":
            sets.append("closed_on = CURRENT_DATE")
            sets.append("closed_by = :by")
            params["by"] = str(actor.id) if actor else None
    if not sets:
        raise HTTPException(status_code=400, detail="Nothing to change.")

    sets.append("updated_at = NOW()")
    await session.execute(
        text(f"UPDATE product_complaints SET {', '.join(sets)} WHERE id = :c"),
        params)

    still_adverse = (potential_adverse_event
                     if potential_adverse_event is not None
                     else row["potential_adverse_event"])
    return {
        "id": str(complaint_id), "status": status or row["status"],
        "warning": (ADVERSE_EVENT_WARNING if still_adverse
                    and not row["regulator_notified_on"]
                    and regulator_notified_on is None else None),
    }


async def list_complaints(
    session: AsyncSession, *, open_only: bool = False,
    adverse_only: bool = False, batch_id: Optional[UUID] = None,
) -> dict:
    clauses, params = ["1 = 1"], {}
    if open_only:
        clauses.append("c.status <> 'CLOSED'")
    if adverse_only:
        clauses.append("c.potential_adverse_event")
    if batch_id:
        clauses.append("c.batch_id = :b")
        params["b"] = str(batch_id)

    rows = (await session.execute(
        text(f"""SELECT c.id, c.complaint_reference, c.received_on,
                        c.received_from, c.contact_phone, c.description,
                        c.severity, c.status, c.potential_adverse_event,
                        c.regulator_notified_on, c.regulator_reference,
                        c.investigation, c.outcome,
                        p.name AS product, b.batch_number,
                        d.legal_name AS distributor, o.name AS outlet
                   FROM product_complaints c
                   LEFT JOIN products p ON p.id = c.product_id
                   LEFT JOIN product_batches b ON b.id = c.batch_id
                   LEFT JOIN distributors d ON d.id = c.distributor_id
                   LEFT JOIN distributor_outlets o ON o.id = c.outlet_id
                  WHERE {' AND '.join(clauses)}
                  ORDER BY c.potential_adverse_event DESC,
                           c.received_on DESC"""),
        params)).mappings().all()

    unreported = [
        r for r in rows
        if r["potential_adverse_event"] and r["regulator_notified_on"] is None
    ]
    return {
        "complaints": [dict(r) | {"id": str(r["id"])} for r in rows],
        "possible_adverse_events": sum(
            1 for r in rows if r["potential_adverse_event"]),
        "adverse_events_with_no_regulator_record": len(unreported),
        "warning": (ADVERSE_EVENT_WARNING if unreported else None),
    }
