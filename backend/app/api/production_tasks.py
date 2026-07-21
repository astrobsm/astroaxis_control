"""
Production Tasks Module
- Admin / Production Manager assigns tasks (from a fixed catalog) to staff for a
  given product, optional batch number, scheduled date and start time.
- Staff log completion (with quantity processed) using their 4-digit clock PIN.
- Production Manager (admin / production_staff role) confirms or rejects each
  completed task.

Auto-creates the underlying tables on first request.
"""
from fastapi import APIRouter, Depends, HTTPException, Header, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Optional, List, Literal
from pydantic import BaseModel, Field
from datetime import datetime, date, time, timezone
from decimal import Decimal
import uuid

from app.db import get_session
from app.api.auth import decode_token

router = APIRouter(prefix="/api/production-tasks")


# ─── TASK CATALOG ────────────────────────────────────────────────────────────
# All products share this universal task catalog grouped by stage.
TASK_CATALOG = [
    {
        "group": "Pre-Production",
        "tasks": [
            "Pre-production cleaning of floor and tables",
            "Setting up the sterile stations",
        ],
    },
    {
        "group": "Gauze (Sterile)",
        "tasks": [
            "Gauze folding",
            "Pouching",
            "Pouch sealing",
            "Leakage checks",
            "Washing and drying",
            "First stage packaging",
            "Batch coding",
            "Final stage packaging",
        ],
    },
    {
        "group": "Tube Production",
        "tasks": [
            "Tube filling",
            "Tube sealing",
            "Tube trimming",
            "First stage packaging",
            "Gauze cutting",
        ],
    },
    {
        "group": "Gauze Cutting & Bagging",
        "tasks": [
            "Thick gauze cutting",
            "Gauze trimming and sorting",
            "Gauze counting and bagging",
        ],
    },
    {
        "group": "Bottle Production",
        "tasks": [
            "Bottle washing",
            "Bottle filling",
            "Bottle capping",
            "Sticker labelling and tamperproofing",
            "Shrink wrapping",
        ],
    },
    {
        "group": "Foam & Gamgee",
        "tasks": [
            "Foam cutting",
            "Foam pouch cutting",
            "Foam packaging",
            "Gamgee folding",
            "Gamgee final sealing",
            "Sterile gauze and cotton wool cutting",
        ],
    },
    {
        "group": "Post-Production",
        "tasks": [
            "Post-production cleaning",
        ],
    },
]

# Flat allowlist used for validation
ALL_TASK_NAMES = {t for g in TASK_CATALOG for t in g["tasks"]}
TASK_TO_GROUP = {t: g["group"] for g in TASK_CATALOG for t in g["tasks"]}

MANAGER_ROLES = {"admin", "production_staff"}


# ─── SCHEMA ──────────────────────────────────────────────────────────────────
CREATE_ASSIGNMENTS_SQL = """
CREATE TABLE IF NOT EXISTS production_task_assignments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    assignment_number VARCHAR(64) UNIQUE NOT NULL,
    product_id UUID NOT NULL REFERENCES products(id),
    batch_number VARCHAR(64),
    scheduled_date DATE NOT NULL,
    scheduled_start_time TIME,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    notes TEXT,
    created_by UUID REFERENCES users(id),
    created_by_name VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
)
"""

CREATE_ITEMS_SQL = """
CREATE TABLE IF NOT EXISTS production_task_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    assignment_id UUID NOT NULL REFERENCES production_task_assignments(id) ON DELETE CASCADE,
    task_name VARCHAR(255) NOT NULL,
    task_group VARCHAR(64),
    assigned_staff_id UUID NOT NULL REFERENCES staff(id),
    planned_qty NUMERIC(18,6),
    completed_qty NUMERIC(18,6) DEFAULT 0,
    unit VARCHAR(32),
    status VARCHAR(32) NOT NULL DEFAULT 'assigned',
    staff_notes TEXT,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    confirmed_by_user_id UUID REFERENCES users(id),
    confirmed_by_name VARCHAR(255),
    confirmed_at TIMESTAMP WITH TIME ZONE,
    confirmation_notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
)
"""

CREATE_INDEX_SQLS = [
    "CREATE INDEX IF NOT EXISTS idx_pta_status   ON production_task_assignments(status)",
    "CREATE INDEX IF NOT EXISTS idx_pta_date     ON production_task_assignments(scheduled_date)",
    "CREATE INDEX IF NOT EXISTS idx_pta_product  ON production_task_assignments(product_id)",
    "CREATE INDEX IF NOT EXISTS idx_pti_assign   ON production_task_items(assignment_id)",
    "CREATE INDEX IF NOT EXISTS idx_pti_staff    ON production_task_items(assigned_staff_id)",
    "CREATE INDEX IF NOT EXISTS idx_pti_status   ON production_task_items(status)",
]


async def _ensure_tables(session: AsyncSession):
    await session.execute(text(CREATE_ASSIGNMENTS_SQL))
    await session.execute(text(CREATE_ITEMS_SQL))
    for s in CREATE_INDEX_SQLS:
        await session.execute(text(s))
    await session.commit()


# ─── AUTH HELPERS ────────────────────────────────────────────────────────────
async def _user_from_header(authorization: Optional[str], session: AsyncSession):
    """Decode Bearer token → (user_id, role, full_name) or (None,None,None)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None, None, None
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_token(token)
        uid = payload.get("sub")
        role = payload.get("role")
        # full_name is not in the token; fetch lazily only if needed
        return uid, role, payload.get("email")
    except Exception:
        return None, None, None


async def _require_manager(authorization: Optional[str], session: AsyncSession):
    uid, role, label = await _user_from_header(authorization, session)
    if not uid or role not in MANAGER_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Only an admin or production manager can perform this action.",
        )
    # Fetch user full_name
    full_name = label or ""
    try:
        row = (
            await session.execute(
                text("SELECT full_name FROM users WHERE id = CAST(:uid AS uuid)").bindparams(uid=uid)
            )
        ).first()
        if row and row[0]:
            full_name = row[0]
    except Exception:
        pass
    return uid, role, full_name


async def _staff_from_pin(pin: str, session: AsyncSession):
    if not pin or not pin.strip():
        raise HTTPException(status_code=400, detail="Clock PIN is required.")
    row = (
        await session.execute(
            text(
                "SELECT id, first_name, last_name, is_active "
                "FROM staff WHERE clock_pin = :pin"
            ).bindparams(pin=pin.strip())
        )
    ).first()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid clock PIN.")
    if row[3] is False:
        raise HTTPException(status_code=403, detail="This staff account is inactive.")
    return str(row[0]), f"{row[1]} {row[2]}".strip()


# ─── PYDANTIC SCHEMAS ────────────────────────────────────────────────────────
class TaskItemCreate(BaseModel):
    task_name: str
    # Either a single staff id (legacy) or a list of staff ids (new).
    assigned_staff_id: Optional[str] = None
    assigned_staff_ids: Optional[List[str]] = None
    planned_qty: Optional[float] = None
    unit: Optional[str] = None

    def staff_list(self) -> List[str]:
        ids: List[str] = []
        if self.assigned_staff_ids:
            ids.extend(self.assigned_staff_ids)
        if self.assigned_staff_id:
            ids.append(self.assigned_staff_id)
        # de-duplicate while preserving order
        seen = set(); out = []
        for x in ids:
            if x and x not in seen:
                seen.add(x); out.append(x)
        return out


class AssignmentCreate(BaseModel):
    product_id: str
    batch_number: Optional[str] = None
    scheduled_date: date
    scheduled_start_time: Optional[time] = None
    notes: Optional[str] = None
    items: List[TaskItemCreate] = Field(..., min_items=1)


class ItemStartRequest(BaseModel):
    clock_pin: str


class ItemCompleteRequest(BaseModel):
    clock_pin: str
    completed_qty: float = 0
    notes: Optional[str] = None


class ItemConfirmRequest(BaseModel):
    notes: Optional[str] = None


class ItemRejectRequest(BaseModel):
    reason: str


# ─── CATALOG / DROPDOWNS ─────────────────────────────────────────────────────
@router.get("/catalog")
async def get_catalog():
    """Return the universal task catalog (no auth)."""
    return TASK_CATALOG


@router.get("/staff")
async def list_assignable_staff(session: AsyncSession = Depends(get_session)):
    """Active staff dropdown for assigning tasks."""
    rows = (
        await session.execute(
            text(
                "SELECT id, employee_id, first_name, last_name, position "
                "FROM staff WHERE COALESCE(is_active, true) = true "
                "ORDER BY first_name, last_name"
            )
        )
    ).mappings().all()
    return [
        {
            "id": str(r["id"]),
            "employee_id": r["employee_id"],
            "name": f'{r["first_name"]} {r["last_name"]}'.strip(),
            "position": r["position"],
        }
        for r in rows
    ]


@router.get("/products")
async def list_products_for_tasks(session: AsyncSession = Depends(get_session)):
    """Lightweight product dropdown."""
    rows = (
        await session.execute(
            text(
                "SELECT id, sku, name, unit FROM products ORDER BY name"
            )
        )
    ).mappings().all()
    return [
        {
            "id": str(r["id"]),
            "sku": r["sku"],
            "name": r["name"],
            "unit": r["unit"],
        }
        for r in rows
    ]


# ─── ASSIGNMENT CRUD ─────────────────────────────────────────────────────────
@router.post("/assignments")
async def create_assignment(
    payload: AssignmentCreate,
    authorization: Optional[str] = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    await _ensure_tables(session)
    uid, role, full_name = await _require_manager(authorization, session)

    # Validate product
    prod = (
        await session.execute(
            text("SELECT id, name FROM products WHERE id = CAST(:pid AS uuid)").bindparams(
                pid=payload.product_id
            )
        )
    ).first()
    if not prod:
        raise HTTPException(status_code=404, detail="Product not found.")

    # Validate task names + staff IDs up front
    if not payload.items:
        raise HTTPException(status_code=400, detail="Add at least one task item.")
    for it in payload.items:
        if it.task_name not in ALL_TASK_NAMES:
            raise HTTPException(
                status_code=400, detail=f"Unknown task: '{it.task_name}'"
            )
        if not it.staff_list():
            raise HTTPException(
                status_code=400,
                detail=f"Assign at least one staff member to '{it.task_name}'.",
            )

    staff_ids = list({sid for it in payload.items for sid in it.staff_list()})
    valid_staff = (
        await session.execute(
            text(
                "SELECT id FROM staff WHERE id = ANY(CAST(:ids AS uuid[])) "
                "AND COALESCE(is_active, true) = true"
            ).bindparams(ids=staff_ids)
        )
    ).all()
    if len(valid_staff) != len(staff_ids):
        raise HTTPException(status_code=400, detail="One or more staff are invalid or inactive.")

    assignment_number = (
        f"PTA-{datetime.now(timezone.utc).strftime('%Y%m%d')}-"
        f"{uuid.uuid4().hex[:6].upper()}"
    )
    aid = str(uuid.uuid4())
    await session.execute(
        text(
            """
            INSERT INTO production_task_assignments
                (id, assignment_number, product_id, batch_number, scheduled_date,
                 scheduled_start_time, notes, created_by, created_by_name)
            VALUES (CAST(:id AS uuid), :num, CAST(:pid AS uuid), :batch, :sdate, :stime, :notes,
                    CAST(:uid AS uuid), :uname)
            """
        ).bindparams(
            id=aid,
            num=assignment_number,
            pid=payload.product_id,
            batch=payload.batch_number,
            sdate=payload.scheduled_date,
            stime=payload.scheduled_start_time,
            notes=payload.notes,
            uid=uid,
            uname=full_name,
        )
    )

    for it in payload.items:
        for sid in it.staff_list():
            await session.execute(
                text(
                    """
                    INSERT INTO production_task_items
                        (id, assignment_id, task_name, task_group, assigned_staff_id,
                         planned_qty, unit)
                    VALUES (gen_random_uuid(), CAST(:aid AS uuid), :tname, :tgroup,
                            CAST(:sid AS uuid), :pqty, :unit)
                    """
                ).bindparams(
                    aid=aid,
                    tname=it.task_name,
                    tgroup=TASK_TO_GROUP.get(it.task_name),
                    sid=sid,
                    pqty=it.planned_qty,
                    unit=it.unit,
                )
            )
    await session.commit()
    return await get_assignment(aid, session)


@router.get("/assignments")
async def list_assignments(
    status: Optional[str] = None,
    staff_id: Optional[str] = None,
    product_id: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    session: AsyncSession = Depends(get_session),
):
    await _ensure_tables(session)
    where = ["1=1"]
    params = {}
    if status:
        where.append("a.status = :st")
        params["st"] = status
    if product_id:
        where.append("a.product_id = CAST(:pid AS uuid)")
        params["pid"] = product_id
    if date_from:
        where.append("a.scheduled_date >= :df")
        params["df"] = date_from
    if date_to:
        where.append("a.scheduled_date <= :dt")
        params["dt"] = date_to
    if staff_id:
        where.append(
            "EXISTS (SELECT 1 FROM production_task_items i "
            "WHERE i.assignment_id = a.id AND i.assigned_staff_id = CAST(:sid AS uuid))"
        )
        params["sid"] = staff_id

    sql = text(
        f"""
        SELECT a.id, a.assignment_number, a.product_id, p.name AS product_name,
               p.sku, a.batch_number, a.scheduled_date, a.scheduled_start_time,
               a.status, a.notes, a.created_by_name, a.created_at,
               COUNT(i.id) AS items_total,
               SUM(CASE WHEN i.status = 'confirmed' THEN 1 ELSE 0 END) AS items_confirmed,
               SUM(CASE WHEN i.status = 'completed' THEN 1 ELSE 0 END) AS items_pending_confirm,
               SUM(CASE WHEN i.status IN ('assigned','in_progress') THEN 1 ELSE 0 END) AS items_open
        FROM production_task_assignments a
        JOIN products p ON p.id = a.product_id
        LEFT JOIN production_task_items i ON i.assignment_id = a.id
        WHERE {' AND '.join(where)}
        GROUP BY a.id, p.name, p.sku
        ORDER BY a.scheduled_date DESC, a.created_at DESC
        LIMIT 500
        """
    ).bindparams(**params)
    rows = (await session.execute(sql)).mappings().all()
    return [
        {
            "id": str(r["id"]),
            "assignment_number": r["assignment_number"],
            "product_id": str(r["product_id"]),
            "product_name": r["product_name"],
            "product_sku": r["sku"],
            "batch_number": r["batch_number"],
            "scheduled_date": r["scheduled_date"].isoformat() if r["scheduled_date"] else None,
            "scheduled_start_time": (
                r["scheduled_start_time"].isoformat() if r["scheduled_start_time"] else None
            ),
            "status": r["status"],
            "notes": r["notes"],
            "created_by_name": r["created_by_name"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "items_total": int(r["items_total"] or 0),
            "items_confirmed": int(r["items_confirmed"] or 0),
            "items_pending_confirm": int(r["items_pending_confirm"] or 0),
            "items_open": int(r["items_open"] or 0),
        }
        for r in rows
    ]


@router.get("/assignments/{assignment_id}")
async def get_assignment(
    assignment_id: str,
    session: AsyncSession = Depends(get_session),
):
    await _ensure_tables(session)
    head = (
        await session.execute(
            text(
                """
                SELECT a.id, a.assignment_number, a.product_id, p.name AS product_name,
                       p.sku, a.batch_number, a.scheduled_date, a.scheduled_start_time,
                       a.status, a.notes, a.created_by_name, a.created_at
                FROM production_task_assignments a
                JOIN products p ON p.id = a.product_id
                WHERE a.id = CAST(:aid AS uuid)
                """
            ).bindparams(aid=assignment_id)
        )
    ).mappings().first()
    if not head:
        raise HTTPException(status_code=404, detail="Assignment not found.")

    items = (
        await session.execute(
            text(
                """
                SELECT i.id, i.task_name, i.task_group, i.assigned_staff_id,
                       s.first_name, s.last_name, s.employee_id,
                       i.planned_qty, i.completed_qty, i.unit, i.status,
                       i.staff_notes, i.started_at, i.completed_at,
                       i.confirmed_by_name, i.confirmed_at, i.confirmation_notes
                FROM production_task_items i
                JOIN staff s ON s.id = i.assigned_staff_id
                WHERE i.assignment_id = CAST(:aid AS uuid)
                ORDER BY i.task_group, i.task_name
                """
            ).bindparams(aid=assignment_id)
        )
    ).mappings().all()

    def _f(v):
        return float(v) if v is not None else None

    return {
        "id": str(head["id"]),
        "assignment_number": head["assignment_number"],
        "product_id": str(head["product_id"]),
        "product_name": head["product_name"],
        "product_sku": head["sku"],
        "batch_number": head["batch_number"],
        "scheduled_date": head["scheduled_date"].isoformat() if head["scheduled_date"] else None,
        "scheduled_start_time": (
            head["scheduled_start_time"].isoformat() if head["scheduled_start_time"] else None
        ),
        "status": head["status"],
        "notes": head["notes"],
        "created_by_name": head["created_by_name"],
        "created_at": head["created_at"].isoformat() if head["created_at"] else None,
        "items": [
            {
                "id": str(it["id"]),
                "task_name": it["task_name"],
                "task_group": it["task_group"],
                "assigned_staff_id": str(it["assigned_staff_id"]),
                "assigned_staff_name": f'{it["first_name"]} {it["last_name"]}'.strip(),
                "assigned_staff_employee_id": it["employee_id"],
                "planned_qty": _f(it["planned_qty"]),
                "completed_qty": _f(it["completed_qty"]),
                "unit": it["unit"],
                "status": it["status"],
                "staff_notes": it["staff_notes"],
                "started_at": it["started_at"].isoformat() if it["started_at"] else None,
                "completed_at": it["completed_at"].isoformat() if it["completed_at"] else None,
                "confirmed_by_name": it["confirmed_by_name"],
                "confirmed_at": it["confirmed_at"].isoformat() if it["confirmed_at"] else None,
                "confirmation_notes": it["confirmation_notes"],
            }
            for it in items
        ],
    }


@router.get("/assignments/{assignment_id}/pdf")
async def assignment_pdf(
    assignment_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Render an assignment + its task items as a printable PDF."""
    from io import BytesIO
    from fastapi.responses import StreamingResponse
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
    )

    await _ensure_tables(session)
    a = (
        await session.execute(
            text(
                """
                SELECT a.id, a.assignment_number, a.batch_number, a.scheduled_date,
                       a.scheduled_start_time, a.status, a.notes, a.created_by_name,
                       a.created_at, p.name AS product_name, p.sku AS product_sku
                FROM production_task_assignments a
                JOIN products p ON p.id = a.product_id
                WHERE a.id = CAST(:aid AS uuid)
                """
            ).bindparams(aid=assignment_id)
        )
    ).mappings().first()
    if not a:
        raise HTTPException(status_code=404, detail="Assignment not found.")

    items = (
        await session.execute(
            text(
                """
                SELECT i.task_group, i.task_name, i.planned_qty, i.completed_qty,
                       i.unit, i.status, i.staff_notes, i.started_at, i.completed_at,
                       i.confirmed_by_name, i.confirmed_at, i.confirmation_notes,
                       s.first_name, s.last_name, s.employee_id
                FROM production_task_items i
                LEFT JOIN staff s ON s.id = i.assigned_staff_id
                WHERE i.assignment_id = CAST(:aid AS uuid)
                ORDER BY i.task_group, i.created_at
                """
            ),
            {"aid": assignment_id},
        )
    ).mappings().all()

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=14 * mm, rightMargin=14 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm,
        title=f"Production Assignment {a['assignment_number']}",
    )
    styles = getSampleStyleSheet()
    h_co = ParagraphStyle("co", parent=styles["Title"], fontSize=15,
                          alignment=TA_CENTER, textColor=colors.HexColor("#0a3d62"),
                          spaceAfter=2)
    h_sub = ParagraphStyle("sub", parent=styles["Normal"], fontSize=9,
                           alignment=TA_CENTER, textColor=colors.HexColor("#6b7280"),
                           spaceAfter=10)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=12,
                        textColor=colors.HexColor("#0a3d62"), spaceAfter=4)
    body = ParagraphStyle("b", parent=styles["Normal"], fontSize=9, leading=12)
    small = ParagraphStyle("s", parent=styles["Normal"], fontSize=8, leading=10,
                           textColor=colors.HexColor("#444"))

    story = []
    story.append(Paragraph("BONNESANTE MEDICALS", h_co))
    story.append(Paragraph("Production Task Assignment", h_sub))

    sched_d = a["scheduled_date"].strftime("%d %b %Y") if a["scheduled_date"] else "—"
    sched_t = a["scheduled_start_time"].strftime("%H:%M") if a["scheduled_start_time"] else ""
    created = a["created_at"].strftime("%d %b %Y %H:%M") if a["created_at"] else "—"

    header_tbl = Table(
        [
            ["Assignment #", a["assignment_number"], "Status", a["status"].replace("_", " ").upper()],
            ["Product",      f"{a['product_name']} ({a['product_sku'] or ''})",
             "Batch",        a["batch_number"] or "—"],
            ["Scheduled",    f"{sched_d} {sched_t}".strip(),
             "Created",      f"{created}"],
            ["Created by",   a["created_by_name"] or "—",
             "Notes",        (a["notes"] or "—")[:120]],
        ],
        colWidths=[28 * mm, 65 * mm, 22 * mm, 65 * mm],
    )
    header_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef2f7")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#eef2f7")),
        ("FONTNAME",   (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME",   (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE",   (0, 0), (-1, -1), 9),
        ("VALIGN",     (0, 0), (-1, -1), "TOP"),
        ("BOX",        (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("INNERGRID",  (0, 0), (-1, -1), 0.25, colors.HexColor("#e5e7eb")),
        ("LEFTPADDING",(0, 0), (-1, -1), 5),
        ("RIGHTPADDING",(0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
    ]))
    story.append(header_tbl)
    story.append(Spacer(1, 8))

    story.append(Paragraph(f"Tasks ({len(items)})", h2))

    rows = [["#", "Group / Task", "Assigned To", "Planned", "Done", "Unit", "Status"]]
    for idx, it in enumerate(items, 1):
        staff_label = (
            f"{(it['first_name'] or '').strip()} {(it['last_name'] or '').strip()}".strip()
            or "—"
        )
        if it["employee_id"]:
            staff_label = f"{staff_label}\n{it['employee_id']}"
        task_cell = Paragraph(
            f"<b>{(it['task_name'] or '')}</b><br/><font size=7 color='#6b7280'>{it['task_group'] or ''}</font>",
            body,
        )
        rows.append([
            str(idx),
            task_cell,
            staff_label,
            "" if it["planned_qty"] is None else f"{it['planned_qty']}",
            "" if it["completed_qty"] is None else f"{it['completed_qty']}",
            it["unit"] or "",
            (it["status"] or "").replace("_", " ").upper(),
        ])

    items_tbl = Table(
        rows,
        colWidths=[10 * mm, 75 * mm, 32 * mm, 18 * mm, 16 * mm, 14 * mm, 25 * mm],
        repeatRows=1,
    )
    items_tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0), colors.HexColor("#0a3d62")),
        ("TEXTCOLOR",    (0, 0), (-1, 0), colors.white),
        ("FONTNAME",     (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, -1), 8.5),
        ("ALIGN",        (0, 0), (0, -1), "CENTER"),
        ("ALIGN",        (3, 0), (5, -1), "CENTER"),
        ("ALIGN",        (6, 0), (6, -1), "CENTER"),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1),
                          [colors.white, colors.HexColor("#f7faff")]),
        ("BOX",          (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("INNERGRID",    (0, 0), (-1, -1), 0.25, colors.HexColor("#e5e7eb")),
        ("LEFTPADDING",  (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING",   (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
    ]))
    story.append(items_tbl)
    story.append(Spacer(1, 14))

    # Sign-off block
    story.append(Paragraph("Sign-off", h2))
    sign_tbl = Table(
        [
            ["Assigned by", "_______________________________",
             "Date", "_____________________"],
            ["Production Manager", "_______________________________",
             "Date", "_____________________"],
            ["QA / Admin", "_______________________________",
             "Date", "_____________________"],
        ],
        colWidths=[35 * mm, 70 * mm, 18 * mm, 50 * mm],
    )
    sign_tbl.setStyle(TableStyle([
        ("FONTSIZE",     (0, 0), (-1, -1), 9),
        ("FONTNAME",     (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME",     (2, 0), (2, -1), "Helvetica-Bold"),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 14),
        ("TOPPADDING",   (0, 0), (-1, -1), 6),
    ]))
    story.append(sign_tbl)

    story.append(Spacer(1, 10))
    story.append(Paragraph(
        f"Generated {datetime.now().strftime('%d %b %Y %H:%M')} · "
        f"BONNESANTE MEDICALS — Production Tasks Tracker",
        small,
    ))

    doc.build(story)
    buf.seek(0)
    fname = f"production-assignment-{a['assignment_number']}.pdf"
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.delete("/assignments/{assignment_id}")
async def delete_assignment(
    assignment_id: str,
    authorization: Optional[str] = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    await _ensure_tables(session)
    uid, role, _ = await _require_manager(authorization, session)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Only admin can delete an assignment.")
    res = await session.execute(
        text("DELETE FROM production_task_assignments WHERE id = CAST(:aid AS uuid)").bindparams(
            aid=assignment_id
        )
    )
    await session.commit()
    if res.rowcount == 0:
        raise HTTPException(status_code=404, detail="Assignment not found.")
    return {"success": True}


# ─── STAFF: SELF-SERVICE ─────────────────────────────────────────────────────
@router.get("/my-tasks")
async def my_tasks(
    clock_pin: str,
    include_done: bool = False,
    session: AsyncSession = Depends(get_session),
):
    """Staff lookup of their own assigned tasks via clock PIN."""
    await _ensure_tables(session)
    sid, sname = await _staff_from_pin(clock_pin, session)
    statuses = ("assigned", "in_progress") if not include_done else (
        "assigned", "in_progress", "completed", "confirmed", "rejected"
    )
    rows = (
        await session.execute(
            text(
                """
                SELECT i.id, i.task_name, i.task_group, i.planned_qty, i.completed_qty,
                       i.unit, i.status, i.staff_notes, i.started_at, i.completed_at,
                       a.id AS assignment_id, a.assignment_number, a.batch_number,
                       a.scheduled_date, a.scheduled_start_time,
                       p.name AS product_name, p.sku
                FROM production_task_items i
                JOIN production_task_assignments a ON a.id = i.assignment_id
                JOIN products p ON p.id = a.product_id
                WHERE i.assigned_staff_id = CAST(:sid AS uuid)
                  AND i.status = ANY(:sts)
                ORDER BY a.scheduled_date DESC, a.scheduled_start_time NULLS LAST
                LIMIT 200
                """
            ).bindparams(sid=sid, sts=list(statuses))
        )
    ).mappings().all()

    def _f(v):
        return float(v) if v is not None else None

    return {
        "staff_id": sid,
        "staff_name": sname,
        "items": [
            {
                "id": str(r["id"]),
                "assignment_id": str(r["assignment_id"]),
                "assignment_number": r["assignment_number"],
                "batch_number": r["batch_number"],
                "product_name": r["product_name"],
                "product_sku": r["sku"],
                "scheduled_date": r["scheduled_date"].isoformat() if r["scheduled_date"] else None,
                "scheduled_start_time": (
                    r["scheduled_start_time"].isoformat() if r["scheduled_start_time"] else None
                ),
                "task_name": r["task_name"],
                "task_group": r["task_group"],
                "planned_qty": _f(r["planned_qty"]),
                "completed_qty": _f(r["completed_qty"]),
                "unit": r["unit"],
                "status": r["status"],
                "staff_notes": r["staff_notes"],
                "started_at": r["started_at"].isoformat() if r["started_at"] else None,
                "completed_at": r["completed_at"].isoformat() if r["completed_at"] else None,
            }
            for r in rows
        ],
    }


async def _load_item(item_id: str, session: AsyncSession):
    row = (
        await session.execute(
            text(
                "SELECT id, assignment_id, assigned_staff_id, status "
                "FROM production_task_items WHERE id = CAST(:iid AS uuid)"
            ).bindparams(iid=item_id)
        )
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Task item not found.")
    return row


@router.post("/items/{item_id}/start")
async def start_item(
    item_id: str,
    payload: ItemStartRequest,
    session: AsyncSession = Depends(get_session),
):
    await _ensure_tables(session)
    item = await _load_item(item_id, session)
    sid, _ = await _staff_from_pin(payload.clock_pin, session)
    if str(item[2]) != sid:
        raise HTTPException(status_code=403, detail="This task is assigned to a different staff member.")
    if item[3] not in ("assigned", "in_progress"):
        raise HTTPException(status_code=400, detail=f"Cannot start a task with status '{item[3]}'.")

    await session.execute(
        text(
            "UPDATE production_task_items SET status='in_progress', "
            "started_at = COALESCE(started_at, NOW()) WHERE id = CAST(:iid AS uuid)"
        ).bindparams(iid=item_id)
    )
    await session.execute(
        text(
            "UPDATE production_task_assignments SET status='in_progress', updated_at=NOW() "
            "WHERE id = CAST(:aid AS uuid) AND status = 'pending'"
        ).bindparams(aid=str(item[1]))
    )
    await session.commit()
    return {"success": True}


@router.post("/items/{item_id}/complete")
async def complete_item(
    item_id: str,
    payload: ItemCompleteRequest,
    session: AsyncSession = Depends(get_session),
):
    await _ensure_tables(session)
    item = await _load_item(item_id, session)
    sid, _ = await _staff_from_pin(payload.clock_pin, session)
    if str(item[2]) != sid:
        raise HTTPException(status_code=403, detail="This task is assigned to a different staff member.")
    if item[3] not in ("assigned", "in_progress", "completed"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot log completion on a task with status '{item[3]}'.",
        )
    if payload.completed_qty is None or payload.completed_qty < 0:
        raise HTTPException(status_code=400, detail="Completed quantity must be 0 or greater.")

    await session.execute(
        text(
            """
            UPDATE production_task_items
            SET status='completed',
                completed_qty = :qty,
                staff_notes   = COALESCE(:notes, staff_notes),
                started_at    = COALESCE(started_at, NOW()),
                completed_at  = NOW()
            WHERE id = CAST(:iid AS uuid)
            """
        ).bindparams(qty=payload.completed_qty, notes=payload.notes, iid=item_id)
    )
    # Roll up assignment status if every item is done
    rollup = (
        await session.execute(
            text(
                "SELECT COUNT(*) FILTER (WHERE status NOT IN ('completed','confirmed','rejected')) "
                "FROM production_task_items WHERE assignment_id = CAST(:aid AS uuid)"
            ).bindparams(aid=str(item[1]))
        )
    ).scalar()
    if (rollup or 0) == 0:
        await session.execute(
            text(
                "UPDATE production_task_assignments SET status='completed', updated_at=NOW() "
                "WHERE id = CAST(:aid AS uuid) AND status NOT IN ('confirmed','cancelled')"
            ).bindparams(aid=str(item[1]))
        )
    await session.commit()
    return {"success": True}


# ─── MANAGER: CONFIRM / REJECT ───────────────────────────────────────────────
@router.post("/items/{item_id}/confirm")
async def confirm_item(
    item_id: str,
    payload: ItemConfirmRequest = Body(default=None),
    authorization: Optional[str] = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    await _ensure_tables(session)
    uid, role, full_name = await _require_manager(authorization, session)
    item = await _load_item(item_id, session)
    if item[3] != "completed":
        raise HTTPException(
            status_code=400,
            detail="Only tasks with status 'completed' can be confirmed.",
        )
    notes = payload.notes if payload else None
    await session.execute(
        text(
            """
            UPDATE production_task_items
            SET status='confirmed',
                confirmed_by_user_id = CAST(:uid AS uuid),
                confirmed_by_name    = :uname,
                confirmation_notes   = :notes,
                confirmed_at         = NOW()
            WHERE id = CAST(:iid AS uuid)
            """
        ).bindparams(uid=uid, uname=full_name, notes=notes, iid=item_id)
    )
    rollup = (
        await session.execute(
            text(
                "SELECT COUNT(*) FILTER (WHERE status <> 'confirmed') "
                "FROM production_task_items WHERE assignment_id = CAST(:aid AS uuid)"
            ).bindparams(aid=str(item[1]))
        )
    ).scalar()
    if (rollup or 0) == 0:
        await session.execute(
            text(
                "UPDATE production_task_assignments SET status='confirmed', updated_at=NOW() "
                "WHERE id = CAST(:aid AS uuid)"
            ).bindparams(aid=str(item[1]))
        )
    await session.commit()
    return {"success": True}


@router.post("/items/{item_id}/reject")
async def reject_item(
    item_id: str,
    payload: ItemRejectRequest,
    authorization: Optional[str] = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    await _ensure_tables(session)
    uid, role, full_name = await _require_manager(authorization, session)
    item = await _load_item(item_id, session)
    if item[3] not in ("completed", "confirmed"):
        raise HTTPException(
            status_code=400,
            detail="Only tasks already completed or confirmed can be rejected.",
        )
    if not payload.reason or not payload.reason.strip():
        raise HTTPException(status_code=400, detail="A rejection reason is required.")
    await session.execute(
        text(
            """
            UPDATE production_task_items
            SET status='in_progress',
                completed_at = NULL,
                confirmed_by_user_id = NULL,
                confirmed_by_name    = NULL,
                confirmed_at         = NULL,
                confirmation_notes   = :notes
            WHERE id = CAST(:iid AS uuid)
            """
        ).bindparams(notes=f"REJECTED by {full_name}: {payload.reason.strip()}", iid=item_id)
    )
    await session.execute(
        text(
            "UPDATE production_task_assignments SET status='in_progress', updated_at=NOW() "
            "WHERE id = CAST(:aid AS uuid) AND status IN ('completed','confirmed')"
        ).bindparams(aid=str(item[1]))
    )
    await session.commit()
    return {"success": True}
