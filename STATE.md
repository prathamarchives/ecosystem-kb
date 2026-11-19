# STATE.md — current implementation state

Last updated: 2026-11-19 (feat/supermemory-style-mcp-oauth, on top of main@5356824)

## Baseline

main@5356824 carried the host-side Supabase OAuth proxy work; CI was RED
on main (trailing-slash regression: tests/integration/test_host_cli.py
:190/:205 expected `.../mcp`, the CLI and app emitted/served `/mcp/`).
Baseline in this sandbox at branch start: 155 passed / 2 failed / 2
skipped, ruff clean.

## This branch: supermemory-style MCP plug-and-play

Runbook: upload/ECOSYSTEM_KB_SUPERMEMORY_STYLE_EXECUTION_RUNBOOK (1049
lines). Branch commit 8ff3640. In-sandbox verification: **202 passed /
0 failed / 2 skipped**, ruff clean, plus a 40-check end-to-end OAuth
protocol smoke (scripts/oauth_smoke.py) with the app lifespan running.

### What is implemented (runbook Steps 5-10, 12)

1. **Canonical config contract (Step 5).** KB_PUBLIC_BASE_URL (origin
   only), KB_MCP_RESOURCE (derived <base>/mcp), KB_OWNER_SUPABASE_SUBJECT,
   KB_ALLOW_STATIC_TOKENS / KB_ALLOW_LEGACY_CLIENT_TOKENS (default false),
   production startup validation (HTTPS, resource binding, static-token
   refusal). KB_PUBLIC_URL is a one-release alias with a deprecation log.
2. **First-party OAuth 2.1 AS (Steps 5, 7).** Authorization Code + PKCE
   S256 only; public clients (token_endpoint_auth_method=none); exact
   redirect-URI matching; resource binding checked at authorize, token,
   refresh and every /mcp request; opaque tokens stored SHA-256-hashed
   (access 1h, refresh 30d, rotating); refresh replay revokes the family;
   RFC 7009 revocation; DCR endpoint + CIMD resolution with an
   SSRF-hardened HTTPS fetcher (private/loopback/multicast refused, no
   redirects, size cap, JSON schema validation); RFC 9728 protected-
   resource + RFC 8414 AS metadata, all same-origin, issuer = the public
   origin; consent screen with stored-consent skip; pending
   authorizations survive the email-link login round trip.
   The Supabase `/oauth/*` proxy is REMOVED — Ecosystem KB is the issuer.
3. **Owner login (Step 6).** Supabase magic link (PKCE,
   shouldCreateUser=false) verified server-side (JWKS + pinned algs +
   issuer + audience + exp + owner sub + authoritative admin user check
   when the secret key is configured) exchanged for a first-party
   kb_session cookie (HttpOnly, Secure, SameSite=Lax, 7-day, hashed at
   rest, independently revocable). Routes: /auth/config, /auth/session,
   /auth/me, /auth/logout, /auth/callback.
   Supabase user JWTs are NO LONGER accepted as MCP/REST bearer tokens.
4. **One principal resolver (Step 8).** AuthService.authenticate_token:
   KB OAuth first (a formerly-valid-but-revoked OAuth token never falls
   through to weaker paths), the loopback kb-admin client row always
   valid (host CLI), static client tokens only under the migration flags.
   The MCP ASGI gate calls the SAME resolver — MCP and REST accept
   identical credentials; cookies never work on /mcp.
5. **One-origin dashboard (Step 9).** FastAPI serves the whole owner UI
   at /: Knowledge (search + capture with typed extraction JSON),
   Clients (registration mode, consent, active tokens, revoke), Connect
   (canonical URL + JSON snippet, no token field), Status (control plane,
   graph, queue). CSP + security headers; untrusted values rendered with
   textContent only; owner-session data endpoints with Origin checking.
   The legacy ui container is behind the `legacy-ui` compose profile
   pending owner-confirmed cutover.
6. **Typed capture (Step 10).** kb_capture accepts an optional extraction
   object (entities/edges against the controlled ontology); validated at
   submit AND by the worker; invalid extraction rejected with specific
   ontology errors; missing extraction under the fake provider reports
   `capture saved; extraction unavailable`. The runbook's `EXTRACTION:`
   marker parser does NOT exist in the repo (verified) — nothing to
   deprecate; typed capture is the only path.
7. **Canonical /mcp (the CI-red root cause).** FastAPI Mount +
   redirect_slashes rewrote bare /mcp to /mcp/ before any mounted app
   ran (verified: the middleware never saw the request). The MCP sub-app
   is now owned by raw ASGI routes (/mcp + /mcp/ alias) that reproduce
   the Mount root_path semantics without redirecting. kb connect and
   kb doctor emit/probe the no-slash URL.
8. **Tests (Step 12).** tests/contract/test_oauth_as.py (28 checks:
   metadata, DCR negatives, authorize/consent/resume, PKCE negatives,
   code reuse, rotation + replay + family revocation, cross-client
   isolation, cross-origin refusal, session lifecycle) and
   tests/contract/test_extraction.py (17 checks: ontology validation
   matrix, honest statuses, integration through the service layer).
   Migration count tests updated for 003.

### Database

Migration 003 (additive only): kb_sessions, oauth_clients,
oauth_pending_authorizations, oauth_authorization_codes, oauth_tokens,
oauth_consents, oauth_registration_tickets. Postgres + SQLite dialects;
ORM parity asserted by the existing postgres-integration job.

### Owner actions still required (cannot be done from this sandbox)

1. Supabase hosted Auth settings: Site URL `https://kb.itscatalyst.com`,
   redirect `https://kb.itscatalyst.com/auth/callback`, email provider
   on, sign-ups disabled. The sb_secret key authenticates the Auth-admin
   API (users) but NOT the Management API (needs an sbp_ access token);
   applying these is a 2-minute dashboard task.
2. Fill .env on the host: KB_PUBLIC_BASE_URL, KB_SUPABASE_URL,
   KB_SUPABASE_ANON_KEY, KB_OWNER_SUPABASE_SUBJECT
   (=ad0fa277-f001-433f-883d-87e4dab606a3), keep CF_TUNNEL_TOKEN.
3. Deploy: git pull; docker compose build api worker; up -d (no -v);
   verify /health /ready dashboard /mcp 401 metadata.
4. Reconnect one real OAuth-capable client through the URL flow
   (full browser flow per runbook Step 13), then the rest; then remove
   KB_ALLOW_LEGACY_CLIENT_TOKENS if it was enabled.
5. After public verification: remove the knowledgebase.itscatalyst.com
   tunnel route + DNS record and the legacy-ui profile container.

### Known limitations

- Real-client browser-flow test (runbook Step 13) not yet executed —
  needs the deployed host + a real MCP client; the 40-check smoke covers
  the full protocol server-side.
- Supabase hosted settings are owner actions (above).
- The dashboard callback page redirects to / and relies on the login
  card's Supabase JS flow; a pinned @supabase/supabase-js dependency
  should replace any CDN include before cutover (runbook Step 6 note).
