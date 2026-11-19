"""Shared test fixtures: embedded FalkorDB, control plane, app factory."""

from __future__ import annotations

from datetime import UTC

import pytest
import pytest_asyncio
from contracts.clients import AuthenticatedClient


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def embedded_graph():
    """A real embedded FalkorDB server for the duration of a test."""
    from falkordb.asyncio import FalkorDB as AsyncFalkorDB

    from ecosystem_kb.adapters.graphiti import EmbeddedFalkorDB, build_graphiti
    from ecosystem_kb.core.config import KbSettings

    with EmbeddedFalkorDB() as embedded:
        settings = KbSettings(graph_database="test_ns", llm_provider="fake")
        client = AsyncFalkorDB(host="127.0.0.1", port=embedded.port)
        graphiti = build_graphiti(settings, falkor_client=client)
        await graphiti.build_indices_and_constraints()
        yield graphiti
        # The FalkorDriver schedules its own index build at __init__; drain it
        # so teardown never races an in-flight connection.
        init_task = getattr(graphiti.driver, "_init_task", None)
        if init_task is not None and not init_task.done():
            try:
                await init_task
            except Exception:
                pass
        await graphiti.close()


@pytest_asyncio.fixture
async def control_plane(tmp_path):
    from ecosystem_kb.adapters.postgres.repositories import ControlPlane, Repositories

    cp = ControlPlane(f"sqlite+aiosqlite:///{tmp_path}/kb_test.db")
    await cp.create_all()
    yield Repositories(cp)
    await cp.dispose()


@pytest_asyncio.fixture
async def services(embedded_graph, control_plane):
    from ecosystem_kb.core.config import KbSettings
    from ecosystem_kb.services.event_service import KbServices

    settings = KbSettings(
        graph_database="test_ns",
        llm_provider="fake",
        default_namespace="ecosystem-dev",
        allowed_namespaces="ecosystem-dev,ecosystem-test,ecosystem-prod",
    )
    # The source-ownership boundary is real: register the test producer.
    await control_plane.sources.upsert(
        source_id="system:arivoo",
        canonical_uri="arivoo://",
        source_type="note",
        owner_system="system:arivoo",
        trust_class="system_generated",
    )
    yield KbServices(control_plane, embedded_graph, settings)


@pytest.fixture
def admin_client():
    return AuthenticatedClient(
        client_id="test-admin",
        scopes=["kb:read", "kb:write", "kb:review", "kb:maintain", "kb:admin"],
        allowed_namespaces=["ecosystem-dev", "ecosystem-test", "ecosystem-prod"],
    )


@pytest.fixture
def reader_client():
    return AuthenticatedClient(
        client_id="test-reader",
        scopes=["kb:read"],
        allowed_namespaces=["ecosystem-dev"],
    )


def marker_event(idem: str, payload: dict, event_type: str = "knowledge.note.committed"):
    """Build a valid event whose body carries a deterministic extraction."""
    import json
    from datetime import datetime

    from contracts.events import KnowledgeEvent

    return KnowledgeEvent(
        idempotency_key=idem,
        event_type=event_type,
        source={
            "system_id": "system:arivoo",
            "record_id": idem,
            "revision": 1,
            "canonical_uri": f"arivoo://notes/{idem}",
        },
        timestamps={
            "occurred_at": "2026-11-18T10:00:00Z",
            "recorded_at": datetime.now(UTC).isoformat(),
        },
        content={"body": "test prose\nEXTRACTION: " + json.dumps(payload)},
    )
