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
    responses={
        409: {"model": ErrorBody},
        422: {"model": ErrorBody},
        401: {"model": ErrorBody},
        403: {"model": ErrorBody},
    },
)
async def submit_event(
    request: Request,
    event: KnowledgeEvent,
    _client=Depends(authenticated_client),
) -> EventReceipt:
    services = request.app.state.services
    client = request.state.client
    # Submit is atomic: ledger row + serialized envelope + job in ONE
    # transaction; no post-return persistence step exists to forget.
    return await services.submit_event(client, event)


@router.get("/events/{event_id}")
async def event_status(
    request: Request, event_id: UUID, _client=Depends(authenticated_client)
):
    services = request.app.state.services
    client = request.state.client
    row = await services.event_status(client, str(event_id))
    return {
        "event_id": row.event_id,
        "idempotency_key": row.idempotency_key,
        "event_type": row.event_type,
        "namespace": row.namespace,
        "status": row.processing_status,
        "occurred_at": row.occurred_at,
        "received_at": row.received_at,
    }
