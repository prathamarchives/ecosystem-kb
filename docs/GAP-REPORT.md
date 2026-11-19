# Plug-and-Play Gap Report — `main@5f4d499` vs the execution plan

Audit date: 2026-11-19. Branch: `feat/plug-and-play-local-tunnel` from verified
`main@5f4d499` (CI run 44461844352, success). Baseline re-verified in-sandbox:
113 tests passed / 2 skipped, ruff clean. Verified against current source, not
prior review notes.

## What already satisfies the plan

| Plan requirement | Status at `5f4d499` |
|---|---|
| Streamable HTTP MCP at `/mcp` | Done — FastMCP 4.2.1 `http_app()` mounted at `/mcp` (`src/ecosystem_kb/api/app.py:105`) |
| One service layer under REST + MCP | Done — all 7 tools call `KbServices` (ADR-005) |
| Bearer auth, server-side scopes, hashed tokens | Done — SHA-256 token hashes in `kb_clients`, dev-token fallback disabled in production mode |
| Namespace derived from authenticated identity | Done — `_namespace_for()`, never caller-supplied (LAW-009) |
| Versioned migrations, not `create_all()` | Done — `migrations/001..002` + runner recorded in `kb_schema_migrations` |
| Atomic ledger+payload+queue submit | Done — single transaction, fault-injection tested |
| Idempotent intake, duplicate receipts | Done — `idempotency_key` unique constraint (LAW-008) |
| Provenance on retrieval | Done — `kb_provenance_links`, search/entity responses carry sources + epistemic status |
| Real backup + restore with fresh-instance verification | Done in the drill — `scripts/deploy_drill.py` step 7/7 (pg_dump + FalkorDB SAVE + manifest + restore + consistency checks) |
| Secrets never in the graph | Done — ingestion secret-scan rejects credential-shaped payloads (LAW-010) |
| No public DB ports in Compose | Done — `deploy/compose.yaml` publishes nothing for falkordb |
| Worker leases, retries, dead-letter | Done — FOR UPDATE SKIP LOCKED, bounded backoff, `dead_letter` state |

## Gaps (what this branch must build)

### WP-B — local deployment
1. **No Postgres service in Compose** — `deploy/compose.yaml` expects an external
   DB. The plan requires a self-contained local stack with pinned Postgres.
2. **No local profile separation** — the single compose file conflates a Caddy
   TLS edge (VM profile) with the local tunnel profile; plan §4.1 wants the
   local route to be `http://api:8000` directly.
3. **No `kb up/down/status/doctor/logs/backup/restore` CLI** — current CLI is
   `bootstrap / worker / register-client / graph-health` only.
4. **`scripts/backup.py` is a stub** — 19 lines, prints docker instructions
   instead of executing; no manifest/checksum/atomic archive; restore doesn't
   exist as a command.
5. **No `.env` bootstrap** (`kb setup`) with secret generation and 0600 perms.
6. **FalkorDB image unpinned digest/major pin only (4.22.1)** — acceptable but
   Postgres needs a pin too; no healthchecks on any service; no
   `depends_on: condition: service_healthy` readiness; no migration one-shot.
7. **No restart policy specifics for tunnel; no cloudflared service at all.**

### WP-D — MCP ergonomics
8. **No `kb_context`** (high-level, project/repository-scoped, decisions +
   constraints + contradictions in one bounded response).
9. **No `kb_capture`** (high-level write without the kb.event.v1 envelope).
10. **Tool descriptions are one-liners** — plan §D3 requires when-to-call,
    what-to-supply, epistemic caveats, what-it-does-not-do.
11. **No MCP protocol-level integration test** (initialize → list tools →
    call → structured result) — current `tests/contract/test_mcp.py` calls
    tool functions directly, not over Streamable HTTP.

### WP-E — client experience
12. **No per-client token lifecycle commands** (`client create/list/revoke`,
    rotate) — `register-client` exists but cannot list or revoke.
13. **No `kb connect claude-code|cursor|generic`** wizard, no
    `docs/clients/` snippets, no usage-instruction snippet generation.
14. **No `kb tunnel status` / `kb disconnect`.**

### WP-C — tunnel
15. **No `cloudflared` service or tunnel profile** in Compose; no named-tunnel
    documentation; `KB_PUBLIC_URL` concept absent.

### WP-F — security review
16. **Negative tests exist for** missing/invalid token, reader-writes, namespace
    escape, maintenance scope, secret rejection, audit coverage. **Missing:**
    revoked-token rejection, payload actor-spoofing (event payload claiming a
    different actor), docs exposure check (FastAPI `/docs` behind auth), DB-port
    non-exposure assertion in the compose profile.

### WP-G — model config
17. **No doctor/setup signal for LLM/embedding configuration status**; no
    labelled extraction eval set; extraction quality unmeasured (STATE.md
    honestly-not-done #4).

### WP-H — observability
18. **No `kb status` aggregate view** (queue depth, oldest job, dead letters,
    last backup, tunnel state, app version/commit) — the data exists in the
    control plane; nothing surfaces it in one command.

## Out-of-scope confirmations (plan §0)

No Arivoo runtime connector, no GitHub/Notion/Drive ingestion, no SaaS
multi-user mode, no new frontend, no Graphiti fork, no new graph engine, no
agent framework will be added by this branch. The Arivoo relay stays as the
existing integration-contract test fixture only.

## Sandbox capability note

The agent sandbox has no Docker daemon. All Python-level work (CLI, tools,
backup/restore logic, protocol tests, security negatives) is implemented and
verified here against real pgserver PostgreSQL + embedded FalkorDB (the same
engines CI uses). Container-level acceptance (AT-01/02/06/07 in their literal
Docker form) runs on the owner's machine; the drill and `kb doctor` cover the
equivalent in-sandbox verifications.
