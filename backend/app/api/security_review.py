"""The permissions matrix, derived from the running application.

WHY THIS IS NOT A DOCUMENT
==========================
A permissions matrix written by hand is wrong the first time somebody adds a
route and does not update it, and nobody finds out until the wrong person reads
something. This endpoint walks the FastAPI app itself and reports what each
route actually requires, so the matrix cannot disagree with the code: it IS the
code.

It also names the routes that require nothing. Three do, all of them the
distributor ordering portal, and they are listed by name rather than counted --
an unauthenticated route should have to be looked at, not summarised into a
number somebody scrolls past.

`test_hardening.py` asserts that set is exactly those three, so adding a fourth
breaks the build rather than shipping quietly.

WHAT THIS ENDPOINT IS NOT
=========================
It is not a security guarantee. It reports what the dependencies say, which
catches a missing guard and does not catch a guard that is present and wrong --
an endpoint that checks authentication but not whether THIS user may see THAT
distributor's data still shows as guarded here. Those are read by a person; see
the known limitations returned with the matrix.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import (
    DISTRIBUTION_ROLES, require_admin, require_authenticated_user,
    require_distribution_access,
)
from app.db import get_session
from app.models import User

router = APIRouter(prefix="/api/security", tags=["Security review"])

# The routers this review covers. The rest of the ERP predates this work and is
# reported separately rather than silently included in a clean-looking total.
DISTRIBUTION_PREFIXES = (
    "/api/geography", "/api/distributors", "/api/portal", "/api/batches",
    "/api/downstream", "/api/performance", "/api/inbox", "/api/command-centre",
    "/api/recalls",
)

# Public BY DESIGN. Anything reaching this list has to be justified here, in
# the code, where a reviewer will see it.
EXPECTED_PUBLIC = {
    ("GET", "/api/portal/{token}"): (
        "The distributor opens their ordering page. The long random token in "
        "the path IS the credential: only its hash is stored, it expires, it "
        "is revocable, and every use and every miss is logged."),
    ("POST", "/api/portal/{token}/quote"): (
        "Prices the basket. Returns one total and no unit prices."),
    ("POST", "/api/portal/{token}/orders"): (
        "Places the order. Every price is looked up server-side; nothing about "
        "money is trusted from the client."),
}


def _callables(obj, depth: int = 0) -> set:
    """Every dependency callable reachable from a route or router, recursively.

    Recursive and identity-based, both deliberately:

    * `require_admin` is not a plain function. It is `require_roles("admin")`,
      a closure whose __name__ is `_guard`, and it nests
      `require_authenticated_user` beneath itself. Matching on names would miss
      every admin route and report it as merely authenticated -- the failure
      direction that matters.
    * The guard can sit several levels down when a router inherits it, so the
      tree is walked rather than only its top level.
    """
    if depth > 8:
        return set()
    found = set()
    dependant = getattr(obj, "dependant", None)
    if dependant is not None:
        found |= _callables(dependant, depth + 1)
    for dependency in getattr(obj, "dependencies", []) or []:
        call = getattr(dependency, "call", None) or getattr(
            dependency, "dependency", None)
        if call is not None:
            found.add(call)
        found |= _callables(dependency, depth + 1)
    return found


def _classify(calls: set) -> str:
    # Identity, not name: see _callables. Ordered narrowest first, because
    # require_distribution_access and require_admin both nest
    # require_authenticated_user beneath them -- checking the broad one first
    # would report every guarded route as merely authenticated.
    if require_admin in calls:
        return "ADMIN"
    if require_distribution_access in calls:
        return "DISTRIBUTION"
    if require_authenticated_user in calls:
        return "AUTHENTICATED"
    return "PUBLIC"


def _walk(app) -> list[dict]:
    """Every route with its EFFECTIVE guard, including inherited ones.

    Two traps here, both of which have already cost this application once:

    1. include_router() flattened routes into app.routes up to FastAPI 0.128;
       from 0.141 it appends a wrapper holding the router on `original_router`
       instead. Reading only app.routes finds almost nothing on the newer
       version -- main.py's own startup guard was broken by exactly this and
       refused to boot. Both shapes are walked.

    2. main.py applies most guards at the ROUTER level
       (`include_router(..., dependencies=[Depends(require_authenticated_user)])`),
       not on each function. A walk that reads only the route's own
       dependencies reports every one of those as PUBLIC -- which would make
       this report claim dozens of open endpoints that are in fact guarded, and
       a security report that cries wolf gets ignored. Guards are therefore
       inherited from every wrapper on the way down.
    """
    out, seen = [], set()

    def visit(routes, inherited: set):
        for route in routes:
            here = inherited | _callables(route)

            inner = getattr(route, "original_router", None)
            if inner is None:
                inner = getattr(route, "routes", None)
            if inner is not None:
                visit(getattr(inner, "routes", inner), here)
                continue

            path = getattr(route, "path", None)
            methods = getattr(route, "methods", None)
            if not path or not methods:
                continue
            for method in methods:
                if method in ("HEAD", "OPTIONS"):
                    continue
                key = (method, path)
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    "method": method, "path": path,
                    "requires": _classify(here),
                    "name": getattr(route, "name", ""),
                })

    visit(getattr(app, "routes", []), set())
    return sorted(out, key=lambda r: (r["path"], r["method"]))


@router.get("/permissions")
async def permissions_matrix(
    request: Request,
    scope: str = Query("distribution", pattern="^(distribution|all)$"),
    user: User = Depends(require_admin),
):
    """What every route actually requires, read from the running app.

    Not a document that can drift: this walks the application itself.
    """
    routes = _walk(request.app)
    if scope == "distribution":
        routes = [r for r in routes
                  if any(r["path"].startswith(p)
                         for p in DISTRIBUTION_PREFIXES)]

    public = [r for r in routes if r["requires"] == "PUBLIC"]
    for route in public:
        route["justification"] = EXPECTED_PUBLIC.get(
            (route["method"], route["path"]),
            "*** NOT IN THE EXPECTED LIST. Review this. ***")

    counts = {}
    for route in routes:
        counts[route["requires"]] = counts.get(route["requires"], 0) + 1

    unexpected = [r for r in public
                  if (r["method"], r["path"]) not in EXPECTED_PUBLIC]

    return {
        "scope": scope,
        "routes": routes,
        "counts": counts,
        # Named, never just counted. An unauthenticated route should have to be
        # looked at.
        "public_routes": public,
        "unexpected_public_routes": unexpected,
        "role_meanings": {
            "ADMIN": "role = admin only.",
            "DISTRIBUTION": ("A distributor record is commercial information. "
                             f"Restricted to: {', '.join(DISTRIBUTION_ROLES)}. "
                             "Marketers, production and warehouse staff are "
                             "deliberately excluded -- see app/api/auth.py."),
            "AUTHENTICATED": ("Any staff login. Batches and recalls sit here on "
                              "purpose: a picker has to know which batch to "
                              "take, and a recall has to be collected by "
                              "whoever is in the warehouse."),
            "PUBLIC": "No credential beyond the token in the URL.",
        },
        "known_limitations": [
            "This reports what the dependencies REQUIRE. It catches a missing "
            "guard; it does not catch a guard that is present and wrong.",
            "An endpoint that checks authentication but not whether this user "
            "may see this particular distributor's data appears here as "
            "guarded. Record-level access is not modelled: every authenticated "
            "user is company staff and can read any distributor's record.",
            "Evidence and document downloads are authenticated but not scoped "
            "to a distributor. Anyone with a staff login and a document id can "
            "fetch it.",
        ],
    }


@router.get("/audit")
async def audit_review(
    event_type: Optional[str] = None,
    entity_type: Optional[str] = None,
    limit: int = Query(200, ge=1, le=2000),
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """The distributor domain's audit trail, and proof it cannot be edited.

    The trail is only worth reading if it cannot be rewritten, so this returns
    the triggers that enforce that alongside the entries themselves.
    """
    clauses, params = ["1 = 1"], {"lim": limit}
    if event_type:
        clauses.append("a.event_type = :et")
        params["et"] = event_type
    if entity_type:
        clauses.append("a.entity_type = :ent")
        params["ent"] = entity_type

    rows = (await session.execute(
        text(f"""SELECT a.event_type, a.entity_type, a.reason, a.created_at,
                        a.ip_address, a.old_value, a.new_value,
                        COALESCE(u.full_name, a.actor_label) AS actor,
                        d.distributor_code, t.code AS territory
                   FROM distributor_audit_logs a
                   LEFT JOIN users u ON u.id = a.actor_user_id
                   LEFT JOIN distributors d ON d.id = a.distributor_id
                   LEFT JOIN territories t ON t.id = a.territory_id
                  WHERE {' AND '.join(clauses)}
                  ORDER BY a.created_at DESC LIMIT :lim"""),
        params)).mappings().all()

    summary = (await session.execute(
        text("""SELECT event_type, COUNT(*) AS n,
                       MAX(created_at) AS most_recent
                  FROM distributor_audit_logs
                 GROUP BY event_type ORDER BY n DESC"""))).mappings().all()

    # An append-only trail is worth exactly as much as the trigger enforcing
    # it, so the trigger is reported with the entries rather than assumed.
    immutability = (await session.execute(
        text("""SELECT c.relname AS table_name, t.tgname
                  FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
                 WHERE NOT t.tgisinternal
                   AND c.relname IN (
                       'distributor_audit_logs', 'batch_status_events',
                       'distributor_agreement_signatures',
                       'distributor_sale_evidence', 'recall_notifications',
                       'performance_periods', 'scheduled_job_runs')
                 ORDER BY c.relname"""))).mappings().all()

    return {
        "entries": [dict(r) for r in rows],
        "by_event_type": [dict(r) for r in summary],
        "append_only_protection": [dict(r) for r in immutability],
        "note": ("An audit trail is worth what the trigger protecting it is "
                 "worth, so the triggers are listed here rather than assumed. "
                 "If a table you expect is missing from that list, its history "
                 "can be edited."),
    }


@router.get("/immutability")
async def immutability_report(
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Which records in this module cannot be edited or deleted, and by what.

    Every one of these is a claim the rest of the system makes -- "signatures
    are append-only", "a submitted assessment freezes", "a recall's denominator
    is fixed". This is where somebody checks the claims are still true in the
    database they are actually running.
    """
    rows = (await session.execute(
        text("""SELECT c.relname AS table_name, t.tgname AS trigger_name,
                       p.proname AS function_name
                  FROM pg_trigger t
                  JOIN pg_class c ON c.oid = t.tgrelid
                  JOIN pg_proc p ON p.oid = t.tgfoid
                 WHERE NOT t.tgisinternal
                 ORDER BY c.relname, t.tgname"""))).mappings().all()

    constraints = (await session.execute(
        text("""SELECT conname, conrelid::regclass::text AS table_name
                  FROM pg_constraint
                 WHERE contype = 'c'
                   AND conrelid::regclass::text IN (
                       'product_recalls','product_complaints',
                       'distributor_sales','facility_assessments',
                       'distributor_agreements','product_batches',
                       'distributor_order_links','scheduled_job_runs',
                       'attention_acknowledgements',
                       'distributor_performance_reviews')
                 ORDER BY conrelid::regclass::text, conname"""),
    )).mappings().all()

    return {
        "triggers": [dict(r) for r in rows],
        "check_constraints": [dict(r) for r in constraints],
        "note": ("These are the guarantees the module's documentation claims. "
                 "If one is missing here, the claim is no longer true of this "
                 "database."),
    }
