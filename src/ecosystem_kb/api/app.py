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
        yield
        await cp.dispose()
        if graphiti is None and graph is not None:
            await graph.close()

    app = FastAPI(title="ecosystem-kb", version="0.1.0", lifespan=lifespan)

    from ecosystem_kb.api.routes.events import router as events_router
    from ecosystem_kb.api.routes.knowledge import router as knowledge_router
    from ecosystem_kb.api.routes.maintenance import router as maintenance_router

    app.include_router(events_router, prefix="/v1")
    app.include_router(knowledge_router, prefix="/v1")
    app.include_router(maintenance_router, prefix="/v1")

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
    from ecosystem_kb.mcp.server import build_mcp_server

    mcp = build_mcp_server()
    app.mount("/mcp", mcp.http_app(path="/"))

    return app
