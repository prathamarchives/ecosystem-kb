"""Plug-and-play MCP tests (plan §D exit gate, AT-03/04/09).

PROTOCOL layer: a real uvicorn server (thread) running the production app
factory against an embedded FalkorDB; every interaction is genuine
Streamable HTTP — initialize, session handling, tools/list, tools/call —
exactly what a remote MCP client and the tunnel exercise. The app builds
its own graphiti inside uvicorn's event loop (no cross-loop sharing); the
test loop only seeds/inspects through separate connections.

SERVICE layer: kb_capture/kb_context semantics incl. authorization
negatives for the high-level surface (WP-F additions).
"""

from __future__ import annotations

import json
import threading
import time

import httpx
import pytest

pytestmark = pytest.mark.embedded

from tests.integration.test_vertical_slice import PAYLOAD  # noqa: E402


def _parse_sse(text: str) -> dict:
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    raise AssertionError(f"no data line in body: {text[:200]}")


@pytest.fixture
async def server(tmp_path):
    """Real HTTP server: embedded FalkorDB + uvicorn thread + SQLite CP."""
    from ecosystem_kb.adapters.graphiti import EmbeddedFalkorDB
    from ecosystem_kb.core.config import KbSettings

    with EmbeddedFalkorDB() as embedded:
        db = tmp_path / "proto.db"
        settings = KbSettings(
            database_url=f"sqlite+aiosqlite:///{db}",
            graph_host="127.0.0.1",
            graph_port=embedded.port,
            graph_database="test_ns",
            llm_provider="fake",
            default_namespace="ecosystem-dev",
            allowed_namespaces="ecosystem-dev",
            api_tokens="mcp-test-token",
        )
        from ecosystem_kb.api.app import create_app

        app = create_app(settings=settings)

        import uvicorn

        port = 8900 + (id(tmp_path) % 500)  # pseudo-unique per test
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{port}"
        for _ in range(100):  # wait for readiness
            try:
                if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                    break
            except Exception:
                time.sleep(0.1)
        yield {
            "base": base,
            "token": "mcp-test-token",
            "settings": settings,
            "port": embedded.port,
            "db": str(db),
        }
        server.should_exit = True
        thread.join(timeout=10)


class McpClient:
    """Minimal Streamable HTTP MCP client for tests."""

    def __init__(self, base: str, token: str) -> None:
        self.base = base
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        self.session: str | None = None

    async def call(self, method: str, params: dict | None = None) -> httpx.Response:
        headers = dict(self.headers)
        if self.session:
            headers["mcp-session-id"] = self.session
        async with httpx.AsyncClient(timeout=15) as c:
            return await c.post(f"{self.base}/mcp/", headers=headers,
                                json={"jsonrpc": "2.0", "id": 1, "method": method,
                                      "params": params or {}})

    async def initialize(self) -> dict:
        r = await self.call("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "kb-test", "version": "0.0.0"},
        })
        assert r.status_code == 200, r.text[:300]
        self.session = r.headers.get("mcp-session-id")
        await self.call("notifications/initialized")
        return _parse_sse(r.text)

    async def list_tools(self) -> list[dict]:
        r = await self.call("tools/list")
        assert r.status_code == 200
        return _parse_sse(r.text)["result"]["tools"]

    async def tool(self, name: str, arguments: dict) -> dict:
        r = await self.call("tools/call", {"name": name, "arguments": arguments})
        assert r.status_code == 200, r.text[:300]
        body = _parse_sse(r.text)
        assert "error" not in body, body
        result = body["result"]
        if result.get("isError"):
            raise RuntimeError(result["content"][0]["text"][:400])
        sc = result.get("structuredContent")
        if sc is not None:
            return sc
        return json.loads(result["content"][0]["text"])


class TestMcpProtocol:
    """AT-03: protocol compatibility over real Streamable HTTP + uvicorn."""

    async def test_initialize_handshake(self, server):
        client = McpClient(server["base"], server["token"])
        body = await client.initialize()
        assert "serverInfo" in body["result"]
        assert client.session  # session header issued

    async def test_missing_token_rejected_before_protocol(self, server):
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{server['base']}/mcp/",
                             headers={"Content-Type": "application/json",
                                      "Accept": "application/json, text/event-stream"},
                             json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                   "params": {"protocolVersion": "2025-06-18",
                                              "capabilities": {},
                                              "clientInfo": {"name": "x", "version": "0"}}})
        assert r.status_code == 401  # WP-F: no protocol without credentials

    async def test_invalid_token_rejected(self, server):
        client = McpClient(server["base"], "forged-token-value")
        r = await client.call("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "x", "version": "0"},
        })
        assert r.status_code == 401  # WP-F: validation before protocol init

    async def test_tools_listed(self, server):
        client = McpClient(server["base"], server["token"])
        await client.initialize()
        names = {t["name"] for t in await client.list_tools()}
        assert {"kb_search", "kb_context", "kb_capture", "kb_get_provenance",
                "kb_submit_event"} <= names

    async def test_capture_over_http_and_ledger_row(self, server):
        """AT-09 core: plain fields in, receipt out, ledger row exists."""
        client = McpClient(server["base"], server["token"])
        await client.initialize()
        receipt = await client.tool("kb_capture", {
            "kind": "decision",
            "title": "Use Streamable HTTP for remote MCP",
            "content": "The shared knowledge service exposes one remote Streamable HTTP endpoint.",
            "project": "ecosystem-kb",
            "status": "proposed",
            "tags": ["mcp", "architecture"],
        })
        assert receipt["event_id"]
        assert receipt["kind"] == "decision"
        assert receipt["duplicate"] is False
        assert "unreviewed" in receipt["epistemic_note"]

        # Ledger row exists with the authenticated actor, not a payload claim.
        from sqlalchemy import text

        from ecosystem_kb.adapters.postgres.repositories import ControlPlane

        cp = ControlPlane(server["settings"].database_url)
        try:
            async with cp.session() as s:
                res = await s.execute(
                    text("SELECT event_payload FROM kb_events WHERE event_id = :id"),
                    {"id": receipt["event_id"]},
                )
                serialized = json.loads(res.scalar())
            assert serialized["actor"]["id"] == "local-dev"
            assert serialized["actor"]["type"] in ("agent", "application", "system")
            assert serialized["payload"]["capture_kind"] == "decision"
            assert serialized["payload"]["captured_by"] == "local-dev"
        finally:
            await cp.dispose()

    async def test_capture_invalid_kind_rejected(self, server):
        client = McpClient(server["base"], server["token"])
        await client.initialize()
        with pytest.raises(RuntimeError):
            await client.tool("kb_capture", {"kind": "gossip", "title": "x", "content": "y"})

    async def test_reader_client_cannot_capture(self, server):
        """AT-05 piece: scope-restricted client cannot write."""
        from ecosystem_kb.adapters.postgres.repositories import ControlPlane, Repositories

        cp = ControlPlane(server["settings"].database_url)
        try:
            repos = Repositories(cp)
            reader_token = await repos.clients.register(
                "proto-reader", "Reader", ["kb:read"], ["ecosystem-dev"]
            )
        finally:
            await cp.dispose()
        client = McpClient(server["base"], reader_token)
        await client.initialize()
        with pytest.raises(RuntimeError):
            await client.tool("kb_capture", {"kind": "note", "title": "x", "content": "y"})

    async def test_revoked_client_rejected(self, server):
        """AT-05 piece: revocation takes effect on the next request."""
        from ecosystem_kb.adapters.postgres.repositories import ControlPlane, Repositories

        cp = ControlPlane(server["settings"].database_url)
        try:
            repos = Repositories(cp)
            token = await repos.clients.register("proto-revoked", "R", ["kb:read"], ["ecosystem-dev"])
            await repos.clients.set_status("proto-revoked", "revoked", actor="test")
        finally:
            await cp.dispose()
        client = McpClient(server["base"], token)
        r = await client.call("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "x", "version": "0"},
        })
        assert r.status_code == 401  # revoked = rejected before the protocol

    async def test_context_over_http(self, server):
        client = McpClient(server["base"], server["token"])
        await client.initialize()
        result = await client.tool("kb_context", {
            "query": "What do I need to know before changing the MCP gateway?",
            "project": "ecosystem-kb",
            "limit": 5,
        })
        for key in ("decisions", "constraints", "facts", "contradictions", "guidance"):
            assert key in result
        assert result["project"] == "ecosystem-kb"

    async def test_search_over_http_after_direct_seed(self, server):
        """Seed via the test loop's own graphiti; read via the server."""
        from falkordb.asyncio import FalkorDB as AsyncFalkorDB

        from ecosystem_kb.adapters.graphiti import build_graphiti
        from ecosystem_kb.adapters.graphiti.ingestion import ingest_event
        from ecosystem_kb.core.config import KbSettings
        from tests.fixtures.kb_fixtures import marker_event

        seed_settings = KbSettings(graph_database="test_ns", llm_provider="fake")
        fclient = AsyncFalkorDB(host="127.0.0.1", port=server["port"])
        graphiti = build_graphiti(seed_settings, falkor_client=fclient)
        try:
            event = marker_event("proto:seed:1", PAYLOAD)
            await ingest_event(graphiti, event, "ecosystem-dev")
        finally:
            init_task = getattr(graphiti.driver, "_init_task", None)
            if init_task is not None and not init_task.done():
                try:
                    await init_task
                except Exception:
                    pass
            await graphiti.close()

        client = McpClient(server["base"], server["token"])
        await client.initialize()
        result = await client.tool("kb_search", {"query": "Arivoo depends", "num_results": 5})
        assert any("depends on PostgreSQL" in f.get("fact", "") for f in result["facts"])

    async def test_two_clients_share_knowledge(self, server):
        """AT-04: client A captures; client B (separate credential) retrieves."""
        from ecosystem_kb.adapters.postgres.repositories import ControlPlane, Repositories

        cp = ControlPlane(server["settings"].database_url)
        try:
            repos = Repositories(cp)
            token_b = await repos.clients.register("client-b", "B", ["kb:read"], ["ecosystem-dev"])
        finally:
            await cp.dispose()

        a = McpClient(server["base"], server["token"])
        await a.initialize()
        receipt = await a.tool("kb_capture", {
            "kind": "finding",
            "title": "Two-client shared state works",
            "content": "Client A captured this; client B must retrieve the same ledger row.",
        })
        # B reads the event status through the REST surface (same origin).
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{server['base']}/v1/events/{receipt['event_id']}",
                            headers={"Authorization": f"Bearer {token_b}"})
            assert r.status_code == 200
            body = r.json()
            assert body["event_id"] == receipt["event_id"]
            assert body["status"] in ("accepted", "queued", "processing", "completed")
