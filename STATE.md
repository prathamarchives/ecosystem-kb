# STATE.md — current implementation state

Last updated: 2026-11-19 (repair/onboarding-host-cli, on top of main@ff32c93)

## Baseline

`main@ff32c93` is the merged plug-and-play milestone (PR #1; CI runs
44480930330 on the PR and 44481173378 on main, both green, 5/5 jobs:
lint, unit, embedded-integration, postgres-integration, docker-build).
In-sandbox verification at merge time: **141 passed / 2 skipped**, ruff
clean, deployment drill 27/27.

## This branch: final onboarding repair

Problems fixed (all owner-facing, found by re-testing the milestone the
way a real installation runs it):

1. **Dead control-plane path (§1).** The CLI's client-management/status/
   doctor commands connected directly to `127.0.0.1:5432` — invalid with
   PostgreSQL on a private Compose network. Replaced with the loopback
   admin API (`/v1/admin/*`, kb:admin scope, audited, REST-only, never
   MCP tools) + `kb-admin` client seeded by the compose migrate one-shot
   from `KB_ADMIN_TOKEN` (ADR-019; docs/HOST-CLI-ARCHITECTURE.md).
2. **API reachability (§2).** Compose now publishes
   `127.0.0.1:${KB_BIND_PORT:-8000}:8000` (loopback only); the CLI reads
   `KB_BIND_HOST/PORT` everywhere; DB ports remain unpublished.
3. **Connect wizard (§3).** Client id defaults from the target; re-running
   connect on an active client REFUSES to rotate without `--rotate`;
   Cursor output is real valid JSON (parsed before display) with actual
   endpoint/token; `--write/--path` merges into mcp.json preserving
   unrelated servers; credentials printed exactly once.
4. **Doctor severity split (§4).** Mandatory failures (API readiness, MCP
   initialize, admin API, .env perms) vs WARN for local-only tunnel,
   no-backup-yet, LLM=read-only; `--strict` escalates warnings.
   A clean local-only install exits 0.
5. **Install-path bug (found by host-level tests).** Deployment state
   (.env, backups, compose reference) was derived from the installed
   package location — `kb setup` from any other directory wrote config
   into the source tree. Now anchored to the working directory; real
   environment variables override .env values.

## Test state (branch, final run)

- unit 60, contract 21 (15 protocol + 6 admin-API over real HTTP),
  security 12, integration 44 (32 + 9 host-subprocess + 3 extraction eval)
  → **137 passed / 2 skipped**; ruff clean; drill 27/27.
- CI now runs host CLI flows + the 30-case extraction eval (fake provider;
  real-provider runbook in docs/EVAL.md). Docker-level §6 checks run on
  the owner host via scripts/host_acceptance.sh (restart persistence, DB
  port privacy, loopback-only bind, tunnel route).

## Honestly not done / limits

1. Container-level §6 items (real `kb up`, restart persistence, port
   scans) execute on the owner's machine — no Docker daemon in the
   sandbox. scripts/host_acceptance.sh automates them.
2. Live named-tunnel check (§6.11) needs the owner's CF_TUNNEL_TOKEN.
3. Real-LLM extraction quality still unmeasured (docs/EVAL.md runbook).

## Next actions

1. Merge this repair PR after CI green.
2. Owner: `bash scripts/host_acceptance.sh` on the real machine.
