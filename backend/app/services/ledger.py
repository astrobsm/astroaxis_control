"""Double-entry general ledger: the only way anything is posted.

Design rules, and why each exists:

  * **Balanced by construction.** `post_entry` refuses any entry whose debits
    and credits differ. There is no code path that writes a journal line
    outside this function, so the trial balance cannot drift.
  * **Immutable once posted.** Nothing edits or deletes a posted entry. A
    mistake is corrected by posting a reversal that points at the original,
    so the ledger records what was believed at the time AND the correction.
    This is the difference between an audit trail and a mutable table.
  * **Period locking.** Entries cannot be posted into a closed period, so a
    back-dated transaction cannot silently change a month that has already
    been reported.
  * **Decimal money at 2dp.** Every amount is quantized on the way in; float
    arithmetic has already caused settling payments to be rejected elsewhere
    in this codebase.

The ledger is a DERIVED system: it records events that happened in inventory,
sales and production. It never originates them, and it must never be the
place a balance is 'fixed up' -- fix the source, then post a correction.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Sequence
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

CENT = Decimal("0.01")

ASSET, LIABILITY, EQUITY, INCOME, EXPENSE = (
    "ASSET", "LIABILITY", "EQUITY", "INCOME", "EXPENSE")

DEBIT_NORMAL = {ASSET, EXPENSE}
CREDIT_NORMAL = {LIABILITY, EQUITY, INCOME}


def money(value) -> Decimal:
    if value is None:
        return Decimal("0.00")
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class Line:
    """One side of an entry. Exactly one of debit/credit may be non-zero."""
    account_code: str
    debit: Decimal = Decimal("0.00")
    credit: Decimal = Decimal("0.00")
    description: Optional[str] = None
    cost_centre: Optional[str] = None


# ---------------------------------------------------------------------------
# Chart of accounts
# ---------------------------------------------------------------------------

async def get_account_id(session: AsyncSession, code: str) -> UUID:
    row = (await session.execute(
        text("SELECT id, is_active, is_postable FROM gl_accounts WHERE code = :c"),
        {"c": code},
    )).first()
    if row is None:
        raise HTTPException(
            status_code=400,
            detail=f"Account {code!r} does not exist in the chart of accounts.")
    if not row.is_active:
        raise HTTPException(
            status_code=400, detail=f"Account {code!r} is inactive.")
    if not row.is_postable:
        raise HTTPException(
            status_code=400,
            detail=f"Account {code!r} is a header and cannot be posted to.")
    return row.id


# ---------------------------------------------------------------------------
# Period control
# ---------------------------------------------------------------------------

async def assert_period_open(session: AsyncSession, on: date) -> None:
    row = (await session.execute(
        text("""
            SELECT name, status FROM gl_periods
             WHERE :d BETWEEN start_date AND end_date
             LIMIT 1
        """),
        {"d": on},
    )).first()
    # No period defined => unrestricted. Periods are opt-in; requiring one
    # would block posting entirely until someone configures the calendar.
    if row is not None and row.status == 'CLOSED':
        raise HTTPException(
            status_code=400,
            detail=(f"Accounting period {row.name!r} is closed; "
                    f"{on} cannot be posted to. Post to an open period or "
                    f"reopen it deliberately."),
        )


# ---------------------------------------------------------------------------
# Posting
# ---------------------------------------------------------------------------

async def _next_entry_number(session: AsyncSession, on: date) -> str:
    # Sequence per month, collision-safe: the unique index on entry_number is
    # the real guard, and the random suffix makes a retry succeed rather than
    # 500 the way a COUNT(*)+1 scheme does.
    return f"JE-{on.strftime('%Y%m')}-{uuid4().hex[:8].upper()}"


async def post_entry(
    session: AsyncSession,
    *,
    entry_date: date,
    description: str,
    source_module: str,
    lines: Sequence[Line],
    source_reference: Optional[str] = None,
    created_by: Optional[UUID] = None,
    reverses_entry_id: Optional[UUID] = None,
) -> UUID:
    """Post one balanced journal entry. Returns the entry id.

    Does not commit -- the caller owns the transaction, so the journal lands
    in the same transaction as the event it records. An inventory movement and
    its accounting consequence either both happen or neither does.
    """
    if not lines:
        raise HTTPException(
            status_code=400, detail="A journal entry must have at least one line.")

    await assert_period_open(session, entry_date)

    total_debit = Decimal("0.00")
    total_credit = Decimal("0.00")
    prepared = []

    for idx, ln in enumerate(lines):
        debit, credit = money(ln.debit), money(ln.credit)
        if debit < 0 or credit < 0:
            raise HTTPException(
                status_code=400,
                detail="Journal amounts cannot be negative; use the other "
                       "side of the entry instead.")
        if debit > 0 and credit > 0:
            raise HTTPException(
                status_code=400,
                detail=f"Line {idx + 1} has both a debit and a credit; "
                       f"a line is one or the other.")
        if debit == 0 and credit == 0:
            continue  # a zero line carries no information; drop it silently

        account_id = await get_account_id(session, ln.account_code)
        total_debit += debit
        total_credit += credit
        prepared.append((account_id, debit, credit, ln))

    if not prepared:
        raise HTTPException(
            status_code=400,
            detail="A journal entry must move a non-zero amount.")

    if total_debit != total_credit:
        raise HTTPException(
            status_code=400,
            detail=(f"Entry does not balance: debits {total_debit:,.2f} vs "
                    f"credits {total_credit:,.2f} "
                    f"(difference {abs(total_debit - total_credit):,.2f})."),
        )

    entry_id = uuid4()
    await session.execute(
        text("""
            INSERT INTO gl_journal_entries
                (id, entry_number, entry_date, description, source_module,
                 source_reference, status, reverses_entry_id, posted_at,
                 created_by, created_at)
            VALUES (:id, :num, :dt, :desc, :mod, :ref, 'POSTED', :rev,
                    NOW(), :by, NOW())
        """),
        {
            "id": str(entry_id),
            "num": await _next_entry_number(session, entry_date),
            "dt": entry_date, "desc": description,
            "mod": source_module, "ref": source_reference,
            "rev": str(reverses_entry_id) if reverses_entry_id else None,
            "by": str(created_by) if created_by else None,
        },
    )

    for n, (account_id, debit, credit, ln) in enumerate(prepared, start=1):
        await session.execute(
            text("""
                INSERT INTO gl_journal_lines
                    (id, entry_id, account_id, debit, credit, description,
                     cost_centre, line_number)
                VALUES (gen_random_uuid(), :eid, :aid, :dr, :cr, :desc,
                        :cc, :n)
            """),
            {
                "eid": str(entry_id), "aid": str(account_id),
                "dr": str(debit), "cr": str(credit),
                "desc": ln.description, "cc": ln.cost_centre, "n": n,
            },
        )

    return entry_id


async def reverse_entry(
    session: AsyncSession,
    *,
    entry_id: UUID,
    reason: str,
    on: Optional[date] = None,
    created_by: Optional[UUID] = None,
) -> UUID:
    """Reverse a posted entry by posting its mirror image.

    The original is never edited or deleted -- correcting the books by
    rewriting history is exactly what an audit trail exists to prevent.
    """
    entry = (await session.execute(
        text("""SELECT id, entry_number, entry_date, status
                  FROM gl_journal_entries WHERE id = :id FOR UPDATE"""),
        {"id": str(entry_id)},
    )).first()
    if entry is None:
        raise HTTPException(status_code=404, detail="Journal entry not found.")
    if entry.status == 'REVERSED':
        raise HTTPException(
            status_code=400, detail="Entry has already been reversed.")

    already = (await session.execute(
        text("SELECT id FROM gl_journal_entries WHERE reverses_entry_id = :id"),
        {"id": str(entry_id)},
    )).first()
    if already:
        raise HTTPException(
            status_code=400, detail="A reversal for this entry already exists.")

    original_lines = (await session.execute(
        text("""
            SELECT a.code, l.debit, l.credit, l.description, l.cost_centre
              FROM gl_journal_lines l
              JOIN gl_accounts a ON a.id = l.account_id
             WHERE l.entry_id = :id
             ORDER BY l.line_number
        """),
        {"id": str(entry_id)},
    )).fetchall()

    mirrored = [
        Line(account_code=r.code,
             debit=money(r.credit),      # swapped, hence a reversal
             credit=money(r.debit),
             description=r.description,
             cost_centre=r.cost_centre)
        for r in original_lines
    ]

    reversal_id = await post_entry(
        session,
        entry_date=on or date.today(),
        description=f"Reversal of {entry.entry_number}: {reason}",
        source_module="ledger.reversal",
        source_reference=entry.entry_number,
        lines=mirrored,
        created_by=created_by,
        reverses_entry_id=entry_id,
    )

    await session.execute(
        text("UPDATE gl_journal_entries SET status = 'REVERSED' WHERE id = :id"),
        {"id": str(entry_id)},
    )
    return reversal_id


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

async def trial_balance(
    session: AsyncSession,
    *,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> dict:
    """Debits and credits per account. Must always balance in total."""
    clauses, params = ["e.status <> 'DRAFT'"], {}
    if start:
        clauses.append("e.entry_date >= :start")
        params["start"] = start
    if end:
        clauses.append("e.entry_date <= :end")
        params["end"] = end

    rows = (await session.execute(
        text(f"""
            SELECT a.code, a.name, a.account_type, a.normal_balance,
                   COALESCE(SUM(l.debit), 0)  AS debit,
                   COALESCE(SUM(l.credit), 0) AS credit
              FROM gl_journal_lines l
              JOIN gl_journal_entries e ON e.id = l.entry_id
              JOIN gl_accounts a        ON a.id = l.account_id
             WHERE {' AND '.join(clauses)}
             GROUP BY a.code, a.name, a.account_type, a.normal_balance
             HAVING COALESCE(SUM(l.debit), 0) <> 0
                 OR COALESCE(SUM(l.credit), 0) <> 0
             ORDER BY a.code
        """),
        params,
    )).fetchall()

    accounts, td, tc = [], Decimal("0.00"), Decimal("0.00")
    for r in rows:
        dr, cr = money(r.debit), money(r.credit)
        td += dr
        tc += cr
        # Net balance expressed on the account's normal side, so an asset
        # reads positive when it holds value rather than when it is debited.
        net = (dr - cr) if r.normal_balance == 'DEBIT' else (cr - dr)
        accounts.append({
            "code": r.code, "name": r.name, "account_type": r.account_type,
            "debit": float(dr), "credit": float(cr), "balance": float(net),
        })

    return {
        "accounts": accounts,
        "total_debit": float(td),
        "total_credit": float(tc),
        "balanced": td == tc,
        "difference": float(td - tc),
        "period": {"start": str(start) if start else None,
                   "end": str(end) if end else None},
    }


async def _type_totals(session, account_type, start, end) -> Decimal:
    clauses, params = ["e.status <> 'DRAFT'", "a.account_type = :t"], {
        "t": account_type}
    if start:
        clauses.append("e.entry_date >= :start")
        params["start"] = start
    if end:
        clauses.append("e.entry_date <= :end")
        params["end"] = end
    row = (await session.execute(
        text(f"""
            SELECT COALESCE(SUM(l.debit), 0) AS dr,
                   COALESCE(SUM(l.credit), 0) AS cr
              FROM gl_journal_lines l
              JOIN gl_journal_entries e ON e.id = l.entry_id
              JOIN gl_accounts a        ON a.id = l.account_id
             WHERE {' AND '.join(clauses)}
        """),
        params,
    )).first()
    dr, cr = money(row.dr), money(row.cr)
    return (dr - cr) if account_type in DEBIT_NORMAL else (cr - dr)


async def profit_and_loss(
    session: AsyncSession, *, start: Optional[date] = None,
    end: Optional[date] = None,
) -> dict:
    income = await _type_totals(session, INCOME, start, end)
    expense = await _type_totals(session, EXPENSE, start, end)

    detail = (await session.execute(
        text("""
            SELECT a.code, a.name, a.account_type,
                   COALESCE(SUM(l.credit), 0) - COALESCE(SUM(l.debit), 0) AS credit_net,
                   COALESCE(SUM(l.debit), 0) - COALESCE(SUM(l.credit), 0) AS debit_net
              FROM gl_journal_lines l
              JOIN gl_journal_entries e ON e.id = l.entry_id
              JOIN gl_accounts a        ON a.id = l.account_id
             WHERE e.status <> 'DRAFT'
               AND a.account_type IN ('INCOME','EXPENSE')
               AND (CAST(:start AS date) IS NULL OR e.entry_date >= :start)
               AND (CAST(:end AS date)   IS NULL OR e.entry_date <= :end)
             GROUP BY a.code, a.name, a.account_type
             ORDER BY a.account_type DESC, a.code
        """),
        {"start": start, "end": end},
    )).fetchall()

    return {
        "income": [
            {"code": r.code, "name": r.name, "amount": float(money(r.credit_net))}
            for r in detail if r.account_type == INCOME],
        "expenses": [
            {"code": r.code, "name": r.name, "amount": float(money(r.debit_net))}
            for r in detail if r.account_type == EXPENSE],
        "total_income": float(income),
        "total_expenses": float(expense),
        "net_profit": float(income - expense),
        "period": {"start": str(start) if start else None,
                   "end": str(end) if end else None},
    }


async def balance_sheet(
    session: AsyncSession, *, as_at: Optional[date] = None) -> dict:
    assets = await _type_totals(session, ASSET, None, as_at)
    liabilities = await _type_totals(session, LIABILITY, None, as_at)
    equity = await _type_totals(session, EQUITY, None, as_at)
    income = await _type_totals(session, INCOME, None, as_at)
    expense = await _type_totals(session, EXPENSE, None, as_at)

    # Profit for the period is part of equity but has not been closed out to
    # retained earnings, so include it explicitly rather than losing it.
    retained = income - expense
    total_equity = equity + retained

    return {
        "assets": float(assets),
        "liabilities": float(liabilities),
        "equity": float(equity),
        "current_period_earnings": float(retained),
        "total_equity": float(total_equity),
        "total_liabilities_and_equity": float(liabilities + total_equity),
        "balanced": assets == (liabilities + total_equity),
        "difference": float(assets - (liabilities + total_equity)),
        "as_at": str(as_at) if as_at else None,
    }


async def account_ledger(
    session: AsyncSession, *, account_code: str,
    start: Optional[date] = None, end: Optional[date] = None,
) -> dict:
    """Every movement on one account, with a running balance."""
    rows = (await session.execute(
        text("""
            SELECT e.entry_number, e.entry_date, e.description,
                   e.source_module, e.source_reference,
                   l.debit, l.credit, l.description AS line_description
              FROM gl_journal_lines l
              JOIN gl_journal_entries e ON e.id = l.entry_id
              JOIN gl_accounts a        ON a.id = l.account_id
             WHERE a.code = :code
               AND e.status <> 'DRAFT'
               AND (CAST(:start AS date) IS NULL OR e.entry_date >= :start)
               AND (CAST(:end AS date)   IS NULL OR e.entry_date <= :end)
             -- e.seq, not entry_number or posted_at: the number carries a
             -- random collision-safety suffix, and NOW() is transaction time
             -- so entries in one transaction share it. Only the sequence
             -- gives a stable insertion order for the running balance.
             ORDER BY e.entry_date, e.seq, l.line_number
        """),
        {"code": account_code, "start": start, "end": end},
    )).fetchall()

    acct = (await session.execute(
        text("SELECT code, name, account_type, normal_balance "
             "FROM gl_accounts WHERE code = :c"),
        {"c": account_code},
    )).first()
    if acct is None:
        raise HTTPException(status_code=404, detail="Account not found.")

    running = Decimal("0.00")
    entries = []
    for r in rows:
        dr, cr = money(r.debit), money(r.credit)
        running += (dr - cr) if acct.normal_balance == 'DEBIT' else (cr - dr)
        entries.append({
            "entry_number": r.entry_number,
            "date": str(r.entry_date),
            "description": r.line_description or r.description,
            "source": r.source_module,
            "reference": r.source_reference,
            "debit": float(dr), "credit": float(cr),
            "balance": float(running),
        })

    return {
        "account": {"code": acct.code, "name": acct.name,
                    "type": acct.account_type},
        "entries": entries,
        "closing_balance": float(running),
    }


# ---------------------------------------------------------------------------
# Cash flow statement
# ---------------------------------------------------------------------------

# Accounts that ARE cash. Movement across these is what the statement
# explains; everything else is a reason for that movement.
CASH_ACCOUNTS = ("1100", "1110", "1200")


async def cash_flow_statement(
    session: AsyncSession, *, start: date, end: date,
) -> dict:
    """Cash flow by the DIRECT method, derived from the journal.

    Rather than inferring cash movement from balance-sheet deltas (the
    indirect method, which needs a full comparative balance sheet and hides
    what actually happened), this reads every journal line that touched a
    cash account and classifies it by what the OTHER side of that entry was.

    That makes each figure traceable: every number here can be drilled back
    to the entries that produced it.

    Classification follows the counterpart account:
      OPERATING  -- income, expenses, receivables, payables, inventory
      INVESTING  -- fixed assets
      FINANCING  -- equity and loans
    """
    opening = money((await session.execute(
        text(f"""
            SELECT COALESCE(SUM(l.debit - l.credit), 0)
              FROM gl_journal_lines l
              JOIN gl_journal_entries e ON e.id = l.entry_id
              JOIN gl_accounts a ON a.id = l.account_id
             WHERE a.code IN {CASH_ACCOUNTS}
               AND e.status <> 'DRAFT'
               AND e.entry_date < :start
        """), {"start": start})).scalar())

    # For each entry touching cash, pair the cash movement with the
    # counterpart accounts that explain it.
    rows = (await session.execute(
        text(f"""
            WITH cash_lines AS (
                SELECT l.entry_id,
                       SUM(l.debit - l.credit) AS cash_delta
                  FROM gl_journal_lines l
                  JOIN gl_journal_entries e ON e.id = l.entry_id
                  JOIN gl_accounts a ON a.id = l.account_id
                 WHERE a.code IN {CASH_ACCOUNTS}
                   AND e.status <> 'DRAFT'
                   AND e.entry_date BETWEEN :start AND :end
                 GROUP BY l.entry_id
            ),
            counterpart AS (
                SELECT cl.entry_id, cl.cash_delta,
                       a.code, a.name, a.account_type,
                       SUM(l.credit - l.debit) AS other_delta
                  FROM cash_lines cl
                  JOIN gl_journal_lines l ON l.entry_id = cl.entry_id
                  JOIN gl_accounts a ON a.id = l.account_id
                 WHERE a.code NOT IN {CASH_ACCOUNTS}
                 GROUP BY cl.entry_id, cl.cash_delta, a.code, a.name,
                          a.account_type
            )
            SELECT code, name, account_type,
                   SUM(other_delta) AS amount
              FROM counterpart
             GROUP BY code, name, account_type
             ORDER BY code
        """),
        {"start": start, "end": end},
    )).fetchall()

    def classify(code: str, account_type: str) -> str:
        # Fixed assets and their accumulated depreciation are investing.
        if code.startswith("15"):
            return "investing"
        # Equity and long-term loans are financing.
        if account_type == "EQUITY" or code.startswith("24"):
            return "financing"
        return "operating"

    buckets = {"operating": [], "investing": [], "financing": []}
    totals = {"operating": Decimal("0.00"), "investing": Decimal("0.00"),
              "financing": Decimal("0.00")}

    for r in rows:
        amount = money(r.amount)
        if amount == 0:
            continue
        section = classify(r.code, r.account_type)
        buckets[section].append({
            "code": r.code, "name": r.name, "amount": float(amount)})
        totals[section] += amount

    net_movement = money(sum(totals.values()))

    closing_actual = money((await session.execute(
        text(f"""
            SELECT COALESCE(SUM(l.debit - l.credit), 0)
              FROM gl_journal_lines l
              JOIN gl_journal_entries e ON e.id = l.entry_id
              JOIN gl_accounts a ON a.id = l.account_id
             WHERE a.code IN {CASH_ACCOUNTS}
               AND e.status <> 'DRAFT'
               AND e.entry_date <= :end
        """), {"end": end})).scalar())

    computed_closing = money(opening + net_movement)

    return {
        "period": {"start": str(start), "end": str(end)},
        "opening_cash": float(opening),
        "operating": {"items": buckets["operating"],
                      "total": float(totals["operating"])},
        "investing": {"items": buckets["investing"],
                      "total": float(totals["investing"])},
        "financing": {"items": buckets["financing"],
                      "total": float(totals["financing"])},
        "net_movement": float(net_movement),
        "closing_cash": float(closing_actual),
        # The statement must reconcile to the actual cash balance. If it does
        # not, the classification has dropped something and the report cannot
        # be trusted -- surfaced rather than silently presented.
        "reconciles": computed_closing == closing_actual,
        "reconciliation_difference": float(computed_closing - closing_actual),
    }


async def cash_position(session: AsyncSession) -> dict:
    """Current balance of every cash and bank account."""
    rows = (await session.execute(
        text(f"""
            SELECT a.code, a.name,
                   COALESCE(SUM(l.debit - l.credit), 0) AS balance
              FROM gl_accounts a
              LEFT JOIN gl_journal_lines l ON l.account_id = a.id
              LEFT JOIN gl_journal_entries e
                     ON e.id = l.entry_id AND e.status <> 'DRAFT'
             WHERE a.code IN {CASH_ACCOUNTS}
             GROUP BY a.code, a.name
             ORDER BY a.code
        """)
    )).fetchall()
    accounts = [{"code": r.code, "name": r.name,
                 "balance": float(money(r.balance))} for r in rows]
    return {"accounts": accounts,
            "total": float(money(sum(Decimal(str(a["balance"]))
                                     for a in accounts)))}
