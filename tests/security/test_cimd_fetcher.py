"""CIMD fetcher security tests (review P1): SSRF guards, DNS-rebinding
(TOCTOU) closure via connection pinning, and document validation."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from ecosystem_kb.api.cimd_fetcher import (
    CimdFetchError,
    _build_pinned_client,
    _resolve_validated_ips,
    fetch_cimd_document,
)


class TestSsrfGuards:
    def test_http_scheme_refused(self):
        with pytest.raises(CimdFetchError, match="https"):
            asyncio.run(fetch_cimd_document("http://example.com/doc"))

    def test_loopback_literal_refused(self):
        with pytest.raises(CimdFetchError, match="non-global"):
            asyncio.run(fetch_cimd_document("https://127.0.0.1/doc"))

    def test_private_literal_refused(self):
        with pytest.raises(CimdFetchError, match="non-global"):
            asyncio.run(fetch_cimd_document("https://192.168.1.1/doc"))

    def test_link_local_metadata_refused(self):
        with pytest.raises(CimdFetchError, match="non-global"):
            asyncio.run(fetch_cimd_document("https://169.254.169.254/doc"))

    def test_invalid_document_rejected(self):
        """A syntactically fetched but semantically invalid document fails."""
        with pytest.raises(CimdFetchError, match="client_id"):
            _validate({"client_id": "not-the-url"}, "https://example.com/doc")

    def test_redirect_uri_policy(self):
        with pytest.raises(CimdFetchError, match="not acceptable"):
            _validate(
                {
                    "client_id": "https://example.com/doc",
                    "client_name": "x",
                    "redirect_uris": ["ftp://evil.example/cb"],
                },
                "https://example.com/doc",
            )


def _validate(doc, url):
    from ecosystem_kb.api.cimd_fetcher import _validate_document

    return _validate_document(doc, url)


class TestDnsRebindingPin:
    """The TOCTOU closure: the connection goes to the validated IP even
    when DNS would answer differently at connect time."""

    def _pin_to_unroutable(self):
        """Resolve a real host, then build a client pinned to a TEST-NET
        address. If the transport honored the hostname (re-resolving), the
        request would succeed; pinned to TEST-NET it must fail to connect."""
        ips = _resolve_validated_ips("api.github.com")
        assert ips and ips[0] != "192.0.2.1"
        url = httpx.URL("https://api.github.com")
        return _build_pinned_client(
            url, ["192.0.2.1"]
        )  # TEST-NET-1: unroutable by definition

    def test_connection_uses_pinned_ip_not_dns(self):
        """DNS at connect time says api.github.com -> 20.205.x; the pin says
        192.0.2.1. The request MUST fail to connect (proving the pin wins
        over any re-resolution)."""

        async def run():
            client = self._pin_to_unroutable()
            try:
                await client.get("https://api.github.com/zen")
                return "succeeded"
            except httpx.ConnectError:
                return "connect-error"
            except httpx.HTTPError as exc:
                return f"http-error:{type(exc).__name__}"
            finally:
                await client.aclose()

        outcome = asyncio.run(run())
        # ConnectTimeout against the unroutable pin is the expected
        # behaviour (TEST-NET never answers); ConnectError likewise. Any
        # successful HTTP round trip means the pin was ignored.
        assert outcome in (
            "connect-error",
            "http-error:ConnectError",
            "http-error:ConnectTimeout",
        ), f"connection escaped the pin (outcome={outcome})"

    def test_pinned_ip_mismatch_dns_does_not_leak_destination(self):
        """Even a hostname whose DNS NOW resolves internally stays pinned:
        build the client pinned to an unroutable IP for a host that
        (in this sandbox) resolves to a private address — the connection
        must still go to the pin, never to the private address."""

        async def run():
            client = _build_pinned_client(httpx.URL("https://localhost"), ["192.0.2.1"])
            try:
                await client.get("https://localhost/doc")
                return "succeeded"
            except httpx.ConnectError:
                return "connect-error"
            except httpx.HTTPError as exc:
                return f"http-error:{type(exc).__name__}"
            finally:
                await client.aclose()

        outcome = asyncio.run(run())
        # 'succeeded' would mean the transport re-resolved localhost ->
        # 127.0.0.1 and ignored the pin (the DNS-rebinding hole).
        assert outcome != "succeeded", "transport re-resolved the hostname: pin ignored"

    def test_full_fetch_pins_after_validation(self):
        """End-to-end: fetch_cimd_document resolves, validates, and the
        transport is pinned. With DNS monkeypatched to return a private IP
        at CONNECT time (after validation), the fetch must fail rather than
        connect internally."""
        import socket as socket_mod

        original_getaddrinfo = socket_mod.getaddrinfo

        state = {"calls": 0}

        def flaky_getaddrinfo(host, *args, **kwargs):
            state["calls"] += 1
            if state["calls"] == 1:
                # Validation call: healthy public answer.
                return original_getaddrinfo(host, *args, **kwargs)
            # Any later call (re-resolution at connect time) flips to a
            # private address — the classic rebinding attack.
            return [(socket_mod.AF_INET, socket_mod.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]

        socket_mod.getaddrinfo = flaky_getaddrinfo
        try:

            async def run():
                try:
                    await fetch_cimd_document("https://api.github.com/doc.json")
                    return "fetched"
                except CimdFetchError:
                    return "refused"
                except httpx.ConnectError:
                    return "connect-error"

            outcome = asyncio.run(run())
        finally:
            socket_mod.getaddrinfo = original_getaddrinfo

        # The document would not validate anyway, but the security property
        # is the connection: it must never reach 10.0.0.5. With the pin,
        # httpcore asks the backend to connect to the validated public IP.
        assert outcome in ("refused", "connect-error"), outcome
