-- Gate 1-4: durability, FIFO jobs with leases + dead-letter, durable
-- maintenance proposals, provenance links, source authorization.

-- Full serialized envelope, written in the SAME transaction as the ledger
-- row and the job (fixes the partial-commit window).
ALTER TABLE kb_events ADD COLUMN event_payload TEXT DEFAULT NULL;

-- Request fingerprint: same key + same payload = duplicate. same key +
-- different payload = conflict (not a silent duplicate).
ALTER TABLE kb_events ADD COLUMN request_fingerprint TEXT;

-- FIFO ordering and visibility timestamp for job claims.
ALTER TABLE kb_ingestion_jobs ADD COLUMN created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE kb_ingestion_jobs ADD COLUMN next_attempt_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP;

-- Dead-letter state for exhausted jobs, distinct from 'failed'.
CREATE INDEX IF NOT EXISTS ix_kb_jobs_claim
    ON kb_ingestion_jobs(status, next_attempt_at);

-- Durable maintenance proposals with versioned, idempotent application.
CREATE TABLE IF NOT EXISTS kb_maintenance_actions (
    action_id TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    namespace TEXT NOT NULL,
    target TEXT NOT NULL,
    candidates JSON DEFAULT '[]',
    evidence JSON DEFAULT '[]',
    reason TEXT DEFAULT '',
    risk_level TEXT DEFAULT 'medium',
    reversibility TEXT DEFAULT 'reversible',
    proposal_version TEXT NOT NULL,
    approval_status TEXT DEFAULT 'pending',
    reviewer TEXT,
    reviewed_at TIMESTAMPTZ,
    review_reason TEXT DEFAULT '',
    execution_status TEXT DEFAULT 'not_started',
    executed_at TIMESTAMPTZ,
    executed_by TEXT,
    apply_attempts INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- Episode/source/event/edge provenance mapping (LAW-002, LAW-015):
-- the join that makes provenance RETRIEVABLE, not just recorded.
CREATE TABLE IF NOT EXISTS kb_provenance_links (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    event_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    episode_uuid TEXT,
    source_id TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    source_revision TEXT,
    content_hash TEXT,
    occurred_at TIMESTAMPTZ,
    received_at TIMESTAMPTZ,
    entity_uuid TEXT,
    edge_uuid TEXT,
    fact TEXT DEFAULT '',
    ontology_version TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_prov_edge ON kb_provenance_links(edge_uuid);
CREATE INDEX IF NOT EXISTS ix_prov_entity ON kb_provenance_links(entity_uuid);
CREATE INDEX IF NOT EXISTS ix_prov_event ON kb_provenance_links(event_id);

-- Client-to-source authorization: the ingestion boundary (P1 fix).
ALTER TABLE kb_clients ADD COLUMN allowed_sources JSON DEFAULT '["*"]';
