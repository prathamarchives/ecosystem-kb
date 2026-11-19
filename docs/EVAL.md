# Extraction evaluation — how to run and record

The labelled set lives in `tests/fixtures/extraction_eval_cases.py`
(30 cases: single/multi-entity, aliases, ambiguity, contradictions,
revisions, outdated facts, injection text, secrets, invalid relations).
The harness is `tests/integration/test_extraction_eval.py`.

## What the fake-provider run proves (CI)

Plumbing only: labelled payloads flow through the REAL pipeline
(marker extraction → ontology gate → graph write/quarantine → policy
rejection for secrets). It does NOT measure extraction judgment — the
deterministic fake reproduces whatever the marker says. Two invariants are
asserted because they are laws, not quality:

- LAW-010: credential-shaped content is policy-rejected before extraction.
- LAW-006: non-canonical relations are never written (quarantined or
  dropped via the strict edge-type gate).

## Running a real baseline

```bash
# .env (untracked): real provider
KB_LLM_PROVIDER=openai        # or another supported provider
KB_LLM_MODEL=<model-id>
KB_LLM_API_KEY=<key>
KB_EMBEDDER_MODEL=text-embedding-3-small

# then, with the repo venv active:
pytest tests/integration/test_extraction_eval.py -s
```

The harness dispatches on the provider the same way production does
(`build_llm_client`), so a real run exercises the real extraction prompts.
Record in the table below: provider, model, date, and the printed totals
(entities / relations / quarantined per category).

## Known limitation (recorded 2026-11-19)

graphiti 0.31.2 `add_episode_bulk` returns nodes only for entities that
participate in admitted edges: edge-less entity lists write zero nodes
(probe: same entity with an edge → 1 node). Entity-recall scoring against
this bulk path must account for it, or seed a companion canonical edge.

## Baseline records

| Date | Provider | Model | Entities | Relations | Quarantined | Notes |
|---|---|---|---|---|---|---|
| 2026-11-19 | fake | marker | 4 | 1 | 1 | plumbing check only; quality unmeasured |
