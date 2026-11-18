"""API contract tests: async httpx over the real app (same loop as fixtures)."""

import httpx
import pytest

from tests.fixtures.kb_fixtures import marker_event
from tests.integration.test_vertical_slice import PAYLOAD

pytestmark = pytest.mark.embedded


@pytest.fixture
async def api(embedded_graph, tmp_path):
    from ecosystem_kb.adapters.postgres.repositories import ControlPlane, Repositories
    from ecosystem_kb.api.app import create_app
    from ecosystem_kb.api.auth import AuthService
    from ecosystem_kb.core.config import KbSettings
    from ecosystem_kb.services.event_service import KbServices
    from ecosystem_kb.services.maintenance_service import MaintenanceService

    settings = KbSettings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/api_test.db",
        graph_database="test_ns",
        llm_provider="fake",
        default_namespace="ecosystem-dev",
        allowed_namespaces="ecosystem-dev,ecosystem-test,ecosystem-prod",
        api_tokens="test-admin-token",
    )
    cp = ControlPlane(settings.database_url)
    await cp.create_all()
    app = create_app(settings=settings, graphiti=embedded_graph, control_plane=cp)
    # Lifespan-equivalent wiring (ASGITransport does not run lifespans).
    app.state.settings = settings
    app.state.control_plane = cp
    app.state.repos = Repositories(cp)
    app.state.graphiti = embedded_graph
    app.state.services = KbServices(app.state.repos, embedded_graph, settings)
    app.state.maintenance = MaintenanceService(app.state.repos, embedded_graph)
    app.state.auth = AuthService(app.state.repos, settings)

    reader_token = await app.state.repos.clients.register(
        "reader-1", "Reader", ["kb:read"], ["ecosystem-dev"]
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, app, reader_token
    await cp.dispose()


AUTH = {"Authorization": "Bearer test-admin-token"}


async def _drain(app):
    services = app.state.services
    while True:
        job = await services.repos.events.claim_next_job("test-worker")
        if job is None:
            break
        await services.process_event(job.event_row_id)
        await services.repos.events.complete_job(job.id)


class TestHealth:
    async def test_health(self, api):
        client, _, _ = api
        r = await client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    async def test_ready(self, api):
        client, _, _ = api
        r = await client.get("/ready")
        assert r.status_code == 200
        assert r.json()["ready"] is True


class TestAuth:
    async def test_missing_token_401(self, api):
        client, _, _ = api
        r = await client.post("/v1/search", json={"query": "x"})
        assert r.status_code == 401

    async def test_bad_token_401(self, api):
        client, _, _ = api
        r = await client.post(
            "/v1/search", json={"query": "x"}, headers={"Authorization": "Bearer nope"}
        )
        assert r.status_code == 401

    async def test_event_submission(self, api):
        client, _, _ = api
        event = marker_event("api:test:1", PAYLOAD)
        r = await client.post("/v1/events", json=event.model_dump(mode="json"), headers=AUTH)
        assert r.status_code == 200
        assert r.json()["status"] in ("queued", "quarantined")

    async def test_duplicate_event_idempotent(self, api):
        client, _, _ = api
        event = marker_event("api:dup:1", PAYLOAD)
        data = event.model_dump(mode="json")
        r1 = await client.post("/v1/events", json=data, headers=AUTH)
        r2 = await client.post("/v1/events", json=data, headers=AUTH)
        assert r1.status_code == r2.status_code == 200
        assert r2.json()["duplicate"] is True

    async def test_secret_event_rejected(self, api):
        client, _, _ = api
        event = marker_event("api:sec:1", PAYLOAD)
        data = event.model_dump(mode="json")
        data["content"]["body"] = "leak\napi_key = AKIAIOSFODNN7EXAMPLE"
        r = await client.post("/v1/events", json=data, headers=AUTH)
        assert r.status_code == 422
        assert "secret" in r.json()["error"]


class TestNamespace:
    async def test_reader_cannot_write(self, api):
        client, _, reader_token = api
        event = marker_event("api:reader:1", PAYLOAD)
        r = await client.post(
            "/v1/events",
            json=event.model_dump(mode="json"),
            headers={"Authorization": f"Bearer {reader_token}"},
        )
        assert r.status_code == 403

    async def test_namespace_escape_403(self, api):
        client, _, reader_token = api
        r = await client.post(
            "/v1/search",
            json={"query": "x", "namespace": "ecosystem-prod"},
            headers={"Authorization": f"Bearer {reader_token}"},
        )
        assert r.status_code == 403
        assert r.json()["error"] == "namespace_not_allowed"

    async def test_reader_search_within_own_namespace(self, api):
        client, _, reader_token = api
        r = await client.post(
            "/v1/search",
            json={"query": "anything"},
            headers={"Authorization": f"Bearer {reader_token}"},
        )
        assert r.status_code == 200
        assert r.json()["namespace"] == "ecosystem-dev"


class TestKnowledge:
    async def test_search_roundtrip(self, api):
        client, app, _ = api
        event = marker_event("api:search:1", PAYLOAD)
        r = await client.post("/v1/events", json=event.model_dump(mode="json"), headers=AUTH)
        assert r.status_code == 200
        await _drain(app)
        r = await client.post("/v1/search", json={"query": "Arivoo depends"}, headers=AUTH)
        assert r.status_code == 200
        facts = r.json()["facts"]
        assert any("depends on PostgreSQL" in f["fact"] for f in facts)
        assert all(f["relation"] == "DEPENDS_ON" for f in facts)

    async def test_provenance_lookup(self, api):
        client, _, _ = api
        event = marker_event("api:prov:1", PAYLOAD)
        r = await client.post("/v1/events", json=event.model_dump(mode="json"), headers=AUTH)
        event_id = r.json()["event_id"]
        r = await client.get(f"/v1/sources/{event_id}", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["provenance"]["event_id"] == event_id

    async def test_event_status_endpoint(self, api):
        client, _, _ = api
        event = marker_event("api:status:1", PAYLOAD)
        r = await client.post("/v1/events", json=event.model_dump(mode="json"), headers=AUTH)
        event_id = r.json()["event_id"]
        r = await client.get(f"/v1/events/{event_id}", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["status"] in ("queued", "accepted", "completed")
