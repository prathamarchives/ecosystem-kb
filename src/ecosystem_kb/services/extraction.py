"""Typed capture-extraction contract (runbook Step 10).

A capturing client (connected-client-LLM mode) may send structured
entities/edges alongside plain content. The payload is validated against
the CONTROLLED ONTOLOGY (registry entity types + relationship domain/range)
at submit time AND again in the worker before the graph write. Nothing is
trusted because the API validated it before.

Entity references are by NAME (the LLM's surface form); the resolution to
graph nodes happens in the ingestion adapter, same as extracted nodes.
`entity_type` uses the canonical names (Project, System, Component, ...).
"""

from __future__ import annotations

from typing import Any

from ontology.registry import REGISTRY, OntologyValidationError
from pydantic import BaseModel, ConfigDict, Field


class ExtractionEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    entity_type: str = Field(min_length=1, max_length=50)
    summary: str = Field(default="", max_length=2000)
    episode_indices: list[int] = Field(default_factory=lambda: [0])


class ExtractionEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_entity_name: str = Field(min_length=1, max_length=200)
    target_entity_name: str = Field(min_length=1, max_length=200)
    relation_type: str = Field(min_length=1, max_length=60)
    fact: str = Field(default="", max_length=2000)


class TypedExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entities: list[ExtractionEntity] = Field(min_length=1, max_length=50)
    edges: list[ExtractionEdge] = Field(default_factory=list, max_length=100)

    def validate_ontology(self) -> list[str]:
        """Validate against the controlled vocabulary. Returns the list of
        violations (empty = valid). Raises nothing: callers decide whether
        to reject or quarantine."""
        errors: list[str] = []
        names: set[str] = set()
        types_by_name: dict[str, str] = {}
        for entity in self.entities:
            names.add(entity.name)
            if entity.name in types_by_name:
                errors.append(f"duplicate entity name '{entity.name}'")
                continue
            types_by_name[entity.name] = entity.entity_type
            try:
                REGISTRY.validate_entity_type(entity.entity_type)
            except OntologyValidationError as exc:
                errors.append(str(exc))
        for edge in self.edges:
            if edge.source_entity_name not in names:
                errors.append(f"edge references unknown source entity '{edge.source_entity_name}'")
            if edge.target_entity_name not in names:
                errors.append(f"edge references unknown target entity '{edge.target_entity_name}'")
            if edge.source_entity_name in types_by_name and edge.target_entity_name in types_by_name:
                try:
                    REGISTRY.validate_relationship(
                        edge.relation_type.upper(),
                        types_by_name[edge.source_entity_name],
                        types_by_name[edge.target_entity_name],
                    )
                except OntologyValidationError as exc:
                    errors.append(str(exc))
        return errors


def parse_extraction(payload: Any) -> tuple[TypedExtraction | None, str | None]:
    """Parse + structurally validate an extraction payload. Returns
    (extraction, error) — exactly one is None."""
    if payload is None:
        return None, None
    try:
        extraction = TypedExtraction(**payload)
    except Exception as exc:  # noqa: BLE001 — surface as a validation string
        return None, f"extraction payload invalid: {exc}"
    return extraction, None


def extraction_episode_body(extraction: TypedExtraction, title: str) -> str:
    """Render the typed extraction as episode text the ingestion pathway
    understands: one line per entity, one per edge. This keeps the SAME
    Graphiti pipeline (LAW-006) — extraction rides as structured content."""
    lines = [title, ""]
    for entity in extraction.entities:
        line = f"{entity.name} is a {entity.entity_type}."
        if entity.summary:
            line += f" {entity.summary}"
        lines.append(line)
    for edge in extraction.edges:
        fact = edge.fact or f"{edge.source_entity_name} {edge.relation_type} {edge.target_entity_name}"
        lines.append(f"{edge.source_entity_name} --{edge.relation_type}--> {edge.target_entity_name}: {fact}")
    return "\n".join(lines)
