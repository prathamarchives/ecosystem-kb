"""kb.event.v1 — the universal event envelope (spec §21).

Every connected system sends this common envelope. Domain-specific fields
belong in typed payloads, never as arbitrary envelope additions.

Validation is the ingress gate: provenance (LAW-002), idempotency (LAW-008),
temporal sanity (LAW-004), and secret rejection (LAW-010) all start here.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "kb.event.v1"

IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._:-]{2,200}$")
CANONICAL_URI_PATTERN = re.compile(r"^[a-z][a-z0-9+.-]*://\S+$")


class ActorType(StrEnum):
    HUMAN = "human"
    APPLICATION = "application"
    AGENT = "agent"
    SYSTEM = "system"
    UNKNOWN = "unknown"


class ContentType(StrEnum):
    TEXT = "text"
    MARKDOWN = "markdown"
    JSON = "json"
    HTML = "html"
    REFERENCE = "reference"


class EventClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trust_class: str = Field(
        default="system_generated", description="TrustClass value (origin, not truth)"
    )
    review_status: str = Field(default="unreviewed")
    sensitivity: str = Field(default="internal")


class EventSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system_id: str = Field(description="Canonical system identity, e.g. 'system:arivoo'")
    record_id: str = Field(min_length=1)
    revision: str | int | None = None
    canonical_uri: str = Field(description="Stable reference to the original record")

    @field_validator("canonical_uri")
    @classmethod
    def uri_has_scheme(cls, v: str) -> str:
        if not CANONICAL_URI_PATTERN.match(v):
            raise ValueError("canonical_uri must be a URI with a scheme, e.g. arivoo://notes/123")
        return v


class EventTimestamps(BaseModel):
    model_config = ConfigDict(extra="forbid")

    occurred_at: datetime = Field(description="Domain time: when it happened in the source system")
    recorded_at: datetime = Field(description="Receipt time set by the KB at ingestion")

    @model_validator(mode="after")
    def recorded_not_before_occurred(self) -> EventTimestamps:
        # recorded_at may precede occurred_at legitimately (delayed backfill),
        # but a recorded_at in the future or a >1y skew is a data error.
        now = datetime.now().astimezone()
        if self.recorded_at > now:
            raise ValueError("recorded_at is in the future")
        delta = abs((self.recorded_at - self.occurred_at).total_seconds())
        if delta > 366 * 24 * 3600:
            raise ValueError("occurred_at/recorded_at skew exceeds one year")
        return self


class EventActor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ActorType = ActorType.UNKNOWN
    id: str | None = None


class EventContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_type: ContentType = ContentType.TEXT
    content_reference: str | None = Field(
        default=None, description="URI of full content when not inlined"
    )
    content_hash: str | None = Field(default=None, description="sha256:... integrity digest")
    body: str | None = Field(
        default=None, max_length=100_000, description="Inlined content (bounded)"
    )

    @model_validator(mode="after")
    def body_or_reference(self) -> EventContent:
        if self.body is None and self.content_reference is None:
            raise ValueError("content needs a body or a content_reference")
        return self


class KnowledgeEvent(BaseModel):
    """The envelope every integrating system submits to POST /v1/events."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=SCHEMA_VERSION)
    event_id: UUID = Field(default_factory=uuid4)
    idempotency_key: str = Field(
        description="Caller-scoped unique key, e.g. 'arivoo:note-123:revision-4'"
    )
    event_type: str = Field(
        min_length=3, max_length=100, description="Namespaced type, e.g. knowledge.source.updated"
    )

    source: EventSource
    timestamps: EventTimestamps
    actor: EventActor = Field(default_factory=EventActor)
    classification: EventClassification = Field(default_factory=EventClassification)
    content: EventContent
    payload: dict = Field(
        default_factory=dict,
        description="Typed domain payload (validated separately per event_type)",
    )

    @field_validator("schema_version")
    @classmethod
    def version_is_v1(cls, v: str) -> str:
        if v != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {v!r}; this gateway speaks {SCHEMA_VERSION}")
        return v

    @field_validator("idempotency_key")
    @classmethod
    def idempotency_key_shape(cls, v: str) -> str:
        if not IDEMPOTENCY_KEY_PATTERN.match(v):
            raise ValueError(
                "idempotency_key must be lowercase slug with : . _ - separators"
            )
        return v

    @field_validator("event_type")
    @classmethod
    def event_type_namespaced(cls, v: str) -> str:
        if "." not in v:
            raise ValueError("event_type must be namespaced, e.g. knowledge.note.committed")
        return v


# Event types we understand in v1 (anything else still ingests, flagged 'unknown')
KNOWN_EVENT_TYPES = {
    "knowledge.note.committed",
    "knowledge.note.revised",
    "knowledge.source.updated",
    "knowledge.decision.recorded",
    "knowledge.decision.revised",
    "knowledge.claim.submitted",
    "knowledge.evidence.submitted",
    "knowledge.experiment.completed",
    "knowledge.finding.recorded",
    "knowledge.variable.defined",
    "knowledge.system.registered",
    "knowledge.revision.superseded",
}


class EventReceipt(BaseModel):
    """Deterministic response for every accepted (or deduplicated) event."""

    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    idempotency_key: str
    status: str = Field(description="Ingestion-processing state (spec §11)")
    duplicate: bool = False
    received_at: datetime
    detail: str | None = None
