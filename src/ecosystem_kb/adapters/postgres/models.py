"""Control-plane schema (spec Part X, §20): 8 tables, async SQLAlchemy 2.

PostgreSQL in production, SQLite (aiosqlite) for local dev — same models,
same repositories. The control plane is deterministic state: it governs what
enters the graph and how we manage it. It is NOT a second knowledge graph.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class KbSource(Base):
    """§20.1 — registered source systems and artifacts."""

    __tablename__ = "kb_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source_id: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    canonical_uri: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(50))
    owner_system: Mapped[str] = mapped_column(String(200))
    trust_class: Mapped[str] = mapped_column(String(50))
    access_class: Mapped[str] = mapped_column(String(20), default="internal")
    current_revision: Mapped[str | None] = mapped_column(String(100), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KbEvent(Base):
    """§20.2 — the event ledger. Idempotency key is UNIQUE (LAW-008)."""

    __tablename__ = "kb_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(220), unique=True, index=True)
    schema_version: Mapped[str] = mapped_column(String(30))
    source_id: Mapped[str] = mapped_column(String(200), index=True)
    source_revision: Mapped[str | None] = mapped_column(String(100), nullable=True)
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    namespace: Mapped[str] = mapped_column(String(100), index=True)
    payload_reference: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str | None] = mapped_column(String(100), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processing_status: Mapped[str] = mapped_column(String(30), default="received", index=True)
    flags: Mapped[dict] = mapped_column(JSON, default=dict)


class KbIngestionJob(Base):
    """§20.3 — background work with leases (ADR-008)."""

    __tablename__ = "kb_ingestion_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_row_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("kb_events.id"), index=True
    )
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    worker_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class KbEntityIdentity(Base):
    """§20.4 — canonical identity registry (LAW-005, ADR-012)."""

    __tablename__ = "kb_entity_identity"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    canonical_key: Mapped[str] = mapped_column(String(300), unique=True, index=True)
    graphiti_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    entity_type: Mapped[str] = mapped_column(String(50))
    canonical_name: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KbAlias(Base):
    """§20.5 — alias → canonical identity mappings."""

    __tablename__ = "kb_aliases"
    __table_args__ = (UniqueConstraint("canonical_key", "alias_value", name="uq_alias"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    canonical_key: Mapped[str] = mapped_column(String(300), index=True)
    alias_value: Mapped[str] = mapped_column(String(300), index=True)
    alias_source: Mapped[str] = mapped_column(String(20), default="extraction")
    confidence_basis: Mapped[str] = mapped_column(String(20), default="unverified")
    review_status: Mapped[str] = mapped_column(String(20), default="unreviewed")


class KbReviewQueue(Base):
    """§20.6 — claims and maintenance recommendations requiring review."""

    __tablename__ = "kb_review_queue"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    item_type: Mapped[str] = mapped_column(String(30), index=True)
    item_reference: Mapped[str] = mapped_column(String(400), index=True)
    proposed_action: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence_reference: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    reviewer: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KbAuditLog(Base):
    """§20.7 — immutable operational audit history (LAW-014).

    Application-level discipline: nothing updates or deletes rows here;
    the append-only rule is enforced by never exposing mutation paths."""

    __tablename__ = "kb_audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100), index=True)
    resource_type: Mapped[str] = mapped_column(String(50))
    resource_id: Mapped[str] = mapped_column(String(400))
    reason: Mapped[str] = mapped_column(Text, default="")
    before_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KbClient(Base):
    """§20.8 — integrating systems and their capabilities (LAW-009)."""

    __tablename__ = "kb_clients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    client_id: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    client_name: Mapped[str] = mapped_column(String(200))
    token_hash: Mapped[str] = mapped_column(String(100))  # sha256, never the token
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    allowed_namespaces: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


ALL_TABLES = [
    KbSource,
    KbEvent,
    KbIngestionJob,
    KbEntityIdentity,
    KbAlias,
    KbReviewQueue,
    KbAuditLog,
    KbClient,
]
