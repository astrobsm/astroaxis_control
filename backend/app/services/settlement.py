"""MAPD: the automatic payment distribution engine.

One customer, one invoice covering several product lines, one payment -- and
each product's share landing in the account that product's business unit
banks into, with no manual transfer and no spreadsheet.

WHERE THIS SITS
---------------
`app.services.receivables.record_payment` is the single choke point through
which every customer payment in this ERP passes (mark-paid, payment tracking,
the public order portal). Distribution hangs off that one call, so a payment
recorded through any route settles the same way. Nothing else may write
`settlements` rows.

WHAT IT GUARANTEES
------------------
* **Exactness.** Allocations are Decimal and apportioned by largest remainder,
  so the split sums to the payment EXACTLY -- never a cent over or under. A
  70/20/10 split of 33.33 is 23.33 + 6.67 + 3.33, not three roundings that
  leave a stray cent to be written off.
* **Idempotence.** A unique partial index allows at most one live settlement
  per payment. A retried request, a double-clicked verify button, or a re-run
  of the retry job cannot pay a destination account twice.
* **All or nothing.** The full plan is built and validated -- every rule
  resolved, every destination account confirmed ACTIVE -- before a single
  detail row is written. A payment is never left half-distributed.
* **Never lose the cash.** If distribution fails, the PAYMENT still stands.
  The money arrived; refusing to record that because a destination account is
  suspended would be a worse error than a settlement queued for retry. The
  failure is recorded with its reason, surfaced on the dashboard, and retried.
* **Immutability.** Settlement details and the audit log are append-only, and
  the database enforces it with a trigger. Corrections are reversals.

PARTIAL PAYMENTS
----------------
Each payment is allocated across the invoice lines in proportion to their
REMAINING capacity (line total less what previous settlements already sent).
Instalments therefore converge exactly on the full split once the invoice is
settled, with no drift and no dependence on the order payments arrive in.

TAX
---
`product_accounts.tax_group` is configuration and reporting metadata. VAT
recognition remains with `app.services.tax`, which derives it from the ledger.
Carving VAT out here as well would double-count it.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ledger import Line, money, post_entry, reverse_entry
from app.services.posting import (
    ACC_BANK, ACC_CASH, already_posted, business_date, posting_enabled)

CENT = Decimal("0.01")
ZERO = Decimal("0.00")


# ---------------------------------------------------------------------------
# Roll-out control
# ---------------------------------------------------------------------------

def mapd_enabled() -> bool:
    """Is automatic distribution on?

    Defaults ON, unlike ACCOUNTING_POSTING_ENABLED, because switching it on
    changes nothing until somebody configures rules: an invoice whose products
    have no settlement configuration produces a SKIPPED settlement recording
    that fact, not a distribution. Set MAPD_ENABLED=false to disable the hook
    entirely and drive the engine only through POST /api/payments/distribute.
    """
    return os.getenv("MAPD_ENABLED", "true").lower() not in (
        "0", "false", "no", "off")


def strict_mode() -> bool:
    """Should an unconfigured product FAIL the settlement rather than skip it?

    Once every product has been mapped, an unconfigured one is a mistake worth
    shouting about. Until then it is just work not yet done. Off by default;
    turn it on when the configuration is complete.
    """
    return os.getenv("MAPD_STRICT", "").lower() in ("1", "true", "yes", "on")


_schema_ready: Optional[bool] = None


async def mapd_schema_ready(session: AsyncSession) -> bool:
    """Has the MAPD migration been applied to this database?

    The hook in record_payment runs on every payment, including in test
    fixtures and on a deployment that has not migrated yet. Probing beats
    catching UndefinedTable: a swallowed exception around money movement hides
    real failures just as effectively as it hides this one.

    A True result is cached (a table does not un-exist); a False result is
    re-probed, so the engine starts working the moment the migration lands
    without needing a restart.
    """
    global _schema_ready
    if _schema_ready:
        return True
    found = (await session.execute(
        text("SELECT to_regclass('public.settlements') IS NOT NULL"))).scalar()
    _schema_ready = bool(found)
    return _schema_ready


# ---------------------------------------------------------------------------
# Money splitting
# ---------------------------------------------------------------------------

def apportion(total, weights) -> list[Decimal]:
    """Split `total` in proportion to `weights`, summing to it EXACTLY.

    Largest-remainder (Hamilton) apportionment: floor every share to the cent,
    then hand the leftover cents to the shares with the largest discarded
    fractions. Rounding each share independently does not work -- 1/3 of 100.00
    three times is 33.33 x 3 = 99.99, and that missing cent has to come out of
    somebody's account.

    Ties break toward the earlier weight, so the same inputs always produce the
    same split; a distribution that varies run to run cannot be reconciled.
    """
    total = money(total)
    ws = [money(w) if w is not None else ZERO for w in weights]
    n = len(ws)
    if n == 0:
        return []
    if total == ZERO:
        return [ZERO] * n

    weight_sum = sum(ws)
    if weight_sum <= ZERO:
        # No basis on which to prefer any share: give it all to the first,
        # rather than silently dropping the money.
        return [total] + [ZERO] * (n - 1)

    # Work in integer cents; Decimal division would reintroduce the rounding
    # this function exists to eliminate.
    total_c = int((total / CENT).to_integral_value())
    weight_c = [int((w / CENT).to_integral_value()) for w in ws]
    weight_total_c = sum(weight_c)

    shares, remainders = [], []
    for i, w in enumerate(weight_c):
        exact = total_c * w
        base = exact // weight_total_c
        shares.append(base)
        remainders.append((exact - base * weight_total_c, -i))  # -i: earlier wins

    leftover = total_c - sum(shares)
    for _, neg_i in sorted(remainders, reverse=True)[:leftover]:
        shares[-neg_i] += 1

    return [Decimal(s) * CENT for s in shares]


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------

async def mapd_audit(
    session: AsyncSession,
    *,
    event_type: str,
    entity_type: Optional[str] = None,
    entity_id: Optional[UUID] = None,
    payment_id: Optional[UUID] = None,
    settlement_id: Optional[UUID] = None,
    actor_user_id: Optional[UUID] = None,
    actor_label: Optional[str] = None,
    detail: Optional[dict] = None,
) -> None:
    """Append one line to the immutable MAPD audit log.

    Every state change the engine makes goes through here. The table rejects
    UPDATE and DELETE at the database level, so this is a record of what
    happened rather than a record of what someone last decided it should say.
    """
    await session.execute(
        text("""
            INSERT INTO mapd_audit_logs
                (id, event_type, entity_type, entity_id, payment_id,
                 settlement_id, actor_user_id, actor_label, detail, created_at)
            VALUES (gen_random_uuid(), :ev, :et, :eid, :pid, :sid, :uid,
                    :label, CAST(:detail AS JSONB), NOW())
        """),
        {
            "ev": event_type, "et": entity_type,
            "eid": str(entity_id) if entity_id else None,
            "pid": str(payment_id) if payment_id else None,
            "sid": str(settlement_id) if settlement_id else None,
            "uid": str(actor_user_id) if actor_user_id else None,
            "label": actor_label,
            "detail": json.dumps(detail, default=str) if detail else None,
        },
    )


# ---------------------------------------------------------------------------
# Rule resolution
# ---------------------------------------------------------------------------

class AccountUnavailable(Exception):
    """A destination account is suspended or closed."""

    def __init__(self, account_code: str, account_name: str, status: str):
        self.account_code = account_code
        super().__init__(
            f"Destination account {account_code} ({account_name}) is "
            f"{status}. Settlement paused rather than allocated partially; "
            f"reactivate the account and retry.")


async def resolve_rule(
    session: AsyncSession, *, product_id, business_unit_id, on: date
) -> Optional[dict]:
    """The one rule that applies to this product on this date, or None.

    Specificity then priority then age: PRODUCT beats BUSINESS_UNIT beats
    GLOBAL, lower priority number wins within a scope, older rule wins on a
    tie. The order has to be total -- if two overlapping rules could each be
    "the" rule, the same invoice would settle differently depending on which
    row the planner happened to read first.
    """
    rule = (await session.execute(
        text("""
            SELECT r.id, r.code, r.name, r.scope, r.basis, r.priority
              FROM settlement_rules r
             WHERE r.is_active
               AND r.effective_from <= :on
               AND (r.effective_to IS NULL OR r.effective_to >= :on)
               AND ( (r.scope = 'PRODUCT'       AND r.product_id = :pid)
                  OR (r.scope = 'BUSINESS_UNIT' AND r.business_unit_id = :bu)
                  OR  r.scope = 'GLOBAL' )
             ORDER BY CASE r.scope WHEN 'PRODUCT' THEN 0
                                   WHEN 'BUSINESS_UNIT' THEN 1
                                   ELSE 2 END,
                      r.priority, r.created_at
             LIMIT 1
        """),
        {"on": on, "pid": str(product_id) if product_id else None,
         "bu": str(business_unit_id) if business_unit_id else None},
    )).first()
    if rule is None:
        return None

    splits = (await session.execute(
        text("""
            SELECT s.id, s.financial_account_id, s.allocation_type,
                   s.percentage, s.fixed_amount, s.rate_per_unit,
                   s.is_residual, s.description,
                   fa.code AS account_code, fa.name AS account_name,
                   fa.status AS account_status, fa.account_kind,
                   fa.gl_account_code, fa.contra_gl_account_code
              FROM settlement_rule_splits s
              JOIN financial_accounts fa ON fa.id = s.financial_account_id
             WHERE s.rule_id = :rid
             ORDER BY s.sort_order, s.id
        """),
        {"rid": str(rule.id)},
    )).fetchall()

    if not splits:
        # A rule with no splits allocates nothing; treating it as "the" rule
        # would silently swallow the line's share.
        return None

    return {
        "id": rule.id, "code": rule.code, "name": rule.name,
        "scope": rule.scope, "basis": rule.basis,
        "splits": [dict(s._mapping) for s in splits],
    }


def _claim_for(split: dict, line_total: Decimal, quantity: Decimal) -> Optional[Decimal]:
    """What one split claims against a FULL line, before pro-rating.

    Fixed and per-unit amounts are defined against the whole line, so they are
    computed here and scaled down later for a partial payment. A flat ₦5,000
    handling fee must not swallow a ₦1,000 first instalment whole.
    """
    if split["is_residual"]:
        return None                                  # resolved by the caller
    if split["percentage"] is not None:
        return money(line_total * Decimal(str(split["percentage"])) / Decimal(100))
    if split["fixed_amount"] is not None:
        return money(split["fixed_amount"])
    if split["rate_per_unit"] is not None:
        return money(Decimal(str(split["rate_per_unit"])) * quantity)
    return ZERO


def split_line(
    rule: dict, *, line_total: Decimal, quantity: Decimal
) -> tuple[list[tuple[dict, Decimal]], list[tuple[dict, Decimal]]]:
    """Apply a rule to a full line. Returns (cash_claims, obligation_claims).

    Cash claims MUST account for the whole line -- that money physically
    arrived and every naira of it has to land somewhere. Obligations are
    additional: owing a distributor 10% commission does not reduce the cash
    banked, it creates a liability alongside it.
    """
    line_total = money(line_total)
    cash_splits = [s for s in rule["splits"] if s["allocation_type"] == 'CASH']
    obl_splits = [s for s in rule["splits"] if s["allocation_type"] == 'OBLIGATION']

    if not cash_splits:
        raise HTTPException(
            status_code=400,
            detail=(f"Settlement rule {rule['code']} has no CASH split, so the "
                    f"money received has no destination."))

    claims: list[Optional[Decimal]] = [
        _claim_for(s, line_total, quantity) for s in cash_splits]
    explicit = sum(c for c in claims if c is not None)
    residual_positions = [i for i, c in enumerate(claims) if c is None]

    if residual_positions:
        remainder = money(line_total - explicit)
        if remainder < ZERO:
            raise HTTPException(
                status_code=400,
                detail=(f"Settlement rule {rule['code']} allocates "
                        f"{explicit:,.2f} of a line worth {line_total:,.2f} "
                        f"before the residual split, which would make the "
                        f"residual negative."))
        claims[residual_positions[0]] = remainder
    elif abs(explicit - line_total) > CENT:
        # Rounding is fixed below; a real shortfall is a misconfigured rule and
        # must not be papered over by silently rescaling the percentages.
        raise HTTPException(
            status_code=400,
            detail=(f"Settlement rule {rule['code']} allocates "
                    f"{explicit:,.2f} of a line worth {line_total:,.2f}. "
                    f"Cash splits must account for the whole line -- add a "
                    f"residual split or correct the percentages."))

    # Normalise the last cents so the split sums to the line exactly.
    exact = apportion(line_total, [c or ZERO for c in claims])
    cash_claims = list(zip(cash_splits, exact))

    obligation_claims = []
    for s in obl_splits:
        amt = _claim_for(s, line_total, quantity)
        if amt is None:
            raise HTTPException(
                status_code=400,
                detail=(f"Settlement rule {rule['code']} has a residual "
                        f"OBLIGATION split, which has no defined meaning -- "
                        f"an obligation is a stated share, not a remainder."))
        if amt > ZERO:
            obligation_claims.append((s, amt))

    return cash_claims, obligation_claims


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

async def build_plan(
    session: AsyncSession, *, payment_id: UUID, on: Optional[date] = None
) -> dict:
    """Work out where one payment's money goes, without writing anything.

    Also serves the UI: an operator can see the split a payment WOULD produce
    before it is committed, which is the difference between an automatic
    system people trust and one they check by hand anyway.
    """
    pay = (await session.execute(
        text("""
            SELECT p.id, p.invoice_id, p.amount, p.payment_method,
                   p.payment_date, p.reference,
                   i.invoice_number, i.total_amount AS invoice_total,
                   i.sales_order_id
              FROM payments p
              JOIN invoices i ON i.id = p.invoice_id
             WHERE p.id = :pid
        """),
        {"pid": str(payment_id)},
    )).first()
    if pay is None:
        raise HTTPException(status_code=404, detail="Payment not found.")

    gross = money(pay.amount)
    on_date = on or business_date(pay.payment_date) or date.today()

    lines = (await session.execute(
        text("""
            SELECT il.id, il.product_id, il.quantity, il.line_total,
                   pr.name AS product_name, pr.sku,
                   pa.business_unit_id, pa.default_financial_account_id,
                   pa.settlement_priority, pa.tax_group,
                   bu.code AS business_unit_code, bu.name AS business_unit_name
              FROM invoice_lines il
              LEFT JOIN products pr ON pr.id = il.product_id
              LEFT JOIN product_accounts pa ON pa.product_id = il.product_id
              LEFT JOIN business_units bu ON bu.id = pa.business_unit_id
             WHERE il.invoice_id = :iid
             ORDER BY COALESCE(pa.settlement_priority, 100), il.id
        """),
        {"iid": str(pay.invoice_id)},
    )).fetchall()

    if not lines:
        return {"payment_id": payment_id, "invoice_id": pay.invoice_id,
                "invoice_number": pay.invoice_number, "gross_amount": gross,
                "status": "SKIPPED", "on": on_date,
                "reason": "The invoice has no lines, so there is nothing to "
                          "allocate the payment against.",
                "allocations": [], "obligations": []}

    # What previous settlements have already sent, per line. Only live
    # settlements count -- a reversed one released its share back.
    sent = dict((str(r.invoice_line_id), money(r.sent)) for r in (
        await session.execute(
            text("""
                SELECT d.invoice_line_id, SUM(d.amount) AS sent
                  FROM settlement_details d
                  JOIN settlements s ON s.id = d.settlement_id
                 WHERE s.invoice_id = :iid
                   AND s.status IN ('PENDING','COMPLETED')
                   AND d.allocation_type = 'CASH'
                   AND d.invoice_line_id IS NOT NULL
                 GROUP BY d.invoice_line_id
            """),
            {"iid": str(pay.invoice_id)},
        )).fetchall())

    capacity = [max(ZERO, money(ln.line_total) - sent.get(str(ln.id), ZERO))
                for ln in lines]
    total_capacity = money(sum(capacity))

    if gross - total_capacity > CENT:
        raise HTTPException(
            status_code=400,
            detail=(f"Payment of {gross:,.2f} exceeds the {total_capacity:,.2f} "
                    f"still unallocated on invoice {pay.invoice_number}. "
                    f"The excess has no invoice line to attach to, so it "
                    f"cannot be distributed."))

    distributable = min(gross, total_capacity)
    per_line = apportion(distributable, capacity)

    allocations, obligations, unconfigured = [], [], []

    for ln, line_share in zip(lines, per_line):
        if line_share <= ZERO:
            continue
        line_total = money(ln.line_total)
        quantity = Decimal(str(ln.quantity or 0))

        rule = await resolve_rule(
            session, product_id=ln.product_id,
            business_unit_id=ln.business_unit_id, on=on_date)

        if rule is None:
            if ln.default_financial_account_id is None:
                unconfigured.append(
                    (ln.product_name or "Unnamed product", ln.product_id))
                continue
            # No rule, but the product names a default account: the whole
            # share goes there. A registered product settles without anyone
            # first having to author a rule for it.
            acct = (await session.execute(
                text("""SELECT id, code, name, status, account_kind,
                               gl_account_code, contra_gl_account_code
                          FROM financial_accounts WHERE id = :aid"""),
                {"aid": str(ln.default_financial_account_id)},
            )).first()
            allocations.append({
                "invoice_line_id": ln.id, "product_id": ln.product_id,
                "product_name": ln.product_name, "sku": ln.sku,
                "business_unit": ln.business_unit_name,
                "financial_account_id": acct.id,
                "account_code": acct.code, "account_name": acct.name,
                "account_status": acct.status,
                "account_kind": acct.account_kind,
                "gl_account_code": acct.gl_account_code,
                "contra_gl_account_code": acct.contra_gl_account_code,
                "rule_id": None, "rule_code": "(product default)",
                "split_id": None, "basis": "PERCENTAGE",
                "amount": line_share,
                "line_total": line_total, "line_share": line_share,
            })
            continue

        cash_claims, obl_claims = split_line(
            rule, line_total=line_total, quantity=quantity)

        # Pro-rate the full-line split down to this payment's share of it.
        # Doing it in one apportionment keeps the cents exact rather than
        # rounding each split against a scaled line and hoping they add up.
        line_amounts = apportion(line_share, [c for _, c in cash_claims])
        for (split, _full), amount in zip(cash_claims, line_amounts):
            if amount <= ZERO:
                continue
            allocations.append({
                "invoice_line_id": ln.id, "product_id": ln.product_id,
                "product_name": ln.product_name, "sku": ln.sku,
                "business_unit": ln.business_unit_name,
                "financial_account_id": split["financial_account_id"],
                "account_code": split["account_code"],
                "account_name": split["account_name"],
                "account_status": split["account_status"],
                "account_kind": split["account_kind"],
                "gl_account_code": split["gl_account_code"],
                "contra_gl_account_code": split["contra_gl_account_code"],
                "rule_id": rule["id"], "rule_code": rule["code"],
                "split_id": split["id"], "basis": rule["basis"],
                "amount": amount,
                "line_total": line_total, "line_share": line_share,
            })

        if obl_claims and line_total > ZERO:
            obl_full = money(sum(a for _, a in obl_claims))
            obl_this = money(obl_full * line_share / line_total)
            obl_amounts = apportion(obl_this, [a for _, a in obl_claims])
            for (split, _full), amount in zip(obl_claims, obl_amounts):
                if amount <= ZERO:
                    continue
                obligations.append({
                    "invoice_line_id": ln.id, "product_id": ln.product_id,
                    "product_name": ln.product_name,
                    "financial_account_id": split["financial_account_id"],
                    "account_code": split["account_code"],
                    "account_name": split["account_name"],
                    "account_status": split["account_status"],
                    "account_kind": split["account_kind"],
                    "gl_account_code": split["gl_account_code"],
                    "contra_gl_account_code": split["contra_gl_account_code"],
                    "rule_id": rule["id"], "rule_code": rule["code"],
                    "split_id": split["id"], "basis": rule["basis"],
                    "amount": amount,
                })

    allocated = money(sum(a["amount"] for a in allocations))

    status, reason = "READY", None
    if unconfigured:
        names = ", ".join(sorted({n for n, _ in unconfigured}))
        reason = (f"No settlement configuration for: {names}. Map the "
                  f"product to a financial account or author a settlement "
                  f"rule, then retry.")
        status = "FAILED" if strict_mode() else "SKIPPED"
    elif not allocations:
        status, reason = "SKIPPED", (
            "No settlement rules or product account mappings apply to this "
            "invoice, so there is nothing to distribute.")
    elif allocated != distributable:
        # Defensive: apportion guarantees this, so a mismatch means a bug
        # upstream. Better a refused settlement than a silent shortfall.
        status = "FAILED"
        reason = (f"Allocation total {allocated:,.2f} does not equal the "
                  f"distributable amount {distributable:,.2f}.")

    return {
        "payment_id": pay.id,
        "invoice_id": pay.invoice_id,
        "invoice_number": pay.invoice_number,
        "sales_order_id": pay.sales_order_id,
        "payment_method": pay.payment_method,
        "gross_amount": gross,
        "distributable": distributable,
        "allocated_amount": allocated,
        "obligation_amount": money(sum(o["amount"] for o in obligations)),
        "allocations": allocations,
        "obligations": obligations,
        "unconfigured_products": [
            {"product_id": str(pid), "product_name": name}
            for name, pid in unconfigured],
        "status": status,
        "reason": reason,
        "on": on_date,
    }


# ---------------------------------------------------------------------------
# Ledger posting
# ---------------------------------------------------------------------------

def source_account_for(payment_method: Optional[str]) -> str:
    """Which ledger account the payment itself landed in.

    Must mirror `post_customer_payment` exactly: the distribution moves money
    OUT of that account, and crediting an account the receipt never debited
    would drive it negative and leave the real one overstated.
    """
    return ACC_CASH if (payment_method or "").lower() == "cash" else ACC_BANK


async def _post_settlement_entry(
    session: AsyncSession,
    *,
    reference: str,
    entry_date: date,
    allocations: list[dict],
    obligations: list[dict],
    source_code: str,
    invoice_number: str,
    created_by: Optional[UUID] = None,
) -> Optional[UUID]:
    """Record the distribution in the general ledger.

    Cash allocations move money between the company's own accounts:
        Dr <destination> / Cr <the account the payment landed in>
    Obligations recognise a debt to a third party:
        Dr <expense> / Cr <liability>

    An allocation whose destination IS the source account is netted out rather
    than posted as Dr 1200 / Cr 1200 -- a self-cancelling pair adds nothing to
    the ledger except noise in the bank account's history.

    No-ops unless ACCOUNTING_POSTING_ENABLED, and refuses to post twice for the
    same settlement reference.
    """
    if not posting_enabled():
        return None
    if await already_posted(session, source_module="settlement",
                            source_reference=reference):
        return None

    debits: dict[str, Decimal] = defaultdict(lambda: ZERO)
    credit_total = ZERO
    for a in allocations:
        debits[a["gl_account_code"]] += a["amount"]
        credit_total += a["amount"]

    if source_code in debits:
        overlap = min(debits[source_code], credit_total)
        debits[source_code] -= overlap
        credit_total -= overlap
        if debits[source_code] <= ZERO:
            del debits[source_code]

    lines: list[Line] = [
        Line(code, debit=amount,
             description=f"Settlement of {invoice_number}")
        for code, amount in sorted(debits.items()) if amount > ZERO
    ]
    if credit_total > ZERO:
        lines.append(Line(source_code, credit=credit_total,
                          description=f"Distributed from {invoice_number}"))

    obl_pairs: dict[tuple[str, str], Decimal] = defaultdict(lambda: ZERO)
    for o in obligations:
        obl_pairs[(o["gl_account_code"], o["contra_gl_account_code"])] += o["amount"]
    for (expense_code, liability_code), amount in sorted(obl_pairs.items()):
        lines.append(Line(expense_code, debit=amount,
                          description=f"Allocation accrued, {invoice_number}"))
        lines.append(Line(liability_code, credit=amount,
                          description=f"Payable, {invoice_number}"))

    if not lines:
        return None

    return await post_entry(
        session,
        entry_date=entry_date,
        description=f"Payment distribution for {invoice_number}",
        source_module="settlement",
        source_reference=reference,
        lines=lines,
        created_by=created_by,
    )


# ---------------------------------------------------------------------------
# Distribution
# ---------------------------------------------------------------------------

async def distribute_payment(
    session: AsyncSession,
    *,
    payment_id: UUID,
    created_by: Optional[UUID] = None,
    actor_label: Optional[str] = None,
    raise_on_failure: bool = False,
) -> dict:
    """Distribute one payment to its destination accounts. THE entry point.

    Does not commit -- the caller owns the transaction, so the settlement, its
    detail rows and its journal entry land with the payment or not at all.

    Runs inside a SAVEPOINT. If planning or posting fails, only the work done
    here is rolled back; the payment survives and a FAILED settlement records
    why, so the caller's transaction stays usable and the money is never lost
    because a destination account happened to be suspended.

    Idempotent: a payment that already has a live settlement returns it
    unchanged rather than distributing again.
    """
    if not await mapd_schema_ready(session):
        return {"status": "UNAVAILABLE",
                "reason": "The MAPD schema is not installed on this database."}

    existing = (await session.execute(
        text("""SELECT id, settlement_reference, status, allocated_amount
                  FROM settlements
                 WHERE payment_id = :pid
                   AND status IN ('PENDING','COMPLETED','SKIPPED')
                 LIMIT 1"""),
        {"pid": str(payment_id)},
    )).first()
    if existing is not None:
        return {"status": existing.status,
                "settlement_id": existing.id,
                "settlement_reference": existing.settlement_reference,
                "allocated_amount": money(existing.allocated_amount),
                "reason": "Already settled; nothing further was distributed.",
                "idempotent": True}

    attempt = int((await session.execute(
        text("SELECT COUNT(*) FROM settlements WHERE payment_id = :pid"),
        {"pid": str(payment_id)},
    )).scalar() or 0) + 1

    reference = f"STL-{uuid4().hex[:12].upper()}"
    failure: Optional[str] = None
    plan: Optional[dict] = None

    savepoint = await session.begin_nested()
    try:
        plan = await build_plan(session, payment_id=payment_id)

        if plan["status"] == "READY":
            for a in plan["allocations"] + plan["obligations"]:
                if a["account_status"] != 'ACTIVE':
                    raise AccountUnavailable(
                        a["account_code"], a["account_name"],
                        a["account_status"])

            settlement_id = uuid4()
            source_code = source_account_for(plan["payment_method"])

            await session.execute(
                text("""
                    INSERT INTO settlements
                        (id, settlement_reference, payment_id, invoice_id,
                         sales_order_id, gross_amount, allocated_amount,
                         obligation_amount, status, attempt_number,
                         payment_method, source_gl_account_code,
                         distributed_at, created_by, created_at)
                    VALUES (:id, :ref, :pid, :iid, :oid, :gross, :alloc,
                            :obl, 'PENDING', :attempt, :method, :src,
                            NULL, :by, NOW())
                """),
                {
                    "id": str(settlement_id), "ref": reference,
                    "pid": str(payment_id), "iid": str(plan["invoice_id"]),
                    "oid": str(plan["sales_order_id"]) if plan["sales_order_id"] else None,
                    "gross": str(plan["gross_amount"]),
                    "alloc": str(plan["allocated_amount"]),
                    "obl": str(plan["obligation_amount"]),
                    "attempt": attempt, "method": plan["payment_method"],
                    "src": source_code,
                    "by": str(created_by) if created_by else None,
                },
            )

            for kind, rows in (("CASH", plan["allocations"]),
                               ("OBLIGATION", plan["obligations"])):
                for a in rows:
                    await session.execute(
                        text("""
                            INSERT INTO settlement_details
                                (id, settlement_id, invoice_line_id, product_id,
                                 financial_account_id, rule_id, split_id,
                                 allocation_type, basis, amount, description,
                                 created_at)
                            VALUES (gen_random_uuid(), :sid, :lid, :prod,
                                    :acct, :rule, :split, :kind, :basis,
                                    :amt, :desc, NOW())
                        """),
                        {
                            "sid": str(settlement_id),
                            "lid": str(a["invoice_line_id"]) if a.get("invoice_line_id") else None,
                            "prod": str(a["product_id"]) if a.get("product_id") else None,
                            "acct": str(a["financial_account_id"]),
                            "rule": str(a["rule_id"]) if a.get("rule_id") else None,
                            "split": str(a["split_id"]) if a.get("split_id") else None,
                            "kind": kind, "basis": a.get("basis") or "PERCENTAGE",
                            "amt": str(a["amount"]),
                            "desc": f"{a.get('product_name') or 'Line'} "
                                    f"-> {a['account_name']}",
                        },
                    )

            entry_id = await _post_settlement_entry(
                session,
                reference=reference,
                entry_date=plan["on"],
                allocations=plan["allocations"],
                obligations=plan["obligations"],
                source_code=source_code,
                invoice_number=plan["invoice_number"],
                created_by=created_by,
            )

            await session.execute(
                text("""UPDATE settlements
                           SET status = 'COMPLETED', distributed_at = NOW(),
                               journal_entry_id = :eid
                         WHERE id = :sid"""),
                {"sid": str(settlement_id),
                 "eid": str(entry_id) if entry_id else None},
            )

            await mapd_audit(
                session, event_type="SETTLEMENT_COMPLETED",
                entity_type="settlement", entity_id=settlement_id,
                payment_id=payment_id, settlement_id=settlement_id,
                actor_user_id=created_by, actor_label=actor_label,
                detail={
                    "reference": reference,
                    "invoice": plan["invoice_number"],
                    "gross": str(plan["gross_amount"]),
                    "allocated": str(plan["allocated_amount"]),
                    "obligations": str(plan["obligation_amount"]),
                    "journal_entry_id": str(entry_id) if entry_id else None,
                    "destinations": [
                        {"account": a["account_code"],
                         "product": a.get("product_name"),
                         "amount": str(a["amount"])}
                        for a in plan["allocations"]],
                },
            )
            await savepoint.commit()
            return {
                "status": "COMPLETED",
                "settlement_id": settlement_id,
                "settlement_reference": reference,
                "gross_amount": plan["gross_amount"],
                "allocated_amount": plan["allocated_amount"],
                "obligation_amount": plan["obligation_amount"],
                "journal_entry_id": entry_id,
                "allocations": plan["allocations"],
                "obligations": plan["obligations"],
                "idempotent": False,
            }

        failure = plan["reason"] or "Nothing to distribute."
        outcome = plan["status"]          # SKIPPED or FAILED
    except AccountUnavailable as e:
        await savepoint.rollback()
        failure, outcome = str(e), "FAILED"
    except HTTPException as e:
        await savepoint.rollback()
        failure, outcome = str(e.detail), "FAILED"
    except Exception as e:                                # noqa: BLE001
        await savepoint.rollback()
        failure, outcome = f"{type(e).__name__}: {e}", "FAILED"
    else:
        await savepoint.commit()

    # Record the non-distribution. Written in its own savepoint so a failure
    # here cannot poison the caller's transaction either -- the payment must
    # survive whatever the engine could not do with it.
    record = await session.begin_nested()
    try:
        gross = money((await session.execute(
            text("SELECT amount FROM payments WHERE id = :pid"),
            {"pid": str(payment_id)},
        )).scalar())
        invoice_id = (await session.execute(
            text("SELECT invoice_id FROM payments WHERE id = :pid"),
            {"pid": str(payment_id)},
        )).scalar()

        settlement_id = uuid4()
        await session.execute(
            text("""
                INSERT INTO settlements
                    (id, settlement_reference, payment_id, invoice_id,
                     gross_amount, allocated_amount, obligation_amount,
                     status, failure_reason, attempt_number, created_by,
                     created_at)
                VALUES (:id, :ref, :pid, :iid, :gross, 0, 0, :st, :why,
                        :attempt, :by, NOW())
            """),
            {"id": str(settlement_id), "ref": reference,
             "pid": str(payment_id), "iid": str(invoice_id),
             "gross": str(gross), "st": outcome, "why": failure,
             "attempt": attempt,
             "by": str(created_by) if created_by else None},
        )
        await mapd_audit(
            session,
            event_type=("SETTLEMENT_SKIPPED" if outcome == "SKIPPED"
                        else "SETTLEMENT_FAILED"),
            entity_type="settlement", entity_id=settlement_id,
            payment_id=payment_id, settlement_id=settlement_id,
            actor_user_id=created_by, actor_label=actor_label,
            detail={"reference": reference, "reason": failure,
                    "attempt": attempt},
        )
        await record.commit()
    except Exception as e:                                # noqa: BLE001
        await record.rollback()
        settlement_id = None
        failure = f"{failure} (and the failure record could not be written: {e})"

    if raise_on_failure and outcome == "FAILED":
        raise HTTPException(status_code=400, detail=failure)

    return {"status": outcome, "settlement_id": settlement_id,
            "settlement_reference": reference, "reason": failure,
            "idempotent": False}


async def distribute_payment_safely(
    session: AsyncSession, *, payment_id: UUID,
    created_by: Optional[UUID] = None,
) -> Optional[dict]:
    """The hook used by record_payment: never breaks the payment.

    A payment is a fact about money that has already changed hands. The one
    thing this engine must never do is refuse to record that fact because a
    downstream configuration is wrong -- so the payment path calls this, which
    reports rather than raises, and the settlement is queued for retry.
    """
    if not mapd_enabled():
        return None

    # The whole call runs inside a SAVEPOINT, not just the parts
    # distribute_payment guards itself. In PostgreSQL any failed statement
    # poisons the entire transaction until it is rolled back -- so an
    # unexpected error out here (a missing table on a half-migrated database,
    # say) would abort the caller's transaction and take the PAYMENT down with
    # it. Rolling back to a savepoint restores a usable transaction, which is
    # what lets the payment commit even when distribution could not run.
    outer = await session.begin_nested()
    try:
        if not await mapd_schema_ready(session):
            await outer.commit()
            return None
        result = await distribute_payment(
            session, payment_id=payment_id, created_by=created_by,
            actor_label="auto (payment received)")
        await outer.commit()
        return result
    except Exception as e:                                # noqa: BLE001
        # distribute_payment handles its own failures; reaching here means
        # something outside that guard broke. Log loudly and let the payment
        # stand -- no settlement row exists, so the payment shows up in
        # /api/payments/undistributed and settlement_health flags it.
        await outer.rollback()
        print(f"[MAPD] distribution hook failed for payment {payment_id}: {e}")
        return {"status": "ERROR", "reason": str(e)}


async def retry_failed_settlements(
    session: AsyncSession, *, limit: int = 50,
    created_by: Optional[UUID] = None,
) -> dict:
    """Re-attempt payments whose distribution failed.

    Failures here are overwhelmingly transient-by-configuration -- a suspended
    account, a rule not yet authored. Retrying costs nothing once the cause is
    fixed, and the alternative is an operator hunting for payments that never
    reached their accounts.
    """
    rows = (await session.execute(
        text("""
            SELECT DISTINCT ON (s.payment_id) s.payment_id, s.failure_reason
              FROM settlements s
             WHERE s.status = 'FAILED'
               AND NOT EXISTS (
                   SELECT 1 FROM settlements live
                    WHERE live.payment_id = s.payment_id
                      AND live.status IN ('PENDING','COMPLETED','SKIPPED'))
             ORDER BY s.payment_id, s.created_at DESC
             LIMIT :lim
        """),
        {"lim": limit},
    )).fetchall()

    results = {"attempted": 0, "settled": 0, "still_failing": 0, "details": []}
    for row in rows:
        results["attempted"] += 1
        outcome = await distribute_payment(
            session, payment_id=row.payment_id, created_by=created_by,
            actor_label="retry")
        if outcome["status"] == "COMPLETED":
            results["settled"] += 1
        else:
            results["still_failing"] += 1
        results["details"].append({
            "payment_id": str(row.payment_id),
            "status": outcome["status"],
            "reason": outcome.get("reason"),
        })
    return results


# ---------------------------------------------------------------------------
# Refunds
# ---------------------------------------------------------------------------

async def refund_settlement(
    session: AsyncSession,
    *,
    settlement_id: UUID,
    reason: str,
    amount=None,
    created_by: Optional[UUID] = None,
    approved_by: Optional[UUID] = None,
    actor_label: Optional[str] = None,
) -> dict:
    """Unwind a distribution: every allocation reversed, nothing deleted.

    A full refund (amount omitted) mirrors the original journal entry exactly
    and marks the settlement REVERSED, which releases its lines' capacity so a
    later payment redistributes them correctly.

    A partial refund posts a proportional counter-entry instead, apportioned
    across the original destinations so each account gives back its share --
    taking it all from one account would leave the others overstated.

    This reverses the DISTRIBUTION. The customer-facing credit (returning the
    money and reversing revenue) belongs to the returns module; doing both here
    would double-count the reversal.
    """
    stl = (await session.execute(
        text("""SELECT s.id, s.settlement_reference, s.status, s.gross_amount,
                       s.allocated_amount, s.journal_entry_id, s.payment_id,
                       s.invoice_id, s.source_gl_account_code,
                       i.invoice_number
                  FROM settlements s
                  JOIN invoices i ON i.id = s.invoice_id
                 WHERE s.id = :sid FOR UPDATE OF s"""),
        {"sid": str(settlement_id)},
    )).first()
    if stl is None:
        raise HTTPException(status_code=404, detail="Settlement not found.")
    if stl.status != 'COMPLETED':
        raise HTTPException(
            status_code=400,
            detail=f"Only a COMPLETED settlement can be refunded; this one is "
                   f"{stl.status}.")

    allocated = money(stl.allocated_amount)
    refund_amount = allocated if amount is None else money(amount)
    if refund_amount <= ZERO:
        raise HTTPException(
            status_code=400, detail="Refund amount must be greater than zero.")

    already = money((await session.execute(
        text("""SELECT COALESCE(SUM(amount), 0) FROM mapd_refunds
                 WHERE settlement_id = :sid AND status = 'COMPLETED'"""),
        {"sid": str(settlement_id)},
    )).scalar())
    if refund_amount + already - allocated > CENT:
        raise HTTPException(
            status_code=400,
            detail=(f"Refunding {refund_amount:,.2f} would exceed the "
                    f"{money(allocated - already):,.2f} still refundable on "
                    f"settlement {stl.settlement_reference}."))

    is_full = refund_amount + already >= allocated - CENT
    refund_reference = f"RFD-{uuid4().hex[:12].upper()}"
    entry_id = None

    if posting_enabled():
        if is_full and already == ZERO and stl.journal_entry_id:
            entry_id = await reverse_entry(
                session, entry_id=stl.journal_entry_id,
                reason=f"Refund {refund_reference}: {reason}",
                created_by=created_by)
        else:
            details = (await session.execute(
                text("""SELECT d.allocation_type, d.amount,
                               fa.gl_account_code, fa.contra_gl_account_code
                          FROM settlement_details d
                          JOIN financial_accounts fa
                            ON fa.id = d.financial_account_id
                         WHERE d.settlement_id = :sid
                         ORDER BY d.created_at, d.id"""),
                {"sid": str(settlement_id)},
            )).fetchall()
            cash = [d for d in details if d.allocation_type == 'CASH']
            shares = apportion(refund_amount, [money(d.amount) for d in cash])

            credits: dict[str, Decimal] = defaultdict(lambda: ZERO)
            for d, share in zip(cash, shares):
                if share > ZERO:
                    credits[d.gl_account_code] += share
            source = stl.source_gl_account_code or ACC_BANK
            debit_total = money(sum(credits.values()))
            if source in credits:
                overlap = min(credits[source], debit_total)
                credits[source] -= overlap
                debit_total -= overlap
                if credits[source] <= ZERO:
                    del credits[source]

            lines = []
            if debit_total > ZERO:
                lines.append(Line(source, debit=debit_total,
                                  description=f"Refund {refund_reference}"))
                lines += [Line(code, credit=amt,
                               description=f"Allocation returned, "
                                           f"{stl.invoice_number}")
                          for code, amt in sorted(credits.items()) if amt > ZERO]
            if lines:
                entry_id = await post_entry(
                    session,
                    entry_date=date.today(),
                    description=(f"Partial refund of settlement "
                                 f"{stl.settlement_reference}: {reason}"),
                    source_module="settlement.refund",
                    source_reference=refund_reference,
                    lines=lines,
                    created_by=created_by,
                )

    await session.execute(
        text("""
            INSERT INTO mapd_refunds
                (id, refund_reference, settlement_id, payment_id, invoice_id,
                 amount, is_full_reversal, reason, status, journal_entry_id,
                 approved_by, created_by, created_at)
            VALUES (gen_random_uuid(), :ref, :sid, :pid, :iid, :amt, :full,
                    :reason, 'COMPLETED', :eid, :appr, :by, NOW())
        """),
        {"ref": refund_reference, "sid": str(settlement_id),
         "pid": str(stl.payment_id), "iid": str(stl.invoice_id),
         "amt": str(refund_amount), "full": is_full, "reason": reason,
         "eid": str(entry_id) if entry_id else None,
         "appr": str(approved_by) if approved_by else None,
         "by": str(created_by) if created_by else None},
    )

    if is_full:
        # REVERSED drops out of the "already sent" calculation, so the invoice
        # lines regain their capacity and a subsequent payment redistributes
        # them from scratch.
        await session.execute(
            text("UPDATE settlements SET status = 'REVERSED' WHERE id = :sid"),
            {"sid": str(settlement_id)},
        )

    await mapd_audit(
        session, event_type="SETTLEMENT_REFUNDED",
        entity_type="refund", entity_id=settlement_id,
        payment_id=stl.payment_id, settlement_id=settlement_id,
        actor_user_id=created_by, actor_label=actor_label,
        detail={"refund_reference": refund_reference,
                "settlement": stl.settlement_reference,
                "amount": str(refund_amount), "full_reversal": is_full,
                "reason": reason,
                "journal_entry_id": str(entry_id) if entry_id else None},
    )

    return {
        "refund_reference": refund_reference,
        "settlement_reference": stl.settlement_reference,
        "amount": refund_amount,
        "is_full_reversal": is_full,
        "journal_entry_id": entry_id,
        "settlement_status": 'REVERSED' if is_full else 'COMPLETED',
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

async def settlement_health(session: AsyncSession) -> dict:
    """Is every payment accounted for, and does every settlement add up?

    Three questions an auditor would ask, answered from the rows rather than
    from a status flag:
      * is any received payment still undistributed?
      * does any settlement's details disagree with its header?
      * did any settlement send more than the payment brought in?
    """
    if not await mapd_schema_ready(session):
        return {"healthy": None,
                "summary": "MAPD schema is not installed on this database."}

    undistributed = (await session.execute(
        text("""
            SELECT COUNT(*) AS n, COALESCE(SUM(p.amount), 0) AS total
              FROM payments p
             WHERE NOT EXISTS (SELECT 1 FROM settlements s
                                WHERE s.payment_id = p.id
                                  AND s.status IN ('PENDING','COMPLETED','SKIPPED'))
        """)
    )).first()

    failed = (await session.execute(
        text("""
            SELECT COUNT(DISTINCT s.payment_id) AS n,
                   COALESCE(SUM(s.gross_amount), 0) AS total
              FROM settlements s
             WHERE s.status = 'FAILED'
               AND NOT EXISTS (SELECT 1 FROM settlements live
                                WHERE live.payment_id = s.payment_id
                                  AND live.status IN ('PENDING','COMPLETED','SKIPPED'))
        """)
    )).first()

    mismatched = (await session.execute(
        text("""
            SELECT s.settlement_reference, s.gross_amount, s.allocated_amount,
                   COALESCE(SUM(d.amount) FILTER (
                       WHERE d.allocation_type = 'CASH'), 0) AS detail_total
              FROM settlements s
              LEFT JOIN settlement_details d ON d.settlement_id = s.id
             WHERE s.status = 'COMPLETED'
             GROUP BY s.id, s.settlement_reference, s.gross_amount,
                      s.allocated_amount
            HAVING COALESCE(SUM(d.amount) FILTER (
                       WHERE d.allocation_type = 'CASH'), 0)
                   <> s.allocated_amount
                OR s.allocated_amount > s.gross_amount
             LIMIT 100
        """)
    )).fetchall()

    healthy = (failed.n == 0 and not mismatched)
    return {
        "healthy": healthy,
        "undistributed_payments": int(undistributed.n or 0),
        "undistributed_value": float(money(undistributed.total)),
        "failed_settlements": int(failed.n or 0),
        "failed_value": float(money(failed.total)),
        "mismatched_settlements": [
            {"reference": r.settlement_reference,
             "gross": float(money(r.gross_amount)),
             "header_allocated": float(money(r.allocated_amount)),
             "detail_total": float(money(r.detail_total))}
            for r in mismatched
        ],
        "summary": (
            "Every payment is distributed and every settlement reconciles."
            if healthy else
            f"{int(failed.n or 0)} payment(s) failed to distribute"
            + (f" and {len(mismatched)} settlement(s) do not reconcile"
               if mismatched else "") + "."),
    }
