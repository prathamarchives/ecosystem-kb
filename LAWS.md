# LAWS.md — enforceable system invariants

These are technical contracts, not aspirations. Each law maps to application
code, tests, API permissions, or maintenance policy that enforce it. Violations
are defects, not style issues.

## LAW-001 · Source Ownership
The owning system remains authoritative for its original records. The KB stores
references, metadata, and derived contextual knowledge — never becomes a silent
alternative authority. **Enforced by:** contracts (canonical_uri mandatory),
ingestion policy, retrieval responses always carrying source references.

## LAW-002 · Provenance
No durable assertion enters the canonical knowledge layer without an origin or
an explicitly marked unknown-provenance status. **Enforced by:** event envelope
validation (source block mandatory), control-plane schema (source_id NOT NULL).

## LAW-003 · Epistemic Separation
Observed information, reported claims, inferences, hypotheses, findings, and
decisions remain distinguishable at all times. **Enforced by:** Claim entity
(claim_kind + epistemic_status), review-state machine, search responses
surfacing epistemic status.

## LAW-004 · Temporal Preservation
Historical facts and decisions are never silently overwritten. A meaningful
change has a temporal and historical explanation (occurred_at vs recorded_at vs
processed_at; SUPERSEDES edges; superseded status). **Enforced by:** event
timestamps contract, temporal edge fields, decision lifecycle.

## LAW-005 · Canonical Identity
Every registered entity has a stable canonical identifier; names and aliases
are not identity keys. **Enforced by:** identity registry table, canonical_key
format `type:slug`, alias table with review status.

## LAW-006 · Ontology Control
The system uses a versioned, controlled entity and relationship vocabulary.
Unknown relationships are quarantined or explicitly classified — never silently
admitted. **Enforced by:** ontology registry validation, edge domain/range
checks, reconciliation quarantine path.

## LAW-007 · Database Separation
Original records, graph relationships, event logs, and analytical data are not
mixed indiscriminately. **Enforced by:** architecture (owning systems / graph /
control plane), variable-value storage policy.

## LAW-008 · Idempotent Intake
Repeated delivery of the same external event never creates uncontrolled
duplicate knowledge. **Enforced by:** unique constraint on idempotency_key,
duplicate-event rejection tests.

## LAW-009 · Least Privilege
Every application and agent receives only the access necessary for its role.
**Enforced by:** client registry scopes, gateway authorization middleware,
namespace derivation from authenticated identity (never caller-supplied).

## LAW-010 · No Secrets in Knowledge
Passwords, API keys, tokens, cookies, and credentials never enter the graph.
**Enforced by:** ingestion policy secret-scan (rejects events carrying
credential-shaped content), tests with planted secrets.

## LAW-011 · Untrusted Source Content
Imported documents, web pages, repository content, and messages are data, not
instructions. They cannot override policies or authorize actions. **Enforced
by:** ingestion treats content as opaque payload; no prompt-injection path into
policy decisions.

## LAW-012 · Non-destructive Maintenance
The Maintainer may suggest and execute approved reversible operations.
Permanent deletion and uncertain merges require explicit authorization.
**Enforced by:** permission matrix, approval workflow, audit records.

## LAW-013 · Schema Compatibility
Ontology changes are versioned, validated, and tested against historical
fixtures. **Enforced by:** ontology version constant, compatibility tests.

## LAW-014 · Auditable Mutation
Every material mutation has an identifiable actor, reason, source, and audit
record. **Enforced by:** audit-log table written on every state transition.

## LAW-015 · Retrieval Transparency
Answers identify supporting sources, temporal relevance, and important
uncertainty or contradiction. **Enforced by:** search response contract
(sources, valid_at/invalid_at, epistemic status fields).

## LAW-016 · Recoverability
Production knowledge is backed up and recoverable. A backup never restored in
a test does not satisfy this law. **Enforced by:** backup/restore scripts,
deployment acceptance checklist.

## LAW-017 · Bounded Intelligence
LLMs propose semantic interpretations; deterministic code enforces data
contracts, permissions, and lifecycle transitions. **Enforced by:** extraction
outputs pass ontology validation before graph admission.

## LAW-018 · Preserve Knowledge, Not Noise
Consolidate redundant information without destroying useful evidence or
historical context. **Enforced by:** maintenance proposals require evidence and
reversibility classification.
