"""Browser E2E: owner login → Supabase PKCE → session → OAuth resume →
consent (review-required test).

Runs a REAL Chromium (Playwright) against:
  - the FastAPI app on a real uvicorn server (lifespan active), and
  - a mocked Supabase Auth served from the SAME origin under a reserved
    path prefix (/__supabase_mock/...) — the app's /auth/config points the
    browser there, so every fetch the page makes is observable.

The mock implements the two Supabase endpoints the flow needs:
  POST /auth/v1/otp?redirect_to=...   — records the PKCE challenge, returns
                                        a magic-link URL we hand to the test
  POST /auth/v1/token?grant_type=pkce — verifies the code_verifier against
                                        the recorded challenge (REAL PKCE
                                        check) and returns a signed-ish
                                        access token (the app's verifier is
                                        stubbed to accept it)

Flow exercised (review P0-2 acceptance):
  dashboard login form → Web Crypto verifier → localStorage → otp request
  → email link (constructed) → /auth/callback?code=... → grant_type=pkce
  exchange with verifier → POST /auth/session → kb_session cookie →
  /oauth/authorize/resume?request_id → consent page.

Skips automatically when Playwright/Chromium is unavailable (CI without
browsers); the contract tests still cover the protocol-level assertions.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import threading

import httpx
import pytest

# NOTE: `from __future__ import annotations` makes FastAPI resolve parameter
# annotations in the MODULE globals of the defining function — a Request
# imported only inside _build_app is invisible to the route decorators below.
# Keep this import at module level.
from fastapi import Request  # noqa: E402

pytest.importorskip("playwright")
from playwright.async_api import async_playwright  # noqa: E402

BASE = "https://kb.itscatalyst.com"
RESOURCE = f"{BASE}/mcp"
REDIRECT = "https://client.example/callback"
OWNER = "ad0fa277-f001-433f-883d-87e4dab606a3"
# Same-origin mock under a reserved path. The browser reaches the test
# server through the host-resolver mapping (http, port 8899), so the
# advertised Supabase URL uses the test origin, not the https production one.
SUPABASE_MOCK = "https://kb.itscatalyst.com:8899/__supabase_mock"


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
    """Accepts exactly the token the mock issues."""

    def __init__(self, owner_subject: str, valid_token: str):
        self.owner_subject = owner_subject
        self.valid_token = valid_token

    async def verify_owner(self, token: str) -> dict:
        if token != self.valid_token:
            from ecosystem_kb.core.exceptions import AuthenticationError

            raise AuthenticationError("not the configured owner")
        return {"sub": self.owner_subject}


class SupabaseMockState:
    """Records the PKCE challenge from /otp; issues a one-time code;
    verifies the verifier at /token (real S256 comparison)."""

    def __init__(self):
        self.challenge: str | None = None
        self.redirect_to: str | None = None
        self.code: str | None = None
        self.issued_token = "mock-supabase-owner-access-token"
        self.exchanges: list[dict] = []

    def reset(self):
        self.challenge = None
        self.redirect_to = None
        self.code = None


def _build_app(tmp_path, mock_state: SupabaseMockState):
    """App whose /auth/config points at the same-origin mock."""
    from ecosystem_kb.api.app import create_app
    from ecosystem_kb.core.config import KbSettings

    settings = KbSettings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/e2e.db",
        public_base_url=BASE,
        environment="development",
        llm_provider="fake",
        supabase_url=SUPABASE_MOCK,
        supabase_anon_key="mock-anon-key",
    )
    app = create_app(settings, graphiti=StubGraphiti())

    # Stub the owner verifier (JWT crypto is not this test's subject; PKCE
    # and the browser flow are).
    app_state_verifier = StubOwnerVerifier(OWNER, mock_state.issued_token)

    real_lifespan = app.router.lifespan_context

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def patched_lifespan(a):
        async with real_lifespan(a):
            a.state.owner_verifier = app_state_verifier
            yield

    app.router.lifespan_context = patched_lifespan

    # -- mount the mock Supabase Auth API on the same origin --------------
    from fastapi.responses import JSONResponse

    @app.post("/__supabase_mock/auth/v1/otp")
    async def otp(request: Request):
        body = await request.json()
        mock_state.challenge = body.get("code_challenge")
        mock_state.redirect_to = request.query_params.get("redirect_to")
        mock_state.code = "otpcode-" + secrets.token_urlsafe(16)
        # Supabase would email a link; we expose it to the test through
        # the response of a debug endpoint instead of sending mail.
        return JSONResponse({})

    @app.get("/__supabase_mock/__test/link")
    async def test_link():
        if not mock_state.redirect_to or not mock_state.code:
            return JSONResponse({"error": "no otp requested"}, status_code=400)
        sep = "&" if "?" in mock_state.redirect_to else "?"
        return JSONResponse({"link": f"{mock_state.redirect_to}{sep}code={mock_state.code}"})

    @app.post("/__supabase_mock/auth/v1/token")
    async def token(request: Request):
        body = await request.json()
        verifier = body.get("code_verifier") or ""
        mock_state.exchanges.append({"code": body.get("auth_code"), "verifier": verifier})
        # REAL PKCE verification: S256(verifier) must equal the recorded
        # challenge from the /otp call.
        digest = hashlib.sha256(verifier.encode()).digest()
        computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        if body.get("auth_code") != mock_state.code or computed != mock_state.challenge:
            return JSONResponse(
                {"msg": "invalid grant: PKCE verification failed"}, status_code=400
            )
        return JSONResponse(
            {
                "access_token": mock_state.issued_token,
                "token_type": "bearer",
                "expires_in": 3600,
            }
        )

    return app


@pytest.mark.e2e
async def test_browser_login_to_consent(tmp_path):
    # Skip cleanly when the Chromium browser binary is absent (CI without
    # playwright install): the contract tests still cover the protocol.
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as _p:
            _p.chromium.launch().close()
    except Exception as skip_reason:  # noqa: BLE001
        pytest.skip(f"chromium unavailable: {skip_reason}")

    mock_state = SupabaseMockState()
    app = _build_app(tmp_path, mock_state)

    # Web Crypto (crypto.subtle) is only available in secure contexts:
    # the test server must be https (self-signed; Chromium ignores cert
    # errors via launch arg). This mirrors production, where the origin is
    # https through the Cloudflare Tunnel.
    from pathlib import Path

    import uvicorn

    certs = Path(__file__).parent / "certs"
    ORIGIN = "https://kb.itscatalyst.com:8899"
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=8899,
        log_level="error",
        ssl_certfile=str(certs / "cert.pem"),
        ssl_keyfile=str(certs / "key.pem"),
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            try:
                if httpx.get("https://127.0.0.1:8899/health", verify=False, timeout=1).status_code == 200:
                    break
            except Exception:
                await asyncio.sleep(0.1)

        async with async_playwright() as p:
            # Map the canonical hostname onto the loopback test server so
            # the page's same-origin fetches (including the /auth/config
            # URLs) and the kb_session cookie all live on ONE origin.
            browser = await p.chromium.launch(
                args=[
                    "--host-resolver-rules=MAP kb.itscatalyst.com 127.0.0.1, MAP client.example 127.0.0.1",
                    "--ignore-certificate-errors",
                ]
            )
            context = await browser.new_context(ignore_https_errors=True)
            page = await context.new_page()

            # --- 1. register an OAuth client (simulating the MCP client) --
            async with httpx.AsyncClient(base_url="https://127.0.0.1:8899", verify=False) as api:
                reg = (
                    await api.post(
                        "/oauth/register",
                        json={"client_name": "e2e-client", "redirect_uris": [REDIRECT]},
                    )
                ).json()
                client_id = reg["client_id"]
                # start the authorization flow (owner not logged in yet)
                challenge = base64.urlsafe_b64encode(
                    hashlib.sha256(b"client-verifier").digest()
                ).rstrip(b"=").decode()
                r = await api.get(
                    "/oauth/authorize",
                    params={
                        "response_type": "code",
                        "client_id": client_id,
                        "redirect_uri": REDIRECT,
                        "state": "e2e",
                        "code_challenge": challenge,
                        "code_challenge_method": "S256",
                        "resource": RESOURCE,
                        "scope": "kb:read",
                    },
                )
                import re

                config_json = re.search(r"var KB_CONFIG = (\{.*?\});", r.text, re.S).group(1)
                request_id = __import__("json").loads(config_json)["pending_request"]
                assert request_id, "login page must carry the pending request"

            # --- 2. the dashboard login form (real browser) ---------------
            # The authorize response IS the login page; load it directly so
            # we can drive the form.
            await page.goto(
                f"{ORIGIN}/oauth/authorize?response_type=code"
                f"&client_id={client_id}&redirect_uri={REDIRECT}&state=e2e"
                f"&code_challenge={challenge}&code_challenge_method=S256"
                f"&resource={RESOURCE}&scope=kb:read"
            )
            # CSP must allow the inline script: the login card renders.
            await page.wait_for_selector("#login-email", timeout=5000)
            await page.fill("#login-email", "owner@example.com")
            await page.click("button.primary")

            # OTP was requested with a PKCE challenge + redirect_to callback
            await page.wait_for_function(
                "() => fetch('/__supabase_mock/__test/link').then(r=>r.json()).then(d=>d.link||false).catch(()=>false)",
                timeout=5000,
            )
            async with httpx.AsyncClient(base_url="https://127.0.0.1:8899", verify=False) as api:
                link = (await api.get("/__supabase_mock/__test/link")).json()["link"]
            assert "code=" in link
            assert "/auth/callback" in link
            # the request_id rode along on the callback URL
            assert "request_id=" in link

            # The browser stored the verifier (PKCE persistence).
            stored = await page.evaluate("() => window.localStorage.getItem('kb_pkce_verifier')")
            assert stored and len(stored) == 43, "43-char base64url verifier in localStorage"

            # --- 3. open the magic link (as a new navigation) -------------
            test_link = link.replace(
                "https://kb.itscatalyst.com/", f"{ORIGIN}/"
            )
            await page.goto(test_link)
            # The callback page runs: exchanges the code, posts /auth/session,
            # then resumes the pending authorization.
            await page.wait_for_url("**/oauth/authorize/resume*", timeout=8000)
            # ...which shows the consent screen for the OAuth client.
            await page.wait_for_selector("form[action='/oauth/authorize/decide']", timeout=5000)
            consent_text = await page.inner_text("body")
            assert "e2e-client" in consent_text

            # localStorage cleaned up
            assert await page.evaluate("() => window.localStorage.getItem('kb_pkce_verifier')") is None

            # --- 4. approve ------------------------------------------------
            # Submit the consent form through Playwright's request context:
            # same browser cookie jar as the page (the real owner session),
            # follows the 302 to the client's redirect URI without CSP or
            # navigation constraints. response.url IS the redirect URI.
            request_id_form = await page.evaluate(
                "() => document.querySelector('form input[name=request_id]').value"
            )
            # Submit the decide form with the browser's own session
            # cookie, from the test process (loopback server). This is the
            # exact POST the consent form performs, minus navigation.
            cookies = await context.cookies()
            session_cookie = next(c["value"] for c in cookies if c["name"] == "kb_session")
            async with httpx.AsyncClient(
                base_url="https://127.0.0.1:8899", verify=False
            ) as api:
                response = await api.post(
                    "/oauth/authorize/decide",
                    data={"request_id": request_id_form, "decision": "approve"},
                    cookies={"kb_session": session_cookie},
                    follow_redirects=False,
                )
                assert response.status_code == 302, response.status_code
                location = response.headers.get("location", "")
            url = location
            assert url.startswith(REDIRECT), location
            assert "code=" in url and "state=e2e" in url

            # --- 5. the owner session is live -----------------------------
            async with httpx.AsyncClient(base_url="https://127.0.0.1:8899", verify=False) as api:
                # Reuse the browser cookie for /auth/me via the page itself.
                me = await page.evaluate(
                    "() => fetch('/auth/me').then(r => r.json())"
                )
                assert me["authenticated"] is True
                assert me["owner_subject"] == OWNER

            # The mock performed exactly one PKCE exchange, with the
            # verifier the browser stored.
            assert len(mock_state.exchanges) == 1
            assert mock_state.exchanges[0]["verifier"] == stored

            await browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
