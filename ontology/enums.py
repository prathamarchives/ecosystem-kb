"""Controlled vocabulary enums for the ecosystem ontology.

Every enum here is part of the versioned ontology (LAW-006): values are
added, never silently renamed or removed. See ontology/versions.py.
"""

from enum import Enum


class SystemKind(str, Enum):
    APPLICATION = "application"
    AGENT = "agent"
    SERVICE = "service"
    DATABASE = "database"
    INFRASTRUCTURE = "infrastructure"
    WORKFLOW = "workflow"
    CLI = "cli"
    RESEARCH_ENVIRONMENT = "research_environment"


class LifecycleStatus(str, Enum):
    PLANNED = "planned"
    ACTIVE = "active"
    MAINTENANCE = "maintenance"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


class ComponentKind(str, Enum):
    MODULE = "module"
    SUBSYSTEM = "subsystem"
    LIBRARY = "library"
    ADAPTER = "adapter"
    WORKER = "worker"
    INTERFACE_BOUNDARY = "interface_boundary"


class InterfaceKind(str, Enum):
    REST = "rest"
    MCP = "mcp"
    CLI = "cli"
    EVENT = "event"
    DATABASE = "database"
    FILE = "file"
    WEBHOOK = "webhook"
    INTERNAL_API = "internal_api"


class ArtifactKind(str, Enum):
    REPOSITORY = "repository"
    DOCUMENT = "document"
    SPECIFICATION = "specification"
    SOURCE_CODE = "source_code"
    ARCHITECTURE_DIAGRAM = "architecture_diagram"
    CONFIGURATION = "configuration"
    REPORT = "report"
    SCHEMA = "schema"


class TrustClass(str, Enum):
    """Origin classification — describes where content came from, not how true it is."""

    HUMAN_AUTHORED = "human_authored"
    PRIMARY_PUBLICATION = "primary_publication"
    OFFICIAL_DOCUMENTATION = "official_documentation"
    OFFICIAL_DATASET = "official_dataset"
    SECONDARY_PUBLICATION = "secondary_publication"
    MODEL_GENERATED = "model_generated"
    SYSTEM_GENERATED = "system_generated"


class AccessClass(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    PRIVATE = "private"
    CONFIDENTIAL = "confidential"


class RequirementKind(str, Enum):
    STAKEHOLDER = "stakeholder"
    FUNCTIONAL = "functional"
    NON_FUNCTIONAL = "non_functional"
    SECURITY = "security"
    OPERATIONAL = "operational"
    DATA_GOVERNANCE = "data_governance"
    INTERFACE = "interface"
    RELIABILITY = "reliability"


class RequirementStatus(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    IMPLEMENTED = "implemented"
    VERIFIED = "verified"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class DecisionStatus(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"
    UNDER_REVIEW = "under_review"


class ClaimKind(str, Enum):
    OBSERVATION = "observation"
    REPORTED_CLAIM = "reported_claim"
    INFERENCE = "inference"
    HYPOTHESIS = "hypothesis"
    INTERPRETATION = "interpretation"
    FINDING = "finding"


class EpistemicStatus(str, Enum):
    ESTABLISHED_WITHIN_SCOPE = "established_within_scope"
    SUPPORTED = "supported"
    PROVISIONAL = "provisional"
    DISPUTED = "disputed"
    RETRACTED = "retracted"


class ReviewStatus(str, Enum):
    UNREVIEWED = "unreviewed"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    REJECTED = "rejected"


class ExecutionStatus(str, Enum):
    NOT_STARTED = "not_started"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FindingKind(str, Enum):
    EMPIRICAL = "empirical"
    THEORETICAL = "theoretical"
    COMPUTATIONAL = "computational"
    STATISTICAL = "statistical"
    QUALITATIVE = "qualitative"
    OPERATIONAL = "operational"


class ProjectKind(str, Enum):
    PRODUCT = "product"
    RESEARCH = "research"
    INFRASTRUCTURE = "infrastructure"
    EXPLORATION = "exploration"
    EDUCATION = "education"


class ProjectStatus(str, Enum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


# ---------------------------------------------------------------------------
# Variable model (spec Part IV)
# ---------------------------------------------------------------------------


class VariableKind(str, Enum):
    CONFIGURATION = "configuration"
    DESIGN_PARAMETER = "design_parameter"
    MODEL_PARAMETER = "model_parameter"
    INPUT = "input"
    STATE = "state"
    OUTPUT_METRIC = "output_metric"
    DATASET_FIELD = "dataset_field"


class VariableDataType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    ENUM = "enum"
    DATE = "date"
    TIMESTAMP = "timestamp"
    DURATION = "duration"
    CATEGORICAL = "categorical"
    OBJECT_REFERENCE = "object_reference"
    SERIES_REFERENCE = "series_reference"


class VariableScope(str, Enum):
    ECOSYSTEM = "ecosystem"
    PROJECT = "project"
    SYSTEM = "system"
    COMPONENT = "component"
    ENVIRONMENT = "environment"
    MODEL = "model"
    EXPERIMENT = "experiment"
    DATASET = "dataset"


class VariableMutability(str, Enum):
    IMMUTABLE = "immutable"
    CONFIGURATION_CONTROLLED = "configuration_controlled"
    OPERATOR_CONTROLLED = "operator_controlled"
    RUNTIME_OBSERVED = "runtime_observed"
    CALCULATED = "calculated"
    EXTERNALLY_UPDATED = "externally_updated"


class VariableSensitivity(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    SENSITIVE = "sensitive"
    # NOTE: no 'secret' level — secrets never enter the graph (LAW-010)


class SourceKind(str, Enum):
    NOTE = "note"
    REPOSITORY = "repository"
    DOCUMENT = "document"
    PAPER = "paper"
    API_DOCUMENTATION = "api_documentation"
    FILING = "filing"
    DATASET = "dataset"
    DECISION_RECORD = "decision_record"
    CONVERSATION = "conversation"
    WEB_PAGE = "web_page"


class EvidenceStatus(str, Enum):
    """Evidence classification for extracted/derived knowledge (spec §4)."""

    OBSERVED = "observed"
    REPORTED = "reported"
    INFERRED = "inferred"
    HYPOTHESIS = "hypothesis"
    DECISION = "decision"
    REVIEWED = "reviewed"
