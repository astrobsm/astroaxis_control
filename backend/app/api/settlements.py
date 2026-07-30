"""MAPD API: payments in, allocations out, and the configuration behind them.

Three routers, split by what they are about rather than by which file they
happen to live in:

  /api/payments   receiving money and distributing it
  /api/finance    the configuration that decides where it goes
  /api/reports    what happened, for finance and for auditors

ACCESS
------
Reading is open to any authenticated user; the dashboards are for the whole
business. Everything that changes where money goes -- accounts, rules, product
mappings, refunds -- is admin-only, because a settlement rule is a standing
instruction to move company funds.

Refunds additionally require a SECOND admin to authenticate in the request
body. That is genuine dual control, not a confirmation checkbox: reversing an
allocation moves money back out of accounts other people are reconciling
against, and one compromised session should not be enough to do it.
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.api.auth import require_admin, require_authenticated_user, verify_password
from app.models import User
from app.services.encryption import decrypt_secret, encrypt_secret, has_secret
from app.services.ledger import money
from app.services.receivables import ensure_invoice_for_order, record_payment
from app.services.settlement import (
    build_plan, distribute_payment, mapd_audit, mapd_schema_ready,
    refund_settlement, retry_failed_settlements, settlement_health)

router = APIRouter(prefix='/api/payments', tags=['Payments & Distribution'])
finance_router = APIRouter(prefix='/api/finance', tags=['Finance Accounts'])
reports_router = APIRouter(prefix='/api/reports', tags=['Finance Reports'])


async def _require_schema(session: AsyncSession) -> None:
    if not await mapd_schema_ready(session):
        raise HTTPException(
            status_code=503,
            detail="The payment distribution module is not installed on this "
                   "database. Run the s8901234567r migration.")


def _mask(account_number_enc: Optional[str]) -> Optional[str]:
    """Show the last four digits only.

    A full bank account number has no business being returned to a browser
    just because someone opened the accounts list.
    """
    if not has_secret(account_number_enc):
        return None
    plain = decrypt_secret(account_number_enc)
    if not plain:
        return "****"
    return f"****{plain[-4:]}" if len(plain) > 4 else "****"


# ===========================================================================
# Schemas
# ===========================================================================

class InitiateIn(BaseModel):
    sales_order_id: Optional[UUID] = None
    invoice_id: Optional[UUID] = None
    payment_method: str = Field('bank_transfer', min_length=1)

    @model_validator(mode='after')
    def _one_target(self):
        if not self.sales_order_id and not self.invoice_id:
            raise ValueError("Provide either sales_order_id or invoice_id.")
        return self


class VerifyIn(BaseModel):
    invoice_id: UUID
    amount: float = Field(..., gt=0)
    payment_method: str = Field('bank_transfer', min_length=1)
    # The reference is what makes verification idempotent. A gateway callback
    # that fires twice, or an operator who double-clicks, must not produce two
    # payments -- so the same reference against the same invoice returns the
    # payment already recorded rather than recording another.
    reference: str = Field(..., min_length=3)
    gateway_reference: Optional[str] = None
    notes: Optional[str] = None
    payment_date: Optional[date] = None


class DistributeIn(BaseModel):
    payment_id: Optional[UUID] = None
    retry_failed: bool = False
    limit: int = Field(50, ge=1, le=500)

    @model_validator(mode='after')
    def _one_mode(self):
        if not self.payment_id and not self.retry_failed:
            raise ValueError(
                "Provide payment_id, or set retry_failed to re-attempt every "
                "failed distribution.")
        return self


class RefundIn(BaseModel):
    settlement_id: Optional[UUID] = None
    payment_id: Optional[UUID] = None
    amount: Optional[float] = Field(None, gt=0)
    reason: str = Field(..., min_length=5)
    # Dual control: a second admin authenticates here.
    approver_email: str = Field(..., min_length=3)
    approver_password: str = Field(..., min_length=1)

    @model_validator(mode='after')
    def _one_target(self):
        if not self.settlement_id and not self.payment_id:
            raise ValueError("Provide settlement_id or payment_id.")
        return self


class BusinessUnitIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=32)
    name: str = Field(..., min_length=1)
    description: Optional[str] = None
    is_active: bool = True


class RevenueCenterIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=32)
    name: str = Field(..., min_length=1)
    business_unit_id: Optional[UUID] = None
    is_active: bool = True


class FinancialAccountIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=32)
    name: str = Field(..., min_length=1)
    account_kind: str = Field('BANK')
    gl_account_code: str = Field(..., min_length=1)
    contra_gl_account_code: Optional[str] = None
    bank_name: Optional[str] = None
    account_number: Optional[str] = None
    account_name: Optional[str] = None
    currency: str = 'NGN'
    business_unit_id: Optional[UUID] = None
    status: str = 'ACTIVE'
    description: Optional[str] = None

    @model_validator(mode='after')
    def _check(self):
        kinds = {'BANK', 'CASH', 'WALLET', 'VIRTUAL', 'OBLIGATION'}
        if self.account_kind not in kinds:
            raise ValueError(f"account_kind must be one of {sorted(kinds)}")
        if self.status not in {'ACTIVE', 'SUSPENDED', 'CLOSED'}:
            raise ValueError("status must be ACTIVE, SUSPENDED or CLOSED")
        if self.account_kind == 'OBLIGATION' and not self.contra_gl_account_code:
            raise ValueError(
                "An OBLIGATION account needs contra_gl_account_code: the "
                "liability credited when the expense is debited.")
        return self


class FinancialAccountUpdate(BaseModel):
    name: Optional[str] = None
    bank_name: Optional[str] = None
    account_number: Optional[str] = None
    account_name: Optional[str] = None
    business_unit_id: Optional[UUID] = None
    status: Optional[str] = None
    description: Optional[str] = None

    @model_validator(mode='after')
    def _check(self):
        if self.status and self.status not in {'ACTIVE', 'SUSPENDED', 'CLOSED'}:
            raise ValueError("status must be ACTIVE, SUSPENDED or CLOSED")
        return self


class ProductAccountIn(BaseModel):
    product_id: UUID
    sales_account_code: Optional[str] = None
    cost_account_code: Optional[str] = None
    inventory_account_code: Optional[str] = None
    tax_group: Optional[str] = None
    business_unit_id: Optional[UUID] = None
    revenue_center_id: Optional[UUID] = None
    settlement_priority: int = 100
    default_financial_account_id: Optional[UUID] = None
    notes: Optional[str] = None


class SplitIn(BaseModel):
    financial_account_id: UUID
    allocation_type: str = 'CASH'
    percentage: Optional[float] = Field(None, ge=0, le=100)
    fixed_amount: Optional[float] = Field(None, ge=0)
    rate_per_unit: Optional[float] = Field(None, ge=0)
    is_residual: bool = False
    sort_order: int = 0
    description: Optional[str] = None

    @model_validator(mode='after')
    def _one_basis(self):
        given = sum(x is not None for x in
                    (self.percentage, self.fixed_amount, self.rate_per_unit))
        given += 1 if self.is_residual else 0
        if given != 1:
            raise ValueError(
                "A split needs exactly one of: percentage, fixed_amount, "
                "rate_per_unit, or is_residual.")
        if self.allocation_type not in {'CASH', 'OBLIGATION'}:
            raise ValueError("allocation_type must be CASH or OBLIGATION")
        if self.is_residual and self.allocation_type == 'OBLIGATION':
            raise ValueError(
                "An OBLIGATION split cannot be the residual: an obligation is "
                "a stated share of the sale, not whatever is left over.")
        return self


class RuleIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1)
    scope: str = 'PRODUCT'
    product_id: Optional[UUID] = None
    business_unit_id: Optional[UUID] = None
    basis: str = 'PERCENTAGE'
    priority: int = 100
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    is_active: bool = True
    description: Optional[str] = None
    splits: List[SplitIn] = Field(..., min_length=1)

    @model_validator(mode='after')
    def _check(self):
        if self.scope not in {'PRODUCT', 'BUSINESS_UNIT', 'GLOBAL'}:
            raise ValueError("scope must be PRODUCT, BUSINESS_UNIT or GLOBAL")
        if self.basis not in {'PERCENTAGE', 'FIXED', 'PER_UNIT'}:
            raise ValueError("basis must be PERCENTAGE, FIXED or PER_UNIT")
        if self.scope == 'PRODUCT' and not self.product_id:
            raise ValueError("A PRODUCT rule needs a product_id.")
        if self.scope == 'BUSINESS_UNIT' and not self.business_unit_id:
            raise ValueError("A BUSINESS_UNIT rule needs a business_unit_id.")

        cash = [s for s in self.splits if s.allocation_type == 'CASH']
        if not cash:
            raise ValueError(
                "A rule needs at least one CASH split -- money that arrives "
                "has to land somewhere.")
        if sum(1 for s in cash if s.is_residual) > 1:
            raise ValueError("Only one CASH split can be the residual.")

        # Percentage rules must account for the whole line. Caught here rather
        # than at settlement time: a rule that allocates 90% would otherwise
        # look configured and then fail on a live customer payment.
        pct = [s.percentage for s in cash if s.percentage is not None]
        if pct and len(pct) == len(cash):
            total = round(sum(pct), 6)
            if abs(total - 100) > 0.0001:
                raise ValueError(
                    f"CASH percentages total {total}%, not 100%. Add a "
                    f"residual split or correct the percentages.")

        obl_pct = sum(s.percentage or 0 for s in self.splits
                      if s.allocation_type == 'OBLIGATION')
        if obl_pct > 100:
            raise ValueError(
                f"OBLIGATION percentages total {obl_pct}%, which would commit "
                f"more than the sale is worth.")
        return self


# ===========================================================================
# /api/payments
# ===========================================================================

@router.get('/methods')
async def list_payment_methods(
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    await _require_schema(session)
    rows = (await session.execute(
        text("""SELECT code, name, category, requires_reference, is_active
                  FROM payment_methods WHERE is_active
                 ORDER BY sort_order, name""")
    )).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post('/initiate')
async def initiate_payment(
    body: InitiateIn,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_authenticated_user),
):
    """Open a payment: make sure there is an invoice, and quote what is owed.

    Returns the reference the payer should quote. No payment row is created --
    money has not moved yet, and recording a receipt before it has is exactly
    how a debtors ledger stops being trustworthy. The initiation is written to
    the audit log so the later verification can be matched against what was
    quoted.
    """
    await _require_schema(session)
    try:
        invoice_id = body.invoice_id
        if invoice_id is None:
            invoice_id = await ensure_invoice_for_order(
                session, order_id=body.sales_order_id,
                created_by=current_user.id)

        inv = (await session.execute(
            text("""SELECT i.id, i.invoice_number, i.total_amount,
                           i.sales_order_id, c.name AS customer_name,
                           COALESCE((SELECT SUM(p.amount) FROM payments p
                                      WHERE p.invoice_id = i.id), 0) AS paid
                      FROM invoices i
                      LEFT JOIN customers c ON c.id = i.customer_id
                     WHERE i.id = :iid"""),
            {"iid": str(invoice_id)},
        )).first()
        if inv is None:
            raise HTTPException(status_code=404, detail="Invoice not found.")

        due = money(money(inv.total_amount) - money(inv.paid))
        if due <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"Invoice {inv.invoice_number} is already settled in "
                       f"full; there is nothing to pay.")

        reference = f"PAYREF-{uuid4().hex[:12].upper()}"

        # Preview the split now so the operator can see where this money will
        # go before the customer pays, and so a misconfiguration surfaces
        # before the money arrives rather than after.
        preview = await _preview_for_invoice(session, invoice_id, due)

        await mapd_audit(
            session, event_type="PAYMENT_INITIATED",
            entity_type="invoice", entity_id=invoice_id,
            actor_user_id=current_user.id, actor_label=current_user.email,
            detail={"reference": reference, "amount_due": str(due),
                    "invoice": inv.invoice_number,
                    "payment_method": body.payment_method},
        )
        await session.commit()

        return {
            "reference": reference,
            "invoice_id": str(invoice_id),
            "invoice_number": inv.invoice_number,
            "sales_order_id": str(inv.sales_order_id) if inv.sales_order_id else None,
            "customer_name": inv.customer_name,
            "invoice_total": float(money(inv.total_amount)),
            "already_paid": float(money(inv.paid)),
            "amount_due": float(due),
            "payment_method": body.payment_method,
            "distribution_preview": preview,
        }
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:                                # noqa: BLE001
        await session.rollback()
        raise HTTPException(
            status_code=400, detail=f"Could not initiate payment: {e}")


async def _preview_for_invoice(session: AsyncSession, invoice_id, amount) -> dict:
    """Show the split a hypothetical payment would produce.

    Built by simulating against the invoice's rules directly rather than by
    writing a throwaway payment: a preview must not be able to leave anything
    behind in the payments table.
    """
    rows = (await session.execute(
        text("""
            SELECT il.id, il.product_id, il.quantity, il.line_total,
                   pr.name AS product_name,
                   pa.business_unit_id, pa.default_financial_account_id
              FROM invoice_lines il
              LEFT JOIN products pr ON pr.id = il.product_id
              LEFT JOIN product_accounts pa ON pa.product_id = il.product_id
             WHERE il.invoice_id = :iid
             ORDER BY COALESCE(pa.settlement_priority, 100), il.id
        """),
        {"iid": str(invoice_id)},
    )).fetchall()

    from decimal import Decimal
    from app.services.settlement import apportion, resolve_rule, split_line

    capacity = [money(r.line_total) for r in rows]
    shares = apportion(money(amount), capacity)

    destinations, unconfigured = [], []
    for r, share in zip(rows, shares):
        if share <= 0:
            continue
        rule = await resolve_rule(
            session, product_id=r.product_id,
            business_unit_id=r.business_unit_id, on=date.today())
        if rule is None:
            if r.default_financial_account_id is None:
                unconfigured.append(r.product_name or "Unnamed product")
                continue
            acct = (await session.execute(
                text("SELECT code, name FROM financial_accounts WHERE id = :a"),
                {"a": str(r.default_financial_account_id)},
            )).first()
            destinations.append({
                "product": r.product_name, "account_code": acct.code,
                "account_name": acct.name, "amount": float(share),
                "rule": "(product default)", "allocation_type": "CASH"})
            continue
        try:
            cash, obl = split_line(
                rule, line_total=money(r.line_total),
                quantity=Decimal(str(r.quantity or 0)))
        except HTTPException as e:
            unconfigured.append(f"{r.product_name}: {e.detail}")
            continue
        amounts = apportion(share, [c for _, c in cash])
        for (s, _), amt in zip(cash, amounts):
            if amt > 0:
                destinations.append({
                    "product": r.product_name, "account_code": s["account_code"],
                    "account_name": s["account_name"], "amount": float(amt),
                    "rule": rule["code"], "allocation_type": "CASH"})
        for s, full in obl:
            scaled = money(full * share / money(r.line_total)) \
                if money(r.line_total) > 0 else money(0)
            if scaled > 0:
                destinations.append({
                    "product": r.product_name, "account_code": s["account_code"],
                    "account_name": s["account_name"], "amount": float(scaled),
                    "rule": rule["code"], "allocation_type": "OBLIGATION"})

    return {"destinations": destinations,
            "unconfigured_products": unconfigured,
            "ready": not unconfigured and bool(destinations)}


@router.post('/verify')
async def verify_payment(
    body: VerifyIn,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_authenticated_user),
):
    """Confirm money was received, then distribute it. Idempotent.

    Re-verifying the same reference against the same invoice returns the
    payment already on file. Gateways retry callbacks, operators double-click,
    and networks time out after the write succeeded -- any of which would
    otherwise book the customer's money twice.
    """
    await _require_schema(session)
    try:
        existing = (await session.execute(
            text("""SELECT id, amount FROM payments
                     WHERE invoice_id = :iid AND reference = :ref
                     LIMIT 1"""),
            {"iid": str(body.invoice_id), "ref": body.reference},
        )).first()
        if existing is not None:
            settlement = (await session.execute(
                text("""SELECT settlement_reference, status, allocated_amount
                          FROM settlements WHERE payment_id = :pid
                         ORDER BY created_at DESC LIMIT 1"""),
                {"pid": str(existing.id)},
            )).first()
            await session.rollback()
            return {
                "success": True, "idempotent": True,
                "payment_id": str(existing.id),
                "amount": float(money(existing.amount)),
                "message": "This reference was already verified; the payment "
                           "on file was returned unchanged.",
                "settlement": (dict(settlement._mapping) if settlement else None),
            }

        notes = body.notes
        if body.gateway_reference:
            notes = f"{notes + ' | ' if notes else ''}gateway:{body.gateway_reference}"

        await mapd_audit(
            session, event_type="PAYMENT_VERIFIED",
            entity_type="invoice", entity_id=body.invoice_id,
            actor_user_id=current_user.id, actor_label=current_user.email,
            detail={"reference": body.reference, "amount": str(body.amount),
                    "method": body.payment_method,
                    "gateway_reference": body.gateway_reference},
        )

        result = await record_payment(
            session,
            invoice_id=body.invoice_id,
            amount=body.amount,
            payment_method=body.payment_method,
            reference=body.reference,
            notes=notes,
            payment_date=body.payment_date,
        )
        await session.commit()

        settlement = result.get("settlement") or {}
        return {
            "success": True, "idempotent": False,
            "payment_id": str(result["payment_id"]),
            "amount": float(result["amount"]),
            "invoice_total_paid": float(result["total_paid"]),
            "invoice_balance": float(result["balance"]),
            "invoice_status": result["status"],
            "settlement": {
                "status": settlement.get("status"),
                "reference": settlement.get("settlement_reference"),
                "allocated_amount": float(settlement["allocated_amount"])
                if settlement.get("allocated_amount") is not None else None,
                "reason": settlement.get("reason"),
                "allocations": [
                    {"product": a.get("product_name"),
                     "account_code": a["account_code"],
                     "account_name": a["account_name"],
                     "amount": float(a["amount"])}
                    for a in settlement.get("allocations", [])],
            } if settlement else None,
        }
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:                                # noqa: BLE001
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Verification failed: {e}")


@router.post('/distribute')
async def distribute(
    body: DistributeIn,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Distribute a payment, or re-attempt every failed distribution.

    The automatic hook on record_payment covers the normal path; this exists
    for payments taken before the module was configured, and for retrying once
    a suspended account is back.
    """
    await _require_schema(session)
    try:
        if body.retry_failed:
            result = await retry_failed_settlements(
                session, limit=body.limit, created_by=current_user.id)
            await session.commit()
            return {"success": True, **result}

        result = await distribute_payment(
            session, payment_id=body.payment_id, created_by=current_user.id,
            actor_label=current_user.email)
        await session.commit()
        return {
            "success": result["status"] in ('COMPLETED', 'SKIPPED'),
            "status": result["status"],
            "settlement_reference": result.get("settlement_reference"),
            "allocated_amount": float(result["allocated_amount"])
            if result.get("allocated_amount") is not None else None,
            "reason": result.get("reason"),
            "idempotent": result.get("idempotent", False),
            "allocations": [
                {"product": a.get("product_name"),
                 "account_code": a["account_code"],
                 "account_name": a["account_name"],
                 "amount": float(a["amount"])}
                for a in result.get("allocations", [])],
        }
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:                                # noqa: BLE001
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Distribution failed: {e}")


@router.get('/undistributed')
async def list_undistributed(
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    """Money received that has not reached its destination accounts."""
    await _require_schema(session)
    rows = (await session.execute(
        text("""
            SELECT p.id, p.amount, p.payment_date, p.payment_method,
                   p.reference, i.invoice_number, c.name AS customer_name,
                   (SELECT s.failure_reason FROM settlements s
                     WHERE s.payment_id = p.id
                     ORDER BY s.created_at DESC LIMIT 1) AS last_failure
              FROM payments p
              JOIN invoices i ON i.id = p.invoice_id
              LEFT JOIN customers c ON c.id = i.customer_id
             WHERE NOT EXISTS (
                   SELECT 1 FROM settlements s
                    WHERE s.payment_id = p.id
                      AND s.status IN ('PENDING','COMPLETED','SKIPPED'))
             ORDER BY p.payment_date DESC
             LIMIT :lim
        """),
        {"lim": limit},
    )).fetchall()
    return [
        {"payment_id": str(r.id), "amount": float(money(r.amount)),
         "payment_date": r.payment_date.isoformat() if r.payment_date else None,
         "payment_method": r.payment_method, "reference": r.reference,
         "invoice_number": r.invoice_number, "customer_name": r.customer_name,
         "last_failure": r.last_failure}
        for r in rows
    ]


@router.post('/refund')
async def refund(
    body: RefundIn,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Reverse a distribution. Requires a second admin to authorise.

    Dual control is the point: reversing an allocation takes money back out of
    accounts other people are reconciling against, so it needs two people who
    both have the authority, not one session that happens to be open.

    This reverses the DISTRIBUTION only. Refunding the customer and reversing
    the revenue is the returns module's job -- doing both here would reverse
    the sale twice.
    """
    await _require_schema(session)

    approver = (await session.execute(
        select(User).where(
            func.lower(User.email) == body.approver_email.lower().strip())
    )).scalar_one_or_none()
    if approver is None or not verify_password(
            body.approver_password, approver.hashed_password):
        raise HTTPException(
            status_code=403, detail="Approver credentials are not valid.")
    if approver.id == current_user.id:
        raise HTTPException(
            status_code=403,
            detail="The approver must be a different administrator; a refund "
                   "cannot be authorised by the person requesting it.")
    if approver.role != 'admin' or not approver.is_active or approver.is_locked:
        raise HTTPException(
            status_code=403,
            detail="The approver must be an active administrator.")

    try:
        settlement_id = body.settlement_id
        if settlement_id is None:
            row = (await session.execute(
                text("""SELECT id FROM settlements
                         WHERE payment_id = :pid AND status = 'COMPLETED'
                         ORDER BY created_at DESC LIMIT 1"""),
                {"pid": str(body.payment_id)},
            )).first()
            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail="No completed settlement found for that payment.")
            settlement_id = row.id

        result = await refund_settlement(
            session, settlement_id=settlement_id, reason=body.reason,
            amount=body.amount, created_by=current_user.id,
            approved_by=approver.id,
            actor_label=f"{current_user.email} approved by {approver.email}")
        await session.commit()
        return {
            "success": True,
            "refund_reference": result["refund_reference"],
            "settlement_reference": result["settlement_reference"],
            "amount": float(result["amount"]),
            "is_full_reversal": result["is_full_reversal"],
            "settlement_status": result["settlement_status"],
            "approved_by": approver.email,
        }
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:                                # noqa: BLE001
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Refund failed: {e}")


@router.get('/health')
async def health(
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    return await settlement_health(session)


@router.get('/{payment_id}')
async def get_payment(
    payment_id: UUID,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    """One payment, its invoice, and what became of it."""
    await _require_schema(session)
    pay = (await session.execute(
        text("""
            SELECT p.id, p.amount, p.payment_method, p.payment_date,
                   p.reference, p.notes, i.id AS invoice_id, i.invoice_number,
                   i.total_amount, i.paid_amount, i.status AS invoice_status,
                   c.name AS customer_name, so.order_number
              FROM payments p
              JOIN invoices i ON i.id = p.invoice_id
              LEFT JOIN customers c ON c.id = i.customer_id
              LEFT JOIN sales_orders so ON so.id = i.sales_order_id
             WHERE p.id = :pid
        """),
        {"pid": str(payment_id)},
    )).first()
    if pay is None:
        raise HTTPException(status_code=404, detail="Payment not found.")

    settlements = (await session.execute(
        text("""SELECT id, settlement_reference, status, gross_amount,
                       allocated_amount, obligation_amount, failure_reason,
                       distributed_at, created_at
                  FROM settlements WHERE payment_id = :pid
                 ORDER BY created_at DESC"""),
        {"pid": str(payment_id)},
    )).fetchall()

    return {
        "payment_id": str(pay.id),
        "amount": float(money(pay.amount)),
        "payment_method": pay.payment_method,
        "payment_date": pay.payment_date.isoformat() if pay.payment_date else None,
        "reference": pay.reference,
        "notes": pay.notes,
        "invoice": {
            "id": str(pay.invoice_id), "invoice_number": pay.invoice_number,
            "order_number": pay.order_number,
            "customer_name": pay.customer_name,
            "total_amount": float(money(pay.total_amount)),
            "paid_amount": float(money(pay.paid_amount)),
            "status": pay.invoice_status,
        },
        "settlements": [
            {"id": str(s.id), "reference": s.settlement_reference,
             "status": s.status,
             "gross_amount": float(money(s.gross_amount)),
             "allocated_amount": float(money(s.allocated_amount)),
             "obligation_amount": float(money(s.obligation_amount)),
             "failure_reason": s.failure_reason,
             "distributed_at": s.distributed_at.isoformat() if s.distributed_at else None,
             "created_at": s.created_at.isoformat() if s.created_at else None}
            for s in settlements
        ],
    }


@router.get('/{payment_id}/settlements')
async def get_payment_settlements(
    payment_id: UUID,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    """Every allocation this payment produced, to the naira."""
    await _require_schema(session)
    rows = (await session.execute(
        text("""
            SELECT s.id AS settlement_id, s.settlement_reference, s.status,
                   s.gross_amount, s.allocated_amount, s.obligation_amount,
                   s.failure_reason, s.distributed_at, s.journal_entry_id,
                   d.id AS detail_id, d.allocation_type, d.basis, d.amount,
                   d.description, pr.name AS product_name, pr.sku,
                   fa.code AS account_code, fa.name AS account_name,
                   fa.gl_account_code, r.code AS rule_code,
                   bu.name AS business_unit
              FROM settlements s
              LEFT JOIN settlement_details d ON d.settlement_id = s.id
              LEFT JOIN products pr ON pr.id = d.product_id
              LEFT JOIN financial_accounts fa ON fa.id = d.financial_account_id
              LEFT JOIN settlement_rules r ON r.id = d.rule_id
              LEFT JOIN business_units bu ON bu.id = fa.business_unit_id
             WHERE s.payment_id = :pid
             ORDER BY s.created_at DESC, d.created_at, d.id
        """),
        {"pid": str(payment_id)},
    )).fetchall()
    if not rows:
        raise HTTPException(
            status_code=404,
            detail="No settlement has been recorded for that payment.")

    grouped: dict = {}
    for r in rows:
        key = str(r.settlement_id)
        grouped.setdefault(key, {
            "settlement_id": key,
            "reference": r.settlement_reference,
            "status": r.status,
            "gross_amount": float(money(r.gross_amount)),
            "allocated_amount": float(money(r.allocated_amount)),
            "obligation_amount": float(money(r.obligation_amount)),
            "failure_reason": r.failure_reason,
            "journal_entry_id": str(r.journal_entry_id) if r.journal_entry_id else None,
            "distributed_at": r.distributed_at.isoformat() if r.distributed_at else None,
            "allocations": [],
        })
        if r.detail_id is not None:
            grouped[key]["allocations"].append({
                "product": r.product_name, "sku": r.sku,
                "business_unit": r.business_unit,
                "account_code": r.account_code, "account_name": r.account_name,
                "gl_account_code": r.gl_account_code,
                "allocation_type": r.allocation_type, "basis": r.basis,
                "rule": r.rule_code,
                "amount": float(money(r.amount)),
                "description": r.description,
            })
    return list(grouped.values())


# ===========================================================================
# /api/finance  -- configuration
# ===========================================================================

@finance_router.get('/accounts')
async def list_accounts(
    status: Optional[str] = Query(None),
    business_unit_id: Optional[UUID] = Query(None),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    await _require_schema(session)
    clauses, params = [], {}
    if status:
        clauses.append("fa.status = :st"); params["st"] = status.upper()
    if business_unit_id:
        clauses.append("fa.business_unit_id = :bu")
        params["bu"] = str(business_unit_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    rows = (await session.execute(
        text(f"""
            SELECT fa.id, fa.code, fa.name, fa.account_kind,
                   fa.gl_account_code, fa.contra_gl_account_code,
                   fa.bank_name, fa.account_number_enc, fa.account_name,
                   fa.currency, fa.status, fa.description,
                   bu.name AS business_unit, bu.id AS business_unit_id,
                   ga.name AS gl_account_name,
                   COALESCE((SELECT SUM(d.amount) FROM settlement_details d
                              JOIN settlements s ON s.id = d.settlement_id
                             WHERE d.financial_account_id = fa.id
                               AND s.status = 'COMPLETED'), 0) AS settled_total
              FROM financial_accounts fa
              LEFT JOIN business_units bu ON bu.id = fa.business_unit_id
              LEFT JOIN gl_accounts ga ON ga.code = fa.gl_account_code
              {where}
             ORDER BY fa.code
        """),
        params,
    )).fetchall()
    return [
        {"id": str(r.id), "code": r.code, "name": r.name,
         "account_kind": r.account_kind, "gl_account_code": r.gl_account_code,
         "gl_account_name": r.gl_account_name,
         "contra_gl_account_code": r.contra_gl_account_code,
         "bank_name": r.bank_name, "account_number_masked": _mask(r.account_number_enc),
         "account_name": r.account_name, "currency": r.currency,
         "status": r.status, "description": r.description,
         "business_unit": r.business_unit,
         "business_unit_id": str(r.business_unit_id) if r.business_unit_id else None,
         "settled_total": float(money(r.settled_total))}
        for r in rows
    ]


@finance_router.post('/accounts')
async def create_account(
    body: FinancialAccountIn,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Register a destination account.

    The GL account is required and must already exist and be postable: an
    account money can be sent to but that the ledger cannot record is how a
    distribution engine ends up disagreeing with the books.
    """
    await _require_schema(session)
    for code in filter(None, (body.gl_account_code, body.contra_gl_account_code)):
        gl = (await session.execute(
            text("SELECT is_postable, is_active FROM gl_accounts WHERE code = :c"),
            {"c": code},
        )).first()
        if gl is None:
            raise HTTPException(
                status_code=400,
                detail=f"GL account {code!r} is not in the chart of accounts.")
        if not gl.is_postable or not gl.is_active:
            raise HTTPException(
                status_code=400,
                detail=f"GL account {code!r} cannot receive postings.")
    try:
        row = (await session.execute(
            text("""
                INSERT INTO financial_accounts
                    (id, code, name, account_kind, gl_account_code,
                     contra_gl_account_code, bank_name, account_number_enc,
                     account_name, currency, business_unit_id, status,
                     description, created_by, created_at, updated_at)
                VALUES (gen_random_uuid(), :code, :name, :kind, :gl, :contra,
                        :bank, :acctno, :acctname, :cur, :bu, :st, :desc,
                        :by, NOW(), NOW())
             RETURNING id
            """),
            {"code": body.code, "name": body.name, "kind": body.account_kind,
             "gl": body.gl_account_code, "contra": body.contra_gl_account_code,
             "bank": body.bank_name,
             "acctno": encrypt_secret(body.account_number),
             "acctname": body.account_name, "cur": body.currency,
             "bu": str(body.business_unit_id) if body.business_unit_id else None,
             "st": body.status, "desc": body.description,
             "by": str(current_user.id)},
        )).first()
        await mapd_audit(
            session, event_type="ACCOUNT_CREATED",
            entity_type="financial_account", entity_id=row.id,
            actor_user_id=current_user.id, actor_label=current_user.email,
            detail={"code": body.code, "name": body.name,
                    "gl_account_code": body.gl_account_code,
                    "kind": body.account_kind})
        await session.commit()
        return {"success": True, "id": str(row.id),
                "message": f"Financial account {body.code} created."}
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:                                # noqa: BLE001
        await session.rollback()
        if "financial_accounts_code_key" in str(e):
            raise HTTPException(
                status_code=400,
                detail=f"An account with code {body.code!r} already exists.")
        raise HTTPException(status_code=400, detail=f"Could not create: {e}")


@finance_router.put('/accounts/{account_id}')
async def update_account(
    account_id: UUID,
    body: FinancialAccountUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Update an account. Code and GL mapping are deliberately immutable.

    Repointing an existing account at a different ledger account would silently
    restate every settlement already made against it. Create a new account and
    retire this one instead.
    """
    await _require_schema(session)
    current = (await session.execute(
        text("SELECT id, code, status FROM financial_accounts WHERE id = :i"),
        {"i": str(account_id)},
    )).first()
    if current is None:
        raise HTTPException(status_code=404, detail="Account not found.")

    fields, params = [], {"i": str(account_id)}
    for column, value in (
        ("name", body.name), ("bank_name", body.bank_name),
        ("account_name", body.account_name), ("status", body.status),
        ("description", body.description),
    ):
        if value is not None:
            fields.append(f"{column} = :{column}")
            params[column] = value
    if body.business_unit_id is not None:
        fields.append("business_unit_id = :bu")
        params["bu"] = str(body.business_unit_id)
    if body.account_number is not None:
        fields.append("account_number_enc = :acctno")
        params["acctno"] = encrypt_secret(body.account_number)

    if not fields:
        raise HTTPException(status_code=400, detail="Nothing to update.")

    fields.append("updated_at = NOW()")
    await session.execute(
        text(f"UPDATE financial_accounts SET {', '.join(fields)} WHERE id = :i"),
        params)
    await mapd_audit(
        session, event_type="ACCOUNT_UPDATED",
        entity_type="financial_account", entity_id=account_id,
        actor_user_id=current_user.id, actor_label=current_user.email,
        detail={"code": current.code,
                "changes": {k: v for k, v in params.items() if k != "i"}})
    await session.commit()
    return {"success": True, "message": f"Account {current.code} updated."}


@finance_router.get('/business-units')
async def list_business_units(
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    await _require_schema(session)
    rows = (await session.execute(
        text("""SELECT bu.id, bu.code, bu.name, bu.description, bu.is_active,
                       (SELECT COUNT(*) FROM product_accounts pa
                         WHERE pa.business_unit_id = bu.id) AS product_count
                  FROM business_units bu ORDER BY bu.code""")
    )).fetchall()
    return [{"id": str(r.id), "code": r.code, "name": r.name,
             "description": r.description, "is_active": r.is_active,
             "product_count": int(r.product_count)} for r in rows]


@finance_router.post('/business-units')
async def create_business_unit(
    body: BusinessUnitIn,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    await _require_schema(session)
    try:
        row = (await session.execute(
            text("""INSERT INTO business_units (id, code, name, description,
                                                is_active)
                    VALUES (gen_random_uuid(), :c, :n, :d, :a) RETURNING id"""),
            {"c": body.code, "n": body.name, "d": body.description,
             "a": body.is_active},
        )).first()
        await mapd_audit(
            session, event_type="BUSINESS_UNIT_CREATED",
            entity_type="business_unit", entity_id=row.id,
            actor_user_id=current_user.id, actor_label=current_user.email,
            detail={"code": body.code, "name": body.name})
        await session.commit()
        return {"success": True, "id": str(row.id)}
    except Exception as e:                                # noqa: BLE001
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Could not create: {e}")


@finance_router.get('/revenue-centers')
async def list_revenue_centers(
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    await _require_schema(session)
    rows = (await session.execute(
        text("""SELECT rc.id, rc.code, rc.name, rc.is_active,
                       bu.name AS business_unit
                  FROM revenue_centers rc
                  LEFT JOIN business_units bu ON bu.id = rc.business_unit_id
                 ORDER BY rc.code""")
    )).fetchall()
    return [{"id": str(r.id), "code": r.code, "name": r.name,
             "is_active": r.is_active, "business_unit": r.business_unit}
            for r in rows]


@finance_router.post('/revenue-centers')
async def create_revenue_center(
    body: RevenueCenterIn,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    await _require_schema(session)
    try:
        row = (await session.execute(
            text("""INSERT INTO revenue_centers (id, code, name,
                                                 business_unit_id, is_active)
                    VALUES (gen_random_uuid(), :c, :n, :bu, :a) RETURNING id"""),
            {"c": body.code, "n": body.name,
             "bu": str(body.business_unit_id) if body.business_unit_id else None,
             "a": body.is_active},
        )).first()
        await mapd_audit(
            session, event_type="REVENUE_CENTER_CREATED",
            entity_type="revenue_center", entity_id=row.id,
            actor_user_id=current_user.id, actor_label=current_user.email,
            detail={"code": body.code, "name": body.name})
        await session.commit()
        return {"success": True, "id": str(row.id)}
    except Exception as e:                                # noqa: BLE001
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Could not create: {e}")


@finance_router.get('/product-accounts')
async def list_product_accounts(
    unmapped_only: bool = Query(False),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    """Every product and its financial configuration.

    Products without configuration are included, not filtered out: an
    unconfigured product is the thing that stops a payment settling, so it has
    to be visible on the same screen as the configured ones.
    """
    await _require_schema(session)
    having = "WHERE pa.id IS NULL" if unmapped_only else ""
    rows = (await session.execute(
        text(f"""
            SELECT p.id AS product_id, p.sku, p.name AS product_name,
                   pa.id, pa.sales_account_code, pa.cost_account_code,
                   pa.inventory_account_code, pa.tax_group,
                   pa.settlement_priority, pa.notes,
                   bu.id AS business_unit_id, bu.name AS business_unit,
                   rc.id AS revenue_center_id, rc.name AS revenue_center,
                   fa.id AS default_account_id, fa.code AS default_account_code,
                   fa.name AS default_account_name,
                   (SELECT COUNT(*) FROM settlement_rules r
                     WHERE r.product_id = p.id AND r.is_active) AS rule_count
              FROM products p
              LEFT JOIN product_accounts pa ON pa.product_id = p.id
              LEFT JOIN business_units bu ON bu.id = pa.business_unit_id
              LEFT JOIN revenue_centers rc ON rc.id = pa.revenue_center_id
              LEFT JOIN financial_accounts fa
                     ON fa.id = pa.default_financial_account_id
              {having}
             ORDER BY p.name
        """)
    )).fetchall()
    return [
        {"product_id": str(r.product_id), "sku": r.sku,
         "product_name": r.product_name,
         "configured": r.id is not None,
         "sales_account_code": r.sales_account_code,
         "cost_account_code": r.cost_account_code,
         "inventory_account_code": r.inventory_account_code,
         "tax_group": r.tax_group,
         "settlement_priority": r.settlement_priority,
         "business_unit": r.business_unit,
         "business_unit_id": str(r.business_unit_id) if r.business_unit_id else None,
         "revenue_center": r.revenue_center,
         "revenue_center_id": str(r.revenue_center_id) if r.revenue_center_id else None,
         "default_account_id": str(r.default_account_id) if r.default_account_id else None,
         "default_account_code": r.default_account_code,
         "default_account_name": r.default_account_name,
         "rule_count": int(r.rule_count or 0),
         "notes": r.notes,
         "settleable": bool(r.default_account_id) or bool(r.rule_count)}
        for r in rows
    ]


@finance_router.put('/product-accounts')
async def upsert_product_account(
    body: ProductAccountIn,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Map one product to its financial accounts. Idempotent upsert."""
    await _require_schema(session)
    try:
        await session.execute(
            text("""
                INSERT INTO product_accounts
                    (id, product_id, sales_account_code, cost_account_code,
                     inventory_account_code, tax_group, business_unit_id,
                     revenue_center_id, settlement_priority,
                     default_financial_account_id, notes, updated_by,
                     updated_at, created_at)
                VALUES (gen_random_uuid(), :pid, :sales, :cost, :inv, :tax,
                        :bu, :rc, :prio, :acct, :notes, :by, NOW(), NOW())
                ON CONFLICT (product_id) DO UPDATE SET
                    sales_account_code = EXCLUDED.sales_account_code,
                    cost_account_code = EXCLUDED.cost_account_code,
                    inventory_account_code = EXCLUDED.inventory_account_code,
                    tax_group = EXCLUDED.tax_group,
                    business_unit_id = EXCLUDED.business_unit_id,
                    revenue_center_id = EXCLUDED.revenue_center_id,
                    settlement_priority = EXCLUDED.settlement_priority,
                    default_financial_account_id =
                        EXCLUDED.default_financial_account_id,
                    notes = EXCLUDED.notes,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = NOW()
            """),
            {"pid": str(body.product_id), "sales": body.sales_account_code,
             "cost": body.cost_account_code,
             "inv": body.inventory_account_code, "tax": body.tax_group,
             "bu": str(body.business_unit_id) if body.business_unit_id else None,
             "rc": str(body.revenue_center_id) if body.revenue_center_id else None,
             "prio": body.settlement_priority,
             "acct": str(body.default_financial_account_id)
             if body.default_financial_account_id else None,
             "notes": body.notes, "by": str(current_user.id)},
        )
        await mapd_audit(
            session, event_type="PRODUCT_ACCOUNT_MAPPED",
            entity_type="product", entity_id=body.product_id,
            actor_user_id=current_user.id, actor_label=current_user.email,
            detail={"default_account": str(body.default_financial_account_id)
                    if body.default_financial_account_id else None,
                    "business_unit": str(body.business_unit_id)
                    if body.business_unit_id else None,
                    "tax_group": body.tax_group})
        await session.commit()
        return {"success": True, "message": "Product financial mapping saved."}
    except Exception as e:                                # noqa: BLE001
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Could not save: {e}")


@finance_router.get('/settlement-rules')
async def list_rules(
    product_id: Optional[UUID] = Query(None),
    active_only: bool = Query(False),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    await _require_schema(session)
    clauses, params = [], {}
    if product_id:
        clauses.append("r.product_id = :pid"); params["pid"] = str(product_id)
    if active_only:
        clauses.append("r.is_active")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    rules = (await session.execute(
        text(f"""
            SELECT r.id, r.code, r.name, r.scope, r.basis, r.priority,
                   r.effective_from, r.effective_to, r.is_active,
                   r.description, p.name AS product_name, p.sku,
                   bu.name AS business_unit
              FROM settlement_rules r
              LEFT JOIN products p ON p.id = r.product_id
              LEFT JOIN business_units bu ON bu.id = r.business_unit_id
              {where}
             ORDER BY CASE r.scope WHEN 'PRODUCT' THEN 0
                                   WHEN 'BUSINESS_UNIT' THEN 1 ELSE 2 END,
                      r.priority, r.code
        """),
        params,
    )).fetchall()

    splits = (await session.execute(
        text("""SELECT s.rule_id, s.id, s.allocation_type, s.percentage,
                       s.fixed_amount, s.rate_per_unit, s.is_residual,
                       s.description, fa.code AS account_code,
                       fa.name AS account_name, fa.status AS account_status
                  FROM settlement_rule_splits s
                  JOIN financial_accounts fa ON fa.id = s.financial_account_id
                 ORDER BY s.sort_order, s.id""")
    )).fetchall()
    by_rule: dict = {}
    for s in splits:
        by_rule.setdefault(str(s.rule_id), []).append({
            "id": str(s.id), "allocation_type": s.allocation_type,
            "percentage": float(s.percentage) if s.percentage is not None else None,
            "fixed_amount": float(s.fixed_amount) if s.fixed_amount is not None else None,
            "rate_per_unit": float(s.rate_per_unit) if s.rate_per_unit is not None else None,
            "is_residual": s.is_residual, "description": s.description,
            "account_code": s.account_code, "account_name": s.account_name,
            "account_status": s.account_status,
        })

    return [
        {"id": str(r.id), "code": r.code, "name": r.name, "scope": r.scope,
         "basis": r.basis, "priority": r.priority,
         "effective_from": str(r.effective_from),
         "effective_to": str(r.effective_to) if r.effective_to else None,
         "is_active": r.is_active, "description": r.description,
         "product_name": r.product_name, "sku": r.sku,
         "business_unit": r.business_unit,
         "splits": by_rule.get(str(r.id), [])}
        for r in rules
    ]


@finance_router.post('/settlement-rules')
async def create_rule(
    body: RuleIn,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Author a settlement rule.

    Validated at creation, not at settlement time: a rule that allocates 90%
    of a line would otherwise sit in the configuration screen looking correct
    and fail on a live customer payment.
    """
    await _require_schema(session)
    try:
        for s in body.splits:
            acct = (await session.execute(
                text("""SELECT status, account_kind FROM financial_accounts
                         WHERE id = :i"""),
                {"i": str(s.financial_account_id)},
            )).first()
            if acct is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"Financial account {s.financial_account_id} "
                           f"does not exist.")
            if s.allocation_type == 'OBLIGATION' and acct.account_kind != 'OBLIGATION':
                raise HTTPException(
                    status_code=400,
                    detail="An OBLIGATION split must point at an OBLIGATION "
                           "account, which carries the liability side of the "
                           "entry.")
            if s.allocation_type == 'CASH' and acct.account_kind == 'OBLIGATION':
                raise HTTPException(
                    status_code=400,
                    detail="A CASH split cannot pay into an OBLIGATION "
                           "account; cash lands in a bank, wallet or cash "
                           "account.")

        rule = (await session.execute(
            text("""
                INSERT INTO settlement_rules
                    (id, code, name, scope, product_id, business_unit_id,
                     basis, priority, effective_from, effective_to, is_active,
                     description, created_by, created_at, updated_at)
                VALUES (gen_random_uuid(), :code, :name, :scope, :pid, :bu,
                        :basis, :prio,
                        COALESCE(CAST(:from_ AS date), CURRENT_DATE),
                        CAST(:to_ AS date),
                        :active, :desc, :by, NOW(), NOW())
             RETURNING id
            """),
            {"code": body.code, "name": body.name, "scope": body.scope,
             "pid": str(body.product_id) if body.product_id else None,
             "bu": str(body.business_unit_id) if body.business_unit_id else None,
             "basis": body.basis, "prio": body.priority,
             "from_": body.effective_from, "to_": body.effective_to,
             "active": body.is_active, "desc": body.description,
             "by": str(current_user.id)},
        )).first()

        for i, s in enumerate(body.splits):
            await session.execute(
                text("""
                    INSERT INTO settlement_rule_splits
                        (id, rule_id, financial_account_id, allocation_type,
                         percentage, fixed_amount, rate_per_unit, is_residual,
                         sort_order, description, created_at)
                    VALUES (gen_random_uuid(), :rid, :acct, :type, :pct,
                            :fixed, :rate, :resid, :sort, :desc, NOW())
                """),
                {"rid": str(rule.id), "acct": str(s.financial_account_id),
                 "type": s.allocation_type, "pct": s.percentage,
                 "fixed": s.fixed_amount, "rate": s.rate_per_unit,
                 "resid": s.is_residual,
                 "sort": s.sort_order if s.sort_order else i,
                 "desc": s.description},
            )

        await mapd_audit(
            session, event_type="RULE_CREATED",
            entity_type="settlement_rule", entity_id=rule.id,
            actor_user_id=current_user.id, actor_label=current_user.email,
            detail={"code": body.code, "scope": body.scope,
                    "splits": [
                        {"account": str(s.financial_account_id),
                         "type": s.allocation_type,
                         "percentage": s.percentage,
                         "fixed_amount": s.fixed_amount,
                         "rate_per_unit": s.rate_per_unit,
                         "residual": s.is_residual}
                        for s in body.splits]})
        await session.commit()
        return {"success": True, "id": str(rule.id),
                "message": f"Settlement rule {body.code} created."}
    except HTTPException:
        await session.rollback()
        raise
    except Exception as e:                                # noqa: BLE001
        await session.rollback()
        if "settlement_rules_code_key" in str(e):
            raise HTTPException(
                status_code=400,
                detail=f"A rule with code {body.code!r} already exists.")
        raise HTTPException(status_code=400, detail=f"Could not create: {e}")


@finance_router.patch('/settlement-rules/{rule_id}/active')
async def toggle_rule(
    rule_id: UUID,
    is_active: bool = Query(...),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Activate or retire a rule.

    Rules are switched off, never deleted: settlement_details reference the
    rule that produced them, and an auditor asking "why did this money go
    there" needs the rule to still exist.
    """
    await _require_schema(session)
    row = (await session.execute(
        text("""UPDATE settlement_rules SET is_active = :a, updated_at = NOW()
                 WHERE id = :i RETURNING code"""),
        {"a": is_active, "i": str(rule_id)},
    )).first()
    if row is None:
        await session.rollback()
        raise HTTPException(status_code=404, detail="Rule not found.")
    await mapd_audit(
        session, event_type="RULE_ACTIVATED" if is_active else "RULE_RETIRED",
        entity_type="settlement_rule", entity_id=rule_id,
        actor_user_id=current_user.id, actor_label=current_user.email,
        detail={"code": row.code, "is_active": is_active})
    await session.commit()
    return {"success": True,
            "message": f"Rule {row.code} "
                       f"{'activated' if is_active else 'retired'}."}


@finance_router.get('/dashboard')
async def finance_dashboard(
    start: Optional[date] = Query(None),
    end: Optional[date] = Query(None),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    """The distribution dashboard: collections, destinations, and what failed."""
    await _require_schema(session)
    params = {"start": start, "end": end}
    window = ("AND s.created_at >= CAST(:start AS date) " if start else "") + \
             ("AND s.created_at < CAST(:end AS date) + INTERVAL '1 day' " if end else "")

    totals = (await session.execute(
        text(f"""
            SELECT COUNT(*) FILTER (WHERE s.status = 'COMPLETED') AS completed,
                   COUNT(*) FILTER (WHERE s.status = 'FAILED') AS failed,
                   COUNT(*) FILTER (WHERE s.status = 'SKIPPED') AS skipped,
                   COALESCE(SUM(s.allocated_amount)
                            FILTER (WHERE s.status = 'COMPLETED'), 0) AS allocated,
                   COALESCE(SUM(s.obligation_amount)
                            FILTER (WHERE s.status = 'COMPLETED'), 0) AS obligations,
                   COALESCE(SUM(s.gross_amount)
                            FILTER (WHERE s.status = 'FAILED'), 0) AS failed_value
              FROM settlements s WHERE TRUE {window}
        """), params)).first()

    by_account = (await session.execute(
        text(f"""
            SELECT fa.code, fa.name, fa.status,
                   bu.name AS business_unit,
                   COALESCE(SUM(d.amount), 0) AS total,
                   COUNT(DISTINCT s.id) AS settlements
              FROM financial_accounts fa
              LEFT JOIN settlement_details d ON d.financial_account_id = fa.id
              LEFT JOIN settlements s
                     ON s.id = d.settlement_id AND s.status = 'COMPLETED' {window}
              LEFT JOIN business_units bu ON bu.id = fa.business_unit_id
             GROUP BY fa.code, fa.name, fa.status, bu.name
             ORDER BY total DESC
        """), params)).fetchall()

    by_product = (await session.execute(
        text(f"""
            SELECT pr.name AS product, pr.sku,
                   COALESCE(SUM(d.amount), 0) AS total
              FROM settlement_details d
              JOIN settlements s ON s.id = d.settlement_id
              LEFT JOIN products pr ON pr.id = d.product_id
             WHERE s.status = 'COMPLETED' AND d.allocation_type = 'CASH' {window}
             GROUP BY pr.name, pr.sku
             ORDER BY total DESC LIMIT 20
        """), params)).fetchall()

    by_unit = (await session.execute(
        text(f"""
            SELECT COALESCE(bu.name, 'Unassigned') AS business_unit,
                   COALESCE(SUM(d.amount), 0) AS total
              FROM settlement_details d
              JOIN settlements s ON s.id = d.settlement_id
              LEFT JOIN financial_accounts fa ON fa.id = d.financial_account_id
              LEFT JOIN business_units bu ON bu.id = fa.business_unit_id
             WHERE s.status = 'COMPLETED' AND d.allocation_type = 'CASH' {window}
             GROUP BY bu.name ORDER BY total DESC
        """), params)).fetchall()

    daily = (await session.execute(
        text(f"""
            SELECT CAST(s.created_at AS date) AS day,
                   COALESCE(SUM(s.allocated_amount), 0) AS total
              FROM settlements s
             WHERE s.status = 'COMPLETED' {window}
             GROUP BY CAST(s.created_at AS date)
             ORDER BY day DESC LIMIT 30
        """), params)).fetchall()

    outstanding = money((await session.execute(
        text("""SELECT COALESCE(SUM(GREATEST(i.total_amount - COALESCE((
                        SELECT SUM(p.amount) FROM payments p
                         WHERE p.invoice_id = i.id), 0), 0)), 0)
                  FROM invoices i WHERE i.status <> 'cancelled'""")
    )).scalar())

    unconfigured = int((await session.execute(
        text("""SELECT COUNT(*) FROM products p
                 LEFT JOIN product_accounts pa ON pa.product_id = p.id
                WHERE pa.default_financial_account_id IS NULL
                  AND NOT EXISTS (SELECT 1 FROM settlement_rules r
                                   WHERE r.product_id = p.id AND r.is_active)""")
    )).scalar() or 0)

    return {
        "period": {"start": str(start) if start else None,
                   "end": str(end) if end else None},
        "totals": {
            "completed": int(totals.completed or 0),
            "failed": int(totals.failed or 0),
            "skipped": int(totals.skipped or 0),
            "allocated": float(money(totals.allocated)),
            "obligations": float(money(totals.obligations)),
            "failed_value": float(money(totals.failed_value)),
            "outstanding_receivables": float(outstanding),
            "unconfigured_products": unconfigured,
        },
        "by_account": [
            {"code": r.code, "name": r.name, "status": r.status,
             "business_unit": r.business_unit,
             "total": float(money(r.total)),
             "settlements": int(r.settlements or 0)} for r in by_account],
        "by_product": [
            {"product": r.product, "sku": r.sku, "total": float(money(r.total))}
            for r in by_product],
        "by_business_unit": [
            {"business_unit": r.business_unit, "total": float(money(r.total))}
            for r in by_unit],
        "daily": [
            {"date": str(r.day), "total": float(money(r.total))}
            for r in reversed(daily)],
    }


@finance_router.get('/audit-log')
async def audit_log(
    event_type: Optional[str] = Query(None),
    payment_id: Optional[UUID] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
    _admin: User = Depends(require_admin),
):
    """The immutable MAPD audit trail. Admin-only; it contains money movement."""
    await _require_schema(session)
    clauses, params = [], {"lim": limit}
    if event_type:
        clauses.append("a.event_type = :ev"); params["ev"] = event_type
    if payment_id:
        clauses.append("a.payment_id = :pid"); params["pid"] = str(payment_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    rows = (await session.execute(
        text(f"""SELECT a.id, a.event_type, a.entity_type, a.entity_id,
                        a.payment_id, a.settlement_id, a.actor_label,
                        a.detail, a.created_at,
                        u.full_name AS actor_name
                   FROM mapd_audit_logs a
                   LEFT JOIN users u ON u.id = a.actor_user_id
                   {where}
                  ORDER BY a.created_at DESC LIMIT :lim"""),
        params,
    )).fetchall()
    return [
        {"id": str(r.id), "event_type": r.event_type,
         "entity_type": r.entity_type,
         "entity_id": str(r.entity_id) if r.entity_id else None,
         "payment_id": str(r.payment_id) if r.payment_id else None,
         "settlement_id": str(r.settlement_id) if r.settlement_id else None,
         "actor": r.actor_name or r.actor_label, "detail": r.detail,
         "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in rows
    ]


@finance_router.get('/preview/{payment_id}')
async def preview_distribution(
    payment_id: UUID,
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    """What WOULD this payment's split be? Writes nothing."""
    await _require_schema(session)
    plan = await build_plan(session, payment_id=payment_id)
    await session.rollback()
    return {
        "status": plan["status"], "reason": plan.get("reason"),
        "invoice_number": plan.get("invoice_number"),
        "gross_amount": float(plan["gross_amount"]),
        "allocated_amount": float(plan.get("allocated_amount", 0)),
        "obligation_amount": float(plan.get("obligation_amount", 0)),
        "allocations": [
            {"product": a.get("product_name"), "sku": a.get("sku"),
             "business_unit": a.get("business_unit"),
             "account_code": a["account_code"], "account_name": a["account_name"],
             "account_status": a["account_status"], "rule": a.get("rule_code"),
             "amount": float(a["amount"])}
            for a in plan.get("allocations", [])],
        "obligations": [
            {"product": o.get("product_name"), "account_code": o["account_code"],
             "account_name": o["account_name"], "rule": o.get("rule_code"),
             "amount": float(o["amount"])}
            for o in plan.get("obligations", [])],
        "unconfigured_products": plan.get("unconfigured_products", []),
    }


# ===========================================================================
# /api/reports
# ===========================================================================

@reports_router.get('/revenue')
async def revenue_report(
    start: Optional[date] = Query(None),
    end: Optional[date] = Query(None),
    group_by: str = Query('product'),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    """Distributed revenue, grouped the way the reader needs it.

    Reads the settlement details -- money that actually reached an account --
    rather than order totals or a payment-status flag. The three used to
    disagree; this reports the one that is a record of an event.
    """
    await _require_schema(session)

    # Validated here rather than with Query(pattern=...): that argument needs
    # FastAPI >= 0.100, and nothing else in this codebase requires it (staff.py
    # still uses the older Query(regex=...)). A module that refuses to import
    # takes the WHOLE application down with it -- main.py raises rather than
    # registering a partial route table -- so this module must not be the one
    # thing that needs a newer FastAPI than the rest.
    #
    # It is also a whitelist guarding SQL that is interpolated below, so it has
    # to be enforced regardless of framework version.
    if group_by not in ('product', 'account', 'business_unit', 'day'):
        raise HTTPException(
            status_code=422,
            detail="group_by must be one of: product, account, "
                   "business_unit, day")

    dimension = {
        'product': ("COALESCE(pr.name, 'Unknown product')", "pr.sku"),
        'account': ("fa.name", "fa.code"),
        'business_unit': ("COALESCE(bu.name, 'Unassigned')", "bu.code"),
        'day': ("CAST(s.distributed_at AS text)", "NULL"),
    }[group_by]

    label_expr = ("CAST(CAST(s.distributed_at AS date) AS text)"
                  if group_by == 'day' else dimension[0])
    key_expr = "NULL" if group_by == 'day' else dimension[1]

    rows = (await session.execute(
        text(f"""
            SELECT {label_expr} AS label, {key_expr} AS key,
                   COALESCE(SUM(d.amount) FILTER (
                       WHERE d.allocation_type = 'CASH'), 0) AS cash,
                   COALESCE(SUM(d.amount) FILTER (
                       WHERE d.allocation_type = 'OBLIGATION'), 0) AS obligations,
                   COUNT(DISTINCT s.id) AS settlements
              FROM settlement_details d
              JOIN settlements s ON s.id = d.settlement_id
              LEFT JOIN products pr ON pr.id = d.product_id
              LEFT JOIN financial_accounts fa ON fa.id = d.financial_account_id
              LEFT JOIN business_units bu ON bu.id = fa.business_unit_id
             WHERE s.status = 'COMPLETED'
               AND (CAST(:start AS date) IS NULL
                    OR CAST(s.distributed_at AS date) >= CAST(:start AS date))
               AND (CAST(:end AS date) IS NULL
                    OR CAST(s.distributed_at AS date) <= CAST(:end AS date))
             GROUP BY 1, 2
             ORDER BY 3 DESC
        """),
        {"start": start, "end": end},
    )).fetchall()

    items = [
        {"label": r.label, "key": r.key,
         "cash": float(money(r.cash)),
         "obligations": float(money(r.obligations)),
         "settlements": int(r.settlements)}
        for r in rows
    ]
    return {
        "group_by": group_by,
        "period": {"start": str(start) if start else None,
                   "end": str(end) if end else None},
        "items": items,
        "total_cash": float(money(sum(i["cash"] for i in items))),
        "total_obligations": float(money(sum(i["obligations"] for i in items))),
    }


@reports_router.get('/settlements')
async def settlements_report(
    start: Optional[date] = Query(None),
    end: Optional[date] = Query(None),
    status: Optional[str] = Query(None),
    account_code: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    """The settlement register: every distribution, with its destinations."""
    await _require_schema(session)
    clauses = ["TRUE"]
    params = {"lim": limit, "off": offset, "start": start, "end": end}
    if status:
        clauses.append("s.status = :st"); params["st"] = status.upper()
    if account_code:
        clauses.append("""EXISTS (SELECT 1 FROM settlement_details d2
                                    JOIN financial_accounts fa2
                                      ON fa2.id = d2.financial_account_id
                                   WHERE d2.settlement_id = s.id
                                     AND fa2.code = :acct)""")
        params["acct"] = account_code
    clauses.append("(CAST(:start AS date) IS NULL "
                   "OR CAST(s.created_at AS date) >= CAST(:start AS date))")
    clauses.append("(CAST(:end AS date) IS NULL "
                   "OR CAST(s.created_at AS date) <= CAST(:end AS date))")

    rows = (await session.execute(
        text(f"""
            SELECT s.id, s.settlement_reference, s.status, s.gross_amount,
                   s.allocated_amount, s.obligation_amount, s.failure_reason,
                   s.payment_method, s.distributed_at, s.created_at,
                   i.invoice_number, c.name AS customer_name,
                   so.order_number,
                   (SELECT STRING_AGG(fa.code || ':' || d.amount, ' | '
                                      ORDER BY fa.code)
                      FROM settlement_details d
                      JOIN financial_accounts fa
                        ON fa.id = d.financial_account_id
                     WHERE d.settlement_id = s.id) AS destinations
              FROM settlements s
              JOIN invoices i ON i.id = s.invoice_id
              LEFT JOIN customers c ON c.id = i.customer_id
              LEFT JOIN sales_orders so ON so.id = s.sales_order_id
             WHERE {' AND '.join(clauses)}
             ORDER BY s.created_at DESC
             LIMIT :lim OFFSET :off
        """),
        params,
    )).fetchall()

    return [
        {"id": str(r.id), "reference": r.settlement_reference,
         "status": r.status,
         "gross_amount": float(money(r.gross_amount)),
         "allocated_amount": float(money(r.allocated_amount)),
         "obligation_amount": float(money(r.obligation_amount)),
         "failure_reason": r.failure_reason,
         "payment_method": r.payment_method,
         "invoice_number": r.invoice_number,
         "order_number": r.order_number,
         "customer_name": r.customer_name,
         "destinations": r.destinations,
         "distributed_at": r.distributed_at.isoformat() if r.distributed_at else None,
         "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in rows
    ]


@reports_router.get('/refunds')
async def refunds_report(
    limit: int = Query(200, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
    _user: User = Depends(require_authenticated_user),
):
    await _require_schema(session)
    rows = (await session.execute(
        text("""SELECT r.refund_reference, r.amount, r.is_full_reversal,
                       r.reason, r.status, r.created_at,
                       s.settlement_reference, i.invoice_number,
                       u.full_name AS created_by_name,
                       a.full_name AS approved_by_name
                  FROM mapd_refunds r
                  JOIN settlements s ON s.id = r.settlement_id
                  LEFT JOIN invoices i ON i.id = r.invoice_id
                  LEFT JOIN users u ON u.id = r.created_by
                  LEFT JOIN users a ON a.id = r.approved_by
                 ORDER BY r.created_at DESC LIMIT :lim"""),
        {"lim": limit},
    )).fetchall()
    return [
        {"refund_reference": r.refund_reference,
         "settlement_reference": r.settlement_reference,
         "invoice_number": r.invoice_number,
         "amount": float(money(r.amount)),
         "is_full_reversal": r.is_full_reversal,
         "reason": r.reason, "status": r.status,
         "created_by": r.created_by_name, "approved_by": r.approved_by_name,
         "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in rows
    ]
