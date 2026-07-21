"""
ASTRO-ASIX ERP - Communication Module API
Handles Notice Board, Team Chat, and Letter storage for cross-device sync.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, func, desc, asc
from app.db import get_session
from app.models import Notice, ChatMessage, Letter
from app.schemas import (
    NoticeCreate, NoticeUpdate, NoticeSchema,
    ChatMessageCreate, ChatMessageSchema,
    LetterCreate, LetterUpdate, LetterSchema,
)
from typing import Optional, List
from uuid import UUID
from datetime import datetime

router = APIRouter(prefix="/api/communication", tags=["communication"])


# ============ UNREAD COUNTS (for WhatsApp-style badges) ============

@router.get("/unread")
async def get_unread_counts(
    notices_since: Optional[str] = None,
    messages_since: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    """Return counts of new notices and messages since given timestamps."""
    result = {}

    # Count new notices since timestamp
    nq = select(func.count(Notice.id)).where(Notice.is_active == True)
    if notices_since:
        try:
            ts = datetime.fromisoformat(notices_since.replace("Z", "+00:00"))
            nq = nq.where(Notice.created_at > ts)
        except (ValueError, TypeError):
            pass
    r = await session.execute(nq)
    result["notices"] = r.scalar() or 0

    # Count new messages per channel since timestamp
    mq = select(ChatMessage.channel, func.count(ChatMessage.id).label("cnt"))
    if messages_since:
        try:
            ts = datetime.fromisoformat(messages_since.replace("Z", "+00:00"))
            mq = mq.where(ChatMessage.created_at > ts)
        except (ValueError, TypeError):
            pass
    mq = mq.group_by(ChatMessage.channel)
    r = await session.execute(mq)
    rows = r.all()
    msg_counts = {row[0]: row[1] for row in rows}
    result["messages"] = msg_counts
    result["messages_total"] = sum(msg_counts.values())

    return result


# ============ NOTICE BOARD ============

@router.get("/notices", response_model=List[NoticeSchema])
async def list_notices(
    category: Optional[str] = None,
    priority: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    """List all active notices, pinned first, newest first."""
    q = select(Notice).where(Notice.is_active == True)
    if category:
        q = q.where(Notice.category == category)
    if priority:
        q = q.where(Notice.priority == priority)
    q = q.order_by(desc(Notice.pinned), desc(Notice.created_at))
    result = await session.execute(q)
    return result.scalars().all()


@router.post("/notices", response_model=NoticeSchema, status_code=201)
async def create_notice(
    payload: NoticeCreate,
    author_name: str = Query("Admin", alias="author"),
    session: AsyncSession = Depends(get_session),
):
    """Create a new notice."""
    notice = Notice(
        title=payload.title,
        content=payload.content,
        priority=payload.priority,
        category=payload.category,
        author=author_name,
        pinned=payload.pinned,
    )
    session.add(notice)
    await session.commit()
    await session.refresh(notice)
    return notice


@router.put("/notices/{notice_id}", response_model=NoticeSchema)
async def update_notice(
    notice_id: UUID,
    payload: NoticeUpdate,
    session: AsyncSession = Depends(get_session),
):
    """Update an existing notice."""
    result = await session.execute(select(Notice).where(Notice.id == notice_id))
    notice = result.scalar_one_or_none()
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")
    for field, value in payload.dict(exclude_unset=True).items():
        setattr(notice, field, value)
    await session.commit()
    await session.refresh(notice)
    return notice


@router.delete("/notices/{notice_id}")
async def delete_notice(
    notice_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """Soft-delete a notice."""
    result = await session.execute(select(Notice).where(Notice.id == notice_id))
    notice = result.scalar_one_or_none()
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")
    notice.is_active = False
    await session.commit()
    return {"success": True, "message": "Notice deleted"}


@router.patch("/notices/{notice_id}/pin")
async def toggle_pin_notice(
    notice_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """Toggle pin status of a notice."""
    result = await session.execute(select(Notice).where(Notice.id == notice_id))
    notice = result.scalar_one_or_none()
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found")
    notice.pinned = not notice.pinned
    await session.commit()
    return {"success": True, "pinned": notice.pinned}


# ============ TEAM CHAT ============

@router.get("/messages", response_model=List[ChatMessageSchema])
async def list_messages(
    channel: str = "general",
    limit: int = Query(200, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
):
    """Get messages for a channel, newest last (chronological)."""
    q = (
        select(ChatMessage)
        .where(ChatMessage.channel == channel)
        .order_by(asc(ChatMessage.created_at))
        .limit(limit)
    )
    result = await session.execute(q)
    return result.scalars().all()


@router.post("/messages", response_model=ChatMessageSchema, status_code=201)
async def send_message(
    payload: ChatMessageCreate,
    sender_name: str = Query("User", alias="sender"),
    session: AsyncSession = Depends(get_session),
):
    """Send a chat message to a channel."""
    msg = ChatMessage(
        channel=payload.channel,
        sender=sender_name,
        text=payload.text,
    )
    session.add(msg)
    await session.commit()
    await session.refresh(msg)
    return msg


@router.get("/messages/channels")
async def list_channels(session: AsyncSession = Depends(get_session)):
    """List all channels with message counts."""
    q = (
        select(ChatMessage.channel, func.count(ChatMessage.id).label("count"))
        .group_by(ChatMessage.channel)
        .order_by(ChatMessage.channel)
    )
    result = await session.execute(q)
    rows = result.all()
    return [{"channel": r[0], "count": r[1]} for r in rows]


# ============ LETTERS (saved drafts for cross-device access) ============

@router.get("/letters", response_model=List[LetterSchema])
async def list_letters(
    session: AsyncSession = Depends(get_session),
):
    """List all saved letters, newest first."""
    q = select(Letter).order_by(desc(Letter.created_at))
    result = await session.execute(q)
    return result.scalars().all()


@router.post("/letters", response_model=LetterSchema, status_code=201)
async def create_letter(
    payload: LetterCreate,
    author_name: str = Query("Admin", alias="author"),
    session: AsyncSession = Depends(get_session),
):
    """Save a new letter."""
    letter = Letter(
        title=payload.title,
        recipient=payload.recipient,
        body=payload.body,
        author=author_name,
    )
    session.add(letter)
    await session.commit()
    await session.refresh(letter)
    return letter


@router.put("/letters/{letter_id}", response_model=LetterSchema)
async def update_letter(
    letter_id: UUID,
    payload: LetterUpdate,
    session: AsyncSession = Depends(get_session),
):
    """Update a saved letter."""
    result = await session.execute(select(Letter).where(Letter.id == letter_id))
    letter = result.scalar_one_or_none()
    if not letter:
        raise HTTPException(status_code=404, detail="Letter not found")
    for field, value in payload.dict(exclude_unset=True).items():
        setattr(letter, field, value)
    await session.commit()
    await session.refresh(letter)
    return letter


@router.delete("/letters/{letter_id}")
async def delete_letter(
    letter_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """Delete a saved letter."""
    result = await session.execute(select(Letter).where(Letter.id == letter_id))
    letter = result.scalar_one_or_none()
    if not letter:
        raise HTTPException(status_code=404, detail="Letter not found")
    await session.delete(letter)
    await session.commit()
    return {"success": True, "message": "Letter deleted"}
