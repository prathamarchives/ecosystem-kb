# DECISIONS.md — accepted architectural decisions

Append-only. Mark superseded in place, never delete. Format: ADR-NNN.

## ADR-001 · Use upstream Graphiti Core as a dependency
**Status:** Accepted (2026-11-18). **Decision:** `graphiti-core==0.31.2` pinned
as installed dependency; customization via its public interfaces (custom Pydantic
entity types, edge types, edge_type_map, group_id resolution). **Rationale:**
our knowledge architecture (ontology, identity, contracts, governance) evolves
much faster than the graph engine; owning a fork multiplies maintenance. **Fork
policy:** a fork requires a reproducible requirement the public API cannot meet
(driver capability gap, upstream defect, identity-resolution behavior,
performance) plus an ADR, test case, and compatibility assessment.

## ADR-002 · FalkorDB as graph persistence
**Status:** Accepted (2026-11-18). **Decision:** FalkorDB behind Graphiti's
FalkorDriver, private network, password-protected. **Rationale:** multi-model
graph store supported natively by graphiti; multi-tenant graph databases map
cleanly to our namespace policy. **Verified:** FalkorDriver works against both
a real server and an embedded server (see ADR-004).

## ADR-003 · PostgreSQL control plane
**Status:** Accepted (2026-11-18). **Decision:** PostgreSQL owns source
registry, event ledger, idempotency, job queue, identity registry, aliases,
review queue, audit log, client registry. SQLAlchemy 2 async ORM; SQLite
(aiosqlite) for local dev/tests, Postgres (asyncpg) in production — same
schema, same repositories. **Rationale:** the control plane must be
deterministic and queryable; it is not a knowledge store.

## ADR-004 · Embedded FalkorDB for in-sandbox integration tests
**Status:** Accepted (2026-11-18). **Decision:** tests boot
`redis-server --loadmodule falkordb.so` from the `falkordblite` wheel in a
temp dir and connect through `falkordb.asyncio.FalkorDB` injected into
FalkorDriver. **Rationale:** no docker in the dev sandbox; this exercises the
production driver code path (real Cypher, real FalkorDB semantics, real
namespacing) rather than a mock. **Findings pinned here:** (1) falkordblite's
own redislite client double-loads the module and fails — start the server
manually, load once; (2) FalkorDriver awaits `graph.query`, so the client must
be `falkordb.asyncio` (sync client returns QueryResult, not a coroutine);
(3) KuzuDriver is deprecated upstream and never sets `_database`, breaking
group_id resolution — do not use it.

## ADR-005 · Custom authenticated gateway (REST + MCP over one service layer)
**Status:** Accepted (2026-11-18). **Decision:** FastAPI app exposing REST
under `/v1/*` and mounting FastMCP's `http_app()` at `/mcp`; both call the
same internal services. Bearer-token clients with server-side scopes
(kb:read, kb:write, kb:review, kb:maintain, kb:admin); namespace derived from
the authenticated client, never from caller arguments. **Rationale:** upstream
graphiti MCP server is experimental, unauthenticated, and exposes clear_graph;
our gateway is the security boundary.

## ADR-006 · One shared production namespace
**Status:** Accepted (2026-11-18). **Decision:** `ecosystem-dev` /
`ecosystem-test` / `ecosystem-prod`; all production applications share
ecosystem-prod. **Rationale:** the point is cross-system knowledge connection;
per-application graphs would defeat it. Source registry distinguishes
ownership.

## ADR-007 · Versioned Pydantic ontology
**Status:** Accepted (2026-11-18). **Decision:** 14 canonical entity types,
16 canonical relationship types with domain/range constraints, typed variable
model (7 kinds, 12 data types, mutability classes); ONTOLOGY_VERSION constant;
extraction outputs are proposals until ontology validation passes; unknown
edges quarantined. **Rationale:** LAW-006; controlled vocabulary beats
agent-invented relationship names.

## ADR-008 · Durable asynchronous ingestion
**Status:** Accepted (2026-11-18). **Decision:** events are validated and
persisted to the ledger synchronously (receipt returned), then processed by a
worker with job leases; at-least-once with idempotency dedup and explicit
reconciliation for ambiguous graph-write outcomes. Single ordered worker per
namespace in v1. **Rationale:** event ledger and graph cannot share a
transaction; a timeout after a graph write is ambiguous, so reconcile by event
identity, never blind-retry.

## ADR-009 · Bounded deterministic maintenance
**Status:** Accepted (2026-11-18). **Decision:** Maintainer is a service with
four modes (audit / proposal / apply / verify); deterministic audits first,
LLM-assisted judgment later; automatic reversible hygiene only; merges,
deletions, bulk rewrites, and ontology migrations require approval.
**Rationale:** LAW-012, LAW-017; a maintainer that cannot be evaluated is a
liability — audits run against seeded-fault fixtures.

## ADR-010 · Original records stay in owning systems
**Status:** Accepted (2026-11-18). **Decision:** Arivoo keeps its Postgres
study records; git keeps code history; the KB stores canonical references,
relationships, provenance, and contextual knowledge. Variable definitions are
in the KB; variable values live in owning systems. **Rationale:** LAW-001,
LAW-007; the KB is the shared context layer, not a replacement database.

## ADR-011 · Deterministic test extraction (marker protocol)
**Status:** Accepted (2026-11-18). **Decision:** the test-suite LLM fake reads
a JSON payload after the `EXTRACTION:` marker in the episode body (last
occurrence, raw_decode) and returns it shaped to graphiti's extraction schemas
(`extracted_entities: [{name, entity_type_id, episode_indices}]`,
`edges: [{source_entity_name, target_entity_name, relation_type, fact}]`).
**Rationale:** tests must control exactly what gets extracted without network
or API keys, while running the real extraction pipeline; the fake also
implements `create_batch` (flat single vector from `create()` even for list
input — graphiti passes `[query]`).

## ADR-012 · Canonical key registry external to the graph
**Status:** Accepted (2026-11-18). **Decision:** `canonical_key →
graphiti_uuid → source_references → aliases` maintained in the control plane;
graph UUIDs are storage details, not identity. **Rationale:** LAW-005; renames
must not create entities; merges preserve aliases.
