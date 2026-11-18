"""Ontology registry: the frozen validation surface (LAW-006, LAW-017).

Everything the ingestion pipeline needs to validate extracted knowledge:
  - entity types (name → class, key prefix → type)
  - edge domain/range rules
  - the Graphiti edge_type_map for typed edge extraction
  - ontology version

An extraction result is a proposal until `validate_*` here passes it.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ontology.entities import (
    ENTITY_TYPE_BY_KEY_PREFIX,
    ENTITY_TYPES,
    KbEntity,
)
from ontology.graph_edges import graphiti_edge_types
from ontology.graph_entities import graphiti_entity_types
from ontology.relationships import (
    RELATIONSHIP_DOMAIN_RANGE,
    validate_edge,
)
from ontology.versions import ONTOLOGY_VERSION, OntologyVersion


class OntologyValidationError(ValueError):
    """Raised when proposed knowledge violates the controlled vocabulary."""


class QuarantinedEdge(BaseModel):
    """An extracted edge that failed ontology validation — recorded, not lost."""

    relation: str
    source_type: str
    target_type: str
    source_name: str = ""
    target_name: str = ""
    fact: str = ""
    reason: str = Field(description="Why the edge was quarantined")


class OntologyRegistry:
    """Immutable facade over the controlled vocabulary for one ontology version."""

    def __init__(self, version: OntologyVersion | None = None):
        self.version = version or ONTOLOGY_VERSION
        self._entity_types = dict(ENTITY_TYPES)
        self._edge_rules = {
            rel: list(pairs) for rel, pairs in RELATIONSHIP_DOMAIN_RANGE.items()
        }

    # -- entity surface ----------------------------------------------------

    @property
    def entity_type_names(self) -> list[str]:
        return sorted(self._entity_types)

    def entity_class(self, type_name: str) -> type[KbEntity] | None:
        return self._entity_types.get(type_name)

    def type_for_canonical_key(self, canonical_key: str) -> str | None:
        prefix = canonical_key.split(":", 1)[0] if ":" in canonical_key else ""
        return ENTITY_TYPE_BY_KEY_PREFIX.get(prefix)

    def validate_entity_type(self, type_name: str) -> str:
        if type_name not in self._entity_types:
            raise OntologyValidationError(
                f"unknown entity type '{type_name}'; known: {self.entity_type_names}"
            )
        return type_name

    # -- edge surface -------------------------------------------------------

    @property
    def relationship_names(self) -> list[str]:
        return sorted(self._edge_rules)

    def validate_relationship(
        self, relation: str, source_type: str, target_type: str
    ) -> tuple[str, str, str]:
        if relation not in self._edge_rules:
            raise OntologyValidationError(
                f"unknown relationship '{relation}'; known: {self.relationship_names}"
            )
        self.validate_entity_type(source_type)
        self.validate_entity_type(target_type)
        if not validate_edge(relation, source_type, target_type):
            raise OntologyValidationError(
                f"domain/range violation: {relation} does not connect "
                f"{source_type} -> {target_type}"
            )
        return relation, source_type, target_type

    def quarantine_edge(
        self,
        relation: str,
        source_type: str,
        target_type: str,
        source_name: str = "",
        target_name: str = "",
        fact: str = "",
    ) -> QuarantinedEdge:
        if relation not in self._edge_rules:
            reason = f"unknown relationship '{relation}'"
        else:
            reason = f"domain/range violation for {source_type} -[{relation}]-> {target_type}"
        return QuarantinedEdge(
            relation=relation,
            source_type=source_type,
            target_type=target_type,
            source_name=source_name,
            target_name=target_name,
            fact=fact,
            reason=reason,
        )

    # -- Graphiti bridge ----------------------------------------------------

    def graphiti_entity_types(self) -> dict[str, type]:
        """Graph-projection entity classes for Graphiti add_episode(entity_types=...).

        These are collision-free projections (ontology/graph_entities.py), not
        the rich domain models — graphiti rejects fields colliding with
        EntityNode's protected names (name, summary, created_at, ...)."""
        return graphiti_entity_types()

    def graphiti_edge_types(self) -> dict[str, type]:
        """Pydantic models per canonical relationship, keyed by SCREAMING_SNAKE
        relation name. graphiti only enforces edge_type_map signatures for
        types present here — without them every edge collapses to RELATES_TO."""
        return graphiti_edge_types()

    def graphiti_edge_type_map(self) -> dict[tuple[str, str], list[str]]:
        """edge_type_map keyed by (source_type, target_type): the controlled
        vocabulary for typed edge extraction. Unmapped pairs that arrive as
        plain predicates must be quarantined by the adapter (spec §7.2)."""
        mapping: dict[tuple[str, str], list[str]] = {}
        for relation, pairs in self._edge_rules.items():
            for source_type, target_type in pairs:
                mapping.setdefault((source_type, target_type), []).append(relation)
        return mapping

    def describe(self) -> dict:
        return {
            "version": self.version.version,
            "entity_types": len(self._entity_types),
            "relationships": len(self._edge_rules),
            "edge_rules": sum(len(p) for p in self._edge_rules.values()),
        }


# Module-level singleton — the ontology is loaded once per process.
REGISTRY = OntologyRegistry()
