"""Restricted MCP server (spec §23, ADR-005).

Seven tools, all calling the SAME service layer as REST. No clear_graph, no
delete tools, no raw graph access. Authorization: the same bearer token the
REST API uses, resolved per request via FastMCP's get_http_request();
scopes are enforced inside the services.

FastMCP 4.2.1 verified interfaces:
  - fastmcp.server.dependencies.get_http_request() -> starlette Request
    (works inside @mcp.tool when served over http_app()).
  - mcp.http_app(path='/') -> ASGI app we mount under /mcp.
"""

from __future__ import annotations

from typing import Any

from contracts.search import SearchRequest
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request


async def _aresolve(request) -> tuple:
    """Async resolution — every tool uses this.

    The mounted MCP sub-app REPLACES scope["app"] with itself (Starlette
    semantics), so the FastAPI parent is not reachable through the scope.
    create_app() mirrors the service wiring onto the sub-app's own state at
    startup; resolve through that. authenticate_token resolves KB OAuth
    access tokens first (runbook Step 8), then legacy static tokens only
    while the migration flag allows.
    """
    from ecosystem_kb.core.exceptions import AuthenticationError

    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise AuthenticationError("missing bearer token")
    token = header[7:].strip()
    app = request.scope.get("app")
    state = getattr(app, "state", None)
    services = getattr(state, "services", None)
    maintenance = getattr(state, "maintenance", None)
    auth = getattr(state, "auth", None)
    if services is None or auth is None:
        raise RuntimeError(
            "MCP sub-app state is not wired; was this app built by create_app()?"
        )
    client = await auth.authenticate_token(token)
    return services, maintenance, client


mcp = FastMCP("ecosystem-kb")


@mcp.tool
async def kb_search(query: str, num_results: int = 10, namespace: str | None = None) -> dict[str, Any]:
    """Search the shared knowledge graph.

    WHEN TO CALL: you need facts about entities, systems, or past work; when
    checking whether something is already known/decided before acting.

    WHAT TO SUPPLY: a natural-language query (what you want to know).

    WHAT YOU GET: facts with provenance (source reference), temporal windows
    (valid_at/invalid_at), epistemic status (proposed/accepted/superseded/
    disputed), and surfaced contradictions. Results are retrieval hits —
    source-backed but not verified truth; check review/evidence status.

    DOES NOT: modify anything; guarantee completeness (bounded result set).
    """
    services, _, client = await _aresolve(get_http_request())
    result = await services.search(
        client, SearchRequest(query=query, num_results=num_results, namespace=namespace)
    )
    return result.model_dump(mode="json")


@mcp.tool
async def kb_context(
    query: str,
    project: str | None = None,
    repository: str | None = None,
    include_decisions: bool = True,
    include_constraints: bool = True,
    include_history: bool = False,
    limit: int = 12,
) -> dict[str, Any]:
    """Retrieve the working context for a task — call this BEFORE substantial work.

    WHEN TO CALL: at the start of any non-trivial task (implementation,
    debugging, design), with the task description and, when known, the
    project/repository scope.

    WHAT TO SUPPLY: the task in natural language; optional project and
    repository names to narrow retrieval.

    WHAT YOU GET: a bounded, task-shaped bundle — current decisions and
    constraints (treat as constraints, not suggestions), related facts, and
    any known contradictions or uncertainty. Superseded items are hidden
    unless include_history=True. Every item carries provenance and epistemic
    status.

    DOES NOT: execute anything or modify the graph. If context conflicts
    with the current repository state or a newer verified source, surface
    the conflict — do not blindly follow stale memory.
    """
    from ecosystem_kb.services.high_level import kb_context as _kb_context

    services, _, client = await _aresolve(get_http_request())
    return await _kb_context(
        services, client, query,
        project=project, repository=repository,
        include_decisions=include_decisions, include_constraints=include_constraints,
        include_history=include_history, limit=limit,
    )


@mcp.tool
async def kb_capture(
    kind: str,
    title: str,
    content: str,
    project: str | None = None,
    repository: str | None = None,
    source_uri: str | None = None,
    status: str = "proposed",
    tags: list[str] | None = None,
    extraction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Capture durable knowledge — decisions, findings, corrections, resolved problems.

    WHEN TO CALL: after making a decision, discovering something durable
    (an important correction, a resolved problem, a finding worth keeping),
    or recording a requirement. Do NOT capture transient chatter, secrets,
    speculation presented as fact, or entire transcripts.

    WHAT TO SUPPLY: kind (decision|finding|correction|note|claim|experiment|
    system), a short title, the content itself, and optionally project,
    repository, source_uri (commit/PR/URL backing this), status
    (proposed|accepted|superseded|rejected|disputed), tags.

    TYPED EXTRACTION (optional): `extraction` may carry structured
    {"entities":[{"name","entity_type","summary"}], "edges":[{"source_entity_name",
    "target_entity_name","relation_type","fact"}]} aligned with the KB's
    controlled ontology (entity types like Project/System/Component;
    relations like PART_OF/DEPENDS_ON/IMPLEMENTS). It is validated against
    the ontology and rejected with a specific error when invalid. Supplying
    it makes captured knowledge land as typed graph entities/edges instead
    of relying on server-side extraction. It is still a PROPOSAL: captured
    items remain unreviewed agent-reported knowledge until reviewed.

    WHAT YOU GET: a receipt with the event id and processing status, plus
    extraction_status (validated | unavailable when no inference provider
    is configured). The item is recorded as UNREVIEWED agent-reported
    knowledge — durable and retrievable, but not canonical truth until
    reviewed (epistemic status is preserved end-to-end). Include the
    receipt id in your final answer.

    DOES NOT: overwrite or supersede existing knowledge by itself; make the
    content an approved fact; bypass review for flagged content.
    """
    from ecosystem_kb.services.high_level import kb_capture as _kb_capture

    services, _, client = await _aresolve(get_http_request())
    return await _kb_capture(
        services, client, kind, title, content,
        project=project, repository=repository, source_uri=source_uri,
        status=status, tags=tags, extraction=extraction,
    )


@mcp.tool
async def kb_get_entity(canonical_key: str, namespace: str | None = None) -> dict[str, Any]:
    """Retrieve a canonical entity and its connected context."""
    services, _, client = await _aresolve(get_http_request())
    result = await services.get_entity(client, canonical_key, namespace)
    return result.model_dump(mode="json")


@mcp.tool
async def kb_get_context(query: str, namespace: str | None = None) -> dict[str, Any]:
    """Retrieve connected context for a query: entities plus related facts."""
    services, _, client = await _aresolve(get_http_request())
    result = await services.search(
        client, SearchRequest(query=query, num_results=15, namespace=namespace)
    )
    return {
        "entities": [e.model_dump(mode="json") for e in result.entities],
        "facts": [f.model_dump(mode="json") for f in result.facts],
        "contradictions": result.contradictions,
    }


@mcp.tool
async def kb_get_provenance(reference: str) -> dict[str, Any]:
    """Trace a source reference or event to its origin and history."""
    services, _, client = await _aresolve(get_http_request())
    result = await services.get_provenance(client, reference)
    return result.model_dump(mode="json")


@mcp.tool
async def kb_submit_event(event_json: str) -> dict[str, Any]:
    """Submit a durable knowledge event (kb.event.v1 envelope as JSON).
    Write scope required; idempotent by idempotency_key."""
    import json

    from contracts.events import KnowledgeEvent

    services, _, client = await _aresolve(get_http_request())
    event = KnowledgeEvent(**json.loads(event_json))
    receipt = await services.submit_event(client, event)
    return receipt.model_dump(mode="json")


@mcp.tool
async def kb_propose_update(reference: str, update_note: str) -> dict[str, Any]:
    """Propose a correction or revision to existing knowledge. Creates a
    review-queue item; nothing is mutated without review (LAW-012)."""
    from contracts.clients import AuthzAction

    services, _, client = await _aresolve(get_http_request())
    services._authorize(client, AuthzAction.PROPOSE_UPDATE)
    item = await services.repos.reviews.enqueue(
        item_type="proposed_update",
        item_reference=reference,
        proposed_action="review_update",
        reason=update_note,
    )
    await services.repos.audit.record(
        actor=f"client:{client.client_id}",
        action="propose_update",
        resource_type="knowledge",
        resource_id=reference,
        reason=update_note,
    )
    return {"review_id": item.id, "status": item.status}


@mcp.tool
async def kb_get_maintenance_report(namespace: str | None = None) -> dict[str, Any]:
    """Graph health report: duplicates, contradictions, orphans, proposals.
    Maintain scope required."""
    from ecosystem_kb.core.exceptions import AuthorizationError, NamespaceError

    _, maintenance, client = await _aresolve(get_http_request())
    if "kb:maintain" not in client.scopes:
        raise AuthorizationError("kb:maintain scope required")
    ns = namespace or client.default_namespace
    if ns not in client.allowed_namespaces:
        raise NamespaceError(f"namespace '{ns}' not allowed")
    report = await maintenance.run_audit(ns)
    return report.model_dump(mode="json")


def build_mcp_server() -> FastMCP:
    return mcp
