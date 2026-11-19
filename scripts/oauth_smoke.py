"""End-to-end OAuth protocol smoke test against a live app instance.

Exercises: metadata, DCR registration, authorization request, pending
survival, consent approve, code exchange with PKCE, resource binding,
MCP bearer validation, refresh rotation, replay detection, revocation,
and the canonical /mcp no-redirect behaviour.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import sys
import tempfile

os.environ.update(
    {
        "KB_DATABASE_URL": "",  # set below per-temp-db
        "KB_PUBLIC_BASE_URL": "https://kb.itscatalyst.com",
        "KB_ENVIRONMENT": "development",
        "KB_LLM_PROVIDER": "fake",
    }
)
import pathlib

tmp = tempfile.mkdtemp()
os.environ["KB_DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp}/smoke.db"
pathlib.Path("./data").mkdir(exist_ok=True)

from fastapi.testclient import TestClient  # noqa: E402

from ecosystem_kb.api.app import create_app  # noqa: E402
from ecosystem_kb.core.config import KbSettings  # noqa: E402

RESOURCE = "https://kb.itscatalyst.com/mcp"

passed, failed = [], []


def check(name, condition, detail=""):
    if condition:
        passed.append(name)
        print(f"  ok  {name}")
    else:
        failed.append((name, detail))
        print(f" FAIL {name} {detail}")


def make_client():
    settings = KbSettings(
        database_url=os.environ["KB_DATABASE_URL"],
        public_base_url="https://kb.itscatalyst.com",
        environment="development",
    )
    return create_app(settings, graphiti=_StubGraphiti())


class _StubGraphiti:
    """Minimal graph stub: the OAuth smoke test exercises protocol paths,
    not graph writes. search returns nothing; build/close are no-ops."""

    driver = None

    async def build_indices_and_constraints(self):
        return None

    async def retrieve_episodes(self, *args, **kwargs):
        return []

    async def search(self, *args, **kwargs):
        return []

    async def close(self):
        return None


def pkce():
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def main() -> int:
    app = make_client()
    with TestClient(app, base_url="https://kb.itscatalyst.com") as client:
        # -- metadata ------------------------------------------------------
        r = client.get("/.well-known/oauth-protected-resource/mcp")
        check("PRM 200", r.status_code == 200, r.status_code)
        check("PRM resource", r.json().get("resource") == RESOURCE)
        r = client.get("/.well-known/oauth-authorization-server")
        check("ASM 200", r.status_code == 200)
        meta = r.json()
        check("ASM issuer", meta.get("issuer") == "https://kb.itscatalyst.com")
        check("ASM S256 only", meta.get("code_challenge_methods_supported") == ["S256"])
        check("ASM auth none", meta.get("token_endpoint_auth_methods_supported") == ["none"])
        check("ASM CIMD not advertised", "client_id_metadata_document_supported" not in meta)

        # -- canonical /mcp: no redirect ----------------------------------
        r = client.get("/mcp")
        check("MCP 401 no redirect", r.status_code == 401, f"{r.status_code} {r.headers.get('location')}")
        check(
            "MCP WWW-Authenticate",
            "resource_metadata" in r.headers.get("www-authenticate", ""),
            r.headers.get("www-authenticate"),
        )

        # -- DCR ------------------------------------------------------------
        r = client.post(
            "/oauth/register",
            json={
                "client_name": "smoke-test-client",
                "redirect_uris": ["https://client.example/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "token_endpoint_auth_method": "none",
            },
        )
        check("DCR 201", r.status_code == 201, r.text[:120])
        reg = r.json()
        check("DCR client_id", bool(reg.get("client_id")))
        client_id = reg["client_id"]

        # -- authorization request (no session -> login page) ---------------
        verifier, challenge = pkce()
        r = client.get(
            "/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": "https://client.example/callback",
                "state": "st4te",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "resource": RESOURCE,
                "scope": "kb:read kb:write offline_access",
            },
            follow_redirects=False,
        )
        check("authorize shows login", r.status_code == 200, r.status_code)
        import json as _json
        import re

        m = re.search(r"var KB_CONFIG = (\{.*?\});", r.text, re.S)
        request_id = _json.loads(m.group(1))["pending_request"] if m else None
        check("login page carries pending request_id", request_id is not None)

        # pending survives (JSON status endpoint)
        r = client.get("/oauth/consent", params={"request_id": request_id})
        check("pending survives login round trip", r.json().get("pending") is True, r.text[:100])

        # -- owner session: fabricate directly through the repository -------
        sessions = app.state.oauth_sessions
        import asyncio

        async def _session():
            return await sessions.create("owner-smoke", ttl_days=7)

        cookie_value = asyncio.get_event_loop().run_until_complete(_session())
        client.cookies.set("kb_session", cookie_value)

        # -- resume after login -> consent page ------------------------------
        r = client.get(
            "/oauth/authorize/resume", params={"request_id": request_id}, follow_redirects=False
        )
        check("resume shows consent", r.status_code == 200, r.status_code)
        m = re.search(r'name="request_id" value="([^"]+)"', r.text)
        consent_request_id = m.group(1) if m else None
        check("consent page has request_id", consent_request_id is not None)
        request_id = consent_request_id or request_id

        # -- approve consent ------------------------------------------------
        r = client.post(
            "/oauth/authorize/decide",
            data={"request_id": request_id, "decision": "approve"},
            follow_redirects=False,
        )
        check("decide 302", r.status_code == 302, r.status_code)
        location = r.headers.get("location", "")
        check("decide to redirect_uri", location.startswith("https://client.example/callback"), location)
        check("decide has code", "code=" in location)
        check("decide has state", "state=st4te" in location)
        code = location.split("code=")[1].split("&")[0]

        # -- token exchange --------------------------------------------------
        r = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "https://client.example/callback",
                "client_id": client_id,
                "code_verifier": verifier,
                "resource": RESOURCE,
            },
        )
        check("token 200", r.status_code == 200, r.text[:200])
        tokens = r.json()
        check("token access", bool(tokens.get("access_token")))
        check("token refresh", bool(tokens.get("refresh_token")))
        check("token type Bearer", tokens.get("token_type") == "Bearer")
        access = tokens["access_token"]
        refresh = tokens["refresh_token"]

        # -- code reuse fails ------------------------------------------------
        r = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "https://client.example/callback",
                "client_id": client_id,
                "code_verifier": verifier,
                "resource": RESOURCE,
            },
        )
        check("code reuse rejected", r.status_code == 400 and r.json().get("error") == "invalid_grant", r.text[:100])

        # -- MCP with the access token ---------------------------------------
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "0"}}},
            headers={"Authorization": f"Bearer {access}"},
        )
        check("MCP initialize with OAuth token", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

        # cookie-only MCP: refused
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "0"}}},
        )
        check("MCP cookie-only refused", r.status_code == 401, r.status_code)

        # -- wrong resource on refresh --------------------------------------
        r = client.post(
            "/oauth/token",
            data={"grant_type": "refresh_token", "refresh_token": refresh, "resource": "https://other.example/api"},
        )
        check("refresh wrong resource", r.status_code == 400, r.text[:100])

        # -- refresh rotation + replay ---------------------------------------
        r = client.post(
            "/oauth/token",
            data={"grant_type": "refresh_token", "refresh_token": refresh, "resource": RESOURCE, "client_id": client_id},
        )
        check("refresh 200", r.status_code == 200, r.text[:150])
        new_refresh = r.json().get("refresh_token")
        check("refresh rotated", new_refresh and new_refresh != refresh)

        # replay of the old refresh token -> family revoked
        r = client.post(
            "/oauth/token",
            data={"grant_type": "refresh_token", "refresh_token": refresh, "resource": RESOURCE, "client_id": client_id},
        )
        check("replay rejected", r.status_code == 400, r.text[:100])

        # the replay revoked the family: new_refresh must now fail too
        r = client.post(
            "/oauth/token",
            data={"grant_type": "refresh_token", "refresh_token": new_refresh, "resource": RESOURCE, "client_id": client_id},
        )
        check("family revoked after replay", r.status_code == 400, r.text[:100])

        # access token from that family should be dead as well
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "0"}}},
            headers={"Authorization": f"Bearer {access}"},
        )
        check("access dead after family revoke", r.status_code == 401, r.status_code)

        # -- dashboard endpoints ---------------------------------------------
        r = client.get("/auth/me")
        check("auth/me", r.status_code == 200 and r.json().get("authenticated") is True, r.text[:100])
        r = client.get("/v1/clients")
        check("dashboard clients", r.status_code == 200, r.text[:150])
        clients = r.json().get("clients", [])
        check("one client listed", len(clients) == 1, len(clients))
        r = client.get("/")
        check("dashboard page", r.status_code == 200 and "ecosystem-kb" in r.text)
        r = client.get("/v1/status")
        check("status endpoint", r.status_code == 200, r.text[:150])

        # -- logout -----------------------------------------------------------
        r = client.post("/auth/logout")
        check("logout", r.status_code == 200)
        r = client.get("/auth/me")
        check("post-logout me", r.json().get("authenticated") is False)

    print()
    print(f"passed={len(passed)} failed={len(failed)}")
    for name, detail in failed:
        print(f"  FAILED: {name} — {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
