"""The field portal API. Distributor marketers, and nobody else.

THIS ROUTER IS MOUNTED OUTSIDE THE AUTHENTICATED BLOCK, and that is the point.
It does not accept a staff session, and a staff session cannot be used here --
authentication is a field token, signed with FIELD_PORTAL_SECRET, carrying
`typ: "field_marketer"`.

Two consequences worth stating plainly, because they are the security model:

  * A distributor's marketer holding a valid field token can reach exactly the
    routes in this file. There is no other router in the application that
    accepts their token, so there is no path from here into stock, payroll,
    production, pricing for other distributors, or the customer book.

  * `distributor_id` is never a parameter. Every route resolves the session
    into a `me` dict and the service reads the tenant from that. A marketer
    cannot request another distributor's data because the API has no way to
    express the request.

WHY THESE ARE NOT IN portal.py
==============================
The other public routers are unauthenticated by design -- a link IS the
credential. This one has real accounts, passwords, lockout and sessions, so it
gets its own file and its own entry in the permissions matrix rather than
being filed under "public".
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.services import field_portal as svc

router = APIRouter(prefix="/api/field", tags=["Field portal"])


def _client(request: Request) -> dict:
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() or (
        request.client.host if request.client else "")
    return {"ip": ip, "user_agent": request.headers.get("user-agent", "")}


async def current_marketer(
    authorization: Optional[str] = Header(None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Resolve the field session, or refuse.

    The account is re-read from the database on every request, so a marketer
    deactivated this morning stops working immediately rather than when their
    token happens to expire.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Please sign in.")
    return await svc.resolve_session(session, authorization.split(" ", 1)[1])


# ---------------------------------------------------------------------------
# Getting in
# ---------------------------------------------------------------------------

class RegisterIn(BaseModel):
    full_name: str = Field(..., min_length=3, max_length=160)
    phone: str = Field(..., min_length=7, max_length=40)
    password: str = Field(..., min_length=8, max_length=128)
    email: Optional[str] = None


class SignInIn(BaseModel):
    phone: str = Field(..., min_length=7, max_length=40)
    password: str = Field(..., min_length=1, max_length=128)


class ConsentIn(BaseModel):
    agreed: bool


@router.get("/join/{token}")
async def open_invite(token: str, session: AsyncSession = Depends(get_session)):
    """What this link is for. The distributor's name and nothing else."""
    invite = await svc.resolve_invite(session, token=token)
    return {
        "distributor": invite["distributor_name"],
        "note": ("Create your field account. It gives you your distributor's "
                 "price list and lets you register customers and log visits. "
                 "It is not an account on the Bonnesante Medicals system."),
    }


@router.post("/join/{token}", status_code=201)
async def register(
    token: str, body: RegisterIn, request: Request,
    session: AsyncSession = Depends(get_session),
):
    client = _client(request)
    result = await svc.register_from_invite(
        session, token=token, full_name=body.full_name, phone=body.phone,
        password=body.password, email=body.email, ip=client["ip"],
        user_agent=client["user_agent"])
    await session.commit()
    return result


@router.post("/sign-in")
async def sign_in(
    body: SignInIn, request: Request,
    session: AsyncSession = Depends(get_session),
):
    client = _client(request)
    result = await svc.sign_in(
        session, phone=body.phone, password=body.password, ip=client["ip"],
        user_agent=client["user_agent"])
    await session.commit()
    return result


@router.get("/me")
async def me(me: dict = Depends(current_marketer)):
    return {
        "name": me["name"],
        "distributor": me["distributor_name"],
        "consented": me["consented_at"] is not None,
        "consent_text": svc.CONSENT_TEXT,
        "location_retention_days": svc.LOCATION_RETENTION_DAYS,
    }


@router.post("/consent")
async def consent(
    body: ConsentIn, me: dict = Depends(current_marketer),
    session: AsyncSession = Depends(get_session),
):
    """Agree to, or withdraw agreement to, being located.

    Withdrawal is a button, not a request to somebody. It stops all future
    recording at once.
    """
    result = await svc.set_consent(session, me=me, agreed=body.agreed)
    await session.commit()
    return result


# ---------------------------------------------------------------------------
# The job
# ---------------------------------------------------------------------------

@router.get("/catalogue")
async def catalogue(
    me: dict = Depends(current_marketer),
    session: AsyncSession = Depends(get_session),
):
    """This distributor's products, at this distributor's prices."""
    return await svc.catalogue(session, me=me)


class CustomerIn(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    phone: Optional[str] = Field(None, max_length=40)
    email: Optional[str] = Field(None, max_length=255)
    address: Optional[str] = None


@router.post("/customers", status_code=201)
async def register_customer(
    body: CustomerIn, request: Request,
    me: dict = Depends(current_marketer),
    session: AsyncSession = Depends(get_session),
):
    """Add a customer to the company database, tagged to this marketer."""
    result = await svc.register_customer(
        session, me=me, name=body.name, phone=body.phone, email=body.email,
        address=body.address, ip=_client(request)["ip"])
    await session.commit()
    return result


@router.get("/customers")
async def my_customers(
    me: dict = Depends(current_marketer),
    session: AsyncSession = Depends(get_session),
):
    """Only the customers this marketer registered."""
    return {"customers": await svc.my_customers(session, me=me)}


class VisitIn(BaseModel):
    place_name: str = Field(..., min_length=2, max_length=255)
    purpose: str = "VISIT"
    customer_id: Optional[UUID] = None
    outcome: Optional[str] = None
    products_discussed: Optional[str] = None
    order_value: Optional[Decimal] = None
    follow_up_on: Optional[date] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    accuracy_m: Optional[float] = None
    address: Optional[str] = None


@router.post("/visits", status_code=201)
async def log_visit(
    body: VisitIn, me: dict = Depends(current_marketer),
    session: AsyncSession = Depends(get_session),
):
    result = await svc.log_visit(
        session, me=me, place_name=body.place_name, purpose=body.purpose,
        customer_id=body.customer_id, outcome=body.outcome,
        products_discussed=body.products_discussed,
        order_value=body.order_value, follow_up_on=body.follow_up_on,
        latitude=body.latitude, longitude=body.longitude,
        accuracy_m=body.accuracy_m, address=body.address)
    await session.commit()
    return result


@router.get("/visits")
async def my_visits(
    days: int = 30, me: dict = Depends(current_marketer),
    session: AsyncSession = Depends(get_session),
):
    return {"visits": await svc.my_visits(session, me=me, days=days)}


class PingIn(BaseModel):
    latitude: float
    longitude: float
    accuracy_m: Optional[float] = None
    speed_mps: Optional[float] = None
    working: bool = True


@router.post("/location")
async def record_location(
    body: PingIn, me: dict = Depends(current_marketer),
    session: AsyncSession = Depends(get_session),
):
    """One position. Refused outright unless the marketer has consented."""
    result = await svc.record_location(
        session, me=me, latitude=body.latitude, longitude=body.longitude,
        accuracy_m=body.accuracy_m, speed_mps=body.speed_mps,
        working=body.working)
    await session.commit()
    return result


@router.get("/trail")
async def my_trail(
    on: Optional[date] = None, me: dict = Depends(current_marketer),
    session: AsyncSession = Depends(get_session),
):
    """The marketer's own movements, for one day.

    Available to the marketer themselves, deliberately. Somebody whose
    position is recorded should be able to see what was recorded.
    """
    return {"points": await svc.my_trail(session, me=me, on=on)}


@router.get("/performance")
async def my_performance(
    days: int = 30, me: dict = Depends(current_marketer),
    session: AsyncSession = Depends(get_session),
):
    return await svc.my_performance(session, me=me, days=days)
