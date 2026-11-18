"""Knowledge routes: search, entities, context, provenance."""

from __future__ import annotations

from contracts.responses import ErrorBody
from contracts.search import (
    EntityContextResponse,
    ProvenanceResponse,
    SearchRequest,
    SearchResponse,
)
from fastapi import APIRouter, Depends, Request

from ecosystem_kb.api.dependencies import authenticated_client

router = APIRouter(tags=["knowledge"])


@router.post(
    "/search",
    response_model=SearchResponse,
    responses={401: {"model": ErrorBody}, 403: {"model": ErrorBody}},
)
async def search(request: Request, body: SearchRequest, _client=Depends(authenticated_client)) -> SearchResponse:
    services = request.app.state.services
    client = request.state.client
    return await services.search(client, body)


@router.get(
    "/entities/{canonical_key}",
    response_model=EntityContextResponse,
    responses={404: {"model": ErrorBody}},
)
async def get_entity(
    request: Request,
    canonical_key: str,
    namespace: str | None = None,
    _client=Depends(authenticated_client),
) -> EntityContextResponse:
    services = request.app.state.services
    client = request.state.client
    return await services.get_entity(client, canonical_key, namespace)


@router.get(
    "/sources/{reference}",
    response_model=ProvenanceResponse,
    responses={404: {"model": ErrorBody}},
)
async def get_provenance(request: Request, reference: str, _client=Depends(authenticated_client)) -> ProvenanceResponse:
    services = request.app.state.services
    client = request.state.client
    return await services.get_provenance(client, reference)
