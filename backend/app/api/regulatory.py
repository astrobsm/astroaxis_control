"""Regulatory Compliance module — GMP / NAFDAC document & quality system.

Provides:
- Runtime DDL bootstrap for the regulatory tables (no Alembic needed).
- Document lifecycle (DRAFT → IN_REVIEW → APPROVED → EFFECTIVE → OBSOLETE).
- SOP/BMR/POLICY skeleton generation from the template library.
- E-signature ledger with SHA-256 content hashing (21 CFR Part 11 spirit).
- Deviation / CAPA tracker.
- Environmental monitoring log (temperature, RH, differential pressure …).
- GMP-styled PDF export with watermark, QR, controlled-copy stamp.
- Dashboard counters.

All tables are created at startup via `bootstrap_regulatory_schema`.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.services import sop_templates
from app.services.gmp_pdf import build_document_pdf

router = APIRouter(prefix="/api/regulatory", tags=["regulatory"])


# ─────────────────────────────────────────────────────────────────────────────
# DDL (idempotent)
# ─────────────────────────────────────────────────────────────────────────────
DDL_STATEMENTS: List[str] = [
    # Documents (SOPs, BMRs, policies, validation protocols …)
    """
    CREATE TABLE IF NOT EXISTS reg_documents (
        id              TEXT PRIMARY KEY,
        doc_number      TEXT UNIQUE NOT NULL,
        doc_type        TEXT NOT NULL,
        category        TEXT NOT NULL,
        title           TEXT NOT NULL,
        version         TEXT NOT NULL DEFAULT '1.0',
        status          TEXT NOT NULL DEFAULT 'DRAFT',
        content_json    JSONB NOT NULL DEFAULT '{}'::jsonb,
        author          TEXT,
        owner           TEXT,
        effective_date  DATE,
        review_date     DATE,
        obsoleted_at    TIMESTAMPTZ,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_reg_docs_status ON reg_documents(status)",
    "CREATE INDEX IF NOT EXISTS idx_reg_docs_category ON reg_documents(category)",
    "CREATE INDEX IF NOT EXISTS idx_reg_docs_type ON reg_documents(doc_type)",

    # E-signatures (immutable ledger)
    """
    CREATE TABLE IF NOT EXISTS reg_signatures (
        id            TEXT PRIMARY KEY,
        document_id   TEXT NOT NULL REFERENCES reg_documents(id) ON DELETE CASCADE,
        signer_name   TEXT NOT NULL,
        signer_role   TEXT NOT NULL,
        meaning       TEXT NOT NULL,
        content_hash  TEXT NOT NULL,
        signed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_reg_sigs_doc ON reg_signatures(document_id)",

    # Audit trail (every state change & edit)
    """
    CREATE TABLE IF NOT EXISTS reg_audit_trail (
        id            TEXT PRIMARY KEY,
        document_id   TEXT REFERENCES reg_documents(id) ON DELETE CASCADE,
        actor         TEXT,
        action        TEXT NOT NULL,
        from_state    TEXT,
        to_state      TEXT,
        details       JSONB DEFAULT '{}'::jsonb,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_reg_audit_doc ON reg_audit_trail(document_id)",

    # Deviations / CAPA
    """
    CREATE TABLE IF NOT EXISTS reg_deviations (
        id                TEXT PRIMARY KEY,
        ref_number        TEXT UNIQUE NOT NULL,
        title             TEXT NOT NULL,
        deviation_type    TEXT NOT NULL DEFAULT 'UNPLANNED',
        severity          TEXT NOT NULL DEFAULT 'MINOR',
        description       TEXT NOT NULL,
        root_cause        TEXT,
        corrective_action TEXT,
        preventive_action TEXT,
        owner             TEXT,
        status            TEXT NOT NULL DEFAULT 'OPEN',
        opened_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        due_date          DATE,
        closed_at         TIMESTAMPTZ
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_reg_dev_status ON reg_deviations(status)",

    # Environmental monitoring log
    """
    CREATE TABLE IF NOT EXISTS reg_env_logs (
        id           TEXT PRIMARY KEY,
        area         TEXT NOT NULL,
        param_type   TEXT NOT NULL,
        value        DOUBLE PRECISION NOT NULL,
        unit         TEXT,
        lower_limit  DOUBLE PRECISION,
        upper_limit  DOUBLE PRECISION,
        oos          BOOLEAN NOT NULL DEFAULT FALSE,
        recorded_by  TEXT,
        notes        TEXT,
        recorded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_reg_env_area_time ON reg_env_logs(area, recorded_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_reg_env_oos ON reg_env_logs(oos, recorded_at DESC)",
]


async def bootstrap_regulatory_schema(session: AsyncSession) -> None:
    """Run DDL once at startup. Per-statement try/rollback so a single bad
    grant doesn't poison the whole transaction."""
    for sql in DDL_STATEMENTS:
        try:
            await session.execute(text(sql))
        except Exception:
            await session.rollback()
            continue
    await session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
ALLOWED_STATUSES = {"DRAFT", "IN_REVIEW", "APPROVED", "EFFECTIVE", "OBSOLETE"}

LIFECYCLE = {
    "submit-review": ("DRAFT", "IN_REVIEW", "Submitted for review"),
    "approve":       ("IN_REVIEW", "APPROVED", "Approved by QA"),
    "effect":        ("APPROVED", "EFFECTIVE", "Effective release"),
    "obsolete":      (None, "OBSOLETE", "Marked obsolete"),
    "reject":        ("IN_REVIEW", "DRAFT", "Returned for revision"),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex


def _content_hash(content: Any) -> str:
    blob = json.dumps(content, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


async def _next_doc_number(session: AsyncSession, category: str, doc_type: str) -> str:
    cat = (category or "QA").upper()
    info = sop_templates.CATEGORIES.get(cat) or sop_templates.CATEGORIES["QA"]
    prefix = info["prefix"]
    if doc_type and doc_type.upper() != "SOP":
        prefix = f"{doc_type.upper()}-{cat}"
    row = await session.execute(text(
        "SELECT COUNT(*) FROM reg_documents WHERE doc_number LIKE :p"
    ), {"p": f"{prefix}-%"})
    n = int(row.scalar() or 0) + 1
    return f"{prefix}-{n:03d}"


async def _record_audit(
    session: AsyncSession, *, document_id: Optional[str], actor: str,
    action: str, from_state: Optional[str] = None,
    to_state: Optional[str] = None, details: Optional[Dict] = None,
) -> None:
    await session.execute(text(
        "INSERT INTO reg_audit_trail (id, document_id, actor, action, from_state, to_state, details) "
        "VALUES (:id, :doc, :actor, :action, :fs, :ts, CAST(:det AS JSONB))"
    ), {
        "id": _new_id(), "doc": document_id, "actor": actor or "system",
        "action": action, "fs": from_state, "ts": to_state,
        "det": json.dumps(details or {}),
    })


def _row_to_doc(row) -> Dict:
    m = row._mapping if hasattr(row, "_mapping") else row
    content = m["content_json"]
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except Exception:
            content = {}
    return {
        "id": m["id"],
        "doc_number": m["doc_number"],
        "doc_type": m["doc_type"],
        "category": m["category"],
        "title": m["title"],
        "version": m["version"],
        "status": m["status"],
        "content": content,
        "author": m["author"],
        "owner": m["owner"],
        "effective_date": m["effective_date"].isoformat() if m["effective_date"] else None,
        "review_date": m["review_date"].isoformat() if m["review_date"] else None,
        "created_at": m["created_at"].isoformat() if m["created_at"] else None,
        "updated_at": m["updated_at"].isoformat() if m["updated_at"] else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────
class DocumentCreate(BaseModel):
    title: str = Field(..., min_length=2, max_length=240)
    doc_type: str = Field("SOP", max_length=20)
    category: str = Field("QA", max_length=10)
    author: Optional[str] = None
    owner: Optional[str] = None
    content: Optional[Dict[str, Any]] = None


class DocumentUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[Dict[str, Any]] = None
    owner: Optional[str] = None
    author: Optional[str] = None


class SignRequest(BaseModel):
    signer_name: str
    signer_role: str
    meaning: str = "Approved"


class GenerateSOPRequest(BaseModel):
    template_key: str
    title: Optional[str] = None
    category: Optional[str] = None
    author: Optional[str] = None
    owner: Optional[str] = None


class DeviationCreate(BaseModel):
    title: str
    description: str
    deviation_type: str = "UNPLANNED"
    severity: str = "MINOR"
    owner: Optional[str] = None
    due_date: Optional[date] = None


class DeviationUpdate(BaseModel):
    root_cause: Optional[str] = None
    corrective_action: Optional[str] = None
    preventive_action: Optional[str] = None
    status: Optional[str] = None
    owner: Optional[str] = None
    due_date: Optional[date] = None


class EnvLogCreate(BaseModel):
    area: str
    param_type: str
    value: float
    unit: Optional[str] = None
    lower_limit: Optional[float] = None
    upper_limit: Optional[float] = None
    recorded_by: Optional[str] = None
    notes: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard / health
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)):
    row = (await session.execute(text(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='reg_documents'"
    ))).scalar() or 0
    return {"ok": int(row) >= 1, "ddl_ready": int(row) >= 1}


@router.get("/dashboard")
async def dashboard(session: AsyncSession = Depends(get_session)):
    counts: Dict[str, int] = {}
    for st in ALLOWED_STATUSES:
        c = (await session.execute(text(
            "SELECT COUNT(*) FROM reg_documents WHERE status=:s"
        ), {"s": st})).scalar() or 0
        counts[st] = int(c)
    total = sum(counts.values())

    open_dev = (await session.execute(text(
        "SELECT COUNT(*) FROM reg_deviations WHERE status NOT IN ('CLOSED','REJECTED')"
    ))).scalar() or 0
    overdue_dev = (await session.execute(text(
        "SELECT COUNT(*) FROM reg_deviations WHERE status NOT IN ('CLOSED','REJECTED') "
        "AND due_date IS NOT NULL AND due_date < CURRENT_DATE"
    ))).scalar() or 0

    reviews_due = (await session.execute(text(
        "SELECT COUNT(*) FROM reg_documents WHERE status='EFFECTIVE' "
        "AND review_date IS NOT NULL AND review_date <= CURRENT_DATE + INTERVAL '30 days'"
    ))).scalar() or 0

    env_oos = (await session.execute(text(
        "SELECT COUNT(*) FROM reg_env_logs WHERE oos=TRUE "
        "AND recorded_at >= now() - INTERVAL '7 days'"
    ))).scalar() or 0

    return {
        "documents": {**counts, "total": total},
        "deviations": {"open": int(open_dev), "overdue": int(overdue_dev)},
        "reviews_due_30d": int(reviews_due),
        "env_oos_7d": int(env_oos),
        "as_of": _now().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Templates
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/templates")
async def list_templates():
    return {
        "categories": [
            {"code": k, **v} for k, v in sop_templates.CATEGORIES.items()
        ],
        "doc_types": sop_templates.DOC_TYPES,
        "templates": sop_templates.list_templates(),
    }


@router.post("/sop/generate")
async def generate_sop_skeleton(req: GenerateSOPRequest):
    content = sop_templates.generate_sop(req.template_key)
    if req.title:
        content["title"] = req.title
    return {
        "template_key": req.template_key,
        "title": content.get("title"),
        "content": content,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Document CRUD
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/documents")
async def list_documents(
    status: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    doc_type: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
):
    where = []
    params: Dict[str, Any] = {"limit": limit}
    if status:
        where.append("status = :status")
        params["status"] = status
    if category:
        where.append("category = :category")
        params["category"] = category
    if doc_type:
        where.append("doc_type = :doc_type")
        params["doc_type"] = doc_type
    if q:
        where.append("(title ILIKE :q OR doc_number ILIKE :q)")
        params["q"] = f"%{q}%"
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = (
        "SELECT id, doc_number, doc_type, category, title, version, status, content_json, "
        "author, owner, effective_date, review_date, created_at, updated_at "
        f"FROM reg_documents {where_sql} ORDER BY updated_at DESC LIMIT :limit"
    )
    rows = (await session.execute(text(sql), params)).fetchall()
    return [_row_to_doc(r) for r in rows]


@router.post("/documents")
async def create_document(
    payload: DocumentCreate,
    actor: str = Query("system"),
    session: AsyncSession = Depends(get_session),
):
    doc_id = _new_id()
    cat = (payload.category or "QA").upper()
    dtype = (payload.doc_type or "SOP").upper()
    doc_number = await _next_doc_number(session, cat, dtype)
    content = payload.content or {"title": payload.title, "sections": []}
    await session.execute(text(
        "INSERT INTO reg_documents (id, doc_number, doc_type, category, title, version, "
        "status, content_json, author, owner) "
        "VALUES (:id, :dn, :dt, :cat, :title, '1.0', 'DRAFT', CAST(:content AS JSONB), :author, :owner)"
    ), {
        "id": doc_id, "dn": doc_number, "dt": dtype, "cat": cat,
        "title": payload.title, "content": json.dumps(content),
        "author": payload.author, "owner": payload.owner,
    })
    await _record_audit(session, document_id=doc_id, actor=actor,
                        action="CREATE", to_state="DRAFT",
                        details={"doc_number": doc_number})
    await session.commit()

    row = (await session.execute(text(
        "SELECT id, doc_number, doc_type, category, title, version, status, content_json, "
        "author, owner, effective_date, review_date, created_at, updated_at "
        "FROM reg_documents WHERE id = :id"
    ), {"id": doc_id})).fetchone()
    return _row_to_doc(row)


@router.get("/documents/{doc_id}")
async def get_document(doc_id: str, session: AsyncSession = Depends(get_session)):
    row = (await session.execute(text(
        "SELECT id, doc_number, doc_type, category, title, version, status, content_json, "
        "author, owner, effective_date, review_date, created_at, updated_at "
        "FROM reg_documents WHERE id = :id"
    ), {"id": doc_id})).fetchone()
    if not row:
        raise HTTPException(404, "Document not found")
    doc = _row_to_doc(row)

    sigs = (await session.execute(text(
        "SELECT id, signer_name, signer_role, meaning, content_hash, signed_at "
        "FROM reg_signatures WHERE document_id = :id ORDER BY signed_at"
    ), {"id": doc_id})).fetchall()
    doc["signatures"] = [
        {
            "id": s._mapping["id"],
            "name": s._mapping["signer_name"],
            "role": s._mapping["signer_role"],
            "meaning": s._mapping["meaning"],
            "content_hash": s._mapping["content_hash"],
            "signed_at": s._mapping["signed_at"].isoformat() if s._mapping["signed_at"] else None,
        }
        for s in sigs
    ]

    trail = (await session.execute(text(
        "SELECT actor, action, from_state, to_state, details, created_at "
        "FROM reg_audit_trail WHERE document_id = :id ORDER BY created_at"
    ), {"id": doc_id})).fetchall()
    doc["audit_trail"] = [
        {
            "actor": t._mapping["actor"],
            "action": t._mapping["action"],
            "from_state": t._mapping["from_state"],
            "to_state": t._mapping["to_state"],
            "details": t._mapping["details"],
            "at": t._mapping["created_at"].isoformat() if t._mapping["created_at"] else None,
        }
        for t in trail
    ]
    return doc


@router.patch("/documents/{doc_id}")
async def update_document(
    doc_id: str,
    payload: DocumentUpdate,
    actor: str = Query("system"),
    session: AsyncSession = Depends(get_session),
):
    cur = (await session.execute(text(
        "SELECT status FROM reg_documents WHERE id = :id"
    ), {"id": doc_id})).fetchone()
    if not cur:
        raise HTTPException(404, "Document not found")
    if cur._mapping["status"] not in ("DRAFT", "IN_REVIEW"):
        raise HTTPException(409, "Only DRAFT or IN_REVIEW documents can be edited")

    sets = []
    params: Dict[str, Any] = {"id": doc_id, "now": _now()}
    if payload.title is not None:
        sets.append("title = :title"); params["title"] = payload.title
    if payload.content is not None:
        sets.append("content_json = CAST(:content AS JSONB)"); params["content"] = json.dumps(payload.content)
    if payload.owner is not None:
        sets.append("owner = :owner"); params["owner"] = payload.owner
    if payload.author is not None:
        sets.append("author = :author"); params["author"] = payload.author
    if not sets:
        return {"updated": False}
    sets.append("updated_at = :now")
    await session.execute(text(
        f"UPDATE reg_documents SET {', '.join(sets)} WHERE id = :id"
    ), params)
    await _record_audit(session, document_id=doc_id, actor=actor,
                        action="UPDATE", details={"fields": list(payload.dict(exclude_none=True).keys())})
    await session.commit()
    return {"updated": True}


@router.post("/documents/{doc_id}/transition/{action}")
async def transition_document(
    doc_id: str, action: str,
    actor: str = Query("system"),
    session: AsyncSession = Depends(get_session),
):
    if action not in LIFECYCLE:
        raise HTTPException(400, f"Unknown action '{action}'")
    required_from, target, meaning = LIFECYCLE[action]

    cur = (await session.execute(text(
        "SELECT status, version FROM reg_documents WHERE id = :id"
    ), {"id": doc_id})).fetchone()
    if not cur:
        raise HTTPException(404, "Document not found")
    current_state = cur._mapping["status"]
    if required_from and current_state != required_from:
        raise HTTPException(409, f"Cannot {action} from state {current_state}; expected {required_from}")

    params: Dict[str, Any] = {"id": doc_id, "status": target, "now": _now()}
    sets = ["status = :status", "updated_at = :now"]
    if target == "EFFECTIVE":
        eff = date.today()
        rev = eff + timedelta(days=365)
        sets += ["effective_date = :eff", "review_date = :rev"]
        params["eff"] = eff; params["rev"] = rev
    if target == "OBSOLETE":
        sets.append("obsoleted_at = :now")

    await session.execute(text(
        f"UPDATE reg_documents SET {', '.join(sets)} WHERE id = :id"
    ), params)
    await _record_audit(session, document_id=doc_id, actor=actor,
                        action=f"TRANSITION:{action}",
                        from_state=current_state, to_state=target,
                        details={"meaning": meaning})
    await session.commit()
    return {"id": doc_id, "status": target, "meaning": meaning}


@router.post("/documents/{doc_id}/sign")
async def sign_document(
    doc_id: str,
    payload: SignRequest,
    actor: str = Query("system"),
    session: AsyncSession = Depends(get_session),
):
    row = (await session.execute(text(
        "SELECT content_json, version, status FROM reg_documents WHERE id = :id"
    ), {"id": doc_id})).fetchone()
    if not row:
        raise HTTPException(404, "Document not found")
    content = row._mapping["content_json"]
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except Exception:
            content = {}
    chash = _content_hash({
        "content": content,
        "version": row._mapping["version"],
        "status": row._mapping["status"],
    })
    sig_id = _new_id()
    await session.execute(text(
        "INSERT INTO reg_signatures (id, document_id, signer_name, signer_role, meaning, content_hash) "
        "VALUES (:id, :doc, :name, :role, :meaning, :hash)"
    ), {
        "id": sig_id, "doc": doc_id,
        "name": payload.signer_name, "role": payload.signer_role,
        "meaning": payload.meaning, "hash": chash,
    })
    await _record_audit(session, document_id=doc_id, actor=actor,
                        action="SIGN",
                        details={"role": payload.signer_role, "meaning": payload.meaning,
                                 "content_hash": chash})
    await session.commit()
    return {"id": sig_id, "content_hash": chash}


@router.get("/documents/{doc_id}/pdf")
async def download_pdf(doc_id: str, session: AsyncSession = Depends(get_session)):
    row = (await session.execute(text(
        "SELECT id, doc_number, doc_type, category, title, version, status, content_json, "
        "author, owner, effective_date, review_date "
        "FROM reg_documents WHERE id = :id"
    ), {"id": doc_id})).fetchone()
    if not row:
        raise HTTPException(404, "Document not found")
    m = row._mapping
    content = m["content_json"]
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except Exception:
            content = {}

    sigs = (await session.execute(text(
        "SELECT signer_name, signer_role, meaning, signed_at FROM reg_signatures "
        "WHERE document_id = :id ORDER BY signed_at"
    ), {"id": doc_id})).fetchall()
    sig_list = [
        {
            "name": s._mapping["signer_name"],
            "role": s._mapping["signer_role"],
            "meaning": s._mapping["meaning"],
            "signed_at": s._mapping["signed_at"].strftime("%Y-%m-%d %H:%M UTC")
                         if s._mapping["signed_at"] else "",
        }
        for s in sigs
    ]

    pdf_bytes = build_document_pdf(
        doc_meta={
            "doc_number": m["doc_number"],
            "doc_type": m["doc_type"],
            "category": m["category"],
            "title": m["title"],
            "version": m["version"],
            "status": m["status"],
            "effective_date": m["effective_date"].isoformat() if m["effective_date"] else None,
            "review_date": m["review_date"].isoformat() if m["review_date"] else None,
            "author": m["author"],
            "owner": m["owner"],
        },
        content=content,
        signatures=sig_list,
        verify_url=f"https://erp.bonnesantemedicals.com/regulatory/verify/{m['id']}",
    )
    filename = f"{m['doc_number']}_v{m['version']}.pdf".replace(" ", "_")
    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Deviations / CAPA
# ─────────────────────────────────────────────────────────────────────────────
async def _next_dev_ref(session: AsyncSession) -> str:
    year = datetime.now(timezone.utc).year
    c = (await session.execute(text(
        "SELECT COUNT(*) FROM reg_deviations WHERE ref_number LIKE :p"
    ), {"p": f"DEV-{year}-%"})).scalar() or 0
    return f"DEV-{year}-{int(c) + 1:04d}"


@router.get("/deviations")
async def list_deviations(
    status: Optional[str] = None,
    limit: int = Query(200, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
):
    params: Dict[str, Any] = {"limit": limit}
    where = ""
    if status:
        where = "WHERE status = :status"
        params["status"] = status
    rows = (await session.execute(text(
        "SELECT id, ref_number, title, deviation_type, severity, description, root_cause, "
        "corrective_action, preventive_action, owner, status, opened_at, due_date, closed_at "
        f"FROM reg_deviations {where} ORDER BY opened_at DESC LIMIT :limit"
    ), params)).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/deviations")
async def create_deviation(
    payload: DeviationCreate,
    session: AsyncSession = Depends(get_session),
):
    dev_id = _new_id()
    ref = await _next_dev_ref(session)
    await session.execute(text(
        "INSERT INTO reg_deviations (id, ref_number, title, deviation_type, severity, "
        "description, owner, due_date, status) "
        "VALUES (:id, :ref, :title, :dt, :sev, :desc, :owner, :due, 'OPEN')"
    ), {
        "id": dev_id, "ref": ref, "title": payload.title,
        "dt": payload.deviation_type, "sev": payload.severity,
        "desc": payload.description, "owner": payload.owner,
        "due": payload.due_date,
    })
    await session.commit()
    return {"id": dev_id, "ref_number": ref}


@router.patch("/deviations/{dev_id}")
async def update_deviation(
    dev_id: str,
    payload: DeviationUpdate,
    session: AsyncSession = Depends(get_session),
):
    fields = payload.dict(exclude_none=True)
    if not fields:
        return {"updated": False}
    sets = []
    params: Dict[str, Any] = {"id": dev_id}
    for k, v in fields.items():
        sets.append(f"{k} = :{k}")
        params[k] = v
    if fields.get("status") in ("CLOSED", "REJECTED"):
        sets.append("closed_at = now()")
    await session.execute(text(
        f"UPDATE reg_deviations SET {', '.join(sets)} WHERE id = :id"
    ), params)
    await session.commit()
    return {"updated": True}


# ─────────────────────────────────────────────────────────────────────────────
# Environmental monitoring
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/env-logs")
async def list_env_logs(
    area: Optional[str] = None,
    param_type: Optional[str] = None,
    days: int = Query(7, ge=1, le=180),
    limit: int = Query(200, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    where = ["recorded_at >= :since"]
    params: Dict[str, Any] = {"since": since, "limit": limit}
    if area:
        where.append("area = :area"); params["area"] = area
    if param_type:
        where.append("param_type = :pt"); params["pt"] = param_type
    rows = (await session.execute(text(
        "SELECT id, area, param_type, value, unit, lower_limit, upper_limit, oos, "
        "recorded_by, notes, recorded_at "
        f"FROM reg_env_logs WHERE {' AND '.join(where)} "
        "ORDER BY recorded_at DESC LIMIT :limit"
    ), params)).fetchall()
    return [dict(r._mapping) for r in rows]


@router.post("/env-logs")
async def create_env_log(
    payload: EnvLogCreate,
    session: AsyncSession = Depends(get_session),
):
    oos = False
    if payload.lower_limit is not None and payload.value < payload.lower_limit:
        oos = True
    if payload.upper_limit is not None and payload.value > payload.upper_limit:
        oos = True
    env_id = _new_id()
    await session.execute(text(
        "INSERT INTO reg_env_logs (id, area, param_type, value, unit, lower_limit, "
        "upper_limit, oos, recorded_by, notes) "
        "VALUES (:id, :area, :pt, :val, :unit, :lo, :hi, :oos, :by, :notes)"
    ), {
        "id": env_id, "area": payload.area, "pt": payload.param_type,
        "val": payload.value, "unit": payload.unit,
        "lo": payload.lower_limit, "hi": payload.upper_limit,
        "oos": oos, "by": payload.recorded_by, "notes": payload.notes,
    })
    await session.commit()
    return {"id": env_id, "oos": oos}
