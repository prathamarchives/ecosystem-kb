"""Event routes: POST /v1/events, GET /v1/events/{id}."""

from __future__ import annotations

from uuid import UUID

from contracts.events import EventReceipt, KnowledgeEvent
from contracts.responses import ErrorBody
from fastapi import APIRouter, Depends, Request

from ecosystem_kb.api.dependencies import authenticated_client

router = APIRouter(tags=["events"])


@router.post(
    "/events",
    response_model=EventReceipt,
    responses={422: {"model": ErrorBody}, 401: {"model": ErrorBody}, 403: {"model": ErrorBody}},
)
async def submit_event(request: Request, event: KnowledgeEvent, _client=Depends(authenticated_client)) -> EventReceipt:
    services = request.app.state.services
    client = request.state.client
    receipt = await services.submit_event(client, event)
    # Persist the envelope for the worker before returning the receipt.
    row = await services.repos.events.get_by_event_id(str(event.event_id))
    if row is not None and not receipt.duplicate:
        await services.store_event_payload(row.id, event)
    return receipt


@router.get("/events/{event_id}")
async def event_status(request: Request, event_id: UUID, _client=Depends(authenticated_client)):
    services = request.app.state.services
    client = request.state.client
    try:
        row = await services.event_status(client, str(event_id))
    except Exception as exc:  # surfaced uniformly
        return error_response(exc)
    return {
        "event_id": row.event_id,
        "idempotency_key": row.idempotency_key,
        "event_type": row.event_type,
        "namespace": row.namespace,
        "status": row.processing_status,
        "occurred_at": row.occurred_at,
        "received_at": row.received_at,
    }
