"""
Public customer-facing ordering endpoints.

These endpoints power the public order page at /order.html and DO NOT require
authentication. Submitted orders are created in 'pending' status and DO NOT
deduct stock automatically — sales staff must review and confirm them in the
ERP before stock is allocated.

Pricing is sourced from the `product_pricing` table (the same source used by
the in-app Product Price List), so each (product, unit) variant is exposed
as its own line item with both retail and wholesale prices. The server picks
the price based on the chosen `customer_type` ('retail' or 'wholesale') and
enforces wholesale minimum-quantity rules.
"""
from __future__ import annotations

from app.api.auth import require_authenticated_user

import os
import re
import secrets
import shutil
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import List, Optional, Literal

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, EmailStr
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import (
    Product,
    Customer,
    Warehouse,
    SalesOrder,
    SalesOrderLine,
    StockLevel,
)
from app.api.notifications import notify_sale_created, fire_notification

router = APIRouter(prefix="/api/public", tags=["public"])

SUPPORT_WHATSAPP_NUMBER = "2347025755406"  # +234 702 575 5406

# --- Wholesale qualification rules (case-insensitive on unit) ---
# Units sold strictly in bulk packs (e.g. cartons): wholesale-only, any qty.
WHOLESALE_ONLY_UNITS = {"CTN", "CARTON"}
# Per-unit minimum quantities required to qualify for wholesale price.
# Units not listed are treated as wholesale-eligible at qty >= 1.
WHOLESALE_MIN_QTY = {
    "PCS": 24,
    "PIECE": 24,
    "PIECES": 24,
    "PACKET": 12,
    "PACKETS": 12,
    "PCK": 12,
    "TUBE": 12,
    "TUBES": 12,
}


# ---------- Schemas ----------
class PublicProductVariant(BaseModel):
    product_id: str
    sku: str
    name: str
    unit: str
    description: Optional[str] = None
    manufacturer: Optional[str] = None
    retail_price: float
    wholesale_price: float
    wholesale_min_qty: int
    wholesale_only: bool
    in_stock: bool


class PublicOrderItem(BaseModel):
    product_id: str
    unit: str
    quantity: float = Field(gt=0)


class PublicCustomerInfo(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    phone: str = Field(min_length=6, max_length=50)
    email: Optional[EmailStr] = None
    address: Optional[str] = None


class PublicOrderCreate(BaseModel):
    customer: PublicCustomerInfo
    customer_type: Literal["retail", "wholesale"] = "retail"
    items: List[PublicOrderItem] = Field(min_length=1)
    notes: Optional[str] = None


class PublicOrderResponse(BaseModel):
    success: bool
    order_number: str
    total_amount: float
    item_count: int
    customer_type: str
    customer_code: str
    message: str
    whatsapp_url: str


# ---------- Helpers ----------
def _norm_unit(u: Optional[str]) -> str:
    return (u or "").strip().upper()


def _wholesale_min_for(unit: str) -> int:
    nu = _norm_unit(unit)
    if nu in WHOLESALE_ONLY_UNITS:
        return 1
    return WHOLESALE_MIN_QTY.get(nu, 1)


def _is_wholesale_only(unit: str) -> bool:
    return _norm_unit(unit) in WHOLESALE_ONLY_UNITS


async def _get_default_warehouse(session: AsyncSession) -> Warehouse:
    result = await session.execute(
        select(Warehouse).where(
            func.upper(Warehouse.name) == "SALES WAREHOUSE"
        )
    )
    wh = result.scalar_one_or_none()
    if wh:
        return wh
    result = await session.execute(
        select(Warehouse)
        .where(Warehouse.is_active == True)  # noqa: E712
        .order_by(Warehouse.name)
    )
    wh = result.scalars().first()
    if not wh:
        raise HTTPException(
            status_code=500, detail="No active warehouse configured"
        )
    return wh


async def _find_or_create_customer(
    session: AsyncSession, info: PublicCustomerInfo
) -> Customer:
    phone_norm = re.sub(r"\D", "", info.phone or "")
    candidate = None
    if phone_norm:
        result = await session.execute(
            select(Customer).where(Customer.phone.isnot(None))
        )
        for c in result.scalars().all():
            if re.sub(r"\D", "", c.phone or "") == phone_norm:
                candidate = c
                break
    if candidate is None and info.email:
        result = await session.execute(
            select(Customer).where(
                func.lower(Customer.email) == info.email.lower()
            )
        )
        candidate = result.scalar_one_or_none()

    if candidate is not None:
        updated = False
        if not candidate.name and info.name:
            candidate.name = info.name
            updated = True
        if not candidate.email and info.email:
            candidate.email = str(info.email)
            updated = True
        if not candidate.address and info.address:
            candidate.address = info.address
            updated = True
        if updated:
            await session.flush()
        return candidate

    result = await session.execute(
        select(Customer.customer_code).where(
            Customer.customer_code.like("CUST-%")
        )
    )
    max_num = 0
    for (code,) in result.all():
        try:
            n = int(str(code).split("-")[1])
            if n > max_num:
                max_num = n
        except (ValueError, IndexError):
            continue
    next_code = f"CUST-{max_num + 1:04d}"

    customer = Customer(
        id=uuid.uuid4(),
        customer_code=next_code,
        name=info.name.strip(),
        email=str(info.email) if info.email else None,
        phone=info.phone.strip(),
        address=info.address.strip() if info.address else None,
        is_active=True,
    )
    session.add(customer)
    await session.flush()
    return customer


# ---------- Endpoints ----------
@router.get("/products", response_model=List[PublicProductVariant])
async def list_public_products(session: AsyncSession = Depends(get_session)):
    """One row per (product, unit) variant, with retail+wholesale prices."""
    sql = text(
        """
        SELECT
            p.id           AS product_id,
            p.sku          AS sku,
            p.name         AS name,
            p.description  AS description,
            p.manufacturer AS manufacturer,
            pp.unit        AS unit,
            pp.retail_price    AS retail_price,
            pp.wholesale_price AS wholesale_price
        FROM product_pricing pp
        JOIN products p ON p.id = pp.product_id
        WHERE pp.unit IS NOT NULL
          AND (
              COALESCE(pp.retail_price, 0) > 0
              OR COALESCE(pp.wholesale_price, 0) > 0
          )
        ORDER BY p.name, pp.unit
        """
    )
    rows = (await session.execute(sql)).mappings().all()

    stock_result = await session.execute(
        select(
            StockLevel.product_id,
            func.coalesce(func.sum(StockLevel.current_stock), 0),
        )
        .where(StockLevel.product_id.isnot(None))
        .group_by(StockLevel.product_id)
    )
    stock_map = {row[0]: float(row[1] or 0) for row in stock_result.all()}

    out: List[PublicProductVariant] = []
    for r in rows:
        unit = r["unit"]
        retail = float(r["retail_price"] or 0)
        wholesale = float(r["wholesale_price"] or 0)
        out.append(
            PublicProductVariant(
                product_id=str(r["product_id"]),
                sku=r["sku"],
                name=r["name"],
                unit=unit,
                description=r["description"],
                manufacturer=r["manufacturer"],
                retail_price=retail,
                wholesale_price=wholesale,
                wholesale_min_qty=_wholesale_min_for(unit),
                wholesale_only=_is_wholesale_only(unit),
                in_stock=stock_map.get(r["product_id"], 0) > 0,
            )
        )
    return out


@router.post("/orders", response_model=PublicOrderResponse)
async def create_public_order(
    payload: PublicOrderCreate,
    session: AsyncSession = Depends(get_session),
):
    """Create a pending sales order from a public submission."""
    customer_type = payload.customer_type
    if customer_type not in ("retail", "wholesale"):
        raise HTTPException(status_code=400, detail="Invalid customer_type")

    product_ids = []
    for it in payload.items:
        try:
            product_ids.append(uuid.UUID(it.product_id))
        except (ValueError, AttributeError):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid product_id: {it.product_id}",
            )

    result = await session.execute(
        select(Product).where(Product.id.in_(product_ids))
    )
    products_by_id = {p.id: p for p in result.scalars().all()}
    missing = [str(pid) for pid in product_ids if pid not in products_by_id]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Products not found: {', '.join(missing)}",
        )

    pricing_rows = (
        await session.execute(
            text(
                """
                SELECT product_id, unit, retail_price, wholesale_price
                FROM product_pricing
                WHERE product_id = ANY(:ids)
                """
            ).bindparams(ids=list({str(pid) for pid in product_ids}))
        )
    ).mappings().all()
    pricing_map = {
        (uuid.UUID(str(r["product_id"])), _norm_unit(r["unit"])): r
        for r in pricing_rows
    }

    warehouse = await _get_default_warehouse(session)
    customer = await _find_or_create_customer(session, payload.customer)

    order_number = (
        f"WEB-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
        f"-{str(uuid.uuid4())[:6].upper()}"
    )

    notes_parts = [f"[{customer_type.upper()}]"]
    if payload.notes:
        notes_parts.append(payload.notes)
    order = SalesOrder(
        id=uuid.uuid4(),
        order_number=order_number,
        customer_id=customer.id,
        warehouse_id=warehouse.id,
        status="pending",
        payment_status="unpaid",
        notes=" ".join(notes_parts),
        total_amount=Decimal("0"),
    )
    session.add(order)
    await session.flush()

    total = Decimal("0")
    for it in payload.items:
        pid = uuid.UUID(it.product_id)
        product = products_by_id[pid]
        unit_norm = _norm_unit(it.unit)
        pr = pricing_map.get((pid, unit_norm))
        if not pr:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Product '{product.name}' is not available in unit "
                    f"'{it.unit}'."
                ),
            )

        try:
            qty = Decimal(str(it.quantity))
        except (InvalidOperation, TypeError):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid quantity for {product.name}",
            )
        if qty <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"Quantity must be > 0 for {product.name}",
            )

        if customer_type == "wholesale":
            price = Decimal(str(pr["wholesale_price"] or 0))
            min_qty = _wholesale_min_for(unit_norm)
            if qty < Decimal(min_qty):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"For wholesale price on {product.name} "
                        f"({unit_norm}), minimum order is "
                        f"{min_qty} {unit_norm}."
                    ),
                )
            if price <= 0:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"No wholesale price configured for "
                        f"{product.name} ({unit_norm})."
                    ),
                )
        else:  # retail
            if _is_wholesale_only(unit_norm):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"{product.name} ({unit_norm}) is sold by the "
                        f"carton only. Please switch to Wholesaler "
                        f"to order this item."
                    ),
                )
            max_retail = WHOLESALE_MIN_QTY.get(unit_norm)
            if max_retail is not None and qty >= Decimal(max_retail):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"For retail price on {product.name} "
                        f"({unit_norm}), order less than {max_retail} "
                        f"{unit_norm}. Switch to Wholesaler for "
                        f"{max_retail}+."
                    ),
                )
            price = Decimal(str(pr["retail_price"] or 0))
            if price <= 0:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"No retail price configured for "
                        f"{product.name} ({unit_norm})."
                    ),
                )

        line_total = (price * qty).quantize(Decimal("0.01"))
        line = SalesOrderLine(
            id=uuid.uuid4(),
            sales_order_id=order.id,
            product_id=product.id,
            unit=it.unit,
            quantity=qty,
            unit_price=price,
            line_total=line_total,
        )
        session.add(line)
        total += line_total

    order.total_amount = total.quantize(Decimal("0.01"))
    await session.commit()

    try:
        fire_notification(
            notify_sale_created(
                order_number=order.order_number,
                customer_name=customer.name,
                total_amount=float(order.total_amount),
                line_count=len(payload.items),
            )
        )
    except Exception:
        pass

    msg_lines = [
        f"Hello, I just placed order {order.order_number} on the "
        f"BONNESANTE MEDICALS portal.",
        f"Customer: {customer.name} ({customer_type.upper()})",
        f"Phone: {customer.phone}",
        f"Total: NGN {float(order.total_amount):,.2f}",
        "Please confirm and advise on payment.",
    ]
    text_msg = "%0A".join(msg_lines).replace(" ", "%20")
    whatsapp_url = (
        f"https://wa.me/{SUPPORT_WHATSAPP_NUMBER}?text={text_msg}"
    )

    return PublicOrderResponse(
        success=True,
        order_number=order.order_number,
        total_amount=float(order.total_amount),
        item_count=len(payload.items),
        customer_type=customer_type,
        customer_code=customer.customer_code,
        message=(
            f"Order {order.order_number} received. Our sales team will "
            f"contact you shortly on {customer.phone} to confirm and "
            f"arrange delivery."
        ),
        whatsapp_url=whatsapp_url,
    )


@router.get("/orders/{order_number}")
async def get_public_order_status(
    order_number: str, session: AsyncSession = Depends(get_session)
):
    """Customer can check their order status by order number."""
    result = await session.execute(
        select(SalesOrder).where(SalesOrder.order_number == order_number)
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    cust_result = await session.execute(
        select(Customer).where(Customer.id == order.customer_id)
    )
    customer = cust_result.scalar_one_or_none()
    return {
        "order_number": order.order_number,
        "status": order.status,
        "payment_status": order.payment_status,
        "total_amount": float(order.total_amount or 0),
        "order_date": (
            order.order_date.isoformat() if order.order_date else None
        ),
        "customer_name": customer.name if customer else None,
    }


# ---------- Payment evidence upload ----------
PAYMENT_EVIDENCE_DIR = Path("/app/uploads/payment-evidence")
ALLOWED_EVIDENCE_MIME_PREFIXES = ("image/",)
ALLOWED_EVIDENCE_MIME_EXACT = {"application/pdf"}
MAX_EVIDENCE_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_EVIDENCE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".pdf",
}


def _ensure_evidence_dir() -> Path:
    # Fall back to a relative path if /app is not writable (dev/Windows)
    target = PAYMENT_EVIDENCE_DIR
    try:
        target.mkdir(parents=True, exist_ok=True)
        return target
    except (PermissionError, OSError):
        fallback = Path("uploads/payment-evidence").resolve()
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


@router.post("/orders/{order_number}/payment-evidence")
async def upload_payment_evidence(
    order_number: str,
    file: UploadFile = File(...),
    payer_note: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_session),
):
    """
    Customer uploads proof of payment (transfer receipt screenshot, PDF
    bank receipt, etc.) tied to their public order. Stored on disk and
    referenced in the SalesOrder.notes for sales-staff review.
    """
    result = await session.execute(
        select(SalesOrder).where(SalesOrder.order_number == order_number)
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    content_type = (file.content_type or "").lower()
    is_allowed_mime = (
        any(content_type.startswith(p) for p in ALLOWED_EVIDENCE_MIME_PREFIXES)
        or content_type in ALLOWED_EVIDENCE_MIME_EXACT
    )
    orig_name = (file.filename or "evidence").strip()
    ext = os.path.splitext(orig_name)[1].lower()
    if ext not in ALLOWED_EVIDENCE_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported file type. Please upload an image "
                "(JPG, PNG, etc.) or a PDF."
            ),
        )
    if not is_allowed_mime and ext not in ALLOWED_EVIDENCE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported file type")

    target_dir = _ensure_evidence_dir()
    safe_token = secrets.token_hex(6)
    safe_name = f"{order_number}_{safe_token}{ext}"
    target_path = target_dir / safe_name

    # Stream copy with size cap
    bytes_written = 0
    try:
        with target_path.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > MAX_EVIDENCE_BYTES:
                    out.close()
                    try:
                        target_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    raise HTTPException(
                        status_code=413,
                        detail="File too large (max 10 MB).",
                    )
                out.write(chunk)
    except HTTPException:
        raise
    except Exception:
        try:
            target_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="Failed to save file")

    # Append a marker to notes so sales staff can spot it
    marker_lines = [
        f"[PAYMENT-EVIDENCE uploaded {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}]",
        f"file={safe_name}",
    ]
    if payer_note:
        marker_lines.append(f"payer_note={payer_note.strip()[:500]}")
    marker = " | ".join(marker_lines)
    order.notes = (order.notes or "") + ("\n" if order.notes else "") + marker
    await session.commit()

    return {
        "success": True,
        "order_number": order_number,
        "file_name": safe_name,
        "size_bytes": bytes_written,
        "message": (
            "Payment evidence received. Our finance team will reconcile "
            "and confirm shortly."
        ),
    }


# ---------- Staff-facing list of customer (web) orders ----------
@router.get("/admin/orders")
async def list_customer_orders(
    status: Optional[str] = None,
    limit: int = 200,
    session: AsyncSession = Depends(get_session),
    _user=Depends(require_authenticated_user)):
    """
    Returns sales orders that originated from the public order page
    (order_number starts with 'WEB-'), enriched with customer phone +
    a parsed list of payment-evidence file names from the notes.

    Used by the staff "Customer Orders" sidebar page so they can call
    the customer to confirm and then re-create the order properly.
    """
    limit = max(1, min(int(limit or 200), 1000))
    query = (
        select(SalesOrder, Customer)
        .join(Customer, Customer.id == SalesOrder.customer_id, isouter=True)
        .where(SalesOrder.order_number.like("WEB-%"))
        .order_by(SalesOrder.order_date.desc())
        .limit(limit)
    )
    if status:
        query = query.where(SalesOrder.status == status)
    rows = (await session.execute(query)).all()

    out = []
    for order, customer in rows:
        notes = order.notes or ""
        # Parse customer_type marker [RETAIL] / [WHOLESALE]
        ctype = "retail"
        m_ctype = re.search(r"\[(RETAIL|WHOLESALE)\]", notes, re.IGNORECASE)
        if m_ctype:
            ctype = m_ctype.group(1).lower()
        # Parse payment-evidence markers
        evidence_files = re.findall(r"file=([^\s|]+)", notes)
        # Count lines (best-effort)
        try:
            lc_res = await session.execute(
                select(func.count(SalesOrderLine.id)).where(
                    SalesOrderLine.sales_order_id == order.id
                )
            )
            line_count = int(lc_res.scalar() or 0)
        except Exception:
            line_count = 0

        out.append({
            "id": str(order.id),
            "order_number": order.order_number,
            "status": order.status,
            "payment_status": order.payment_status,
            "total_amount": float(order.total_amount or 0),
            "order_date": (
                order.order_date.isoformat() if order.order_date else None
            ),
            "customer_id": str(customer.id) if customer else None,
            "customer_name": customer.name if customer else None,
            "customer_code": customer.customer_code if customer else None,
            "phone": customer.phone if customer else None,
            "email": customer.email if customer else None,
            "address": customer.address if customer else None,
            "customer_type": ctype,
            "notes": notes,
            "evidence_files": evidence_files,
            "line_count": line_count,
        })
    return {"items": out, "count": len(out)}


@router.get("/admin/orders/{order_id}/lines")
async def get_customer_order_lines(
    order_id: str, session: AsyncSession = Depends(get_session)
, _user=Depends(require_authenticated_user)):
    """Detailed line items for a single customer (web) order."""
    try:
        oid = uuid.UUID(order_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid order_id")
    sql = text(
        """
        SELECT sol.id, sol.product_id, sol.unit, sol.quantity,
               sol.unit_price, sol.line_total,
               p.name AS product_name, p.sku AS product_sku
        FROM sales_order_lines sol
        LEFT JOIN products p ON p.id = sol.product_id
        WHERE sol.sales_order_id = :oid
        ORDER BY p.name
        """
    ).bindparams(oid=oid)
    rows = (await session.execute(sql)).mappings().all()
    items = [
        {
            "id": str(r["id"]),
            "product_id": str(r["product_id"]) if r["product_id"] else None,
            "product_name": r["product_name"],
            "product_sku": r["product_sku"],
            "unit": r["unit"],
            "quantity": float(r["quantity"] or 0),
            "unit_price": float(r["unit_price"] or 0),
            "line_total": float(r["line_total"] or 0),
        }
        for r in rows
    ]
    return {"items": items, "count": len(items)}


@router.get("/admin/orders/{order_number}/evidence/{file_name}")
async def download_customer_order_evidence(
    order_number: str, file_name: str
, _user=Depends(require_authenticated_user)):
    """Stream a payment-evidence file for staff review."""
    # Sanitize: only allow files matching the order_number prefix
    safe_name = os.path.basename(file_name)
    if not safe_name.startswith(order_number + "_"):
        raise HTTPException(status_code=400, detail="Invalid file name")
    target_dir = _ensure_evidence_dir()
    target = target_dir / safe_name
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Evidence file not found")
    return FileResponse(str(target), filename=safe_name)

