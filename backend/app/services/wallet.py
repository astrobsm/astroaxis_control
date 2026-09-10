"""Staff operational wallets: the money logic, in one place.

WHAT THIS IS
------------
The company gives an employee money to run operations with. This module keeps
the record of what was entrusted, what was spent on what evidence, what came
back, and what is still unaccounted for. It does not hold or move money -- the
actual transfer happens through the company's existing bank or cash, outside
the application. See the migration t9012345678s docstring for why an advance
is an ASSET (a claim on the employee) and not an expense.

THE INVARIANTS THIS FILE EXISTS TO PROTECT
------------------------------------------
1.  **The ledger is the balance.** `wallets.balance` is a cache. Every read
    that matters, and every write, derives the figure from `wallet_ledger`.
    Nothing anywhere trusts a number sent by a client.

2.  **Nothing is edited.** Every movement appends a row. A mistake appends a
    reversal pointing at the original. The database enforces this with
    triggers, so a bug here cannot quietly rewrite history.

3.  **One writer at a time, per wallet.** Every mutating operation takes
    `SELECT ... FOR UPDATE` on the wallet row first. Two phones submitting an
    expense at the same moment serialise; without the lock, both could read a
    N5,000 balance and both spend it.

4.  **A retry is not a second transaction.** Callers pass an idempotency key;
    a partial unique index makes the duplicate lose. This matters on a phone
    on a bad connection, where "submit" is pressed twice because the first
    response never arrived.

5.  **Money quantized to 2dp, always Decimal.** Reusing `ledger.money()`,
    because float arithmetic has already broken payment settlement elsewhere
    in this codebase.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It never blocks a transaction because of an anomaly flag. Flags are questions
for a human. Automatically freezing a factory supervisor's wallet at 2am on a
heuristic is how a machine stays broken until morning.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ledger import Line, money, post_entry
from app.services.posting import _should_post

ZERO = Decimal("0.00")

# Chart-of-accounts codes this module posts to. Named once so a change to the
# chart is a single edit rather than a search for string literals.
ACC_STAFF_ADVANCES = "1120"
ACC_REIMBURSEMENT_PAYABLE = "2160"
ACC_CASH = "1100"
ACC_BANK = "1200"
ACC_FALLBACK_EXPENSE = "6600"  # Office Expenses, when a category names none

SOURCE_MODULE = "wallet"

# Approver seniority. A FINANCE approver outranks a supervisor and can settle
# reconciliations, but cannot wave through a MANAGEMENT-tier expense: the
# person who pays is not the person who authorises.
_TIER_RANK = {"SELF": 0, "SUPERVISOR": 1, "FINANCE": 2, "MANAGEMENT": 3}
_RANK_TIER = {v: k for k, v in _TIER_RANK.items()}

MAX_RECEIPT_BYTES = 5 * 1024 * 1024
ALLOWED_RECEIPT_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif",
    "application/pdf",
}


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------

def _reference(prefix: str, on: Optional[date] = None) -> str:
    """A human-quotable reference with a random tail.

    Random rather than a COUNT(*)+1 sequence: under concurrency a counted
    sequence collides, and the retry path for "reference already exists" on a
    financial insert is far more dangerous than a slightly less tidy number.
    """
    stamp = (on or date.today()).strftime("%Y%m")
    return f"{prefix}-{stamp}-{secrets.token_hex(4).upper()}"


# ---------------------------------------------------------------------------
# Wallet access
# ---------------------------------------------------------------------------

async def lock_wallet(session: AsyncSession, wallet_id: UUID) -> Any:
    """Take the per-wallet write lock and return the row.

    Every mutating operation starts here. The lock is released when the
    caller's transaction ends, which is also when the ledger row lands -- so
    the read of the balance and the write that depends on it cannot be
    interleaved by another request.
    """
    row = (await session.execute(
        text("SELECT * FROM wallets WHERE id = :id FOR UPDATE"),
        {"id": str(wallet_id)},
    )).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Wallet not found.")
    return row


async def get_wallet(session: AsyncSession, wallet_id: UUID) -> Any:
    row = (await session.execute(
        text("SELECT * FROM wallets WHERE id = :id"), {"id": str(wallet_id)},
    )).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Wallet not found.")
    return row


def _assert_spendable(wallet) -> None:
    if wallet["status"] != "ACTIVE":
        raise HTTPException(
            status_code=400,
            detail=(f"Wallet {wallet['wallet_number']} is {wallet['status']} "
                    f"and cannot be used. An administrator must reactivate it."),
        )


# ---------------------------------------------------------------------------
# Balance derivation -- the heart of the module
# ---------------------------------------------------------------------------

_TOTALS_SQL = text("""
    SELECT
      COALESCE(SUM(CASE WHEN direction = 'CREDIT' THEN amount
                        ELSE -amount END), 0) AS balance,
      COALESCE(SUM(CASE
          WHEN entry_type IN ('FUNDING','OPENING') THEN amount
          WHEN entry_type = 'REVERSAL' AND source_type = 'wallet_funding'
               THEN -amount
          ELSE 0 END), 0) AS total_funded,
      COALESCE(SUM(CASE
          WHEN entry_type = 'EXPENSE' THEN amount
          WHEN entry_type = 'REVERSAL' AND source_type = 'wallet_expense'
               THEN -amount
          ELSE 0 END), 0) AS total_spent,
      COALESCE(SUM(CASE
          WHEN entry_type = 'RETURN' THEN amount
          WHEN entry_type = 'REVERSAL' AND source_type = 'wallet_return'
               THEN -amount
          ELSE 0 END), 0) AS total_returned,
      COALESCE(SUM(CASE WHEN entry_type = 'ADJUSTMENT'
                        THEN CASE WHEN direction = 'CREDIT' THEN amount
                                  ELSE -amount END
                        ELSE 0 END), 0) AS total_adjusted,
      MAX(created_at) AS last_transaction_at
      FROM wallet_ledger
     WHERE wallet_id = :w
""")


async def derive_totals(session: AsyncSession, wallet_id: UUID) -> dict:
    """Recompute every wallet figure from the ledger. Never cached, never guessed.

    The identity that must hold, and that test_wallet.py asserts:

        balance = funded + adjusted - spent - returned
                = SUM(credits) - SUM(debits)

    Both halves are computed here from the same rows, so a drift between them
    would mean the entry_type/direction pairing is inconsistent -- which is
    exactly the bug worth failing loudly on.
    """
    row = (await session.execute(_TOTALS_SQL, {"w": str(wallet_id)})).mappings().first()
    totals = {
        "balance": money(row["balance"]),
        "total_funded": money(row["total_funded"]),
        "total_spent": money(row["total_spent"]),
        "total_returned": money(row["total_returned"]),
        "total_adjusted": money(row["total_adjusted"]),
        "last_transaction_at": row["last_transaction_at"],
    }
    composed = (totals["total_funded"] + totals["total_adjusted"]
                - totals["total_spent"] - totals["total_returned"])
    if composed != totals["balance"]:
        # Not a user error and not recoverable by retrying: the ledger itself
        # is inconsistent. Refuse rather than serve a number nobody can defend.
        raise HTTPException(
            status_code=500,
            detail=(f"Wallet ledger is internally inconsistent: net movement "
                    f"{totals['balance']} does not equal funded+adjusted-spent"
                    f"-returned ({composed}). Refusing to report a balance."),
        )
    return totals


async def refresh_wallet_cache(session: AsyncSession, wallet_id: UUID) -> dict:
    """Rewrite the denormalised totals on `wallets` from the ledger.

    Called inside the same transaction as every movement, so the cache cannot
    lag the truth by more than the transaction it is part of.
    """
    totals = await derive_totals(session, wallet_id)
    await session.execute(
        text("""
            UPDATE wallets
               SET balance = :bal, total_funded = :f, total_spent = :s,
                   total_returned = :r, total_adjusted = :a,
                   last_transaction_at = :last, updated_at = NOW()
             WHERE id = :id
        """),
        {
            "bal": str(totals["balance"]), "f": str(totals["total_funded"]),
            "s": str(totals["total_spent"]), "r": str(totals["total_returned"]),
            "a": str(totals["total_adjusted"]),
            "last": totals["last_transaction_at"], "id": str(wallet_id),
        },
    )
    return totals


async def pending_expense_total(session: AsyncSession, wallet_id: UUID) -> Decimal:
    """Money already claimed but not yet approved.

    Subtracted from the balance to get what is genuinely available. Without
    this, an employee could submit five claims against the same N10,000 while
    all five sat unapproved.
    """
    row = (await session.execute(
        text("""SELECT COALESCE(SUM(amount), 0) AS total FROM wallet_expenses
                 WHERE wallet_id = :w AND status = 'PENDING'"""),
        {"w": str(wallet_id)},
    )).first()
    return money(row.total)


async def outstanding_amount(
    session: AsyncSession, wallet_id: UUID, *, totals: Optional[dict] = None,
    as_of: Optional[date] = None,
) -> Decimal:
    """The part of the balance that is overdue for accounting.

    "Outstanding" is not the same thing as "balance". Money issued this morning
    with 30 days to account for it is not outstanding; the same money on day 31
    is. Advances are treated as discharged oldest-first (FIFO), so:

        outstanding = max(0, overdue_funded - (spent + returned))

    A funding with no `account_by` date is never overdue -- if management did
    not set a deadline, the system does not invent one and then accuse someone
    of missing it.
    """
    totals = totals or await derive_totals(session, wallet_id)
    row = (await session.execute(
        text("""SELECT COALESCE(SUM(amount), 0) AS total FROM wallet_fundings
                 WHERE wallet_id = :w AND status = 'ISSUED'
                   AND account_by IS NOT NULL AND account_by < :d"""),
        {"w": str(wallet_id), "d": as_of or date.today()},
    )).first()
    overdue = money(row.total)
    discharged = totals["total_spent"] + totals["total_returned"]
    return max(ZERO, overdue - discharged)


async def verify_wallet_integrity(session: AsyncSession, wallet_id: UUID) -> dict:
    """Re-derive everything and compare it against the cache and the snapshots.

    Three independent things must agree: the cached columns on `wallets`, the
    sum over `wallet_ledger`, and the `balance_after` snapshot on the newest
    entry. They are written together and can only diverge through a bug or a
    manual database edit -- both of which someone needs to be told about.
    """
    wallet = await get_wallet(session, wallet_id)
    totals = await derive_totals(session, wallet_id)
    last = (await session.execute(
        text("""SELECT balance_after FROM wallet_ledger
                 WHERE wallet_id = :w ORDER BY sequence DESC LIMIT 1"""),
        {"w": str(wallet_id)},
    )).first()
    snapshot = money(last.balance_after) if last else ZERO

    problems = []
    if money(wallet["balance"]) != totals["balance"]:
        problems.append(
            f"cached balance {money(wallet['balance'])} != ledger "
            f"{totals['balance']}")
    if snapshot != totals["balance"]:
        problems.append(
            f"last balance_after snapshot {snapshot} != ledger "
            f"{totals['balance']}")
    return {
        "wallet_id": str(wallet_id),
        "wallet_number": wallet["wallet_number"],
        "ledger_balance": str(totals["balance"]),
        "cached_balance": str(money(wallet["balance"])),
        "last_snapshot": str(snapshot),
        "consistent": not problems,
        "problems": problems,
    }


# ---------------------------------------------------------------------------
# Appending to the ledger
# ---------------------------------------------------------------------------

async def append_entry(
    session: AsyncSession,
    *,
    wallet_id: UUID,
    entry_type: str,
    direction: str,
    amount: Decimal,
    description: str,
    occurred_on: date,
    source_type: Optional[str] = None,
    source_id: Optional[UUID] = None,
    reverses_entry_id: Optional[UUID] = None,
    journal_entry_id: Optional[UUID] = None,
    idempotency_key: Optional[str] = None,
    created_by: Optional[UUID] = None,
) -> tuple[UUID, Decimal]:
    """Append one movement. Returns (entry_id, balance_after).

    The caller MUST already hold the wallet lock (`lock_wallet`). This is not
    checked, because checking it would cost a query on every write; it is
    instead guaranteed by every call site living in this module.

    Does not commit: the ledger row lands in the caller's transaction, so the
    expense document and its balance effect either both happen or neither does.
    """
    amt = money(amount)
    if amt <= 0:
        raise HTTPException(
            status_code=400,
            detail="A wallet movement must be a positive amount.")
    if direction not in ("CREDIT", "DEBIT"):
        raise HTTPException(
            status_code=500, detail=f"Invalid ledger direction {direction!r}.")

    totals = await derive_totals(session, wallet_id)
    delta = amt if direction == "CREDIT" else -amt
    balance_after = totals["balance"] + delta

    entry_id = uuid4()
    await session.execute(
        text("""
            INSERT INTO wallet_ledger
                (id, wallet_id, entry_type, direction, amount, balance_after,
                 description, occurred_on, source_type, source_id,
                 reverses_entry_id, journal_entry_id, idempotency_key,
                 created_by)
            VALUES (:id, :w, :t, :d, :amt, :bal, :desc, :on, :st, :sid,
                    :rev, :je, :idem, :by)
        """),
        {
            "id": str(entry_id), "w": str(wallet_id), "t": entry_type,
            "d": direction, "amt": str(amt), "bal": str(balance_after),
            "desc": description, "on": occurred_on,
            "st": source_type, "sid": str(source_id) if source_id else None,
            "rev": str(reverses_entry_id) if reverses_entry_id else None,
            "je": str(journal_entry_id) if journal_entry_id else None,
            "idem": idempotency_key,
            "by": str(created_by) if created_by else None,
        },
    )
    await refresh_wallet_cache(session, wallet_id)
    return entry_id, balance_after


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

async def audit(
    session: AsyncSession,
    *,
    event_type: str,
    entity_type: Optional[str] = None,
    entity_id: Optional[UUID] = None,
    wallet_id: Optional[UUID] = None,
    amount: Optional[Decimal] = None,
    actor_user_id: Optional[UUID] = None,
    actor_label: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    result: Optional[str] = None,
    detail: Optional[dict] = None,
) -> None:
    """Write the append-only trail. Lands in the caller's transaction.

    Deliberately in the same transaction as the event: an audit row that
    survives a rolled-back expense would describe something that never
    happened, which is worse than no row at all.
    """
    await session.execute(
        text("""
            INSERT INTO wallet_audit_logs
                (id, event_type, entity_type, entity_id, wallet_id, amount,
                 actor_user_id, actor_label, ip_address, user_agent, result,
                 detail)
            VALUES (gen_random_uuid(), :e, :et, :eid, :w, :amt, :a, :al,
                    :ip, :ua, :res, CAST(:d AS JSONB))
        """),
        {
            "e": event_type, "et": entity_type,
            "eid": str(entity_id) if entity_id else None,
            "w": str(wallet_id) if wallet_id else None,
            "amt": str(money(amount)) if amount is not None else None,
            "a": str(actor_user_id) if actor_user_id else None,
            "al": actor_label,
            "ip": (ip_address or "")[:64] or None,
            "ua": (user_agent or "")[:500] or None,
            "res": result,
            "d": json.dumps(detail or {}, default=str),
        },
    )


# ---------------------------------------------------------------------------
# Authorization: who may see and do what
# ---------------------------------------------------------------------------

async def approver_grants(session: AsyncSession, user_id: UUID) -> list[dict]:
    rows = (await session.execute(
        text("""SELECT tier, max_amount, department, wallet_id, can_fund,
                       can_reconcile
                  FROM wallet_approvers
                 WHERE user_id = :u AND is_active = TRUE"""),
        {"u": str(user_id)},
    )).mappings().all()
    return [dict(r) for r in rows]


async def user_capabilities(session: AsyncSession, user) -> dict:
    """Everything the UI needs to know about what this user may do.

    Resolved server-side and sent to the client for rendering only. Every
    endpoint re-checks; nothing here is load-bearing for security.
    """
    is_admin = user.role == "admin"
    grants = await approver_grants(session, user.id)
    best = max((_TIER_RANK.get(g["tier"], 0) for g in grants), default=0)
    return {
        "is_admin": is_admin,
        "can_approve": is_admin or best >= _TIER_RANK["SUPERVISOR"],
        "can_fund": is_admin or any(g["can_fund"] for g in grants),
        "can_reconcile": is_admin or any(g["can_reconcile"] for g in grants),
        "can_administer": is_admin,
        "max_tier": "MANAGEMENT" if is_admin else _RANK_TIER.get(best, "SELF"),
        "grants": grants,
    }


async def assert_can_view_wallet(session: AsyncSession, user, wallet) -> None:
    """A wallet is private to its holder, its approvers, and administrators."""
    if user.role == "admin":
        return
    if str(wallet["user_id"]) == str(user.id):
        return
    for g in await approver_grants(session, user.id):
        if g["wallet_id"] and str(g["wallet_id"]) == str(wallet["id"]):
            return
        if g["wallet_id"] is None and (
                g["department"] is None
                or g["department"] == wallet["department"]):
            return
    raise HTTPException(
        status_code=403,
        detail="You do not have access to this wallet.")


async def assert_can_approve(
    session: AsyncSession, user, wallet, *, amount: Decimal,
    required_tier: str, submitted_by: UUID,
) -> str:
    """Raise unless this user may approve this specific expense.

    Two rules, in this order:

    1.  **Nobody approves their own spending.** Not supervisors, not the
        managing director. This is the single control that makes the rest of
        the module worth having, so it has no exception -- an administrator
        who holds a wallet needs a second person for anything above the
        self-approval band, and that is the intended cost.
    2.  The approver's granted tier must reach the tier the expense requires,
        and their per-grant ceiling must cover the amount.

    Returns the tier the approval was exercised at, for the audit record.
    """
    if str(submitted_by) == str(user.id):
        raise HTTPException(
            status_code=403,
            detail=("You cannot approve your own expense. Someone else with "
                    "approval authority must review it."),
        )

    need = _TIER_RANK.get(required_tier, 0)
    if user.role == "admin":
        return "MANAGEMENT"

    for g in await approver_grants(session, user.id):
        if _TIER_RANK.get(g["tier"], 0) < need:
            continue
        if g["max_amount"] is not None and money(g["max_amount"]) < amount:
            continue
        if g["department"] is not None and g["department"] != wallet["department"]:
            continue
        if g["wallet_id"] is not None and str(g["wallet_id"]) != str(wallet["id"]):
            continue
        return g["tier"]

    raise HTTPException(
        status_code=403,
        detail=(f"This expense requires {required_tier} approval for "
                f"{amount:,.2f}, which exceeds your authority."),
    )


async def assert_can_fund(session: AsyncSession, user, wallet=None) -> None:
    if user.role == "admin":
        return
    for g in await approver_grants(session, user.id):
        if not g["can_fund"]:
            continue
        if wallet is not None and g["wallet_id"] and str(g["wallet_id"]) != str(wallet["id"]):
            continue
        if wallet is not None and g["department"] and g["department"] != wallet["department"]:
            continue
        return
    raise HTTPException(
        status_code=403,
        detail="You are not authorised to fund wallets.")


async def assert_can_reconcile(session: AsyncSession, user) -> None:
    if user.role == "admin":
        return
    if any(g["can_reconcile"] for g in await approver_grants(session, user.id)):
        return
    raise HTTPException(
        status_code=403,
        detail="You are not authorised to settle wallet reconciliations.")


# ---------------------------------------------------------------------------
# Approval ladder
# ---------------------------------------------------------------------------

async def resolve_required_tier(
    session: AsyncSession, wallet, amount: Decimal,
) -> str:
    """Which tier must approve an expense of this size on this wallet.

    Rules are rows, resolved most-specific-first (WALLET, then WALLET_TYPE,
    then GLOBAL) so a wallet can be tightened without rewriting the company
    ladder.

    `self_approve_limit` is authoritative in BOTH directions when it is set.
    Below it, the holder spends without asking; above it, self-approval is off
    even if the company ladder would still allow it. Only honouring the
    generous direction would make "this person may spend up to N2,000 alone" a
    setting that cannot actually tighten anything -- the reason someone sets it
    is usually to restrict, not to widen. Leave it NULL to follow the company
    ladder exactly.
    """
    self_limit = wallet["self_approve_limit"]
    if self_limit is not None and amount <= money(self_limit):
        return "SELF"

    rows = (await session.execute(
        text("""
            SELECT scope, tier, min_amount, max_amount
              FROM wallet_approval_rules
             WHERE is_active = TRUE
               AND min_amount <= :amt
               AND (max_amount IS NULL OR max_amount > :amt)
               AND (scope = 'GLOBAL'
                    OR (scope = 'WALLET_TYPE' AND wallet_type = :wt)
                    OR (scope = 'WALLET' AND wallet_id = :wid))
             ORDER BY CASE scope WHEN 'WALLET' THEN 0
                                 WHEN 'WALLET_TYPE' THEN 1
                                 ELSE 2 END,
                      priority ASC, min_amount DESC
             LIMIT 1
        """),
        {"amt": str(amount), "wt": wallet["wallet_type"],
         "wid": str(wallet["id"])},
    )).mappings().first()

    if rows is None:
        # No rule covers this amount. Failing closed is the only safe answer:
        # a gap in the ladder must not become a self-approval.
        return "MANAGEMENT"

    tier = rows["tier"]
    if self_limit is not None and tier == "SELF":
        # An explicit per-wallet limit was set and this amount is above it.
        return "SUPERVISOR"
    return tier


# ---------------------------------------------------------------------------
# Spending limits
# ---------------------------------------------------------------------------

async def check_limits(
    session: AsyncSession, wallet, *, amount: Decimal, spent_on: date,
    category_id: UUID,
) -> list[str]:
    """Enforce the configured limits. Raises 400 on a breach.

    Returns a list of soft warnings (things worth telling the employee that do
    not justify refusing the record of money already spent).

    A note on why the balance check is hard and the rest are too: this system
    records money that has ALREADY left the employee's hand. Refusing the
    record does not un-spend it. But accepting an expense larger than the
    balance would mean the wallet claims the employee discharged more than
    they were ever given -- an arithmetic impossibility. If they really did
    spend their own money, that is a reimbursement, and the error says so.
    """
    warnings: list[str] = []
    totals = await derive_totals(session, wallet["id"])
    pending = await pending_expense_total(session, wallet["id"])
    available = totals["balance"] - pending

    if amount > available:
        raise HTTPException(
            status_code=400,
            detail=(
                f"This expense of {amount:,.2f} is more than the "
                f"{available:,.2f} available on wallet "
                f"{wallet['wallet_number']} "
                f"(balance {totals['balance']:,.2f} less {pending:,.2f} "
                f"already awaiting approval). If you spent your own money, "
                f"submit it as a reimbursement instead."),
        )

    if wallet["single_txn_limit"] is not None:
        cap = money(wallet["single_txn_limit"])
        if amount > cap:
            raise HTTPException(
                status_code=400,
                detail=(f"This wallet's single-transaction limit is "
                        f"{cap:,.2f}; this expense is {amount:,.2f}. It needs "
                        f"a limit change or a separate approval from "
                        f"management."),
            )

    if wallet["daily_limit"] is not None:
        cap = money(wallet["daily_limit"])
        row = (await session.execute(
            text("""SELECT COALESCE(SUM(amount), 0) AS total FROM wallet_expenses
                     WHERE wallet_id = :w AND spent_on = :d
                       AND status IN ('PENDING','APPROVED')"""),
            {"w": str(wallet["id"]), "d": spent_on},
        )).first()
        already = money(row.total)
        if already + amount > cap:
            raise HTTPException(
                status_code=400,
                detail=(f"Daily limit of {cap:,.2f} would be exceeded: "
                        f"{already:,.2f} is already recorded for {spent_on} "
                        f"and this expense is {amount:,.2f}."),
            )
        if already + amount > cap * Decimal("0.8"):
            warnings.append(
                f"You have now used {already + amount:,.2f} of your "
                f"{cap:,.2f} daily limit.")

    # The whole calendar month, not "up to spent_on". Checking only up to the
    # date would let an expense back-dated to the 5th ignore everything already
    # recorded between the 6th and today -- which is precisely how a monthly
    # cap gets quietly exceeded.
    month_start = spent_on.replace(day=1)
    month_end = (month_start.replace(year=month_start.year + 1, month=1)
                 if month_start.month == 12
                 else month_start.replace(month=month_start.month + 1))
    if wallet["monthly_limit"] is not None:
        cap = money(wallet["monthly_limit"])
        row = (await session.execute(
            text("""SELECT COALESCE(SUM(amount), 0) AS total FROM wallet_expenses
                     WHERE wallet_id = :w AND spent_on >= :s AND spent_on < :e
                       AND status IN ('PENDING','APPROVED')"""),
            {"w": str(wallet["id"]), "s": month_start, "e": month_end},
        )).first()
        already = money(row.total)
        if already + amount > cap:
            raise HTTPException(
                status_code=400,
                detail=(f"Monthly limit of {cap:,.2f} would be exceeded: "
                        f"{already:,.2f} is already recorded this month."),
            )

    # Category cap: the wallet-specific override wins over the category default.
    cap_row = (await session.execute(
        text("""
            SELECT COALESCE(cl.monthly_cap, c.default_monthly_cap) AS cap,
                   c.name AS cname
              FROM wallet_expense_categories c
              LEFT JOIN wallet_category_limits cl
                     ON cl.category_id = c.id AND cl.wallet_id = :w
             WHERE c.id = :c
        """),
        {"w": str(wallet["id"]), "c": str(category_id)},
    )).mappings().first()
    if cap_row and cap_row["cap"] is not None:
        cap = money(cap_row["cap"])
        row = (await session.execute(
            text("""SELECT COALESCE(SUM(amount), 0) AS total FROM wallet_expenses
                     WHERE wallet_id = :w AND category_id = :c
                       AND spent_on >= :s AND spent_on < :e
                       AND status IN ('PENDING','APPROVED')"""),
            {"w": str(wallet["id"]), "c": str(category_id),
             "s": month_start, "e": month_end},
        )).first()
        already = money(row.total)
        if already + amount > cap:
            raise HTTPException(
                status_code=400,
                detail=(f"The monthly cap for {cap_row['cname']} is "
                        f"{cap:,.2f} and {already:,.2f} has already been "
                        f"recorded this month."),
            )

    return warnings


# ---------------------------------------------------------------------------
# Funding
# ---------------------------------------------------------------------------

async def fund_wallet(
    session: AsyncSession,
    *,
    wallet_id: UUID,
    amount: Decimal,
    purpose: str,
    disbursement_method: str,
    funded_on: date,
    issued_by: UUID,
    source_account_code: str = ACC_BANK,
    disbursement_reference: Optional[str] = None,
    account_by: Optional[date] = None,
    notes: Optional[str] = None,
    fund_request_id: Optional[UUID] = None,
    idempotency_key: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """Record that money has been entrusted to the wallet holder.

    The money itself moved outside this application -- by transfer, cash or
    card. This writes the accountability record and, when accounting posting
    is switched on, the journal entry that puts the advance on the balance
    sheet as a claim on the employee rather than as a cost already incurred.
    """
    amt = money(amount)
    if amt <= 0:
        raise HTTPException(
            status_code=400, detail="Funding amount must be greater than zero.")

    wallet = await lock_wallet(session, wallet_id)
    if wallet["status"] in ("CLOSED", "FROZEN"):
        raise HTTPException(
            status_code=400,
            detail=(f"Wallet {wallet['wallet_number']} is {wallet['status']}; "
                    f"it cannot be funded until it is reactivated."))

    reference = _reference("FND", funded_on)
    funding_id = uuid4()

    await session.execute(
        text("""
            INSERT INTO wallet_fundings
                (id, funding_reference, wallet_id, amount, purpose,
                 disbursement_method, disbursement_reference,
                 source_account_code, funded_on, account_by, status,
                 fund_request_id, issued_by, notes)
            VALUES (:id, :ref, :w, :amt, :p, :m, :mref, :src, :on, :by_date,
                    'ISSUED', :req, :issuer, :notes)
        """),
        {
            "id": str(funding_id), "ref": reference, "w": str(wallet_id),
            "amt": str(amt), "p": purpose, "m": disbursement_method,
            "mref": disbursement_reference, "src": source_account_code,
            "on": funded_on, "by_date": account_by,
            "req": str(fund_request_id) if fund_request_id else None,
            "issuer": str(issued_by), "notes": notes,
        },
    )

    # Post the journal BEFORE appending the ledger row, so the wallet entry
    # can carry the journal id. Ledger rows are immutable -- a link written
    # afterwards could never be added.
    journal_id = await _post_funding(
        session, reference=reference, amount=amt, on=funded_on,
        source_account_code=source_account_code, purpose=purpose,
        created_by=issued_by,
    )

    entry_id, balance_after = await append_entry(
        session,
        wallet_id=wallet_id, entry_type="FUNDING", direction="CREDIT",
        amount=amt,
        description=f"Operational advance {reference}: {purpose}",
        occurred_on=funded_on, source_type="wallet_funding",
        source_id=funding_id, journal_entry_id=journal_id,
        idempotency_key=idempotency_key, created_by=issued_by,
    )

    await session.execute(
        text("""UPDATE wallet_fundings
                   SET ledger_entry_id = :e, journal_entry_id = :j
                 WHERE id = :id"""),
        {"e": str(entry_id), "j": str(journal_id) if journal_id else None,
         "id": str(funding_id)},
    )

    await audit(
        session, event_type="WALLET_FUNDED", entity_type="wallet_funding",
        entity_id=funding_id, wallet_id=wallet_id, amount=amt,
        actor_user_id=issued_by, result="ISSUED",
        ip_address=ip_address, user_agent=user_agent,
        detail={"reference": reference, "purpose": purpose,
                "disbursement_method": disbursement_method,
                "disbursement_reference": disbursement_reference,
                "balance_after": str(balance_after)},
    )

    return {
        "id": str(funding_id),
        "funding_reference": reference,
        "amount": str(amt),
        "balance_after": str(balance_after),
        "ledger_entry_id": str(entry_id),
        "journal_entry_id": str(journal_id) if journal_id else None,
    }


async def _post_funding(
    session, *, reference: str, amount: Decimal, on: date,
    source_account_code: str, purpose: str, created_by: UUID,
) -> Optional[UUID]:
    """Dr Staff Operational Advances / Cr the account the money came from."""
    if not await _should_post(
            session, source_module=SOURCE_MODULE,
            source_reference=reference, on=on):
        return None
    return await post_entry(
        session,
        entry_date=on,
        description=f"Staff operational advance {reference}",
        source_module=SOURCE_MODULE, source_reference=reference,
        lines=[
            Line(ACC_STAFF_ADVANCES, debit=amount, description=purpose),
            Line(source_account_code, credit=amount,
                 description=f"Advance issued ({reference})"),
        ],
        created_by=created_by,
    )


async def reverse_funding(
    session: AsyncSession, *, funding_id: UUID, reason: str, actor_id: UUID,
) -> dict:
    """Undo an advance that was recorded in error.

    Appends the mirror entry; the original stays exactly as it was. Used when
    the transfer failed, or the amount was keyed wrongly -- in which case the
    correct sequence is reverse, then fund again with the right figure.
    """
    funding = (await session.execute(
        text("SELECT * FROM wallet_fundings WHERE id = :id"),
        {"id": str(funding_id)},
    )).mappings().first()
    if funding is None:
        raise HTTPException(status_code=404, detail="Funding not found.")
    if funding["status"] == "REVERSED":
        raise HTTPException(
            status_code=400, detail="This funding has already been reversed.")

    await lock_wallet(session, funding["wallet_id"])
    amt = money(funding["amount"])

    entry_id, balance_after = await append_entry(
        session,
        wallet_id=funding["wallet_id"], entry_type="REVERSAL",
        direction="DEBIT", amount=amt,
        description=(f"Reversal of advance {funding['funding_reference']}: "
                     f"{reason}"),
        occurred_on=date.today(), source_type="wallet_funding",
        source_id=funding_id,
        reverses_entry_id=funding["ledger_entry_id"], created_by=actor_id,
    )
    await session.execute(
        text("UPDATE wallet_fundings SET status = 'REVERSED' WHERE id = :id"),
        {"id": str(funding_id)},
    )

    if funding["journal_entry_id"]:
        from app.services.ledger import reverse_entry
        await reverse_entry(
            session, entry_id=funding["journal_entry_id"],
            reason=f"Wallet funding {funding['funding_reference']} reversed: "
                   f"{reason}",
            created_by=actor_id,
        )

    return {
        "reversed": funding["funding_reference"],
        "ledger_entry_id": str(entry_id),
        "balance_after": str(balance_after),
    }


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------

async def submit_expense(
    session: AsyncSession,
    *,
    wallet_id: UUID,
    category_id: UUID,
    amount: Decimal,
    spent_on: date,
    purpose: str,
    submitted_by: UUID,
    description: Optional[str] = None,
    vendor: Optional[str] = None,
    location: Optional[str] = None,
    payment_method: str = "CASH",
    department: Optional[str] = None,
    project_reference: Optional[str] = None,
    customer_id: Optional[UUID] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    gps_accuracy: Optional[float] = None,
    notes: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict:
    """Record money the holder has spent. Does NOT move the balance yet.

    The balance moves on approval, because until the company accepts the
    expense it has not accepted that the advance was discharged. Expenses in
    the SELF band are approved in the same transaction, which is what makes
    the common case a one-step action for the employee.
    """
    amt = money(amount)
    if amt <= 0:
        raise HTTPException(
            status_code=400, detail="An expense must be greater than zero.")
    if spent_on > date.today() + timedelta(days=1):
        # One day of slack for timezone skew on a phone; beyond that a future
        # date is a typo or an attempt to park spending in a later period.
        raise HTTPException(
            status_code=400,
            detail="An expense cannot be dated in the future.")

    wallet = await lock_wallet(session, wallet_id)
    _assert_spendable(wallet)

    category = (await session.execute(
        text("""SELECT id, name, requires_receipt, is_active
                  FROM wallet_expense_categories WHERE id = :c"""),
        {"c": str(category_id)},
    )).mappings().first()
    if category is None or not category["is_active"]:
        raise HTTPException(
            status_code=400, detail="That expense category is not available.")

    warnings = await check_limits(
        session, wallet, amount=amt, spent_on=spent_on, category_id=category_id)

    required_tier = await resolve_required_tier(session, wallet, amt)
    reference = _reference("EXP", spent_on)
    expense_id = uuid4()

    await session.execute(
        text("""
            INSERT INTO wallet_expenses
                (id, expense_reference, wallet_id, category_id, amount,
                 spent_on, purpose, description, vendor, location,
                 payment_method, department, project_reference, customer_id,
                 latitude, longitude, gps_accuracy, status, required_tier,
                 submitted_by, idempotency_key, notes)
            VALUES (:id, :ref, :w, :c, :amt, :on, :p, :desc, :v, :loc,
                    :pm, :dept, :proj, :cust, :lat, :lng, :acc,
                    'PENDING', :tier, :by, :idem, :notes)
        """),
        {
            "id": str(expense_id), "ref": reference, "w": str(wallet_id),
            "c": str(category_id), "amt": str(amt), "on": spent_on,
            "p": purpose, "desc": description, "v": vendor, "loc": location,
            "pm": payment_method,
            "dept": department or wallet["department"],
            "proj": project_reference,
            "cust": str(customer_id) if customer_id else None,
            "lat": latitude, "lng": longitude, "acc": gps_accuracy,
            "tier": required_tier, "by": str(submitted_by),
            "idem": idempotency_key, "notes": notes,
        },
    )

    await audit(
        session, event_type="EXPENSE_SUBMITTED", entity_type="wallet_expense",
        entity_id=expense_id, wallet_id=wallet_id, amount=amt,
        actor_user_id=submitted_by, result="PENDING",
        ip_address=ip_address, user_agent=user_agent,
        detail={"reference": reference, "category": category["name"],
                "required_tier": required_tier, "purpose": purpose,
                # Device-reported position, recorded as submitted. It is
                # evidence about where the phone said it was, never proof of
                # where the person was, and nothing depends on it.
                "latitude": latitude, "longitude": longitude,
                "gps_accuracy": gps_accuracy},
    )

    auto_approved = False
    if required_tier == "SELF":
        # Within the band the employee is trusted to spend without asking, so
        # there is nobody to wait for. The record still carries who decided it
        # and when -- "the policy did" is not an acceptable audit answer.
        #
        # Note this path does NOT block on a missing receipt, unlike an
        # approval by another person. It cannot: the employee submits the
        # expense and photographs the receipt in the same interaction, and
        # refusing here would make the one-minute flow impossible. Section 22
        # asks for a missing receipt to be FLAGGED rather than to block, and
        # detect_expense_anomalies below raises exactly that flag, which
        # sweep_missing_receipts then surfaces to management.
        await _apply_approval(
            session, expense_id=expense_id, wallet=wallet, amount=amt,
            spent_on=spent_on, reference=reference,
            category_id=category_id, purpose=purpose,
            decided_by=submitted_by, decision_note="Within self-approval limit",
            tier_exercised="SELF",
        )
        auto_approved = True

    flags = await detect_expense_anomalies(
        session, expense_id=expense_id, wallet=wallet, amount=amt,
        category_id=category_id, spent_on=spent_on)

    totals = await derive_totals(session, wallet_id)
    return {
        "id": str(expense_id),
        "expense_reference": reference,
        "amount": str(amt),
        "status": "APPROVED" if auto_approved else "PENDING",
        "required_tier": required_tier,
        "auto_approved": auto_approved,
        "requires_receipt": category["requires_receipt"],
        "balance": str(totals["balance"]),
        "warnings": warnings,
        "flags": flags,
    }


async def _apply_approval(
    session: AsyncSession, *, expense_id: UUID, wallet, amount: Decimal,
    spent_on: date, reference: str, category_id: UUID, purpose: str,
    decided_by: UUID, decision_note: Optional[str], tier_exercised: str,
) -> tuple[UUID, Decimal, Optional[UUID]]:
    """The one place an approved expense becomes a balance movement."""
    account_row = (await session.execute(
        text("""SELECT gl_account_code, name FROM wallet_expense_categories
                 WHERE id = :c"""),
        {"c": str(category_id)},
    )).mappings().first()
    expense_account = (account_row["gl_account_code"]
                       if account_row and account_row["gl_account_code"]
                       else ACC_FALLBACK_EXPENSE)

    journal_id = None
    if await _should_post(
            session, source_module=SOURCE_MODULE,
            source_reference=reference, on=spent_on):
        journal_id = await post_entry(
            session,
            entry_date=spent_on,
            description=f"Wallet expense {reference}: {purpose}",
            source_module=SOURCE_MODULE, source_reference=reference,
            lines=[
                Line(expense_account, debit=amount, description=purpose),
                Line(ACC_STAFF_ADVANCES, credit=amount,
                     description=(f"Advance discharged by "
                                  f"{wallet['wallet_number']}")),
            ],
            created_by=decided_by,
        )

    # Ledger row last, carrying the journal link: it is immutable once
    # written, so anything it must reference has to exist first.
    entry_id, balance_after = await append_entry(
        session,
        wallet_id=wallet["id"], entry_type="EXPENSE", direction="DEBIT",
        amount=amount,
        description=f"Expense {reference}: {purpose}",
        occurred_on=spent_on, source_type="wallet_expense",
        source_id=expense_id, journal_entry_id=journal_id,
        created_by=decided_by,
    )

    await session.execute(
        text("""
            UPDATE wallet_expenses
               SET status = 'APPROVED', decided_by = :by, decided_at = NOW(),
                   decision_note = :note, ledger_entry_id = :e,
                   journal_entry_id = :j, updated_at = NOW()
             WHERE id = :id
        """),
        {"by": str(decided_by), "note": decision_note, "e": str(entry_id),
         "j": str(journal_id) if journal_id else None, "id": str(expense_id)},
    )
    await audit(
        session, event_type="EXPENSE_APPROVED", entity_type="wallet_expense",
        entity_id=expense_id, wallet_id=wallet["id"], amount=amount,
        actor_user_id=decided_by, result="APPROVED",
        detail={"reference": reference, "tier_exercised": tier_exercised,
                "balance_after": str(balance_after),
                "expense_account": expense_account},
    )
    return entry_id, balance_after, journal_id


async def decide_expense(
    session: AsyncSession, *, expense_id: UUID, approve: bool, user,
    note: Optional[str] = None,
) -> dict:
    """Approve or reject a pending expense.

    Takes the wallet lock BEFORE re-reading the expense, so two approvers
    clicking at once cannot both apply the balance movement.
    """
    head = (await session.execute(
        text("SELECT wallet_id FROM wallet_expenses WHERE id = :id"),
        {"id": str(expense_id)},
    )).first()
    if head is None:
        raise HTTPException(status_code=404, detail="Expense not found.")

    wallet = await lock_wallet(session, head.wallet_id)
    expense = (await session.execute(
        text("SELECT * FROM wallet_expenses WHERE id = :id"),
        {"id": str(expense_id)},
    )).mappings().first()

    if expense["status"] != "PENDING":
        raise HTTPException(
            status_code=400,
            detail=(f"Expense {expense['expense_reference']} has already been "
                    f"{expense['status'].lower()}."))

    amt = money(expense["amount"])
    tier = await assert_can_approve(
        session, user, wallet, amount=amt,
        required_tier=expense["required_tier"],
        submitted_by=expense["submitted_by"])

    if not approve:
        await session.execute(
            text("""UPDATE wallet_expenses
                       SET status = 'REJECTED', decided_by = :by,
                           decided_at = NOW(), decision_note = :note,
                           updated_at = NOW()
                     WHERE id = :id"""),
            {"by": str(user.id), "note": note, "id": str(expense_id)},
        )
        await audit(
            session, event_type="EXPENSE_REJECTED",
            entity_type="wallet_expense", entity_id=expense_id,
            wallet_id=wallet["id"], amount=amt, actor_user_id=user.id,
            result="REJECTED",
            detail={"reference": expense["expense_reference"], "note": note},
        )
        # A rejected expense never moved the balance, so nothing is unwound.
        # The employee still holds the money and still answers for it.
        return {
            "id": str(expense_id),
            "expense_reference": expense["expense_reference"],
            "status": "REJECTED",
        }

    # Re-check the receipt requirement at approval, not only at submission: a
    # category can be made receipt-mandatory between the two.
    await _assert_receipt_present(session, expense_id, expense["category_id"])

    _, balance_after, journal_id = await _apply_approval(
        session, expense_id=expense_id, wallet=wallet, amount=amt,
        spent_on=expense["spent_on"], reference=expense["expense_reference"],
        category_id=expense["category_id"], purpose=expense["purpose"],
        decided_by=user.id, decision_note=note, tier_exercised=tier,
    )
    return {
        "id": str(expense_id),
        "expense_reference": expense["expense_reference"],
        "status": "APPROVED",
        "balance_after": str(balance_after),
        "journal_entry_id": str(journal_id) if journal_id else None,
    }


async def _assert_receipt_present(
    session: AsyncSession, expense_id: UUID, category_id: UUID,
) -> None:
    cat = (await session.execute(
        text("""SELECT requires_receipt, name FROM wallet_expense_categories
                 WHERE id = :c"""),
        {"c": str(category_id)},
    )).mappings().first()
    if not cat or not cat["requires_receipt"]:
        return
    row = (await session.execute(
        text("SELECT 1 FROM wallet_receipts WHERE expense_id = :e LIMIT 1"),
        {"e": str(expense_id)},
    )).first()
    if row is None:
        raise HTTPException(
            status_code=400,
            detail=(f"{cat['name']} expenses require a receipt. Attach the "
                    f"receipt photograph before approving."),
        )


async def reverse_expense(
    session: AsyncSession, *, expense_id: UUID, reason: str, actor_id: UUID,
) -> dict:
    """Correct an approved expense by appending its mirror image.

    Section 5 of the specification: never delete, always reverse. The original
    expense and its ledger entry stay untouched and visible; the wallet
    balance returns to what it was; the employee is accountable again for the
    amount. If the real figure was different, a corrected expense is submitted
    separately -- which keeps both numbers, and the reason, on the record.
    """
    head = (await session.execute(
        text("SELECT wallet_id FROM wallet_expenses WHERE id = :id"),
        {"id": str(expense_id)},
    )).first()
    if head is None:
        raise HTTPException(status_code=404, detail="Expense not found.")

    wallet = await lock_wallet(session, head.wallet_id)
    expense = (await session.execute(
        text("SELECT * FROM wallet_expenses WHERE id = :id"),
        {"id": str(expense_id)},
    )).mappings().first()

    if expense["status"] != "APPROVED":
        raise HTTPException(
            status_code=400,
            detail=(f"Only an approved expense can be reversed; this one is "
                    f"{expense['status'].lower()}."))

    amt = money(expense["amount"])
    entry_id, balance_after = await append_entry(
        session,
        wallet_id=wallet["id"], entry_type="REVERSAL", direction="CREDIT",
        amount=amt,
        description=(f"Reversal of expense {expense['expense_reference']}: "
                     f"{reason}"),
        occurred_on=date.today(), source_type="wallet_expense",
        source_id=expense_id, reverses_entry_id=expense["ledger_entry_id"],
        created_by=actor_id,
    )
    await session.execute(
        text("""UPDATE wallet_expenses SET status = 'REVERSED',
                       decision_note = :note, updated_at = NOW()
                 WHERE id = :id"""),
        {"note": f"Reversed: {reason}", "id": str(expense_id)},
    )

    if expense["journal_entry_id"]:
        from app.services.ledger import reverse_entry
        await reverse_entry(
            session, entry_id=expense["journal_entry_id"],
            reason=f"Wallet expense {expense['expense_reference']} reversed: "
                   f"{reason}",
            created_by=actor_id,
        )

    await audit(
        session, event_type="EXPENSE_REVERSED", entity_type="wallet_expense",
        entity_id=expense_id, wallet_id=wallet["id"], amount=amt,
        actor_user_id=actor_id, result="REVERSED",
        detail={"reference": expense["expense_reference"], "reason": reason,
                "balance_after": str(balance_after)},
    )
    return {
        "id": str(expense_id),
        "expense_reference": expense["expense_reference"],
        "status": "REVERSED",
        "ledger_entry_id": str(entry_id),
        "balance_after": str(balance_after),
    }


# ---------------------------------------------------------------------------
# Receipts
# ---------------------------------------------------------------------------

async def attach_receipt(
    session: AsyncSession, *, filename: str, content_type: str,
    content: bytes, uploaded_by: UUID,
    expense_id: Optional[UUID] = None,
    reimbursement_id: Optional[UUID] = None,
) -> dict:
    """Store a receipt and report whether its image has been seen before.

    The duplicate check is on the SHA-256 of the bytes, so the same photograph
    submitted against two expenses is caught even if it is renamed. It does
    not block the upload -- a legitimate reason exists (one receipt covering
    two line items) -- it raises a flag for a human.
    """
    if expense_id is None and reimbursement_id is None:
        raise HTTPException(
            status_code=400,
            detail="A receipt must belong to an expense or a reimbursement.")
    if content_type not in ALLOWED_RECEIPT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(f"Receipts must be an image or PDF. "
                    f"{content_type or 'unknown type'} is not accepted."))
    if not content:
        raise HTTPException(status_code=400, detail="The receipt file is empty.")
    if len(content) > MAX_RECEIPT_BYTES:
        raise HTTPException(
            status_code=400,
            detail=(f"Receipt is {len(content) / 1_048_576:.1f}MB; the limit "
                    f"is {MAX_RECEIPT_BYTES // 1_048_576}MB. Photograph it at "
                    f"a lower resolution."))

    digest = hashlib.sha256(content).hexdigest()
    receipt_id = uuid4()
    await session.execute(
        text("""
            INSERT INTO wallet_receipts
                (id, expense_id, reimbursement_id, filename, content_type,
                 byte_size, sha256, content, uploaded_by)
            VALUES (:id, :e, :r, :fn, :ct, :sz, :h, :blob, :by)
        """),
        {
            "id": str(receipt_id),
            "e": str(expense_id) if expense_id else None,
            "r": str(reimbursement_id) if reimbursement_id else None,
            "fn": filename[:255], "ct": content_type, "sz": len(content),
            "h": digest, "blob": content, "by": str(uploaded_by),
        },
    )

    # Look for the same image elsewhere AFTER inserting, excluding this exact
    # row. Excluding by receipt id rather than by expense id keeps the query
    # free of nullable comparisons, and still catches a receipt reused across
    # an expense and a reimbursement.
    prior = (await session.execute(
        text("""SELECT COALESCE(e.expense_reference,
                                m.reimbursement_reference) AS ref
                  FROM wallet_receipts r
                  LEFT JOIN wallet_expenses e ON e.id = r.expense_id
                  LEFT JOIN wallet_reimbursements m ON m.id = r.reimbursement_id
                 WHERE r.sha256 = :h AND r.id <> :self
                 LIMIT 1"""),
        {"h": digest, "self": str(receipt_id)},
    )).mappings().first()

    duplicate_of = None
    if prior is not None and expense_id is not None:
        duplicate_of = prior["ref"]
        wallet_row = (await session.execute(
            text("SELECT wallet_id FROM wallet_expenses WHERE id = :e"),
            {"e": str(expense_id)},
        )).first()
        await raise_flag(
            session, wallet_id=wallet_row.wallet_id, expense_id=expense_id,
            flag_type="DUPLICATE_RECEIPT", severity="HIGH",
            message=(f"This receipt image was already submitted with expense "
                     f"{duplicate_of}. It may legitimately cover both, or it "
                     f"may have been claimed twice."),
            detail={"sha256": digest, "previous_expense": duplicate_of},
        )

    await audit(
        session, event_type="RECEIPT_ATTACHED", entity_type="wallet_receipt",
        entity_id=receipt_id,
        actor_user_id=uploaded_by, result="STORED",
        detail={"filename": filename, "bytes": len(content),
                "sha256": digest, "duplicate_of": duplicate_of},
    )
    return {
        "id": str(receipt_id),
        "filename": filename,
        "byte_size": len(content),
        "sha256": digest,
        "duplicate_of": duplicate_of,
    }


# ---------------------------------------------------------------------------
# Returning unused money
# ---------------------------------------------------------------------------

async def declare_return(
    session: AsyncSession, *, wallet_id: UUID, amount: Decimal,
    returned_on: date, method: str, declared_by: UUID,
    reference: Optional[str] = None, note: Optional[str] = None,
    destination_account_code: str = ACC_CASH,
) -> dict:
    """The holder says they have handed money back. Balance does not move yet.

    Deliberately two-step (section 16 asks for confirmation of the returned
    amount). Until finance confirms receipt, the employee still has the money
    and must still answer for it -- a self-declared return that moved the
    balance immediately would be a way to zero an accountability figure
    without anyone checking.
    """
    amt = money(amount)
    if amt <= 0:
        raise HTTPException(
            status_code=400, detail="A return must be greater than zero.")

    wallet = await lock_wallet(session, wallet_id)
    totals = await derive_totals(session, wallet_id)
    pending_returns = (await session.execute(
        text("""SELECT COALESCE(SUM(amount), 0) AS total FROM wallet_returns
                 WHERE wallet_id = :w AND status = 'DECLARED'"""),
        {"w": str(wallet_id)},
    )).first()
    already = money(pending_returns.total)
    if amt + already > totals["balance"]:
        raise HTTPException(
            status_code=400,
            detail=(f"You cannot return {amt:,.2f}: the wallet balance is "
                    f"{totals['balance']:,.2f} and {already:,.2f} is already "
                    f"declared and awaiting confirmation."))

    ref = _reference("RTN", returned_on)
    return_id = uuid4()
    await session.execute(
        text("""
            INSERT INTO wallet_returns
                (id, return_reference, wallet_id, amount, returned_on, method,
                 reference, destination_account_code, status, declared_by, note)
            VALUES (:id, :ref, :w, :amt, :on, :m, :r, :dst, 'DECLARED', :by,
                    :note)
        """),
        {"id": str(return_id), "ref": ref, "w": str(wallet_id),
         "amt": str(amt), "on": returned_on, "m": method, "r": reference,
         "dst": destination_account_code, "by": str(declared_by),
         "note": note},
    )
    await audit(
        session, event_type="RETURN_DECLARED", entity_type="wallet_return",
        entity_id=return_id, wallet_id=wallet_id, amount=amt,
        actor_user_id=declared_by, result="DECLARED",
        detail={"reference": ref, "method": method},
    )
    return {"id": str(return_id), "return_reference": ref,
            "amount": str(amt), "status": "DECLARED"}


async def confirm_return(
    session: AsyncSession, *, return_id: UUID, confirm: bool, user,
    note: Optional[str] = None,
) -> dict:
    """Finance confirms the money is back. This is what moves the balance."""
    head = (await session.execute(
        text("SELECT wallet_id FROM wallet_returns WHERE id = :id"),
        {"id": str(return_id)},
    )).first()
    if head is None:
        raise HTTPException(status_code=404, detail="Return not found.")

    wallet = await lock_wallet(session, head.wallet_id)
    ret = (await session.execute(
        text("SELECT * FROM wallet_returns WHERE id = :id"),
        {"id": str(return_id)},
    )).mappings().first()
    if ret["status"] != "DECLARED":
        raise HTTPException(
            status_code=400,
            detail=f"This return is already {ret['status'].lower()}.")
    if str(ret["declared_by"]) == str(user.id) and user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail=("You cannot confirm your own return; the person receiving "
                    "the money must confirm it."))

    amt = money(ret["amount"])
    if not confirm:
        await session.execute(
            text("""UPDATE wallet_returns
                       SET status = 'REJECTED', confirmed_by = :by,
                           confirmed_at = NOW(), note = :note
                     WHERE id = :id"""),
            {"by": str(user.id), "note": note, "id": str(return_id)},
        )
        await audit(
            session, event_type="RETURN_REJECTED", entity_type="wallet_return",
            entity_id=return_id, wallet_id=wallet["id"], amount=amt,
            actor_user_id=user.id, result="REJECTED", detail={"note": note},
        )
        return {"id": str(return_id), "status": "REJECTED"}

    journal_id = None
    if await _should_post(
            session, source_module=SOURCE_MODULE,
            source_reference=ret["return_reference"], on=ret["returned_on"]):
        journal_id = await post_entry(
            session,
            entry_date=ret["returned_on"],
            description=f"Unused advance returned {ret['return_reference']}",
            source_module=SOURCE_MODULE,
            source_reference=ret["return_reference"],
            lines=[
                Line(ret["destination_account_code"], debit=amt,
                     description="Unused operational advance returned"),
                Line(ACC_STAFF_ADVANCES, credit=amt,
                     description=f"Advance repaid by {wallet['wallet_number']}"),
            ],
            created_by=user.id,
        )

    entry_id, balance_after = await append_entry(
        session,
        wallet_id=wallet["id"], entry_type="RETURN", direction="DEBIT",
        amount=amt,
        description=f"Unused funds returned ({ret['return_reference']})",
        occurred_on=ret["returned_on"], source_type="wallet_return",
        source_id=return_id, journal_entry_id=journal_id, created_by=user.id,
    )

    await session.execute(
        text("""UPDATE wallet_returns
                   SET status = 'CONFIRMED', confirmed_by = :by,
                       confirmed_at = NOW(), ledger_entry_id = :e,
                       journal_entry_id = :j, note = COALESCE(:note, note)
                 WHERE id = :id"""),
        {"by": str(user.id), "e": str(entry_id),
         "j": str(journal_id) if journal_id else None, "note": note,
         "id": str(return_id)},
    )
    await audit(
        session, event_type="RETURN_CONFIRMED", entity_type="wallet_return",
        entity_id=return_id, wallet_id=wallet["id"], amount=amt,
        actor_user_id=user.id, result="CONFIRMED",
        detail={"reference": ret["return_reference"],
                "balance_after": str(balance_after)},
    )
    return {
        "id": str(return_id), "status": "CONFIRMED",
        "balance_after": str(balance_after),
        "journal_entry_id": str(journal_id) if journal_id else None,
    }


# ---------------------------------------------------------------------------
# Top-up requests
# ---------------------------------------------------------------------------

async def request_funds(
    session: AsyncSession, *, wallet_id: UUID, amount: Decimal, reason: str,
    urgency: str, requested_by: UUID,
) -> dict:
    amt = money(amount)
    if amt <= 0:
        raise HTTPException(
            status_code=400, detail="Request an amount greater than zero.")

    wallet = await get_wallet(session, wallet_id)
    _assert_spendable(wallet)
    totals = await derive_totals(session, wallet_id)

    ref = _reference("REQ")
    request_id = uuid4()
    await session.execute(
        text("""
            INSERT INTO wallet_fund_requests
                (id, request_reference, wallet_id, amount_requested, reason,
                 urgency, balance_at_request, status, requested_by)
            VALUES (:id, :ref, :w, :amt, :r, :u, :bal, 'PENDING', :by)
        """),
        {"id": str(request_id), "ref": ref, "w": str(wallet_id),
         "amt": str(amt), "r": reason, "u": urgency,
         "bal": str(totals["balance"]), "by": str(requested_by)},
    )
    await audit(
        session, event_type="FUND_REQUESTED",
        entity_type="wallet_fund_request", entity_id=request_id,
        wallet_id=wallet_id, amount=amt, actor_user_id=requested_by,
        result="PENDING",
        detail={"reference": ref, "urgency": urgency, "reason": reason},
    )
    flags = await detect_request_anomalies(session, wallet_id=wallet_id,
                                           request_id=request_id)
    return {"id": str(request_id), "request_reference": ref,
            "amount_requested": str(amt), "status": "PENDING",
            "balance_at_request": str(totals["balance"]), "flags": flags}


async def decide_fund_request(
    session: AsyncSession, *, request_id: UUID, decision: str, user,
    amount_approved: Optional[Decimal] = None, note: Optional[str] = None,
) -> dict:
    """APPROVE / PARTIALLY_APPROVE / REJECT / INFO_REQUESTED.

    Approving does NOT fund the wallet. It authorises someone to hand over
    money; the funding is recorded separately once it actually has been. This
    separation is on purpose -- an approved request that auto-funded would put
    money in the ledger that nobody had yet transferred.
    """
    req = (await session.execute(
        text("SELECT * FROM wallet_fund_requests WHERE id = :id"),
        {"id": str(request_id)},
    )).mappings().first()
    if req is None:
        raise HTTPException(status_code=404, detail="Fund request not found.")
    if req["status"] not in ("PENDING", "INFO_REQUESTED"):
        raise HTTPException(
            status_code=400,
            detail=f"This request is already {req['status'].lower()}.")
    if str(req["requested_by"]) == str(user.id):
        raise HTTPException(
            status_code=403,
            detail="You cannot decide your own request for funds.")

    wallet = await get_wallet(session, req["wallet_id"])
    await assert_can_fund(session, user, wallet)

    if decision not in ("APPROVED", "PARTIALLY_APPROVED", "REJECTED",
                        "INFO_REQUESTED"):
        raise HTTPException(status_code=400, detail="Unknown decision.")

    approved = None
    if decision == "APPROVED":
        approved = money(req["amount_requested"])
    elif decision == "PARTIALLY_APPROVED":
        if amount_approved is None:
            raise HTTPException(
                status_code=400,
                detail="State how much is approved for a partial approval.")
        approved = money(amount_approved)
        if approved <= 0 or approved >= money(req["amount_requested"]):
            raise HTTPException(
                status_code=400,
                detail=("A partial approval must be more than zero and less "
                        "than the amount requested."))

    await session.execute(
        text("""UPDATE wallet_fund_requests
                   SET status = :s, amount_approved = :amt, decided_by = :by,
                       decided_at = NOW(), decision_note = :note
                 WHERE id = :id"""),
        {"s": decision, "amt": str(approved) if approved is not None else None,
         "by": str(user.id), "note": note, "id": str(request_id)},
    )
    await audit(
        session, event_type=f"FUND_REQUEST_{decision}",
        entity_type="wallet_fund_request", entity_id=request_id,
        wallet_id=req["wallet_id"],
        amount=approved, actor_user_id=user.id, result=decision,
        detail={"reference": req["request_reference"], "note": note},
    )
    return {
        "id": str(request_id),
        "request_reference": req["request_reference"],
        "status": decision,
        "amount_approved": str(approved) if approved is not None else None,
    }


# ---------------------------------------------------------------------------
# Reimbursements -- the employee spent their own money
# ---------------------------------------------------------------------------

async def submit_reimbursement(
    session: AsyncSession, *, user_id: UUID, category_id: UUID,
    amount: Decimal, spent_on: date, purpose: str,
    description: Optional[str] = None, vendor: Optional[str] = None,
    department: Optional[str] = None, wallet_id: Optional[UUID] = None,
) -> dict:
    amt = money(amount)
    if amt <= 0:
        raise HTTPException(
            status_code=400, detail="A claim must be greater than zero.")
    if spent_on > date.today() + timedelta(days=1):
        raise HTTPException(
            status_code=400, detail="A claim cannot be dated in the future.")

    ref = _reference("RMB", spent_on)
    reimbursement_id = uuid4()
    await session.execute(
        text("""
            INSERT INTO wallet_reimbursements
                (id, reimbursement_reference, user_id, wallet_id, category_id,
                 amount, spent_on, purpose, description, vendor, department,
                 status)
            VALUES (:id, :ref, :u, :w, :c, :amt, :on, :p, :desc, :v, :dept,
                    'PENDING')
        """),
        {"id": str(reimbursement_id), "ref": ref, "u": str(user_id),
         "w": str(wallet_id) if wallet_id else None, "c": str(category_id),
         "amt": str(amt), "on": spent_on, "p": purpose, "desc": description,
         "v": vendor, "dept": department},
    )
    await audit(
        session, event_type="REIMBURSEMENT_SUBMITTED",
        entity_type="wallet_reimbursement", entity_id=reimbursement_id,
        wallet_id=wallet_id, amount=amt, actor_user_id=user_id,
        result="PENDING", detail={"reference": ref, "purpose": purpose},
    )
    return {"id": str(reimbursement_id), "reimbursement_reference": ref,
            "amount": str(amt), "status": "PENDING"}


async def decide_reimbursement(
    session: AsyncSession, *, reimbursement_id: UUID, approve: bool, user,
    amount_approved: Optional[Decimal] = None, note: Optional[str] = None,
) -> dict:
    """Accept the claim and book what the company now owes the employee.

    Posting on approval, not on payment, is what makes the liability visible
    between the two. Dr the expense (the cost was incurred when the money was
    spent) / Cr Staff Reimbursements Payable.
    """
    claim = (await session.execute(
        text("SELECT * FROM wallet_reimbursements WHERE id = :id"),
        {"id": str(reimbursement_id)},
    )).mappings().first()
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found.")
    if claim["status"] != "PENDING":
        raise HTTPException(
            status_code=400,
            detail=f"This claim is already {claim['status'].lower()}.")
    if str(claim["user_id"]) == str(user.id):
        raise HTTPException(
            status_code=403, detail="You cannot approve your own claim.")

    caps = await user_capabilities(session, user)
    if not caps["can_approve"]:
        raise HTTPException(
            status_code=403,
            detail="You are not authorised to decide reimbursement claims.")

    if not approve:
        await session.execute(
            text("""UPDATE wallet_reimbursements
                       SET status = 'REJECTED', decided_by = :by,
                           decided_at = NOW(), decision_note = :note
                     WHERE id = :id"""),
            {"by": str(user.id), "note": note, "id": str(reimbursement_id)},
        )
        await audit(
            session, event_type="REIMBURSEMENT_REJECTED",
            entity_type="wallet_reimbursement", entity_id=reimbursement_id,
            amount=money(claim["amount"]), actor_user_id=user.id,
            result="REJECTED", detail={"note": note},
        )
        return {"id": str(reimbursement_id), "status": "REJECTED"}

    approved = money(amount_approved) if amount_approved is not None \
        else money(claim["amount"])
    if approved <= 0 or approved > money(claim["amount"]):
        raise HTTPException(
            status_code=400,
            detail="The approved amount must be positive and no more than "
                   "the amount claimed.")

    cat = (await session.execute(
        text("""SELECT gl_account_code FROM wallet_expense_categories
                 WHERE id = :c"""),
        {"c": str(claim["category_id"])},
    )).mappings().first()
    expense_account = (cat["gl_account_code"] if cat and cat["gl_account_code"]
                       else ACC_FALLBACK_EXPENSE)

    journal_id = None
    accrual_ref = f"{claim['reimbursement_reference']}-ACCRUAL"
    if await _should_post(
            session, source_module=SOURCE_MODULE,
            source_reference=accrual_ref, on=claim["spent_on"]):
        journal_id = await post_entry(
            session,
            entry_date=claim["spent_on"],
            description=(f"Staff reimbursement approved "
                         f"{claim['reimbursement_reference']}"),
            source_module=SOURCE_MODULE, source_reference=accrual_ref,
            lines=[
                Line(expense_account, debit=approved,
                     description=claim["purpose"]),
                Line(ACC_REIMBURSEMENT_PAYABLE, credit=approved,
                     description="Owed to employee for out-of-pocket expense"),
            ],
            created_by=user.id,
        )

    await session.execute(
        text("""UPDATE wallet_reimbursements
                   SET status = 'APPROVED', amount_approved = :amt,
                       decided_by = :by, decided_at = NOW(),
                       decision_note = :note, accrual_journal_entry_id = :j
                 WHERE id = :id"""),
        {"amt": str(approved), "by": str(user.id), "note": note,
         "j": str(journal_id) if journal_id else None,
         "id": str(reimbursement_id)},
    )
    await audit(
        session, event_type="REIMBURSEMENT_APPROVED",
        entity_type="wallet_reimbursement", entity_id=reimbursement_id,
        amount=approved, actor_user_id=user.id, result="APPROVED",
        detail={"reference": claim["reimbursement_reference"],
                "expense_account": expense_account},
    )
    return {"id": str(reimbursement_id), "status": "APPROVED",
            "amount_approved": str(approved)}


async def pay_reimbursement(
    session: AsyncSession, *, reimbursement_id: UUID, user,
    paid_on: date, payment_method: str,
    payment_reference: Optional[str] = None,
    paid_from_account_code: str = ACC_BANK,
) -> dict:
    """Settle the liability: Dr Staff Reimbursements Payable / Cr bank|cash."""
    claim = (await session.execute(
        text("SELECT * FROM wallet_reimbursements WHERE id = :id"),
        {"id": str(reimbursement_id)},
    )).mappings().first()
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found.")
    if claim["status"] != "APPROVED":
        raise HTTPException(
            status_code=400,
            detail=(f"Only an approved claim can be paid; this one is "
                    f"{claim['status'].lower()}."))
    await assert_can_reconcile(session, user)

    amount = money(claim["amount_approved"] or claim["amount"])
    journal_id = None
    pay_ref = f"{claim['reimbursement_reference']}-PAYMENT"
    if await _should_post(
            session, source_module=SOURCE_MODULE,
            source_reference=pay_ref, on=paid_on):
        journal_id = await post_entry(
            session,
            entry_date=paid_on,
            description=(f"Staff reimbursement paid "
                         f"{claim['reimbursement_reference']}"),
            source_module=SOURCE_MODULE, source_reference=pay_ref,
            lines=[
                Line(ACC_REIMBURSEMENT_PAYABLE, debit=amount,
                     description="Reimbursement settled"),
                Line(paid_from_account_code, credit=amount,
                     description=f"Paid by {payment_method}"),
            ],
            created_by=user.id,
        )

    await session.execute(
        text("""UPDATE wallet_reimbursements
                   SET status = 'PAID', paid_on = :on,
                       payment_method = :m, payment_reference = :r,
                       paid_from_account_code = :acct, paid_by = :by,
                       payment_journal_entry_id = :j
                 WHERE id = :id"""),
        {"on": paid_on, "m": payment_method, "r": payment_reference,
         "acct": paid_from_account_code, "by": str(user.id),
         "j": str(journal_id) if journal_id else None,
         "id": str(reimbursement_id)},
    )
    await audit(
        session, event_type="REIMBURSEMENT_PAID",
        entity_type="wallet_reimbursement", entity_id=reimbursement_id,
        amount=amount, actor_user_id=user.id, result="PAID",
        detail={"reference": claim["reimbursement_reference"],
                "method": payment_method},
    )
    return {"id": str(reimbursement_id), "status": "PAID",
            "amount": str(amount)}


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

async def submit_reconciliation(
    session: AsyncSession, *, wallet_id: UUID, period_start: date,
    period_end: date, submitted_by: UUID,
    declared_cash_on_hand: Optional[Decimal] = None,
    note: Optional[str] = None,
) -> dict:
    """Close off a period: here is what I was given, spent, and still hold.

    Every figure is computed from the ledger, not accepted from the client.
    The only thing the employee supplies is what they say is physically in
    their hand, and the variance between that and the computed balance is the
    number management actually needs to look at.
    """
    if period_end < period_start:
        raise HTTPException(
            status_code=400, detail="The period end cannot precede its start.")

    wallet = await lock_wallet(session, wallet_id)

    opening = (await session.execute(
        text("""SELECT COALESCE(SUM(CASE WHEN direction = 'CREDIT'
                                         THEN amount ELSE -amount END), 0) AS total
                  FROM wallet_ledger
                 WHERE wallet_id = :w AND occurred_on < :s"""),
        {"w": str(wallet_id), "s": period_start},
    )).first()

    period = (await session.execute(
        text("""
            SELECT
              COALESCE(SUM(CASE
                  WHEN entry_type IN ('FUNDING','OPENING') THEN amount
                  WHEN entry_type = 'REVERSAL'
                       AND source_type = 'wallet_funding' THEN -amount
                  ELSE 0 END), 0) AS funded,
              COALESCE(SUM(CASE
                  WHEN entry_type = 'EXPENSE' THEN amount
                  WHEN entry_type = 'REVERSAL'
                       AND source_type = 'wallet_expense' THEN -amount
                  ELSE 0 END), 0) AS spent,
              COALESCE(SUM(CASE
                  WHEN entry_type = 'RETURN' THEN amount
                  WHEN entry_type = 'REVERSAL'
                       AND source_type = 'wallet_return' THEN -amount
                  ELSE 0 END), 0) AS returned,
              COALESCE(SUM(CASE WHEN direction = 'CREDIT'
                                THEN amount ELSE -amount END), 0) AS net
              FROM wallet_ledger
             WHERE wallet_id = :w AND occurred_on BETWEEN :s AND :e
        """),
        {"w": str(wallet_id), "s": period_start, "e": period_end},
    )).mappings().first()

    opening_balance = money(opening.total)
    closing_balance = opening_balance + money(period["net"])
    variance = (money(declared_cash_on_hand) - closing_balance
                if declared_cash_on_hand is not None else None)

    ref = _reference("REC", period_end)
    rec_id = uuid4()
    await session.execute(
        text("""
            INSERT INTO wallet_reconciliations
                (id, reconciliation_reference, wallet_id, period_start,
                 period_end, opening_balance, total_funded, total_expenses,
                 total_returned, closing_balance, declared_cash_on_hand,
                 variance, status, submitted_by, note)
            VALUES (:id, :ref, :w, :s, :e, :ob, :f, :sp, :r, :cb, :dec, :var,
                    'SUBMITTED', :by, :note)
        """),
        {"id": str(rec_id), "ref": ref, "w": str(wallet_id),
         "s": period_start, "e": period_end, "ob": str(opening_balance),
         "f": str(money(period["funded"])), "sp": str(money(period["spent"])),
         "r": str(money(period["returned"])), "cb": str(closing_balance),
         "dec": str(money(declared_cash_on_hand))
                if declared_cash_on_hand is not None else None,
         "var": str(variance) if variance is not None else None,
         "by": str(submitted_by), "note": note},
    )
    await audit(
        session, event_type="RECONCILIATION_SUBMITTED",
        entity_type="wallet_reconciliation", entity_id=rec_id,
        wallet_id=wallet_id, amount=closing_balance,
        actor_user_id=submitted_by, result="SUBMITTED",
        detail={"reference": ref, "period": f"{period_start}..{period_end}",
                "variance": str(variance) if variance is not None else None},
    )
    return {
        "id": str(rec_id), "reconciliation_reference": ref,
        "opening_balance": str(opening_balance),
        "total_funded": str(money(period["funded"])),
        "total_expenses": str(money(period["spent"])),
        "total_returned": str(money(period["returned"])),
        "closing_balance": str(closing_balance),
        "declared_cash_on_hand": str(money(declared_cash_on_hand))
                                 if declared_cash_on_hand is not None else None,
        "variance": str(variance) if variance is not None else None,
        "status": "SUBMITTED",
    }


async def review_reconciliation(
    session: AsyncSession, *, reconciliation_id: UUID, accept: bool, user,
    note: Optional[str] = None,
) -> dict:
    rec = (await session.execute(
        text("SELECT * FROM wallet_reconciliations WHERE id = :id"),
        {"id": str(reconciliation_id)},
    )).mappings().first()
    if rec is None:
        raise HTTPException(
            status_code=404, detail="Reconciliation not found.")
    if rec["status"] != "SUBMITTED":
        raise HTTPException(
            status_code=400,
            detail=f"This reconciliation is already {rec['status'].lower()}.")
    if str(rec["submitted_by"]) == str(user.id):
        raise HTTPException(
            status_code=403,
            detail="You cannot settle your own reconciliation.")
    await assert_can_reconcile(session, user)

    status = "ACCEPTED" if accept else "REJECTED"
    await session.execute(
        text("""UPDATE wallet_reconciliations
                   SET status = :s, reviewed_by = :by, reviewed_at = NOW(),
                       review_note = :note
                 WHERE id = :id"""),
        {"s": status, "by": str(user.id), "note": note,
         "id": str(reconciliation_id)},
    )
    await audit(
        session, event_type=f"RECONCILIATION_{status}",
        entity_type="wallet_reconciliation", entity_id=reconciliation_id,
        wallet_id=rec["wallet_id"], amount=money(rec["closing_balance"]),
        actor_user_id=user.id, result=status, detail={"note": note},
    )
    return {"id": str(reconciliation_id), "status": status}


# ---------------------------------------------------------------------------
# Anomaly detection -- questions for a human, never verdicts
# ---------------------------------------------------------------------------

async def raise_flag(
    session: AsyncSession, *, wallet_id: UUID, flag_type: str, severity: str,
    message: str, expense_id: Optional[UUID] = None,
    fund_request_id: Optional[UUID] = None, detail: Optional[dict] = None,
) -> None:
    """Record a concern. Idempotent per (expense, type) via a partial index."""
    await session.execute(
        text("""
            INSERT INTO wallet_flags
                (id, wallet_id, expense_id, fund_request_id, flag_type,
                 severity, message, detail)
            VALUES (gen_random_uuid(), :w, :e, :r, :t, :sev, :m,
                    CAST(:d AS JSONB))
            ON CONFLICT DO NOTHING
        """),
        {"w": str(wallet_id),
         "e": str(expense_id) if expense_id else None,
         "r": str(fund_request_id) if fund_request_id else None,
         "t": flag_type, "sev": severity, "m": message,
         "d": json.dumps(detail or {}, default=str)},
    )


async def detect_expense_anomalies(
    session: AsyncSession, *, expense_id: UUID, wallet, amount: Decimal,
    category_id: UUID, spent_on: date,
) -> list[str]:
    """The patterns from section 22, phrased as questions rather than charges.

    Nothing here blocks or reverses anything. Each check answers "is this
    worth a person's attention?", and the wording of every message is chosen
    to describe the pattern, not to allege intent.
    """
    found: list[str] = []
    wallet_id = wallet["id"]

    # 1. The same amount, same category, repeatedly, in a short window.
    row = (await session.execute(
        text("""SELECT COUNT(*) AS c FROM wallet_expenses
                 WHERE wallet_id = :w AND category_id = :c AND amount = :amt
                   AND spent_on >= :since AND status <> 'REVERSED'"""),
        {"w": str(wallet_id), "c": str(category_id), "amt": str(amount),
         "since": spent_on - timedelta(days=7)},
    )).first()
    if row.c >= 3:
        await raise_flag(
            session, wallet_id=wallet_id, expense_id=expense_id,
            flag_type="REPEATED_IDENTICAL", severity="MEDIUM",
            message=(f"{row.c} expenses of exactly {amount:,.2f} in the same "
                     f"category within seven days. Worth confirming these are "
                     f"separate purchases."),
            detail={"count": row.c, "amount": str(amount)},
        )
        found.append("REPEATED_IDENTICAL")

    # 2. Amounts clustering just under an approval threshold.
    #
    # Splitting is only meaningful against the NEXT threshold up, so read the
    # ladder rather than assuming the seeded numbers -- management may have
    # changed them, and a hard-coded 50,000 here would quietly stop working.
    threshold = (await session.execute(
        text("""SELECT MIN(min_amount) AS total FROM wallet_approval_rules
                 WHERE is_active = TRUE AND min_amount > :amt
                   AND (scope = 'GLOBAL'
                        OR (scope = 'WALLET_TYPE' AND wallet_type = :wt)
                        OR (scope = 'WALLET' AND wallet_id = :wid))"""),
        {"amt": str(amount), "wt": wallet["wallet_type"],
         "wid": str(wallet_id)},
    )).first()
    # MIN() over no rows still yields one row holding NULL, so the row itself
    # is always truthy -- the value has to be tested explicitly. An expense in
    # the top band has no threshold above it and nothing to split under.
    if threshold is not None and threshold.total is not None:
        band_top = money(threshold.total)
        band_floor = band_top * Decimal("0.90")
        if band_floor <= amount < band_top:
            near = (await session.execute(
                text("""SELECT COUNT(*) AS c FROM wallet_expenses
                         WHERE wallet_id = :w AND amount >= :lo AND amount < :hi
                           AND spent_on >= :since AND status <> 'REVERSED'"""),
                {"w": str(wallet_id), "lo": str(band_floor),
                 "hi": str(band_top), "since": spent_on - timedelta(days=7)},
            )).first()
            if near.c >= 3:
                await raise_flag(
                    session, wallet_id=wallet_id, expense_id=expense_id,
                    flag_type="THRESHOLD_SPLITTING", severity="HIGH",
                    message=(f"{near.c} expenses in seven days fall just below "
                             f"the {band_top:,.2f} approval threshold. This can "
                             f"happen naturally, but it is the pattern a split "
                             f"purchase would produce -- worth a look."),
                    detail={"count": near.c, "threshold": str(band_top)},
                )
                found.append("THRESHOLD_SPLITTING")

    # 3. A day far outside this wallet's own normal.
    #
    # Compared against the wallet's own history, not a company-wide figure: a
    # factory wallet and a marketing wallet have different normals, and one
    # threshold for both would either shout constantly or never fire.
    hist = (await session.execute(
        text("""SELECT AVG(daily) AS mean, COUNT(*) AS days FROM (
                    SELECT spent_on, SUM(amount) AS daily
                      FROM wallet_expenses
                     WHERE wallet_id = :w AND status = 'APPROVED'
                       AND spent_on >= :since AND spent_on < :today
                     GROUP BY spent_on) d"""),
        {"w": str(wallet_id), "since": spent_on - timedelta(days=60),
         "today": spent_on},
    )).first()
    if hist and hist.days and hist.days >= 5 and hist.mean:
        today_total = (await session.execute(
            text("""SELECT COALESCE(SUM(amount), 0) AS total FROM wallet_expenses
                     WHERE wallet_id = :w AND spent_on = :d
                       AND status <> 'REVERSED'"""),
            {"w": str(wallet_id), "d": spent_on},
        )).first()
        mean = money(hist.mean)
        total = money(today_total.total)
        if mean > 0 and total > mean * 4:
            await raise_flag(
                session, wallet_id=wallet_id, expense_id=expense_id,
                flag_type="UNUSUAL_SPEND", severity="MEDIUM",
                message=(f"{total:,.2f} recorded on {spent_on}, against a "
                         f"typical day of about {mean:,.2f} for this wallet. "
                         f"An unusual day is not a problem by itself."),
                detail={"day_total": str(total), "typical": str(mean)},
            )
            found.append("UNUSUAL_SPEND")

    # 4. Required receipt not yet attached.
    cat = (await session.execute(
        text("""SELECT requires_receipt, name FROM wallet_expense_categories
                 WHERE id = :c"""),
        {"c": str(category_id)},
    )).mappings().first()
    if cat and cat["requires_receipt"]:
        has = (await session.execute(
            text("SELECT 1 FROM wallet_receipts WHERE expense_id = :e LIMIT 1"),
            {"e": str(expense_id)},
        )).first()
        if has is None:
            await raise_flag(
                session, wallet_id=wallet_id, expense_id=expense_id,
                flag_type="MISSING_RECEIPT", severity="LOW",
                message=(f"No receipt attached yet for this {cat['name']} "
                         f"expense."),
                detail={"category": cat["name"]},
            )
            found.append("MISSING_RECEIPT")

    return found


async def detect_request_anomalies(
    session: AsyncSession, *, wallet_id: UUID, request_id: UUID,
) -> list[str]:
    row = (await session.execute(
        text("""SELECT COUNT(*) AS c FROM wallet_fund_requests
                 WHERE wallet_id = :w AND requested_at >= NOW() - INTERVAL '30 days'"""),
        {"w": str(wallet_id)},
    )).first()
    if row.c >= 4:
        # The partial unique index only dedupes flags that name an expense, so
        # dedupe this one explicitly: the 4th, 5th and 6th request describe the
        # same concern, and three identical open flags is noise that trains
        # people to ignore the list.
        open_already = (await session.execute(
            text("""SELECT 1 FROM wallet_flags
                     WHERE wallet_id = :w AND flag_type = 'FREQUENT_TOPUP'
                       AND status = 'OPEN' LIMIT 1"""),
            {"w": str(wallet_id)},
        )).first()
        if open_already is not None:
            return ["FREQUENT_TOPUP"]
        await raise_flag(
            session, wallet_id=wallet_id, fund_request_id=request_id,
            flag_type="FREQUENT_TOPUP", severity="MEDIUM",
            message=(f"{row.c} requests for additional funds in the last 30 "
                     f"days. The wallet's limits may simply be set too low "
                     f"for the work."),
            detail={"count": row.c},
        )
        return ["FREQUENT_TOPUP"]
    return []


async def sweep_missing_receipts(session: AsyncSession) -> int:
    """Flag approved expenses that still have no receipt. Returns the count.

    Run on demand from the management dashboard rather than on a schedule:
    the check is cheap, and a report someone asked for is more useful than a
    nightly job whose output nobody reads.
    """
    rows = (await session.execute(
        text("""
            SELECT e.id, e.wallet_id, e.expense_reference, c.name AS cat
              FROM wallet_expenses e
              JOIN wallet_expense_categories c ON c.id = e.category_id
             WHERE e.status = 'APPROVED' AND c.requires_receipt = TRUE
               AND NOT EXISTS (SELECT 1 FROM wallet_receipts r
                                WHERE r.expense_id = e.id)
        """),
    )).mappings().all()
    for r in rows:
        await raise_flag(
            session, wallet_id=r["wallet_id"], expense_id=r["id"],
            flag_type="MISSING_RECEIPT", severity="MEDIUM",
            message=(f"Approved {r['cat']} expense {r['expense_reference']} "
                     f"has no receipt on file."),
            detail={"category": r["cat"]},
        )
    return len(rows)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

async def wallet_summary(session: AsyncSession, wallet_id: UUID) -> dict:
    """Everything the staff dashboard shows, all derived server-side."""
    wallet = await get_wallet(session, wallet_id)
    totals = await derive_totals(session, wallet_id)
    pending = await pending_expense_total(session, wallet_id)
    outstanding = await outstanding_amount(session, wallet_id, totals=totals)

    today = date.today()
    month_start = today.replace(day=1)
    month = (await session.execute(
        text("""
            SELECT
              COALESCE(SUM(CASE WHEN entry_type IN ('FUNDING','OPENING')
                                THEN amount ELSE 0 END), 0) AS funded,
              COALESCE(SUM(CASE WHEN entry_type = 'EXPENSE'
                                THEN amount ELSE 0 END), 0) AS spent
              FROM wallet_ledger
             WHERE wallet_id = :w AND occurred_on >= :s
        """),
        {"w": str(wallet_id), "s": month_start},
    )).mappings().first()

    holder = (await session.execute(
        text("SELECT full_name, email, role FROM users WHERE id = :u"),
        {"u": str(wallet["user_id"])},
    )).mappings().first()

    open_flags = (await session.execute(
        text("""SELECT COUNT(*) AS c FROM wallet_flags
                 WHERE wallet_id = :w AND status = 'OPEN'"""),
        {"w": str(wallet_id)},
    )).first()

    return {
        "id": str(wallet["id"]),
        "wallet_number": wallet["wallet_number"],
        "wallet_type": wallet["wallet_type"],
        "department": wallet["department"],
        "purpose": wallet["purpose"],
        "currency": wallet["currency"],
        "status": wallet["status"],
        "holder": {
            "user_id": str(wallet["user_id"]),
            "full_name": holder["full_name"] if holder else None,
            "email": holder["email"] if holder else None,
        },
        "balance": str(totals["balance"]),
        "available": str(totals["balance"] - pending),
        "pending_approval": str(pending),
        "outstanding": str(outstanding),
        "total_funded": str(totals["total_funded"]),
        "total_spent": str(totals["total_spent"]),
        "total_returned": str(totals["total_returned"]),
        "funded_this_month": str(money(month["funded"])),
        "spent_this_month": str(money(month["spent"])),
        "limits": {
            "single_txn": str(money(wallet["single_txn_limit"]))
                          if wallet["single_txn_limit"] is not None else None,
            "daily": str(money(wallet["daily_limit"]))
                     if wallet["daily_limit"] is not None else None,
            "monthly": str(money(wallet["monthly_limit"]))
                       if wallet["monthly_limit"] is not None else None,
            "self_approve": str(money(wallet["self_approve_limit"]))
                            if wallet["self_approve_limit"] is not None else None,
        },
        "open_flags": open_flags.c,
        "last_transaction_at": wallet["last_transaction_at"],
    }


async def management_dashboard(
    session: AsyncSession, *, department: Optional[str] = None,
) -> dict:
    """Company-wide position, and the same picture broken out by department."""
    where = "WHERE w.status <> 'CLOSED'"
    params: dict = {}
    if department:
        where += " AND w.department = :dept"
        params["dept"] = department

    totals = (await session.execute(
        text(f"""
            SELECT COALESCE(SUM(w.total_funded), 0) AS funded,
                   COALESCE(SUM(w.total_spent), 0) AS spent,
                   COALESCE(SUM(w.total_returned), 0) AS returned,
                   COALESCE(SUM(w.balance), 0) AS balance,
                   COUNT(*) AS wallets
              FROM wallets w {where}
        """), params,
    )).mappings().first()

    by_dept = (await session.execute(
        text(f"""
            SELECT COALESCE(w.department, 'Unassigned') AS department,
                   COALESCE(SUM(w.total_funded), 0) AS funded,
                   COALESCE(SUM(w.total_spent), 0) AS spent,
                   COALESCE(SUM(w.total_returned), 0) AS returned,
                   COALESCE(SUM(w.balance), 0) AS balance,
                   COUNT(*) AS wallets
              FROM wallets w {where}
             GROUP BY COALESCE(w.department, 'Unassigned')
             ORDER BY funded DESC
        """), params,
    )).mappings().all()

    # Outstanding is per-wallet arithmetic (overdue fundings less what has
    # been discharged), so it cannot be summed with a single GROUP BY.
    overdue = (await session.execute(
        text(f"""
            SELECT COALESCE(w.department, 'Unassigned') AS department,
                   w.id,
                   COALESCE((SELECT SUM(f.amount) FROM wallet_fundings f
                              WHERE f.wallet_id = w.id AND f.status = 'ISSUED'
                                AND f.account_by IS NOT NULL
                                AND f.account_by < CURRENT_DATE), 0) AS overdue,
                   w.total_spent + w.total_returned AS discharged
              FROM wallets w {where}
        """), params,
    )).mappings().all()
    outstanding_by_dept: dict[str, Decimal] = {}
    outstanding_total = ZERO
    for r in overdue:
        amt = max(ZERO, money(r["overdue"]) - money(r["discharged"]))
        outstanding_total += amt
        outstanding_by_dept[r["department"]] = (
            outstanding_by_dept.get(r["department"], ZERO) + amt)

    pending = (await session.execute(
        text("""
            SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total
              FROM wallet_expenses WHERE status = 'PENDING'
        """),
    )).mappings().first()
    requests = (await session.execute(
        text("""SELECT COUNT(*) AS n, COALESCE(SUM(amount_requested), 0) AS total
                  FROM wallet_fund_requests WHERE status = 'PENDING'"""),
    )).mappings().first()
    flags = (await session.execute(
        text("SELECT COUNT(*) AS n FROM wallet_flags WHERE status = 'OPEN'"),
    )).first()
    claims = (await session.execute(
        text("""SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total
                  FROM wallet_reimbursements WHERE status = 'PENDING'"""),
    )).mappings().first()

    return {
        "totals": {
            "wallets": totals["wallets"],
            "issued": str(money(totals["funded"])),
            "spent": str(money(totals["spent"])),
            "returned": str(money(totals["returned"])),
            "balance": str(money(totals["balance"])),
            "outstanding": str(outstanding_total),
        },
        "departments": [
            {
                "department": r["department"],
                "wallets": r["wallets"],
                "issued": str(money(r["funded"])),
                "spent": str(money(r["spent"])),
                "returned": str(money(r["returned"])),
                "balance": str(money(r["balance"])),
                "outstanding": str(outstanding_by_dept.get(r["department"], ZERO)),
            }
            for r in by_dept
        ],
        "queues": {
            "pending_expenses": pending["n"],
            "pending_expense_value": str(money(pending["total"])),
            "pending_fund_requests": requests["n"],
            "pending_fund_request_value": str(money(requests["total"])),
            "pending_reimbursements": claims["n"],
            "pending_reimbursement_value": str(money(claims["total"])),
            "open_flags": flags.n,
        },
    }


async def staff_accountability_report(
    session: AsyncSession, wallet_id: UUID,
) -> dict:
    """Section 20: one employee, everything about their money, in one place."""
    summary = await wallet_summary(session, wallet_id)

    fundings = (await session.execute(
        text("""SELECT funding_reference, amount, purpose, funded_on,
                       account_by, disbursement_method, status
                  FROM wallet_fundings WHERE wallet_id = :w
                 ORDER BY funded_on DESC, created_at DESC"""),
        {"w": str(wallet_id)},
    )).mappings().all()

    expenses = (await session.execute(
        text("""SELECT e.expense_reference, e.amount, e.spent_on, e.purpose,
                       e.status, e.vendor, c.name AS category,
                       (SELECT COUNT(*) FROM wallet_receipts r
                         WHERE r.expense_id = e.id) AS receipts
                  FROM wallet_expenses e
                  JOIN wallet_expense_categories c ON c.id = e.category_id
                 WHERE e.wallet_id = :w
                 ORDER BY e.spent_on DESC, e.submitted_at DESC"""),
        {"w": str(wallet_id)},
    )).mappings().all()

    returns = (await session.execute(
        text("""SELECT return_reference, amount, returned_on, method, status
                  FROM wallet_returns WHERE wallet_id = :w
                 ORDER BY returned_on DESC"""),
        {"w": str(wallet_id)},
    )).mappings().all()

    requests = (await session.execute(
        text("""SELECT request_reference, amount_requested, amount_approved,
                       reason, urgency, status, requested_at
                  FROM wallet_fund_requests WHERE wallet_id = :w
                 ORDER BY requested_at DESC"""),
        {"w": str(wallet_id)},
    )).mappings().all()

    flags = (await session.execute(
        text("""SELECT flag_type, severity, message, status, created_at
                  FROM wallet_flags WHERE wallet_id = :w
                 ORDER BY created_at DESC"""),
        {"w": str(wallet_id)},
    )).mappings().all()

    holder_id = (await session.execute(
        text("SELECT user_id FROM wallets WHERE id = :w"),
        {"w": str(wallet_id)},
    )).first()
    claims = (await session.execute(
        text("""SELECT reimbursement_reference, amount, amount_approved,
                       spent_on, purpose, status
                  FROM wallet_reimbursements WHERE user_id = :u
                 ORDER BY spent_on DESC"""),
        {"u": str(holder_id.user_id)},
    )).mappings().all()

    def rows(items):
        return [{k: (str(v) if isinstance(v, Decimal) else v)
                 for k, v in dict(r).items()} for r in items]

    return {
        "summary": summary,
        "fundings": rows(fundings),
        "expenses": rows(expenses),
        "returns": rows(returns),
        "fund_requests": rows(requests),
        "reimbursements": rows(claims),
        "flags": rows(flags),
    }


async def inbox(session: AsyncSession, user) -> dict:
    """What needs this user's attention right now.

    Derived on read rather than delivered as stored notifications. The push
    subscription store in app/api/notifications.py is keyed by browser
    endpoint with no user association (it carries a TODO saying so), so
    targeted push cannot be sent today without changing that module. A derived
    inbox is accurate by construction, cannot be missed, and does not depend
    on a delivery that may have failed while the phone was off.
    """
    caps = await user_capabilities(session, user)
    items: list[dict] = []

    own = (await session.execute(
        text("SELECT id, wallet_number, balance FROM wallets "
             "WHERE user_id = :u AND status = 'ACTIVE'"),
        {"u": str(user.id)},
    )).mappings().all()

    for w in own:
        totals = await derive_totals(session, w["id"])
        outstanding = await outstanding_amount(session, w["id"], totals=totals)
        if outstanding > 0:
            items.append({
                "kind": "OUTSTANDING",
                "severity": "HIGH",
                "message": (f"{outstanding:,.2f} on wallet {w['wallet_number']} "
                            f"is past its accounting date."),
                "wallet_id": str(w["id"]),
            })
        if totals["balance"] <= Decimal("2000") and totals["total_funded"] > 0:
            items.append({
                "kind": "LOW_BALANCE",
                "severity": "LOW",
                "message": (f"Wallet {w['wallet_number']} is down to "
                            f"{totals['balance']:,.2f}."),
                "wallet_id": str(w["id"]),
            })

    rejected = (await session.execute(
        text("""SELECT e.expense_reference, e.amount, e.decision_note
                  FROM wallet_expenses e
                 WHERE e.submitted_by = :u AND e.status = 'REJECTED'
                   AND e.decided_at >= NOW() - INTERVAL '14 days'"""),
        {"u": str(user.id)},
    )).mappings().all()
    for r in rejected:
        items.append({
            "kind": "EXPENSE_REJECTED",
            "severity": "MEDIUM",
            "message": (f"Expense {r['expense_reference']} for "
                        f"{money(r['amount']):,.2f} was not approved"
                        + (f": {r['decision_note']}" if r["decision_note"] else ".")),
        })

    if caps["can_approve"]:
        queue = await approval_queue(session, user)
        if queue:
            items.append({
                "kind": "AWAITING_APPROVAL",
                "severity": "MEDIUM",
                "message": (f"{len(queue)} expense(s) are waiting for your "
                            f"approval."),
            })
    if caps["can_fund"]:
        reqs = (await session.execute(
            text("""SELECT COUNT(*) AS c FROM wallet_fund_requests
                     WHERE status IN ('PENDING','INFO_REQUESTED')"""),
        )).first()
        if reqs.c:
            items.append({
                "kind": "FUND_REQUESTS",
                "severity": "MEDIUM",
                "message": f"{reqs.c} request(s) for funds are waiting.",
            })

    return {"capabilities": caps, "items": items}


async def approval_queue(session: AsyncSession, user) -> list[dict]:
    """Pending expenses this user is actually able to approve.

    Filtered by the same rules `assert_can_approve` enforces, so the queue
    never shows something that would be refused on click -- including the
    user's own submissions.
    """
    rows = (await session.execute(
        text("""
            SELECT e.id, e.expense_reference, e.amount, e.spent_on, e.purpose,
                   e.required_tier, e.submitted_by, e.submitted_at, e.vendor,
                   c.name AS category, w.id AS wallet_id,
                   w.wallet_number, w.department, u.full_name AS submitted_by_name,
                   (SELECT COUNT(*) FROM wallet_receipts r
                     WHERE r.expense_id = e.id) AS receipts
              FROM wallet_expenses e
              JOIN wallets w ON w.id = e.wallet_id
              JOIN wallet_expense_categories c ON c.id = e.category_id
              LEFT JOIN users u ON u.id = e.submitted_by
             WHERE e.status = 'PENDING'
             ORDER BY e.submitted_at ASC
        """),
    )).mappings().all()

    grants = await approver_grants(session, user.id)
    is_admin = user.role == "admin"
    out = []
    for r in rows:
        if str(r["submitted_by"]) == str(user.id):
            continue  # never your own
        if is_admin:
            allowed = True
        else:
            need = _TIER_RANK.get(r["required_tier"], 0)
            allowed = any(
                _TIER_RANK.get(g["tier"], 0) >= need
                and (g["max_amount"] is None
                     or money(g["max_amount"]) >= money(r["amount"]))
                and (g["department"] is None
                     or g["department"] == r["department"])
                and (g["wallet_id"] is None
                     or str(g["wallet_id"]) == str(r["wallet_id"]))
                for g in grants
            )
        if not allowed:
            continue
        out.append({
            "id": str(r["id"]),
            "expense_reference": r["expense_reference"],
            "amount": str(money(r["amount"])),
            "spent_on": r["spent_on"],
            "purpose": r["purpose"],
            "category": r["category"],
            "vendor": r["vendor"],
            "required_tier": r["required_tier"],
            "wallet_id": str(r["wallet_id"]),
            "wallet_number": r["wallet_number"],
            "department": r["department"],
            "submitted_by": r["submitted_by_name"],
            "submitted_at": r["submitted_at"],
            "receipts": r["receipts"],
        })
    return out
