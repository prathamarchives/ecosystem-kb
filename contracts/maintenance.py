"""Maintenance contracts (spec §16): proposals, permission matrix, reports."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class MaintenanceOperation(str, Enum):
    SCHEMA_VALIDATION = "schema_validation"
    DUPLICATE_EVENT_REJECTION = "duplicate_event_rejection"
    ALIAS_CANDIDATE_DISCOVERY = "alias_candidate_discovery"
    SEARCH_INDEX_VERIFICATION = "search_index_verification"
    DUPLICATE_SUMMARY_DETECTION = "duplicate_summary_detection"
    DUPLICATE_ENTITY_AUDIT = "duplicate_entity_audit"
    CONTRADICTION_AUDIT = "contradiction_audit"
    ORPHAN_DETECTION = "orphan_detection"
    STALE_KNOWLEDGE_DETECTION = "stale_knowledge_detection"
    MERGE_ENTITY_ALIASES = "merge_entity_aliases"
    ENTITY_MERGE = "entity_merge"
    HISTORICAL_DELETION = "historical_deletion"
    BULK_GRAPH_REWRITE = "bulk_graph_rewrite"
    ONTOLOGY_MIGRATION = "ontology_migration"
    KNOWLEDGE_COMPACTION = "knowledge_compaction"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Reversibility(str, Enum):
    REVERSIBLE = "reversible"
    REVERSIBLE_WITH_EFFORT = "reversible_with_effort"
    IRREVERSIBLE = "irreversible"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ExecutionStatus_(str, Enum):
    NOT_STARTED = "not_started"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    VERIFIED = "verified"


# The permission matrix (spec §16): which operations may run automatically,
# and which require an approval record first (LAW-012).
AUTOMATIC_OPERATIONS: set[MaintenanceOperation] = {
    MaintenanceOperation.SCHEMA_VALIDATION,
    MaintenanceOperation.DUPLICATE_EVENT_REJECTION,
    MaintenanceOperation.ALIAS_CANDIDATE_DISCOVERY,
    MaintenanceOperation.SEARCH_INDEX_VERIFICATION,
    MaintenanceOperation.DUPLICATE_SUMMARY_DETECTION,
    MaintenanceOperation.DUPLICATE_ENTITY_AUDIT,
    MaintenanceOperation.CONTRADICTION_AUDIT,
    MaintenanceOperation.ORPHAN_DETECTION,
    MaintenanceOperation.STALE_KNOWLEDGE_DETECTION,
}

APPROVAL_REQUIRED_OPERATIONS: set[MaintenanceOperation] = {
    MaintenanceOperation.MERGE_ENTITY_ALIASES,
    MaintenanceOperation.ENTITY_MERGE,
    MaintenanceOperation.HISTORICAL_DELETION,
    MaintenanceOperation.BULK_GRAPH_REWRITE,
    MaintenanceOperation.ONTOLOGY_MIGRATION,
    MaintenanceOperation.KNOWLEDGE_COMPACTION,
}


class MaintenanceAction(BaseModel):
    """A proposed (or approved) maintenance change with evidence + rationale."""

    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(default_factory=lambda: str(uuid4()))
    operation: MaintenanceOperation
    target: str = Field(description="canonical_key or record the action touches")
    candidates: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list, description="What justifies the action")
    reason: str = ""
    risk_level: RiskLevel = RiskLevel.MEDIUM
    reversibility: Reversibility = Reversibility.REVERSIBLE
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    execution_status: ExecutionStatus_ = ExecutionStatus_.NOT_STARTED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    executed_at: datetime | None = None

    def requires_approval(self) -> bool:
        return self.operation in APPROVAL_REQUIRED_OPERATIONS


class MaintenanceReport(BaseModel):
    """Result of one maintainer audit run (kb_get_maintenance_report)."""

    model_config = ConfigDict(extra="forbid")

    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    namespace: str
    duplicate_entities: list[dict] = Field(default_factory=list)
    contradictions: list[dict] = Field(default_factory=list)
    orphans: list[dict] = Field(default_factory=list)
    stale_edges: list[dict] = Field(default_factory=list)
    proposals: list[MaintenanceAction] = Field(default_factory=list)
    summary: dict = Field(default_factory=dict)


class ProposalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str
    decision: ApprovalStatus = Field(description="approved | rejected")
    reviewer: str
    reason: str = ""
