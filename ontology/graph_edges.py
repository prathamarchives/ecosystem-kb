"""Graphiti-facing edge projections: one Pydantic model per canonical
relationship (spec §7, LAW-006).

graphiti matches extracted relation_type names against edge_types keys and
enforces edge_type_map signatures between endpoint labels. With these models
registered, DEPENDS_ON extracted between System/System keeps its name;
an unmapped relation still becomes RELATES_TO, which our adapter's ontology
gate then quarantines (LAW-006 fallback).

Fields stay minimal and defaulted — graphiti stores edge facts; our typed
semantics (domain/range, evidence) live in the ontology registry and the
control plane.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _GraphEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_note: str = Field(
        default="", description="Optional note on the evidence behind this relationship"
    )


class PartOf(_GraphEdge):
    """Component or system belongs to a larger system or project."""


class Contains(_GraphEdge):
    """Project or system includes a system, component or artifact."""


class DependsOn(_GraphEdge):
    """One system or component requires another to function."""


class Implements(_GraphEdge):
    """A system, component or artifact satisfies a requirement."""


class Exposes(_GraphEdge):
    """A system or component provides an interface."""


class Defines(_GraphEdge):
    """A system or component defines a variable or requirement."""


class Uses(_GraphEdge):
    """A system, component or experiment uses a concept, dataset or variable."""


class OwnedBy(_GraphEdge):
    """A component or interface is owned by a system."""


class SupportedBy(_GraphEdge):
    """A claim is supported by a source, finding or experiment."""


class ContradictedBy(_GraphEdge):
    """A claim is contradicted by a claim, source or finding."""


class DerivedFrom(_GraphEdge):
    """A claim or finding is derived from a source, dataset or experiment."""


class Tests(_GraphEdge):
    """An experiment tests a claim or hypothesis."""


class Produces(_GraphEdge):
    """An experiment produces a finding."""


class Supersedes(_GraphEdge):
    """A decision, claim or requirement replaces an earlier one (temporal)."""


class Refines(_GraphEdge):
    """A requirement or concept refines another."""


class References(_GraphEdge):
    """An artifact or claim references a source or artifact."""


# graphiti expects edge type keys matching the extracted SCREAMING_SNAKE_CASE
# relation_type values.
GRAPH_EDGE_TYPES: dict[str, type[_GraphEdge]] = {
    "PART_OF": PartOf,
    "CONTAINS": Contains,
    "DEPENDS_ON": DependsOn,
    "IMPLEMENTS": Implements,
    "EXPOSES": Exposes,
    "DEFINES": Defines,
    "USES": Uses,
    "OWNED_BY": OwnedBy,
    "SUPPORTED_BY": SupportedBy,
    "CONTRADICTED_BY": ContradictedBy,
    "DERIVED_FROM": DerivedFrom,
    "TESTS": Tests,
    "PRODUCES": Produces,
    "SUPERSEDES": Supersedes,
    "REFINES": Refines,
    "REFERENCES": References,
}


def graphiti_edge_types() -> dict[str, type[_GraphEdge]]:
    return dict(GRAPH_EDGE_TYPES)
