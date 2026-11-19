# Connecting MCP clients to ecosystem-kb

One endpoint serves every client: `https://kb.<your-domain>/mcp`
(Streamable HTTP). Each client gets its own bearer token — create one with
`kb connect <target>` or `kb client create <name> --role <role>`. Tokens are
shown once and stored only as hashes.

Roles map to scopes: `reader` (kb:read — search/context/provenance),
`contributor` (kb:read+write — also capture), `reviewer`, `maintainer`,
`administrator`. Start with `contributor` for coding agents.

## Claude Code

```bash
claude mcp add --transport http ecosystem-kb \
  https://kb.<your-domain>/mcp \
  --header "Authorization: Bearer <CLIENT_TOKEN>"
```

Verify the exact flags against your `claude --version`; the shape above is
the documented remote-HTTP MCP form. Add the usage instructions from
[docs/clients/AGENT-INSTRUCTIONS.md](clients/AGENT-INSTRUCTIONS.md) to your
global rules so the agent retrieves context before work and captures durable
outcomes.

## Cursor

`~/.cursor/mcp.json` (or project-level `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "ecosystem-kb": {
      "url": "https://kb.<your-domain>/mcp",
      "headers": {
        "Authorization": "Bearer <CLIENT_TOKEN>"
      }
    }
  }
}
```

## Generic Streamable HTTP MCP client

- Endpoint: `https://kb.<your-domain>/mcp`
- Header: `Authorization: Bearer <CLIENT_TOKEN>`
- Protocol: MCP Streamable HTTP (JSON-RPC over HTTP POST; SSE stream responses)
- Tools: `kb_context`, `kb_capture`, `kb_search`, `kb_get_entity`,
  `kb_get_context`, `kb_get_provenance`, `kb_propose_update`,
  `kb_get_maintenance_report`, `kb_submit_event`

## MCP Inspector (local validation)

```bash
npx @modelcontextprotocol/inspector
# Connect URL: http://127.0.0.1:8000/mcp  (local port, while `kb up` runs)
# Add header: Authorization = Bearer <CLIENT_TOKEN>
```

## Notes

- Revocation: `kb client revoke <name>` — other clients keep working.
- Rotation: `kb client rotate <name>` — new token printed once, old dies
  immediately.
- A client that supports only local stdio would need a bridge; none is
  implemented in this release (plan §E1).
- ChatGPT custom-MCP availability and write capabilities depend on the
  product plan; validate separately before relying on them.
