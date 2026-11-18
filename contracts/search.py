"""Search request/response contracts (spec §22, LAW-015).

Responses must carry sources, temporal relevance, epistemic status, and
contradictions — retrieval transparency is a law, not a nicety.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500)
    num_results: int = Field(default=10, ge=1, le=50)
    namespace: str | None = Field(
        default=None,
        description="Optional; must be within the client's allowed namespaces",
    )
    include_episodes: bool = False


class FactResult(BaseModel):
    """One retrieved fact with its provenance and temporal frame."""

    model_config = ConfigDict(extra="forbid")

    fact: str
    relation: str | None = None
    source_name: str | None = None
    target_name: str | None = None
    valid_at: datetime | None = None
    invalid_at: datetime | None = None
    expired_at: datetime | None = None
    epistemic_status: str | None = None
    review_status: str | None = None
    evidence_status: str | None = None
    provenance: str | None = Field(
        default=None, description="ProvenanceRecord.summary() when available"
    )
    source_reference: str | None = Field(default=None, description="canonical_uri of origin")
    contradiction_with: list[str] = Field(
        default_factory=list, description="Facts this one contradicts"
    )


class EntityResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_key: str | None = None
    name: str
    entity_type: str | None = None
    summary: str = ""


class SearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    namespace: str
    facts: list[FactResult] = Field(default_factory=list)
    entities: list[EntityResult] = Field(default_factory=list)
    contradictions: list[str] = Field(
        default_factory=list, description="IDs/summaries of conflicting knowledge found"
    )
    retrieval_metadata: dict = Field(default_factory=dict)


class EntityContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity: EntityResult
    related_facts: list[FactResult] = Field(default_factory=list)
    related_entities: list[EntityResult] = Field(default_factory=list)
    provenance: list[str] = Field(default_factory=list, description="Provenance summaries")


class ProvenanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference: str
    provenance: dict = Field(default_factory=dict)
    history: list[dict] = Field(default_factory=list, description="Ordered provenance trail")
