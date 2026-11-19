# ecosystem-kb

Shared knowledge infrastructure for the ecosystem: the common source-of-truth
context layer that every application, agent, and research environment
contributes to and retrieves from.

**Stack:** Graphiti Core (temporal knowledge engine, pinned dependency) ·
FalkorDB (graph persistence) · PostgreSQL (deterministic control plane) ·
FastAPI (authenticated REST) · FastMCP (restricted MCP tools) — one service
layer under both.

Read first: [INTENT-LEDGER.md](INTENT-LEDGER.md) (why),
[LAWS.md](LAWS.md) (invariants), [DECISIONS.md](DECISIONS.md) (choices),
[STATE.md](STATE.md) (status), [docs/spec/EKIS-SPEC-001.md](docs/spec/EKIS-SPEC-001.md)
(full system-engineering spec).

## What it does

- Accepts durable **knowledge events** (`kb.event.v1` envelope) from
  authenticated systems, with idempotency and provenance.
- Extracts typed entities and relationships into a **versioned ontology**
  (systems, components, interfaces, variables, decisions, claims, experiments,
  findings…), where extraction output is a *proposal* until ontology
  validation passes.
- Preserves **temporal history**: supersession instead of overwrite,
  occurred_at / recorded_at / processed_at kept separate.
- Answers **hybrid searches** with provenance, epistemic status, and
  contradictions surfaced.
- Runs a **bounded Maintainer** that audits graph health and proposes
  changes under an approval gate.

## Quickstart (plug-and-play local stack)

```bash
git clone https://github.com/prathamarchives/ecosystem-kb && cd ecosystem-kb
pip install -e .                      # or uv pip install -e .
kb setup                              # creates .env (0600) with generated secrets
# add KB_LLM_API_KEY etc. to .env for full ingestion (read-only without)
kb up                                 # postgres + falkordb + api + worker (+ tunnel if CF_TUNNEL_TOKEN)
kb doctor                             # deep diagnostics (clean local-only install exits 0; --strict for provisioned checks)
kb connect claude-code                # client credential + connection snippet
```

Remote access goes through a named Cloudflare Tunnel:
`https://kb.<your-domain>/mcp` — see [docs/TUNNEL-SETUP.md](docs/TUNNEL-SETUP.md).
Connecting clients: [docs/clients/CONNECT.md](docs/clients/CONNECT.md).
Day-to-day: `kb status`, `kb backup` / `kb restore`, `kb client list`.
Host CLI architecture and security boundary (ADR-019): [docs/HOST-CLI-ARCHITECTURE.md](docs/HOST-CLI-ARCHITECTURE.md).
Owner-host acceptance checks (Docker-level): `bash scripts/host_acceptance.sh`.
Acceptance criteria: [docs/PLUG-AND-PLAY-ACCEPTANCE.md](docs/PLUG-AND-PLAY-ACCEPTANCE.md).

## Quickstart (developer, embedded graph)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,embedded]"
pytest                                     # unit + embedded integration
uvicorn ecosystem_kb.api.app:create_app --factory --reload   # REST + MCP at :8000
```

The same container images ship to production (see `deploy/`); SQLite is the
dev control plane, PostgreSQL the production one.

## Repository map

| Path | Role |
|---|---|
| `ontology/` | Entity/edge/variable Pydantic models, domain-range registry, versions |
| `contracts/` | kb.event.v1 envelope, sources, provenance, clients, search/maintenance DTOs |
| `src/ecosystem_kb/core/` | Config, errors, identity, policies, lifecycle state machines |
| `src/ecosystem_kb/adapters/` | Graphiti adapter (ingest/search/reconcile/health) + control-plane repositories |
| `src/ecosystem_kb/services/` | Event, search, identity, review, maintenance services |
| `src/ecosystem_kb/api/` | FastAPI app, auth, routes |
| `src/ecosystem_kb/mcp/` | FastMCP server with 7 restricted tools |
| `src/ecosystem_kb/workers/` | Ingestion + maintenance workers (job leases) |
| `maintenance/` | Deterministic audit implementations |
| `tests/` | unit / contract / integration (embedded FalkorDB) / security |
| `docs/spec/` | EKIS-SPEC-001 (the full V-model engineering spec) |

## Verifying claims

Nothing here is "done" because code exists. Each release gate has tests:
`pytest -m embedded` runs the real-engine vertical slice; security tests plant
secrets, forged namespaces, and privilege escalations; maintenance tests seed
duplicates and contradictions and check the Maintainer finds them.
