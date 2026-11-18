"""Graphiti-facing entity projections (spec §19.2 constraint).

graphiti-core rejects custom entity types whose fields collide with
EntityNode's protected names (name, summary, created_at, labels, uuid,
group_id, attributes, name_embedding). Our rich domain models live in
ontology/entities.py; these projections carry only the semantic core the
graph needs, with collision-free field names. The canonical_key is the
joinback to the control plane (LAW-005).

entity_type_id mapping: tests and extraction payloads refer to types by the
INDEX in the ordered dict returned by graphiti_entity_types() below.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _GraphEntity(BaseModel):
    """Shared projection shape; extra fields forbidden for extraction safety."""

    model_config = ConfigDict(extra="forbid")

    canonical_key: str = Field(default="unknown:unknown", description="Stable identity 'type:slug' in the KB control plane; explicit unknown allowed (LAW-002)")
    description: str = Field(default="", description="One- or two-sentence semantic description")


class ProjectEntity(_GraphEntity):
    project_kind: str = Field(default="product", description="product | research | infrastructure | exploration | education")
    status: str = Field(default="proposed", description="proposed | active | paused | completed | archived")


class SystemEntity(_GraphEntity):
    system_kind: str = Field(default="application", description="application | agent | service | database | infrastructure | workflow | cli | research_environment")
    lifecycle: str = Field(default="planned", description="planned | active | maintenance | deprecated | retired")
    repository_uri: str | None = None


class ComponentEntity(_GraphEntity):
    component_kind: str = Field(default="module", description="module | subsystem | library | adapter | worker | interface_boundary")
    owner_system: str = Field(default="unknown", description="canonical_key of the owning system")


class InterfaceEntity(_GraphEntity):
    interface_kind: str = Field(default="rest", description="rest | mcp | cli | event | database | file | webhook | internal_api")
    protocol: str = ""
    contract_uri: str | None = None


class ArtifactEntity(_GraphEntity):
    artifact_kind: str = Field(default="document", description="repository | document | specification | source_code | architecture_diagram | configuration | report | schema")
    uri: str | None = None
    revision: str | None = None


class SourceEntity(_GraphEntity):
    source_kind: str = Field(default="document", description="note | repository | document | paper | api_documentation | filing | dataset | decision_record | conversation | web_page")
    trust_class: str = Field(default="secondary_publication", description="Origin classification, not truth")
    publisher: str | None = None


class DatasetEntity(_GraphEntity):
    provider: str | None = None
    geography: str | None = None
    frequency: str | None = None
    units: str | None = None


class ConceptEntity(_GraphEntity):
    domain: str | None = Field(default=None, description="Knowledge domain, e.g. distributed-systems")
    discipline: str | None = None


class RequirementEntity(_GraphEntity):
    requirement_id: str = Field(default="REQ-UNKNOWN", description="Stable requirement ID, e.g. SYS-F-001")
    requirement_kind: str = Field(default="functional", description="stakeholder | functional | non_functional | security | operational | data_governance | interface | reliability")
    priority: str = Field(default="should", description="must | should | could | wont")


class DecisionEntity(_GraphEntity):
    decision_id: str = Field(default="DECISION-UNKNOWN", description="Stable decision ID, e.g. ADR-001")
    decision_status: str = Field(default="proposed", description="proposed | accepted | superseded | rejected | under_review")
    rationale: str = Field(default="", description="Why this course of action was selected")
    selected_option: str | None = None


class ClaimEntity(_GraphEntity):
    claim_id: str = Field(default="CLAIM-UNKNOWN", description="Stable claim ID")
    claim_kind: str = Field(default="reported_claim", description="observation | reported_claim | inference | hypothesis | interpretation | finding")
    epistemic_status: str = Field(default="provisional", description="established_within_scope | supported | provisional | disputed | retracted")


class ExperimentEntity(_GraphEntity):
    experiment_id: str = Field(default="EXPERIMENT-UNKNOWN", description="Stable experiment ID")
    experiment_type: str = Field(default="computational", description="empirical | computational | theoretical | statistical")
    execution_status: str = Field(default="not_started", description="not_started | running | completed | failed | cancelled")


class FindingEntity(_GraphEntity):
    finding_id: str = Field(default="FINDING-UNKNOWN", description="Stable finding ID")
    finding_kind: str = Field(default="computational", description="empirical | theoretical | computational | statistical | qualitative | operational")
    limitations: str | None = None


class VariableDefinitionEntity(_GraphEntity):
    variable_name: str = Field(default="unknown_variable", description="Variable name, e.g. api_timeout")
    kind: str = Field(default="configuration", description="configuration | design_parameter | model_parameter | input | state | output_metric | dataset_field")
    data_type: str = Field(default="string", description="string | integer | number | decimal | boolean | enum | date | timestamp | duration | categorical | object_reference | series_reference")
    unit: str | None = Field(default=None, description="Unit of measurement; values live in owning systems")


# Ordered — entity_type_id in extraction payloads is the index here.
GRAPH_ENTITY_TYPES: dict[str, type[_GraphEntity]] = {
    "Project": ProjectEntity,
    "System": SystemEntity,
    "Component": ComponentEntity,
    "Interface": InterfaceEntity,
    "Artifact": ArtifactEntity,
    "Source": SourceEntity,
    "Dataset": DatasetEntity,
    "Concept": ConceptEntity,
    "Requirement": RequirementEntity,
    "Decision": DecisionEntity,
    "Claim": ClaimEntity,
    "Experiment": ExperimentEntity,
    "Finding": FindingEntity,
    "VariableDefinition": VariableDefinitionEntity,
}


def graphiti_entity_types() -> dict[str, type[_GraphEntity]]:
    return dict(GRAPH_ENTITY_TYPES)


ENTITY_TYPE_BY_INDEX: dict[int, str] = {
    i: name for i, name in enumerate(GRAPH_ENTITY_TYPES)
}
