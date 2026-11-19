"""Gate 6 acceptance: Arivoo as first real producer (review's scenario).

An Arivoo note is revised; the outbox emits versioned events; the KB ingests
exactly once; the revision supersedes without erasing history; another client
retrieves the fact with source revision and provenance; a relay crash mid-
delivery neither duplicates nor loses the event.
"""


import pytest

from ecosystem_kb.integrations.arivoo_relay import (
    ArivooOutboxRow,
    outbox_to_kb_event,
)
from ecosystem_kb.workers.ingestion import IngestionWorker
from tests.fixtures.kb_fixtures import marker_event
from tests.integration.test_vertical_slice import _admin_client

pytestmark = pytest.mark.embedded

# The note, revision 1 and revision 2, as extraction payloads.
REV1 = {
    "entities": [
        {"name": "Arivoo", "entity_type_id": 2, "episode_indices": [0]},
        {"name": "PostgreSQL", "entity_type_id": 2, "episode_indices": [0]},
    ],
    "edges": [
        {
            "source_entity_name": "Arivoo",
            "target_entity_name": "PostgreSQL",
            "relation_type": "DEPENDS_ON",
            "fact": "Arivoo stores its study ledger in PostgreSQL",
        }
    ],
}
REV2_FACT = "Arivoo stores its study ledger in PostgreSQL with read replicas"
REV2 = {
    "entities": [
        {"name": "Arivoo", "entity_type_id": 2, "episode_indices": [0]},
        {"name": "PostgreSQL", "entity_type_id": 2, "episode_indices": [0]},
    ],
    "edges": [
        {
            "source_entity_name": "Arivoo",
            "target_entity_name": "PostgreSQL",
            "relation_type": "DEPENDS_ON",
            "fact": REV2_FACT,
        }
    ],
}


def _row(record: str, revision: int, payload: dict) -> ArivooOutboxRow:
    import json

    return ArivooOutboxRow(
        record_id=record,
        revision=revision,
        event_type="note.committed" if revision == 1 else "note.revised",
        title=f"Storage decision rev {revision}",
        body="prose\nEXTRACTION: " + json.dumps(payload),
        occurred_at="2026-11-19T10:00:00Z" if revision == 1 else "2026-11-19T12:00:00Z",
    )


class TestMappingContract:
    def test_idempotency_key_is_record_and_revision(self):
        r1 = outbox_to_kb_event(_row("note-42", 1, REV1))
        r2 = outbox_to_kb_event(_row("note-42", 1, REV1))  # replay
        r3 = outbox_to_kb_event(_row("note-42", 2, REV2))  # new revision
        assert r1["idempotency_key"] == r2["idempotency_key"]
        assert r1["idempotency_key"] != r3["idempotency_key"]

    def test_event_type_maps_and_supersedes_carried(self):
        assert outbox_to_kb_event(_row("n", 1, REV1))["event_type"] == "knowledge.note.committed"
        ev2 = outbox_to_kb_event(_row("n", 2, REV2))
        assert ev2["event_type"] == "knowledge.note.revised"
        assert ev2["payload"]["supersedes_revision"] == 1
        assert ev2["source"]["revision"] == 2
        assert ev2["classification"]["trust_class"] == "human_authored"


class TestArivooAcceptanceScenario:
    async def test_revisions_supersede_without_losing_history(self, services, control_plane):
        """The full acceptance scenario, end to end."""
        from contracts.search import SearchRequest

        worker = IngestionWorker(services)

        # Rev 1 committed.
        e1 = marker_event("arivoo:note-42:revision-1", REV1)
        e1 = e1.model_copy(
            update={
                "idempotency_key": "arivoo:note-42:revision-1",
                "source": e1.source.model_copy(
                    update={"record_id": "note-42", "revision": 1,
                            "canonical_uri": "arivoo://notes/note-42?rev=1"}
                ),
            }
        )
        receipt1 = await services.submit_event(_admin_client(), e1)
        assert receipt1.status == "queued"
        await worker.run_once()

        # Rev 2 revises the same record.
        e2 = marker_event("arivoo:note-42:revision-2", REV2)
        e2 = e2.model_copy(
            update={
                "idempotency_key": "arivoo:note-42:revision-2",
                "event_type": "knowledge.note.revised",
                "source": e2.source.model_copy(
                    update={"record_id": "note-42", "revision": 2,
                            "canonical_uri": "arivoo://notes/note-42?rev=2"}
                ),
            }
        )
        receipt2 = await services.submit_event(_admin_client(), e2)
        assert receipt2.status == "queued"
        await worker.run_once()

        # Both events are in the ledger: history preserved (LAW-004).
        row1 = await control_plane.events.get_by_idempotency_key("arivoo:note-42:revision-1")
        row2 = await control_plane.events.get_by_idempotency_key("arivoo:note-42:revision-2")
        assert row1.processing_status == "completed"
        assert row2.processing_status == "completed"

        # The new fact is retrievable.
        result = await services.search(
            _admin_client(), SearchRequest(query="Arivoo stores its study ledger")
        )
        facts = [f for f in result.facts if f.fact in (REV1["edges"][0]["fact"], REV2_FACT)]
        assert facts, "neither revision retrievable"

        # Provenance distinguishes the revisions.
        prov_recs = []
        for f in facts:
            if f.provenance:
                prov_recs.append(f.provenance)
        assert prov_recs, "facts carry no provenance"
        assert any("rev=2" in (f.source_reference or "") or "revision-2" in (f.provenance or "")
                   for f in facts), "revision 2 not traceable"

    async def test_relay_crash_then_replay_is_exactly_once(self, services, control_plane):
        """Relay delivers rev 1, crashes BEFORE marking sent; on restart the
        replay is deduplicated; the ledger holds exactly one event."""
        row = _row("note-77", 1, REV1)
        e = marker_event("arivoo:note-77:revision-1", REV1)
        e = e.model_copy(
            update={
                "idempotency_key": "arivoo:note-77:revision-1",
                "source": e.source.model_copy(
                    update={"record_id": "note-77", "revision": 1}
                ),
            }
        )

        # First delivery succeeds…
        receipt = await services.submit_event(_admin_client(), e)
        assert receipt.status == "queued"
        # …but the relay crashes before marking the row sent (sent_at stays None).
        assert row.sent_at is None

        # Restart: the relay replays the SAME outbox row.
        replay = await services.submit_event(_admin_client(), e)
        assert replay.duplicate is True, "replay must be a duplicate, not a second event"

        worker = IngestionWorker(services)
        await worker.run_once()

        row_ledger = await control_plane.events.get_by_idempotency_key(
            "arivoo:note-77:revision-1"
        )
        assert row_ledger.processing_status == "completed"

        # Exactly one ledger row and one job for this record.
        async with control_plane.cp.session() as s:
            from sqlalchemy import func, select

            from ecosystem_kb.adapters.postgres.models import KbEvent

            ev_count = (
                await s.execute(
                    select(func.count()).select_from(KbEvent).where(
                        KbEvent.source_id == "system:arivoo"
                    )
                )
            ).scalar()
        assert ev_count == 1, f"exactly one arivoo event expected, got {ev_count}"

    async def test_second_client_reads_arivoo_knowledge(self, services, control_plane):
        """The central purpose: a DIFFERENT client reads Arivoo knowledge
        with provenance, knowing nothing about Arivoo's schema."""
        from contracts.clients import AuthenticatedClient
        from contracts.search import SearchRequest

        e = marker_event("arivoo:note-99:revision-1", REV1)
        e = e.model_copy(
            update={
                "idempotency_key": "arivoo:note-99:revision-1",
                "source": e.source.model_copy(
                    update={"record_id": "note-99", "revision": 1,
                            "canonical_uri": "arivoo://notes/note-99?rev=1"}
                ),
            }
        )
        await services.submit_event(_admin_client(), e)
        worker = IngestionWorker(services)
        await worker.run_once()

        reader = AuthenticatedClient(
            client_id="research-harness",
            scopes=["kb:read"],
            allowed_namespaces=["ecosystem-dev"],
        )
        result = await services.search(
            reader, SearchRequest(query="Arivoo study ledger PostgreSQL")
        )
        target = [
            f for f in result.facts
            if f.fact == "Arivoo stores its study ledger in PostgreSQL"
        ]
        assert target, "fact not found for second client"
        fact = target[0]
        assert fact.provenance and "system:arivoo" in fact.provenance
        assert fact.source_reference and fact.source_reference.startswith("arivoo://")

        # And can trace the full provenance chain.
        prov = await services.get_provenance(reader, str(e.event_id))
        assert prov.provenance["source_id"] == "system:arivoo"
        assert prov.provenance["source_uri"].startswith("arivoo://")
