# AGENTS.md — operating instructions for coding agents

This repository is the ecosystem knowledge infrastructure. It is the shared
memory of every system in the ecosystem; treat correctness and provenance as
higher priorities than feature speed.

## Architecture in one screen

```
Sources (Arivoo, repos, agents)          Readers (ChatGPT, coding agents, apps)
        │ outbox / POST /v1/events                 │ MCP /v1 or REST /v1/search
        ▼                                          ▼
   KB Gateway (FastAPI + FastMCP, bearer auth, scopes, server-side namespace)
        │                                          │
        ▼                                          ▼
 PostgreSQL control plane ──► Ingestion worker ──► Graphiti Core (pinned dep)
 (sources, events, jobs,      (leases, retries,    ──► FalkorDB (graph store)
  identity, aliases,           ontology gate)
  review queue, audit,
  clients)
```

Laws (LAWS.md) are enforced in code: contracts validate provenance; policies
reject secrets; the ontology registry quarantines unknown edges; the ledger
deduplicates events; the gateway derives namespaces from authenticated
identity only.

## Ground rules

1. **Read LAWS.md and DECISIONS.md before changing contracts, policies, or
   the ontology.** If a change contradicts a law, the change is wrong or the
   law needs an explicit, recorded revision — never a silent break.
2. **Contracts are frozen surface.** `kb.event.v1`, REST routes, MCP tool
   schemas, and the 8 control-plane tables change only through versioned
   additions (schema_version fields exist for this).
3. **Determinism stays deterministic.** LLM-shaped data never flows into
   authz, lifecycle transitions, or schema validation. Extraction output is a
   proposal until the ontology registry validates it.
4. **No new dependencies without a decision record.** Especially no agent
   frameworks, no second vector DB, no graph engine swap.
5. **Every mutation writes an audit record** with actor, action, reason.
   Maintenance apply-mode is gated behind kb:maintain + approval records.
6. **Tests are the release gates.** `pytest` must pass before any push.
   Embedded-FalkorDB tests (`-m embedded`) exercise the real driver path;
   do not delete or skip them to make CI green.

## Development commands

```bash
pip install -e ".[dev,embedded]"
pytest                     # everything
pytest -m "not embedded"   # fast unit/contract/security set
pytest tests/integration   # real-engine vertical slice (boots embedded FalkorDB)
ruff check . && ruff format --check .
kb --help                  # CLI: bootstrap, seed, graph-health, worker run
```

## Where things live

- Add an entity/relationship type → `ontology/` + fixture in
  `tests/fixtures/` + bump `ontology/versions.py` (LAW-013).
- Change the event envelope → `contracts/events.py`; never break v1 parsing.
- Add an API route → `src/ecosystem_kb/api/routes/` + contract test.
- Add an MCP tool → `src/ecosystem_kb/mcp/tools.py`; scope it; document it.
- Add an audit → `maintenance/audits/`; deterministic; fixture-seeded test.

## Prohibited shortcuts

- Ingesting content without source/provenance blocks.
- Trusting caller-supplied namespace/group_id for authorization.
- Deleting or overwriting historical rows (mark superseded instead).
- Bypassing the ledger with direct graph writes from the API layer.
- Storing variable values, secrets, or bulk content in the graph.
- Fixing a failing test by weakening its assertion.
