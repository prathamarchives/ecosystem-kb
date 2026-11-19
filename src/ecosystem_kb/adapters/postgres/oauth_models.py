"""First-party OAuth 2.1 control-plane tables (runbook Step 7).

Additive only: these tables sit beside the existing kb_* control plane in
the SAME PostgreSQL/SQLite database and never touch the knowledge ledger.
Nothing here stores plaintext secrets: codes, tokens and session ids are
stored as SHA-256 hashes (LAW-009/010 discipline).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ecosystem_kb.adapters.postgres.models import Base, _uuid, utcnow


class KbSession(Base):
    """Owner browser sessions (runbook Step 6). Opaque cookie value; only
    its SHA-256 hash is stored. Independently revocable from Supabase."""

    __tablename__ = "kb_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    owner_subject: Mapped[str] = mapped_column(String(100), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Audit-only fingerprint of the login request; never more PII than needed.
    user_agent_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class OauthClient(Base):
    """Registered OAuth clients (runbook Step 7.4): pre-registered, DCR
    (RFC 7591) or CIMD (client_id metadata document) modes."""

    __tablename__ = "oauth_clients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    client_id: Mapped[str] = mapped_column(String(400), unique=True, index=True)
    client_name: Mapped[str] = mapped_column(String(200))
    registration_mode: Mapped[str] = mapped_column(String(20))  # cimd|dcr|pre_registered
    redirect_uris: Mapped[list] = mapped_column(JSON, default=list)
    token_endpoint_auth_method: Mapped[str] = mapped_column(String(40), default="none")
    grant_types: Mapped[list] = mapped_column(JSON, default=list)
    response_types: Mapped[list] = mapped_column(JSON, default=list)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    # CIMD provenance: the metadata URL, its content hash and when validated.
    metadata_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OauthPendingAuthorization(Base):
    """Server-side pending authorization requests (runbook Step 7.6): the
    transaction survives the owner's email-link login round trip."""

    __tablename__ = "oauth_pending_authorizations"
    __table_args__ = (
        Index("ix_pending_auth_expiry", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    request_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    client_id: Mapped[str] = mapped_column(String(400), index=True)
    redirect_uri: Mapped[str] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(Text, nullable=True)
    code_challenge: Mapped[str] = mapped_column(String(128))
    code_challenge_method: Mapped[str] = mapped_column(String(10), default="S256")
    resource: Mapped[str] = mapped_column(Text)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    # Bound to the approving owner session after login (CSRF/substitution guard).
    session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("kb_sessions.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OauthAuthorizationCode(Base):
    """One-use authorization codes (runbook Step 7.1): random, hashed,
    short-lived, bound to client/redirect/PKCE/resource/scopes."""

    __tablename__ = "oauth_authorization_codes"

    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(400), index=True)
    redirect_uri: Mapped[str] = mapped_column(Text)
    code_challenge: Mapped[str] = mapped_column(String(128))
    code_challenge_method: Mapped[str] = mapped_column(String(10), default="S256")
    owner_subject: Mapped[str] = mapped_column(String(100))
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    resource: Mapped[str] = mapped_column(Text)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OauthToken(Base):
    """Opaque access/refresh tokens (runbook Step 7.1): stored hashed,
    resource-bound, family-tracked for rotation replay detection."""

    __tablename__ = "oauth_tokens"
    __table_args__ = (
        Index("ix_oauth_tokens_client_active", "client_id", "revoked_at"),
        Index("ix_oauth_tokens_family", "family_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    token_type: Mapped[str] = mapped_column(String(10))  # access|refresh
    client_id: Mapped[str] = mapped_column(String(400), index=True)
    owner_subject: Mapped[str] = mapped_column(String(100))
    resource: Mapped[str] = mapped_column(Text)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    family_id: Mapped[str] = mapped_column(String(36), index=True)
    replaces_token_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OauthConsent(Base):
    """Stored owner consent per (owner, client, resource) — reconnecting the
    same client with unchanged scopes does not re-prompt (runbook Step 7.6)."""

    __tablename__ = "oauth_consents"
    __table_args__ = (
        UniqueConstraint("owner_subject", "client_id", "resource", name="uq_consent"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    owner_subject: Mapped[str] = mapped_column(String(100), index=True)
    client_id: Mapped[str] = mapped_column(String(400), index=True)
    resource: Mapped[str] = mapped_column(Text)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OauthRegistrationTicket(Base):
    """CIMD metadata cache + DCR registration audit (runbook Step 7.4): the
    fetched document, its hash, and when it was validated. Rate-limit
    accounting uses the same rows."""

    __tablename__ = "oauth_registration_tickets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(10))  # cimd|dcr
    subject: Mapped[str] = mapped_column(Text)  # metadata URL or client_id
    document: Mapped[dict] = mapped_column(JSON, default=dict)
    document_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
