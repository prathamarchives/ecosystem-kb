"""Provenance contract (spec §10, LAW-002, LAW-014).

Every durable claim, decision, finding, and extracted relationship is
traceable. An LLM confidence score is never a substitute for provenance.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProvenanceRecord(BaseModel):
    """Required provenance for a durable assertion (spec §10 table)."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(description="Canonical origin")
    source_uri: str = Field(description="Original location")
    source_revision: str | None = None
    content_hash: str | None = None
    event_id: UUID | None = Field(default=None, description="Ingestion event identity")
    occurred_at: datetime | None = Field(default=None, description="Domain event time")
    recorded_at: datetime | None = Field(default=None, description="System receipt time")
    extraction_version: str | None = Field(
        default=None, description="Extraction implementation version"
    )
    ontology_version: str | None = Field(default=None, description="Ontology used at write time")
    actor_type: str = Field(default="unknown", description="human | application | agent | system")
    review_status: str = Field(default="unreviewed")

    def summary(self) -> str:
        parts = [f"source={self.source_id}"]
        if self.source_revision:
            parts.append(f"rev={self.source_revision}")
        if self.occurred_at:
            parts.append(f"occurred={self.occurred_at.isoformat()}")
        if self.event_id:
            parts.append(f"event={str(self.event_id)[:8]}")
        if self.ontology_version:
            parts.append(f"ontology={self.ontology_version}")
        return " ".join(parts)


class UnknownProvenance(ProvenanceRecord):
    """Explicitly marked unknown provenance — the only legal substitute for
    real provenance (LAW-002). Used for legacy imports under audit."""

    source_id: str = "unknown"
    source_uri: str = "unknown://unknown"
