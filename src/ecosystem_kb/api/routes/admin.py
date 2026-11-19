"""Administrative routes for the host CLI (onboarding repair §1).

The local Compose deployment keeps PostgreSQL on a private Docker network
(no published ports), so the host CLI must NOT connect to the database
directly. These routes give the CLI a scoped, audited path through the
local API (published on loopback only): client lifecycle and service
stats.

Boundary (ADR-019):
- Every route requires the kb:admin scope on the authenticated client.
- Mutations write audit records (LAW-014); tokens are returned ONCE.
- No graph or raw database operation is exposed, and nothing here is
  registered as an MCP tool — the MCP surface stays read/write knowledge
  operations only.
"""

from __future__ import annotations

from typing import Any

from contracts.clients import ROLE_SCOPES
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from ecosystem_kb.api.dependencies import authenticated_client
from ecosystem_kb.core.exceptions import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)

router = APIRouter(tags=["admin"])

VALID_ROLES = sorted(ROLE_SCOPES.keys())


class ClientCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(min_length=3, max_length=60)
    client_name: str | None = None
    role: str = Field(default="contributor")


def _require_admin(request: Request) -> None:
    client = request.state.client
    if "kb:admin" not in client.scopes:
        raise AuthorizationError("kb:admin scope required for administrative operations")


def _client_row(row) -> dict[str, Any]:
    return {
        "client_id": row.client_id,
        "client_name": row.client_name,
        "scopes": list(row.scopes or []),
        "allowed_namespaces": list(row.allowed_namespaces or []),
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "rotated_at": row.rotated_at.isoformat() if row.rotated_at else None,
    }


@router.post("/admin/clients")
async def create_client(
    body: ClientCreateRequest,
    request: Request,
    _client=Depends(authenticated_client),
) -> dict[str, Any]:
    _require_admin(request)
    if body.role not in ROLE_SCOPES:
        raise ValidationError(f"unknown role '{body.role}'; choose one of: {', '.join(VALID_ROLES)}")
    repos = request.app.state.repos
    settings = request.app.state.settings
    if await repos.clients.get(body.client_id) is not None:
        raise ConflictError(f"client '{body.client_id}' already exists")
    scopes = ROLE_SCOPES[body.role]
    namespaces = [settings.default_namespace]
    token = await repos.clients.register(
        body.client_id, body.client_name or body.client_id, scopes, namespaces
    )
    await repos.audit.record(
        actor=request.state.client.client_id,
        action="client.created",
        resource_type="client",
        resource_id=body.client_id,
        reason=f"role={body.role}",
    )
    return {"client_id": body.client_id, "scopes": scopes, "token": token}


@router.get("/admin/clients")
async def list_clients(
    request: Request,
    _client=Depends(authenticated_client),
) -> dict[str, Any]:
    _require_admin(request)
    rows = await request.app.state.repos.clients.list_clients()
    return {"clients": [_client_row(r) for r in rows]}


@router.post("/admin/clients/{client_id}/revoke")
async def revoke_client(
    client_id: str,
    request: Request,
    _client=Depends(authenticated_client),
) -> dict[str, Any]:
    _require_admin(request)
    row = await request.app.state.repos.clients.set_status(
        client_id, "revoked", actor=request.state.client.client_id
    )
    if row is None:
        raise NotFoundError(f"no such client: {client_id}")
    return {"client_id": client_id, "status": row.status}


@router.post("/admin/clients/{client_id}/rotate")
async def rotate_client(
    client_id: str,
    request: Request,
    _client=Depends(authenticated_client),
) -> dict[str, Any]:
    _require_admin(request)
    token = await request.app.state.repos.clients.rotate_token(
        client_id, actor=request.state.client.client_id
    )
    if token is None:
        raise NotFoundError(f"no such client: {client_id}")
    return {"client_id": client_id, "token": token}


@router.get("/admin/stats")
async def service_stats(
    request: Request,
    _client=Depends(authenticated_client),
) -> dict[str, Any]:
    """Knowledge + queue snapshot for `kb status` (plan §H observability)."""
    _require_admin(request)
    repos = request.app.state.repos
    counts = await repos.cp.table_counts()
    queue = await repos.events.queue_stats()
    return {
        "knowledge": {
            "entities": counts.get("kb_entities", 0),
            "provenance_links": counts.get("kb_provenance_links", 0),
            "events": counts.get("kb_events", 0),
        },
        "queue": queue,
    }
