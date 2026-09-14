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

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.services import portal as svc

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
