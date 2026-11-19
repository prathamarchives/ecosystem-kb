# Connecting MCP clients to ecosystem-kb

One endpoint serves every client: `https://kb.<your-domain>/mcp`
(Streamable HTTP, OAuth 2.1). **No API key, no bearer token to copy.**
Add the URL to any OAuth-capable MCP client; the client opens the browser,
you sign in as the owner and approve the client once, and the client manages
its own credentials from there.

```json
{
  "mcpServers": {
    "ecosystem-kb": {
      "url": "https://kb.<your-domain>/mcp"
    }
  }
}
```

## How the connection works

1. The client calls the endpoint unauthenticated and receives a `401` with
   Protected Resource Metadata (`/.well-known/oauth-protected-resource/mcp`).
2. It discovers the authorization server (`/.well-known/oauth-authorization-server`)
   — same origin, first-party.
3. It registers itself (client-id metadata document or dynamic registration).
4. The browser opens the KB login page; the owner signs in with the Supabase
   email magic link (the one configured owner only).
5. The owner approves the client and its scopes (`kb:read`, `kb:write`,
   optionally `offline_access` for refresh tokens).
6. The client completes the PKCE token exchange and connects. Reconnecting
   with unchanged scopes does not prompt again.

Scopes: `kb:read` (search/context/provenance), `kb:write` (capture +
submit), `offline_access` (stay signed in). Clients never receive admin,
review, or maintain scopes.

## Tested clients

- **Claude Code** — remote MCP with OAuth: add the URL above; select the
  OAuth/remote connection mode when prompted.
- **Cursor** — remote MCP: paste the JSON snippet; Cursor performs the
  browser flow.
- **OpenCode** — remote MCP with OAuth discovery.
- **MCP Inspector** (local validation) — connect to
  `http://127.0.0.1:8000/mcp` while `kb up` runs; the inspector performs
  the same OAuth flow against the local instance.
- **ChatGPT** — custom MCP connectors are plan-gated (Business/Enterprise:
  read+write; Pro: read/fetch). The server flow is identical; only the
  account entitlement decides what the client may do.

A client that only accepts a static bearer token is not OAuth-compatible
and cannot use this flow. The `kb client create` static-token path is a
legacy migration bridge (disabled by default) — do not use it for new
connections.

## Managing clients

The dashboard at `https://kb.<your-domain>/` (owner login) shows every
registered client, its consent state, active token counts and last use.
Revoking a client kills its tokens immediately; other clients keep working.

## Legacy token migration

If you still hold a static client token from the pre-OAuth release: set
`KB_ALLOW_LEGACY_CLIENT_TOKENS=true` in `.env` during your migration
window, reconnect your clients through the URL flow, then remove the flag
and verify the old tokens fail.
