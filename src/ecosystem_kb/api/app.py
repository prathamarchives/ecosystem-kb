"""Application factory: FastAPI app + mounted MCP server, shared services."""

from __future__ import annotations

from contextlib import asynccontextmanager

from contracts.responses import HealthResponse, ReadinessResponse
from fastapi import FastAPI

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

    app.include_router(events_router, prefix="/v1")
    app.include_router(knowledge_router, prefix="/v1")
    app.include_router(maintenance_router, prefix="/v1")
    app.include_router(admin_router, prefix="/v1")

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
                response = JSONResponse(
                    {"error": code, "message": message}, status_code=status
                )
                await response(scope, receive, send)
                return
            await self.sub_app(scope, receive, send)

    app.mount("/mcp", McpAuthMiddleware(mcp_asgi))

    return app
