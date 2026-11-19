"""Hardened HTTPS fetcher for OAuth Client ID Metadata Documents (CIMD).

Runbook Step 7.4 SSRF rules: reject loopback/private/link-local/multicast/
reserved/non-global IPs; resolve and validate DNS; no redirects; short
timeouts; small response cap; JSON content-type + schema validation. The
fetched document never chooses an internal destination.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any

import httpx

MAX_DOCUMENT_BYTES = 64 * 1024
FETCH_TIMEOUT = 10.0
ALLOWED_SCHEMES = ("https",)


class CimdFetchError(ValueError):
    pass


def _validate_url(url: str) -> None:
    if not url.startswith("https://"):
        raise CimdFetchError("client_id metadata URL must be https")
    try:
        host = httpx.URL(url).host
    except Exception as exc:  # noqa: BLE001
        raise CimdFetchError(f"unparseable URL: {exc}") from exc
    if not host:
        raise CimdFetchError("URL has no host")


def _resolve_and_validate(host: str) -> None:
    """Every resolved address must be a global unicast IP (SSRF guard)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise CimdFetchError(f"DNS resolution failed: {exc}") from exc
    if not infos:
        raise CimdFetchError("no addresses resolved")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise CimdFetchError(f"non-global address {ip} refused")


def _validate_document(doc: Any, url: str) -> dict:
    if not isinstance(doc, dict):
        raise CimdFetchError("document must be a JSON object")
    if doc.get("client_id") != url:
        raise CimdFetchError("document client_id does not match its URL")
    name = doc.get("client_name")
    if not isinstance(name, str) or not name.strip():
        raise CimdFetchError("client_name missing")
    uris = doc.get("redirect_uris")
    if not isinstance(uris, list) or not uris:
        raise CimdFetchError("redirect_uris missing")
    for uri in uris:
        if not isinstance(uri, str) or not uri.startswith(
            ("https://", "http://localhost:", "http://127.0.0.1:")
        ):
            raise CimdFetchError(f"redirect URI not acceptable: {uri!r}")
    grants = doc.get("grant_types", ["authorization_code", "refresh_token"])
    if any(g not in ("authorization_code", "refresh_token") for g in grants):
        raise CimdFetchError("unsupported grant_types in document")
    responses = doc.get("response_types", ["code"])
    if responses != ["code"]:
        raise CimdFetchError("only response_types=['code'] is supported")
    auth_method = doc.get("token_endpoint_auth_method", "none")
    if auth_method != "none":
        raise CimdFetchError("only public clients (auth method none) are supported")
    return doc


async def fetch_cimd_document(url: str) -> dict:
    """Fetch and validate a CIMD document. Raises CimdFetchError."""
    _validate_url(url)
    parsed = httpx.URL(url)
    _resolve_and_validate(parsed.host)
    async with httpx.AsyncClient(
        timeout=FETCH_TIMEOUT,
        follow_redirects=False,  # never follow: redirect could target internals
        headers={"Accept": "application/json"},
    ) as client:
        response = await client.get(url)
    if response.status_code != 200:
        raise CimdFetchError(f"metadata URL returned HTTP {response.status_code}")
    if len(response.content) > MAX_DOCUMENT_BYTES:
        raise CimdFetchError("metadata document too large")
    content_type = response.headers.get("content-type", "")
    if "application/json" not in content_type:
        raise CimdFetchError(f"unexpected content type {content_type!r}")
    try:
        doc = response.json()
    except ValueError as exc:
        raise CimdFetchError(f"invalid JSON: {exc}") from exc
    return _validate_document(doc, url)
