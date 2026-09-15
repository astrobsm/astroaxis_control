"""The distributor ordering portal. UNAUTHENTICATED BY DESIGN.

Registered in main.py's public block. A distributor opens a link and orders
without an account, so there is no bearer token to present -- the long random
token in the URL path IS the credential.

WHAT THAT COSTS, AND WHAT KEEPS IT HONEST
-----------------------------------------
Everything in this module is reachable by anyone on the internet who has the
URL. So:

* Only the hash of a token is stored, and `portal.resolve` is the single place a
  token is turned into a distributor. Every route goes through it.
* Every route is scoped to ONE distributor by that token. There is no route here
  that takes a distributor id, an order id, or anything else a caller could
  change to reach somebody else's data.
* Nothing here reads or writes anything but this distributor's catalogue view
  and their own new order. No invoices, no balances, no other distributors.
* Every refusal is logged, including tokens matching nothing -- a run of those
  is what guessing at links looks like.
* No price ever leaves this module except the one total for a basket the caller
  has already assembled. See app/services/portal.py for what that is and is not
  worth.

THIS ROUTER MUST NEVER GAIN AN ADMIN ROUTE. Managing links lives in
app/api/distributors.py behind require_admin.
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.services import portal as svc
from app.services import registration as reg

router = APIRouter(prefix="/api/portal", tags=["Distributor ordering portal"])


def _client(request: Request) -> dict:
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() or (
        request.client.host if request.client else "")
    return {"ip": ip, "user_agent": request.headers.get("user-agent", "")}


class BasketItem(BaseModel):
    product_id: str
    unit: str
    quantity: float = Field(..., gt=0)


class BasketIn(BaseModel):
    items: List[BasketItem]


class OrderIn(BaseModel):
    items: List[BasketItem]
    notes: Optional[str] = None
    required_date: Optional[date] = None


@router.get("/{token}")
async def open_portal(
    token: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Who this link orders for, and the catalogue -- without prices.

    The greeting carries the distributor's own name so the holder can see at
    once whether they have been sent the right link. It carries nothing else
    about the account.
    """
    link = await svc.resolve(session, token=token, log_open=True,
                             **_client(request))
    items = await svc.catalogue(session)
    await session.commit()
    return {
        "distributor": link["trading_name"] or link["legal_name"],
        "distributor_code": link["distributor_code"],
        "expires_at": link["expires_at"],
        "catalogue": items,
        "note": ("Choose what you need and the order value will be shown "
                 "before you send it."),
    }


@router.post("/{token}/quote")
async def quote(
    token: str,
    body: BasketIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """The value of the basket. One figure, for the whole order."""
    result = await svc.quote(session, token=token, items=body.items,
                             **_client(request))
    await session.commit()
    return result


@router.post("/{token}/orders", status_code=201)
async def place_order(
    token: str,
    body: OrderIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Place the order. Every price is looked up here, never sent by the client."""
    result = await svc.place_order(
        session, token=token, items=body.items, notes=body.notes,
        required_date=body.required_date, **_client(request))
    await session.commit()
    # The credit position is for the staff who confirm the order, not for the
    # person who just placed it -- being told "you are over your limit" by a
    # screen that cannot say what to pay or to whom helps nobody.
    result.pop("credit", None)
    return result


# ---------------------------------------------------------------------------
# Distributor self-registration -- ALSO UNAUTHENTICATED, and a different risk
#
# The ordering link above is a credential for ONE distributor's account. A
# registration link is shared widely so that anyone can APPLY, so it is a
# credential for nothing: every submission is an unverified claim from a
# stranger and lands in a review queue, never in the distributor register.
#
# These live in this router rather than a new one so that every public route in
# the system stays in one file, where the hardening test and anyone reviewing
# the permissions matrix will find them together.
# ---------------------------------------------------------------------------

class CustomerCheckIn(BaseModel):
    phone: str = Field(..., min_length=7, max_length=40)


class RegistrationIn(BaseModel):
    legal_name: str = Field(..., min_length=2, max_length=255)
    phone: str = Field(..., min_length=7, max_length=40)
    trading_name: Optional[str] = None
    entity_type: str = "COMPANY"
    contact_name: Optional[str] = None
    whatsapp: Optional[str] = None
    email: Optional[str] = None
    business_address: Optional[str] = None
    state_id: Optional[UUID] = None
    lga_id: Optional[UUID] = None
    town: Optional[str] = None
    cac_number: Optional[str] = None
    tin: Optional[str] = None
    years_in_operation: Optional[int] = Field(None, ge=0, le=200)
    business_type: Optional[str] = None
    employee_count: Optional[int] = Field(None, ge=0, le=100000)
    marketer_count: Optional[int] = Field(None, ge=0, le=100000)
    storage_description: Optional[str] = None
    products_of_interest: Optional[str] = None
    applicant_note: Optional[str] = None
    claims_existing_customer: bool = False
    claimed_customer_id: Optional[UUID] = None


@router.get("/register/{token}")
async def open_registration(
    token: str,
    session: AsyncSession = Depends(get_session),
):
    """Open the registration form, and the geography it needs.

    Returns states and LGAs because the form has to offer them. That is
    reference data -- the administrative map of Nigeria -- and carries nothing
    about the company or who it trades with.
    """
    link = await reg.resolve(session, token=token, count_view=True)
    states = await reg.states_with_availability(session)
    await session.commit()
    return {
        "campaign": link["campaign"],
        "states": states,
        "note": ("Applying does not create an account and does not let you "
                 "order. Somebody at Bonnesante Medicals reviews every "
                 "application."),
        "coverage_note": ("Areas already covered by a distributor, or by an "
                          "application we are reviewing, are shown but cannot "
                          "be selected."),
    }


@router.get("/register/{token}/lgas/{state_id}")
async def registration_lgas(
    token: str,
    state_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """The LGAs of one state, each marked available or already covered.

    Taken areas are returned rather than hidden, so the applicant can see that
    the area exists and is spoken for. WHY it is taken is never said -- naming
    the distributor who holds it would map the company's network for anyone
    holding a forwarded link.
    """
    await reg.resolve(session, token=token)
    return {"lgas": await reg.lgas_with_availability(session, state_id=state_id)}


@router.post("/register/{token}/check-customer")
async def check_customer(
    token: str,
    body: CustomerCheckIn,
    session: AsyncSession = Depends(get_session),
):
    """Does a FULL phone number match one existing account?

    Deliberately not a name search. A type-ahead over customer names on a
    public form is a way to export the customer list to anyone holding the
    link; this requires the applicant to already know the number and answers
    about at most one account, with the name masked.
    """
    result = await reg.confirm_existing_customer(
        session, token=token, phone=body.phone)
    await session.commit()
    return result


@router.post("/register/{token}", status_code=201)
async def submit_registration(
    token: str,
    body: RegistrationIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Send an application. Creates no distributor and grants nothing."""
    client = _client(request)
    result = await reg.submit(
        session, token=token, payload=body.model_dump(mode="json"),
        ip=client["ip"], user_agent=client["user_agent"])
    await session.commit()
    return result
