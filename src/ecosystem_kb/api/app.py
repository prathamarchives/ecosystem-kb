"""Application factory: FastAPI app + mounted MCP server, shared services.

Public surface (runbook §0.1) — one hostname:
  /                    owner dashboard (this process, static HTML + JS)
  /auth/*              Supabase login bootstrap + first-party kb_session
  /oauth/*             first-party OAuth 2.1 authorization server
  /v1/*                REST API (bearer or owner session)
  /mcp                 FastMCP Streamable HTTP (canonical, no trailing slash)
  /.well-known/*       protected-resource + authorization-server metadata
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from contracts.responses import HealthResponse, ReadinessResponse
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from ecosystem_kb.adapters.graphiti.client import build_graphiti
from ecosystem_kb.adapters.graphiti.health import check_graph
from ecosystem_kb.adapters.postgres.oauth_repositories import (
    AuthorizationCodeRepository,
    ConsentRepository,
    OauthClientRepository,
    PendingAuthorizationRepository,
    SessionRepository,
    TokenRepository,
)
from ecosystem_kb.adapters.postgres.repositories import ControlPlane, Repositories
from ecosystem_kb.api.auth import AuthService
from ecosystem_kb.core.config import KbSettings, get_settings
from ecosystem_kb.services.event_service import KbServices
from ecosystem_kb.services.maintenance_service import MaintenanceService


def _build_oauth_repositories(cp: ControlPlane) -> dict:
    """OAuth store repositories over the same control plane."""
    return {
        "sessions": SessionRepository(cp.session),
        "clients": OauthClientRepository(cp.session),
        "pending": PendingAuthorizationRepository(cp.session),
        "codes": AuthorizationCodeRepository(cp.session),
        "tokens": TokenRepository(cp.session),
        "consents": ConsentRepository(cp.session),
    }


def create_app(
    settings: KbSettings | None = None,
    *,
    graphiti=None,
    control_plane: ControlPlane | None = None,
) -> FastAPI:
    """Build the gateway. Injection points exist for tests (embedded graph,
    temp control plane); production builds from settings."""
    settings = settings or get_settings()

    # Build the MCP sub-app FIRST: its Starlette lifespan initializes the
    # Streamable HTTP session manager. FastAPI accepts only one lifespan,
    # so the app lifespan must chain into it — mounting alone would leave
    # the MCP transport uninitialized (every /mcp request would fail with
    # 'Task group is not initialized').
    from ecosystem_kb.mcp.server import build_mcp_server

    mcp = build_mcp_server()
    # Canonical endpoint is /mcp with NO trailing slash (runbook §1.2.5):
    # mount the sub-app at the root path inside our /mcp mount, so GET/POST
    # /mcp hits the protocol handler directly instead of a 307 to /mcp/.
    mcp_asgi = mcp.http_app(path="/")
    mcp_lifespan = mcp_asgi.lifespan

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Production safety: refuse unsafe configs before serving traffic.
        settings.validate_runtime_safety()
        cp = control_plane or ControlPlane(settings.database_url)
        # Versioned migrations are the schema mechanism (review P1);
        # create_all is only a dev/test shortcut on throwaway databases.
        from ecosystem_kb.adapters.postgres.migrations import migrate

        await migrate(cp.engine)
        repos = Repositories(cp)
        graph = graphiti if graphiti is not None else build_graphiti(settings)
        app.state.settings = settings
        app.state.control_plane = cp
        app.state.repos = repos
        app.state.graphiti = graph
        app.state.services = KbServices(repos, graph, settings)
        app.state.maintenance = MaintenanceService(repos, graph)
        app.state.auth = AuthService(repos, settings)
        # First-party OAuth AS + owner sessions (runbook Steps 6-8).
        from ecosystem_kb.api.oauth_server import AuthorizationServer

        oauth_repos = _build_oauth_repositories(cp)
        oauth_repos["audit"] = repos.audit
        app.state.oauth = AuthorizationServer(oauth_repos, settings)
        app.state.oauth_sessions = oauth_repos["sessions"]
        # The single principal resolver (Step 8): AuthService resolves KB
        # OAuth tokens through the AS, legacy static tokens behind the flag.
        app.state.auth.oauth = app.state.oauth
        app.state.owner_verifier = None
        if settings.supabase_url and settings.owner_supabase_subject:
            from ecosystem_kb.api.owner_auth import SupabaseOwnerVerifier

            app.state.owner_verifier = SupabaseOwnerVerifier(
                settings.supabase_url,
                settings.owner_supabase_subject,
                anon_key=settings.supabase_anon_key,
            )
        # The mounted MCP sub-app replaces scope["app"] with itself, so tools
        # resolving services through the request cannot see THIS app. Mirror
        # the wiring onto the sub-app's own State (its middleware reads
        # transport_type from the same object, so the attribute surface is
        # the supported one).
        mcp_asgi.state.services = app.state.services
        mcp_asgi.state.maintenance = app.state.maintenance
        mcp_asgi.state.auth = app.state.auth
        mcp_asgi.state.oauth = app.state.oauth
        mcp_asgi.state.kb_root_app = app
        # Chain the MCP sub-app's lifespan (session manager init) inside
        # ours, then serve.
        async with mcp_lifespan(mcp_asgi):
            yield
        await cp.dispose()
        if graphiti is None and graph is not None:
            await graph.close()

    app = FastAPI(title="ecosystem-kb", version="0.1.0", lifespan=lifespan)

    from ecosystem_kb.api.routes.admin import router as admin_router
    from ecosystem_kb.api.routes.auth import router as auth_router
    from ecosystem_kb.api.routes.dashboard import router as dashboard_router
    from ecosystem_kb.api.routes.dashboard_api import router as dashboard_api_router
    from ecosystem_kb.api.routes.events import router as events_router
    from ecosystem_kb.api.routes.knowledge import router as knowledge_router
    from ecosystem_kb.api.routes.maintenance import router as maintenance_router
    from ecosystem_kb.api.routes.oauth import router as oauth_router

    app.include_router(events_router, prefix="/v1")
    app.include_router(knowledge_router, prefix="/v1")
    app.include_router(maintenance_router, prefix="/v1")
    app.include_router(admin_router, prefix="/v1")
    app.include_router(dashboard_api_router)
    app.include_router(auth_router)
    app.include_router(oauth_router)
    app.include_router(dashboard_router)

    # Metadata (runbook §5.5): issuer is THIS server; endpoints same-origin.
    resource = settings.canonical_mcp_resource or ""
    base = (settings.public_base_url or "").rstrip("/")

    def _protected_resource():
        return {
            "resource": resource,
            "authorization_servers": [base],
            "scopes_supported": ["kb:read", "kb:write", "offline_access"],
            "bearer_methods_supported": ["header"],
        }

    def _as_metadata():
        metadata = {
            "issuer": base,
            "authorization_endpoint": base + "/oauth/authorize",
            "token_endpoint": base + "/oauth/token",
            "revocation_endpoint": base + "/oauth/revoke",
            "registration_endpoint": base + "/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": ["kb:read", "kb:write", "offline_access"],
        }
        # CIMD is intentionally NOT advertised: clients fall back to DCR,
        # which registers the loopback redirect URI with its concrete port
        # (OpenCode's CIMD document only lists portless loopbacks).
        return metadata

    @app.get("/.well-known/oauth-protected-resource")
    @app.get("/.well-known/oauth-protected-resource/mcp")
    async def oauth_protected_resource():
        return JSONResponse(_protected_resource())

    @app.get("/.well-known/oauth-authorization-server")
    @app.get("/.well-known/oauth-authorization-server/mcp")
    async def oauth_authorization_server():
        return JSONResponse(_as_metadata())

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse()

    @app.get("/ready", response_model=ReadinessResponse)
    async def ready() -> ReadinessResponse:
        checks: dict = {}
        ready = True
        try:
            await app.state.control_plane.table_counts()
            checks["control_plane"] = "ok"
        except Exception as exc:
            checks["control_plane"] = f"error: {exc}"
            ready = False
        try:
            graph_health = await check_graph(app.state.graphiti, settings.default_namespace)
            if graph_health.ok:
                checks["graph"] = "ok"
            else:
                # Index-building or transient driver state: soft-degrade with
                # detail; the control plane being up still serves events.
                checks["graph"] = f"degraded: {graph_health.detail[:120]}"
        except Exception as exc:
            checks["graph"] = f"degraded: {exc}"
        return ReadinessResponse(ready=ready, checks=checks)

    # Global error mapping: KbError subclasses -> uniform JSON bodies.
    from ecosystem_kb.core.exceptions import KbError

    @app.exception_handler(KbError)
    async def kb_error_handler(request, exc: KbError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.error_code, "message": str(exc), "detail": {}},
        )

    # Mount MCP at /mcp — same services, restricted tool surface (ADR-005).
    # The sub-app was built above; its lifespan is chained in ours.
    # A thin ASGI middleware enforces the SAME bearer auth on every MCP
    # request BEFORE the protocol layer runs: unauthenticated clients must
    # not even complete protocol initialization (plan §F-1). It calls the
    # ONE principal resolver (AuthService.authenticate_token, Step 8), so
    # MCP and REST accept exactly the same credentials: KB OAuth access
    # tokens normally; the loopback admin client and the dev/migration
    # static paths where the settings allow them.
    class McpAuthMiddleware:
        """Bearer gate for every /mcp request, BEFORE protocol handling."""

        def __init__(self, sub_app) -> None:
            self.sub_app = sub_app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                await self.sub_app(scope, receive, send)
                return
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            auth = headers.get("authorization", "")
            error = None
            if not auth.lower().startswith("bearer "):
                error = (401, "authentication_required", "missing bearer token")
            else:
                token = auth[7:].strip()
                state = getattr(self.sub_app, "state", None)
                auth_svc = getattr(state, "auth", None)
                if auth_svc is None:
                    error = (503, "service_unavailable", "auth service not ready")
                else:
                    try:
                        await auth_svc.authenticate_token(token)
                    except Exception:
                        error = (401, "invalid_token", "invalid or revoked token")
            if error is not None:
                status, code, message = error
                challenge_headers = {}
                if base and resource:
                    challenge_headers["WWW-Authenticate"] = (
                        f'Bearer resource_metadata="{base}/.well-known/'
                        'oauth-protected-resource/mcp"'
                    )
                response = JSONResponse(
                    {"error": code, "message": message},
                    status_code=status,
                    headers=challenge_headers,
                )
                await response(scope, receive, send)
                return
            await self.sub_app(scope, receive, send)

    # Canonical mount: /mcp (no trailing slash) is the protocol endpoint and
    # must NOT redirect (runbook §1.2.5; the CI-red trailing-slash bug).
    # FastAPI's Mount/redirect_slashes 307s bare /mcp to /mcp/ before any
    # mounted app runs, so we bypass Mount entirely: a raw ASGI wrapper
    # installed as the LAST route owns /mcp and /mcp/ exactly.
    class McpEndpointApp:
        """Owns /mcp and /mcp/ — auth gate + FastMCP sub-app, no redirect."""

        def __init__(self, sub_app) -> None:
            self.middleware = McpAuthMiddleware(sub_app)

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                await self.middleware(scope, receive, send)
                return
            path = scope.get("path", "")
            if path in ("/mcp", "/mcp/"):
                # Reproduce exactly what Starlette Mount('/mcp') does for the
                # slashed form: root_path gains the mount prefix and the
                # sub-app's internal router sees its own root path.
                scope = dict(scope)
                scope["root_path"] = scope.get("root_path", "") + "/mcp"
                scope["path"] = "/" if path == "/mcp" else "/"
            await self.middleware(scope, receive, send)

    # Register as a catch-route AFTER include_router calls but with explicit
    # path matching, using Starlette Route with the raw ASGI callable.
    from starlette.routing import Route

    mcp_route = Route(
        "/mcp",
        endpoint=McpEndpointApp(mcp_asgi),
        methods=["GET", "POST", "DELETE", "OPTIONS", "HEAD", "PUT", "PATCH"],
        name="mcp",
    )
    mcp_route_slash = Route(
        "/mcp/",
        endpoint=McpEndpointApp(mcp_asgi),
        methods=["GET", "POST", "DELETE", "OPTIONS", "HEAD", "PUT", "PATCH"],
        name="mcp-slash-alias",
    )
    app.router.routes.append(mcp_route)
    app.router.routes.append(mcp_route_slash)

    return app
