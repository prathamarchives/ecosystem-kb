# Cloudflare named tunnel setup (WP-C)

This documents the ONLY supported remote path: a remotely managed **named
tunnel** routing your stable hostname to the local stack. Quick Tunnels
(`trycloudflare.com`) are for smoke tests only — ephemeral URLs, no SSE.

## Prerequisites

1. A Cloudflare account.
2. A domain added to Cloudflare (nameservers pointing at Cloudflare).
   If you don't have one, this is the single external blocker: a stable
   public hostname cannot be configured until a domain is active there.
3. Permission to create tunnels (Networks → Tunnels).

References: https://developers.cloudflare.com/tunnel/get-started/ and
https://developers.cloudflare.com/tunnel/reference/tunnel-tokens/

## One-time dashboard setup

1. Cloudflare Dashboard → Networking → Tunnels → **Create tunnel**.
2. Name it `ecosystem-kb-local`. Choose **Docker** as the connector
   environment and copy the tunnel token shown.
3. Add a Public Hostname (Published Application):
   - Hostname: `kb.<your-domain>`
   - Service: `http://api:8000` (the compose service name — cloudflared
     runs on the same private Docker network and resolves it).
4. Save the route.

## Wiring the token (never commit it)

Put the token directly into your local untracked `.env`:

```
CF_TUNNEL_TOKEN=<paste the token here>
KB_PUBLIC_URL=https://kb.<your-domain>
```

Then:

```bash
kb up          # activates the tunnel profile automatically when the token exists
kb tunnel status
```

The `cloudflared` service (pinned `cloudflare/cloudflared:2025.11.1`) runs
behind the `tunnel` compose profile; `kb up` includes it only when
`CF_TUNNEL_TOKEN` is set. Do not paste the token into chat, issues, or any
committed file.

## What the tunnel does and does not do

- Cloudflare terminates public TLS at the edge; traffic reaches the API over
  the private Docker network. No inbound ports are opened on your machine.
- The tunnel is TRANSPORT, not authorization: the KB's own bearer auth is
  enforced on every request (including `/mcp` protocol initialization).
- Only the API is routed. PostgreSQL and FalkorDB have no published ports
  and are never reachable through the tunnel.
- Availability follows this machine: powered on, online, Docker and
  cloudflared running. Sleep/shutdown makes the endpoint unreachable
  without any data impact; the hostname recovers when the stack returns.

## Verification (AT-02)

```bash
kb tunnel status                    # container up + recent logs
curl -s https://kb.<your-domain>/health
curl -s -o /dev/null -w "%{http_code}" \
  -X POST https://kb.<your-domain>/mcp/ \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}'
# Expect 401 (no bearer token) — the endpoint is reachable AND gated.
```

Then connect a real client per [docs/clients/CONNECT.md](clients/CONNECT.md).

## Later VM migration (not in this milestone)

The hostname stays; only the connector moves: deploy the same compose stack
on the VM, restore backups into its volumes, run `cloudflared` there, and
point the tunnel service at the VM's `http://api:8000`. Client configs and
MCP tool semantics do not change.
