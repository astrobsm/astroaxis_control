"""Staff birthdays and work anniversaries.

SEPARATE FROM THE CUSTOMER AGENT, DELIBERATELY
==============================================
This module reads HR data. It has its own tables and its own routes, and
nothing customer-facing imports it. If a customer-facing feature ever needs a
staff name it should be given that name, not given this module.

FOUR THINGS MUST ALL BE TRUE BEFORE ANYBODY IS MESSAGED
=======================================================
    1. the master outbound switch is on
    2. STAFF_ENGAGEMENT_ENABLED is on
    3. this person has agreed, individually
    4. this person is not hidden from staff displays

The fourth is easy to miss and is the one that matters most. The hiding
feature built in m8901234567l suggests "Bereavement -- no birthday reminders"
as a reason, and somebody hidden for exactly that reason receiving an
automated greeting would be the single worst thing this module could do.

NOTHING IS SENT HERE EITHER
===========================
Messages are put in the same outbox as everything else, which nothing drains.
That means the whole pipeline -- who is due, what it would say, who is
excluded and why -- can be inspected on a real day with real staff before any
provider exists.

TODAY'S LIST IS DERIVED, THE RECORD OF SENDING IS NOT
=====================================================
Who has a birthday today is computed from `staff.date_of_birth` every time
anybody asks. Nothing is copied: a second store of a date of birth would
drift, and a wrong birthday is worse than none.

What IS stored is that a greeting was queued, keyed by person, occasion and
YEAR. A job that runs twice, or a container restarted at midnight, cannot
produce two greetings -- enforced by a unique constraint rather than by the
job being careful.
"""
from __future__ import annotations

from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import messaging as msg

VISIBILITIES = ("NOBODY", "MANAGER", "DEPARTMENT", "ORGANISATION")
OCCASIONS = ("BIRTHDAY", "WORK_ANNIVERSARY")

# 29 February exists in one year in four. A birthday on that date is marked on
# 28 February in other years rather than skipped three times out of four,
# which is the behaviour people expect and the one nobody remembers to write.
LEAP_DAY = (2, 29)


def _celebrates_on(day: int, month: int, on: date) -> bool:
    if (month, day) == LEAP_DAY and not _is_leap(on.year):
        return (on.month, on.day) == (2, 28)
    return (on.month, on.day) == (month, day)


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


async def _milestones(session: AsyncSession) -> set:
    raw = await msg.get_setting(
        session, "STAFF_ANNIVERSARY_MILESTONES", "1,3,5,10,15,20")
    out = set()
    for part in str(raw).split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------

async def set_consent(
    session: AsyncSession, *, staff_id: UUID,
    birthday_messages: Optional[bool] = None,
    anniversary_messages: Optional[bool] = None,
    visibility: Optional[str] = None,
    preferred_name: Optional[str] = None,
    preferred_channel: Optional[str] = None,
    source: str = "HR_ENTERED", actor=None,
) -> dict:
    """Record what this member of staff has agreed to."""
    staff = (await session.execute(
        text("""SELECT id, first_name, last_name, display_hidden
                  FROM staff WHERE id = :i"""),
        {"i": str(staff_id)})).mappings().first()
    if staff is None:
        raise HTTPException(status_code=404, detail="Staff member not found.")

    if visibility is not None and visibility not in VISIBILITIES:
        raise HTTPException(
            status_code=400,
            detail=f"Visibility must be one of {', '.join(VISIBILITIES)}.")

    await session.execute(
        text("""INSERT INTO staff_engagement_consent (staff_id)
                VALUES (:i) ON CONFLICT (staff_id) DO NOTHING"""),
        {"i": str(staff_id)})

    sets, params = [], {"i": str(staff_id)}
    for column, value in (("birthday_messages", birthday_messages),
                          ("anniversary_messages", anniversary_messages),
                          ("visibility", visibility),
                          ("preferred_name", preferred_name),
                          ("preferred_channel", preferred_channel)):
        if value is not None:
            sets.append(f"{column} = :{column}")
            params[column] = value
    if not sets:
        raise HTTPException(status_code=400, detail="Nothing to change.")

    if birthday_messages or anniversary_messages:
        sets.append("consent_source = :src")
        sets.append("consent_at = NOW()")
        params["src"] = source[:40]
    sets.append("updated_by = :by")
    sets.append("updated_at = NOW()")
    params["by"] = str(actor.id) if actor is not None else None

    await session.execute(
        text(f"UPDATE staff_engagement_consent SET {', '.join(sets)} "
             f"WHERE staff_id = :i"), params)

    detail = ", ".join(f"{k}={v}" for k, v in params.items()
                       if k not in ("i", "by", "src"))
    await session.execute(
        text("""INSERT INTO staff_engagement_events
                    (staff_id, change, detail, actor_id, actor_name)
                VALUES (:i, 'CONSENT_UPDATED', :d, :a, :an)"""),
        {"i": str(staff_id), "d": detail,
         "a": str(actor.id) if actor is not None else None,
         "an": ((getattr(actor, "full_name", None)
                 or getattr(actor, "username", None)) if actor else None)})

    return {
        "staff_id": str(staff_id),
        "name": f"{staff['first_name']} {staff['last_name']}",
        "note": ("Recorded. Nothing is sent to anybody until the staff "
                 "engagement switch is also on."),
    }


# ---------------------------------------------------------------------------
# Who is due
# ---------------------------------------------------------------------------

async def calendar(
    session: AsyncSession, *, on: Optional[date] = None, days: int = 30,
    for_display: bool = True,
) -> dict:
    """Birthdays and work anniversaries coming up.

    `for_display` is the privacy control. When true -- which is how every
    screen calls it -- only people who agreed to be visible are listed, and
    their dates are shown without a year so nobody's age is broadcast. The
    engagement job calls it false, because it has to see everybody who agreed
    to a MESSAGE even if they did not agree to be on a list.
    """
    today = on or date.today()
    milestones = await _milestones(session)

    rows = (await session.execute(text("""
        SELECT s.id, s.employee_id, s.first_name, s.last_name, s.position,
               s.date_of_birth, s.hire_date, s.display_hidden,
               COALESCE(c.birthday_messages, FALSE)    AS birthday_ok,
               COALESCE(c.anniversary_messages, FALSE) AS anniversary_ok,
               COALESCE(c.visibility, 'NOBODY')        AS visibility,
               c.preferred_name
          FROM staff s
     LEFT JOIN staff_engagement_consent c ON c.staff_id = s.id
         WHERE s.is_active
    """))).mappings().all()

    birthdays, anniversaries, excluded = [], [], []
    for r in rows:
        name = (r["preferred_name"]
                or f"{r['first_name']} {r['last_name']}").strip()
        base = {
            "staff_id": str(r["id"]), "employee_id": r["employee_id"],
            "name": name, "position": r["position"] or "",
        }

        # Hidden is checked before anything else: somebody hidden because of a
        # bereavement must not appear on a birthday list at all.
        if r["display_hidden"]:
            excluded.append({**base, "why": "hidden from staff displays"})
            continue

        if r["date_of_birth"]:
            dob = r["date_of_birth"]
            for offset in range(days + 1):
                when = date.fromordinal(today.toordinal() + offset)
                if _celebrates_on(dob.day, dob.month, when):
                    if for_display and r["visibility"] == "NOBODY":
                        excluded.append(
                            {**base, "why": "has not agreed to be listed"})
                    else:
                        birthdays.append({
                            **base, "on": when.isoformat(),
                            "days_away": offset,
                            "may_message": bool(r["birthday_ok"]),
                            "visibility": r["visibility"],
                        })
                    break

        if r["hire_date"]:
            hired = r["hire_date"]
            for offset in range(days + 1):
                when = date.fromordinal(today.toordinal() + offset)
                if _celebrates_on(hired.day, hired.month, when):
                    years = when.year - hired.year
                    if years > 0 and years in milestones:
                        if for_display and r["visibility"] == "NOBODY":
                            excluded.append(
                                {**base, "why": "has not agreed to be listed"})
                        else:
                            anniversaries.append({
                                **base, "on": when.isoformat(),
                                "days_away": offset, "years": years,
                                "may_message": bool(r["anniversary_ok"]),
                                "visibility": r["visibility"],
                            })
                    break

    birthdays.sort(key=lambda x: x["days_away"])
    anniversaries.sort(key=lambda x: x["days_away"])

    return {
        "from": today.isoformat(), "days": days,
        "birthdays": birthdays,
        "anniversaries": anniversaries,
        "excluded_count": len(excluded),
        "milestones": sorted(milestones),
        "note": (
            "Dates are read from the staff record; nothing is copied, so a "
            "correction there fixes this immediately. No year is shown, "
            "because an employer broadcasting ages internally is a different "
            "thing from marking a birthday. People who have not agreed to "
            "appear, and anyone hidden from staff displays, are counted but "
            "not named."),
    }


# ---------------------------------------------------------------------------
# Preparing the greetings
# ---------------------------------------------------------------------------

async def prepare(
    session: AsyncSession, *, on: Optional[date] = None, actor=None,
) -> dict:
    """Queue today's greetings, once each, for everyone who agreed.

    Safe to run repeatedly: the unique constraint on (staff, occasion, year)
    means a second run adds nothing. That is a database guarantee rather than
    a promise about how carefully the job is scheduled.
    """
    today = on or date.today()

    if not await msg.get_setting(session, "STAFF_ENGAGEMENT_ENABLED", False):
        return {"queued": 0, "skipped": [],
                "note": ("Staff engagement is switched off. Nothing was "
                         "prepared.")}

    due = await calendar(session, on=today, days=0, for_display=False)
    birthday_tpl = await msg.get_setting(
        session, "STAFF_BIRTHDAY_TEMPLATE", "Happy birthday, {preferred_name}!")
    anniversary_tpl = await msg.get_setting(
        session, "STAFF_ANNIVERSARY_TEMPLATE",
        "Congratulations on {years} years, {preferred_name}.")

    queued, skipped = [], []

    async def _one(person, occasion, body, years=None):
        # staff carries a phone and no email column in this database, so a
        # person with no number simply cannot be reached and is reported as
        # such rather than silently dropped.
        to = (await session.execute(
            text("SELECT phone FROM staff WHERE id = :i"),
            {"i": person["staff_id"]})).scalar()
        if not to:
            skipped.append({**person, "why": "no phone number on record"})
            return

        already = (await session.execute(
            text("""SELECT 1 FROM staff_engagement_sends
                     WHERE staff_id = :s AND occasion = :o
                       AND occasion_year = :y"""),
            {"s": person["staff_id"], "o": occasion, "y": today.year})).first()
        if already:
            skipped.append({**person, "why": "already prepared this year"})
            return

        result = await msg.enqueue(
            session, channel="WHATSAPP", to_address=to, body=body,
            reason=f"{occasion.replace('_', ' ').title()} greeting, "
                   f"{today.year}",
            category="SERVICE",
            idempotency_key=f"staff:{occasion}:{person['staff_id']}:{today.year}",
            actor=actor)

        await session.execute(
            text("""INSERT INTO staff_engagement_sends
                        (staff_id, occasion, occasion_year, years_of_service,
                         outbound_message_id, status)
                    VALUES (:s, :o, :y, :yrs, :m, :st)"""),
            {"s": person["staff_id"], "o": occasion, "y": today.year,
             "yrs": years, "m": result["id"], "st": result["status"]})

        queued.append({**person, "occasion": occasion,
                       "status": result["status"],
                       "blocked_reason": result.get("blocked_reason")})

    for person in due["birthdays"]:
        if not person["may_message"]:
            skipped.append({**person, "why": "has not agreed to messages"})
            continue
        await _one(person, "BIRTHDAY",
                   birthday_tpl.replace("{preferred_name}", person["name"]))

    for person in due["anniversaries"]:
        if not person["may_message"]:
            skipped.append({**person, "why": "has not agreed to messages"})
            continue
        body = (anniversary_tpl
                .replace("{preferred_name}", person["name"])
                .replace("{years}", str(person["years"])))
        await _one(person, "WORK_ANNIVERSARY", body, years=person["years"])

    return {
        "date": today.isoformat(),
        "queued": len(queued),
        "prepared": queued,
        "skipped": skipped,
        "note": ("Queued into the same outbox as everything else, which "
                 "nothing drains. Running this again today adds nothing: the "
                 "database refuses a second greeting for the same person, "
                 "occasion and year."),
    }


async def consent_roster(session: AsyncSession) -> dict:
    """Every active member of staff and what they have agreed to.

    Separate from `calendar` on purpose. The calendar deliberately omits
    anybody who has not agreed to be listed -- which is precisely the people
    whose answer still needs recording, so it is the wrong source for this
    screen.
    """
    rows = (await session.execute(text("""
        SELECT s.id, s.employee_id, s.first_name, s.last_name, s.position,
               s.phone, s.display_hidden, s.hidden_reason,
               (s.date_of_birth IS NOT NULL) AS has_birthday,
               (s.hire_date IS NOT NULL)     AS has_hire_date,
               COALESCE(c.birthday_messages, FALSE)    AS birthday_messages,
               COALESCE(c.anniversary_messages, FALSE) AS anniversary_messages,
               COALESCE(c.visibility, 'NOBODY')        AS visibility,
               c.preferred_name, c.consent_source, c.consent_at
          FROM staff s
     LEFT JOIN staff_engagement_consent c ON c.staff_id = s.id
         WHERE s.is_active
         ORDER BY s.first_name, s.last_name
    """))).mappings().all()

    return {
        "staff": [{
            "staff_id": str(r["id"]), "employee_id": r["employee_id"],
            "name": f"{r['first_name']} {r['last_name']}",
            "position": r["position"] or "",
            "has_phone": bool(r["phone"]),
            "has_birthday": bool(r["has_birthday"]),
            "has_hire_date": bool(r["has_hire_date"]),
            "display_hidden": bool(r["display_hidden"]),
            "hidden_reason": r["hidden_reason"],
            "birthday_messages": bool(r["birthday_messages"]),
            "anniversary_messages": bool(r["anniversary_messages"]),
            "visibility": r["visibility"],
            "preferred_name": r["preferred_name"],
            "consent_source": r["consent_source"],
            "consent_at": (r["consent_at"].isoformat()
                           if r["consent_at"] else None),
        } for r in rows],
        "note": ("Everything starts at no. Ask each person before recording "
                 "an answer, and note that somebody hidden from staff "
                 "displays is never messaged whatever is set here."),
    }


async def history(session: AsyncSession, *, staff_id: UUID) -> dict:
    sends = (await session.execute(
        text("""SELECT occasion, occasion_year, years_of_service, status,
                       created_at
                  FROM staff_engagement_sends WHERE staff_id = :i
                 ORDER BY created_at DESC"""),
        {"i": str(staff_id)})).mappings().all()
    changes = (await session.execute(
        text("""SELECT change, detail, actor_name, created_at
                  FROM staff_engagement_events WHERE staff_id = :i
                 ORDER BY created_at DESC"""),
        {"i": str(staff_id)})).mappings().all()
    return {
        "sends": [{"occasion": s["occasion"], "year": s["occasion_year"],
                   "years_of_service": s["years_of_service"],
                   "status": s["status"],
                   "at": s["created_at"].isoformat()} for s in sends],
        "consent_changes": [{"change": c["change"], "detail": c["detail"],
                             "actor": c["actor_name"],
                             "at": c["created_at"].isoformat()}
                            for c in changes],
    }
