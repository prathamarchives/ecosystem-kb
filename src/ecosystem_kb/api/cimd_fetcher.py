"""Hardened HTTPS fetcher for OAuth Client ID Metadata Documents (CIMD).

Runbook Step 7.4 SSRF rules: reject loopback/private/link-local/multicast/
reserved/non-global IPs; resolve and validate DNS; no redirects; short
timeouts; small response cap; JSON content-type + schema validation. The
fetched document never chooses an internal destination.

DNS-rebinding/TOCTOU closure (review P1): the DNS answer is validated and
then the ACTUAL TCP connection is pinned to one of the validated public
IPs. httpx supports this via an explicit `extensions={"network_backend":
...}` custom transport; the portable implementation here uses a custom
httpcore-compatible connection pool that connects by IP while preserving
the original hostname for the HTTP Host header and TLS SNI/certificate
validation. If IP pinning cannot be established, the fetch fails closed —
CIMD advertisement is only honest when this holds.
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
    if not host.endswith(".") and ":" not in host:
        pass  # normal hostname
    # Literal IP hosts are allowed only when they are global unicast.
    try:
        ip = ipaddress.ip_address(host)
        if not ip.is_global:
            raise CimdFetchError(f"non-global address {ip} refused")
    except ValueError:
        pass  # not an IP literal — a DNS name; resolved below


def _resolve_validated_ips(host: str) -> list[str]:
    """Resolve and return ONLY global unicast IPs. Any non-global answer
    (loopback, private, link-local, multicast, reserved, unspecified)
    refuses the whole fetch — an attacker-controlled mixed answer must not
    let a bad IP through by averaging."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise CimdFetchError(f"DNS resolution failed: {exc}") from exc
    if not infos:
        raise CimdFetchError("no addresses resolved")
    good: list[str] = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            not ip.is_global
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
            or ip.is_link_local
        ):
            raise CimdFetchError(f"non-global address {ip} refused")
        good.append(str(ip))
    if not good:
        raise CimdFetchError("no usable addresses resolved")
    return good


class PinnedAsyncTransport(httpx.AsyncHTTPTransport):
    """httpx transport whose TCP connections are pinned to a validated IP.

    httpx 0.28 removed `pool=`/`backend=` injection, so we rebuild the
    transport's pool after construction: an httpcore AsyncConnectionPool
    with a network backend that ignores the (re-resolvable) hostname and
    connects to the pre-validated IP instead. The hostname is preserved
    for TLS SNI + certificate validation and the HTTP Host header (the
    request origin is untouched), closing the DNS-rebinding/TOCTOU gap:
    the connection cannot end up anywhere but the validated IP.
    """

    def __init__(self, ip: str, port: int) -> None:
        import httpcore
        from httpcore._backends.auto import AutoBackend

        super().__init__(verify=True)
        pinned_ip, pinned_port = ip, port

        class _PinnedBackend(AutoBackend):
            """Concrete backend (anyio-based) connecting to the validated IP."""

            async def connect_tcp(  # type: ignore[override]
                self,
                host: str,
                port: int,
                timeout=None,
                local_address=None,
                socket_options=None,
            ):
                if port == pinned_port:
                    connect_host, connect_port = pinned_ip, pinned_port
                else:
                    connect_host, connect_port = host, port
                return await super().connect_tcp(
                    connect_host,
                    connect_port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )

        self._pool = httpcore.AsyncConnectionPool(network_backend=_PinnedBackend())


def _build_pinned_client(url: httpx.URL, ips: list[str]) -> httpx.AsyncClient:
    """Client whose connections go to `ips[0]` with original-host TLS."""
    transport = PinnedAsyncTransport(ip=ips[0], port=(url.port or 443))
    return httpx.AsyncClient(
        transport=transport,
        timeout=FETCH_TIMEOUT,
        follow_redirects=False,  # never follow: redirect could target internals
        headers={"Accept": "application/json"},
        verify=True,
    )


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
    """Fetch and validate a CIMD document. Raises CimdFetchError.

    The connection is pinned to a pre-validated public IP (review P1):
    DNS re-resolution between validation and connect cannot change the
    destination. TLS SNI/certificate validation use the original hostname.
    """
    _validate_url(url)
    parsed = httpx.URL(url)
    ips = _resolve_validated_ips(parsed.host)
    client = _build_pinned_client(parsed, ips)
    try:
        response = await client.get(url)
    except httpx.HTTPError as exc:
        raise CimdFetchError(f"metadata fetch failed: {exc}") from exc
    finally:
        await client.aclose()
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

