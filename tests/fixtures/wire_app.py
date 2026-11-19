"""Test helper: wire app.state exactly as create_app's lifespan does.

ASGITransport (httpx) does NOT run FastAPI lifespans, so contract tests
build the app and wire state manually. This helper keeps that wiring in
one place, including the OAuth authorization server + owner sessions
introduced by the supermemory-style runbook (Steps 6-8).

Usage:
    app = create_app(settings=settings, graphiti=g, control_plane=cp)
    await wire_app_state(app, settings=settings, graphiti=g, control_plane=cp)
"""

from __future__ import annotations

import pytest_asyncio

from ecosystem_kb.adapters.postgres.oauth_repositories import (
    AuthorizationCodeRepository,
    ConsentRepository,
    OauthClientRepository,
    PendingAuthorizationRepository,
    SessionRepository,
    TokenRepository,
)
from ecosystem_kb.adapters.postgres.repositories import Repositories
from ecosystem_kb.api.auth import AuthService
from ecosystem_kb.services.event_service import KbServices
from ecosystem_kb.services.maintenance_service import MaintenanceService


async def wire_app_state(app, *, settings, graphiti, control_plane) -> None:
    repos = Repositories(control_plane)
    app.state.settings = settings
    app.state.control_plane = control_plane
    app.state.repos = repos
    app.state.graphiti = graphiti
    app.state.services = KbServices(repos, graphiti, settings)
    app.state.maintenance = MaintenanceService(repos, graphiti)
    app.state.auth = AuthService(repos, settings)
    from ecosystem_kb.api.oauth_server import AuthorizationServer

    oauth_repos = {
        "sessions": SessionRepository(control_plane.session),
        "clients": OauthClientRepository(control_plane.session),
        "pending": PendingAuthorizationRepository(control_plane.session),
        "codes": AuthorizationCodeRepository(control_plane.session),
        "tokens": TokenRepository(control_plane.session),
        "consents": ConsentRepository(control_plane.session),
        "audit": repos.audit,
    }
    app.state.oauth = AuthorizationServer(oauth_repos, settings)
    app.state.oauth_sessions = oauth_repos["sessions"]
    app.state.auth.oauth = app.state.oauth
    app.state.owner_verifier = None
    # Mirror onto the MCP sub-app (see create_app: McpEndpointApp wraps the
    # FastMCP ASGI app; its middleware resolves services/auth from the
    # sub-app's own State because the mounted sub-app replaces scope['app']).
    for route in app.routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            continue
        sub_app = getattr(endpoint, "middleware", None)
        sub_app = getattr(sub_app, "sub_app", None)
        if sub_app is not None and hasattr(sub_app, "state"):
            sub_app.state.services = app.state.services
            sub_app.state.maintenance = app.state.maintenance
            sub_app.state.auth = app.state.auth
            sub_app.state.oauth = app.state.oauth
            break


__all__ = ["wire_app_state", "pytest_asyncio"]
