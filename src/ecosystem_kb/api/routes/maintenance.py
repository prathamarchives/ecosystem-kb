"""Maintenance routes: report, proposals, approve."""

from __future__ import annotations

from contracts.maintenance import MaintenanceReport, ProposalDecision
from contracts.responses import ErrorBody
from fastapi import APIRouter, Depends, Request

from ecosystem_kb.api.dependencies import authenticated_client
from ecosystem_kb.api.errors import error_response

router = APIRouter(tags=["maintenance"])


@router.get(
    "/maintenance/report",
    response_model=MaintenanceReport,
    responses={403: {"model": ErrorBody}},
)
async def maintenance_report(request: Request, namespace: str | None = None, _client=Depends(authenticated_client)) -> MaintenanceReport:
    maintenance = request.app.state.maintenance
    client = request.state.client
    if "kb:maintain" not in client.scopes:
        return error_response(
            __import__(
                "ecosystem_kb.core.exceptions", fromlist=["AuthorizationError"]
            ).AuthorizationError("kb:maintain scope required")
        )
    ns = namespace or client.default_namespace
    if ns not in client.allowed_namespaces:
        return error_response(
            __import__(
                "ecosystem_kb.core.exceptions", fromlist=["NamespaceError"]
            ).NamespaceError(f"namespace '{ns}' not allowed")
        )
    return await maintenance.run_audit(ns)


@router.post("/maintenance/proposals/{action_id}/approve")
async def approve_proposal(request: Request, action_id: str, decision: ProposalDecision, _client=Depends(authenticated_client)):
    maintenance = request.app.state.maintenance
    client = request.state.client
    if "kb:maintain" not in client.scopes:
        return error_response(
            __import__(
                "ecosystem_kb.core.exceptions", fromlist=["AuthorizationError"]
            ).AuthorizationError("kb:maintain scope required")
        )
    try:
        action = await maintenance.decide_proposal(decision)
        if decision.decision.value == "approved":
            action = await maintenance.apply_action(action_id, operator=client.client_id)
        return {
            "action_id": action.action_id,
            "operation": action.operation.value,
            "approval_status": action.approval_status.value,
            "execution_status": action.execution_status.value,
        }
    except Exception as exc:
        return error_response(exc)
