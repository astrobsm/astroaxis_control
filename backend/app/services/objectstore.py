"""DigitalOcean Spaces, signed by hand.

WHY NOT boto3
-------------
The production container bind-mounts ./backend and is never rebuilt by
deploy.ps1 -- uploading files and restarting is the whole deploy. Adding boto3
to requirements.txt would therefore install nothing, and `pip install` inside a
running container survives a restart but not a recreate, so the feature would
work until the next incident and then vanish with no obvious cause.

AWS Signature Version 4 is a published algorithm and needs only hmac, hashlib
and the httpx client already in the image. Roughly eighty lines, no new
dependency, and nothing to go wrong at deploy time. test_objectstore.py checks
the implementation against AWS's own published signing-key test vector, so a
mistake here fails loudly in CI rather than quietly at upload time.

WHAT IT IS FOR
--------------
Call recordings. They are large, they are personal data, and they must be
deletable on a schedule -- three reasons they do not belong in the database
next to the ledger. Playback uses a short-lived presigned URL so the audio goes
straight from Spaces to the listener's browser and never through the droplet,
which has under a gigabyte of RAM.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

import httpx

ALGORITHM = "AWS4-HMAC-SHA256"
SERVICE = "s3"

SPACES_KEY = os.getenv("SPACES_KEY", "")
SPACES_SECRET = os.getenv("SPACES_SECRET", "")
SPACES_REGION = os.getenv("SPACES_REGION", "nyc3")
SPACES_BUCKET = os.getenv("SPACES_BUCKET", "")
# Regional endpoint, without the bucket. e.g. https://nyc3.digitaloceanspaces.com
SPACES_ENDPOINT = (os.getenv("SPACES_ENDPOINT", "") or "").rstrip("/")

# Recordings larger than this are refused rather than streamed into memory.
# A 4-hour call at 1 MB/minute is ~240 MB; anything past that is a fault.
MAX_RECORDING_BYTES = 256 * 1024 * 1024


def configured() -> tuple[bool, Optional[str]]:
    missing = [n for n, v in (
        ("SPACES_KEY", SPACES_KEY), ("SPACES_SECRET", SPACES_SECRET),
        ("SPACES_BUCKET", SPACES_BUCKET), ("SPACES_ENDPOINT", SPACES_ENDPOINT),
    ) if not v]
    if missing:
        return False, f"Object storage is not configured: {', '.join(missing)}."
    if not SPACES_ENDPOINT.startswith("https://"):
        return False, "SPACES_ENDPOINT must be an https:// URL."
    return True, None


def _host() -> str:
    return SPACES_ENDPOINT.split("://", 1)[1]


def _encode_key(key: str) -> str:
    """Percent-encode an object key, keeping the slashes that make it a path."""
    return quote(key, safe="/~")


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def signing_key(secret: str, datestamp: str, region: str, service: str) -> bytes:
    """Derive the SigV4 signing key. Verified against AWS's published vector."""
    k_date = _sign(f"AWS4{secret}".encode("utf-8"), datestamp)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, service)
    return _sign(k_service, "aws4_request")


def _now() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# Presigned GET -- how a recording is played back
# ---------------------------------------------------------------------------

def presigned_get_url(key: str, *, expires_seconds: int = 300) -> str:
    """A short-lived URL for one object.

    Five minutes by default. Long enough to start playing a recording, short
    enough that a URL copied out of a browser's network tab is useless by the
    time anyone tries it. The link is the credential, so it is never stored and
    never logged.
    """
    ok, reason = configured()
    if not ok:
        raise RuntimeError(reason)
    if expires_seconds < 1 or expires_seconds > 3600:
        raise ValueError("expires_seconds must be between 1 and 3600.")

    amz_date, datestamp = _now()
    host = _host()
    canonical_uri = f"/{SPACES_BUCKET}/{_encode_key(key)}"
    credential = f"{SPACES_KEY}/{datestamp}/{SPACES_REGION}/{SERVICE}/aws4_request"

    # Query parameters must be sorted by name, and each name and value
    # percent-encoded, before the canonical request is built.
    params = {
        "X-Amz-Algorithm": ALGORITHM,
        "X-Amz-Credential": credential,
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(expires_seconds),
        "X-Amz-SignedHeaders": "host",
    }
    canonical_query = "&".join(
        f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}"
        for k, v in sorted(params.items()))

    canonical_request = "\n".join([
        "GET", canonical_uri, canonical_query,
        f"host:{host}\n", "host", "UNSIGNED-PAYLOAD",
    ])
    string_to_sign = "\n".join([
        ALGORITHM, amz_date,
        f"{datestamp}/{SPACES_REGION}/{SERVICE}/aws4_request",
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    signature = hmac.new(
        signing_key(SPACES_SECRET, datestamp, SPACES_REGION, SERVICE),
        string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    return (f"{SPACES_ENDPOINT}{canonical_uri}?{canonical_query}"
            f"&X-Amz-Signature={signature}")


# ---------------------------------------------------------------------------
# Authenticated requests -- upload and delete
# ---------------------------------------------------------------------------

def _auth_headers(method: str, key: str, payload: bytes,
                  extra: Optional[dict] = None) -> dict:
    amz_date, datestamp = _now()
    host = _host()
    canonical_uri = f"/{SPACES_BUCKET}/{_encode_key(key)}"
    payload_hash = hashlib.sha256(payload).hexdigest()

    headers = {"host": host, "x-amz-content-sha256": payload_hash,
               "x-amz-date": amz_date}
    if extra:
        headers.update({k.lower(): v for k, v in extra.items()})

    signed_headers = ";".join(sorted(headers))
    canonical_headers = "".join(
        f"{k}:{headers[k]}\n" for k in sorted(headers))

    canonical_request = "\n".join([
        method, canonical_uri, "", canonical_headers, signed_headers,
        payload_hash,
    ])
    string_to_sign = "\n".join([
        ALGORITHM, amz_date,
        f"{datestamp}/{SPACES_REGION}/{SERVICE}/aws4_request",
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    signature = hmac.new(
        signing_key(SPACES_SECRET, datestamp, SPACES_REGION, SERVICE),
        string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    out = dict(headers)
    out["Authorization"] = (
        f"{ALGORITHM} "
        f"Credential={SPACES_KEY}/{datestamp}/{SPACES_REGION}/{SERVICE}/aws4_request, "
        f"SignedHeaders={signed_headers}, Signature={signature}")
    return out


async def put_object(key: str, content: bytes, *,
                     content_type: str = "application/octet-stream") -> str:
    """Store an object. Returns the key. Private by default -- no public ACL."""
    ok, reason = configured()
    if not ok:
        raise RuntimeError(reason)
    if len(content) > MAX_RECORDING_BYTES:
        raise RuntimeError(
            f"Object is {len(content) / 1_048_576:.0f}MB, above the "
            f"{MAX_RECORDING_BYTES // 1_048_576}MB limit.")

    headers = _auth_headers(
        "PUT", key, content, extra={"content-type": content_type})
    url = f"{SPACES_ENDPOINT}/{SPACES_BUCKET}/{_encode_key(key)}"
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.put(url, content=content, headers=headers)
    if resp.status_code >= 300:
        raise RuntimeError(
            f"Upload failed (HTTP {resp.status_code}): {resp.text[:300]}")
    return key


async def delete_object(key: str) -> None:
    """Remove an object. Succeeds if it is already gone.

    Retention deletion must be idempotent: a sweep that fails because a file
    was already removed would stall every later deletion behind it.
    """
    ok, reason = configured()
    if not ok:
        raise RuntimeError(reason)
    headers = _auth_headers("DELETE", key, b"")
    url = f"{SPACES_ENDPOINT}/{SPACES_BUCKET}/{_encode_key(key)}"
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.delete(url, headers=headers)
    if resp.status_code not in (200, 202, 204, 404):
        raise RuntimeError(
            f"Delete failed (HTTP {resp.status_code}): {resp.text[:300]}")


async def fetch(url: str, *, auth: Optional[tuple[str, str]] = None) -> bytes:
    """Download a recording from the provider, refusing anything oversized.

    Streamed and checked as it arrives rather than read whole: a provider that
    returns something unexpected must not be able to exhaust the droplet's
    memory, and it has under a gigabyte.
    """
    buf = bytearray()
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        async with client.stream("GET", url, auth=auth) as resp:
            if resp.status_code >= 300:
                raise RuntimeError(
                    f"Could not fetch the recording (HTTP {resp.status_code}).")
            async for chunk in resp.aiter_bytes(256 * 1024):
                buf.extend(chunk)
                if len(buf) > MAX_RECORDING_BYTES:
                    raise RuntimeError(
                        f"Recording exceeds "
                        f"{MAX_RECORDING_BYTES // 1_048_576}MB; refused.")
    return bytes(buf)
