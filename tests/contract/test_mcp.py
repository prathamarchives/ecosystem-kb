"""MCP server tests: tool registration, restricted surface, service parity."""

import pytest

from tests.fixtures.kb_fixtures import marker_event
from tests.integration.test_vertical_slice import PAYLOAD

pytestmark = pytest.mark.embedded


@pytest.fixture
async def mcp_app(embedded_graph, tmp_path):
    from ecosystem_kb.adapters.postgres.repositories import ControlPlane, Repositories
    from ecosystem_kb.api.app import create_app
    from ecosystem_kb.api.auth import AuthService
    from ecosystem_kb.core.config import KbSettings
    from ecosystem_kb.services.event_service import KbServices
    from ecosystem_kb.services.maintenance_service import MaintenanceService

    settings = KbSettings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/mcp_test.db",
        graph_database="test_ns",
        llm_provider="fake",
        default_namespace="ecosystem-dev",
        allowed_namespaces="ecosystem-dev,ecosystem-test,ecosystem-prod,mcp-unique-ns",
        api_tokens="mcp-test-token",
    )
    cp = ControlPlane(settings.database_url)
    await cp.create_all()
    from ecosystem_kb.adapters.postgres.repositories import Repositories as _R

    # Register the source the test producer submits on behalf of.
    await _R(cp).sources.upsert(
        source_id="system:arivoo",
        canonical_uri="arivoo://",
        source_type="note",
        owner_system="system:arivoo",
        trust_class="system_generated",
    )
    app = create_app(settings=settings, graphiti=embedded_graph, control_plane=cp)
    app.state.settings = settings
    app.state.control_plane = cp
    app.state.repos = Repositories(cp)
    app.state.graphiti = embedded_graph
    app.state.services = KbServices(app.state.repos, embedded_graph, settings)
    app.state.maintenance = MaintenanceService(app.state.repos, embedded_graph)
    app.state.auth = AuthService(app.state.repos, settings)
    yield app
    await cp.dispose()


class TestMcpTools:
    async def test_seven_tools_registered(self, mcp_app):
        from ecosystem_kb.mcp.server import build_mcp_server

        server = build_mcp_server()
        tools = await server.list_tools()
        names = {t.name for t in tools}
        assert names == {
            "kb_search",
            "kb_get_entity",
            "kb_get_context",
            "kb_get_provenance",
            "kb_submit_event",
            "kb_propose_update",
            "kb_get_maintenance_report",
        }
        # No destructive tools exposed (spec §23) — the restricted surface.
        assert not any("delete" in n or "clear" in n for n in names)

    async def test_search_tool_semantics(self, mcp_app):
        """kb_search behavior through the same service layer the tool calls."""
        from contracts.search import SearchRequest

        from ecosystem_kb.adapters.graphiti.ingestion import ingest_event

        ns = "mcp-unique-ns"
        event = marker_event("mcp:seed:1", PAYLOAD)
        await ingest_event(mcp_app.state.graphiti, event, ns)

        services = mcp_app.state.services
        client = await mcp_app.state.auth.authenticate_token("mcp-test-token")
        result = await services.search(
            client, SearchRequest(query="Arivoo depends", namespace=ns)
        )
        assert any("depends on PostgreSQL" in f.fact for f in result.facts)

    async def test_submit_tool_scope_enforced(self, mcp_app):
        """kb_submit_event goes through the same authz as REST."""
        from contracts.clients import AuthenticatedClient

        from ecosystem_kb.core.exceptions import AuthorizationError

        services = mcp_app.state.services
        reader = AuthenticatedClient(
            client_id="r", scopes=["kb:read"], allowed_namespaces=["ecosystem-dev"]
        )
        event = marker_event("mcp:scope:1", PAYLOAD)
        with pytest.raises(AuthorizationError):
            await services.submit_event(reader, event)

    async def test_maintenance_tool_requires_scope(self, mcp_app):
        from contracts.clients import AuthzAction

        from ecosystem_kb.core.exceptions import AuthorizationError

        reader_token = await mcp_app.state.repos.clients.register(
            "mcp-reader", "Reader", ["kb:read"], ["ecosystem-dev"]
        )
        client = await mcp_app.state.auth.authenticate_token(reader_token)
        assert "kb:maintain" not in client.scopes
        with pytest.raises(AuthorizationError):
            mcp_app.state.services._authorize(client, AuthzAction.MAINTENANCE_REPORT)
