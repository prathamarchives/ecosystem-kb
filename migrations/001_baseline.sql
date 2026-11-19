-- Baseline control-plane schema (v0.1 @ b7cbe39), dialect-neutral.
CREATE TABLE IF NOT EXISTS kb_sources (
    id TEXT PRIMARY KEY,
    source_id TEXT UNIQUE NOT NULL,
    canonical_uri TEXT NOT NULL,
    source_type TEXT NOT NULL,
    owner_system TEXT NOT NULL,
    trust_class TEXT NOT NULL,
    access_class TEXT DEFAULT 'internal',
    current_revision TEXT,
    content_hash TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS kb_events (
    id TEXT PRIMARY KEY,
    event_id TEXT UNIQUE NOT NULL,
    idempotency_key TEXT UNIQUE NOT NULL,
    schema_version TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_revision TEXT,
    event_type TEXT NOT NULL,
    namespace TEXT NOT NULL,
    payload_reference TEXT DEFAULT '',
    content_hash TEXT,
    occurred_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    processing_status TEXT DEFAULT 'received',
    flags JSON DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_kb_events_status ON kb_events(processing_status);

CREATE TABLE IF NOT EXISTS kb_ingestion_jobs (
    id TEXT PRIMARY KEY,
    event_row_id TEXT NOT NULL REFERENCES kb_events(id),
    status TEXT DEFAULT 'queued',
    attempt_count INTEGER DEFAULT 0,
    worker_id TEXT,
    lease_expires_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS kb_entity_identity (
    id TEXT PRIMARY KEY,
    canonical_key TEXT UNIQUE NOT NULL,
    graphiti_uuid TEXT,
    entity_type TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS kb_aliases (
    id TEXT PRIMARY KEY,
    canonical_key TEXT NOT NULL,
    alias_value TEXT NOT NULL,
    alias_source TEXT DEFAULT 'extraction',
    confidence_basis TEXT DEFAULT 'unverified',
    review_status TEXT DEFAULT 'unreviewed',
    CONSTRAINT uq_alias UNIQUE (canonical_key, alias_value)
);

CREATE TABLE IF NOT EXISTS kb_review_queue (
    id TEXT PRIMARY KEY,
    item_type TEXT NOT NULL,
    item_reference TEXT NOT NULL,
    proposed_action TEXT DEFAULT '',
    reason TEXT DEFAULT '',
    evidence_reference TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    reviewer TEXT,
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS kb_audit_log (
    id TEXT PRIMARY KEY,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    reason TEXT DEFAULT '',
    before_reference TEXT,
    after_reference TEXT,
    occurred_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS kb_clients (
    id TEXT PRIMARY KEY,
    client_id TEXT UNIQUE NOT NULL,
    client_name TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    scopes JSON DEFAULT '[]',
    allowed_namespaces JSON DEFAULT '[]',
    status TEXT DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    rotated_at TIMESTAMPTZ
);
