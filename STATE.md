# STATE.md — current implementation state

Last updated: 2026-11-18 (initial scaffold)

## Status: R0→R1 vertical slice complete; R2 engine proof done in-sandbox

Release map (see docs/spec/EKIS-SPEC-001.md Part XVIII):

- **R0 Repository & governance — DONE.** Governance files, pyproject, CI, env
  contract, spec under docs/spec/.
- **R1 Ontology & contracts — DONE (v0.1).** 14 entity types, 16 relationship
  types with domain/range, variable model, kb.event.v1 envelope, provenance
  contract. Unit + fixture tests green.
- **R2 Graph engine — DONE in-sandbox.** Embedded FalkorDB (falkordblite
  server + falkordb.asyncio client) + FalkorDriver + graphiti-core 0.31.2:
  add_episode → typed extraction → hybrid search → episode retrieval, all
  namespace-scoped, verified by tests/integration/test_vertical_slice.py.
  Real FalkorDB server (docker) covered by deploy/compose.yaml; deployment
  acceptance still pending (G2 partial: backups not yet exercised).
- **R3 Control plane — DONE (SQLite dev path).** 8 tables, async
  repositories, idempotency at ingress, job leases, audit log. Postgres
  migrations file present but not yet exercised against a real Postgres
  (pending: first deployment).
- **R4 Gateway — DONE.** REST /v1 (events, search, entities, sources,
  maintenance, health/ready), bearer auth, scopes, server-side namespace.
  Contract tests green (in-process TestClient + embedded FalkorDB).
- **R5 MCP — DONE.** FastMCP 4.2.1 http_app mounted at /mcp; 7 restricted
  tools; same service layer as REST. In-process tool tests green.
- **R6 Maintainer — DONE (deterministic core).** Duplicate/contradiction/
  orphan audits, proposal lifecycle, approval gate, apply+verify; LLM-assisted
  judgment intentionally deferred (ADR-009).
- **R7 Production deployment — NOT STARTED.** Compose + Caddyfile + backup
  scripts written; needs a VM, DNS, secrets, and the deployment acceptance
  checklist (docs/spec §41) executed.
- **R8 Arivoo integration — NOT STARTED.** Blocked on Arivoo's outbox
  contract being stable (its side), not on this repo.

## Known constraints / caveats

- In-sandbox tests use an embedded FalkorDB (ADR-004). Production uses the
  compose stack; first deployment must run the acceptance checklist.
- SQLite dev DB is for local development only; production schema parity is
  the migrations/postgres/001_init.sql file (untested against live Postgres).
- Extraction in tests is deterministic via the marker protocol (ADR-011);
  real LLM extraction quality is untested until a provider key is configured.
- `kb:maintain`-scoped operations never auto-execute destructive actions.

## Next actions

1. Deploy R7 to a persistent VM (compose up, TLS, backups, restore drill).
2. Configure a real LLM/embedder provider; run extraction quality eval
   against fixtures.
3. Define Arivoo outbox → kb.event.v1 mapping with the Arivoo side.
