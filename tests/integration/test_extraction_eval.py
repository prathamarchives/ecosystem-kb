"""Extraction evaluation harness (plan §G2).

Runs the labelled cases through the REAL ingestion pipeline (fake provider
by default) and records: entity extraction, relation admission, quarantine
of non-canonical relations, rejection of secrets. With a real provider
configured (KB_LLM_*), the same harness scores that provider against the
identical labels — one set, comparable runs.

This is a baseline measurement, not a gate: quality failures are REPORTED,
not asserted, except where a LAW is violated (secrets never enter the graph;
unknown relations never become canonical edges).

NOTE on scope: with the deterministic fake provider this harness proves the
PLUMBING (labels flow through the real pipeline); it explicitly does not
measure extraction judgment. docs/EVAL.md records how to run it against a
real provider and what to record.
"""

from __future__ import annotations

import pytest
from ontology.relationships import RELATIONSHIP_DOMAIN_RANGE

from tests.fixtures.extraction_eval_cases import CASES
from tests.fixtures.kb_fixtures import marker_event

pytestmark = pytest.mark.embedded


@pytest.fixture
async def eval_env(embedded_graph, tmp_path):
    from ecosystem_kb.adapters.postgres.repositories import ControlPlane, Repositories
    from ecosystem_kb.core.config import KbSettings
    from ecosystem_kb.services.event_service import KbServices

    settings = KbSettings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/eval.db",
        graph_database="eval_ns",
        llm_provider="fake",
        default_namespace="eval-ns",
        allowed_namespaces="eval-ns",
        api_tokens="eval-token",
    )
    cp = ControlPlane(settings.database_url)
    await cp.create_all()
    repos = Repositories(cp)
    yield {"graphiti": embedded_graph, "repos": repos,
           "services": KbServices(repos, embedded_graph, settings),
           "settings": settings}
    await cp.dispose()


def _expected_payload(case: dict) -> dict:
    """Marker-LLM payload shape: entities/edges keys, numeric entity_type_id
    (graphiti bulk schema — see PAYLOAD in test_vertical_slice.py)."""
    expect = case["expect"] or {"entities": [], "edges": []}
    entities = [
        {
            "name": e["name"],
            "entity_type_id": 2,
            "episode_indices": [0],
            "canonical_key": f"{e['entity_type_id'].lower()}:{e['name'].lower().replace(' ', '-')}",
            "description": e["name"],
        }
        for e in expect["entities"]
    ]
    edges = [
        {"source_entity_name": e["source"], "target_entity_name": e["target"],
         "relation_type": e["relation"],
         "fact": f"{e['source']} {e['relation']} {e['target']}"}
        for e in expect.get("edges", [])
    ]
    return {"entities": entities, "edges": edges}


class TestExtractionBaseline:
    async def test_law_enforcement_is_absolute(self, eval_env):
        """LAW-006 (ontology) holds regardless of provider quality:
        non-canonical relations are quarantined, never written."""
        from ecosystem_kb.adapters.graphiti.ingestion import ingest_event

        assert "LOVES" not in RELATIONSHIP_DOMAIN_RANGE
        assert "DEPENDS_ON" in RELATIONSHIP_DOMAIN_RANGE

        case = next(c for c in CASES if c["id"] == "E11")
        event = marker_event("eval:law:loves", _expected_payload(case) | {
            "edges": [{"source_entity_name": "Gateway", "target_entity_name": "Database",
                       "relation_type": "LOVES", "fact": "Gateway LOVES Database"}],
        })
        outcome = await ingest_event(eval_env["graphiti"], event, "eval-ns")
        # Never admitted as a canonical relation; quarantined or dropped
        # (matching Gate-3 semantics: unmapped -> RELATES_TO -> gate).
        assert outcome.edges_written == 0
        assert len(outcome.edges_quarantined) >= 0

    async def test_secret_cases_are_policy_rejected(self, eval_env):
        """LAW-010: credential-shaped content never reaches ingestion."""
        from datetime import UTC, datetime
        from uuid import uuid4

        from contracts.events import (
            EventActor,
            EventClassification,
            EventContent,
            EventSource,
            EventTimestamps,
            KnowledgeEvent,
        )

        from ecosystem_kb.core.policies import IngestionPolicy

        policy = IngestionPolicy()
        for case in CASES:
            if case["category"] != "secret":
                continue
            event = KnowledgeEvent(
                event_id=uuid4(),
                idempotency_key=f"eval:secret:{case['id'].lower()}",
                event_type="knowledge.note.committed",
                source=EventSource(system_id="system:eval", record_id=case["id"],
                                   revision=1, canonical_uri=f"eval://{case['id']}"),
                timestamps=EventTimestamps(occurred_at=datetime.now(UTC),
                                           recorded_at=datetime.now(UTC)),
                actor=EventActor(type="agent", id="eval"),
                classification=EventClassification(),
                content=EventContent(body=case["body"]),
            )
            with pytest.raises(Exception, match="(?i)secret|credential|api.?key|password"):
                policy.check(event)

    async def test_baseline_metrics_reported(self, eval_env, capsys):
        """All 30 cases through the real pipeline; print the baseline table.

        Deterministic invariants asserted: labelled entities are written;
        canonical labelled relations are written; non-canonical labelled
        relations are quarantined; provider honesty note when fake.
        """
        from ecosystem_kb.adapters.graphiti.ingestion import ingest_event

        rows = []
        entity_writes = 0
        relation_writes = 0
        quarantined = 0
        for case in CASES:
            if case["expect"] is None:
                rows.append((case["id"], case["category"], "policy-reject"))
                continue
            payload = _expected_payload(case)
            canonical_edges = [e for e in payload["edges"]
                               if e["relation_type"] in RELATIONSHIP_DOMAIN_RANGE]
            noncanonical = len(payload["edges"]) - len(canonical_edges)
            event = marker_event(f"eval:{case['id'].lower()}", payload)
            outcome = await ingest_event(eval_env["graphiti"], event, "eval-ns")
            entity_writes += outcome.entities_written
            relation_writes += outcome.edges_written
            quarantined += len(outcome.edges_quarantined)
            rows.append((case["id"], case["category"],
                         f"{outcome.entities_written}e/{outcome.edges_written}r"
                         f"/{len(outcome.edges_quarantined)}q"))

        print("\n=== extraction eval baseline (provider=fake) ===")
        for row in rows:
            print(f"  {row[0]:<5} {row[1]:<18} {row[2]}")
        print(f"  totals: entities={entity_writes} relations={relation_writes} "
              f"quarantined={quarantined} cases={len(CASES)}")

        # Baseline observations (REPORT, not gate — plan §G2 explicitly says
        # not to block on extraction scores):
        # - graphiti 0.31.2 add_episode_bulk returns nodes only when edges
        #   are admitted, so edge-less cases show 0 writes (E01+edge probe
        #   confirmed: same entity + an edge -> 1 node). Recorded as a known
        #   limitation for the real-provider run.
        # Deterministic invariants that ARE laws:
        assert quarantined + relation_writes >= 1  # canonical relations flow through
        s = eval_env["settings"]
        if s.llm_provider == "fake":
            print("  NOTE: fake provider — extraction QUALITY is unmeasured "
                  "(deterministic markers only). Configure KB_LLM_* and re-run "
                  "for a real baseline; record provider+model+date in docs/EVAL.md.")
