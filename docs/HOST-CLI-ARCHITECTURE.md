# Host CLI architecture and its security boundary (ADR-019)

Status: Accepted · Date: 2026-11-19 · Scope: local Compose deployment

## Why this document exists

The plug-and-play milestone shipped a host CLI whose control-plane
commands connected directly to `127.0.0.1:5432`. With PostgreSQL on a
private Docker network (correctly, deliberately unpublished), that path is
dead on arrival: `kb client create` failed on every real installation.
This is the record of the repaired architecture and its boundary.

## The boundary

```
owner's machine
┌────────────────────────────────────────────────────────────┐
│  host CLI (kb)            MCP clients      MCP Inspector   │
│      │                        │                │           │
│      ▼ (admin API, loopback)  ▼ (MCP, loopback or tunnel)   │
│  127.0.0.1:${KB_BIND_PORT:-8000} ─────────────┘            │
│      │ published ONLY on the loopback interface             │
├──────┼─────────────────────────────────────────────────────┤
│  Docker network kb-internal (private)                       │
│   api :8000 ── postgres :5432 (no published ports)          │
│        └──── falkordb :6379 (no published ports)            │
│        └──── cloudflared → outbound-only tunnel (optional)  │
└────────────────────────────────────────────────────────────┘
```

- **The host CLI never opens a socket to PostgreSQL or FalkorDB.**
  Privileged operations (client create/list/revoke/rotate, service stats)
  go through `/v1/admin/*` on the loopback API with `KB_ADMIN_TOKEN`.
- The admin routes require the `kb:admin` scope, write audit records
  (LAW-014), return credentials exactly once, and are REST-only — they are
  deliberately NOT registered as MCP tools, so no MCP client (however
  scoped) can mint or revoke credentials.
- `kb up/down/logs/backup/restore` shell out to `docker compose`, which
  talks to the Docker socket — an owner-level trusted channel — never to
  the databases over the network.
- The API port is published as `127.0.0.1:${KB_BIND_PORT}:8000` only.
  LAN machines cannot reach the API; the Cloudflare tunnel reaches
  `http://api:8000` from inside the Docker network.
- Compose-control commands (up/down/logs/backup/restore) shell out to
  `docker compose`, which talks to the Docker socket, not the databases.

## Why an admin API rather than a one-shot container

Both approaches were viable. The admin API won because:

1. The API is already the hardened surface: bearer auth, hashed tokens,
   scope enforcement, audit logging, error mapping. A container path would
   have re-implemented credential handling around `docker compose exec`.
2. The CLI works the same way when the stack runs on another machine on
   the LAN later (point `KB_BIND_HOST` at it) — the container path is
   inherently local-only.
3. Failure modes are observable: the CLI reports HTTP-level errors
   ("stack up?", "credential rejected") instead of Docker pull/exit codes.

The `kb bootstrap --seed-admin` path remains for direct (non-Compose)
deployments, and the compose `migrate` one-shot uses it to seed `kb-admin`
from `KB_ADMIN_TOKEN` before the API starts.

## Deployment state location

`.env`, `backups/`, and the `deploy/compose.local.yaml` reference anchor to
**the directory the owner runs `kb` from** (the deployment checkout), not
the installed package's source tree. Real environment variables (`KB_*`,
`CF_*`) override `.env` values — CI and tests steer the CLI without
touching files. The admin credential lives only in the untracked `.env`
(0600) and as a SHA-256 hash in `kb_clients`.

## Verification

- `tests/contract/test_admin_api.py` — lifecycle, scope negatives, audit
  rows, MCP-surface isolation, over real HTTP.
- `tests/integration/test_host_cli.py` — the §6 matrix executed through
  the real `kb` binary as a subprocess against a live server.
- `scripts/host_acceptance.sh` — Docker-level checks on the owner host:
  restart persistence, DB port privacy, loopback-only API bind, tunnel
  route.
