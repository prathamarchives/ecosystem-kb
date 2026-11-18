# INTENT-LEDGER.md — enduring direction of the system

This file records WHY the system exists and WHERE it is going. It changes
slowly. Implementation status lives in STATE.md; specific choices live in
DECISIONS.md.

## Mission

One shared knowledge infrastructure that acts as the source of contextual truth
for every application, agent, repository, CLI, research environment and
workflow in the ecosystem: Arivoo, the future economics/finance research
harness, coding agents, and whatever comes next.

## The compounding thesis

A knowledge base does not compound because it stores more information. It
compounds when new information improves, connects, corrects, or extends what
already exists: capture → classify → extract → resolve identities → reconcile
→ index → retrieve → new evidence/decisions/findings → update and maintain.

The system must preserve where information came from, how it relates, how it
changes over time, what is established versus inferred, which decisions
supersede earlier ones, and what new information changes our understanding.

## Non-negotiable direction

1. **Shared, not fragmented.** One production knowledge network
   (ecosystem-prod). Applications connect to the same graph; the source
   registry distinguishes ownership. Separate namespaces only for real
   data-isolation requirements.
2. **Own the knowledge architecture, not the graph engine.** Upstream
   Graphiti Core as a pinned dependency. Our IP is the ontology, identity
   model, contracts, laws, lifecycle, and maintenance governance around it.
3. **Deterministic core, bounded intelligence.** LLMs propose; deterministic
   code validates, authorizes, and commits. Never the reverse.
4. **Machine-readable systems understanding.** Systems, components,
   interfaces, variables, decisions, experiments, findings — as typed,
   queryable structures, not prose.
5. **First system of record, then integration.** Arivoo integrates through
   its outbox once records and revision contracts are stable. The economics
   harness begins after the KB is usable (one vertical slice first:
   question → math spec → deterministic experiment → visualization →
   evaluation → saved result with provenance).

## Success conditions

- A system can contribute durable knowledge through a documented contract.
- Another system can retrieve that knowledge with provenance and temporal
  context, without knowing which application created it.
- Decisions evolve visibly (supersession, not overwrite).
- The graph stays healthy under maintenance (duplicates found, contradictions
  surfaced, nothing silently destroyed).
- Knowledge survives machine changes and deployments (remote-first, backed up,
  restorable).
