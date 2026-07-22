"""VAT returns: a filing register derived from the ledger.

Nigerian VAT is a monthly self-assessment. A business charges output VAT on
its sales, pays input VAT on its purchases, and remits the difference to FIRS
by the 21st of the following month. This module prepares that return, freezes
it on filing, and posts the remittance.

WHERE THE FIGURES COME FROM, and why
------------------------------------
* **Output VAT** is read from the ledger: credits to VAT Payable (2300) in the
  period. That account is credited whenever a sale charges VAT, so the ledger
  is the authoritative record of VAT collected. (If sales are not yet charging
  VAT, this is legitimately zero -- the return then reflects reality rather
  than inventing a liability.)
* **Input VAT** is read from the purchase day book: `purchase_invoices.tax_amount`
  for invoices dated in the period, plus any VAT posted directly to Input VAT
  Recoverable (1360). Purchases are where recoverable VAT is documented, so
  that is the honest source for it.

A prepared return is a COMPUTATION. Filing SNAPSHOTS those figures into
`vat_returns`, because a return is what was declared to FIRS on a date -- a
later back-dated correction to the ledger must not silently change a return
that has already been submitted. The snapshot is the audit record; the live
computation is only how it was arrived at.
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
from app.services.posting import posting_enabled

ACC_VAT_PAYABLE = "2300"
ACC_INPUT_VAT = "1360"
ACC_BANK = "1200"
ACC_CASH = "1100"


async def compute_vat_return(
    session: AsyncSession, *, start: date, end: date,
) -> dict:
    """Compute (not persist) the VAT position for a period.

    Every figure is traceable to its source, and the reader is told when a
    source is empty rather than being shown a bare zero they cannot interpret.
    """
    if end < start:
        raise HTTPException(status_code=400, detail="end cannot precede start.")

    # Output VAT: credits to VAT Payable in the period. Debits to 2300 are
    # remittances, not output VAT, so they are excluded -- output VAT is what
    # was charged, not what is left owing after a payment.
    output_vat = money((await session.execute(
        text("""
            SELECT COALESCE(SUM(l.credit), 0)
              FROM gl_journal_lines l
              JOIN gl_journal_entries e ON e.id = l.entry_id
              JOIN gl_accounts a ON a.id = l.account_id
             WHERE a.code = :vat
               AND e.status <> 'DRAFT'
               AND e.source_module <> 'tax.remittance'
               AND e.entry_date BETWEEN :start AND :end
        """),
        {"vat": ACC_VAT_PAYABLE, "start": start, "end": end},
    )).scalar())

    # Input VAT already carried in the ledger (debits to Input VAT Recoverable).
    ledger_input = money((await session.execute(
        text("""
            SELECT COALESCE(SUM(l.debit - l.credit), 0)
              FROM gl_journal_lines l
              JOIN gl_journal_entries e ON e.id = l.entry_id
              JOIN gl_accounts a ON a.id = l.account_id
             WHERE a.code = :ivat
               AND e.status <> 'DRAFT'
               AND e.entry_date BETWEEN :start AND :end
        """),
        {"ivat": ACC_INPUT_VAT, "start": start, "end": end},
    )).scalar())

    # Input VAT documented on purchase invoices in the period. This is the
    # purchase day book -- recoverable VAT is documented here whether or not a
    # journal has split it out yet, so it is the honest basis for the claim.
    doc_input = money((await session.execute(
        text("""
            SELECT COALESCE(SUM(tax_amount), 0)
              FROM purchase_invoices
             WHERE invoice_date BETWEEN :start AND :end
        """),
        {"start": start, "end": end},
    )).scalar())

    input_vat = money(ledger_input + doc_input)
    net = money(output_vat - input_vat)

    notes = []
    if output_vat == 0:
        notes.append(
            "No output VAT was posted for this period. If sales should be "
            "charging VAT, that is a posting gap, not a nil return.")
    if doc_input > 0 and ledger_input == 0:
        notes.append(
            f"Input VAT of {doc_input:,.2f} is taken from purchase invoices; "
            f"it has not been posted to the ledger's Input VAT account, so it "
            f"reduces this return but is not yet reflected in account 1360.")

    return {
        "period": {"start": str(start), "end": str(end)},
        "output_vat": float(output_vat),
        "input_vat": float(input_vat),
        "input_vat_breakdown": {
            "from_ledger": float(ledger_input),
            "from_purchase_invoices": float(doc_input),
        },
        "net_payable": float(net),
        # A negative net is a credit position: more input than output VAT,
        # carried forward rather than remitted.
        "position": ("PAYABLE" if net > 0 else
                     "CREDIT" if net < 0 else "NIL"),
        "notes": notes,
    }


async def file_vat_return(
    session: AsyncSession, *, start: date, end: date,
    filed_by: str, firs_reference: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    """Freeze the period's computed figures into a FILED return.

    Refuses to file a period twice (a partial unique index enforces it at the
    database level too), because a period can only be declared to FIRS once.
    """
    if not (filed_by or "").strip():
        raise HTTPException(
            status_code=400, detail="filed_by is required to file a return.")

    existing = (await session.execute(
        text("""SELECT id, status FROM vat_returns
                 WHERE period_start = :s AND period_end = :e
                   AND status <> 'DRAFT'"""),
        {"s": start, "e": end},
    )).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"A return for {start}..{end} has already been filed "
                   f"(status {existing.status}).")

    computed = await compute_vat_return(session, start=start, end=end)
    ret_id = uuid4()
    await session.execute(
        text("""
            INSERT INTO vat_returns
                (id, period_start, period_end, output_vat, input_vat,
                 net_payable, status, firs_reference, filed_by, filed_at, notes)
            VALUES (:id, :s, :e, :ov, :iv, :net, 'FILED', :ref, :by, NOW(), :n)
        """),
        {"id": str(ret_id), "s": start, "e": end,
         "ov": computed["output_vat"], "iv": computed["input_vat"],
         "net": computed["net_payable"], "ref": firs_reference,
         "by": filed_by.strip(), "n": notes},
    )
    return {"vat_return_id": str(ret_id), "status": "FILED", **computed}


async def record_vat_payment(
    session: AsyncSession, *, vat_return_id: UUID, amount,
    paid_from: str = "bank", on: Optional[date] = None,
    created_by: Optional[UUID] = None,
) -> dict:
    """Remit a filed return: Dr VAT Payable / Cr Bank, and mark it PAID.

    Settling the VAT liability is not an expense -- the expense was the sale's
    VAT, recognised when it was charged. This entry clears the liability that
    output VAT built up. Posting is gated like every other automatic entry.
    """
    ret = (await session.execute(
        text("""SELECT id, period_start, period_end, net_payable, status
                  FROM vat_returns WHERE id = :id FOR UPDATE"""),
        {"id": str(vat_return_id)},
    )).first()
    if ret is None:
        raise HTTPException(status_code=404, detail="VAT return not found.")
    if ret.status == 'DRAFT':
        raise HTTPException(
            status_code=400, detail="File the return before recording payment.")
    if ret.status == 'PAID':
        raise HTTPException(
            status_code=400, detail="This return is already marked paid.")

    amt = money(amount)
    if amt <= 0:
        raise HTTPException(
            status_code=400, detail="Payment amount must be positive.")

    entry_id = None
    if posting_enabled():
        account = ACC_CASH if (paid_from or "bank").lower() == "cash" \
            else ACC_BANK
        entry_id = await post_entry(
            session,
            entry_date=on or date.today(),
            description=f"VAT remittance for {ret.period_start}..{ret.period_end}",
            source_module="tax.remittance",
            source_reference=f"VAT-{vat_return_id}",
            lines=[
                Line(ACC_VAT_PAYABLE, debit=amt, description="VAT remitted"),
                Line(account, credit=amt, description=f"Paid by {paid_from}"),
            ],
            created_by=created_by,
        )

    await session.execute(
        text("""UPDATE vat_returns
                   SET status = 'PAID', paid_amount = :amt,
                       payment_entry_id = :eid
                 WHERE id = :id"""),
        {"amt": str(amt), "eid": str(entry_id) if entry_id else None,
         "id": str(vat_return_id)},
    )
    return {
        "vat_return_id": str(vat_return_id), "status": "PAID",
        "paid_amount": float(amt),
        "posted": entry_id is not None,
        "journal_entry_id": str(entry_id) if entry_id else None,
    }


async def list_vat_returns(session: AsyncSession) -> list:
    rows = (await session.execute(
        text("""SELECT id, period_start, period_end, output_vat, input_vat,
                       net_payable, status, firs_reference, filed_by, filed_at,
                       paid_amount
                  FROM vat_returns
                 ORDER BY period_start DESC""")
    )).fetchall()
    return [
        {"id": str(r.id), "period_start": str(r.period_start),
         "period_end": str(r.period_end), "output_vat": float(r.output_vat),
         "input_vat": float(r.input_vat), "net_payable": float(r.net_payable),
         "status": r.status, "firs_reference": r.firs_reference,
         "filed_by": r.filed_by,
         "filed_at": r.filed_at.isoformat() if r.filed_at else None,
         "paid_amount": float(r.paid_amount)}
        for r in rows
    ]
