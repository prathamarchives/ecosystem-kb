"""Canonical relationship vocabulary with domain/range constraints (spec Part V, §7).

16 relationship types. An extracted relationship is a PROPOSAL until it passes
ontology validation (LAW-006): the (source_type, relation, target_type) triple
must appear in EDGE_DOMAIN_RANGE, otherwise it is quarantined, not admitted.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ontology.enums import EvidenceStatus

# (source entity type, target entity type) pairs per relationship.
# Types refer to the 14 canonical entity type names in ontology/entities.py.

STRUCTURAL_RELATIONSHIPS: dict[str, list[tuple[str, str]]] = {
    "PART_OF": [
        ("Component", "System"),
        ("Component", "Project"),
        ("System", "Project"),
    ],
    "CONTAINS": [
        ("Project", "System"),
        ("Project", "Component"),
        ("Project", "Artifact"),
        ("System", "Component"),
        ("System", "Artifact"),
    ],
    "DEPENDS_ON": [
        ("System", "System"),
        ("System", "Component"),
        ("Component", "System"),
        ("Component", "Component"),
        ("Project", "Project"),
        ("Project", "System"),
    ],
    "IMPLEMENTS": [
        ("System", "Requirement"),
        ("Component", "Requirement"),
        ("Artifact", "Requirement"),
    ],
    "EXPOSES": [
        ("System", "Interface"),
        ("Component", "Interface"),
    ],
    "DEFINES": [
        ("System", "VariableDefinition"),
        ("Component", "VariableDefinition"),
        ("System", "Requirement"),
        ("Component", "Requirement"),
    ],
    "USES": [
        ("System", "Concept"),
        ("System", "Dataset"),
        ("System", "VariableDefinition"),
        ("Component", "Concept"),
        ("Component", "Dataset"),
        ("Component", "VariableDefinition"),
        ("Experiment", "Concept"),
        ("Experiment", "Dataset"),
        ("Experiment", "VariableDefinition"),
    ],
    "OWNED_BY": [
        ("Component", "System"),
        ("Interface", "System"),
    ],
}

KNOWLEDGE_RELATIONSHIPS: dict[str, list[tuple[str, str]]] = {
    "SUPPORTED_BY": [
        ("Claim", "Source"),
        ("Claim", "Finding"),
        ("Claim", "Experiment"),
    ],
    "CONTRADICTED_BY": [
        ("Claim", "Claim"),
        ("Claim", "Source"),
        ("Claim", "Finding"),
    ],
    "DERIVED_FROM": [
        ("Claim", "Source"),
        ("Claim", "Dataset"),
        ("Claim", "Experiment"),
        ("Finding", "Source"),
        ("Finding", "Dataset"),
        ("Finding", "Experiment"),
    ],
    "TESTS": [
        ("Experiment", "Claim"),
    ],
    "PRODUCES": [
        ("Experiment", "Finding"),
    ],
    "SUPERSEDES": [
        ("Decision", "Decision"),
        ("Claim", "Claim"),
        ("Requirement", "Requirement"),
    ],
    "REFINES": [
        ("Requirement", "Requirement"),
        ("Concept", "Concept"),
    ],
    "REFERENCES": [
        ("Artifact", "Source"),
        ("Artifact", "Artifact"),
        ("Claim", "Source"),
        ("Claim", "Artifact"),
    ],
}

RELATIONSHIP_DOMAIN_RANGE: dict[str, list[tuple[str, str]]] = {
    **STRUCTURAL_RELATIONSHIPS,
    **KNOWLEDGE_RELATIONSHIPS,
}

# Relationships that carry temporal-supersession semantics (LAW-004)
TEMPORAL_RELATIONSHIPS = {"SUPERSEDES"}

# Relationships that express evidence and therefore carry an evidence status
EVIDENCE_RELATIONSHIPS = {"SUPPORTED_BY", "CONTRADICTED_BY", "DERIVED_FROM"}

# Free-form prose meaning of each relationship, used in extraction prompts
# and validation error messages.
RELATIONSHIP_MEANINGS: dict[str, str] = {
    "PART_OF": "Component or system belongs to a larger system or project",
    "CONTAINS": "Project or system includes a system, component or artifact",
    "DEPENDS_ON": "One system or component requires another to function",
    "IMPLEMENTS": "A system, component or artifact satisfies a requirement",
    "EXPOSES": "A system or component provides an interface",
    "DEFINES": "A system or component defines a variable or requirement",
    "USES": "A system, component or experiment uses a concept, dataset or variable",
    "OWNED_BY": "A component or interface is owned by a system",
    "SUPPORTED_BY": "A claim is supported by a source, finding or experiment",
    "CONTRADICTED_BY": "A claim is contradicted by a claim, source or finding",
    "DERIVED_FROM": "A claim or finding is derived from a source, dataset or experiment",
    "TESTS": "An experiment tests a claim or hypothesis",
    "PRODUCES": "An experiment produces a finding",
    "SUPERSEDES": "A decision, claim or requirement replaces an earlier one",
    "REFINES": "A requirement or concept refines another",
    "REFERENCES": "An artifact or claim references a source or artifact",
}


class EdgeProposal(BaseModel):
    """An extracted relationship BEFORE validation — never trusted as-is (LAW-017)."""

    model_config = ConfigDict(extra="forbid")

    relation: str = Field(min_length=1, description="Proposed relationship name")
    source_type: str = Field(description="Source entity type name")
    target_type: str = Field(description="Target entity type name")
    source_key: str | None = Field(default=None, description="canonical_key if resolved")
    target_key: str | None = Field(default=None)
    fact: str = Field(min_length=1, description="Natural-language statement of the relationship")
    evidence_status: EvidenceStatus = EvidenceStatus.INFERRED
    provenance: dict[str, str] = Field(default_factory=dict)

    def is_known_relation(self) -> bool:
        return self.relation in RELATIONSHIP_DOMAIN_RANGE

    def domain_range_ok(self) -> bool:
        pairs = RELATIONSHIP_DOMAIN_RANGE.get(self.relation, [])
        return (self.source_type, self.target_type) in pairs

    def validate(self) -> bool:
        """Known relation AND allowed (source, target) combination."""
        return self.is_known_relation() and self.domain_range_ok()


def validate_edge(relation: str, source_type: str, target_type: str) -> bool:
    """Deterministic ontology gate for an extracted relationship."""
    pairs = RELATIONSHIP_DOMAIN_RANGE.get(relation)
    if pairs is None:
        return False
    return (source_type, target_type) in pairs


def allowed_targets(relation: str, source_type: str) -> list[str]:
    return [t for s, t in RELATIONSHIP_DOMAIN_RANGE.get(relation, []) if s == source_type]


def allowed_relations(source_type: str, target_type: str) -> list[str]:
    return [
        rel
        for rel, pairs in RELATIONSHIP_DOMAIN_RANGE.items()
        if (source_type, target_type) in pairs
    ]
