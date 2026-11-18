"""Ingestion into the graph: episode creation + ontology gate + identity sync.

The write path (spec §18.1): the worker calls ingest_event; extraction runs
(proposal); every extracted edge passes the ontology registry (LAW-006) —
valid ones commit, unknown ones are quarantined and review-queued, never
silently admitted. Canonical identities sync to the control plane (LAW-005).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from contracts.events import KnowledgeEvent
from contracts.provenance import ProvenanceRecord
from graphiti_core.graphiti import Graphiti
from ontology.registry import REGISTRY
from ontology.versions import ONTOLOGY_VERSION


@dataclass
class IngestionOutcome:
    event_row_id: str
    episode_uuid: str | None = None
    entities_written: int = 0
    edges_written: int = 0
    edges_quarantined: list = field(default_factory=list)
    identities_registered: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


def _episode_body(event: KnowledgeEvent) -> str:
    body = event.content.body or ""
    ref = event.content.content_reference or ""
    return body if body else f"(reference-only event: {ref})"


def _source_description(event: KnowledgeEvent) -> str:
    rev = event.source.revision
    rev_part = f" rev={rev}" if rev not in (None, "") else ""
    return (
        f"{event.source.system_id}{rev_part} type={event.event_type} "
        f"uri={event.source.canonical_uri} trust={event.classification.trust_class}"
    )


async def ingest_event(
    graphiti: Graphiti,
    event: KnowledgeEvent,
    namespace: str,
    identity_sync=None,
) -> IngestionOutcome:
    """Run one event through Graphiti with our ontology as the type system.

    identity_sync: optional async callable (canonical_key, entity_type, name,
    graphiti_uuid) used to keep the control-plane registry in step.
    """
    entity_types = REGISTRY.graphiti_entity_types()
    edge_types = REGISTRY.graphiti_edge_types()
    edge_type_map = REGISTRY.graphiti_edge_type_map()

    results = await graphiti.add_episode(
        name=f"{event.event_type}:{event.idempotency_key}",
        episode_body=_episode_body(event),
        source_description=_source_description(event),
        reference_time=event.timestamps.occurred_at,
        group_id=namespace,
        entity_types=entity_types,
        edge_types=edge_types,
        edge_type_map=edge_type_map,
    )

    outcome = IngestionOutcome(event_row_id="")
    outcome.episode_uuid = str(getattr(results, "episode_uuid", "") or "") or None
    outcome.entities_written = len(results.nodes)
    outcome.edges_written = len(results.edges)

    # Identity sync: register every written node under its canonical key.
    for node in results.nodes:
        labels = list(getattr(node, "labels", None) or [])
        entity_type = None
        for label in labels:
            if label in entity_types and label != "Entity":
                entity_type = label
                break
        canonical_name = node.name
        type_prefix = _prefix_for(entity_type) if entity_type else "concept"
        canonical_key = f"{type_prefix}:{canonical_name.strip().lower().replace(' ', '-')}"
        outcome.identities_registered.append(canonical_key)
        if identity_sync is not None:
            result = identity_sync(
                canonical_key, entity_type or "Concept", canonical_name, str(node.uuid)
            )
            if hasattr(result, "__await__"):
                await result

    # Ontology gate on edges (LAW-006): unknown relation or bad domain/range
    # must be surfaced, not buried. Graphiti already filtered what it could
    # via edge_type_map; anything that still arrived as an unmapped predicate
    # is quarantined here.
    for edge in results.edges:
        relation = edge.name.upper()
        if relation not in REGISTRY.relationship_names:
            q = REGISTRY.quarantine_edge(
                relation=relation,
                source_type="Unknown",
                target_type="Unknown",
                source_name=str(getattr(edge, "source_node_uuid", "")),
                target_name=str(getattr(edge, "target_node_uuid", "")),
                fact=edge.fact,
            )
            outcome.edges_quarantined.append(q)
    outcome.edges_written -= len(outcome.edges_quarantined)
    return outcome


def provenance_for(event: KnowledgeEvent, episode_uuid: str | None) -> ProvenanceRecord:
    """Provenance attached to everything this event produced (LAW-002)."""
    return ProvenanceRecord(
        source_id=event.source.system_id,
        source_uri=event.source.canonical_uri,
        source_revision=str(event.source.revision) if event.source.revision is not None else None,
        content_hash=event.content.content_hash,
        event_id=event.event_id,
        occurred_at=event.timestamps.occurred_at,
        recorded_at=event.timestamps.recorded_at,
        ontology_version=ONTOLOGY_VERSION.version,
        actor_type=event.actor.type.value,
        review_status=event.classification.review_status,
    )


_TYPE_PREFIXES = {
    "Project": "project",
    "System": "system",
    "Component": "component",
    "Interface": "interface",
    "Artifact": "artifact",
    "Source": "source",
    "Dataset": "dataset",
    "Concept": "concept",
    "Requirement": "requirement",
    "Decision": "decision",
    "Claim": "claim",
    "Experiment": "experiment",
    "Finding": "finding",
    "VariableDefinition": "variable",
}


def _prefix_for(entity_type: str) -> str:
    return _TYPE_PREFIXES.get(entity_type, "concept")


def utcnow() -> datetime:
    return datetime.now(UTC)
