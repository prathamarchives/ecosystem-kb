"""Maintenance routes: report, proposals, approve, apply, inspect."""

from __future__ import annotations

from contracts.maintenance import MaintenanceReport, ProposalDecision
from contracts.responses import ErrorBody
from fastapi import APIRouter, Depends, Request

from ecosystem_kb.api.dependencies import authenticated_client
from ecosystem_kb.core.exceptions import AuthorizationError, NamespaceError

router = APIRouter(tags=["maintenance"])


def _require_maintain(request, namespace: str | None) -> str:
    client = request.state.client
    if "kb:maintain" not in client.scopes:
        raise AuthorizationError("kb:maintain scope required")
    ns = namespace or client.resolved_namespace(
        request.app.state.settings.default_namespace
    )
    if ns not in client.allowed_namespaces:
        raise NamespaceError(f"namespace '{ns}' not allowed")
    return ns


@router.get(
    "/maintenance/report",
    response_model=MaintenanceReport,
    responses={403: {"model": ErrorBody}},
)
async def maintenance_report(
    request: Request,
    namespace: str | None = None,
    _client=Depends(authenticated_client),
) -> MaintenanceReport:
    maintenance = request.app.state.maintenance
    ns = _require_maintain(request, namespace)
    return await maintenance.run_audit(ns)


@router.post("/maintenance/proposals/{action_id}/decide")
async def decide_proposal(
    request: Request,
    action_id: str,
    decision: ProposalDecision,
    _client=Depends(authenticated_client),
):
    maintenance = request.app.state.maintenance
    _require_maintain(request, None)
    if decision.action_id != action_id:
        decision = decision.model_copy(update={"action_id": action_id})
    action = await maintenance.decide_proposal(decision)
    return {
        "action_id": action.action_id,
        "operation": action.operation.value,
        "approval_status": action.approval_status.value,
        "execution_status": action.execution_status.value,
    }


@router.post("/maintenance/proposals/{action_id}/apply")
async def apply_proposal(
    request: Request,
    action_id: str,
    _client=Depends(authenticated_client),
):
    """Apply an approved proposal. Idempotent: a completed action is a no-op."""
    maintenance = request.app.state.maintenance
    client = request.state.client
    _require_maintain(request, None)
    action = await maintenance.apply_action(action_id, operator=client.client_id)
    return {
        "action_id": action.action_id,
        "operation": action.operation.value,
        "approval_status": action.approval_status.value,
        "execution_status": action.execution_status.value,
    }


@router.get("/maintenance/proposals/{action_id}")
async def get_proposal(
    request: Request,
    action_id: str,
    _client=Depends(authenticated_client),
):
    maintenance = request.app.state.maintenance
    _require_maintain(request, None)
    action = await maintenance.get_action(action_id)
    return {
        "action_id": action.action_id,
        "operation": action.operation.value,
        "target": action.target,
        "candidates": action.candidates,
        "evidence": action.evidence,
        "approval_status": action.approval_status.value,
        "execution_status": action.execution_status,
    }
