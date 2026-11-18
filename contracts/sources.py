"""Source registration contract (spec §3.1, §5.6, control-plane kb_sources)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from contracts.events import CANONICAL_URI_PATTERN


def utcnow() -> datetime:
    return datetime.now(UTC)


class SourceRegistration(BaseModel):
    """Register a source system or artifact in the KB's source registry."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(
        description="Caller-chosen stable ID, e.g. 'arivoo' or 'arivoo:notes'"
    )
    canonical_uri: str
    source_type: str = Field(description="SourceKind value")
    owner_system: str = Field(description="Canonical system key, e.g. 'system:arivoo'")
    trust_class: str
    access_class: str = "internal"
    current_revision: str | None = None
    content_hash: str | None = None

    @field_validator("canonical_uri")
    @classmethod
    def uri_has_scheme(cls, v: str) -> str:
        if not CANONICAL_URI_PATTERN.match(v):
            raise ValueError("canonical_uri must carry a scheme")
        return v


class SourceRecord(BaseModel):
    """Source as stored/returned by the control plane."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    source_id: str
    canonical_uri: str
    source_type: str
    owner_system: str
    trust_class: str
    access_class: str
    current_revision: str | None = None
    content_hash: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
