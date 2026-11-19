"""Retrieval from the graph: search + entity context, contract-shaped.

Everything returned carries temporal and epistemic framing (LAW-015):
valid/invalid windows, review status, provenance summary where the control
plane has one. Contradictions surface, never hide.
"""

from __future__ import annotations

from contracts.search import (
    EntityContextResponse,
    EntityResult,
    FactResult,
    SearchRequest,
    SearchResponse,
)
from graphiti_core.graphiti import Graphiti
from ontology.registry import REGISTRY

from ecosystem_kb.adapters.graphiti.reconciliation import (
    find_contradictions_for,
    identity_lookup,
)


async def search(
    graphiti: Graphiti,
    request: SearchRequest,
    namespace: str,
    provenance_resolver=None,
) -> SearchResponse:
    """Hybrid search scoped to the server-derived namespace."""
    edges = await graphiti.search(
        request.query,
        group_ids=[namespace],
        num_results=request.num_results,
    )

    # LAW-006 at the retrieval boundary (review P1): ONLY canonical
    # relationship names are served. RELATES_TO is graphiti's fallback for
    # unmapped predicates — it is never a canonical fact. Quarantined
    # relations surface through the review queue, not search.
    vocabulary = set(REGISTRY.relationship_names)
    quarantined = [e for e in edges if e.name.upper() not in vocabulary]
    edges = [e for e in edges if e.name.upper() in vocabulary]

    facts: list[FactResult] = []
    entity_map: dict[str, EntityResult] = {}
    for edge in edges:
        source_name = await identity_lookup(graphiti, edge.source_node_uuid)
        target_name = await identity_lookup(graphiti, edge.target_node_uuid)
        provenance = None
        source_reference = None
        if provenance_resolver is not None:
            provenance, source_reference = await provenance_resolver(edge)

        fact = FactResult(
            fact=edge.fact,
            relation=edge.name.upper(),
            source_name=source_name,
            target_name=target_name,
            valid_at=edge.valid_at,
            invalid_at=edge.invalid_at,
            expired_at=edge.expired_at,
            provenance=provenance,
            source_reference=source_reference,
        )
        facts.append(fact)
        for uuid, name in (
            (edge.source_node_uuid, source_name),
            (edge.target_node_uuid, target_name),
        ):
            if uuid and uuid not in entity_map and name:
                entity_map[uuid] = EntityResult(name=name)

    contradictions = await find_contradictions_for(graphiti, namespace, edges)

    return SearchResponse(
        query=request.query,
        namespace=namespace,
        facts=facts,
        entities=list(entity_map.values()),
        contradictions=contradictions,
        retrieval_metadata={
            "result_count": len(facts),
            "quarantined_relations": len(quarantined),
        },
    )


async def entity_context(
    graphiti: Graphiti,
    canonical_key: str,
    namespace: str,
    provenance_resolver=None,
) -> EntityContextResponse | None:
    """Entity + its connected facts. Returns None when the entity is unknown."""
    from ecosystem_kb.adapters.graphiti.reconciliation import edges_touching_entity

    record = await edges_touching_entity(graphiti, namespace, canonical_key)
    if record is None:
        return None
    entity, related = record

    vocabulary = set(REGISTRY.relationship_names)
    related_facts = []
    for edge in related:
        if edge.name.upper() not in vocabulary:
            continue  # non-canonical edges never served (LAW-006)
        provenance = None
        source_reference = None
        if provenance_resolver is not None:
            provenance, source_reference = await provenance_resolver(edge)
        related_facts.append(
            FactResult(
                fact=edge.fact,
                relation=edge.name.upper(),
                source_name=entity.name if edge.source_node_uuid == entity.uuid else None,
                target_name=entity.name if edge.target_node_uuid == entity.uuid else None,
                valid_at=edge.valid_at,
                invalid_at=edge.invalid_at,
                expired_at=edge.expired_at,
                provenance=provenance,
                source_reference=source_reference,
            )
        )
    return EntityContextResponse(
        entity=EntityResult(
            canonical_key=canonical_key,
            name=entity.name,
            entity_type=_type_from_labels(getattr(entity, "labels", None)),
            summary=getattr(entity, "summary", "") or "",
        ),
        related_facts=related_facts,
        related_entities=[],
        provenance=[],
    )


def _type_from_labels(labels) -> str | None:
    for label in labels or []:
        if label != "Entity":
            return label
    return None
