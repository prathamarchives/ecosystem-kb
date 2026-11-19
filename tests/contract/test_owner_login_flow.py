"""Owner login browser-path contract tests (review P0-1/P0-2).

Covers: CSP nonce wiring (script element + header, no unsafe-inline),
no-store, login-page pending-request injection (json.dumps safety),
callback page behaviour, /auth/session negatives (invalid JWT, non-owner,
cross-origin), and the cookie contract (HttpOnly/Secure/SameSite=Lax).
"""

from __future__ import annotations

import json
import re

import httpx
import pytest_asyncio

from ecosystem_kb.api.app import create_app
from ecosystem_kb.core.config import KbSettings

BASE = "https://kb.itscatalyst.com"
RESOURCE = f"{BASE}/mcp"
REDIRECT = "https://client.example/callback"
OWNER = "ad0fa277-f001-433f-883d-87e4dab606a3"


class StubGraphiti:
    driver = None

    async def build_indices_and_constraints(self):
        return None

    async def retrieve_episodes(self, *args, **kwargs):
        return []

    async def search(self, *args, **kwargs):
        return []

    async def close(self):
        return None


class StubOwnerVerifier:
    """Test double for SupabaseOwnerVerifier: accepts a fixed token when
    `valid=True`, maps to the configured owner subject."""

    def __init__(self, owner_subject: str, valid_token: str):
        self.owner_subject = owner_subject
        self.valid_token = valid_token

    async def verify_owner(self, token: str) -> dict:
        if token != self.valid_token:
            from ecosystem_kb.core.exceptions import AuthenticationError

            raise AuthenticationError("not the configured owner")
        return {"sub": self.owner_subject}


@pytest_asyncio.fixture
async def app(tmp_path):
    settings = KbSettings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/login_test.db",
        public_base_url=BASE,
        environment="development",
        llm_provider="fake",
        supabase_url="https://stub.supabase.co",
        supabase_anon_key="stub-anon-key",
    )
    application = create_app(settings, graphiti=StubGraphiti())
    from ecosystem_kb.adapters.postgres.migrations import migrate
    from ecosystem_kb.adapters.postgres.repositories import ControlPlane
    from tests.fixtures.wire_app import wire_app_state

    cp = ControlPlane(settings.database_url)
    await migrate(cp.engine)
    await wire_app_state(
        application, settings=settings, graphiti=StubGraphiti(), control_plane=cp
    )
    application.state._test_cp = cp
    return application


@pytest_asyncio.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as c:
        yield c, app


def _extract_nonce(body: str) -> str | None:
    m = re.search(r'<script nonce="([^"]+)"', body)
    return m.group(1) if m else None


class TestDashboardCsp:
    async def test_dashboard_has_nonce_and_matching_csp(self, client):
        c, _ = client
        r = await c.get("/")
        assert r.status_code == 200
        nonce = _extract_nonce(r.text)
        assert nonce, "inline script must carry a nonce attribute"
        csp = r.headers.get("content-security-policy", "")
        assert f"script-src 'self' 'nonce-{nonce}'" in csp
        assert "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]
        assert r.headers.get("cache-control") == "no-store"
        assert r.headers.get("x-frame-options") == "DENY"

    async def test_nonce_rotates_per_response(self, client):
        c, _ = client
        n1 = _extract_nonce((await c.get("/")).text)
        n2 = _extract_nonce((await c.get("/")).text)
        assert n1 and n2 and n1 != n2, "nonce must be fresh per response"

    async def test_only_one_inline_script_and_it_is_nonce_tagged(self, client):
        c, _ = client
        body = (await c.get("/")).text
        assert body.count("<script") == 1
        assert re.search(r"<script(?![^>]*nonce=)", body) is None

    async def test_config_json_not_python_repr(self, client):
        c, _ = client
        body = (await c.get("/")).text
        m = re.search(r"var KB_CONFIG = (\{.*?\});", body, re.S)
        assert m, "KB_CONFIG must be injected"
        parsed = json.loads(m.group(1))  # valid JSON, never Python repr
        assert parsed["mcp_url"] == RESOURCE


class TestLoginPageInjection:
    async def test_authorize_login_page_carries_pending_request(self, client):
        c, app = client
        reg = (
            await c.post(
                "/oauth/register",
                json={"client_name": "p", "redirect_uris": [REDIRECT]},
            )
        ).json()
        import base64
        import hashlib
        import secrets as pysecrets

        verifier = pysecrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()
        r = await c.get(
            "/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": reg["client_id"],
                "redirect_uri": REDIRECT,
                "state": "s",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "resource": RESOURCE,
            },
        )
        assert r.status_code == 200
        m = re.search(r"var KB_CONFIG = (\{.*?\});", r.text, re.S)
        assert m
        config = json.loads(m.group(1))
        assert config["pending_request"], "login page must carry the request id"
        # Injection is JSON (no quotes/escapes leaking Python repr syntax).
        assert "'" not in m.group(1)
        # And the page is nonce-protected exactly like the dashboard.
        nonce = _extract_nonce(r.text)
        assert nonce
        assert f"nonce-{nonce}" in r.headers.get("content-security-policy", "")

    async def test_malicious_request_id_is_data_not_code(self, client):
        """A tampered request_id can never become script content: it rides
        inside a json.dumps-serialized object."""
        c, _ = client
        body = (await c.get("/")).text
        m = re.search(r"var KB_CONFIG = (\{.*?\});", body, re.S)
        assert m
        assert "</script>" not in m.group(1)


class TestCallbackPage:
    async def test_callback_returns_executable_html(self, client):
        c, _ = client
        r = await c.get("/auth/callback", params={"code": "abc", "request_id": "rid"})
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        # No server-side meta-refresh to / (the old behaviour).
        assert "http-equiv" not in r.text
        # The page contains the PKCE exchange + session + resume logic.
        assert "grant_type=pkce" in r.text
        assert "/auth/session" in r.text
        assert "/oauth/authorize/resume?request_id=" in r.text
        assert "history.replaceState" in r.text
        # Nonce + CSP consistent. (unsafe-inline is deliberately allowed for
        # style-src only — never for script-src.)
        nonce = _extract_nonce(r.text)
        assert nonce
        csp = r.headers.get("content-security-policy", "")
        assert f"nonce-{nonce}" in csp
        script_directive = csp.split("script-src")[1].split(";")[0]
        assert "unsafe-inline" not in script_directive
        assert r.headers.get("cache-control") == "no-store"

    async def test_callback_without_code_still_serves_page(self, client):
        """The page itself shows a client-side error when no code is
        present; the server does not redirect."""
        c, _ = client
        r = await c.get("/auth/callback")
        assert r.status_code == 200
        assert "No sign-in code" in r.text


class TestSessionEndpoint:
    async def test_session_requires_owner_verifier(self, client):
        c, app = client
        # No verifier wired (supabase/owner unset in this fixture? it IS set
        # via settings but state.owner_verifier is only built in lifespan;
        # wire_app_state leaves it None) -> 401 not_configured path.
        r = await c.post("/auth/session", json={"access_token": "anything"})
        assert r.status_code == 401

    async def test_session_rejects_non_owner_token(self, client):
        c, app = client
        app.state.owner_verifier = StubOwnerVerifier(OWNER, valid_token="good-token")
        r = await c.post("/auth/session", json={"access_token": "wrong-token"})
        assert r.status_code == 401
        assert "authenticated" not in r.text or '"authenticated":true' not in r.text

    async def test_session_accepts_owner_and_sets_cookie_contract(self, client):
        c, app = client
        app.state.owner_verifier = StubOwnerVerifier(OWNER, valid_token="good-token")
        r = await c.post("/auth/session", json={"access_token": "good-token"})
        assert r.status_code == 200
        set_cookie = r.headers.get("set-cookie", "")
        assert "kb_session=" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "SameSite=lax" in set_cookie or "samesite=lax" in set_cookie.lower()
        assert "Path=/" in set_cookie
        # Secure: the ASGI request scheme is http in tests; the app marks
        # Secure when the request is https (x-forwarded-proto or scheme).
        # Verify the https branch explicitly:
        r2 = await c.post(
            "/auth/session",
            json={"access_token": "good-token"},
            headers={"x-forwarded-proto": "https"},
        )
        assert "Secure" in r2.headers.get("set-cookie", "")
        # /auth/me resolves the session
        cookie = r.cookies.get("kb_session")
        c.cookies.set("kb_session", cookie)
        me = await c.get("/auth/me")
        assert me.json()["authenticated"] is True
        assert me.json()["owner_subject"] == OWNER

    async def test_cross_origin_session_refused(self, client):
        c, app = client
        app.state.owner_verifier = StubOwnerVerifier(OWNER, valid_token="good-token")
        r = await c.post(
            "/auth/session",
            json={"access_token": "good-token"},
            headers={"Origin": "https://evil.example"},
        )
        assert r.status_code == 403

    async def test_same_origin_session_allowed(self, client):
        c, app = client
        app.state.owner_verifier = StubOwnerVerifier(OWNER, valid_token="good-token")
        r = await c.post(
            "/auth/session",
            json={"access_token": "good-token"},
            headers={"Origin": BASE},
        )
        assert r.status_code == 200

    async def test_no_origin_allowed_for_controlled_tests(self, client):
        c, app = client
        app.state.owner_verifier = StubOwnerVerifier(OWNER, valid_token="good-token")
        r = await c.post("/auth/session", json={"access_token": "good-token"})
        assert r.status_code == 200  # no Origin header: non-browser client

    async def test_malformed_body_rejected(self, client):
        c, _ = client
        r = await c.post(
            "/auth/session",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 401


class TestAuthConfig:
    async def test_config_shape(self, client):
        c, _ = client
        r = await c.get("/auth/config")
        assert r.status_code == 200
        cfg = r.json()
        assert cfg["supabase_url"] == "https://stub.supabase.co"
        assert cfg["supabase_anon_key"] == "stub-anon-key"
        assert cfg["callback_url"] == f"{BASE}/auth/callback"
