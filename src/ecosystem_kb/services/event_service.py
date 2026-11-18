"""Service layer: one home for business logic, shared by REST and MCP.

The gateway owns authorization; services assume an authenticated client with
server-derived namespace. No route or tool talks to the graph or the control
plane directly (ADR-005).
"""

from __future__ import annotations

from datetime import UTC, datetime

from contracts.clients import AuthenticatedClient, AuthzAction
from contracts.events import EventReceipt, KnowledgeEvent
from contracts.search import (
    EntityContextResponse,
    ProvenanceResponse,
    SearchRequest,
    SearchResponse,
)

from ecosystem_kb.adapters.graphiti.ingestion import ingest_event, provenance_for
from ecosystem_kb.adapters.graphiti.reconciliation import episode_exists_for
from ecosystem_kb.adapters.graphiti.retrieval import entity_context, search
from ecosystem_kb.adapters.postgres.repositories import Repositories
from ecosystem_kb.core.config import KbSettings
from ecosystem_kb.core.exceptions import (
    AuthorizationError,
    NamespaceError,
    NotFoundError,
)
from ecosystem_kb.core.lifecycle import IngestionEventState
from ecosystem_kb.core.policies import IngestionPolicy


class KbServices:
    """All gateway operations. REST routes and MCP tools call these."""

    def __init__(
        self,
        repos: Repositories,
        graphiti,
        settings: KbSettings,
    ) -> None:
        self.repos = repos
        self.graphiti = graphiti
        self.settings = settings
        self.policy = IngestionPolicy(max_body_chars=settings.max_event_body_chars)

    # -- authorization helpers ------------------------------------------------

    def _authorize(self, client: AuthenticatedClient, action: AuthzAction) -> None:
        if not client.can(action):
            raise AuthorizationError(
                f"client '{client.client_id}' lacks scope for {action.value}"
            )

    def _namespace_for(self, client: AuthenticatedClient, requested: str | None) -> str:
        """Namespace comes from the authenticated client; a caller may only
        narrow within their allowed set, never escape it (SYS-S-003)."""
        if requested is None:
            return client.default_namespace
        if requested not in client.allowed_namespaces:
            raise NamespaceError(
                f"namespace '{requested}' is not available to client '{client.client_id}'"
            )
        return requested

    # -- events ----------------------------------------------------------------

    async def submit_event(
        self, client: AuthenticatedClient, event: KnowledgeEvent
    ) -> EventReceipt:
        """Validate → policy gate → ledger insert (idempotent) → enqueue job."""
        self._authorize(client, AuthzAction.SUBMIT_EVENT)
        namespace = self._namespace_for(client, None)

        flags = self.policy.check(event)  # raises SecretContentError on violation

        row, created = await self.repos.events.insert_event(
            event_id=str(event.event_id),
            idempotency_key=event.idempotency_key,
            schema_version=event.schema_version,
            source_id=event.source.system_id,
            source_revision=str(event.source.revision) if event.source.revision is not None else None,
            event_type=event.event_type,
            namespace=namespace,
            payload_reference=event.content.content_reference or "",
            content_hash=event.content.content_hash,
            occurred_at=event.timestamps.occurred_at,
            received_at=datetime.now(UTC),
        )
        if not created:
            # Idempotent replay: same receipt, no duplicate work (LAW-008).
            return EventReceipt(
                event_id=event.event_id,
                idempotency_key=event.idempotency_key,
                status=row.processing_status,
                duplicate=True,
                received_at=row.received_at,
                detail="event already ingested under this idempotency key",
            )
        if flags:
            await self.repos.events.force_status(row.id, IngestionEventState.QUARANTINED)
            await self.repos.reviews.enqueue(
                item_type="event_flag",
                item_reference=row.event_id,
                proposed_action="review_content_flags",
                reason="; ".join(flags),
            )
            return EventReceipt(
                event_id=event.event_id,
                idempotency_key=event.idempotency_key,
                status="quarantined",
                duplicate=False,
                received_at=row.received_at,
                detail="content flagged for review: " + "; ".join(flags),
            )

        await self.repos.events.enqueue_job(row.id)
        await self.repos.audit.record(
            actor=f"client:{client.client_id}",
            action="submit_event",
            resource_type="event",
            resource_id=str(event.event_id),
            reason=event.event_type,
            after_reference=event.source.canonical_uri,
        )
        return EventReceipt(
            event_id=event.event_id,
            idempotency_key=event.idempotency_key,
            status="queued",
            duplicate=False,
            received_at=row.received_at,
        )

    async def event_status(self, client: AuthenticatedClient, event_id: str):
        self._authorize(client, AuthzAction.SEARCH)
        row = await self.repos.events.get_by_event_id(event_id)
        if row is None:
            raise NotFoundError(f"event {event_id} not found")
        return row

    # -- retrieval ---------------------------------------------------------------

    async def search(
        self, client: AuthenticatedClient, request: SearchRequest
    ) -> SearchResponse:
        self._authorize(client, AuthzAction.SEARCH)
        namespace = self._namespace_for(client, request.namespace)
        return await search(self.graphiti, request, namespace)

    async def get_entity(
        self, client: AuthenticatedClient, canonical_key: str, namespace: str | None = None
    ) -> EntityContextResponse:
        self._authorize(client, AuthzAction.GET_ENTITY)
        ns = self._namespace_for(client, namespace)
        result = await entity_context(self.graphiti, canonical_key, ns)
        if result is None:
            raise NotFoundError(f"entity '{canonical_key}' not found in {ns}")
        return result

    async def get_provenance(
        self, client: AuthenticatedClient, reference: str
    ) -> ProvenanceResponse:
        """Trace a source reference through the ledger (LAW-002)."""
        self._authorize(client, AuthzAction.GET_PROVENANCE)
        history: list[dict] = []
        row = await self.repos.events.get_by_event_id(reference)
        if row is None:
            rows = []
        else:
            rows = [row]
        if not rows:
            raise NotFoundError(f"no provenance found for '{reference}'")
        for r in rows:
            history.append(
                {
                    "event_id": r.event_id,
                    "event_type": r.event_type,
                    "source_id": r.source_id,
                    "occurred_at": r.occurred_at.isoformat(),
                    "received_at": r.received_at.isoformat(),
                    "status": r.processing_status,
                }
            )
        return ProvenanceResponse(
            reference=reference,
            provenance=history[0],
            history=history,
        )

    # -- ingestion execution (worker path) --------------------------------------

    async def process_event(self, event_row_id: str) -> dict:
        """Worker entry: claim-safe processing of one ledger row through the graph.

        Reconciliation (ADR-008): if the graph already holds an episode for
        this idempotency key, do NOT re-ingest — mark complete.
        """
        from ecosystem_kb.adapters.postgres.models import KbEvent

        row = None
        async with self.repos.cp.session() as s:
            row = await s.get(KbEvent, event_row_id)
        if row is None:
            raise KeyError(event_row_id)

        # The ledger row sits at ACCEPTED after submit; the worker's claim
        # moves the JOB to processing, and the event follows the legal path
        # ACCEPTED -> QUEUED -> PROCESSING before graph writes.
        await self.repos.events.set_status(row.id, IngestionEventState.QUEUED)
        await self.repos.events.set_status(row.id, IngestionEventState.PROCESSING)

        if await episode_exists_for(self.graphiti, row.namespace, row.idempotency_key):
            await self.repos.events.set_status(row.id, IngestionEventState.GRAPH_WRITTEN)
            await self.repos.events.set_status(row.id, IngestionEventState.INDEX_VERIFIED)
            await self.repos.events.set_status(row.id, IngestionEventState.COMPLETED)
            return {"reconciled": True, "entities": 0, "edges": 0}

        # Rebuild the event envelope from the ledger row (body stored in payload_reference
        # is a URI; tests submit bodies — the worker stores the envelope JSON in payload).
        import json as _json

        payload = _json.loads(row.payload_reference or "{}")
        event = KnowledgeEvent(**payload) if payload else None
        if event is None:
            # Reference-only event: minimal envelope for episode creation.
            event = KnowledgeEvent(
                idempotency_key=row.idempotency_key,
                event_type=row.event_type,
                source={
                    "system_id": row.source_id,
                    "record_id": row.idempotency_key,
                    "canonical_uri": row.payload_reference or "unknown://reference",
                },
                timestamps={
                    "occurred_at": row.occurred_at.isoformat(),
                    "recorded_at": row.received_at.isoformat(),
                },
                content={"content_reference": row.payload_reference or None},
            )

        async def identity_sync(canonical_key, entity_type, name, uuid):
            await self.repos.identities.upsert_identity(
                canonical_key=canonical_key,
                entity_type=entity_type or "Concept",
                canonical_name=name,
                graphiti_uuid=uuid,
            )
            await self.repos.identities.add_alias(
                canonical_key, name, alias_source="extraction", confidence_basis="exact"
            )

        outcome = await ingest_event(self.graphiti, event, row.namespace, identity_sync)
        prov = provenance_for(event, outcome.episode_uuid)

        await self.repos.events.set_status(row.id, IngestionEventState.GRAPH_WRITTEN)
        await self.repos.events.set_status(row.id, IngestionEventState.INDEX_VERIFIED)
        await self.repos.events.set_status(row.id, IngestionEventState.COMPLETED)

        for q in outcome.edges_quarantined:
            await self.repos.reviews.enqueue(
                item_type="quarantined_edge",
                item_reference=f"{q.relation}:{q.source_name}->{q.target_name}",
                proposed_action="classify_or_reject",
                reason=q.reason,
            )
        await self.repos.audit.record(
            actor="worker:ingestion",
            action="ingest_event",
            resource_type="event",
            resource_id=row.event_id,
            reason=f"entities={outcome.entities_written} edges={outcome.edges_written} quarantined={len(outcome.edges_quarantined)}",
            after_reference=prov.summary(),
        )
        return {
            "reconciled": False,
            "entities": outcome.entities_written,
            "edges": outcome.edges_written,
            "quarantined": len(outcome.edges_quarantined),
        }

    async def store_event_payload(self, event_row_id: str, event: KnowledgeEvent) -> None:
        """Persist the full envelope on the ledger row for the worker."""
        import json as _json

        async with self.repos.cp.session() as s:
            row = await s.get(
                __import__(
                    "ecosystem_kb.adapters.postgres.models", fromlist=["KbEvent"]
                ).KbEvent,
                event_row_id,
            )
            if row is not None:
                row.payload_reference = _json.dumps(event.model_dump(mode="json"))
                await s.commit()
