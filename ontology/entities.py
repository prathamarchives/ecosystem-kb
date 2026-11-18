"""Canonical entity types (spec Part III, §4-5).

14 controlled types. Subtyping is done with controlled attributes (e.g.
System.system_kind), not by multiplying classes. Each model doubles as:
  1. our typed domain model (control plane + services), and
  2. the Graphiti custom entity_type when supplied to add_episode via the
     registry in ontology/registry.py.

Graphiti constraint kept in mind: custom entity types flow into extraction
prompts; fields must stay few and prompt-describable. Rich operational
metadata (canonical keys, ownership, provenance) lives in the control plane —
the graph carries the semantic core plus the canonical_key for joinback.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from ontology.enums import (
    AccessClass,
    ArtifactKind,
    ClaimKind,
    ComponentKind,
    DecisionStatus,
    EpistemicStatus,
    ExecutionStatus,
    FindingKind,
    InterfaceKind,
    LifecycleStatus,
    ProjectKind,
    ProjectStatus,
    RequirementKind,
    RequirementStatus,
    ReviewStatus,
    SourceKind,
    SystemKind,
    TrustClass,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


class KbEntity(BaseModel):
    """Base: every entity carries its canonical key and a human summary."""

    model_config = ConfigDict(extra="forbid")

    canonical_key: str = Field(description="Stable identity, format 'type:slug' (LAW-005)")
    name: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", description="Regional summary in plain language")


class Project(KbEntity):
    status: ProjectStatus = ProjectStatus.PROPOSED
    project_kind: ProjectKind = ProjectKind.PRODUCT
    description: str = ""
    owner_system: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class System(KbEntity):
    system_kind: SystemKind = SystemKind.APPLICATION
    description: str = ""
    lifecycle_status: LifecycleStatus = LifecycleStatus.PLANNED
    current_version: str | None = None
    repository_uri: str | None = None
    owner_project: str | None = Field(default=None, description="canonical_key of owning Project")


class Component(KbEntity):
    component_kind: ComponentKind = ComponentKind.MODULE
    description: str = ""
    lifecycle_status: LifecycleStatus = LifecycleStatus.PLANNED
    version: str | None = None
    owner_system: str = Field(description="canonical_key of owning System")


class Interface(KbEntity):
    interface_kind: InterfaceKind = InterfaceKind.REST
    protocol: str = ""
    version: str = "1"
    description: str = ""
    contract_uri: str | None = Field(default=None, description="Location of the interface contract")
    owner_system: str = Field(description="canonical_key of owning System")


class Artifact(KbEntity):
    artifact_kind: ArtifactKind = ArtifactKind.DOCUMENT
    uri: str | None = None
    content_hash: str | None = None
    revision: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    owner_system: str | None = None


class Source(KbEntity):
    source_kind: SourceKind = SourceKind.DOCUMENT
    uri: str = Field(description="Canonical location of the original")
    publisher: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    retrieved_at: datetime | None = None
    revision: str | None = None
    content_hash: str | None = None
    trust_class: TrustClass = TrustClass.SECONDARY_PUBLICATION
    access_class: AccessClass = AccessClass.INTERNAL


class Dataset(KbEntity):
    provider: str | None = None
    geography: str | None = None
    frequency: str | None = None
    units: str | None = None
    currency: str | None = None
    coverage_start: datetime | None = None
    coverage_end: datetime | None = None
    revision_policy: str | None = None
    source_uri: str | None = None
    # High-volume observations stay OUT of the graph (LAW-007)


class Concept(KbEntity):
    description: str = ""
    domain: str | None = None
    discipline: str | None = None
    definition_source: str | None = Field(default=None, description="canonical_key of a Source")


class Requirement(KbEntity):
    requirement_id: str = Field(description="Stable external requirement ID, e.g. SYS-F-001")
    requirement_kind: RequirementKind = RequirementKind.FUNCTIONAL
    statement: str = Field(min_length=1)
    rationale: str = ""
    priority: str = "should"
    verification_method: str = "test"
    status: RequirementStatus = RequirementStatus.PROPOSED


class Decision(KbEntity):
    decision_id: str = Field(description="Stable external decision ID, e.g. ADR-001")
    statement: str = Field(min_length=1)
    rationale: str = ""
    selected_option: str | None = None
    alternatives: list[str] = Field(default_factory=list)
    decision_status: DecisionStatus = DecisionStatus.PROPOSED
    decided_at: datetime | None = None
    review_condition: str | None = None
    owner: str | None = None


class Claim(KbEntity):
    claim_id: str = Field(description="Stable external claim ID")
    statement: str = Field(min_length=1)
    claim_kind: ClaimKind = ClaimKind.REPORTED_CLAIM
    epistemic_status: EpistemicStatus = EpistemicStatus.PROVISIONAL
    review_status: ReviewStatus = ReviewStatus.UNREVIEWED
    scope: str | None = Field(default=None, description="Conditions under which the claim applies")
    observed_at: datetime | None = None
    author_or_origin: str | None = None


class Experiment(KbEntity):
    experiment_id: str = Field(description="Stable external experiment ID")
    question: str = Field(min_length=1)
    method: str = ""
    experiment_type: str = "computational"
    parameters_reference: str | None = Field(default=None, description="URI of the parameter set")
    dataset_references: list[str] = Field(default_factory=list)
    code_revision: str | None = None
    seed: int | None = None
    execution_status: ExecutionStatus = ExecutionStatus.NOT_STARTED
    reproducibility_reference: str | None = None


class Finding(KbEntity):
    finding_id: str = Field(description="Stable external finding ID")
    statement: str = Field(min_length=1)
    finding_kind: FindingKind = FindingKind.COMPUTATIONAL
    scope: str | None = None
    method: str | None = None
    limitations: str | None = None
    review_status: ReviewStatus = ReviewStatus.UNREVIEWED


class VariableDefinition(KbEntity):
    """A typed definition of a named property. Definitions live here; VALUES
    live in owning systems (spec §6.5, LAW-007, LAW-010)."""

    name: str = Field(min_length=1, description="Variable name, e.g. api_timeout")
    symbol: str | None = None
    kind: str = Field(description="VariableKind value")
    data_type: str = Field(description="VariableDataType value")
    unit: str | None = Field(default=None, description="Unit of measurement; dimensionless if None")
    scope: str = Field(default="system", description="VariableScope value")
    description: str = ""
    owner_reference: str | None = Field(default=None, description="canonical_key of owning entity")
    mutability: str = "configuration_controlled"
    constraints: dict[str, str | int | float | None] = Field(default_factory=dict)
    default_value: str | int | float | bool | None = None
    sensitivity: str = "internal"
    canonical_source: str | None = Field(default=None, description="Where this definition is authored")


ENTITY_TYPES: dict[str, type[KbEntity]] = {
    "Project": Project,
    "System": System,
    "Component": Component,
    "Interface": Interface,
    "Artifact": Artifact,
    "Source": Source,
    "Dataset": Dataset,
    "Concept": Concept,
    "Requirement": Requirement,
    "Decision": Decision,
    "Claim": Claim,
    "Experiment": Experiment,
    "Finding": Finding,
    "VariableDefinition": VariableDefinition,
}

ENTITY_TYPE_BY_KEY_PREFIX: dict[str, str] = {
    "project": "Project",
    "system": "System",
    "component": "Component",
    "interface": "Interface",
    "artifact": "Artifact",
    "source": "Source",
    "dataset": "Dataset",
    "concept": "Concept",
    "requirement": "Requirement",
    "decision": "Decision",
    "claim": "Claim",
    "experiment": "Experiment",
    "finding": "Finding",
    "variable": "VariableDefinition",
}
