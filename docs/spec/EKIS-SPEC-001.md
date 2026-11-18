# ECOSYSTEM KNOWLEDGE INFRASTRUCTURE
## System Engineering Specification & V-Model Execution Plan

**Document ID:** EKIS-SPEC-001  
**Version:** 0.1  
**Date:** 18 November 2026  
**Status:** Proposed baseline  
**Architecture:** Graphiti Core + FalkorDB + PostgreSQL + Custom Knowledge Gateway  
**Implementation approach:** Contract-first, local development, remote production  
**Engineering model:** Systems Engineering V-Model

---

# PART I. SYSTEM DEFINITION

## 1. Mission and purpose

Build a persistent, continuously evolving, cross-system knowledge infrastructure that acts as the shared source of contextual truth for every application, agent, repository, CLI, research environment and workflow in the ecosystem.

The system must do more than store information.

It must preserve where information came from, how it relates to other information, how it changes over time, what is established versus inferred, which decisions supersede earlier decisions, and what new information changes our understanding.

The knowledge infrastructure must be:

- Shared across independently developed systems.
- Accessible through standardized interfaces.
- Independent of any particular laptop or development environment.
- Model-provider and agent-framework agnostic.
- Persistent, recoverable and auditable.
- Extensible through explicit ontology and contract evolution.
- Capable of continuously improving its organization and usefulness.
- Governed by deterministic rules wherever possible.
- Intelligent where semantic interpretation is necessary.

### 1.1 Primary objective

Establish one common knowledge infrastructure that enables every system to contribute to and benefit from the accumulated knowledge of the entire ecosystem.

### 1.2 Secondary objectives

1. Maintain canonical identities across applications.
2. Preserve semantic and temporal relationships.
3. Retain source provenance and revision history.
4. Identify contradictions, outdated information and duplicate entities.
5. Support cross-system knowledge retrieval.
6. Maintain machine-readable representations of systems, components, interfaces, variables and decisions.
7. Support autonomous maintenance under explicit governance.
8. Allow new applications to integrate without redesigning the infrastructure.

## 2. System boundaries

We must distinguish five things that are often incorrectly treated as one.

| Concept | Responsibility |
|---|---|
| Original record | Canonical record in its owning application |
| Source registry | Identity, ownership, revision and provenance metadata |
| Knowledge graph | Relationships and contextual understanding |
| Control plane | Ingestion, authorization, state, audit and governance |
| Intelligence layer | Extraction, reconciliation, retrieval and maintenance |

### 2.1 Ownership model

| Data | Authority |
|---|---|
| Study notes and documents | Arivoo |
| Repository code and history | Git |
| System configurations | Owning system/configuration store |
| Source publications | Original publishers |
| Original datasets | Original data providers |
| Cross-system identities and references | Ecosystem KB |
| Shared contextual relationships | Graphiti knowledge graph |
| Ingestion history and maintenance audit | KB control plane |
| Knowledge policies and ontology | Ecosystem KB repository |

The KB may contain representations and summaries of external records, but it must not silently become an alternative authority for their contents.

### 2.2 Out of scope for v1

The initial system is not intended to be:

- A replacement for all application databases.
- A general-purpose document storage service.
- A universal vector database.
- A replacement for Arivoo.
- An autonomous decision-maker.
- A general-purpose personal assistant.
- An unrestricted public knowledge graph.
- A financial or economic simulation engine.

The economics research harness will integrate later.

---

# PART II. CONCEPTUAL AND DOMAIN MODEL

## 3. Fundamental knowledge objects

Before defining Graphiti entity types, we need to define the actual objects our system reasons about.

### 3.1 Source

An original origin of information.

Examples:

- Arivoo note.
- Git repository.
- README.
- Research paper.
- API documentation.
- Company filing.
- Dataset.
- Human-authored architectural decision.

A source must have a stable reference, ownership information and provenance metadata.

### 3.2 Event

A meaningful change or occurrence in an external system.

Examples:

- Note committed.
- Decision revised.
- System released.
- Variable definition changed.
- Experiment completed.
- Research finding reviewed.

Events describe what happened.

### 3.3 Episode

Graphiti's representation of an ingestion event.

An episode is not the same thing as our external application event.

Graphiti uses episodes as provenance-bearing inputs from which entities and relationships are extracted. Its documentation describes episodes as graph nodes connected to extracted entities through `MENTIONS` relationships.

Our external event is the durable message received by the KB. The Graphiti episode is the graph-engine representation created from that message.

### 3.4 Entity

A recognizable object with identity.

Examples:

- Arivoo.
- Graphiti.
- PostgreSQL.
- A project.
- A software component.
- An economic concept.
- An API.
- A dataset.

### 3.5 Relationship

A meaningful relationship between two entities.

Examples:

- Arivoo depends on PostgreSQL.
- A component implements an interface.
- A finding is derived from a dataset.
- A decision supersedes an earlier decision.

### 3.6 Claim

An individual proposition about something.

Examples:

- Graphiti supports temporal relationships.
- Our KB uses FalkorDB.
- A particular model performs better under a given condition.

Claims must not automatically become accepted facts.

### 3.7 Evidence

A source, measurement, observation or reproducible experiment that supports or contradicts a claim.

### 3.8 Decision

A recorded selection among alternatives, including rationale and consequences.

A decision is not necessarily universally correct. It records what was chosen, by whom, why and under which conditions.

### 3.9 Variable definition

A machine-readable definition of a named property, parameter, measurement or state variable.

Crucially, its definition is not the same as its current value.

### 3.10 Finding

A conclusion obtained from research, an experiment, an observation or a documented analysis.

A finding should reference its underlying methodology and evidence.

---

# PART III. ONTOLOGY SPECIFICATION

## 4. Canonical entity taxonomy

We should begin with a controlled ontology rather than attempting to model every conceivable concept.

The following 14 types form the proposed initial ontology.

| Entity type | Purpose |
|---|---|
| `Project` | An initiative or bounded area of work |
| `System` | An application, service, agent or operational system |
| `Component` | A subsystem or replaceable module |
| `Interface` | An API, CLI, MCP service, event contract or integration |
| `Artifact` | A durable generated or maintained artifact |
| `Source` | An information origin |
| `Dataset` | A structured information collection |
| `Concept` | A reusable conceptual or technical idea |
| `Requirement` | A documented system or stakeholder requirement |
| `Decision` | An explicit selected course of action |
| `Claim` | An assertion, hypothesis or proposition |
| `VariableDefinition` | A definition of a parameter or measurable quantity |
| `Experiment` | A structured investigation |
| `Finding` | A conclusion or observed result |

We should not initially create separate entity classes for every type of software, document, person, agent or research discipline.

Use controlled attributes to distinguish subtypes.

For example:

`System.system_kind`:
- application
- agent
- service
- database
- infrastructure
- workflow
- CLI
- research_environment

`Artifact.artifact_kind`:
- repository
- document
- specification
- source_code
- architecture_diagram
- configuration
- report
- schema

This keeps the ontology extensible without generating hundreds of nearly identical entity types.

## 5. Entity definitions and attributes

### 5.1 Project

Represents a larger initiative.

Attributes:

| Attribute | Type |
|---|---|
| `canonical_key` | string |
| `name` | string |
| `description` | string |
| `status` | enum |
| `project_kind` | enum |
| `created_at` | datetime |
| `owner_system` | string |

Example:

`project:ecosystem-knowledge-infrastructure`

### 5.2 System

Represents a software or operational system.

Attributes:

- `canonical_key`
- `name`
- `system_kind`
- `description`
- `lifecycle_status`
- `current_version`
- `repository_uri`
- `environment`
- `owner_project`

Example:

`system:arivoo`

### 5.3 Component

Represents an independently identifiable subsystem.

Attributes:

- `canonical_key`
- `name`
- `component_kind`
- `description`
- `lifecycle_status`
- `version`
- `owner_system`

Example:

`component:arivoo/document-ingestion`

### 5.4 Interface

Represents a system boundary or integration contract.

Attributes:

- `canonical_key`
- `name`
- `interface_kind`
- `protocol`
- `version`
- `description`
- `contract_uri`
- `owner_system`

Interface kinds:

- REST
- MCP
- CLI
- event
- database
- file
- webhook
- internal API

### 5.5 Artifact

Represents an inspectable object.

Attributes:

- `canonical_key`
- `name`
- `artifact_kind`
- `uri`
- `content_hash`
- `revision`
- `created_at`
- `owner_system`

Do not put full binary artifacts inside Graphiti. Reference their canonical location.

### 5.6 Source

Represents provenance for information.

Attributes:

- `canonical_key`
- `source_kind`
- `uri`
- `publisher`
- `author`
- `published_at`
- `retrieved_at`
- `revision`
- `content_hash`
- `trust_class`
- `access_class`

Source classes:

- human_authored
- primary_publication
- official_documentation
- official_dataset
- secondary_publication
- model_generated
- system_generated

These classifications describe origins, not automatic truth rankings.

### 5.7 Dataset

Attributes:

- `canonical_key`
- `name`
- `provider`
- `geography`
- `frequency`
- `units`
- `currency`
- `coverage_start`
- `coverage_end`
- `revision_policy`
- `source_uri`

The actual high-volume observations remain outside the graph.

### 5.8 Concept

Represents reusable knowledge.

Attributes:

- `canonical_key`
- `name`
- `description`
- `domain`
- `discipline`
- `definition_source`

Examples:

- Temporal databases.
- Supply elasticity.
- Distributed systems.
- Monetary policy.

### 5.9 Requirement

Attributes:

- `requirement_id`
- `requirement_kind`
- `statement`
- `rationale`
- `priority`
- `verification_method`
- `status`

Requirement kinds:

- stakeholder
- functional
- non_functional
- security
- operational
- data_governance
- interface
- reliability

### 5.10 Decision

Attributes:

- `decision_id`
- `statement`
- `rationale`
- `selected_option`
- `alternatives`
- `decision_status`
- `decided_at`
- `review_condition`
- `owner`

Decision status:

- proposed
- accepted
- superseded
- rejected
- under_review

### 5.11 Claim

A claim is an atomic assertion.

Attributes:

- `claim_id`
- `statement`
- `claim_kind`
- `epistemic_status`
- `review_status`
- `scope`
- `observed_at`
- `author_or_origin`

Claim kinds:

- observation
- reported_claim
- inference
- hypothesis
- interpretation
- finding

Epistemic status:

- established_within_scope
- supported
- provisional
- disputed
- retracted

Review status:

- unreviewed
- reviewed
- approved
- rejected

A source-reported statement must not automatically become an established claim.

### 5.12 Experiment

Attributes:

- `experiment_id`
- `question`
- `method`
- `experiment_type`
- `parameters_reference`
- `dataset_references`
- `code_revision`
- `seed`
- `execution_status`
- `reproducibility_reference`

### 5.13 Finding

Attributes:

- `finding_id`
- `statement`
- `finding_kind`
- `scope`
- `method`
- `limitations`
- `review_status`

Finding kinds:

- empirical
- theoretical
- computational
- statistical
- qualitative
- operational

### 5.14 VariableDefinition

This entity requires special attention.

A variable represents a named property that can be configured, observed, changed, measured or calculated.

Attributes:

- `canonical_key`
- `name`
- `symbol`
- `kind`
- `data_type`
- `unit`
- `scope`
- `description`
- `owner_reference`
- `mutability`
- `constraints`
- `default_value`
- `sensitivity`
- `canonical_source`

Sensitive values and credentials must never be stored as variable values in the knowledge graph.

---

# PART IV. VARIABLE AND PARAMETER MODEL

## 6. Variable taxonomy

We need a consistent distinction between variables across software systems, research experiments and mathematical models.

I propose seven canonical categories.

| Kind | Meaning | Example |
|---|---|---|
| `configuration` | System configuration | API timeout |
| `design_parameter` | A controllable design choice | Cache size |
| `model_parameter` | Parameter of a mathematical model | Elasticity coefficient |
| `input` | Input supplied to a computation | Interest rate |
| `state` | Changing internal state | Current inventory |
| `output_metric` | Measured or calculated output | Revenue |
| `dataset_field` | A named data field | CPI index |

These are semantic categories, not necessarily Graphiti entity classes beyond the shared `VariableDefinition` type.

### 6.1 Data types

A separate `data_type` field determines the representation.

Supported initial types:

- string
- integer
- number
- decimal
- boolean
- enum
- date
- timestamp
- duration
- categorical
- object_reference
- series_reference

Where possible, values should be validated using typed schemas instead of unstructured strings.

### 6.2 Variable scope

A variable also needs a scope.

- ecosystem
- project
- system
- component
- environment
- model
- experiment
- dataset

For example:

`configuration:ecosystem-kb/api_timeout`

and

`model_parameter:elasticity_of_demand`

are different variables even if their displayed names happen to be similar.

### 6.3 Mutability

Define whether the variable can change and how.

- immutable
- configuration_controlled
- operator_controlled
- runtime_observed
- calculated
- externally_updated

### 6.4 Units and dimensionality

Numeric values without units are dangerous.

Examples:

- 100 milliseconds
- 100 seconds
- 100 percent
- 100 Indian rupees
- 100 USD

These cannot be treated as interchangeable values.

Every applicable numerical variable should have a declared unit and, when needed, currency and measurement conventions.

### 6.5 Where variable values live

The KB stores variable definitions, ownership, relationships and meaningful historical changes.

It does not become a general telemetry database.

| Value | Storage authority |
|---|---|
| Current application configuration | Owning application's configuration store |
| Historical configuration changes | Git or control-plane audit records |
| Experiment parameter sets | Experiment registry |
| High-volume state measurements | Telemetry/time-series storage |
| Dataset observations | Dataset storage |
| Secrets | Dedicated secret manager |
| Variable definitions | KB ontology and source registry |

When a variable changes meaningfully, its owner can submit an event.

The KB then links that event to the variable definition and its owning system.

### 6.6 Example variable contract

```json
{
  "canonical_key": "model_parameter:demand_elasticity",
  "name": "demand_elasticity",
  "symbol": "epsilon",
  "kind": "model_parameter",
  "data_type": "number",
  "unit": "dimensionless",
  "scope": "model",
  "description": "Price elasticity of demand",
  "mutability": "operator_controlled",
  "constraints": {
    "minimum": null,
    "maximum": null
  },
  "sensitivity": "internal",
  "owner_reference": "model:demand_function"
}
```

The numeric value used in a particular simulation belongs to its experiment parameter set, not the canonical definition.

---

# PART V. RELATIONSHIP MODEL

## 7. Canonical edge vocabulary

We need controlled relationships.

Do not allow agents to invent a new relationship name for every sentence.

### Structural relationships

| Relationship | Source | Target |
|---|---|---|
| `PART_OF` | Component | System/Project |
| `CONTAINS` | Project/System | System/Component/Artifact |
| `DEPENDS_ON` | System/Component | System/Component |
| `IMPLEMENTS` | System/Component/Artifact | Requirement |
| `EXPOSES` | System/Component | Interface |
| `DEFINES` | System/Component | VariableDefinition/Requirement |
| `USES` | System/Component/Experiment | Concept/Dataset/VariableDefinition |
| `OWNED_BY` | Component/Interface | System |

### Knowledge relationships

| Relationship | Source | Target |
|---|---|---|
| `SUPPORTED_BY` | Claim | Source/Finding/Experiment |
| `CONTRADICTED_BY` | Claim | Claim/Source/Finding |
| `DERIVED_FROM` | Claim/Finding | Source/Dataset/Experiment |
| `TESTS` | Experiment | Claim |
| `PRODUCES` | Experiment | Finding |
| `SUPERSEDES` | Decision/Claim | Decision/Claim |
| `REFINES` | Requirement/Concept | Requirement/Concept |
| `REFERENCES` | Artifact/Claim | Source/Artifact |

Graphiti's own `MENTIONS` relationship remains useful for episodic provenance.

### 7.1 Relationship constraints

Each relationship must have:

- An allowed source type.
- An allowed target type.
- A precise meaning.
- An optional typed metadata model.
- A provenance path.
- Applicable temporal behavior.

For example:

`EXPOSES` connects a System or Component to an Interface.

It should not be used to mean that one idea "exposes" another idea in an essay.

### 7.2 Edge mapping policy

We will use Graphiti's `edge_type_map` to define recognized type combinations.

However, the application must additionally validate extracted relationships.

The reason is important: Graphiti documentation specifies that an unmapped entity pair can retain an extracted predicate as an ordinary edge name without its custom typed edge schema.

Therefore:

**An extracted relationship is a proposal until it passes our ontology validation.**

Unknown edges should be logged and quarantined, not silently admitted into the canonical vocabulary.

---

# PART VI. TEMPORAL, IDENTITY AND PROVENANCE MODEL

## 8. Identity

Graphiti's internal UUID is not our cross-system identity standard.

We need our own stable canonical identifiers.

Examples:

```text
project:ecosystem-kb
system:arivoo
system:graphiti
component:ecosystem-kb/ingestion
interface:ecosystem-kb/mcp-v1
variable:ecosystem-kb/ingestion-concurrency
concept:temporal-knowledge-graph
```

Rules:

1. A canonical identity must be stable across display-name changes.
2. Every external source has a stable source identifier.
3. Aliases resolve to an existing canonical identity.
4. Ambiguous identity matches require review.
5. Renaming does not create a new entity.
6. A merge must preserve all original identifiers as aliases.
7. Graphiti UUIDs must be mapped to our canonical identifiers where an entity is registered.

Maintain an identity registry outside Graphiti.

Suggested mapping:

`canonical_key → graphiti_uuid → source_references → aliases`

Graphiti's entity-resolution mechanism assists with resolution. It does not remove our responsibility for canonical identity management.

## 9. Temporal model

Two separate times must be preserved.

**Reference time:** When something happened or was valid in the real or application domain.

**Ingestion time:** When our KB received and processed it.

Example:

A decision is made on November 14, but its record is integrated into the KB on November 18.

These are not the same time.

Our external event contract should contain:

- `occurred_at`
- `recorded_at`
- `processed_at`

For knowledge claims, also preserve their applicable valid-time interval when known.

A revision must not silently erase history.

When a fact changes:

1. Preserve the earlier evidence.
2. Record the new source or observation.
3. Establish the new valid relationship.
4. Mark the old assertion as superseded, contradicted or invalidated as appropriate.
5. Preserve why the change happened.

Graphiti's temporal edge handling should perform the graph-level temporal work where applicable. Our control plane maintains the associated decision and review history.

## 10. Provenance

Every durable claim, decision, finding and extracted relationship should be traceable.

Required provenance information:

| Field | Purpose |
|---|---|
| `source_id` | Canonical origin |
| `source_uri` | Original location |
| `source_revision` | Version of source |
| `content_hash` | Integrity and change detection |
| `event_id` | Ingestion event identity |
| `occurred_at` | Domain event time |
| `recorded_at` | System receipt time |
| `extraction_version` | Extraction implementation |
| `ontology_version` | Ontology used |
| `actor_type` | Human, application or agent |
| `review_status` | Whether reviewed |

Do not use an LLM's confidence score as a substitute for provenance.

---

# PART VII. KNOWLEDGE LIFECYCLE

## 11. Event ingestion state machine

Every incoming event must have an explicit lifecycle.

```text
RECEIVED
    |
    v
VALIDATING
    |
    +------> REJECTED
    |
    v
ACCEPTED
    |
    v
QUEUED
    |
    v
PROCESSING
    |
    +------> RETRYABLE_FAILURE
    |
    +------> QUARANTINED
    |
    v
GRAPH_WRITTEN
    |
    v
INDEX_VERIFIED
    |
    v
COMPLETED
```

Terminal or exceptional states include:

- rejected
- quarantined
- failed_final
- superseded
- withdrawn

These are ingestion-processing states, not epistemic truth states.

A source can be correctly ingested while its claims remain unreviewed.

That distinction is essential.

## 12. Knowledge review states

Independently track the condition of extracted knowledge.

```text
UNREVIEWED
    |
    v
REVIEWED
    |
    +------> APPROVED
    |
    +------> DISPUTED
    |
    +------> REJECTED
```

A previously approved claim can later be disputed or retracted.

Nothing is silently overwritten.

## 13. Ingestion pipeline

The proposed sequence:

1. Receive authenticated event.
2. Validate event schema.
3. Validate source identity.
4. Verify event idempotency key.
5. Check source and content classification.
6. Persist event in the control-plane database.
7. Apply ingestion policy.
8. Submit approved content to Graphiti.
9. Apply entity and edge schemas.
10. Perform ontology and identity checks.
11. Record graph processing result.
12. Update source and episode mappings.
13. Generate review actions if needed.
14. Return the event receipt.

Large files should be handled through source references and controlled extraction, not unrestricted binary payloads.

## 14. Knowledge compounding loop

The system must continuously improve its understanding.

```text
CAPTURE
   |
   v
CLASSIFY
   |
   v
EXTRACT
   |
   v
RESOLVE IDENTITIES
   |
   v
RECONCILE WITH EXISTING KNOWLEDGE
   |
   v
INDEX AND CONNECT
   |
   v
RETRIEVE AND USE
   |
   v
NEW EVIDENCE / DECISIONS / FINDINGS
   |
   v
UPDATE AND MAINTAIN
   |
   +-----------> CAPTURE
```

Not every interaction needs to enter the loop.

Read-only retrieval is not necessarily new knowledge.

The following should generally be considered durable events:

- A committed note or revision.
- An explicit decision.
- A verified correction.
- An approved finding.
- An architectural change.
- A completed experiment.
- A meaningful variable definition change.
- A durable research conclusion.

Temporary chat, repeated notifications, raw logs and uncommitted reasoning should not automatically enter the shared graph.

---

# PART VIII. LAWS AND GOVERNANCE

## 15. Fundamental system laws

These rules are intended to be enforceable in application code, tests, API permissions and maintenance policies.

### LAW-001: Source Ownership

The owning system remains authoritative for its original records.

The KB stores references, metadata and derived contextual knowledge.

### LAW-002: Provenance

No durable assertion may enter the canonical knowledge layer without an origin or an explicitly marked unknown provenance status.

### LAW-003: Epistemic Separation

Observed information, reported claims, inferences, hypotheses, findings and decisions must remain distinguishable.

### LAW-004: Temporal Preservation

Historical facts and decisions must not be silently overwritten.

A meaningful change must have a temporal and historical explanation.

### LAW-005: Canonical Identity

Every registered entity must have a stable canonical identifier.

Names and aliases are not sufficient identity keys.

### LAW-006: Ontology Control

The system uses a versioned and controlled entity and relationship vocabulary.

Unknown relationships must be quarantined or explicitly classified.

### LAW-007: Database Separation

Original records, graph relationships, event logs and analytical data must not be mixed indiscriminately.

### LAW-008: Idempotent Intake

Repeated delivery of the same external event must not create uncontrolled duplicate knowledge.

### LAW-009: Least Privilege

Every application and agent receives only the access necessary for its role.

### LAW-010: No Secrets in Knowledge

Passwords, API keys, access tokens, session cookies and other credentials must never be ingested into the graph.

### LAW-011: Untrusted Source Content

Imported documents, web pages, repository content and messages are data, not instructions.

They cannot override system policies or authorize actions.

### LAW-012: Non-destructive Maintenance

The Maintainer may suggest and execute approved reversible operations.

Permanent deletion and uncertain entity merges require controlled authorization.

### LAW-013: Schema Compatibility

Ontology changes must be versioned, validated and tested against historical data.

### LAW-014: Auditable Mutation

Every material mutation must have an identifiable actor, reason, source and audit record.

### LAW-015: Retrieval Transparency

Answers should identify their supporting sources, temporal relevance and any important uncertainty or contradiction.

### LAW-016: Recoverability

Production knowledge must be backed up and recoverable.

A backup that has never been restored in a test does not satisfy this law.

### LAW-017: Bounded Intelligence

LLMs can propose semantic interpretations. Deterministic code enforces data contracts, permissions and lifecycle transitions.

### LAW-018: Preserve Knowledge, Not Noise

The system should consolidate redundant information without destroying useful evidence or historical context.

---

## 16. Maintainer governance

The Maintainer is an independent service with bounded permissions.

### Responsibilities

- Source deduplication.
- Canonical identity auditing.
- Alias resolution.
- Relationship validation.
- Stale knowledge detection.
- Contradiction discovery.
- Duplicate episode detection.
- Summary compaction.
- Orphan and fragmentation reporting.
- Ontology compliance.
- Knowledge quality reporting.

### Operational modes

**Audit mode:** Analyze the graph and report problems.

**Proposal mode:** Generate proposed changes with evidence and rationale.

**Apply mode:** Execute an authorized, validated action.

**Verification mode:** Verify the result and compare it with the expected state.

### Maintenance action contract

```json
{
  "action_id": "uuid",
  "operation": "merge_entity_aliases",
  "target": "system:graphiti",
  "candidates": [],
  "evidence": [],
  "reason": "Canonical identity match",
  "risk_level": "medium",
  "reversibility": "reversible",
  "approval_status": "pending",
  "execution_status": "not_started"
}
```

### Initial permission matrix

| Operation | Automatic | Approval |
|---|---|---|
| Schema validation | Yes | No |
| Duplicate event rejection | Yes | No |
| Alias candidate discovery | Yes | No |
| Search index verification | Yes | No |
| Duplicate summaries detection | Yes | No |
| Entity merge | No | Yes |
| Historical deletion | No | Yes |
| Bulk graph rewrite | No | Yes |
| Ontology migration | No | Yes |
| Knowledge compaction | Proposal first | Required for irreversible change |

The Maintainer should not begin as a fully autonomous agent.

First implement deterministic maintenance utilities. Then introduce bounded LLM-assisted judgment. Finally, evaluate its recommendations against a curated test corpus.

---

# PART IX. TECHNICAL ARCHITECTURE

## 17. Selected technology stack

| Layer | Technology | Responsibility |
|---|---|---|
| Knowledge engine | `graphiti-core` | Temporal extraction and retrieval |
| Graph database | FalkorDB | Persistent graph |
| Control plane | PostgreSQL | Event ledger and governance |
| API | FastAPI | Authenticated application boundary |
| Agent protocol | MCP | Shared agent interface |
| Event validation | Pydantic | Typed contracts |
| Background processing | Python worker | Ingestion and maintenance |
| Containerization | Docker Compose | Reproducible deployment |
| Reverse proxy | Caddy or equivalent | HTTPS and gateway boundary |
| Development | `uv` | Python dependencies |
| Testing | pytest | Verification |
| Code quality | Ruff and type checking | Static quality controls |
| Observability | Structured logs and metrics | Operations |

Do not add another vector database solely for the sake of adding one. Graphiti already supplies its own hybrid graph retrieval mechanisms.

Likewise, do not introduce a heavyweight agent framework before we know exactly what responsibilities the Maintainer needs.

## 18. Runtime architecture

### 18.1 Write path

```text
Arivoo / Other Application
          |
          v
     Application Outbox
          |
          v
      KB Gateway
          |
          v
   Authentication & Policy
          |
          v
   PostgreSQL Event Ledger
          |
          v
     Ingestion Worker
          |
          v
    Graphiti Core API
          |
          v
       FalkorDB
```

### 18.2 Read path

```text
ChatGPT / Coding Agent / Application
                  |
                  v
              MCP / REST
                  |
                  v
          Authentication
                  |
                  v
           Access Policy
                  |
                  v
            KB Service
                  |
                  v
             Graphiti
                  |
                  v
             FalkorDB
                  |
                  v
       Provenance Enrichment
                  |
                  v
      Context + Sources + Relations
```

The gateway owns authorization. Clients do not choose arbitrary graph namespaces.

### 18.3 Maintenance path

```text
Scheduled Audit / Trigger
          |
          v
   Maintainer Service
          |
          v
      Graph Analysis
          |
          v
    Proposed Actions
          |
          v
  Policy + Approval Gate
          |
          v
   Authorized Execution
          |
          v
     Audit + Verify
```

## 19. Graphiti integration

Use upstream Graphiti as an installed dependency.

Our code should integrate through its public Python interfaces wherever possible.

We will define:

- Custom entity Pydantic models.
- Custom edge Pydantic models.
- Controlled `edge_type_map`.
- Search configurations.
- Group ID resolution.
- Ingestion adapters.
- Retrieval adapters.
- Lifecycle operations.

### 19.1 Fork policy

We will not fork merely to change prompts, schemas, naming conventions or domain models.

A fork or upstream contribution becomes justified only when we establish a reproducible requirement that cannot be met through the public interfaces, such as:

1. A required graph-storage capability unsupported by the exposed driver.
2. A reproducible upstream defect blocking our requirements.
3. An essential identity-resolution behavior unavailable through supported configuration.
4. An essential performance or consistency requirement needing a core-level change.

Any such change requires an architecture decision record, test case and compatibility assessment.

Until then, dependency pinning and a thin adapter are preferable.

### 19.2 Graphiti limitations to account for

- Custom entity types are supplied as Pydantic models.
- Custom edge types are supplied as Pydantic models.
- Edge mappings control typed relationships between entity pairs.
- Unmapped predicates may remain untyped.
- New attributes can be compatible with historical entities.
- New entity classes do not automatically classify existing entities.
- Group IDs provide graph namespaces, not authorization.

The current upstream documentation also describes its MCP implementation as experimental. It exposes powerful operations including `clear_graph` and deletion tools. We should not expose that unrestricted interface as our production gateway. <https://help.getzep.com/graphiti/getting-started/mcp-server>

---

# PART X. CONTROL-PLANE DATA MODEL

## 20. PostgreSQL responsibility

PostgreSQL is not a second knowledge graph.

It is the deterministic control plane that governs what enters Graphiti and how we manage it.

Initial logical tables:

### 20.1 `kb_sources`

Records registered source systems and artifacts.

Fields:

- source_id
- canonical_uri
- source_type
- owner_system
- trust_class
- access_class
- current_revision
- content_hash
- created_at
- updated_at

### 20.2 `kb_events`

Stores incoming events.

Fields:

- event_id
- idempotency_key
- schema_version
- source_id
- source_revision
- event_type
- payload_reference
- content_hash
- occurred_at
- received_at
- processing_status

Unique constraint: `idempotency_key`.

### 20.3 `kb_ingestion_jobs`

Tracks background work.

Fields:

- job_id
- event_id
- status
- attempt_count
- worker_id
- lease_expires_at
- started_at
- completed_at
- last_error

### 20.4 `kb_entity_identity`

Maps canonical identities.

Fields:

- canonical_key
- graphiti_uuid
- entity_type
- canonical_name
- status
- created_at
- updated_at

### 20.5 `kb_aliases`

Maps alternative names to canonical identities.

Fields:

- alias_id
- canonical_key
- alias_value
- alias_source
- confidence_basis
- review_status

### 20.6 `kb_review_queue`

Tracks claims and maintenance recommendations requiring review.

Fields:

- review_id
- item_type
- item_reference
- proposed_action
- reason
- evidence_reference
- status
- reviewer
- reviewed_at

### 20.7 `kb_audit_log`

Immutable operational audit history.

Fields:

- audit_id
- actor
- action
- resource_type
- resource_id
- reason
- before_reference
- after_reference
- occurred_at

### 20.8 `kb_clients`

Tracks system integrations and access capabilities.

Fields:

- client_id
- client_name
- secret_reference
- granted_scopes
- allowed_namespaces
- status
- created_at
- rotated_at

API secrets themselves must live in a secret manager, not in plaintext database records.

---

# PART XI. EVENT AND INTERFACE CONTRACTS

## 21. Universal event contract

Every connected system should send a common envelope.

```json
{
  "schema_version": "kb.event.v1",
  "event_id": "uuid",
  "idempotency_key": "arivoo:note-123:revision-4",
  "event_type": "knowledge.source.updated",

  "source": {
    "system_id": "system:arivoo",
    "record_id": "note-123",
    "revision": 4,
    "canonical_uri": "arivoo://notes/123"
  },

  "timestamps": {
    "occurred_at": "2026-11-18T10:00:00Z",
    "recorded_at": "2026-11-18T10:00:01Z"
  },

  "actor": {
    "type": "human",
    "id": "actor-reference"
  },

  "classification": {
    "trust_class": "human_authored",
    "review_status": "unreviewed",
    "sensitivity": "internal"
  },

  "content": {
    "content_type": "text",
    "content_reference": "arivoo://notes/123",
    "content_hash": "sha256:..."
  }
}
```

Additional domain-specific fields should be defined by typed event payloads rather than arbitrary additions to the envelope.

## 22. API specification

Initial REST endpoints:

| Endpoint | Method | Purpose |
|---|---|---|
| `/v1/events` | POST | Submit an event |
| `/v1/events/{id}` | GET | Get ingestion status |
| `/v1/search` | POST | Search knowledge |
| `/v1/entities/{id}` | GET | Retrieve an entity |
| `/v1/entities/{id}/context` | GET | Retrieve connected context |
| `/v1/sources/{id}` | GET | Retrieve provenance |
| `/v1/maintenance/proposals` | GET | List maintenance proposals |
| `/v1/maintenance/proposals/{id}/approve` | POST | Approve an action |
| `/health` | GET | Liveness |
| `/ready` | GET | Readiness |

Search responses should contain:

- Query.
- Matching entities and facts.
- Relationships.
- Source references.
- Relevant event or reference times.
- Review and epistemic status.
- Any identified contradictions.
- Retrieval metadata.

## 23. MCP contract

We should expose a small, capability-oriented MCP surface.

### Initial tools

| Tool | Access | Purpose |
|---|---|---|
| `kb_search` | Read | Search the graph |
| `kb_get_entity` | Read | Get canonical entity |
| `kb_get_context` | Read | Retrieve related context |
| `kb_get_provenance` | Read | Trace source and history |
| `kb_submit_event` | Write | Contribute durable knowledge |
| `kb_propose_update` | Write | Propose a revision |
| `kb_get_maintenance_report` | Maintainer | Read graph health |

Approval and destructive maintenance operations must be separately authorized.

No general-purpose `clear_graph` tool should be exposed to ordinary agents.

### Client permissions

- Reader: retrieve knowledge.
- Contributor: submit events in authorized scopes.
- Reviewer: approve or reject claims and decisions.
- Maintainer: audit and create proposals.
- Administrator: manage configuration and approved destructive actions.

The gateway derives the accessible namespace from the authenticated integration identity. It must never trust arbitrary `group_id` values supplied by an MCP caller.

---

# PART XII. SYSTEM SECURITY AND RELIABILITY

## 24. Security requirements

1. TLS for every remote connection.
2. FalkorDB must remain on a private network.
3. PostgreSQL credentials must use least-privilege roles.
4. Each connected application receives its own credentials.
5. Per-client access scopes.
6. Secret rotation capability.
7. Input-size limits and rate limiting.
8. Payload redaction and secret detection.
9. Audit trails for mutations.
10. Prompt-injection resistance.
11. Protected namespace selection.
12. Explicit controls for maintenance operations.

Imported external content must never be treated as trusted system instructions.

## 25. Reliability requirements

The ingestion service must tolerate:

- Temporary database outages.
- LLM provider failures.
- Network interruptions.
- Worker termination.
- Duplicate delivery.
- Delayed events.
- Partial processing.
- Invalid source records.
- Graph persistence failures.

The external event ledger and Graphiti cannot be assumed to share one atomic transaction.

Therefore, v1 uses durable, at-least-once event processing with deduplication and reconciliation.

A timeout after a Graphiti write is ambiguous: the graph might already contain the episode.

We must not blindly retry every ambiguous operation.

The Graphiti adapter needs an explicit reconciliation procedure based on event identity and episode provenance. This needs a fault-injection test against the pinned upstream release before production use.

Initially, use a single ordered ingestion worker per production namespace. Graphiti's current implementation describes episode ingestion as multi-step processing and documents sequential processing guidance; we should increase concurrency only after validating ordering, deduplication and consistency. 

## 26. Backup and recovery

Back up:

- FalkorDB persistent graph data.
- PostgreSQL control-plane data.
- Ontology and migration history.
- Deployment configuration.
- Relevant source manifests.

Required recovery capabilities:

- Restore graph from a consistent backup.
- Restore control-plane metadata.
- Reconnect source references.
- Verify Graphiti entity mappings.
- Run a graph integrity audit.
- Confirm search works after restoration.

The deployment must not rely on a local laptop holding the only copy of the production database.

---

# PART XIII. REPOSITORY AND DEVELOPMENT ARCHITECTURE

## 27. Repository identity

Repository name:

`ecosystem-kb`

The repository contains the product-specific knowledge infrastructure.

Graphiti remains an upstream dependency.

### 27.1 Proposed repository structure

```text
ecosystem-kb/
│
├── AGENTS.md
├── LAWS.md
├── INTENT-LEDGER.md
├── DECISIONS.md
├── STATE.md
├── CHANGELOG.md
├── README.md
│
├── pyproject.toml
├── uv.lock
├── .env.example
├── .gitignore
│
├── docs/
│   ├── 00-system-charter.md
│   ├── 01-conops.md
│   ├── 02-stakeholder-requirements.md
│   ├── 03-system-requirements.md
│   ├── 04-architecture.md
│   ├── 05-ontology.md
│   ├── 06-variable-dictionary.md
│   ├── 07-data-governance.md
│   ├── 08-security.md
│   ├── 09-api-contracts.md
│   ├── 10-mcp-contracts.md
│   ├── 11-deployment.md
│   ├── 12-verification-validation.md
│   ├── 13-operations.md
│   └── adr/
│
├── ontology/
│   ├── __init__.py
│   ├── entities.py
│   ├── relationships.py
│   ├── variables.py
│   ├── enums.py
│   ├── mappings.py
│   ├── registry.py
│   └── versions.py
│
├── contracts/
│   ├── __init__.py
│   ├── events.py
│   ├── sources.py
│   ├── provenance.py
│   ├── clients.py
│   ├── search.py
│   ├── maintenance.py
│   └── responses.py
│
├── src/
│   └── ecosystem_kb/
│       │
│       ├── core/
│       │   ├── config.py
│       │   ├── exceptions.py
│       │   ├── identity.py
│       │   ├── policies.py
│       │   └── lifecycle.py
│       │
│       ├── adapters/
│       │   ├── graphiti/
│       │   │   ├── client.py
│       │   │   ├── ingestion.py
│       │   │   ├── retrieval.py
│       │   │   ├── reconciliation.py
│       │   │   └── health.py
│       │   │
│       │   └── postgres/
│       │       ├── client.py
│       │       ├── repositories.py
│       │       └── migrations.py
│       │
│       ├── services/
│       │   ├── event_service.py
│       │   ├── source_service.py
│       │   ├── search_service.py
│       │   ├── identity_service.py
│       │   ├── review_service.py
│       │   └── maintenance_service.py
│       │
│       ├── api/
│       │   ├── app.py
│       │   ├── dependencies.py
│       │   ├── auth.py
│       │   └── routes/
│       │
│       ├── mcp/
│       │   ├── server.py
│       │   ├── tools/
│       │   └── permissions.py
│       │
│       ├── workers/
│       │   ├── ingestion.py
│       │   ├── reconciliation.py
│       │   └── maintenance.py
│       │
│       └── cli/
│           ├── main.py
│           └── commands/
│
├── maintenance/
│   ├── audits/
│   ├── proposals/
│   ├── evaluators/
│   └── policies/
│
├── migrations/
│   ├── postgres/
│   └── ontology/
│
├── tests/
│   ├── unit/
│   ├── contract/
│   ├── integration/
│   ├── system/
│   ├── acceptance/
│   ├── security/
│   ├── resilience/
│   ├── fixtures/
│   └── evaluations/
│
├── deploy/
│   ├── docker/
│   ├── compose.yaml
│   ├── Dockerfile
│   ├── proxy/
│   ├── env/
│   └── backups/
│
└── scripts/
    ├── bootstrap.py
    ├── validate_ontology.py
    ├── seed_test_graph.py
    ├── graph_health.py
    ├── backup.py
    └── restore.py
```

The directory structure is intentionally modular, but implementation should remain incremental. Create modules when their responsibilities actually become necessary.

## 28. Repository governance

The repository inherits the engineering discipline established in Arivoo.

### `AGENTS.md`

Defines the operating instructions for coding agents.

Includes:

- Architecture and boundaries.
- Important files.
- Development commands.
- Testing requirements.
- Prohibited shortcuts.
- Required documentation updates.

### `LAWS.md`

Contains enforceable system invariants.

It should be treated as a technical contract, not an informal description.

### `INTENT-LEDGER.md`

Records the intended direction and enduring goals of the system.

### `DECISIONS.md`

Records accepted architectural decisions and their rationale.

### `STATE.md`

Records the current implementation state, incomplete work and known constraints.

### `CHANGELOG.md`

Records released changes.

These documents should not compete with one another.

For example, an accepted architectural decision belongs in `DECISIONS.md`, current implementation status belongs in `STATE.md`, and immutable system invariants belong in `LAWS.md`.

---

# PART XIV. V-MODEL SYSTEM ENGINEERING PLAN

## 29. V-model overview

The V-model is the governing implementation and assurance process.

Each design level on the left side has a corresponding verification or validation activity on the right side.

```text
LEFT: DECOMPOSITION                    RIGHT: INTEGRATION AND VALIDATION

Mission / Stakeholder Needs ---------- Operational Acceptance
          \                                      /
           \                                    /
     System Requirements ---------------- System Verification
             \                              /
              \                            /
      Architecture Design ---------- Integration Verification
                 \                    /
                  \                  /
            Detailed Design ---- Component Verification
                      \          /
                       \        /
                    IMPLEMENTATION
```

The purpose is to prevent us from implementing a system and only afterward attempting to determine what it was supposed to do.

Every major requirement must have an associated verification or validation method before implementation begins.

## 30. V-model phase 0: Mission and concept of operations

**Reference:** SE-00

### Objectives

- Establish the system mission.
- Define stakeholders and consumers.
- Describe how the knowledge service is expected to operate.
- Establish the ownership boundary.
- Identify operational environments.
- Define success conditions.

### Activities

1. Identify current and future integrating systems.
2. Define how each system contributes information.
3. Define how agents retrieve information.
4. Define expected human review workflows.
5. Define expected maintenance behavior.
6. Identify security and data ownership constraints.
7. Establish initial operational assumptions.

### Deliverables

- System charter.
- Concept of Operations (CONOPS).
- Stakeholder register.
- Context diagram.
- Initial risk register.
- Initial scope and non-goals.

### Exit criteria

The system mission, ownership model, major actors, system boundaries and expected operational scenarios are documented and accepted.

## 31. V-model phase 1: Stakeholder requirements

**Reference:** SE-01

Define requirements from the perspective of the systems consuming the KB.

### Stakeholder requirements

| ID | Requirement |
|---|---|
| STK-001 | Every integrating application can contribute durable knowledge. |
| STK-002 | Authorized applications can retrieve shared context. |
| STK-003 | Knowledge remains accessible independently of the local machine. |
| STK-004 | Knowledge relationships preserve source provenance. |
| STK-005 | Historical decisions and revisions remain discoverable. |
| STK-006 | New applications can integrate using documented contracts. |
| STK-007 | Agents cannot arbitrarily mutate or delete the knowledge graph. |
| STK-008 | The system can identify contradictions and outdated information. |
| STK-009 | Human reviewers can inspect and control sensitive changes. |
| STK-010 | The system can be maintained and upgraded without losing knowledge. |

### Activities

- Interview each integration owner, including future systems.
- Define typical reads and writes.
- Define user expectations for search results.
- Establish system permission boundaries.
- Define operational constraints.

### Deliverables

- Stakeholder requirements specification.
- Use-case catalogue.
- User and machine interaction models.
- Acceptance-test scenarios.

### Exit criteria

Every stakeholder requirement has a clear meaning, an owner and at least one validation scenario.

## 32. V-model phase 2: System requirements

**Reference:** SE-02

Translate stakeholder expectations into measurable technical requirements.

### Functional requirements

| ID | Requirement |
|---|---|
| SYS-F-001 | The KB shall accept validated event envelopes. |
| SYS-F-002 | The KB shall persist event receipts before asynchronous processing. |
| SYS-F-003 | The KB shall process events through a controlled lifecycle. |
| SYS-F-004 | The KB shall use a versioned entity ontology. |
| SYS-F-005 | The KB shall use a versioned relationship ontology. |
| SYS-F-006 | The KB shall maintain stable canonical identifiers. |
| SYS-F-007 | The KB shall preserve source and revision references. |
| SYS-F-008 | The KB shall support hybrid knowledge retrieval through Graphiti. |
| SYS-F-009 | The KB shall support typed variable definitions. |
| SYS-F-010 | The KB shall distinguish claims, evidence, findings and decisions. |
| SYS-F-011 | The KB shall expose approved functionality through REST and MCP. |
| SYS-F-012 | The KB shall generate maintenance proposals. |
| SYS-F-013 | The KB shall preserve an auditable mutation history. |
| SYS-F-014 | The KB shall support knowledge and ontology evolution. |
| SYS-F-015 | The KB shall report ingestion and graph health. |

### Data requirements

| ID | Requirement |
|---|---|
| SYS-D-001 | Every accepted event shall have a unique identity. |
| SYS-D-002 | Event provenance shall be retained. |
| SYS-D-003 | Source revisions shall be identifiable. |
| SYS-D-004 | Numerical measurements shall not be stored indiscriminately in the graph. |
| SYS-D-005 | Sensitive values shall be excluded from ingestion. |
| SYS-D-006 | Historical information shall remain traceable. |

### Security requirements

| ID | Requirement |
|---|---|
| SYS-S-001 | Every remote client must authenticate. |
| SYS-S-002 | Authorization shall be enforced server-side. |
| SYS-S-003 | Namespace access shall be derived from authenticated identity. |
| SYS-S-004 | No ordinary client may clear or bulk-delete the graph. |
| SYS-S-005 | Every material mutation shall be audited. |
| SYS-S-006 | Secrets must never be exposed through search or logs. |

### Operational requirements

| ID | Requirement |
|---|---|
| SYS-O-001 | The graph database shall use persistent storage. |
| SYS-O-002 | Production must be independent of the developer's laptop. |
| SYS-O-003 | Backup and restore procedures shall be tested. |
| SYS-O-004 | Failed jobs shall be retryable or quarantinable. |
| SYS-O-005 | Services shall expose health and readiness checks. |
| SYS-O-006 | Dependencies and deployment versions shall be pinned. |

### Deliverables

- System Requirements Specification (SyRS).
- Requirements traceability matrix.
- Quality attribute catalogue.
- Security requirements.
- Data dictionary.
- Acceptance criteria.

### Exit criteria

Every system requirement has a unique ID, test method, expected result and parent stakeholder requirement where applicable.

## 33. V-model phase 3: System architecture

**Reference:** SE-03

Convert requirements into an implementation architecture.

### Architecture decisions

| ADR | Decision |
|---|---|
| ADR-001 | Use upstream Graphiti Core as a dependency. |
| ADR-002 | Use FalkorDB as graph persistence. |
| ADR-003 | Use PostgreSQL as the control plane. |
| ADR-004 | Build a custom authenticated gateway. |
| ADR-005 | Expose REST and MCP through controlled interfaces. |
| ADR-006 | Use one shared production namespace. |
| ADR-007 | Use a versioned ontology. |
| ADR-008 | Make event ingestion asynchronous and durable. |
| ADR-009 | Use bounded deterministic maintenance. |
| ADR-010 | Keep original records in owning systems. |

### Activities

1. Establish container and service boundaries.
2. Define network topology.
3. Define trust boundaries.
4. Define deployment configuration.
5. Define namespace ownership.
6. Define database boundaries.
7. Define worker lifecycle.
8. Establish failure and retry behavior.
9. Establish security threat model.
10. Define system observability.

### Deliverables

- Architecture Description Document.
- Container diagram.
- Component diagram.
- Data-flow diagram.
- Deployment diagram.
- Threat model.
- ADR catalogue.
- Interface Control Document.

### Exit criteria

Every system requirement maps to an architecture component or control mechanism.

Every external interface has a documented contract.

Every security boundary has an enforcement point.

## 34. V-model phase 4: Detailed design

**Reference:** SE-04

Design every component sufficiently to implement and verify it independently.

### Detailed design areas

**Ontology design**
- Entity Pydantic models.
- Edge Pydantic models.
- Relationship mappings.
- Attribute constraints.
- Type registry.
- Version compatibility.

**Event design**
- Universal event envelope.
- Event payload schemas.
- Idempotency.
- Processing state transitions.
- Retry rules.

**Identity design**
- Canonical key generation.
- Alias management.
- Collision detection.
- Graph UUID mapping.
- Safe merge procedure.

**Temporal design**
- Reference-time semantics.
- Receipt and processing timestamps.
- Supersession.
- Dispute.
- Retraction.

**Gateway design**
- API authentication.
- Authorization.
- Rate limits.
- Error contracts.
- Namespace resolution.

**MCP design**
- Tool schemas.
- Client scopes.
- Argument validation.
- Output schemas.
- Dangerous-operation restrictions.

**Maintainer design**
- Audit rules.
- Proposal schema.
- Risk classification.
- Approval workflow.
- Verification and rollback.

### Deliverables

- Software Design Description.
- Ontology specification.
- API specification.
- MCP specification.
- Event contract specification.
- Database schema.
- Maintenance operation specifications.
- Component test plans.

### Exit criteria

Every subsystem has an explicit contract, responsibility, error behavior and corresponding component verification test.

## 35. V-model phase 5: Implementation

**Reference:** SE-05

Implement the designed components in dependency order.

### Implementation work packages

**WP-01: Repository foundation**

- Create repository.
- Add governance documents.
- Set up Python project.
- Establish dependency lockfile.
- Configure formatting and type checking.
- Set up tests and CI.
- Add configuration and structured logging.

**WP-02: Ontology**

- Define entity types.
- Define variable types.
- Define edge types.
- Define allowed type combinations.
- Create ontology registry.
- Implement schema validation.
- Establish ontology versioning.

**WP-03: Control plane**

- Configure Postgres connection.
- Create initial migrations.
- Implement source registry.
- Implement event ledger.
- Implement processing queue.
- Implement idempotency.
- Implement audit tables.

**WP-04: Graphiti adapter**

- Install pinned `graphiti-core`.
- Configure FalkorDB driver.
- Initialize Graphiti.
- Implement episode ingestion.
- Supply custom entity and edge schemas.
- Implement search.
- Implement source mappings.
- Implement graph health checks.

**WP-05: Gateway**

- Implement FastAPI.
- Add authentication.
- Add authorization.
- Add namespace resolver.
- Add event submission endpoint.
- Add event status endpoint.
- Add search and context endpoints.

**WP-06: Workers**

- Implement asynchronous ingestion worker.
- Implement job claiming and leases.
- Implement retry handling.
- Implement ambiguous-outcome reconciliation.
- Implement processing-state updates.

**WP-07: MCP**

- Build restricted MCP server.
- Register approved tools.
- Validate all arguments.
- Enforce per-client scopes.
- Ensure all tool operations use the same services as REST.

**WP-08: Maintainer**

- Build deterministic audit jobs.
- Detect duplicates and collisions.
- Generate proposals.
- Add approval records.
- Execute approved operations.
- Verify results.
- Store action history.

**WP-09: Deployment**

- Build Docker images.
- Create Compose deployment.
- Configure private services.
- Add reverse proxy.
- Configure secrets.
- Configure persistent volumes.
- Add backups and health monitoring.

### Implementation rule

No subsystem is considered complete simply because its code exists.

Each work package must pass its corresponding verification suite.

---

# PART XV. RIGHT SIDE OF THE V-MODEL: VERIFICATION AND VALIDATION

## 36. Component verification

**Matches:** Detailed design.

Test each component independently.

### Ontology tests

- Valid entity construction.
- Invalid entity rejection.
- Missing required fields.
- Optional attribute handling.
- Invalid variable types.
- Invalid numerical constraints.
- Relationship domain/range validation.
- Unknown predicate rejection.
- Schema version compatibility.

### Event tests

- Valid event acceptance.
- Duplicate event rejection.
- Invalid event rejection.
- Timestamp validation.
- Missing provenance rejection.
- Payload hash comparison.
- State transition enforcement.

### Identity tests

- Canonical key uniqueness.
- Alias resolution.
- Case and punctuation variants.
- Ambiguous entity handling.
- Safe identity collision detection.

### Security tests

- Missing credentials.
- Expired credentials.
- Unauthorized action.
- Unauthorized namespace.
- Privilege escalation attempts.
- Secret leakage attempts.

### Exit criteria

Every detailed component contract has passing unit and component tests.

## 37. Integration verification

**Matches:** Architecture design.

Test the integration of the components.

### Integration scenarios

1. API accepts an event.
2. Postgres records the event.
3. Worker claims the job.
4. Graphiti processes the episode.
5. FalkorDB persists the graph.
6. Search returns the expected entities and relationships.
7. Provenance references map back to the original source.
8. MCP exposes the same search result.
9. Unauthorized users are rejected.
10. Maintainer identifies a seeded duplicate.
11. An approved maintenance operation produces the expected result.
12. Audit records are preserved.

### Fault-injection scenarios

- FalkorDB unavailable.
- PostgreSQL unavailable.
- LLM provider unavailable.
- Worker killed during processing.
- Duplicate event delivery.
- Network interruption after graph write.
- Invalid ontology version.
- Delayed source event.

### Exit criteria

The complete data flow works across service boundaries, including defined failure and recovery paths.

## 38. System verification

**Matches:** System requirements.

Verify that the integrated service satisfies the SyRS.

### Functional verification

- All API contracts work.
- MCP tools expose the documented behavior.
- All approved event types process correctly.
- Searches honor permissions.
- Source references remain retrievable.
- Temporal history remains accessible.
- Identity and ontology checks are enforced.

### Security verification

- No public database ports.
- Unauthorized namespace access is impossible.
- Destructive MCP tools are unavailable to ordinary clients.
- Secrets are absent from graph records and logs.
- Sensitive events are appropriately blocked or restricted.

### Performance verification

Measure:

- Event intake latency.
- Queue delay.
- Graph processing duration.
- Search latency.
- Context retrieval latency.
- Concurrent requests.
- Resource consumption.
- Recovery time.

Set production performance targets from measured baseline workload rather than inventing arbitrary throughput numbers.

### Reliability verification

- Persistent storage survives container restarts.
- Worker retries behave correctly.
- Database recovery works.
- Backups restore successfully.
- Graph integrity checks pass.
- Failed jobs are discoverable.

### Exit criteria

All mandatory system requirements have evidence of passing verification.

Security-critical failures are resolved before deployment.

## 39. System validation

**Matches:** Stakeholder requirements.

This is where we establish whether we actually built the system we intended.

Use real workflows, not only unit-test fixtures.

### VAL-001: Shared system context

1. Register Arivoo.
2. Register Graphiti as a dependency.
3. Submit an Arivoo system decision.
4. Query from another client.
5. Verify cross-system context is returned.

Expected result: both systems can retrieve the same shared understanding.

### VAL-002: Decision evolution

1. Record Decision V1.
2. Record Decision V2.
3. Connect V2 to V1 through `SUPERSEDES`.
4. Search for the current decision.
5. Search for historical decisions.

Expected result: current and historical decisions remain distinguishable.

### VAL-003: Contradiction handling

1. Submit a claim.
2. Submit new evidence that contradicts it.
3. Create the contradiction relationship.
4. Search for the disputed topic.

Expected result: the system exposes both claims and their supporting evidence.

### VAL-004: Variable evolution

1. Register a variable definition.
2. Change its description or constraints.
3. Record a revision.
4. Inspect historical metadata.

Expected result: the canonical variable remains stable and the change is traceable.

### VAL-005: Duplicate identity

1. Submit an entity under its canonical name.
2. Submit an alias.
3. Run identity reconciliation.
4. Inspect the graph.

Expected result: one canonical identity and preserved aliases.

### VAL-006: Provenance reconstruction

1. Ingest a document.
2. Extract a claim.
3. Retrieve the claim.
4. Follow its source reference.

Expected result: the original source and revision can be identified.

### VAL-007: Unauthorized access

Attempt access using an unprivileged API credential and modified namespace arguments.

Expected result: the gateway denies access.

### VAL-008: Recovery

1. Back up production data.
2. Restore to a clean test environment.
3. Run integrity checks.
4. Search for previously ingested information.

Expected result: required knowledge and provenance are recoverable.

### Exit criteria

A representative user can successfully perform the intended cross-system workflows without bypassing established security or governance rules.

---

# PART XVI. OPERATIONAL EXECUTION

## 40. Deployment architecture

The first deployment should be a persistent cloud VM running Docker Compose.

Suggested services:

```text
ecosystem-kb/
    |
    +-- kb-api
    |
    +-- kb-mcp
    |
    +-- kb-ingestion-worker
    |
    +-- kb-maintainer-worker
    |
    +-- falkordb
    |
    +-- reverse-proxy
```

PostgreSQL may run on an existing managed Supabase instance or a dedicated managed PostgreSQL service.

The key requirements are logical isolation, backups, access control and stable connectivity.

### Network rules

- Only HTTPS gateway ports are publicly exposed.
- FalkorDB is private.
- PostgreSQL uses private or securely authenticated connectivity.
- Container-to-container communication uses an internal network.
- Administrative access is separate from consumer API access.

### Environment configuration

Maintain separate environments:

- development
- testing
- production

Each should use separate namespaces and credentials.

Do not share production credentials with local test environments.

## 41. Deployment acceptance

Before production activation:

- Verify persistent graph storage.
- Verify PostgreSQL connectivity.
- Verify Graphiti initialization.
- Verify authenticated REST.
- Verify authenticated MCP.
- Verify namespace isolation.
- Verify backup execution.
- Verify backup restoration.
- Verify worker restart behavior.
- Verify DNS and HTTPS.
- Verify secret rotation.
- Verify monitoring and health checks.

A successful `docker compose up` is not a production acceptance test.

## 42. Initial namespace policy

Use:

```text
ecosystem-dev
ecosystem-test
ecosystem-prod
```

Do not create namespaces for every application by default.

Arivoo and the economics harness must be able to connect to the same production knowledge network.

Use the source registry to distinguish application ownership.

Introduce separate knowledge domains or private tenant namespaces only when a real data-isolation requirement exists.

Graphiti documentation explicitly recommends deriving authorized group IDs from authenticated application state and warns against trusting group IDs supplied by callers.

---

# PART XVII. ITERATION AND EVOLUTION

## 43. Engineering iteration loop

The system itself must evolve through a controlled cycle.

```text
OBSERVE
   |
   v
IDENTIFY GAP
   |
   v
WRITE REQUIREMENT
   |
   v
PROPOSE CHANGE
   |
   v
ASSESS IMPACT
   |
   v
REVISE DESIGN
   |
   v
IMPLEMENT
   |
   v
VERIFY
   |
   v
VALIDATE
   |
   v
RELEASE
   |
   v
OBSERVE AGAIN
```

Every material system change should have a linked decision record and tests.

## 44. Ontology evolution

Do not modify the production ontology casually.

Every change should identify:

- What new concept is required?
- Why existing types are insufficient.
- Which entity types change?
- Which relationships change?
- Whether old data remains compatible.
- Whether historical reprocessing is necessary.
- Whether queries and maintenance policies change.
- Whether a migration is reversible.

### Migration procedure

1. Propose ontology version.
2. Update Pydantic models.
3. Validate contracts.
4. Run historical fixtures.
5. Test against a copy of representative graph data.
6. Check extraction accuracy.
7. Check retrieval compatibility.
8. Produce migration plan.
9. Deploy compatible service version.
10. Reprocess or migrate data only when required.
11. Verify resulting graph.
12. Record accepted schema version.

For major changes, use a separate migration namespace and compare results before switching production reads.

## 45. Compounding quality metrics

Measure the actual quality of the knowledge system.

| Metric | What it measures |
|---|---|
| Provenance coverage | Share of durable assertions with traceable sources |
| Duplicate rate | Repeated entities or facts |
| Entity resolution quality | Accuracy of canonical identity matching |
| Relationship validity | Share of relationships passing ontology validation |
| Retrieval relevance | Whether retrieved information answers the query |
| Temporal consistency | Correct representation of historical changes |
| Contradiction detection | Ability to discover conflicting evidence |
| Stale reference rate | Broken or outdated source links |
| Ingestion reliability | Successful event processing |
| Maintenance effectiveness | Useful fixes versus rejected proposals |

Do not optimize a single metric at the expense of historical preservation or semantic quality.

---

# PART XVIII. PHASED EXECUTION AND RELEASE GATES

## 46. Engineering release sequence

The following is the actual order in which I recommend we implement the system.

| Release | Work package | Completion condition |
|---|---|---|
| R0 | Repository and governance | Clean, reproducible project foundation |
| R1 | Ontology and contracts | Validated initial knowledge model |
| R2 | Graphiti + FalkorDB | Working graph ingestion and search |
| R3 | PostgreSQL control plane | Durable events, sources and audit |
| R4 | Gateway and REST | Authenticated, scoped knowledge API |
| R5 | MCP integration | Restricted agent interoperability |
| R6 | Maintainer | Tested auditing and proposal lifecycle |
| R7 | Production deployment | Persistent remote knowledge service |
| R8 | Arivoo integration | First real cross-system contribution |

### Release gate G0: Repository ready

- Governance files committed.
- Python environment reproducible.
- CI operational.
- Initial design documents approved.

### Release gate G1: Ontology ready

- Entity classes validated.
- Edge classes validated.
- Variable schema validated.
- Domain/range checks pass.
- Test fixtures created.

### Release gate G2: Graph engine ready

- FalkorDB persists data.
- Graphiti Core connects.
- Sample episode ingests.
- Search returns meaningful results.
- Custom types work.
- Historical timestamps behave as expected.

### Release gate G3: Control plane ready

- Postgres migrations work.
- Event ledger functions.
- Idempotency works at ingress.
- Queue transitions are valid.
- Audit records are immutable through application permissions.

### Release gate G4: Integration ready

- API writes to the control plane.
- Workers ingest into Graphiti.
- Search works through the gateway.
- Ambiguous write outcomes are reconciled.
- Failure scenarios pass.

### Release gate G5: Agent access ready

- MCP connects.
- Tools are documented.
- Authorization works.
- Namespace selection is server controlled.
- Dangerous actions are restricted.

### Release gate G6: Maintainer ready

- Duplicate audit works.
- Contradiction audit works.
- Proposals are generated.
- Approval workflow works.
- Applied changes are verified and recorded.

### Release gate G7: Production ready

- Remote deployment operational.
- Backups restore.
- HTTPS works.
- Credentials and permissions verified.
- Monitoring operational.
- Production acceptance tests pass.

### Release gate G8: First ecosystem integration

- Arivoo sends a valid committed event.
- The event is indexed.
- Provenance resolves.
- Another application retrieves the result.
- Revisions and supersession behave correctly.

---

# PART XIX. FINAL SYSTEM ACCEPTANCE CRITERIA

## 47. Definition of done

The knowledge infrastructure is ready for its first production use when the following are true.

### Architecture

- [ ] No unnecessary Graphiti fork.
- [ ] Graphiti dependency pinned.
- [ ] FalkorDB persistence verified.
- [ ] Control plane operational.
- [ ] Stable system boundaries.
- [ ] Versioned ontology.

### Knowledge

- [ ] Canonical entities.
- [ ] Controlled relationships.
- [ ] Variable definitions.
- [ ] Source provenance.
- [ ] Temporal history.
- [ ] Claim and evidence classification.
- [ ] Decision lifecycle.
- [ ] Historical retrieval.

### Interfaces

- [ ] Authenticated REST.
- [ ] Restricted MCP.
- [ ] Consistent API contracts.
- [ ] Client-level permissions.
- [ ] Health and readiness.

### Governance

- [ ] Laws documented.
- [ ] Maintainer boundaries defined.
- [ ] Audit trails operational.
- [ ] Approval workflow operational.
- [ ] Schema evolution controlled.
- [ ] Dangerous mutations protected.

### Infrastructure

- [ ] Persistent remote hosting.
- [ ] Database backups.
- [ ] Restoration test.
- [ ] Monitoring.
- [ ] Failure recovery.
- [ ] No critical dependency on the local laptop.

### Integration

- [ ] Arivoo successfully contributes knowledge.
- [ ] A second client can retrieve it.
- [ ] Source references resolve.
- [ ] Event revisions work.
- [ ] Cross-system retrieval works.

---

# PART XX. FIRST IMPLEMENTATION BACKLOG

## 48. Immediate engineering tasks

We should not implement all the repository directories immediately.

Start with the smallest set of artifacts that freezes the system's fundamental contracts.

### Task 1: System charter

Create:

- `docs/00-system-charter.md`
- `docs/01-conops.md`
- `LAWS.md`
- `INTENT-LEDGER.md`

### Task 2: Domain ontology

Create:

- `ontology/entities.py`
- `ontology/relationships.py`
- `ontology/variables.py`
- `ontology/enums.py`
- `ontology/registry.py`

Write fixtures for every entity and relationship type.

### Task 3: Event and source contracts

Create:

- `contracts/events.py`
- `contracts/sources.py`
- `contracts/provenance.py`

Validate idempotency keys, timestamps, ownership and classifications.

### Task 4: Architecture decisions

Write:

- ADR-001: Graphiti as upstream dependency.
- ADR-002: FalkorDB as graph database.
- ADR-003: PostgreSQL control plane.
- ADR-004: Shared production namespace.
- ADR-005: Custom REST and MCP gateway.
- ADR-006: Durable asynchronous ingestion.
- ADR-007: Maintainer governance.

### Task 5: Graph engine proof

Build one isolated integration test:

```text
Create FalkorDB
      |
Connect Graphiti Core
      |
Initialize graph
      |
Register custom types
      |
Ingest sample event
      |
Extract entities and edges
      |
Query graph
      |
Verify provenance
      |
Verify persistence after restart
```

No production APIs yet.

### Task 6: Control plane

Implement the event ledger, source registry, worker queue and audit log.

### Task 7: First complete vertical slice

Submit one source event, process it through Graphiti, retrieve its contextual representation and trace it to the original source.

Only after this works should we expand the API and MCP surface.

---

# PART XXI. ARCHITECTURAL DECISIONS TO FREEZE

## 49. Baseline decision register

| Area | Decision |
|---|---|
| Upstream engine | Graphiti Core |
| Fork | Not required at present |
| Graph database | FalkorDB |
| Control database | PostgreSQL |
| Application framework | FastAPI |
| MCP implementation | Custom restricted MCP adapter |
| Ontology | Versioned, Pydantic-based |
| Identity | External canonical key registry |
| Temporal data | Graphiti plus explicit event timestamps |
| Variables | Typed definitions, values stored by owning system |
| Event processing | Durable asynchronous processing |
| Security | Authenticated gateway and server-side scopes |
| Namespace | Shared production group |
| Maintainer | Independent bounded service |
| Deployment | Docker Compose on persistent remote infrastructure |
| Engineering | V-model with traceable requirements and tests |

## 50. Design principles to carry into every future iteration

1. Do not fork upstream dependencies without demonstrated necessity.
2. Never confuse graph extraction with verification of truth.
3. Never allow an LLM to override deterministic system rules.
4. Keep original records in their owning systems.
5. Give every durable assertion a source or an explicit provenance limitation.
6. Preserve temporal history.
7. Keep variables typed and their values in appropriate storage.
8. Use controlled relationship vocabulary.
9. Make every integration use the same event and retrieval contracts.
10. Treat maintenance as an auditable engineering activity.
11. Design interfaces before connecting applications.
12. Make every architectural change traceable to a requirement or an observed deficiency.
13. Evaluate quality continuously, rather than treating graph size as progress.
14. Build the simplest implementation that satisfies the validated requirements.

---

# FINAL ENGINEERING POSITION

The objective is not to finish another application.

The objective is to establish a durable, extensible knowledge infrastructure that becomes an increasingly valuable part of the entire ecosystem.

Graphiti provides the temporal graph capabilities. FalkorDB provides persistence. PostgreSQL gives us deterministic operational control. Our repository defines the ontology, identity, contracts, laws, lifecycle, integration and maintenance behavior.

The most important engineering distinction is that we own the knowledge architecture without unnecessarily owning the underlying graph engine.

**Initial implementation priority:**

1. Freeze the conceptual model and laws.
2. Define the actual Pydantic ontology and variable types.
3. Establish the event and provenance contracts.
4. Create the repository and system-engineering artifacts.
5. Prove Graphiti + FalkorDB locally.
6. Implement PostgreSQL control-plane persistence.
7. Build the authenticated gateway and restricted MCP.
8. Introduce the Maintainer.
9. Deploy remotely and verify recovery.
10. Integrate Arivoo.

This is the baseline for EKIS v0.1. Future changes should emerge from implementation evidence, new system requirements and actual knowledge-quality evaluations, not from arbitrary architectural expansion.