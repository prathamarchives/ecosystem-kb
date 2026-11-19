"""Ingestion into the graph: extraction proposals → ontology gate → commit.

Gate 3 (review P1) — the pre-commit ontology gate. Graphiti's add_episode
extracts AND writes in one call; it cannot validate-before-commit. So the
adapter enforces the ontology on BOTH sides of that call:

  BEFORE: the episode body is composed with strict_edge_types=True so
  graphiti itself only keeps edges whose (source labels, relation, target
  labels) signature appears in our edge_type_map (enforced by
  enforce_edge_type_signatures — mismatches are DROPPED, not renamed).

  AFTER: every returned edge is re-validated against the registry by its
  actual relation name and endpoint labels. Anything that is not a known
  canonical relation — including graphiti's RELATES_TO fallback — is
  quarantined: recorded in the review queue, excluded from counts, and
  (best-effort) deleted from the graph so canonical search stays clean.

The episode node remains as evidence (LAW-002); invalid RELATIONSHIPS never
enter the canonical edge set (LAW-006).
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
    # References for provenance recording (Gate 4): the actual written
    # objects, so the caller can link edge/entity uuids to the event.
    edges_written_refs: list = field(default_factory=list)
    entities_written_refs: list[str] = field(default_factory=list)


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


def _node_entity_type(node, entity_types: dict) -> str | None:
    for label in getattr(node, "labels", None) or []:
        if label in entity_types and label != "Entity":
            return label
    return None


async def ingest_event(
    graphiti: Graphiti,
    event: KnowledgeEvent,
    namespace: str,
    identity_sync=None,
) -> IngestionOutcome:
    """Run one event through Graphiti with the ontology enforced around the
    write (see module docstring). identity_sync: optional callable
    (canonical_key, entity_type, name, graphiti_uuid), sync or async."""
    entity_types = REGISTRY.graphiti_entity_types()
    edge_types = REGISTRY.graphiti_edge_types()
    edge_type_map = REGISTRY.graphiti_edge_type_map()

    # add_episode_bulk is the API that exposes strict_edge_types (0.31.2):
    # extracted edges that do not use one of our canonical edge types are
    # DROPPED by graphiti before commit — the pre-commit ontology gate.
    from graphiti_core.nodes import EpisodeType
    from graphiti_core.utils.bulk_utils import RawEpisode

    raw = RawEpisode(
        name=f"{event.event_type}:{event.idempotency_key}",
        content=_episode_body(event),
        source_description=_source_description(event),
        source=EpisodeType.text,
        reference_time=event.timestamps.occurred_at,
    )
    results = await graphiti.add_episode_bulk(
        [raw],
        group_id=namespace,
        entity_types=entity_types,
        edge_types=edge_types,
        edge_type_map=edge_type_map,
        strict_edge_types=True,
    )

    outcome = IngestionOutcome(event_row_id="")
    episodes = getattr(results, "episodes", None) or []
    outcome.episode_uuid = str(episodes[0].uuid) if episodes else None
    outcome.entities_written = len(results.nodes)

    # Identity sync: register every written node under its canonical key.
    for node in results.nodes:
        entity_type = _node_entity_type(node, entity_types)
        canonical_name = node.name
        type_prefix = _prefix_for(entity_type) if entity_type else "concept"
        canonical_key = f"{type_prefix}:{canonical_name.strip().lower().replace(' ', '-')}"
        outcome.identities_registered.append(canonical_key)
        outcome.entities_written_refs.append(str(node.uuid))
        if identity_sync is not None:
            result = identity_sync(
                canonical_key, entity_type or "Concept", canonical_name, str(node.uuid)
            )
            if hasattr(result, "__await__"):
                await result

    # Post-write ontology gate (LAW-006): every returned edge must carry a
    # CANONICAL relation name AND a valid (source, relation, target) type
    # triple. RELATES_TO is graphiti's fallback for unmapped predicates —
    # it is never a canonical fact, so it is quarantined and removed.
    vocabulary = set(REGISTRY.relationship_names)
    for edge in results.edges:
        relation = edge.name.upper()
        if relation in vocabulary and _edge_domain_range_ok(edge, relation):
            outcome.edges_written += 1
            outcome.edges_written_refs.append(edge)
            continue
        outcome.edges_quarantined.append(
            REGISTRY.quarantine_edge(
                relation=relation,
                source_type="Unknown",
                target_type="Unknown",
                source_name=str(getattr(edge, "source_node_uuid", "")),
                target_name=str(getattr(edge, "target_node_uuid", "")),
                fact=edge.fact,
            )
        )
        await _delete_edge_best_effort(graphiti, namespace, edge)

    return outcome


def _edge_domain_range_ok(edge, relation: str) -> bool:
    """Validate the actual endpoint labels of a written edge (Gate 3)."""
    labels = list(getattr(edge, "source_node_labels", None) or [])
    if not labels:
        return True  # labels unavailable on this edge object; name check only
    return any(
        REGISTRY.validate_relationship(relation, s, t) is not None
        for s in _non_generic(labels)
        for t in _non_generic(list(getattr(edge, "target_node_labels", None) or ["Entity"]))
    )


def _non_generic(labels: list[str]) -> list[str]:
    return [label for label in labels if label != "Entity"] or ["Entity"]


async def _delete_edge_best_effort(graphiti: Graphiti, namespace: str, edge) -> None:
    """Compensation: remove a non-canonical edge from the graph. The
    quarantine record in the review queue preserves the evidence."""
    try:
        from graphiti_core.edges import EntityEdge

        if isinstance(edge, EntityEdge):
            await edge.delete(graphiti.driver)
    except Exception:  # noqa: BLE001 — compensation must never break ingestion
        pass


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
