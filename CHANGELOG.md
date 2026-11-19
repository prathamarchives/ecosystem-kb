# Changelog

## 0.1.0 — 2026-11-18

Initial scaffold: governance, ontology v0.1, kb.event.v1 contracts, control plane,
Graphiti adapter (embedded-FalkorDB verified), REST + MCP gateway, workers, maintainer core.

## [0.2.0] — 2026-11-19 — plug-and-play local + tunnel milestone (branch feat/plug-and-play-local-tunnel)

### Added
- `deploy/compose.local.yaml`: self-contained local stack (PostgreSQL 16.9,
  FalkorDB 4.22.1, one-shot migrate, api, worker, optional cloudflared
  tunnel profile; private network, no published DB ports, healthchecks,
  restart policies, named volumes).
- `kb` CLI: `setup` (0600 .env + generated secrets), `up/down/restart/logs/
  status/doctor/nuke`, `client create/list/revoke/rotate`, `connect
  claude-code|cursor|generic`, `disconnect`, `tunnel status`, `backup` /
  `restore` (manifest + checksums + atomic publish; checksum-gated restore).
- MCP tools `kb_context` (task-shaped bounded retrieval) and `kb_capture`
  (plain structured fields → kb.event.v1 through the existing pipeline;
  per-client source identity; epistemic note; actor from the credential).
- Product-grade tool descriptions (when/what/means/does-not) on all tools.
- Protocol-level MCP tests over real Streamable HTTP (AT-03/04/09
  equivalents), auth negatives (missing/invalid/revoked/read-only), actor
  attribution checks.
- 30-case labelled extraction eval set + harness (LAW invariants asserted;
  quality reported, not gated) and `docs/EVAL.md`.
- Docs: PLUG-AND-PLAY-ACCEPTANCE.md, GAP-REPORT.md, TUNNEL-SETUP.md,
  clients/CONNECT.md, clients/AGENT-INSTRUCTIONS.md.

### Fixed
- **`/mcp` was never reachable over HTTP**: sub-app lifespan never ran
  (FastAPI custom lifespan replaced it), tools could not resolve services
  (sub-app replaces scope["app"]), and initialize succeeded without a valid
  token. Fixed by ADR-018 (chained lifespan, mirrored state, pre-protocol
  auth middleware). Uncovered by the new protocol tests; invisible to the
  old direct-call tests.

### Verified
- Full suite at merge time: 141 passed / 2 skipped (unit 60, contract 15
  incl. 11 protocol-over-HTTP, security 12, integration 35 incl. 3 eval).
  (An earlier draft of this entry said 122; the final run count is 141.)
- Deployment drill: 27/27 including real backup + restore into fresh
  PostgreSQL + FalkorDB instances.

## [0.2.1] — 2026-11-19 — final onboarding repair (repair/onboarding-host-cli)

### Fixed
- Host CLI control-plane access: the direct `127.0.0.1:5432` connection
  was invalid with the private Compose network. All client-management,
  status and doctor operations now go through the loopback admin API
  (`/v1/admin/*`, kb:admin scope, audited, REST-only; never MCP tools).
  `kb-admin` is seeded from `KB_ADMIN_TOKEN` by the compose migrate
  one-shot (ADR-019).
- API published loopback-only: `127.0.0.1:${KB_BIND_PORT:-8000}:8000`;
  KB_BIND_HOST/PORT drive the CLI; databases remain unpublished.
- Connect wizard: default client id from target; no silent rotation
  (explicit `--rotate` required); Cursor output is valid JSON with real
  values, parsed before display; `--write`/`--path` merge into mcp.json
  preserving unrelated servers; credentials shown once.
- Doctor: mandatory failures separated from optional-provisioning
  warnings (local-only tunnel, no-backup-yet, LLM=read-only); clean
  local-only install exits 0; `--strict` for fully provisioned checks.
- Install-path bug: deployment state anchored to the working directory
  (was derived from the installed package location); environment
  variables override .env values.

### Added
- `/v1/admin` routes (create/list/revoke/rotate/stats) with audit records
  and MCP-surface isolation tests; `ClientRepository.get/ensure_admin_client`.
- `kb bootstrap --seed-admin`; `kb connect cursor --write/--path`.
- tests/contract/test_admin_api.py (6, real HTTP), 
  tests/integration/test_host_cli.py (9, real `kb` subprocess flows),
  scripts/host_acceptance.sh (owner-host Docker-level §6 checks).
- CI: host CLI flows + extraction eval (fake provider) in the embedded
  job. docs/HOST-CLI-ARCHITECTURE.md; ADR-019.

### Verified
- Branch final run: 137 passed / 2 skipped; ruff clean; drill 27/27.
  Breakdown: unit 60, contract 21 (15 protocol + 6 admin), security 12,
  integration 44 (32 + 9 host + 3 eval).
