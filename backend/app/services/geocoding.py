"""Reverse-geocoding service using OpenStreetMap Nominatim.

Free, no API key required. Respects Nominatim usage policy:
- Sets a descriptive User-Agent
- Caches results in-memory (rounded to 4 decimals ≈ 11m) to avoid duplicate hits
- Hard timeout of 4 s; failures return None (best-effort)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional, Tuple

import httpx

_LOG = logging.getLogger("services.geocoding")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "AstroAxis-ERP/1.0 (admin@bonnesantemedicals.com)"

# Simple in-process cache: { (lat4, lng4): address }
_CACHE: dict[Tuple[float, float], str] = {}
_CACHE_MAX = 4096
_LOCK = asyncio.Lock()


def _round_key(lat: float, lng: float) -> Tuple[float, float]:
    return (round(float(lat), 4), round(float(lng), 4))


async def reverse_geocode(lat: Optional[float], lng: Optional[float]) -> Optional[str]:
    """Return a human-readable address for the given coordinates, or None.

    Best-effort: any error / timeout / missing input returns None.
    """
    if lat is None or lng is None:
        return None
    try:
        lat_f = float(lat)
        lng_f = float(lng)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat_f <= 90 and -180 <= lng_f <= 180):
        return None

    key = _round_key(lat_f, lng_f)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    params = {
        "format": "jsonv2",
        "lat": f"{lat_f:.6f}",
        "lon": f"{lng_f:.6f}",
        "zoom": "18",
        "addressdetails": "1",
    }
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en"}
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.get(NOMINATIM_URL, params=params, headers=headers)
            if r.status_code != 200:
                return None
            data = r.json()
        address = data.get("display_name") if isinstance(data, dict) else None
        if not address:
            return None
        # Bound cache size
        async with _LOCK:
            if len(_CACHE) >= _CACHE_MAX:
                # Evict ~25% oldest entries (insertion order)
                for k in list(_CACHE.keys())[: _CACHE_MAX // 4]:
                    _CACHE.pop(k, None)
            _CACHE[key] = address
        return address
    except (httpx.HTTPError, ValueError, asyncio.TimeoutError) as exc:
        _LOG.debug("reverse_geocode failed for %s,%s: %s", lat_f, lng_f, exc)
        return None
