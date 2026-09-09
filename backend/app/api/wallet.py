"""Staff operational wallet: HTTP surface.

This layer is deliberately thin. It parses input, decides who is allowed to
do the thing, calls one service function, and commits. Every rule about money
lives in app/services/wallet.py, so there is exactly one place to read to know
what the system will do -- and no endpoint can accidentally implement a
slightly different version of the same rule.

Two conventions worth stating, because breaking either is how this kind of
module goes wrong:

  * **The route commits; the service does not.** One transaction per request
    means the expense document, its ledger movement, its journal entry and its
    audit row either all land or none do. A service that committed halfway
    would make partial states reachable.

  * **Authorization is re-checked here AND in the service.** The service
    functions are the real guard (they are what a test exercises); the checks
    here exist so an unauthorised request is refused before any work is done.

Every route is registered behind `require_authenticated_user` in main.py, so
there is no anonymous access to anything in this file.
"""
from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from fastapi import (
    APIRouter, Depends, File, HTTPException, Query, Request, Response,
    UploadFile,
)
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_admin, require_authenticated_user
from app.db import get_session
from app.models import User
from app.services import wallet as svc

router = APIRouter(prefix="/api/wallet", tags=["Staff Wallet"])


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class WalletCreate(BaseModel):
    user_id: UUID
    wallet_type: str = Field("INDIVIDUAL")
    department: Optional[str] = None
    purpose: Optional[str] = None
    staff_id: Optional[UUID] = None
    single_txn_limit: Optional[Decimal] = Field(None, gt=0)
    daily_limit: Optional[Decimal] = Field(None, gt=0)
    monthly_limit: Optional[Decimal] = Field(None, gt=0)
    self_approve_limit: Optional[Decimal] = Field(None, ge=0)


class WalletUpdate(BaseModel):
    department: Optional[str] = None
    purpose: Optional[str] = None
    single_txn_limit: Optional[Decimal] = Field(None, gt=0)
    daily_limit: Optional[Decimal] = Field(None, gt=0)
    monthly_limit: Optional[Decimal] = Field(None, gt=0)
    self_approve_limit: Optional[Decimal] = Field(None, ge=0)
    status: Optional[str] = None
    clear_limits: list[str] = Field(default_factory=list)


class FundingIn(BaseModel):
    amount: Decimal = Field(..., gt=0)
    purpose: str = Field(..., min_length=3)
    # How the money ACTUALLY left the company, outside this application.
    disbursement_method: str = Field("BANK_TRANSFER")
    disbursement_reference: Optional[str] = None
    source_account_code: str = Field("1200")
    funded_on: Optional[date] = None
    account_by: Optional[date] = None
    notes: Optional[str] = None
    fund_request_id: Optional[UUID] = None
    # Lets a retried submission from a phone on a poor connection be
    # recognised as the same funding rather than issuing the advance twice.
    idempotency_key: Optional[str] = Field(None, max_length=80)


class ExpenseIn(BaseModel):
    category_id: UUID
    amount: Decimal = Field(..., gt=0)
    purpose: str = Field(..., min_length=2, max_length=255)
    spent_on: Optional[date] = None
    description: Optional[str] = None
    vendor: Optional[str] = None
    location: Optional[str] = None
    payment_method: str = Field("CASH")
    project_reference: Optional[str] = None
    customer_id: Optional[UUID] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    gps_accuracy: Optional[float] = None
    notes: Optional[str] = None
    idempotency_key: Optional[str] = Field(None, max_length=80)


class DecisionIn(BaseModel):
    approve: bool
    note: Optional[str] = None


class ReverseIn(BaseModel):
    reason: str = Field(..., min_length=3)


class FundRequestIn(BaseModel):
    amount: Decimal = Field(..., gt=0)
    reason: str = Field(..., min_length=3)
    urgency: str = Field("NORMAL")


class FundRequestDecisionIn(BaseModel):
    decision: str
    amount_approved: Optional[Decimal] = Field(None, gt=0)
    note: Optional[str] = None

    @model_validator(mode="after")
    def _partial_needs_amount(self):
        if self.decision == "PARTIALLY_APPROVED" and self.amount_approved is None:
            raise ValueError(
                "State how much is approved when partially approving.")
        return self


class ReturnIn(BaseModel):
    amount: Decimal = Field(..., gt=0)
    returned_on: Optional[date] = None
    method: str = Field("CASH")
    reference: Optional[str] = None
    destination_account_code: str = Field("1100")
    note: Optional[str] = None


class ReturnConfirmIn(BaseModel):
    confirm: bool
    note: Optional[str] = None


class ReconciliationIn(BaseModel):
    period_start: date
    period_end: date
    declared_cash_on_hand: Optional[Decimal] = Field(None, ge=0)
    note: Optional[str] = None


class ReconciliationReviewIn(BaseModel):
    accept: bool
    note: Optional[str] = None


class ReimbursementIn(BaseModel):
    category_id: UUID
    amount: Decimal = Field(..., gt=0)
    spent_on: date
    purpose: str = Field(..., min_length=2, max_length=255)
    description: Optional[str] = None
    vendor: Optional[str] = None
    department: Optional[str] = None
    wallet_id: Optional[UUID] = None


class ReimbursementDecisionIn(BaseModel):
    approve: bool
    amount_approved: Optional[Decimal] = Field(None, gt=0)
    note: Optional[str] = None


class ReimbursementPayIn(BaseModel):
    paid_on: Optional[date] = None
    payment_method: str = Field("BANK_TRANSFER")
    payment_reference: Optional[str] = None
    paid_from_account_code: str = Field("1200")


class CategoryIn(BaseModel):
    code: str = Field(..., min_length=2, max_length=32)
    name: str = Field(..., min_length=2)
    description: Optional[str] = None
    gl_account_code: Optional[str] = None
    requires_receipt: bool = True
    default_monthly_cap: Optional[Decimal] = Field(None, gt=0)
    sort_order: int = 100


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    gl_account_code: Optional[str] = None
    requires_receipt: Optional[bool] = None
    default_monthly_cap: Optional[Decimal] = Field(None, gt=0)
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


class ApprovalRuleIn(BaseModel):
    scope: str = Field("GLOBAL")
    wallet_type: Optional[str] = None
    wallet_id: Optional[UUID] = None
    min_amount: Decimal = Field(Decimal("0"), ge=0)
    max_amount: Optional[Decimal] = Field(None, gt=0)
    tier: str
    priority: int = 100

    @model_validator(mode="after")
    def _sane_band(self):
        if self.max_amount is not None and self.max_amount <= self.min_amount:
            raise ValueError("max_amount must be greater than min_amount.")
        if self.scope == "WALLET_TYPE" and not self.wallet_type:
            raise ValueError("A WALLET_TYPE rule must name a wallet type.")
        if self.scope == "WALLET" and not self.wallet_id:
            raise ValueError("A WALLET rule must name a wallet.")
        return self


class ApproverIn(BaseModel):
    user_id: UUID
    tier: str
    max_amount: Optional[Decimal] = Field(None, gt=0)
    department: Optional[str] = None
    wallet_id: Optional[UUID] = None
    can_fund: bool = False
    can_reconcile: bool = False


class CategoryLimitIn(BaseModel):
    category_id: UUID
    monthly_cap: Decimal = Field(..., gt=0)


class FlagReviewIn(BaseModel):
    status: str = Field("REVIEWED")
    note: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _client(request: Request) -> dict:
    """Where the request came from, for the audit trail.

    X-Forwarded-For is read because the app runs behind nginx / the
    DigitalOcean load balancer, where request.client is the proxy. It is
    caller-supplied and therefore untrustworthy, so it is recorded as
    evidence, never used for a decision.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() or (
        request.client.host if request.client else "")
    return {"ip_address": ip, "user_agent": request.headers.get("user-agent", "")}


async def _wallet_or_404(session: AsyncSession, wallet_id: UUID):
    return await svc.get_wallet(session, wallet_id)


async def _next_wallet_number(session: AsyncSession, wallet_type: str) -> str:
    prefix = f"WAL-{wallet_type[:3].upper()}"
    row = (await session.execute(
        text("""SELECT COUNT(*) AS c FROM wallets WHERE wallet_type = :t"""),
        {"t": wallet_type},
    )).first()
    # Collisions are prevented by the UNIQUE constraint, not by this count;
    # the suffix only has to be readable, and a retry produces the next number.
    return f"{prefix}-{row.c + 1:04d}"


def _rows(items) -> list[dict]:
    """JSON-safe rows: Decimals become strings so no precision is lost in JS."""
    out = []
    for r in items:
        d = dict(r)
        out.append({k: (str(v) if isinstance(v, Decimal) else v)
                    for k, v in d.items()})
    return out


# ---------------------------------------------------------------------------
# Staff-facing
# ---------------------------------------------------------------------------

@router.get("/me")
async def my_wallets(
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Every wallet this user holds, with the figures the dashboard shows."""
    rows = (await session.execute(
        text("""SELECT id FROM wallets WHERE user_id = :u
                 ORDER BY wallet_type"""),
        {"u": str(user.id)},
    )).all()
    wallets = [await svc.wallet_summary(session, r.id) for r in rows]
    caps = await svc.user_capabilities(session, user)
    return {"wallets": wallets, "capabilities": caps}


@router.get("/inbox")
async def my_inbox(
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    return await svc.inbox(session, user)


@router.get("/categories")
async def list_categories(
    include_inactive: bool = False,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    clause = "" if include_inactive else "WHERE is_active = TRUE"
    rows = (await session.execute(
        text(f"""SELECT id, code, name, description, gl_account_code,
                        requires_receipt, default_monthly_cap, is_active,
                        sort_order
                   FROM wallet_expense_categories {clause}
                  ORDER BY sort_order, name"""),
    )).mappings().all()
    return {"categories": _rows(rows)}


@router.get("/wallets")
async def list_wallets(
    department: Optional[str] = None,
    status: Optional[str] = None,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Wallets this user may see: their own, plus any they oversee."""
    caps = await svc.user_capabilities(session, user)
    where = ["1 = 1"]
    params: dict = {}
    if department:
        where.append("w.department = :dept")
        params["dept"] = department
    if status:
        where.append("w.status = :st")
        params["st"] = status

    if not caps["is_admin"]:
        grants = caps["grants"]
        if not grants:
            where.append("w.user_id = :self")
            params["self"] = str(user.id)
        else:
            # Own wallets, plus anything a grant covers. Built as SQL rather
            # than filtered in Python so a large company does not load every
            # wallet to show three.
            clauses = ["w.user_id = :self"]
            params["self"] = str(user.id)
            for i, g in enumerate(grants):
                bits = []
                if g["wallet_id"]:
                    bits.append(f"w.id = :gw{i}")
                    params[f"gw{i}"] = str(g["wallet_id"])
                if g["department"]:
                    bits.append(f"w.department = :gd{i}")
                    params[f"gd{i}"] = g["department"]
                clauses.append("(" + " AND ".join(bits) + ")" if bits else "TRUE")
            where.append("(" + " OR ".join(clauses) + ")")

    rows = (await session.execute(
        text(f"""
            SELECT w.id, w.wallet_number, w.wallet_type, w.department,
                   w.status, w.balance, w.total_funded, w.total_spent,
                   w.total_returned, w.last_transaction_at,
                   u.full_name AS holder, u.email AS holder_email
              FROM wallets w
              JOIN users u ON u.id = w.user_id
             WHERE {' AND '.join(where)}
             ORDER BY w.department NULLS LAST, u.full_name
        """), params,
    )).mappings().all()
    return {"wallets": _rows(rows)}


@router.post("/wallets", status_code=201)
async def create_wallet(
    body: WalletCreate,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Open a wallet for an employee. Administrators only.

    The holder must be an active user account, because a wallet holder has to
    be able to log in and record their own spending -- a wallet nobody can
    open is an advance nobody can account for.
    """
    holder = (await session.execute(
        text("SELECT id, full_name, is_active, department FROM users "
             "WHERE id = :u"),
        {"u": str(body.user_id)},
    )).mappings().first()
    if holder is None:
        raise HTTPException(status_code=404, detail="That user does not exist.")
    if not holder["is_active"]:
        raise HTTPException(
            status_code=400,
            detail=(f"{holder['full_name']}'s account is not active. Activate "
                    f"it before opening a wallet."))

    existing = (await session.execute(
        text("""SELECT wallet_number FROM wallets
                 WHERE user_id = :u AND wallet_type = :t"""),
        {"u": str(body.user_id), "t": body.wallet_type},
    )).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=(f"{holder['full_name']} already has a "
                    f"{body.wallet_type} wallet ({existing.wallet_number})."))

    wallet_id = uuid4()
    number = await _next_wallet_number(session, body.wallet_type)
    await session.execute(
        text("""
            INSERT INTO wallets
                (id, wallet_number, user_id, staff_id, wallet_type, department,
                 purpose, single_txn_limit, daily_limit, monthly_limit,
                 self_approve_limit, status, created_by)
            VALUES (:id, :num, :u, :s, :t, :dept, :p, :stl, :dl, :ml, :sal,
                    'ACTIVE', :by)
        """),
        {"id": str(wallet_id), "num": number, "u": str(body.user_id),
         "s": str(body.staff_id) if body.staff_id else None,
         "t": body.wallet_type,
         "dept": body.department or holder["department"],
         "p": body.purpose,
         "stl": str(body.single_txn_limit) if body.single_txn_limit else None,
         "dl": str(body.daily_limit) if body.daily_limit else None,
         "ml": str(body.monthly_limit) if body.monthly_limit else None,
         "sal": str(body.self_approve_limit)
                if body.self_approve_limit is not None else None,
         "by": str(user.id)},
    )
    await svc.audit(
        session, event_type="WALLET_CREATED", entity_type="wallet",
        entity_id=wallet_id, wallet_id=wallet_id, actor_user_id=user.id,
        actor_label=user.full_name, result="ACTIVE",
        detail={"wallet_number": number, "holder": holder["full_name"],
                "wallet_type": body.wallet_type}, **_client(request),
    )
    await session.commit()
    return await svc.wallet_summary(session, wallet_id)


@router.get("/wallets/{wallet_id}")
async def get_wallet_summary(
    wallet_id: UUID,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    wallet = await _wallet_or_404(session, wallet_id)
    await svc.assert_can_view_wallet(session, user, wallet)
    return await svc.wallet_summary(session, wallet_id)


@router.patch("/wallets/{wallet_id}")
async def update_wallet(
    wallet_id: UUID,
    body: WalletUpdate,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Change limits, purpose or status. Balances are never editable here.

    `clear_limits` names limits to remove, because a JSON null is
    indistinguishable from "field omitted" and silently keeping an old limit
    when someone meant to lift it is the wrong failure.
    """
    wallet = await _wallet_or_404(session, wallet_id)
    sets, params = [], {"id": str(wallet_id)}

    field_map = {
        "department": "department", "purpose": "purpose",
        "single_txn_limit": "single_txn_limit", "daily_limit": "daily_limit",
        "monthly_limit": "monthly_limit",
        "self_approve_limit": "self_approve_limit",
    }
    for field, column in field_map.items():
        value = getattr(body, field)
        if value is not None:
            sets.append(f"{column} = :{field}")
            params[field] = str(value) if isinstance(value, Decimal) else value

    for name in body.clear_limits:
        if name not in field_map:
            raise HTTPException(
                status_code=400,
                detail=f"{name!r} is not a limit that can be cleared.")
        sets.append(f"{field_map[name]} = NULL")

    if body.status is not None:
        if body.status not in ("ACTIVE", "SUSPENDED", "FROZEN", "CLOSED"):
            raise HTTPException(status_code=400, detail="Unknown wallet status.")
        if body.status == "CLOSED":
            totals = await svc.derive_totals(session, wallet_id)
            if totals["balance"] != Decimal("0.00"):
                raise HTTPException(
                    status_code=400,
                    detail=(f"Wallet {wallet['wallet_number']} still holds "
                            f"{totals['balance']:,.2f}. Record the returned "
                            f"funds before closing it."))
        sets.append("status = :status")
        params["status"] = body.status

    if not sets:
        raise HTTPException(status_code=400, detail="Nothing to change.")

    sets.append("updated_at = NOW()")
    await session.execute(
        text(f"UPDATE wallets SET {', '.join(sets)} WHERE id = :id"), params)
    await svc.audit(
        session, event_type="WALLET_UPDATED", entity_type="wallet",
        entity_id=wallet_id, wallet_id=wallet_id, actor_user_id=user.id,
        actor_label=user.full_name, result=body.status or "UPDATED",
        detail={"changes": body.model_dump(exclude_none=True)},
        **_client(request),
    )
    await session.commit()
    return await svc.wallet_summary(session, wallet_id)


@router.get("/wallets/{wallet_id}/transactions")
async def wallet_transactions(
    wallet_id: UUID,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """The append-only ledger, newest first, with the running balance."""
    wallet = await _wallet_or_404(session, wallet_id)
    await svc.assert_can_view_wallet(session, user, wallet)

    rows = (await session.execute(
        text("""
            SELECT l.id, l.sequence, l.entry_type, l.direction, l.amount,
                   l.balance_after, l.description, l.occurred_on,
                   l.source_type, l.source_id, l.reverses_entry_id,
                   l.journal_entry_id, l.created_at, u.full_name AS created_by
              FROM wallet_ledger l
              LEFT JOIN users u ON u.id = l.created_by
             WHERE l.wallet_id = :w
             ORDER BY l.sequence DESC
             LIMIT :lim OFFSET :off
        """),
        {"w": str(wallet_id), "lim": limit, "off": offset},
    )).mappings().all()
    total = (await session.execute(
        text("SELECT COUNT(*) AS c FROM wallet_ledger WHERE wallet_id = :w"),
        {"w": str(wallet_id)},
    )).first()
    return {"transactions": _rows(rows), "total": total.c,
            "limit": limit, "offset": offset}


@router.get("/wallets/{wallet_id}/expenses")
async def wallet_expenses(
    wallet_id: UUID,
    status: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    wallet = await _wallet_or_404(session, wallet_id)
    await svc.assert_can_view_wallet(session, user, wallet)
    clause = "AND e.status = :st" if status else ""
    params = {"w": str(wallet_id), "lim": limit}
    if status:
        params["st"] = status
    rows = (await session.execute(
        text(f"""
            SELECT e.id, e.expense_reference, e.amount, e.spent_on, e.purpose,
                   e.description, e.vendor, e.location, e.status,
                   e.required_tier, e.submitted_at, e.decided_at,
                   e.decision_note, c.name AS category, c.id AS category_id,
                   u.full_name AS decided_by,
                   (SELECT COUNT(*) FROM wallet_receipts r
                     WHERE r.expense_id = e.id) AS receipts
              FROM wallet_expenses e
              JOIN wallet_expense_categories c ON c.id = e.category_id
              LEFT JOIN users u ON u.id = e.decided_by
             WHERE e.wallet_id = :w {clause}
             ORDER BY e.spent_on DESC, e.submitted_at DESC
             LIMIT :lim
        """), params,
    )).mappings().all()
    return {"expenses": _rows(rows)}


@router.get("/wallets/{wallet_id}/report")
async def accountability_report(
    wallet_id: UUID,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Section 20: one employee's complete money picture."""
    wallet = await _wallet_or_404(session, wallet_id)
    await svc.assert_can_view_wallet(session, user, wallet)
    return await svc.staff_accountability_report(session, wallet_id)


@router.get("/wallets/{wallet_id}/verify")
async def verify_wallet(
    wallet_id: UUID,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Re-derive the balance from the ledger and compare it to the cache."""
    return await svc.verify_wallet_integrity(session, wallet_id)


# ---------------------------------------------------------------------------
# Funding
# ---------------------------------------------------------------------------

@router.post("/wallets/{wallet_id}/fundings", status_code=201)
async def record_funding(
    wallet_id: UUID,
    body: FundingIn,
    request: Request,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Record that money has been given to the wallet holder.

    IMPORTANT: this does not send money. The transfer or cash handover happens
    through the company's existing arrangements; this records the
    accountability so the amount can be tracked until it is spent or returned.
    """
    wallet = await _wallet_or_404(session, wallet_id)
    await svc.assert_can_fund(session, user, wallet)

    if str(wallet["user_id"]) == str(user.id) and user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="You cannot record an advance to your own wallet.")

    result = await svc.fund_wallet(
        session,
        wallet_id=wallet_id, amount=body.amount, purpose=body.purpose,
        disbursement_method=body.disbursement_method,
        disbursement_reference=body.disbursement_reference,
        source_account_code=body.source_account_code,
        funded_on=body.funded_on or date.today(),
        account_by=body.account_by, notes=body.notes,
        fund_request_id=body.fund_request_id,
        idempotency_key=body.idempotency_key, issued_by=user.id,
        **_client(request),
    )

    if body.fund_request_id:
        await session.execute(
            text("""UPDATE wallet_fund_requests
                       SET status = 'FUNDED', funding_id = :f
                     WHERE id = :id AND status IN
                           ('APPROVED','PARTIALLY_APPROVED')"""),
            {"f": result["id"], "id": str(body.fund_request_id)},
        )

    await session.commit()
    return result


@router.post("/fundings/{funding_id}/reverse")
async def reverse_funding(
    funding_id: UUID,
    body: ReverseIn,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Undo an advance recorded in error. The original stays on the record."""
    result = await svc.reverse_funding(
        session, funding_id=funding_id, reason=body.reason, actor_id=user.id)
    await svc.audit(
        session, event_type="FUNDING_REVERSED", entity_type="wallet_funding",
        entity_id=funding_id, actor_user_id=user.id,
        actor_label=user.full_name, result="REVERSED",
        detail={"reason": body.reason}, **_client(request),
    )
    await session.commit()
    return result


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------

@router.post("/wallets/{wallet_id}/expenses", status_code=201)
async def create_expense(
    wallet_id: UUID,
    body: ExpenseIn,
    request: Request,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Record money spent from this wallet.

    Only the holder records their own spending. Someone recording an expense
    on another person's wallet would break the one thing the module is for:
    that the person entrusted with the money is the person who answers for it.
    """
    wallet = await _wallet_or_404(session, wallet_id)
    if str(wallet["user_id"]) != str(user.id):
        raise HTTPException(
            status_code=403,
            detail=("You can only record expenses on your own wallet. If you "
                    "paid for something on someone else's behalf, submit a "
                    "reimbursement claim instead."))

    result = await svc.submit_expense(
        session,
        wallet_id=wallet_id, category_id=body.category_id, amount=body.amount,
        spent_on=body.spent_on or date.today(), purpose=body.purpose,
        description=body.description, vendor=body.vendor,
        location=body.location, payment_method=body.payment_method,
        project_reference=body.project_reference, customer_id=body.customer_id,
        latitude=body.latitude, longitude=body.longitude,
        gps_accuracy=body.gps_accuracy, notes=body.notes,
        idempotency_key=body.idempotency_key, submitted_by=user.id,
        **_client(request),
    )
    await session.commit()
    return result


@router.post("/expenses/{expense_id}/receipt", status_code=201)
async def upload_receipt(
    expense_id: UUID,
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Attach a receipt photograph to an expense.

    Read in bounded chunks rather than with `await file.read()`: an unbounded
    read would load whatever was uploaded into memory before the size check
    could reject it.
    """
    expense = (await session.execute(
        text("""SELECT e.id, e.wallet_id, e.status, e.submitted_by
                  FROM wallet_expenses e WHERE e.id = :id"""),
        {"id": str(expense_id)},
    )).mappings().first()
    if expense is None:
        raise HTTPException(status_code=404, detail="Expense not found.")
    if str(expense["submitted_by"]) != str(user.id) and user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Only the person who submitted the expense can attach its "
                   "receipt.")
    if expense["status"] == "REVERSED":
        raise HTTPException(
            status_code=400,
            detail="This expense has been reversed; a receipt cannot be added.")

    content = await _read_capped(file)
    result = await svc.attach_receipt(
        session, filename=file.filename or "receipt",
        content_type=file.content_type or "application/octet-stream",
        content=content, uploaded_by=user.id, expense_id=expense_id,
    )
    await session.commit()
    return result


async def _read_capped(file: UploadFile) -> bytes:
    buf = bytearray()
    while True:
        chunk = await file.read(256 * 1024)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > svc.MAX_RECEIPT_BYTES:
            raise HTTPException(
                status_code=400,
                detail=(f"Receipt is larger than "
                        f"{svc.MAX_RECEIPT_BYTES // 1_048_576}MB. Photograph "
                        f"it at a lower resolution."))
    return bytes(buf)


@router.get("/receipts/{receipt_id}")
async def download_receipt(
    receipt_id: UUID,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Serve a receipt to someone entitled to see the wallet it belongs to."""
    row = (await session.execute(
        text("""SELECT r.content, r.content_type, r.filename, r.expense_id,
                       r.reimbursement_id, e.wallet_id, m.user_id AS claimant
                  FROM wallet_receipts r
                  LEFT JOIN wallet_expenses e ON e.id = r.expense_id
                  LEFT JOIN wallet_reimbursements m
                         ON m.id = r.reimbursement_id
                 WHERE r.id = :id"""),
        {"id": str(receipt_id)},
    )).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Receipt not found.")

    if row["wallet_id"] is not None:
        wallet = await svc.get_wallet(session, row["wallet_id"])
        await svc.assert_can_view_wallet(session, user, wallet)
    else:
        caps = await svc.user_capabilities(session, user)
        if str(row["claimant"]) != str(user.id) and not caps["can_approve"]:
            raise HTTPException(
                status_code=403,
                detail="You do not have access to this receipt.")

    return Response(
        content=row["content"],
        media_type=row["content_type"],
        headers={
            "Content-Disposition":
                f'inline; filename="{row["filename"]}"',
            # Receipts are financial evidence tied to one person's wallet;
            # a shared cache must never hand one to the next viewer.
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/expenses/{expense_id}/receipts")
async def list_expense_receipts(
    expense_id: UUID,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    head = (await session.execute(
        text("SELECT wallet_id FROM wallet_expenses WHERE id = :id"),
        {"id": str(expense_id)},
    )).first()
    if head is None:
        raise HTTPException(status_code=404, detail="Expense not found.")
    wallet = await svc.get_wallet(session, head.wallet_id)
    await svc.assert_can_view_wallet(session, user, wallet)
    rows = (await session.execute(
        text("""SELECT id, filename, content_type, byte_size, sha256,
                       created_at
                  FROM wallet_receipts WHERE expense_id = :e
                 ORDER BY created_at"""),
        {"e": str(expense_id)},
    )).mappings().all()
    return {"receipts": _rows(rows)}


@router.get("/approvals/queue")
async def approval_queue(
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Expenses waiting that THIS user is actually able to approve."""
    return {"queue": await svc.approval_queue(session, user)}


@router.post("/expenses/{expense_id}/decision")
async def decide_expense(
    expense_id: UUID,
    body: DecisionIn,
    request: Request,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    result = await svc.decide_expense(
        session, expense_id=expense_id, approve=body.approve, user=user,
        note=body.note)
    await session.commit()
    return result


@router.post("/expenses/{expense_id}/reverse")
async def reverse_expense(
    expense_id: UUID,
    body: ReverseIn,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Correct an approved expense by posting its mirror image."""
    result = await svc.reverse_expense(
        session, expense_id=expense_id, reason=body.reason, actor_id=user.id)
    await session.commit()
    return result


# ---------------------------------------------------------------------------
# Top-up requests
# ---------------------------------------------------------------------------

@router.post("/wallets/{wallet_id}/fund-requests", status_code=201)
async def request_funds(
    wallet_id: UUID,
    body: FundRequestIn,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Ask for more money without having to telephone management."""
    wallet = await _wallet_or_404(session, wallet_id)
    if str(wallet["user_id"]) != str(user.id):
        raise HTTPException(
            status_code=403,
            detail="You can only request funds for your own wallet.")
    result = await svc.request_funds(
        session, wallet_id=wallet_id, amount=body.amount, reason=body.reason,
        urgency=body.urgency, requested_by=user.id)
    await session.commit()
    return result


@router.get("/fund-requests")
async def list_fund_requests(
    status: Optional[str] = None,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    caps = await svc.user_capabilities(session, user)
    where = ["1 = 1"]
    params: dict = {}
    if status:
        where.append("r.status = :st")
        params["st"] = status
    if not (caps["is_admin"] or caps["can_fund"]):
        where.append("r.requested_by = :self")
        params["self"] = str(user.id)

    rows = (await session.execute(
        text(f"""
            SELECT r.id, r.request_reference, r.amount_requested,
                   r.amount_approved, r.reason, r.urgency, r.status,
                   r.balance_at_request, r.requested_at, r.decided_at,
                   r.decision_note, w.wallet_number, w.department,
                   w.id AS wallet_id, u.full_name AS requested_by
              FROM wallet_fund_requests r
              JOIN wallets w ON w.id = r.wallet_id
              JOIN users u ON u.id = r.requested_by
             WHERE {' AND '.join(where)}
             ORDER BY CASE r.urgency WHEN 'EMERGENCY' THEN 0
                                     WHEN 'URGENT' THEN 1 ELSE 2 END,
                      r.requested_at ASC
        """), params,
    )).mappings().all()
    return {"requests": _rows(rows)}


@router.post("/fund-requests/{request_id}/decision")
async def decide_fund_request(
    request_id: UUID,
    body: FundRequestDecisionIn,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    result = await svc.decide_fund_request(
        session, request_id=request_id, decision=body.decision, user=user,
        amount_approved=body.amount_approved, note=body.note)
    await session.commit()
    return result


# ---------------------------------------------------------------------------
# Returns
# ---------------------------------------------------------------------------

@router.post("/wallets/{wallet_id}/returns", status_code=201)
async def declare_return(
    wallet_id: UUID,
    body: ReturnIn,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Declare that unused money has been handed back. Confirmed separately."""
    wallet = await _wallet_or_404(session, wallet_id)
    if str(wallet["user_id"]) != str(user.id) and user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Only the wallet holder can declare a return of funds.")
    result = await svc.declare_return(
        session, wallet_id=wallet_id, amount=body.amount,
        returned_on=body.returned_on or date.today(), method=body.method,
        reference=body.reference,
        destination_account_code=body.destination_account_code,
        note=body.note, declared_by=user.id)
    await session.commit()
    return result


@router.get("/returns")
async def list_returns(
    status: Optional[str] = "DECLARED",
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    caps = await svc.user_capabilities(session, user)
    where = ["1 = 1"]
    params: dict = {}
    if status:
        where.append("r.status = :st")
        params["st"] = status
    if not (caps["is_admin"] or caps["can_reconcile"]):
        where.append("r.declared_by = :self")
        params["self"] = str(user.id)

    rows = (await session.execute(
        text(f"""
            SELECT r.id, r.return_reference, r.amount, r.returned_on,
                   r.method, r.reference, r.status, r.declared_at,
                   r.confirmed_at, r.note, w.wallet_number, w.department,
                   u.full_name AS declared_by
              FROM wallet_returns r
              JOIN wallets w ON w.id = r.wallet_id
              JOIN users u ON u.id = r.declared_by
             WHERE {' AND '.join(where)}
             ORDER BY r.declared_at DESC
        """), params,
    )).mappings().all()
    return {"returns": _rows(rows)}


@router.post("/returns/{return_id}/confirm")
async def confirm_return(
    return_id: UUID,
    body: ReturnConfirmIn,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Confirm the money is physically back. This is what moves the balance."""
    await svc.assert_can_reconcile(session, user)
    result = await svc.confirm_return(
        session, return_id=return_id, confirm=body.confirm, user=user,
        note=body.note)
    await session.commit()
    return result


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

@router.post("/wallets/{wallet_id}/reconciliations", status_code=201)
async def submit_reconciliation(
    wallet_id: UUID,
    body: ReconciliationIn,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    wallet = await _wallet_or_404(session, wallet_id)
    if str(wallet["user_id"]) != str(user.id) and user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Only the wallet holder submits their reconciliation.")
    result = await svc.submit_reconciliation(
        session, wallet_id=wallet_id, period_start=body.period_start,
        period_end=body.period_end,
        declared_cash_on_hand=body.declared_cash_on_hand,
        note=body.note, submitted_by=user.id)
    await session.commit()
    return result


@router.get("/reconciliations")
async def list_reconciliations(
    status: Optional[str] = None,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    caps = await svc.user_capabilities(session, user)
    where = ["1 = 1"]
    params: dict = {}
    if status:
        where.append("r.status = :st")
        params["st"] = status
    if not (caps["is_admin"] or caps["can_reconcile"]):
        where.append("r.submitted_by = :self")
        params["self"] = str(user.id)
    rows = (await session.execute(
        text(f"""
            SELECT r.id, r.reconciliation_reference, r.period_start,
                   r.period_end, r.opening_balance, r.total_funded,
                   r.total_expenses, r.total_returned, r.closing_balance,
                   r.declared_cash_on_hand, r.variance, r.status,
                   r.submitted_at, r.reviewed_at, r.review_note,
                   w.wallet_number, w.department, u.full_name AS submitted_by
              FROM wallet_reconciliations r
              JOIN wallets w ON w.id = r.wallet_id
              JOIN users u ON u.id = r.submitted_by
             WHERE {' AND '.join(where)}
             ORDER BY r.submitted_at DESC
        """), params,
    )).mappings().all()
    return {"reconciliations": _rows(rows)}


@router.post("/reconciliations/{reconciliation_id}/review")
async def review_reconciliation(
    reconciliation_id: UUID,
    body: ReconciliationReviewIn,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    result = await svc.review_reconciliation(
        session, reconciliation_id=reconciliation_id, accept=body.accept,
        user=user, note=body.note)
    await session.commit()
    return result


# ---------------------------------------------------------------------------
# Reimbursements
# ---------------------------------------------------------------------------

@router.post("/reimbursements", status_code=201)
async def submit_reimbursement(
    body: ReimbursementIn,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Claim back money the employee spent from their own pocket.

    Never touches a wallet balance: no company money was entrusted, so none is
    discharged. The company simply owes the employee.
    """
    result = await svc.submit_reimbursement(
        session, user_id=user.id, category_id=body.category_id,
        amount=body.amount, spent_on=body.spent_on, purpose=body.purpose,
        description=body.description, vendor=body.vendor,
        department=body.department or user.department,
        wallet_id=body.wallet_id)
    await session.commit()
    return result


@router.post("/reimbursements/{reimbursement_id}/receipt", status_code=201)
async def upload_reimbursement_receipt(
    reimbursement_id: UUID,
    file: UploadFile = File(...),
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    claim = (await session.execute(
        text("SELECT user_id, status FROM wallet_reimbursements WHERE id = :id"),
        {"id": str(reimbursement_id)},
    )).mappings().first()
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found.")
    if str(claim["user_id"]) != str(user.id) and user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Only the claimant can attach a receipt to their claim.")
    content = await _read_capped(file)
    result = await svc.attach_receipt(
        session, filename=file.filename or "receipt",
        content_type=file.content_type or "application/octet-stream",
        content=content, uploaded_by=user.id,
        reimbursement_id=reimbursement_id)
    await session.commit()
    return result


@router.get("/reimbursements")
async def list_reimbursements(
    status: Optional[str] = None,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    caps = await svc.user_capabilities(session, user)
    where = ["1 = 1"]
    params: dict = {}
    if status:
        where.append("m.status = :st")
        params["st"] = status
    if not (caps["is_admin"] or caps["can_approve"]):
        where.append("m.user_id = :self")
        params["self"] = str(user.id)
    rows = (await session.execute(
        text(f"""
            SELECT m.id, m.reimbursement_reference, m.amount,
                   m.amount_approved, m.spent_on, m.purpose, m.description,
                   m.vendor, m.department, m.status, m.submitted_at,
                   m.decided_at, m.decision_note, m.paid_on,
                   c.name AS category, u.full_name AS claimant,
                   (SELECT COUNT(*) FROM wallet_receipts r
                     WHERE r.reimbursement_id = m.id) AS receipts
              FROM wallet_reimbursements m
              JOIN wallet_expense_categories c ON c.id = m.category_id
              JOIN users u ON u.id = m.user_id
             WHERE {' AND '.join(where)}
             ORDER BY m.submitted_at DESC
        """), params,
    )).mappings().all()
    return {"reimbursements": _rows(rows)}


@router.post("/reimbursements/{reimbursement_id}/decision")
async def decide_reimbursement(
    reimbursement_id: UUID,
    body: ReimbursementDecisionIn,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    result = await svc.decide_reimbursement(
        session, reimbursement_id=reimbursement_id, approve=body.approve,
        user=user, amount_approved=body.amount_approved, note=body.note)
    await session.commit()
    return result


@router.post("/reimbursements/{reimbursement_id}/pay")
async def pay_reimbursement(
    reimbursement_id: UUID,
    body: ReimbursementPayIn,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Record that the employee has been paid back, settling the liability."""
    result = await svc.pay_reimbursement(
        session, reimbursement_id=reimbursement_id, user=user,
        paid_on=body.paid_on or date.today(),
        payment_method=body.payment_method,
        payment_reference=body.payment_reference,
        paid_from_account_code=body.paid_from_account_code)
    await session.commit()
    return result


# ---------------------------------------------------------------------------
# Management reporting
# ---------------------------------------------------------------------------

@router.get("/dashboard")
async def dashboard(
    department: Optional[str] = None,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Company-wide position. Visible to administrators and approvers only."""
    caps = await svc.user_capabilities(session, user)
    if not (caps["is_admin"] or caps["can_approve"] or caps["can_fund"]):
        raise HTTPException(
            status_code=403,
            detail="The management dashboard is for approvers and "
                   "administrators.")
    return await svc.management_dashboard(session, department=department)


@router.get("/flags")
async def list_flags(
    status: str = "OPEN",
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Anomalies awaiting a human view. These are questions, not accusations."""
    caps = await svc.user_capabilities(session, user)
    if not (caps["is_admin"] or caps["can_approve"]):
        raise HTTPException(
            status_code=403, detail="Only approvers can review flags.")
    rows = (await session.execute(
        text("""
            SELECT f.id, f.flag_type, f.severity, f.message, f.detail,
                   f.status, f.created_at, f.review_note,
                   w.wallet_number, w.department, u.full_name AS holder,
                   e.expense_reference, e.amount AS expense_amount
              FROM wallet_flags f
              JOIN wallets w ON w.id = f.wallet_id
              JOIN users u ON u.id = w.user_id
              LEFT JOIN wallet_expenses e ON e.id = f.expense_id
             WHERE f.status = :st
             ORDER BY CASE f.severity WHEN 'HIGH' THEN 0
                                      WHEN 'MEDIUM' THEN 1 ELSE 2 END,
                      f.created_at DESC
        """), {"st": status},
    )).mappings().all()
    return {"flags": _rows(rows)}


@router.post("/flags/{flag_id}/review")
async def review_flag(
    flag_id: UUID,
    body: FlagReviewIn,
    request: Request,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    caps = await svc.user_capabilities(session, user)
    if not (caps["is_admin"] or caps["can_approve"]):
        raise HTTPException(
            status_code=403, detail="Only approvers can review flags.")
    if body.status not in ("REVIEWED", "DISMISSED"):
        raise HTTPException(
            status_code=400, detail="A flag is either REVIEWED or DISMISSED.")
    result = await session.execute(
        text("""UPDATE wallet_flags
                   SET status = :s, reviewed_by = :by, reviewed_at = NOW(),
                       review_note = :note
                 WHERE id = :id AND status = 'OPEN'"""),
        {"s": body.status, "by": str(user.id), "note": body.note,
         "id": str(flag_id)},
    )
    if result.rowcount == 0:
        raise HTTPException(
            status_code=404,
            detail="That flag does not exist or has already been reviewed.")
    await svc.audit(
        session, event_type=f"FLAG_{body.status}", entity_type="wallet_flag",
        entity_id=flag_id, actor_user_id=user.id, actor_label=user.full_name,
        result=body.status, detail={"note": body.note}, **_client(request),
    )
    await session.commit()
    return {"id": str(flag_id), "status": body.status}


@router.post("/flags/sweep")
async def sweep_flags(
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Re-scan approved expenses for missing receipts."""
    count = await svc.sweep_missing_receipts(session)
    await session.commit()
    return {"flagged": count}


@router.get("/audit")
async def audit_trail(
    wallet_id: Optional[UUID] = None,
    event_type: Optional[str] = None,
    limit: int = Query(200, ge=1, le=1000),
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """The append-only trail. Administrators only; nobody can edit it."""
    where = ["1 = 1"]
    params: dict = {"lim": limit}
    if wallet_id:
        where.append("a.wallet_id = :w")
        params["w"] = str(wallet_id)
    if event_type:
        where.append("a.event_type = :e")
        params["e"] = event_type
    rows = (await session.execute(
        text(f"""
            SELECT a.id, a.event_type, a.entity_type, a.entity_id,
                   a.wallet_id, a.amount, a.actor_label, a.ip_address,
                   a.user_agent, a.result, a.detail, a.created_at,
                   u.full_name AS actor, w.wallet_number
              FROM wallet_audit_logs a
              LEFT JOIN users u ON u.id = a.actor_user_id
              LEFT JOIN wallets w ON w.id = a.wallet_id
             WHERE {' AND '.join(where)}
             ORDER BY a.created_at DESC
             LIMIT :lim
        """), params,
    )).mappings().all()
    return {"events": _rows(rows)}


@router.get("/reports/expenses.csv")
async def export_expenses(
    date_from: date,
    date_to: date,
    department: Optional[str] = None,
    user: User = Depends(require_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    """Expense register for a period, as CSV for Excel."""
    caps = await svc.user_capabilities(session, user)
    if not (caps["is_admin"] or caps["can_approve"] or caps["can_fund"]):
        raise HTTPException(
            status_code=403, detail="You cannot export company-wide reports.")
    if date_to < date_from:
        raise HTTPException(
            status_code=400, detail="The end date precedes the start date.")

    clause = "AND w.department = :dept" if department else ""
    params = {"s": date_from, "e": date_to}
    if department:
        params["dept"] = department

    rows = (await session.execute(
        text(f"""
            SELECT e.expense_reference, e.spent_on, w.wallet_number,
                   u.full_name AS holder, w.department, c.name AS category,
                   e.amount, e.purpose, e.vendor, e.status,
                   d.full_name AS decided_by, e.decided_at,
                   (SELECT COUNT(*) FROM wallet_receipts r
                     WHERE r.expense_id = e.id) AS receipts
              FROM wallet_expenses e
              JOIN wallets w ON w.id = e.wallet_id
              JOIN users u ON u.id = w.user_id
              JOIN wallet_expense_categories c ON c.id = e.category_id
              LEFT JOIN users d ON d.id = e.decided_by
             WHERE e.spent_on BETWEEN :s AND :e {clause}
             ORDER BY e.spent_on, e.expense_reference
        """), params,
    )).mappings().all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Reference", "Date", "Wallet", "Holder", "Department", "Category",
        "Amount", "Purpose", "Vendor", "Status", "Decided by", "Decided at",
        "Receipts",
    ])
    for r in rows:
        writer.writerow([
            r["expense_reference"], r["spent_on"], r["wallet_number"],
            r["holder"], r["department"], r["category"], r["amount"],
            r["purpose"], r["vendor"], r["status"], r["decided_by"],
            r["decided_at"], r["receipts"],
        ])
    filename = f"wallet-expenses-{date_from}-to-{date_to}.csv"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Administration: categories, ladder, approvers, category caps
# ---------------------------------------------------------------------------

@router.post("/admin/categories", status_code=201)
async def create_category(
    body: CategoryIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    if body.gl_account_code:
        exists = (await session.execute(
            text("SELECT 1 FROM gl_accounts WHERE code = :c AND is_postable"),
            {"c": body.gl_account_code},
        )).first()
        if exists is None:
            raise HTTPException(
                status_code=400,
                detail=(f"{body.gl_account_code} is not a postable account in "
                        f"the chart of accounts."))
    cat_id = uuid4()
    await session.execute(
        text("""
            INSERT INTO wallet_expense_categories
                (id, code, name, description, gl_account_code,
                 requires_receipt, default_monthly_cap, sort_order, created_by)
            VALUES (:id, :c, :n, :d, :gl, :rr, :cap, :so, :by)
            ON CONFLICT (code) DO NOTHING
        """),
        {"id": str(cat_id), "c": body.code.upper(), "n": body.name,
         "d": body.description, "gl": body.gl_account_code,
         "rr": body.requires_receipt,
         "cap": str(body.default_monthly_cap)
                if body.default_monthly_cap else None,
         "so": body.sort_order, "by": str(user.id)},
    )
    await session.commit()
    row = (await session.execute(
        text("SELECT * FROM wallet_expense_categories WHERE code = :c"),
        {"c": body.code.upper()},
    )).mappings().first()
    return _rows([row])[0]


@router.patch("/admin/categories/{category_id}")
async def update_category(
    category_id: UUID,
    body: CategoryUpdate,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    sets, params = [], {"id": str(category_id)}
    for field in ("name", "description", "gl_account_code",
                  "requires_receipt", "default_monthly_cap", "is_active",
                  "sort_order"):
        value = getattr(body, field)
        if value is not None:
            sets.append(f"{field} = :{field}")
            params[field] = str(value) if isinstance(value, Decimal) else value
    if not sets:
        raise HTTPException(status_code=400, detail="Nothing to change.")
    sets.append("updated_at = NOW()")
    result = await session.execute(
        text(f"UPDATE wallet_expense_categories SET {', '.join(sets)} "
             f"WHERE id = :id"), params)
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Category not found.")
    await session.commit()
    return {"id": str(category_id), "updated": True}


@router.get("/admin/approval-rules")
async def list_approval_rules(
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.execute(
        text("""SELECT r.id, r.scope, r.wallet_type, r.wallet_id,
                       r.min_amount, r.max_amount, r.tier, r.is_active,
                       r.priority, w.wallet_number
                  FROM wallet_approval_rules r
                  LEFT JOIN wallets w ON w.id = r.wallet_id
                 ORDER BY CASE r.scope WHEN 'WALLET' THEN 0
                                       WHEN 'WALLET_TYPE' THEN 1 ELSE 2 END,
                          r.min_amount"""),
    )).mappings().all()
    return {"rules": _rows(rows)}


@router.post("/admin/approval-rules", status_code=201)
async def create_approval_rule(
    body: ApprovalRuleIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Add or replace a band in the approval ladder.

    Thresholds are data, never code: management changes what needs whose
    signature without a deployment.
    """
    rule_id = uuid4()
    await session.execute(
        text("""
            INSERT INTO wallet_approval_rules
                (id, scope, wallet_type, wallet_id, min_amount, max_amount,
                 tier, priority, created_by)
            VALUES (:id, :s, :wt, :w, :lo, :hi, :t, :p, :by)
        """),
        {"id": str(rule_id), "s": body.scope, "wt": body.wallet_type,
         "w": str(body.wallet_id) if body.wallet_id else None,
         "lo": str(body.min_amount),
         "hi": str(body.max_amount) if body.max_amount else None,
         "t": body.tier, "p": body.priority, "by": str(user.id)},
    )
    await svc.audit(
        session, event_type="APPROVAL_RULE_CREATED",
        entity_type="wallet_approval_rule", entity_id=rule_id,
        actor_user_id=user.id, actor_label=user.full_name,
        detail=body.model_dump(mode="json"),
    )
    await session.commit()
    return {"id": str(rule_id), **body.model_dump(mode="json")}


@router.delete("/admin/approval-rules/{rule_id}")
async def deactivate_approval_rule(
    rule_id: UUID,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Deactivate a band. The row stays so past decisions remain explicable."""
    result = await session.execute(
        text("""UPDATE wallet_approval_rules SET is_active = FALSE,
                       updated_at = NOW()
                 WHERE id = :id AND is_active = TRUE"""),
        {"id": str(rule_id)})
    if result.rowcount == 0:
        raise HTTPException(
            status_code=404,
            detail="That rule does not exist or is already inactive.")
    await svc.audit(
        session, event_type="APPROVAL_RULE_DEACTIVATED",
        entity_type="wallet_approval_rule", entity_id=rule_id,
        actor_user_id=user.id, actor_label=user.full_name)
    await session.commit()
    return {"id": str(rule_id), "is_active": False}


@router.get("/admin/approvers")
async def list_approvers(
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.execute(
        text("""SELECT a.id, a.user_id, a.tier, a.max_amount, a.department,
                       a.wallet_id, a.can_fund, a.can_reconcile, a.is_active,
                       a.granted_at, u.full_name, u.email,
                       w.wallet_number
                  FROM wallet_approvers a
                  JOIN users u ON u.id = a.user_id
                  LEFT JOIN wallets w ON w.id = a.wallet_id
                 ORDER BY u.full_name"""),
    )).mappings().all()
    return {"approvers": _rows(rows)}


@router.post("/admin/approvers", status_code=201)
async def grant_approver(
    body: ApproverIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Give someone approval authority, scoped as tightly as you like.

    This is how supervisors and finance staff get their powers without adding
    global roles to the shared authentication code the whole ERP depends on.
    """
    holder = (await session.execute(
        text("SELECT full_name, is_active FROM users WHERE id = :u"),
        {"u": str(body.user_id)},
    )).mappings().first()
    if holder is None:
        raise HTTPException(status_code=404, detail="That user does not exist.")
    if not holder["is_active"]:
        raise HTTPException(
            status_code=400,
            detail="That account is not active and cannot be given authority.")

    grant_id = uuid4()
    await session.execute(
        text("""
            INSERT INTO wallet_approvers
                (id, user_id, tier, max_amount, department, wallet_id,
                 can_fund, can_reconcile, granted_by)
            VALUES (:id, :u, :t, :max, :d, :w, :f, :r, :by)
        """),
        {"id": str(grant_id), "u": str(body.user_id), "t": body.tier,
         "max": str(body.max_amount) if body.max_amount else None,
         "d": body.department,
         "w": str(body.wallet_id) if body.wallet_id else None,
         "f": body.can_fund, "r": body.can_reconcile, "by": str(user.id)},
    )
    await svc.audit(
        session, event_type="APPROVER_GRANTED",
        entity_type="wallet_approver", entity_id=grant_id,
        actor_user_id=user.id, actor_label=user.full_name,
        detail={"grantee": holder["full_name"], **body.model_dump(mode="json")})
    await session.commit()
    return {"id": str(grant_id), "user": holder["full_name"],
            **body.model_dump(mode="json")}


@router.delete("/admin/approvers/{grant_id}")
async def revoke_approver(
    grant_id: UUID,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        text("""UPDATE wallet_approvers SET is_active = FALSE
                 WHERE id = :id AND is_active = TRUE"""),
        {"id": str(grant_id)})
    if result.rowcount == 0:
        raise HTTPException(
            status_code=404,
            detail="That grant does not exist or is already revoked.")
    await svc.audit(
        session, event_type="APPROVER_REVOKED",
        entity_type="wallet_approver", entity_id=grant_id,
        actor_user_id=user.id, actor_label=user.full_name)
    await session.commit()
    return {"id": str(grant_id), "is_active": False}


@router.put("/admin/wallets/{wallet_id}/category-limits")
async def set_category_limit(
    wallet_id: UUID,
    body: CategoryLimitIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    await _wallet_or_404(session, wallet_id)
    await session.execute(
        text("""
            INSERT INTO wallet_category_limits
                (id, wallet_id, category_id, monthly_cap, created_by)
            VALUES (gen_random_uuid(), :w, :c, :cap, :by)
            ON CONFLICT (wallet_id, category_id)
            DO UPDATE SET monthly_cap = EXCLUDED.monthly_cap
        """),
        {"w": str(wallet_id), "c": str(body.category_id),
         "cap": str(body.monthly_cap), "by": str(user.id)},
    )
    await session.commit()
    return {"wallet_id": str(wallet_id), "category_id": str(body.category_id),
            "monthly_cap": str(body.monthly_cap)}


@router.get("/admin/wallets/{wallet_id}/category-limits")
async def get_category_limits(
    wallet_id: UUID,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.execute(
        text("""SELECT cl.id, cl.category_id, cl.monthly_cap, c.name, c.code
                  FROM wallet_category_limits cl
                  JOIN wallet_expense_categories c ON c.id = cl.category_id
                 WHERE cl.wallet_id = :w ORDER BY c.sort_order"""),
        {"w": str(wallet_id)},
    )).mappings().all()
    return {"limits": _rows(rows)}
