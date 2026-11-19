# Plug-and-Play Acceptance Specification

Status: **Accepted** · Date: 2026-11-19 · Branch: `feat/plug-and-play-local-tunnel`
Source plan: `upload/ecosystem-kb-plug-and-play-execution (1).md` (owner-supplied)

This document defines what "plug-and-play" means for ecosystem-kb v0.2 and is
the acceptance contract for the branch. The gap analysis against
`main@5f4d499` lives in `docs/GAP-REPORT.md`.

## 1. Deployment assumptions

- **Single-user, private.** One owner; every connected client acts with the
  owner's authority within its granted scopes. No multi-tenant isolation.
- **Locally hosted.** Docker containers and all primary data live on the
  owner's machine. Cloudflare Tunnel provides the public HTTPS route; the
  service is NOT cloud-hosted compute and the hostname does not move the
  service between machines.
- **Availability = host availability.** The endpoint is reachable while the
  host is powered on, online, and running Docker + `cloudflared`. Sleep,
  shutdown, or disconnection makes the endpoint unavailable without any other
  consequence (no data impact).
- **One production knowledge namespace** (`ecosystem-prod`): all clients share
  it; per-client state lives in the client registry, never in separate graphs.

## 2. Endpoint and transport

- Public MCP endpoint: `https://kb.<owner-domain>/mcp` via a **remotely managed
  named Cloudflare Tunnel** (Quick Tunnels are for smoke tests only — they do
  not support SSE and their URL is ephemeral).
- Transport: MCP Streamable HTTP (FastMCP `http_app()` mounted at `/mcp`).
- REST `/v1/*` and `/health`, `/ready` remain on the same origin for
  diagnostics. No database or graph port is ever routed or published.
- Application bearer authentication is mandatory on every non-health route.
  Cloudflare is transport, not authorization (ADR-005 remains authoritative).

## 3. Client credential model

- Each MCP client gets its own high-entropy token (`secrets.token_urlsafe`),
  shown once at creation, stored only as a SHA-256 hash in `kb_clients`.
- Scopes: `kb:read` (search/context/provenance), `kb:write` (capture/submit),
  `kb:review`, `kb:maintain`, `kb:admin` — unchanged from ADR-005.
- Independent revocation and rotation per client; revoking one client must not
  affect another. The admin token is never reused as a client token.
- Client identity used for provenance and audit is derived from the
  authenticated credential, never from request payloads (LAW-009/LAW-014).

## 4. Required MCP tool surface

High-level (new in this milestone):

- `kb_context` — task-scoped retrieval: entities, facts, decisions,
  constraints, contradictions in one bounded response; project/repository are
  retrieval filters, not security namespaces.
- `kb_capture` — durable capture from plain structured fields (kind, title,
  content, project, source_uri, status, tags). Server builds the kb.event.v1
  envelope, ids, timestamps, idempotency key; records the authenticated actor;
  returns a receipt. Captured content is a claim/decision with its own
  epistemic status — never auto-canonicalized as verified truth (LAW-003).

Existing tools retained unchanged: `kb_search`, `kb_get_entity`,
`kb_get_context` (lower-level), `kb_get_provenance`, `kb_propose_update`,
`kb_get_maintenance_report`, `kb_submit_event` (advanced integrations).

Tool descriptions must state when to call, what to supply, what the result
means (including epistemic status semantics), and what the tool does not do.

## 5. CLI / setup experience

`kb setup` → creates `.env` from `.env.example` only if absent (0600),
generates secrets, validates LLM/embedding config presence (read-only mode
with a clear warning when absent), keeps `.env` gitignored.

`kb up | down | restart | status | logs [service] | doctor | backup |
restore <path>` manage the local Compose stack. `kb down` never deletes
volumes; destructive removal is a separate explicit command with confirmation.

`kb client create|list|revoke`, `kb connect claude-code|cursor|generic`,
`kb tunnel status`, `kb disconnect <client>` complete the owner workflow.

## 6. Persistence and backup expectations

- PostgreSQL and FalkorDB data live in named Docker volumes.
- `kb backup` produces: pg_dump of the control plane, FalkorDB SAVE + RDB
  copy, a manifest (timestamp, app version, DB versions, schema version,
  checksums), atomic archive creation; success only if every artifact exists
  and checksums validate.
- `kb restore` restores into **fresh isolated** services for verification;
  live volumes are never overwritten by a test restore.
- A backup that exists only on the same physical disk does not protect against
  disk loss; the owner is warned and off-site (R2) upload is optional/config.

## 7. Out of scope (unchanged from plan §0)

Arivoo runtime integration, GitHub/Notion/Drive/Slack/Gmail connectors,
economics harness, automatic transcript ingestion, multi-user/SaaS/billing,
web dashboard/frontend, Graphiti replacement or fork, new agent framework,
connector marketplace. No mandatory Cloudflare Access layer in front of MCP
in this release.

## 8. Acceptance test matrix

| ID | Scenario | Pass condition |
|---|---|---|
| AT-01 | Clean first-time setup | `kb setup` + `kb up` from fresh clone; `.env` 0600; migrations run; API/worker/DBs ready; no manual venv/SQL surgery |
| AT-02 | Named tunnel | Dashboard tunnel healthy; `https://kb.<domain>/mcp` resolves; bearer auth enforced at the edge of the app; no public DB port |
| AT-03 | MCP protocol | Inspector (or protocol-equivalent client) initializes, lists tools, calls a read tool, captures, reads back with provenance |
| AT-04 | Two-client shared state | Client A captures → B retrieves; B captures → A retrieves; same namespace; correct attribution |
| AT-05 | Auth & revocation | No/invalid token → 401; revoked token → 401 (≤ cache window); scoped client cannot capture; revoking A leaves B working |
| AT-06 | Host/container restart | Data and MCP access return after API/worker restart and full stack restart; no reinit |
| AT-07 | Tunnel interruption | Endpoint unreachable while `cloudflared` down, local service unharmed; same hostname recovers |
| AT-08 | Backup & restore | `kb backup` artifacts + checksums verified; restore into fresh volumes; captured knowledge retrievable |
| AT-09 | Daily ergonomics | New client = config + token only; `kb_context` from natural language; `kb_capture` without envelope knowledge |
| AT-10 | No accidental integrations | No new source-connector dependency; MCP endpoint works with generic clients |

Sandbox-executable equivalents (no Docker daemon in the agent sandbox):
AT-03 protocol tests run over real HTTP against the production app factory;
AT-04/05/08 run against real pgserver PostgreSQL + embedded FalkorDB through
the same service layer; AT-01/02/06/07 container-level steps execute on the
owner's host with the drill + `kb doctor` covering their logic in-sandbox.
