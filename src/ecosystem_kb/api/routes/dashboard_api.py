"""Dashboard data endpoints (runbook Steps 9, 8): owner-session-authenticated
JSON the dashboard JS consumes. CSRF: every state-changing owner endpoint
checks the Origin header against the canonical origin (runbook §9 security)."""

from __future__ import annotations

import importlib.metadata
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from ecosystem_kb.core.exceptions import AuthenticationError, ValidationError

router = APIRouter(tags=["dashboard-api"])


def _check_origin(request: Request) -> None:
    """Same-origin enforcement for cookie-authenticated state changes.
    Shares the scheme+host rule with /auth/session (see routes/auth.py):
    port may differ, foreign hosts never pass."""
    from ecosystem_kb.api.routes.auth import _origin_allowed

    origin = request.headers.get("origin")
    if origin is None:
        return  # non-browser clients (curl) have no Origin; cookie required anyway
    settings = request.app.state.settings
    base = (settings.public_base_url or "").rstrip("/")
    if not _origin_allowed(origin, base):
        raise AuthenticationError("cross-origin request refused")


async def _require_owner(request: Request):
    token = request.cookies.get("kb_session")
    if not token:
        raise AuthenticationError("owner login required")
    row = await request.app.state.oauth_sessions.resolve(token)
    if row is None:
        raise AuthenticationError("owner login required")
    return row


class CaptureRequest(BaseModel):
    """Owner capture from the dashboard (same contract as the MCP tool)."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=100_000)
    project: str | None = None
    repository: str | None = None
    source_uri: str | None = None
    status: str = "proposed"
    tags: list[str] | None = None
    extraction: dict[str, Any] | None = None


@router.get("/v1/clients")
async def list_clients(request: Request):
    owner = await _require_owner(request)
    oauth = request.app.state.oauth
    clients = await oauth.clients.list_all()
    out = []
    for c in clients:
        counts = await oauth.tokens.active_counts(c.client_id)
        consent = await oauth.consents.get(
            owner.owner_subject, c.client_id, oauth.settings.canonical_mcp_resource
        )
        out.append(
            {
                "client_id": c.client_id,
                "client_name": c.client_name,
                "registration_mode": c.registration_mode,
                "scopes": list(c.scopes or []),
                "active_access_tokens": counts.get("access", 0),
                "active_refresh_tokens": counts.get("refresh", 0),
                "consent_granted": consent is not None,
                "last_used_at": c.last_used_at.isoformat() if c.last_used_at else None,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "status": "disabled" if c.disabled_at else "active",
            }
        )
    return {"clients": out}


async def _owner_subject(request: Request) -> str:
    row = await _require_owner(request)
    return row.owner_subject


@router.post("/v1/clients/{client_id}/revoke")
async def revoke_client(client_id: str, request: Request):
    await _require_owner(request)
    _check_origin(request)
    oauth = request.app.state.oauth
    count = await oauth.revoke_client(client_id, actor="owner:dashboard")
    return {"revoked": True, "tokens_revoked": count}


@router.post("/v1/capture")
async def owner_capture(request: Request):
    """Dashboard capture: the owner session acts as the authenticated actor.
    Same typed-extraction contract as the MCP tool (runbook Step 10)."""
    owner = await _require_owner(request)
    _check_origin(request)
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        raise ValidationError("body must be JSON") from None
    body = CaptureRequest(**(payload or {}))
    from ecosystem_kb.services.high_level import kb_capture

    services = request.app.state.services
    client = _owner_as_client(owner.owner_subject, request.app.state.settings)
    receipt = await kb_capture(
        services,
        client,
        body.kind,
        body.title,
        body.content,
        project=body.project,
        repository=body.repository,
        source_uri=body.source_uri,
        status=body.status,
        tags=body.tags,
        extraction=body.extraction,
    )
    return receipt


def _owner_as_client(owner_subject: str, settings) -> object:
    """The owner session acts as a fully-trusted principal for dashboard
    actions (runbook Step 8: owner-session principal)."""
    from contracts.clients import AuthenticatedClient

    return AuthenticatedClient(
        client_id=f"owner:{owner_subject}",
        scopes=["kb:read", "kb:write", "kb:review", "kb:maintain"],
        allowed_namespaces=settings.namespace_list,
        allowed_sources=["*"],
        default_namespace=settings.default_namespace
        if settings.default_namespace in settings.namespace_list
        else None,
    )


@router.get("/v1/status")
async def status(request: Request):
    await _require_owner(request)
    checks: dict[str, str] = {}
    try:
        counts = await request.app.state.control_plane.table_counts()
        checks["control_plane"] = f"ok ({sum(counts.values())} rows)"
    except Exception as exc:  # noqa: BLE001
        checks["control_plane"] = f"error: {exc}"
    try:
        from ecosystem_kb.adapters.graphiti.health import check_graph

        settings = request.app.state.settings
        graph_health = await check_graph(
            request.app.state.graphiti, settings.default_namespace
        )
        checks["graph"] = "ok" if graph_health.ok else f"degraded: {graph_health.detail[:100]}"
    except Exception as exc:  # noqa: BLE001
        checks["graph"] = f"degraded: {exc}"
    try:
        stats = await request.app.state.repos.events.queue_stats()
        checks["worker_queue"] = (
            f"queued={stats.get('queued', 0)} processing={stats.get('processing', 0)} "
            f"dead_letter={stats.get('dead_letter', 0)}"
        )
    except Exception as exc:  # noqa: BLE001
        checks["worker_queue"] = f"error: {exc}"
    try:
        version = importlib.metadata.version("ecosystem-kb")
    except Exception:  # noqa: BLE001
        version = "dev"
    return {"checks": checks, "version": version}


@router.get("/v1/dashboard/search")
async def owner_search(request: Request):
    """Owner-session search (dashboard uses cookie auth, not a bearer)."""
    owner = await _require_owner(request)
    from contracts.search import SearchRequest

    services = request.app.state.services
    client = _owner_as_client(owner.owner_subject, request.app.state.settings)
    params = request.query_params
    query = params.get("query") or params.get("q") or ""
    if not query:
        raise ValidationError("query required")
    num = int(params.get("num_results", "10"))
    response = await services.search(
        client, SearchRequest(query=query, num_results=num)
    )
    return response.model_dump(mode="json")
