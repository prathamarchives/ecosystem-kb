# STATE.md — current implementation state

Last updated: 2026-11-19 (repair/resilience branch, post-review b7cbe39)

## Status: failure-resilience repair complete; all review gates closed in-sandbox

This branch answers the 2026-11-19 review of b7cbe39. Every P0/P1/P2 finding
has a code fix plus a test that would fail without it. 113 tests pass / 2
skipped locally; ruff is clean; the full deployment drill passes 27/27.

### Gate 0 — CI and lint
- All 53 (then 81 with new code) ruff findings fixed properly: StrEnum
  conversions inspected (all pure str-enums), the F821 undefined-name bug
  removed with its dead route branch, B008 scoped to FastAPI's Depends
  idiom via per-file-ignores, B017 tests use specific exception types.
- CI is four separate jobs (lint, unit, embedded-integration,
  postgres-integration) — no test step is skipped because a quality gate
  failed. NOTE: GitHub Actions has not yet OBSERVED these jobs running;
  the workflow is committed but the push requires restored credentials.

### Gate 1 — production control plane
- `asyncpg` is a runtime dependency (was dev-only — the P0 image defect);
  the Dockerfile also copies migrations/ and self-checks imports at build.
- Versioned migration runner (`adapters/postgres/migrations.py`): ordered,
  recorded in `kb_schema_migrations`, dialect-aware, idempotent; verified
  against BOTH SQLite and a real PostgreSQL (pgserver) in
  tests/integration/test_postgres.py (incl. ORM/migration parity check).
  Startup runs `migrate()`, not `create_all()`.
- Atomic submit: ledger row + full serialized envelope (`event_payload`) +
  queue job commit in ONE transaction (fault-injection test proves the
  worker can never see a payload-less row).
- `payload_reference` (content URI) and `event_payload` (envelope) are
  separate columns.
- Fingerprint idempotency: same key + same payload = duplicate receipt;
  same key + different payload = 409 ConflictError (tested over HTTPS).
- Source-ownership authorization: clients carry `allowed_sources`; unknown
  sources quarantine under an onboarding review, never accepted as
  authoritative provenance (tested, including a rival-client rejection).
- Production guard: `KB_ENVIRONMENT=production` refuses weak/static tokens
  at startup and disables the dev-token auth fallback (drill runs in this
  mode; the drill's API has NO static tokens).

### Gate 2 — worker recovery
- Atomic claims: Postgres `FOR UPDATE SKIP LOCKED` + `UPDATE..RETURNING`
  (concurrent-claim test with 4 workers / 8 jobs on real PG: exactly-once);
  SQLite path uses a single transaction.
- FIFO by `created_at, next_attempt_at` (not UUID).
- Lease recovery: expired `processing` leases are re-queued on claim
  (dead-worker test: recovery + ownership loss for the dead worker).
- Heartbeat extends leases during processing; complete/fail are
  ownership-guarded (stale worker gets `False` / `lost_ownership`).
- Bounded exponential backoff (`base * 2^(n-1)`, tested); exhausted or
  non-retryable failures go to a distinct `dead_letter` state with the
  error recorded.
- Retry classification corrected: transient infra errors retry, validation
  errors dead-letter (was inverted).
- Crash-after-graph-write converges: reconciliation by episode identity,
  race-tolerant lifecycle transitions (two-worker race test).

### Gate 3 — ontology before canonical writes
- Ingestion uses `add_episode_bulk(strict_edge_types=True)`: graphiti DROPS
  extracted edges that don't use canonical edge types before commit.
- Post-write verification re-validates every returned edge by relation name
  + endpoint labels; non-canonical edges (including RELATES_TO fallbacks)
  are quarantined with review-queue evidence and compensated (deleted
  best-effort).
- `RELATES_TO` removed from the canonical retrieval vocabulary (search and
  entity context filter by registry names only).
- Verified storage model (upstream): graphiti's FalkorDB driver writes all
  entity edges with physical type RELATES_TO and carries the canonical
  relation in the `name` property; all reads filter on `e.name`. Raw-Cypher
  consumers must use `e.name` (documented here + in the drill).
- Tests: unknown predicate (`HATES`) is quarantined and NOT retrievable;
  the tautological `>= 0` assertion is gone.

### Gate 4 — retrievable provenance + durable maintenance
- `kb_provenance_links` maps event ↔ episode ↔ source ↔ entity/edge with
  revision, content hash, timestamps, ontology version.
- Search and entity-context responses carry provenance summaries and source
  references via a wired resolver (not the never-called optional param).
  `GET /v1/sources/{event_id|edge_uuid|entity_uuid}` reconstructs the chain.
- Maintenance proposals live in `kb_maintenance_actions`: stable action ids
  (operation+targets), `proposal_version` hashes binding approvals to the
  exact proposal, guarded state machine (pending→approved/rejected→
  running→completed/failed), apply-attempts counter.
- Apply is restart-safe and idempotent: already-completed is a no-op; every
  merge step is a compare-and-set; a crashed 'running' resumes cleanly
  (fresh-service-instance test simulates restart).
- ADR-013: ENTITY_MERGE is explicitly a control-plane identity/alias merge
  (renamed accurately), not a graph merge.
- Duplicate detection requires same canonical name AND same entity type;
  cross-type name collisions (System "Mercury" vs Claim "Mercury") never
  propose merges (tested).

### Gate 5 — deployment and recovery drill (scripts/deploy_drill.py, 27/27)
- Boots the production stack locally: real PostgreSQL (pgserver) + persistent
  FalkorDB + Caddy TLS (internal CA, unprivileged ports) + uvicorn API +
  worker subprocess, all in production mode.
- Acceptance over HTTPS: authed submit, idempotent duplicate, 401/403
  enforcement, worker processing, SECOND-client retrieval WITH provenance,
  provenance trace by event id.
- REAL backup + restore: pg_dump + checksums, FalkorDB SAVE + RDB copy,
  manifest; restore into FRESH Postgres + FRESH FalkorDB; consistency
  checks (ledger, provenance links, completed status, graph facts,
  canonical relation preserved).
- The backup script itself (scripts/backup.py) fails loudly and writes a
  manifest; the drill is the executable restore-verification procedure.

### Gate 6 — Arivoo as first producer
- `integrations/arivoo_relay.py`: transactional-outbox row model, the
  outbox→kb.event.v1 mapping contract (idempotency key = record+revision,
  supersession payload), and a crash-safe relay (marks sent only after the
  KB confirms; replays deduplicate).
- Acceptance tests (5/5): mapping contract, revision supersession without
  losing history, relay crash→replay exactly-once (exactly one ledger row),
  second-client retrieval with provenance and full chain trace.

## Remaining honestly-not-done
1. **Push + observed CI.** The sandbox was recycled; GitHub credentials are
   gone. The repair branch is committed locally on top of b7cbe39 and ready
   to push when the PAT is restored; the CI workflow has never been watched
   running on GitHub.
2. **Repo visibility.** GitHub reports the repo as public (per the review).
   Decide intentionally; if unintended, switch back to private.
3. **Real VM deployment.** The drill proves the stack works as deployed
   (production mode, TLS, backup/restore) but on sandbox-local processes.
   DNS + a persistent host + scheduled backups remain operational work.
4. **Real-LLM extraction quality** still unmeasured (fake pipeline is real,
   judgment is not).
5. **Ops surface**: queue depth / oldest job / dead-letter metrics are in
   the DB (statuses, next_attempt_at) but not exported as dashboards yet.

## Next actions
1. Restore GitHub credentials; push repair/resilience; watch CI go green.
2. Decide repo visibility.
3. Deploy to the persistent VM using deploy/ + run scripts/deploy_drill.py
   against it unchanged.
4. Wire real Arivoo to the relay contract.
