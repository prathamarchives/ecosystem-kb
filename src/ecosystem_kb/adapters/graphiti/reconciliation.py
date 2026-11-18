"""Reconciliation: ambiguous-outcome recovery, contradiction discovery,
identity lookups (spec §25, §37).

A timeout after a graph write is AMBIGUOUS: the episode may already exist.
We reconcile by event identity (episode name carries the idempotency key),
never blind-retry (ADR-008).
"""

from __future__ import annotations

from datetime import UTC

from graphiti_core.graphiti import Graphiti

_EPISODE_NAME_PREFIX = "knowledge."


async def episode_exists_for(
    graphiti: Graphiti, namespace: str, idempotency_key: str
) -> bool:
    """Did an episode from this exact event already land? (reconciliation check)"""
    from datetime import datetime

    episodes = await graphiti.retrieve_episodes(
        reference_time=datetime.now(UTC),
        group_ids=[namespace],
        last_n=500,
    )
    target = f"{_EPISODE_NAME_PREFIX}"
    # Episode names are '<event_type>:<idempotency_key>'
    for ep in episodes or []:
        name = getattr(ep, "name", "") or ""
        if name.endswith(f":{idempotency_key}") and name.startswith(target):
            return True
    return False


async def identity_lookup(graphiti: Graphiti, node_uuid: str | None) -> str | None:
    """Best-effort name resolution for a node UUID."""
    if not node_uuid:
        return None
    try:
        nodes = await graphiti.get_nodes_by_uuids([node_uuid]) if hasattr(
            graphiti, "get_nodes_by_uuids"
        ) else []
        for node in nodes or []:
            return node.name
    except Exception:
        pass
    try:
        node = await graphiti.get_node(node_uuid) if hasattr(graphiti, "get_node") else None
        return getattr(node, "name", None) if node else None
    except Exception:
        return None


async def edges_touching_entity(graphiti: Graphiti, namespace: str, canonical_key: str):
    """Find an entity node by name-derived key and its edges.

    v1 resolution: match on node name (slug of canonical key). The identity
    registry is authoritative for ambiguous cases (LAW-005).
    """
    from graphiti_core.search.search_config_recipes import EDGE_HYBRID_SEARCH_RRF

    name = canonical_key.split(":", 1)[1].replace("-", " ")
    config = EDGE_HYBRID_SEARCH_RRF.model_copy(deep=True)
    config.limit = 50
    try:
        results = await graphiti.search_(name, config, group_ids=[namespace])
    except Exception:
        return None
    edges = getattr(results, "edges", []) or []
    matched_uuid = None
    for edge in edges:
        for uuid in (edge.source_node_uuid, edge.target_node_uuid):
            resolved = await identity_lookup(graphiti, uuid)
            if resolved and _slug(resolved) == canonical_key.split(":", 1)[1]:
                matched_uuid = uuid
                break
        if matched_uuid:
            break
    if matched_uuid is None:
        return None

    class _Entity:
        pass

    entity = _Entity()
    entity.uuid = matched_uuid
    entity.name = name
    related = [e for e in edges if matched_uuid in (e.source_node_uuid, e.target_node_uuid)]
    return entity, related


async def find_contradictions_for(graphiti: Graphiti, namespace: str, edges) -> list[str]:
    """Surface CONTRADICTED_BY / DISPUTED knowledge touching these edges.

    v1: flag facts whose relation set includes contradiction vocabulary or
    whose facts share a subject-predicate with conflicting objects (name-level
    heuristic; deep contradiction detection is a Maintainer audit).
    """
    contradictions: list[str] = []
    seen_predicates: dict[tuple[str, str], str] = {}
    for edge in edges:
        source = await identity_lookup(graphiti, edge.source_node_uuid)
        relation = edge.name.upper()
        if relation in {"CONTRADICTED_BY", "SUPERSEDES"}:
            contradictions.append(edge.fact)
            continue
        key = (source or "?", relation)
        fact_key = (key[0], key[1], edge.fact)
        if key in seen_predicates and seen_predicates[key] != edge.fact:
            contradictions.append(f"conflicting facts about {key[0]} ({key[1]})")
        seen_predicates.setdefault(key, edge.fact)
    return contradictions


def _slug(text: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
