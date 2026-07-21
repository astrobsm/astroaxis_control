"""
Announcements Module API

Features:
- Schedule company-wide announcements (date / time / repeat).
- Optional uploaded audio (mp3/wav/ogg/m4a/webm) that plays when triggered.
- Personalized clock-in greetings based on AstroBSM lateness policy:
    * EARLY arrival      -> reward / praise message
    * ON-TIME arrival    -> acknowledgement
    * MILD lateness      -> warning / coaching
    * SEVERE lateness    -> formal disciplinary notice
- Stores audio under /app/uploads/announcements (or ./uploads fallback).
- Auto-creates DB tables on first router call (no alembic migration needed).
"""
from fastapi import (
    APIRouter, Depends, HTTPException, UploadFile, File, Form, Query,
)
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Optional, List
from pathlib import Path
from datetime import datetime, timezone, timedelta, time as time_cls, date as date_cls
import os
import secrets
import uuid

from app.db import get_session

router = APIRouter(prefix='/api/announcements')

# ─────────────────────────────────────────────────────────────────────────────
# Constants & schema
# ─────────────────────────────────────────────────────────────────────────────
LAGOS_TZ = timezone(timedelta(hours=1))  # Africa/Lagos, no DST

AUDIO_DIR_PRIMARY = Path("/app/uploads/announcements")
ALLOWED_AUDIO_EXT = {".mp3", ".wav", ".ogg", ".m4a", ".webm", ".aac"}
MAX_AUDIO_BYTES = 15 * 1024 * 1024  # 15 MB

CREATE_ANNOUNCEMENTS_SQL = """
CREATE TABLE IF NOT EXISTS announcements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(200) NOT NULL,
    message TEXT NOT NULL,
    audio_filename VARCHAR(255),
    scheduled_date DATE,
    scheduled_time TIME NOT NULL,
    repeat_type VARCHAR(16) NOT NULL DEFAULT 'none',
    repeat_days VARCHAR(32),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_played_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
)
"""

CREATE_POLICY_SQL = """
CREATE TABLE IF NOT EXISTS attendance_policy (
    id INTEGER PRIMARY KEY DEFAULT 1,
    expected_start_hour INTEGER NOT NULL DEFAULT 8,
    expected_start_minute INTEGER NOT NULL DEFAULT 0,
    early_threshold_minutes INTEGER NOT NULL DEFAULT 5,
    on_time_grace_minutes INTEGER NOT NULL DEFAULT 5,
    mild_late_max_minutes INTEGER NOT NULL DEFAULT 30,
    company_name VARCHAR(120) NOT NULL DEFAULT 'AstroBSM - Bonnesante Medicals',
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CHECK (id = 1)
)
"""

CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_ann_active ON announcements(is_active)",
    "CREATE INDEX IF NOT EXISTS idx_ann_time ON announcements(scheduled_time)",
]


async def _ensure_tables(session: AsyncSession):
    await session.execute(text(CREATE_ANNOUNCEMENTS_SQL))
    await session.execute(text(CREATE_POLICY_SQL))
    for sql in CREATE_INDEXES:
        await session.execute(text(sql))
    # Seed single policy row
    await session.execute(text(
        "INSERT INTO attendance_policy (id) VALUES (1) ON CONFLICT (id) DO NOTHING"
    ))
    await session.commit()


def _ensure_audio_dir() -> Path:
    target = AUDIO_DIR_PRIMARY
    try:
        target.mkdir(parents=True, exist_ok=True)
        return target
    except (PermissionError, OSError):
        fallback = Path("uploads/announcements").resolve()
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


# ─────────────────────────────────────────────────────────────────────────────
# Policy helpers
# ─────────────────────────────────────────────────────────────────────────────
async def _load_policy(session: AsyncSession) -> dict:
    await _ensure_tables(session)
    row = (await session.execute(text(
        "SELECT expected_start_hour, expected_start_minute, "
        "early_threshold_minutes, on_time_grace_minutes, "
        "mild_late_max_minutes, company_name "
        "FROM attendance_policy WHERE id = 1"
    ))).first()
    if not row:
        return {
            'expected_start_hour': 8,
            'expected_start_minute': 0,
            'early_threshold_minutes': 5,
            'on_time_grace_minutes': 5,
            'mild_late_max_minutes': 30,
            'company_name': 'AstroBSM - Bonnesante Medicals',
        }
    return dict(row._mapping)


def _classify(clock_in_local: datetime, policy: dict) -> dict:
    """Return tier + minutes_diff (negative if early)."""
    expected = clock_in_local.replace(
        hour=policy['expected_start_hour'],
        minute=policy['expected_start_minute'],
        second=0, microsecond=0,
    )
    diff_min = int(round((clock_in_local - expected).total_seconds() / 60))
    early_th = -int(policy['early_threshold_minutes'])
    on_time_grace = int(policy['on_time_grace_minutes'])
    mild_max = int(policy['mild_late_max_minutes'])
    if diff_min <= early_th:
        tier = 'early'
    elif diff_min <= on_time_grace:
        tier = 'on_time'
    elif diff_min <= mild_max:
        tier = 'mild_late'
    else:
        tier = 'severe_late'
    return {'tier': tier, 'minutes_diff': diff_min, 'expected_local': expected.isoformat()}


def _build_message(staff_name: str, classification: dict, policy: dict) -> str:
    """Professional, personalized message per tier."""
    company = policy.get('company_name', 'AstroBSM')
    tier = classification['tier']
    diff = classification['minutes_diff']
    start_h = policy['expected_start_hour']
    start_m = policy['expected_start_minute']
    expected_str = f"{start_h:02d}:{start_m:02d}"

    if tier == 'early':
        mins_early = abs(diff)
        return (
            f"Good morning {staff_name}. Welcome to {company}. "
            f"Outstanding! You arrived {mins_early} minutes ahead of the "
            f"{expected_str} resumption time. Punctuality like this is the "
            f"backbone of our excellence and you have earned a reward point "
            f"for today. Keep setting the standard."
        )
    if tier == 'on_time':
        return (
            f"Good morning {staff_name}. Welcome to {company}. "
            f"You are clocked in on time. Thank you for your reliability. "
            f"Have a productive and safe shift."
        )
    if tier == 'mild_late':
        return (
            f"Good morning {staff_name}. You are clocked in {diff} minutes "
            f"after the {expected_str} resumption time. This is recorded as "
            f"mild lateness. Please plan your morning so you can resume on "
            f"time tomorrow. Repeated lateness may attract a query."
        )
    # severe_late
    return (
        f"Attention {staff_name}. You are clocked in {diff} minutes after "
        f"the {expected_str} resumption time. This is recorded as severe "
        f"lateness in line with the {company} attendance policy. Kindly "
        f"report to your supervisor immediately and submit a written "
        f"explanation. Continued violation will lead to disciplinary action."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints — POLICY
# ─────────────────────────────────────────────────────────────────────────────
@router.get('/policy')
async def get_policy(session: AsyncSession = Depends(get_session)):
    return await _load_policy(session)


@router.put('/policy')
async def update_policy(payload: dict, session: AsyncSession = Depends(get_session)):
    await _ensure_tables(session)
    fields = [
        'expected_start_hour', 'expected_start_minute',
        'early_threshold_minutes', 'on_time_grace_minutes',
        'mild_late_max_minutes', 'company_name',
    ]
    sets, params = [], {}
    for f in fields:
        if f in payload and payload[f] is not None:
            sets.append(f"{f} = :{f}")
            params[f] = payload[f]
    if not sets:
        raise HTTPException(status_code=400, detail="No fields to update")
    sets.append("updated_at = NOW()")
    sql = f"UPDATE attendance_policy SET {', '.join(sets)} WHERE id = 1"
    await session.execute(text(sql), params)
    await session.commit()
    return await _load_policy(session)


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints — ANNOUNCEMENTS CRUD
# ─────────────────────────────────────────────────────────────────────────────
@router.get('/')
async def list_announcements(
    only_active: bool = Query(False),
    session: AsyncSession = Depends(get_session),
):
    await _ensure_tables(session)
    where = "WHERE is_active = TRUE" if only_active else ""
    rows = (await session.execute(text(
        f"SELECT id, title, message, audio_filename, scheduled_date, "
        f"scheduled_time, repeat_type, repeat_days, is_active, "
        f"last_played_at, created_at, updated_at "
        f"FROM announcements {where} ORDER BY scheduled_time ASC"
    ))).mappings().all()
    return [dict(r) for r in rows]


@router.post('/')
async def create_announcement(
    payload: dict,
    session: AsyncSession = Depends(get_session),
):
    await _ensure_tables(session)
    title = (payload.get('title') or '').strip()
    message = (payload.get('message') or '').strip()
    if not title or not message:
        raise HTTPException(status_code=400, detail="title and message required")
    sched_time_raw = (payload.get('scheduled_time') or '').strip()
    if not sched_time_raw:
        raise HTTPException(status_code=400, detail="scheduled_time (HH:MM) required")
    try:
        parts = sched_time_raw.split(':')
        sched_time = time_cls(int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0)
    except Exception:
        raise HTTPException(status_code=400, detail="scheduled_time must be HH:MM")
    sched_date_raw = payload.get('scheduled_date') or None
    sched_date = None
    if sched_date_raw:
        if isinstance(sched_date_raw, date_cls):
            sched_date = sched_date_raw
        else:
            try:
                sched_date = datetime.strptime(str(sched_date_raw)[:10], '%Y-%m-%d').date()
            except Exception:
                raise HTTPException(status_code=400, detail="scheduled_date must be YYYY-MM-DD")
    repeat_type = (payload.get('repeat_type') or 'none').strip().lower()
    if repeat_type not in {'none', 'daily', 'weekly', 'monthly'}:
        repeat_type = 'none'
    repeat_days = payload.get('repeat_days') or None
    if isinstance(repeat_days, list):
        repeat_days = ','.join(str(x) for x in repeat_days)

    new_id = str(uuid.uuid4())
    await session.execute(text(
        "INSERT INTO announcements "
        "(id, title, message, scheduled_date, scheduled_time, repeat_type, "
        " repeat_days, is_active) "
        "VALUES (:id, :title, :message, :sched_date, :sched_time, "
        "        :repeat_type, :repeat_days, TRUE)"
    ), {
        'id': new_id,
        'title': title,
        'message': message,
        'sched_date': sched_date,
        'sched_time': sched_time,
        'repeat_type': repeat_type,
        'repeat_days': repeat_days,
    })
    await session.commit()
    return {'success': True, 'id': new_id}


@router.put('/{ann_id}')
async def update_announcement(
    ann_id: str,
    payload: dict,
    session: AsyncSession = Depends(get_session),
):
    await _ensure_tables(session)
    allowed = {
        'title', 'message', 'scheduled_date', 'scheduled_time',
        'repeat_type', 'repeat_days', 'is_active',
    }
    sets, params = [], {'id': ann_id}
    for k, v in payload.items():
        if k in allowed:
            if k == 'repeat_days' and isinstance(v, list):
                v = ','.join(str(x) for x in v)
            elif k == 'scheduled_date' and isinstance(v, str) and v:
                try:
                    v = datetime.strptime(v[:10], '%Y-%m-%d').date()
                except Exception:
                    raise HTTPException(status_code=400, detail="scheduled_date must be YYYY-MM-DD")
            elif k == 'scheduled_time' and isinstance(v, str) and v:
                try:
                    parts = v.split(':')
                    v = time_cls(int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0)
                except Exception:
                    raise HTTPException(status_code=400, detail="scheduled_time must be HH:MM")
            sets.append(f"{k} = :{k}")
            params[k] = v
    if not sets:
        raise HTTPException(status_code=400, detail="No fields to update")
    sets.append("updated_at = NOW()")
    await session.execute(text(
        f"UPDATE announcements SET {', '.join(sets)} WHERE id = :id"
    ), params)
    await session.commit()
    return {'success': True}


@router.delete('/{ann_id}')
async def delete_announcement(
    ann_id: str,
    session: AsyncSession = Depends(get_session),
):
    await _ensure_tables(session)
    # Try to remove audio file too
    row = (await session.execute(text(
        "SELECT audio_filename FROM announcements WHERE id = :id"
    ), {'id': ann_id})).first()
    if row and row[0]:
        try:
            (_ensure_audio_dir() / row[0]).unlink(missing_ok=True)
        except Exception:
            pass
    await session.execute(text(
        "DELETE FROM announcements WHERE id = :id"
    ), {'id': ann_id})
    await session.commit()
    return {'success': True}


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints — AUDIO upload / serve
# ─────────────────────────────────────────────────────────────────────────────
@router.post('/{ann_id}/audio')
async def upload_audio(
    ann_id: str,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    await _ensure_tables(session)
    row = (await session.execute(text(
        "SELECT audio_filename FROM announcements WHERE id = :id"
    ), {'id': ann_id})).first()
    if not row:
        raise HTTPException(status_code=404, detail="Announcement not found")

    orig = (file.filename or 'audio').strip()
    ext = os.path.splitext(orig)[1].lower()
    if ext not in ALLOWED_AUDIO_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio type. Allowed: {sorted(ALLOWED_AUDIO_EXT)}",
        )

    target_dir = _ensure_audio_dir()
    safe_name = f"{ann_id}_{secrets.token_hex(4)}{ext}"
    target = target_dir / safe_name
    written = 0
    try:
        with target.open('wb') as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_AUDIO_BYTES:
                    out.close()
                    target.unlink(missing_ok=True)
                    raise HTTPException(status_code=400, detail="Audio too large (>15MB)")
                out.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")

    # Remove old audio file if any
    old = row[0]
    if old:
        try:
            (target_dir / old).unlink(missing_ok=True)
        except Exception:
            pass

    await session.execute(text(
        "UPDATE announcements SET audio_filename = :fn, updated_at = NOW() "
        "WHERE id = :id"
    ), {'fn': safe_name, 'id': ann_id})
    await session.commit()
    return {'success': True, 'audio_filename': safe_name,
            'audio_url': f"/api/announcements/audio/{safe_name}"}


@router.get('/audio/{filename}')
async def serve_audio(filename: str):
    # Path-safety: only allow simple basename
    if '/' in filename or '\\' in filename or '..' in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    target_dir = _ensure_audio_dir()
    fp = target_dir / filename
    if not fp.exists() or not fp.is_file():
        raise HTTPException(status_code=404, detail="Audio not found")
    ext = fp.suffix.lower().lstrip('.')
    media_type = {
        'mp3': 'audio/mpeg', 'wav': 'audio/wav', 'ogg': 'audio/ogg',
        'm4a': 'audio/mp4', 'webm': 'audio/webm', 'aac': 'audio/aac',
    }.get(ext, 'application/octet-stream')
    return FileResponse(str(fp), media_type=media_type, filename=filename)


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint — DUE NOW (frontend polls this every 60s to play scheduled audio)
# ─────────────────────────────────────────────────────────────────────────────
def _is_due(row, local_now: datetime, window_seconds: int = 90) -> bool:
    if not row['is_active']:
        return False
    sched_time: Optional[time_cls] = row['scheduled_time']
    if sched_time is None:
        return False
    sched_date: Optional[date_cls] = row['scheduled_date']
    repeat = (row['repeat_type'] or 'none').lower()
    today_local = local_now.date()

    # one-shot scheduled date
    if sched_date and repeat == 'none' and sched_date != today_local:
        return False
    # weekly: only on listed weekdays (0=Mon..6=Sun)
    if repeat == 'weekly':
        days_str = (row['repeat_days'] or '').strip()
        if days_str:
            try:
                allowed = {int(x) for x in days_str.split(',') if x.strip() != ''}
                if today_local.weekday() not in allowed:
                    return False
            except ValueError:
                return False
    # monthly: same day-of-month as scheduled_date
    if repeat == 'monthly':
        if sched_date and sched_date.day != today_local.day:
            return False

    # Time window
    target = local_now.replace(
        hour=sched_time.hour, minute=sched_time.minute,
        second=sched_time.second or 0, microsecond=0,
    )
    delta = (local_now - target).total_seconds()
    return 0 <= delta <= window_seconds


@router.get('/due')
async def list_due(session: AsyncSession = Depends(get_session)):
    """Return announcements due in the last ~90 seconds (Lagos local time)."""
    await _ensure_tables(session)
    rows = (await session.execute(text(
        "SELECT id, title, message, audio_filename, scheduled_date, "
        "scheduled_time, repeat_type, repeat_days, is_active "
        "FROM announcements WHERE is_active = TRUE"
    ))).mappings().all()
    local_now = datetime.now(LAGOS_TZ)
    due = []
    for r in rows:
        d = dict(r)
        if _is_due(d, local_now):
            d['audio_url'] = (
                f"/api/announcements/audio/{d['audio_filename']}"
                if d['audio_filename'] else None
            )
            d['id'] = str(d['id'])
            due.append(d)
    return {'now_local': local_now.isoformat(), 'due': due}


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint — CLOCK-IN GREETING (called by frontend after a successful clock-in)
# ─────────────────────────────────────────────────────────────────────────────
@router.post('/clock-in-greeting')
async def clock_in_greeting(
    payload: dict,
    session: AsyncSession = Depends(get_session),
):
    """
    Body: {"staff_id": "<uuid>", "clock_in_time": "ISO8601 (optional)"}
    Returns: {tier, minutes_diff, message, staff_name, expected_local, ...}
    """
    staff_id = (payload.get('staff_id') or '').strip()
    if not staff_id:
        raise HTTPException(status_code=400, detail="staff_id required")

    # Resolve staff name (avoid hard import — use raw SQL to stay decoupled)
    row = (await session.execute(text(
        "SELECT first_name, last_name, position FROM staff WHERE id = :id"
    ), {'id': staff_id})).first()
    if not row:
        raise HTTPException(status_code=404, detail="Staff not found")
    first_name, last_name, position = row[0], row[1], row[2]
    staff_name = f"{first_name} {last_name}".strip() or 'Team member'

    # Parse clock-in time (default = now)
    raw_ts = (payload.get('clock_in_time') or '').strip()
    if raw_ts:
        try:
            dt_utc = datetime.fromisoformat(raw_ts.replace('Z', '+00:00'))
            if dt_utc.tzinfo is None:
                dt_utc = dt_utc.replace(tzinfo=timezone.utc)
        except ValueError:
            dt_utc = datetime.now(timezone.utc)
    else:
        dt_utc = datetime.now(timezone.utc)
    clock_in_local = dt_utc.astimezone(LAGOS_TZ)

    policy = await _load_policy(session)
    classification = _classify(clock_in_local, policy)
    message = _build_message(staff_name, classification, policy)

    return {
        'staff_id': staff_id,
        'staff_name': staff_name,
        'position': position,
        'clock_in_local': clock_in_local.isoformat(),
        'expected_local': classification['expected_local'],
        'minutes_diff': classification['minutes_diff'],
        'tier': classification['tier'],
        'message': message,
        'company_name': policy.get('company_name'),
    }
