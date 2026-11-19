-- 003: First-party OAuth 2.1 tables (runbook Steps 6-7). Additive only.
-- Opaque values are stored as sha256 hex (64 chars). No plaintext secrets.

CREATE TABLE kb_sessions (
    id VARCHAR(36) PRIMARY KEY,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    owner_subject VARCHAR(100) NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    revoked_at TEXT,
    user_agent_hash VARCHAR(64)
);
CREATE INDEX ix_kb_sessions_owner ON kb_sessions (owner_subject);
CREATE INDEX ix_kb_sessions_expires ON kb_sessions (expires_at);

CREATE TABLE oauth_clients (
    id VARCHAR(36) PRIMARY KEY,
    client_id VARCHAR(400) NOT NULL UNIQUE,
    client_name VARCHAR(200) NOT NULL,
    registration_mode VARCHAR(20) NOT NULL,
    redirect_uris JSON NOT NULL DEFAULT '[]',
    token_endpoint_auth_method VARCHAR(40) NOT NULL DEFAULT 'none',
    grant_types JSON NOT NULL DEFAULT '[]',
    response_types JSON NOT NULL DEFAULT '[]',
    scopes JSON NOT NULL DEFAULT '[]',
    metadata_url TEXT,
    metadata_hash VARCHAR(64),
    metadata_validated_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_used_at TEXT,
    disabled_at TEXT
);
CREATE INDEX ix_oauth_clients_client_id ON oauth_clients (client_id);

CREATE TABLE oauth_pending_authorizations (
    id VARCHAR(36) PRIMARY KEY,
    request_hash VARCHAR(64) NOT NULL UNIQUE,
    client_id VARCHAR(400) NOT NULL,
    redirect_uri TEXT NOT NULL,
    state TEXT,
    code_challenge VARCHAR(128) NOT NULL,
    code_challenge_method VARCHAR(10) NOT NULL DEFAULT 'S256',
    resource TEXT NOT NULL,
    scopes JSON NOT NULL DEFAULT '[]',
    session_id VARCHAR(36) REFERENCES kb_sessions(id),
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL
);
CREATE INDEX ix_pending_auth_client ON oauth_pending_authorizations (client_id);
CREATE INDEX ix_pending_auth_status ON oauth_pending_authorizations (status);
CREATE INDEX ix_pending_auth_expiry ON oauth_pending_authorizations (expires_at);

CREATE TABLE oauth_authorization_codes (
    code_hash VARCHAR(64) PRIMARY KEY,
    client_id VARCHAR(400) NOT NULL,
    redirect_uri TEXT NOT NULL,
    code_challenge VARCHAR(128) NOT NULL,
    code_challenge_method VARCHAR(10) NOT NULL DEFAULT 'S256',
    owner_subject VARCHAR(100) NOT NULL,
    session_id VARCHAR(36),
    resource TEXT NOT NULL,
    scopes JSON NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,
    used_at TEXT
);
CREATE INDEX ix_oauth_codes_client ON oauth_authorization_codes (client_id);

CREATE TABLE oauth_tokens (
    id VARCHAR(36) PRIMARY KEY,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    token_type VARCHAR(10) NOT NULL,
    client_id VARCHAR(400) NOT NULL,
    owner_subject VARCHAR(100) NOT NULL,
    resource TEXT NOT NULL,
    scopes JSON NOT NULL DEFAULT '[]',
    family_id VARCHAR(36) NOT NULL,
    replaces_token_id VARCHAR(36),
    issued_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at TEXT,
    consumed_at TEXT
);
CREATE UNIQUE INDEX ix_oauth_tokens_hash ON oauth_tokens (token_hash);
CREATE INDEX ix_oauth_tokens_client_active ON oauth_tokens (client_id, revoked_at);
CREATE INDEX ix_oauth_tokens_family ON oauth_tokens (family_id);
CREATE INDEX ix_oauth_tokens_expires ON oauth_tokens (expires_at);

CREATE TABLE oauth_consents (
    id VARCHAR(36) PRIMARY KEY,
    owner_subject VARCHAR(100) NOT NULL,
    client_id VARCHAR(400) NOT NULL,
    resource TEXT NOT NULL,
    scopes JSON NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    revoked_at TEXT,
    CONSTRAINT uq_consent UNIQUE (owner_subject, client_id, resource)
);
CREATE INDEX ix_oauth_consents_owner ON oauth_consents (owner_subject);

CREATE TABLE oauth_registration_tickets (
    id VARCHAR(36) PRIMARY KEY,
    kind VARCHAR(10) NOT NULL,
    subject TEXT NOT NULL,
    document JSON NOT NULL DEFAULT '{}',
    document_hash VARCHAR(64) NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT
);
