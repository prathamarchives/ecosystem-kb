"""Admin API contract tests (onboarding repair §1, §6).

Real uvicorn + embedded FalkorDB + SQLite control plane; every interaction
is genuine HTTP against /v1/admin/* — the exact path the host CLI takes.
Covers the full client lifecycle, scope negatives, audit records, and the
ensure_admin_client seeding used by the compose migrate one-shot.
"""

from __future__ import annotations

import json
import threading
import time

import httpx
import pytest

pytestmark = pytest.mark.embedded


@pytest.fixture
async def server(tmp_path):
    """Real HTTP server: embedded FalkorDB + uvicorn thread + SQLite CP,
    with a pre-seeded admin client (exactly what compose 'migrate' does)."""
    from ecosystem_kb.adapters.graphiti import EmbeddedFalkorDB
    from ecosystem_kb.core.config import KbSettings

    with EmbeddedFalkorDB() as embedded:
        db = tmp_path / "admin.db"
        settings = KbSettings(
            database_url=f"sqlite+aiosqlite:///{db}",
            graph_host="127.0.0.1",
            graph_port=embedded.port,
            graph_database="test_ns",
            llm_provider="fake",
            default_namespace="ecosystem-dev",
            allowed_namespaces="ecosystem-dev",
        )
        from ecosystem_kb.adapters.postgres.repositories import ControlPlane, Repositories
        from ecosystem_kb.api.app import create_app

        admin_token = "test-admin-token-0123456789abcdef"
        cp = ControlPlane(settings.database_url)
        # The app lifespan runs migrate() itself; create_all() here first
        # would collide with the migration runner on the same schema.
        from ecosystem_kb.adapters.postgres.migrations import migrate

        await migrate(cp.engine)
        repos = Repositories(cp)
        seeded = await repos.clients.ensure_admin_client(token=admin_token)
        assert seeded == admin_token
        # Idempotency: a second call must NOT change the stored credential.
        assert await repos.clients.ensure_admin_client(token="other") == ""
        still_valid = await repos.clients.find_by_token(admin_token)
        assert still_valid is not None and still_valid.client_id == "kb-admin"
        await cp.dispose()

        app = create_app(settings=settings)

        import uvicorn

        port = 8950 + (id(tmp_path) % 400)
        uvi = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        thread = threading.Thread(target=uvi.run, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                    break
            except Exception:
                time.sleep(0.1)
        yield {"base": base, "admin_token": admin_token, "db": str(db)}
        uvi.should_exit = True
        thread.join(timeout=10)


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestAdminLifecycle:
    async def test_full_lifecycle_over_http(self, server):
        base, admin = server["base"], server["admin_token"]
        async with httpx.AsyncClient(timeout=10) as c:
            # create
            r = await c.post(f"{base}/v1/admin/clients", headers=_hdr(admin),
                             json={"client_id": "test-client", "role": "contributor"})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["scopes"] == ["kb:read", "kb:write"]
            token_a = body["token"]
            assert token_a

            # duplicate create -> 409 (repair §3: no accidental re-issue)
            r = await c.post(f"{base}/v1/admin/clients", headers=_hdr(admin),
                             json={"client_id": "test-client", "role": "reader"})
            assert r.status_code == 409

            # list shows it
            r = await c.get(f"{base}/v1/admin/clients", headers=_hdr(admin))
            assert r.status_code == 200
            ids = {c_["client_id"] for c_ in r.json()["clients"]}
            assert {"kb-admin", "test-client"} <= ids

            # the created client authenticates on the MCP surface...
            r = await c.post(f"{base}/mcp/", headers={
                **_hdr(token_a), "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"}}})
            assert r.status_code == 200

            # ...but NOT on the admin surface (no kb:admin scope)
            r = await c.get(f"{base}/v1/admin/clients", headers=_hdr(token_a))
            assert r.status_code == 403

            # rotate -> old token dies immediately, new one works
            r = await c.post(f"{base}/v1/admin/clients/test-client/rotate", headers=_hdr(admin))
            assert r.status_code == 200
            token_b = r.json()["token"]
            assert token_b != token_a
            r = await c.post(f"{base}/mcp/", headers={
                **_hdr(token_a), "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"}}})
            assert r.status_code == 401

            # revoke -> 401 on MCP
            r = await c.post(f"{base}/v1/admin/clients/test-client/revoke", headers=_hdr(admin))
            assert r.status_code == 200
            assert r.json()["status"] == "revoked"
            r = await c.post(f"{base}/mcp/", headers={
                **_hdr(token_b), "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"}}})
            assert r.status_code == 401

            # revoke of unknown client -> 404
            r = await c.post(f"{base}/v1/admin/clients/ghost/revoke", headers=_hdr(admin))
            assert r.status_code == 404

    async def test_invalid_role_rejected(self, server):
        base, admin = server["base"], server["admin_token"]
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{base}/v1/admin/clients", headers=_hdr(admin),
                             json={"client_id": "bad-role", "role": "superuser"})
            assert r.status_code == 422

    async def test_unauthenticated_rejected(self, server):
        base = server["base"]
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{base}/v1/admin/clients")
            assert r.status_code == 401

    async def test_stats_shape(self, server):
        base, admin = server["base"], server["admin_token"]
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{base}/v1/admin/stats", headers=_hdr(admin))
            assert r.status_code == 200
            body = r.json()
            assert set(body["knowledge"]) == {"entities", "provenance_links", "events"}
            assert "queued" in body["queue"]

    async def test_admin_operations_are_audited(self, server):
        """LAW-014: mutations leave audit rows naming the acting client."""
        from sqlalchemy import text

        from ecosystem_kb.adapters.postgres.repositories import ControlPlane

        base, admin = server["base"], server["admin_token"]
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(f"{base}/v1/admin/clients", headers=_hdr(admin),
                         json={"client_id": "audit-me", "role": "reader"})
            await c.post(f"{base}/v1/admin/clients/audit-me/revoke", headers=_hdr(admin))
        cp = ControlPlane(f"sqlite+aiosqlite:///{server['db']}")
        try:
            async with cp.session() as s:
                res = await s.execute(text(
                    "SELECT actor, action FROM kb_audit_log WHERE resource_id = 'audit-me' ORDER BY occurred_at"
                ))
                rows = res.fetchall()
        finally:
            await cp.dispose()
        actions = [(r[0], r[1]) for r in rows]
        assert ("kb-admin", "client.created") in actions
        assert ("kb-admin", "client.revoked") in actions

    async def test_admin_api_not_exposed_as_mcp_tools(self, server):
        """Repair §1: the admin surface must NOT leak into MCP tools."""
        base, admin = server["base"], server["admin_token"]
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{base}/mcp/", headers={
                **_hdr(admin), "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"}}})
            session = r.headers.get("mcp-session-id")
            r2 = await c.post(f"{base}/mcp/", headers={
                **_hdr(admin), "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "mcp-session-id": session or ""},
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            names = {t["name"] for t in json.loads(
                next(ln for ln in r2.text.splitlines() if ln.startswith("data:"))[5:]
            )["result"]["tools"]}
            assert not any("admin" in n or "client" in n for n in names), names
