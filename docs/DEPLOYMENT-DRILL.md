# Deployment & restore drill

`scripts/deploy_drill.py` is the executable Gate 5 acceptance procedure. It
answers "would this system survive production?" with evidence, not claims.

## What it boots (all in production mode, no static dev tokens)
| Component | Implementation |
|---|---|
| PostgreSQL | real server via `pgserver` (same engine the compose stack runs) |
| FalkorDB | persistent redis-server + falkordb module, private port |
| API | uvicorn subprocess, migrations run at startup (not create_all) |
| Worker | `kb worker` subprocess with lease/heartbeat/backoff |
| TLS | Caddy reverse proxy with internal CA, unprivileged ports |

## What it proves (27 checks)
1. Production config boots and refuses weak tokens.
2. HTTPS works end to end (health, submit, search, provenance, MCP routes).
3. An authed producer submits; duplicate replays deduplicate; unauthenticated
   is 401; wrong-scope is 403; namespace escape is 403.
4. The worker processes the event; a second client retrieves the fact WITH
   provenance (source uri, revision, event id, ontology version) and can
   trace the full chain.
5. REAL backup: `pg_dump` + sha256, FalkorDB `SAVE` + RDB copy, manifest.
6. REAL restore: fresh PostgreSQL + fresh FalkorDB; consistency checks
   (ledger rows, provenance links, completed status, graph facts, canonical
   relation names).

## Storage-model note
Graphiti's FalkorDB driver writes every entity edge with physical type
`RELATES_TO`; the canonical relation (e.g. `DEPENDS_ON`) lives in the edge's
`name` property. The drill's verification queries `e.name`. Any raw Cypher
consumer must do the same.

## Usage
```bash
python scripts/deploy_drill.py          # run and clean up
python scripts/deploy_drill.py --keep   # keep the workspace for inspection
```
Run it unchanged against the VM (it binds localhost ports; point the env at
remote stores for the real deployment).
