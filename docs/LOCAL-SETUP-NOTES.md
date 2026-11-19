# Local setup notes (owner-host, 2026-11-19)

These are the deviations required to run `main@73901d8` (repair PR #2) on this
owner host. They are **local fixes, not yet upstream**. None change contracts,
ontology, or laws.

## Environment
- No Docker daemon; Docker CLI 27.3.1 + Compose v2.29.7 talk to rootless Podman
  (`podman system service` on `/run/user/1000/podman/podman.sock`). The
  `~/.local/bin/docker` wrapper auto-starts the socket.
- Python 3.12 via `uv`; `pip install -e ".[dev,embedded]"`.
- `cloudflared` binary: `~/Applications/squashfs-root/resources/cloudflared/cloudflared`.

## Code/config fixes applied
1. `core/config.py` — added `llm_base_url`, `embedding_dim`, `reranker` settings.
2. `adapters/graphiti/client.py` — LLMConfig `base_url`; `OpenAIEmbedderConfig`
   (the previous `EmbedderConfig` has no `api_key`/`base_url` and crashes on
   the real-LLM path); LLMConfig for reranker; deterministic reranker when
   `KB_RERANKER=fake`; deterministic embedder uses `embedding_dim`.
3. `deploy/compose.local.yaml` —
   - FalkorDB image tag `4.22.1` -> `v4.22.1` (old tag does not exist).
   - `REDIS_ARGS: --requirepass ${FALKORDB_PASSWORD}` (the image ignores
     `FALKORDB_PASSWORD`; without this the API's AUTH fails at startup).
   - volume mount `/data` -> `/var/lib/falkordb/data` (the image's `run.sh`
     uses `FALKORDB_DATA_PATH=/var/lib/falkordb/data`; with the old mount the
     graph was lost on every restart).
4. `deploy/.env` -> `../.env` symlink so Compose interpolation sees the values
   (the CLI runs compose from `deploy/`).
5. `cli/main.py` — `backup` parser gained `--output`; FalkorDB RDB paths
   corrected to `/var/lib/falkordb/data/dump.rdb` (backup + restore); backup
   checksum verification uses the right manifest component key (`postgres`).
6. `.env` — `KB_API_TOKENS=` (required empty in production), `KB_LLM_BASE_URL`,
   `KB_EMBEDDING_DIM=2048`, `KB_RERANKER=fake`, `SEMAPHORE_LIMIT=2`.

## Remote access (COMPLETE)
- Cloudflare zone `itscatalyst.com` is active with Cloudflare nameservers
  (`pablo`/`savanna`); the local resolver was stale, `dig @1.1.1.1` confirms.
- Named tunnel `ecosystem-kb-local` (id `2dd8e40e-e2eb-4ebe-8885-5a0e12d8be93`)
  created; ingress `kb.itscatalyst.com` -> `http://api:8000`, catch-all 404.
- DNS record `kb.itscatalyst.com` (CNAME -> tunnel, Proxied) created via the
  Cloudflare dashboard (the scoped API token lacks `DNS: Edit`).
- `CF_TUNNEL_TOKEN` + `KB_PUBLIC_URL=https://kb.itscatalyst.com` in `.env`;
  cloudflared runs via `docker compose -f deploy/compose.local.yaml --profile
  tunnel up -d --wait`. `kb status` shows Cloudflared running and public URL.
- **Remote MCP verified**: `https://kb.itscatalyst.com/mcp/` with a bearer token
  lists 9 tools and returns facts. NOTE: the trailing slash is REQUIRED —
  without it FastAPI redirects to `http://` and clients refuse the downgrade.
  `kb connect` now emits `/mcp/`.

## OAuth + UI layer (2026-11-19)
- Supabase project `https://zixvpbshbuqtdrznbcqj.supabase.co` (URL + anon key
  from secure cards; service-role key intentionally NOT used). Supabase OIDC
  discovery works at `/auth/v1/.well-known/openid-configuration`; JWKS ES256.
- Backend: `api/supabase_auth.py` verifies Supabase JWTs via JWKS; `AuthService`
  tries static client tokens FIRST (fallback) then Supabase JWTs. New settings:
  `KB_SUPABASE_URL`, `KB_SUPABASE_ANON_KEY`, `KB_PUBLIC_BASE_URL`.
- OAuth discovery endpoints published: `/.well-known/oauth-protected-resource`
  (authorization_servers = Supabase issuer) and `/.well-known/oauth-authorization-server`;
  401s carry `WWW-Authenticate: Bearer resource_metadata=...`. CORS allows the UI origin.
- UI: `deploy/ui/` (static SPA served on :3000). At
  `https://knowledgebase.itscatalyst.com` — magic-link login via Supabase JS,
  consent screen at `/oauth/callback` using `supabase.auth.oauth.*` methods,
  search + capture (capture can carry an agent-authored `EXTRACTION:` payload).
- Tunnel ingress now routes `knowledgebase.itscatalyst.com` -> `http://ui:3000`
  and `kb.itscatalyst.com` -> `http://api:8000`; DNS CNAME for both hostnames
  added via dashboard. Verified: UI loads, /config.js serves Supabase config,
  OAuth metadata reachable, static-token MCP still works remotely.
- **User action still required in Supabase dashboard**: enable OAuth Server +
  dynamic client registration; Site URL `https://knowledgebase.itscatalyst.com`;
  Authorization Path `/oauth/callback`; enable Email magic-link provider; add
  redirect URL `https://knowledgebase.itscatalyst.com/**`.

## Inference state (temporary)
Both OpenRouter keys hit the free tier's 50 requests/day cap. Per the owner's
decision, the KB now uses **the connected client's LLM instead of a separate
inference key**: `KB_LLM_PROVIDER=fake` is set, and the connected agent acts as
the extractor by authoring payloads with the marker protocol
(`EXTRACTION: {...}`) using canonical integer entity type IDs (Project=1,
System=2, ...) and ontology relation names. This exercises the real pipeline
(ontology gate -> graph -> provenance -> search) with zero external calls.

Workflow while in this mode: capture normally with `kb_capture`; when knowledge
needs extraction, have the connected agent supply an `EXTRACTION:` payload in
the captured content (or re-capture with one). Marker schema:
`{"entities":[{"name":...,"entity_type_id":<int>,"episode_indices":[0]}],
"edges":[{"source_entity_name":...,"target_entity_name":...,"relation_type":
<canonical name>,"fact":...}]}`. Unknown/edge-less entities are dropped by the
ontology gate and `add_episode_bulk`.

To restore real extraction later: set `KB_LLM_PROVIDER=openai`, put a funded
key in `KB_LLM_API_KEY`, choose a model that supports the Responses API with
JSON-schema output, and keep `SEMAPHORE_LIMIT=2` (Graphiti's default of 20
concurrent calls stalls OpenRouter's free tier).

## Verified
- `kb status` / `kb doctor` healthy (0 failures; tunnel warning in local-only).
- `kb backup` verified and published.
- MCP over loopback HTTP: 9 tools; capture -> graph write -> `kb_search` /
  `kb_context` with provenance; facts survive `kb restart`.
- Connection wizard: default client-name derivation, valid Cursor JSON, no
  literal f-strings, credential shown once; `kb disconnect` revokes.
