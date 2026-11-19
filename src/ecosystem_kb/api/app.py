"""Application factory: FastAPI app + mounted MCP server, shared services."""

from __future__ import annotations

from contextlib import asynccontextmanager

from contracts.responses import HealthResponse, ReadinessResponse
from fastapi import FastAPI, Request

from ecosystem_kb.adapters.graphiti.client import build_graphiti
from ecosystem_kb.adapters.graphiti.health import check_graph
from ecosystem_kb.adapters.postgres.repositories import ControlPlane, Repositories
from ecosystem_kb.api.auth import AuthService
from ecosystem_kb.core.config import KbSettings, get_settings
from ecosystem_kb.services.event_service import KbServices
from ecosystem_kb.services.maintenance_service import MaintenanceService


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
        # The mounted MCP sub-app replaces scope["app"] with itself, so tools
        # resolving services through the request cannot see THIS app. Mirror
        # the wiring onto the sub-app's own State (its middleware reads
        # transport_type from the same object, so the attribute surface is
        # the supported one).
        mcp_asgi.state.services = app.state.services
        mcp_asgi.state.maintenance = app.state.maintenance
        mcp_asgi.state.auth = app.state.auth
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
    from ecosystem_kb.api.routes.events import router as events_router
    from ecosystem_kb.api.routes.knowledge import router as knowledge_router
    from ecosystem_kb.api.routes.maintenance import router as maintenance_router

    # Browser UI lives on a sibling subdomain; allow its origin to call the API.
    if settings.supabase_url:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=["https://knowledgebase.itscatalyst.com"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(events_router, prefix="/v1")
    app.include_router(knowledge_router, prefix="/v1")
    app.include_router(maintenance_router, prefix="/v1")
    app.include_router(admin_router, prefix="/v1")

    # OAuth 2.1 discovery for MCP clients (RFC 9728 + RFC 8414). Supabase is
    # the actual issuer; we publish a full AS metadata document here because
    # Supabase's OIDC discovery omits `registration_endpoint`, which breaks
    # clients using Dynamic Client Registration (e.g. ChatGPT connectors).
    if settings.supabase_url:
        from fastapi.responses import JSONResponse

        _base = (settings.public_base_url or "").rstrip("/")
        _supabase = settings.supabase_url.rstrip("/") + "/auth/v1"
        _resource = _base + "/mcp"

        def _protected_resource():
            return {
                "resource": _resource,
                # Point at THIS host so clients fetch our complete AS metadata.
                "authorization_servers": [_base],
                "scopes_supported": ["openid", "email", "profile", "kb:read", "kb:write"],
                "bearer_methods_supported": ["header"],
            }

        def _as_metadata():
            # Every endpoint is same-origin with `issuer` — strict MCP clients
            # reject a metadata document whose endpoints live on another host.
            return {
                "issuer": _base,
                "authorization_endpoint": _base + "/oauth/authorize",
                "token_endpoint": _base + "/oauth/token",
                "registration_endpoint": _base + "/oauth/register",
                "jwks_uri": _base + "/oauth/jwks",
                "scopes_supported": ["openid", "email", "profile", "offline_access"],
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": [
                    "client_secret_basic", "client_secret_post", "none"
                ],
            }

        @app.get("/.well-known/oauth-protected-resource")
        @app.get("/.well-known/oauth-protected-resource/mcp")
        async def oauth_protected_resource():
            return JSONResponse(_protected_resource())

        @app.get("/.well-known/oauth-authorization-server")
        @app.get("/.well-known/oauth-authorization-server/mcp")
        async def oauth_authorization_server():
            return JSONResponse(_as_metadata())

        # Same-origin OAuth proxies. Supabase remains the real issuer; these
        # just make the advertised endpoints live under our own domain.
        import httpx
        from fastapi.responses import RedirectResponse, Response

        @app.get("/oauth/authorize", include_in_schema=False)
        async def oauth_authorize_proxy(request: Request):
            return RedirectResponse(
                _supabase + "/oauth/authorize?" + request.url.query, status_code=302
            )

        @app.post("/oauth/token", include_in_schema=False)
        async def oauth_token_proxy(request: Request):
            body = await request.body()
            headers = {k.lower(): v for k, v in request.headers.items()
                       if k.lower() in ("content-type", "authorization")}
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(_supabase + "/oauth/token", content=body, headers=headers)
            return Response(r.content, r.status_code, media_type=r.headers.get("content-type", "application/json"))

        @app.post("/oauth/register", include_in_schema=False)
        async def oauth_register_proxy(request: Request):
            body = await request.body()
            headers = {k.lower(): v for k, v in request.headers.items()
                       if k.lower() in ("content-type", "authorization")}
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(_supabase + "/oauth/clients/register", content=body, headers=headers)
            return Response(r.content, r.status_code, media_type=r.headers.get("content-type", "application/json"))

        @app.get("/oauth/jwks", include_in_schema=False)
        async def oauth_jwks_proxy():
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(_supabase + "/.well-known/jwks.json")
            return Response(r.content, r.status_code, media_type="application/json")

    # A minimal, connector-friendly OpenAPI document (OpenAPI 3.0.3) for
    # ChatGPT/agent connectors that reject the auto-generated 3.1 schema
    # (no server URL, no security scheme, admin endpoints exposed).
    @app.get("/connector/openapi.json", include_in_schema=False)
    async def connector_openapi():
        base = (settings.public_base_url or "").rstrip("/")
        security = (
            [{"openIdConnect": [settings.supabase_url.rstrip("/") + "/auth/v1"]}]
            if settings.supabase_url
            else [{"bearerAuth": []}]
        )
        schemes = {
            "bearerAuth": {"type": "http", "scheme": "bearer"},
        }
        if settings.supabase_url:
            schemes["openIdConnect"] = {
                "type": "openIdConnect",
                "openIdConnectUrl": settings.supabase_url.rstrip("/")
                + "/auth/v1/.well-known/openid-configuration",
            }
        return {
            "openapi": "3.0.3",
            "info": {
                "title": "ecosystem-kb",
                "version": "1.0.0",
                "description": "Shared source-of-truth knowledge graph: search and capture source-backed facts with provenance.",
            },
            "servers": [{"url": base}],
            "security": security,
            "paths": {
                "/v1/search": {
                    "post": {
                        "operationId": "kb_search",
                        "summary": "Search the knowledge graph",
                        "description": "Retrieve source-backed facts about a topic, decision, or entity. Results include provenance and epistemic status.",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/SearchRequest"}
                                }
                            },
                        },
                        "responses": {
                            "200": {
                                "description": "Search results with facts and provenance",
                                "content": {
                                    "application/json": {"schema": {"type": "object"}}
                                },
                            },
                            "401": {"description": "Not authenticated"},
                        },
                    }
                },
                "/v1/events": {
                    "post": {
                        "operationId": "kb_capture",
                        "summary": "Capture durable knowledge",
                        "description": "Store a decision, finding, or correction as a provenance-bearing knowledge event. Extraction of typed entities happens asynchronously.",
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/KnowledgeEvent"}
                                }
                            },
                        },
                        "responses": {
                            "200": {
                                "description": "Event receipt",
                                "content": {
                                    "application/json": {"schema": {"type": "object"}}
                                },
                            },
                            "401": {"description": "Not authenticated"},
                        },
                    }
                },
            },
            "components": {
                "securitySchemes": schemes,
                "schemas": {
                    "SearchRequest": {
                        "type": "object",
                        "required": ["query"],
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Natural-language search query",
                            },
                            "num_results": {"type": "integer", "default": 10},
                        },
                    },
                    "KnowledgeEvent": {
                        "type": "object",
                        "required": [
                            "idempotency_key",
                            "event_type",
                            "source",
                            "timestamps",
                            "content",
                        ],
                        "properties": {
                            "idempotency_key": {
                                "type": "string",
                                "description": "Caller-scoped unique key, e.g. chatgpt:message-123",
                            },
                            "event_type": {
                                "type": "string",
                                "description": "Namespaced type, e.g. knowledge.decision.recorded",
                            },
                            "source": {
                                "type": "object",
                                "properties": {
                                    "system_id": {"type": "string"},
                                    "record_id": {"type": "string"},
                                    "canonical_uri": {"type": "string"},
                                },
                            },
                            "timestamps": {
                                "type": "object",
                                "properties": {
                                    "occurred_at": {"type": "string", "format": "date-time"},
                                    "recorded_at": {"type": "string", "format": "date-time"},
                                },
                            },
                            "content": {
                                "type": "object",
                                "properties": {
                                    "content_type": {"type": "string", "default": "text"},
                                    "body": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            },
        }

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
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.error_code, "message": str(exc), "detail": {}},
        )

    # Mount MCP at /mcp — same services, restricted tool surface (ADR-005).
    # The sub-app was built above; its lifespan is chained in ours.
    # A thin ASGI middleware enforces the SAME bearer auth on every MCP
    # request BEFORE the protocol layer runs: unauthenticated clients must
    # not even complete protocol initialization (plan §F-1).
    from starlette.responses import JSONResponse

    class McpAuthMiddleware:
        """Bearer gate for every /mcp request, BEFORE protocol handling:
        unauthenticated clients must not complete protocol initialization
        (plan §F-1). Token VALIDATION runs here too — initialize itself
        carries no tool call where the service layer could reject it."""

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
                headers = {}
                if settings.supabase_url and settings.public_base_url:
                    base = settings.public_base_url.rstrip("/")
                    headers["WWW-Authenticate"] = (
                        f'Bearer resource_metadata="{base}/.well-known/'
                        'oauth-protected-resource"'
                    )
                response = JSONResponse(
                    {"error": code, "message": message}, status_code=status, headers=headers
                )
                await response(scope, receive, send)
                return
            await self.sub_app(scope, receive, send)

    app.mount("/mcp", McpAuthMiddleware(mcp_asgi))

    return app
