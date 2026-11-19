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
    """Async resolution — every tool uses this."""
    from ecosystem_kb.core.exceptions import AuthenticationError

    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise AuthenticationError("missing bearer token")
    token = header[7:].strip()
    root = request.scope.get("root_app") or request.scope.get("app")
    services = root.state.services
    maintenance = root.state.maintenance
    client = await root.state.auth.authenticate_token(token)
    return services, maintenance, client


mcp = FastMCP("ecosystem-kb")


@mcp.tool
async def kb_search(query: str, num_results: int = 10, namespace: str | None = None) -> dict[str, Any]:
    """Search the shared knowledge graph. Returns facts with provenance,
    temporal windows, and surfaced contradictions."""
    services, _, client = await _aresolve(get_http_request())
    result = await services.search(
        client, SearchRequest(query=query, num_results=num_results, namespace=namespace)
    )
    return result.model_dump(mode="json")


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
