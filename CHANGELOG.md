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

## [0.3.0] — 2026-11-19 — supermemory-style MCP OAuth (branch feat/supermemory-style-mcp-oauth)

### Added
- First-party OAuth 2.1 Authorization Server (runbook Steps 5-8):
  authorization-code + PKCE S256 only, public clients (auth method `none`),
  exact redirect-URI matching, resource binding to the canonical MCP
  resource, opaque hashed tokens (access 1h, refresh 30d rotating), replay
  detection with family revocation, RFC 7009 revocation, RFC 7591 DCR
  endpoint, CIMD (client-id metadata document) resolution with an
  SSRF-hardened HTTPS fetcher, RFC 9728 protected-resource + RFC 8414
  authorization-server metadata (same-origin, issuer = the public origin).
- Owner login (Step 6): Supabase magic link (PKCE, shouldCreateUser false)
  verified server-side (JWKS + issuer + audience + owner sub) and exchanged
  for a first-party `kb_session` cookie (HttpOnly, Secure, SameSite=Lax,
  7-day lifetime, stored hashed, independently revocable). Routes:
  `/auth/config`, `/auth/session`, `/auth/me`, `/auth/logout`,
  `/auth/callback`.
- One-origin dashboard (Step 9): FastAPI-served static HTML + vanilla JS,
  four tabs (Knowledge search/capture, Clients with revocation, Connect
  with the canonical snippet, Status), CSP + security headers, untrusted
  values rendered via textContent only, owner-session-authenticated data
  endpoints with Origin checking.
- Typed capture extraction (Step 10): `kb_capture` accepts an optional
  `extraction` object (entities/edges aligned to the controlled ontology),
  validated at submit AND in the worker; invalid extraction is rejected
  with specific reasons; missing extraction under the fake provider
  reports an honest `capture saved; extraction unavailable` status.
- Database migration 003 (additive): kb_sessions, oauth_clients,
  oauth_pending_authorizations, oauth_authorization_codes, oauth_tokens,
  oauth_consents, oauth_registration_tickets. No existing table touched.
- Config contract (Step 5): `KB_PUBLIC_BASE_URL` canonical (KB_PUBLIC_URL
  one-release alias), `KB_MCP_RESOURCE`, `KB_OWNER_SUPABASE_SUBJECT`,
  `KB_ALLOW_STATIC_TOKENS` / `KB_ALLOW_LEGACY_CLIENT_TOKENS` (both default
  false), production startup validation (HTTPS origin, resource binding,
  unsafe static-token refusal).
- Protocol test suites: tests/contract/test_oauth_as.py (metadata,
  DCR, authorize/consent/resume, PKCE negatives, rotation + replay +
  family revocation, dashboard isolation, cross-origin refusal) and
  tests/contract/test_extraction.py (ontology validation matrix).
- scripts/oauth_smoke.py: 40-check end-to-end protocol probe (with the app
  lifespan running).

### Fixed
- `/mcp` trailing-slash redirect (the CI-red regression): the canonical
  endpoint now serves directly with no 307. Root cause: FastAPI Mount +
  redirect_slashes rewrote bare `/mcp` before any mounted app ran; the MCP
  sub-app is now owned by a raw ASGI route (`/mcp` and `/mcp/` alias) that
  reproduces the Mount root_path semantics without the redirect. `kb
  connect` emits the canonical no-slash URL.
- Refresh with a wrong `resource` no longer burns the refresh token
  (client binding + resource validated before consumption).
- offline_access now persists on stored token scopes so refresh rotation
  re-issues refresh tokens correctly.

### Changed
- Supabase user JWTs are NO LONGER accepted as MCP/REST bearer tokens.
  Supabase verifies the owner at login only. The `/oauth/*` proxy to
  Supabase is removed; Ecosystem KB is the issuer.
- One principal resolver (AuthService): KB OAuth tokens first (formerly
  valid-but-revoked OAuth tokens never fall through to weaker paths),
  loopback kb-admin client always valid, static client tokens only under
  the migration flags.
- The dashboard UI container moved behind the `legacy-ui` compose profile;
  the FastAPI dashboard at `/` replaces it after cutover.
- CORS to knowledgebase.itscatalyst.com removed (same-origin now).
- CLI `status`/`tunnel status` prefer KB_PUBLIC_BASE_URL.

### Migration notes
- Deploy: fill `KB_PUBLIC_BASE_URL`, `KB_MCP_RESOURCE` (optional, derived),
  `KB_SUPABASE_URL`, `KB_SUPABASE_ANON_KEY`, `KB_OWNER_SUPABASE_SUBJECT`
  in `.env`. See .env.example.
- Supabase hosted Auth settings (Site URL + redirect
  `https://kb.<domain>/auth/callback`) are owner actions this release:
  the sb_secret key cannot write Management-API settings (needs an sbp_
  access token). Everything else is code-side.
- Old static client tokens keep working only while
  `KB_ALLOW_LEGACY_CLIENT_TOKENS=true`; reconnect clients via the URL flow
  and remove the flag.
