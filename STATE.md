# STATE.md — current implementation state

Last updated: 2026-11-19 (feat/plug-and-play-local-tunnel, post main@5f4d499)

## Status: plug-and-play milestone implemented on the branch; push/CI/merge pending

This branch implements the 2026-11-19 plug-and-play execution plan against
verified main@5f4d499 (CI run 44461844352 green; baseline 113 tests / 2
skipped re-verified in-sandbox before any change).

## Critical fix discovered by this work
`/mcp` was NEVER reachable over HTTP on main (ADR-018): the FastMCP
sub-app's lifespan (session-manager init) never ran because FastAPI's
custom lifespan replaced it; tools could not resolve services (the mounted
sub-app replaces scope["app"]); and protocol initialize succeeded with ANY
or NO token. Existing tests called tool functions directly, and the drill
only exercised REST, so this was invisible. Fixed via chained lifespan +
mirrored sub-app state + McpAuthMiddleware (pre-protocol bearer
validation: missing/invalid/revoked → 401 at initialize). Protocol-level
tests over real uvicorn + Streamable HTTP now gate this.

## What the branch delivers (plan § map)
- WP-A: docs/GAP-REPORT.md, docs/PLUG-AND-PLAY-ACCEPTANCE.md, ADR-017.
- WP-B: deploy/compose.local.yaml (pg 16.9-alpine, falkordb 4.22.1 pinned;
  private network, no DB ports; healthchecks; one-shot migrate service;
  restart policies; cloudflared behind `tunnel` profile), .env.example
  regroup, full kb CLI (setup/up/down/restart/logs/status/doctor/nuke/
  client create-list-revoke-rotate/connect/disconnect/tunnel/backup/
  restore), EventRepository.queue_stats, client lifecycle in repos
  (audited). Backup: real pg_dump + FalkorDB SAVE + manifest + checksums +
  verify-then-atomic-publish; restore checksum-gated with live-volume
  confirmation. Drill re-run: 27/27 (backup+restore into fresh instances).
- WP-C: cloudflared 2025.11.1 service + docs/TUNNEL-SETUP.md (dashboard
  steps, token handling, AT-02 verification, VM migration path).
- WP-D: kb_context + kb_capture over the SAME service layer (per-client
  source system:mcp:<client_id>, server-generated ids/idempotency, actor
  from the authenticated credential, epistemic note); product-grade tool
  descriptions; 9-tool surface; protocol tests.
- WP-E: connect wizard + docs/clients/CONNECT.md + AGENT-INSTRUCTIONS.md.
- WP-F: negatives — missing/invalid/revoked token 401 pre-protocol,
  reader-cannot-capture, actor attribution from credential only, capture
  goes through the secret-scan gate.
- WP-G: 30-case labelled extraction eval + harness; LAW invariants
  asserted, quality reported; docs/EVAL.md with real-provider runbook.
- WP-H: kb status/doctor output per plan §12 (containers, MCP endpoint,
  readiness, public URL, last backup, knowledge counts, queue depth).

## Test state (in-sandbox, final run on branch)
unit 60, contract 15 (incl. 11 protocol-over-HTTP), security 12,
integration 32 + eval 3 → 122 passed / 2 skipped; ruff clean.

## Honestly not done / limits
1. Push + observed CI + merge — next action (this file documents the
   pre-push state).
2. Container-level acceptance (AT-01/02/06/07 literal Docker form) needs
   the owner's host: no Docker daemon in the agent sandbox. The compose
   file, drill, and kb doctor cover the equivalent logic in-sandbox.
3. Named-tunnel live test (AT-02) requires the owner's Cloudflare domain +
   token in local .env. Docs/TUNNEL-SETUP.md has the exact steps.
4. Real-LLM extraction quality unmeasured (fake provider proves plumbing;
   runbook in docs/EVAL.md).
5. Sandbox was hard-recycled before this session: repos/, tools/, memory/,
   .secrets/ were rebuilt from GitHub + conversation-held PAT.

## Next actions
1. Push feat/plug-and-play-local-tunnel; watch CI; merge after green.
2. Owner: machine with Docker → kb setup / kb up / kb doctor (AT-01).
3. Owner: Cloudflare tunnel per docs/TUNNEL-SETUP.md → kb tunnel status.
4. Owner: connect clients per docs/clients/CONNECT.md (AT-03..05, 09).
5. Real-provider eval baseline recorded in docs/EVAL.md.
