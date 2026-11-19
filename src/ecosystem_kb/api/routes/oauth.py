"""First-party OAuth 2.1 endpoints (runbook Steps 5.5, 7).

  GET  /oauth/authorize   authorization endpoint (login-gated, consent UI)
  POST /oauth/token       code exchange + refresh rotation
  POST /oauth/revoke      RFC 7009 revocation
  POST /oauth/register    DCR fallback (RFC 7591)

Metadata lives in app.py (same origin, issuer = the public base URL).
All protocol errors return standard OAuth error bodies. The authorization
endpoint preserves the transaction server-side (pending authorization)
so the magic-link login round trip cannot lose it.
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ecosystem_kb.api.oauth_server import OauthProtocolError

router = APIRouter(tags=["oauth"])


def _oauth(request: Request):
    return request.app.state.oauth


def _error_redirect(redirect_uri: str, error: str, state: str | None, description: str | None = None):
    params = {"error": error}
    if description:
        params["error_description"] = description
    if state:
        params["state"] = state
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}{urlencode(params)}", status_code=302)


@router.get("/oauth/authorize")
async def authorize(request: Request):
    """Validate the authorization request; then either show consent (owner
    logged in) or the login page with the pending request preserved."""
    oauth = _oauth(request)
    q = request.query_params
    try:
        client, request_id = await oauth.begin_authorization(
            client_id=q.get("client_id", ""),
            redirect_uri=q.get("redirect_uri", ""),
            state=q.get("state"),
            code_challenge=q.get("code_challenge"),
            code_challenge_method=q.get("code_challenge_method"),
            resource=q.get("resource"),
            scope=q.get("scope"),
            response_type=q.get("response_type", ""),
        )
    except OauthProtocolError as exc:
        # Known client + valid redirect URI: send the error back via redirect
        # (RFC 6749 §4.1.2.1). Otherwise surface it as a JSON error.
        redirect_uri = q.get("redirect_uri", "")
        if getattr(exc, "redirect", True) and redirect_uri:
            try:
                client = await oauth.resolve_client(q.get("client_id", ""))
                if client is not None and redirect_uri in (client.redirect_uris or []):
                    return _error_redirect(redirect_uri, exc.error, q.get("state"), exc.description)
            except Exception:  # noqa: BLE001 — fall through to JSON on any doubt
                pass
        return JSONResponse(
            {"error": exc.error, "error_description": exc.description},
            status_code=exc.status,
        )

    token = request.cookies.get("kb_session")
    session_row = await request.app.state.oauth_sessions.resolve(token) if token else None
    if session_row is None:
        # Owner must log in first; the pending authorization survives.
        from ecosystem_kb.api.routes.dashboard import login_page

        return login_page(request, next_request_id=request_id)

    return await _consent_or_redirect(request, oauth, client, request_id, session_row)


@router.get("/oauth/authorize/resume")
async def authorize_resume(request: Request):
    """Resume a pending authorization after login (the login page sends the
    browser here with its request_id)."""
    oauth = _oauth(request)
    request_id = request.query_params.get("request_id", "")
    token = request.cookies.get("kb_session")
    session_row = await request.app.state.oauth_sessions.resolve(token) if token else None
    if session_row is None:
        from ecosystem_kb.api.routes.dashboard import login_page

        return login_page(request, next_request_id=request_id)
    row = await oauth.pending_for(request_id)
    if row is None:
        return JSONResponse(
            {"error": "invalid_request", "error_description": "authorization request expired"},
            status_code=400,
        )
    client = await oauth.resolve_client(row.client_id)
    if client is None:
        return JSONResponse(
            {"error": "unauthorized_client", "error_description": "unknown client"},
            status_code=400,
        )
    return await _consent_or_redirect(request, oauth, client, request_id, session_row)


async def _consent_or_redirect(request: Request, oauth, client, request_id, session_row):
    """Stored consent with unchanged scopes: skip the prompt (runbook §5.6).
    Otherwise render the consent screen."""
    existing = await oauth.existing_consent_scopes(
        session_row.owner_subject, client.client_id, oauth.settings.canonical_mcp_resource
    )
    requested = await oauth.pending_for(request_id)
    if requested is None:
        return JSONResponse(
            {"error": "invalid_request", "error_description": "authorization request expired"},
            status_code=400,
        )
    if existing is not None and set(existing) >= set(requested.scopes or []):
        redirect_uri, code, state = await oauth.decide(
            request_id, session_row.owner_subject, session_row.id, approve=True
        )
        params = {"code": code}
        if state:
            params["state"] = state
        sep = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(f"{redirect_uri}{sep}{urlencode(params)}", status_code=302)

    from ecosystem_kb.api.routes.dashboard import consent_page

    scopes = requested.scopes or []
    return consent_page(
        request,
        client_name=client.client_name,
        client_id=client.client_id,
        scopes=scopes,
        request_id=request_id,
    )


@router.post("/oauth/authorize/decide")
async def authorize_decide(request: Request):
    """Owner decision on the consent screen. Session-authenticated with
    Origin checking (the browser form posts here)."""
    origin = request.headers.get("origin")
    settings = request.app.state.settings
    base = (settings.public_base_url or "").rstrip("/")
    if base and origin is not None and origin != base:
        return JSONResponse({"error": "invalid_origin"}, status_code=403)
    token = request.cookies.get("kb_session")
    session_row = await request.app.state.oauth_sessions.resolve(token) if token else None
    if session_row is None:
        return JSONResponse({"error": "login_required"}, status_code=401)
    form = await request.form()
    request_id = str(form.get("request_id", ""))
    approve = str(form.get("decision", "")) == "approve"
    oauth = _oauth(request)
    try:
        redirect_uri, code_or_error, state = await oauth.decide(
            request_id, session_row.owner_subject, session_row.id, approve=approve
        )
    except OauthProtocolError as exc:
        return JSONResponse(
            {"error": exc.error, "error_description": exc.description}, status_code=exc.status
        )
    if code_or_error == "access_denied":
        return _error_redirect(redirect_uri, "access_denied", state)
    params = {"code": code_or_error}
    if state:
        params["state"] = state
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}{urlencode(params)}", status_code=302)


@router.post("/oauth/token")
async def token(request: Request):
    oauth = _oauth(request)
    form = await request.form()
    grant_type = str(form.get("grant_type", ""))
    try:
        if grant_type == "authorization_code":
            pair = await oauth.exchange_code(
                grant_type=grant_type,
                code=str(form.get("code", "")) or None,
                redirect_uri=str(form.get("redirect_uri", "")) or None,
                client_id=str(form.get("client_id", "")) or None,
                code_verifier=str(form.get("code_verifier", "")) or None,
                resource=str(form.get("resource", "")) or None,
            )
        elif grant_type == "refresh_token":
            pair = await oauth.refresh(
                grant_type=grant_type,
                refresh_token=str(form.get("refresh_token", "")) or None,
                resource=str(form.get("resource", "")) or None,
                client_id=str(form.get("client_id", "")) or None,
            )
        else:
            raise OauthProtocolError("unsupported_grant_type", "unsupported grant type")
    except OauthProtocolError as exc:
        return JSONResponse(
            {"error": exc.error, "error_description": exc.description},
            status_code=exc.status,
            headers={"Cache-Control": "no-store"},
        )
    body = {
        "access_token": pair.access_token,
        "token_type": pair.token_type,
        "expires_in": pair.expires_in,
        "scope": pair.scope,
    }
    if pair.refresh_token:
        body["refresh_token"] = pair.refresh_token
    return JSONResponse(body, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@router.post("/oauth/revoke")
async def revoke(request: Request):
    form = await request.form()
    token_value = str(form.get("token", ""))
    await _oauth(request).revoke_token(token_value)
    return JSONResponse({}, status_code=200)


@router.post("/oauth/register")
async def register(request: Request):
    """DCR fallback (runbook §5.4). Registration never grants consent."""
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse(
            {"error": "invalid_client_metadata", "error_description": "body must be JSON"},
            status_code=400,
        )
    oauth = _oauth(request)
    try:
        registration = await oauth.register_dcr(payload or {})
    except OauthProtocolError as exc:
        return JSONResponse(
            {"error": exc.error, "error_description": exc.description}, status_code=exc.status
        )
    return JSONResponse(registration, status_code=201)


@router.get("/oauth/consent")
async def consent_status(request: Request):
    """Tiny JSON status endpoint for tests: is a request_id still pending?"""
    request_id = request.query_params.get("request_id", "")
    row = await _oauth(request).pending_for(request_id)
    return {"pending": row is not None, "status": row.status if row else None}
