"""Owner login routes (runbook Step 6).

Supabase proves who the owner is; Ecosystem KB then issues its own opaque
kb_session cookie. The browser never sees a service-role key or an MCP
token, and the KB session is independently revocable.
"""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ecosystem_kb.core.exceptions import AuthenticationError

router = APIRouter(tags=["auth"])


def _settings(request: Request):
    return request.app.state.settings


def _require_owner_login_configured(request: Request):
    verifier = getattr(request.app.state, "owner_verifier", None)
    if verifier is None:
        raise AuthenticationError("owner login is not configured")
    return verifier


@router.get("/auth/config")
async def auth_config(request: Request):
    """Public login bootstrap: Supabase URL, publishable key, callback URL."""
    settings = _settings(request)
    if not settings.supabase_url or not settings.supabase_anon_key:
        return JSONResponse({"error": "not_configured"}, status_code=503)
    base = (settings.public_base_url or "").rstrip("/")
    return {
        "supabase_url": settings.supabase_url,
        "supabase_anon_key": settings.supabase_anon_key,
        "callback_url": f"{base}/auth/callback",
    }


@router.post("/auth/session")
async def create_session(request: Request):
    """Exchange a verified Supabase access token for a kb_session cookie."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — malformed body is a client error
        body = {}
    token = (body or {}).get("access_token")
    if not token or not isinstance(token, str):
        raise AuthenticationError("access_token required")
    verifier = _require_owner_login_configured(request)
    await verifier.verify_owner(token)

    settings = _settings(request)
    ua = request.headers.get("user-agent", "")
    cookie_value = await request.app.state.oauth_sessions.create(
        owner_subject=verifier.owner_subject,
        ttl_days=settings.owner_session_days,
        user_agent_hash=hashlib.sha256(ua.encode()).hexdigest() if ua else None,
    )
    response = JSONResponse({"authenticated": True, "owner_subject": verifier.owner_subject})
    secure = request.url.scheme == "https" or (
        request.headers.get("x-forwarded-proto", "").lower() == "https"
    )
    response.set_cookie(
        "kb_session",
        cookie_value,
        path="/",
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=settings.owner_session_days * 24 * 3600,
    )
    return response


@router.get("/auth/me")
async def auth_me(request: Request):
    token = request.cookies.get("kb_session")
    if not token:
        return {"authenticated": False}
    row = await request.app.state.oauth_sessions.resolve(token)
    if row is None:
        return {"authenticated": False}
    await request.app.state.oauth_sessions.touch(token)
    return {
        "authenticated": True,
        "owner_subject": row.owner_subject,
        "expires_at": row.expires_at.isoformat(),
    }


@router.post("/auth/logout")
async def logout(request: Request):
    token = request.cookies.get("kb_session")
    if token:
        await request.app.state.oauth_sessions.revoke(token)
    response = JSONResponse({"authenticated": False})
    response.delete_cookie("kb_session", path="/")
    return response
