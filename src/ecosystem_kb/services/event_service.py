"""Service layer: one home for business logic, shared by REST and MCP.

Gate 1b/2 (review P1) changes:
  - submit_event is ONE atomic control-plane transaction (ledger row + full
    serialized envelope + queue job) with fingerprint-based idempotency and
    conflict detection.
  - Source-ownership authorization: a client may only submit on behalf of
    sources it owns; unregistered sources are quarantined, not accepted as
    authoritative provenance.
  - process_event: lease-guarded execution with correct retry classification
    (transient graph/db errors retry; validation errors do not) and
    provenance link recording for retrievable provenance.
"""

from __future__ import annotations

import json
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
from ecosystem_kb.adapters.postgres.models import KbEvent
from ecosystem_kb.adapters.postgres.repositories import Repositories, SubmitOutcome
from ecosystem_kb.core.config import KbSettings
from ecosystem_kb.core.exceptions import (
    AuthorizationError,
    NamespaceError,
    NotFoundError,
)
from ecosystem_kb.core.lifecycle import IngestionEventState
from ecosystem_kb.core.policies import IngestionPolicy

# Transient infrastructure failures -> retry. Validation/policy failures are
# raised as ValueError-family and never retried.
_RETRYABLE = (ConnectionError, TimeoutError, OSError)


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
            return client.resolved_namespace(self.settings.default_namespace)
        if requested not in client.allowed_namespaces:
            raise NamespaceError(
                f"namespace '{requested}' is not available to client '{client.client_id}'"
            )
        return requested

    async def _authorize_source(self, client: AuthenticatedClient, event: KnowledgeEvent) -> bool:
        """Source-ownership boundary (review P1). Returns True when the source
        is registered and owned by this client. Unregistered sources are
        accepted only into quarantine under an explicit onboarding flow."""
        if not client.may_submit_for_source(event.source.system_id):
            raise AuthorizationError(
                f"client '{client.client_id}' is not permitted to submit events "
                f"on behalf of source '{event.source.system_id}'"
            )
        source = await self.repos.sources.get(event.source.system_id)
        return source is not None

    # -- events ----------------------------------------------------------------

    async def submit_event(
        self, client: AuthenticatedClient, event: KnowledgeEvent
    ) -> EventReceipt:
        """Policy gate → source check → ONE transaction (row+payload+job).

        Idempotency (LAW-008): same key + same fingerprint = duplicate receipt.
        Same key + different fingerprint = ConflictError (409), never a silent
        duplicate.
        """
        self._authorize(client, AuthzAction.SUBMIT_EVENT)
        namespace = self._namespace_for(client, None)

        flags = self.policy.check(event)  # raises SecretContentError on violation
        source_registered = await self._authorize_source(client, event)
        if not source_registered and not flags:
            flags = ["unregistered_source"]

        serialized = event.model_dump(mode="json")
        row, outcome = await self.repos.events.submit_event(
            event_id=str(event.event_id),
            idempotency_key=event.idempotency_key,
            schema_version=event.schema_version,
            source_id=event.source.system_id,
            source_revision=(
                str(event.source.revision) if event.source.revision is not None else None
            ),
            event_type=event.event_type,
            namespace=namespace,
            content_reference=event.content.content_reference or "",
            content_hash=event.content.content_hash,
            occurred_at=event.timestamps.occurred_at,
            received_at=datetime.now(UTC),
            serialized_event=json.dumps(serialized, sort_keys=True),
            initial_status=(
                IngestionEventState.QUARANTINED.value
                if flags
                else IngestionEventState.ACCEPTED.value
            ),
            flags={"flags": flags} if flags else None,
        )

        if outcome == SubmitOutcome.DUPLICATE:
            return EventReceipt(
                event_id=event.event_id,
                idempotency_key=event.idempotency_key,
                status=row.processing_status,
                duplicate=True,
                received_at=row.received_at,
                detail="event already ingested under this idempotency key",
            )
        # SubmitOutcome.CONFLICT raises ConflictError from the repository.

        if flags:
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
        return await search(
            self.graphiti,
            request,
            namespace,
            provenance_resolver=self._provenance_resolver,
        )

    async def get_entity(
        self, client: AuthenticatedClient, canonical_key: str, namespace: str | None = None
    ) -> EntityContextResponse:
        self._authorize(client, AuthzAction.GET_ENTITY)
        ns = self._namespace_for(client, namespace)
        result = await entity_context(
            self.graphiti,
            canonical_key,
            ns,
            provenance_resolver=self._provenance_resolver,
        )
        if result is None:
            raise NotFoundError(f"entity '{canonical_key}' not found in {ns}")
        return result

    async def _provenance_resolver(self, edge):
        """Retrievable provenance (Gate 4): look up kb_provenance_links by
        edge UUID and return (summary, source_reference)."""
        links = await self.repos.provenance.for_edge(edge.uuid)
        if not links:
            return None, None
        link = links[-1]  # most recent occurrence
        summary = (
            f"source={link.source_id}"
            + (f" rev={link.source_revision}" if link.source_revision else "")
            + (f" occurred={link.occurred_at.isoformat()}" if link.occurred_at else "")
            + f" event={link.event_id[:8]}"
            + (f" ontology={link.ontology_version}" if link.ontology_version else "")
        )
        return summary, link.source_uri

    async def get_provenance(self, client: AuthenticatedClient, reference: str) -> ProvenanceResponse:
        """Trace any reference (event id, source uri, edge uuid) through the
        provenance index (LAW-002)."""
        self._authorize(client, AuthzAction.GET_PROVENANCE)
        history: list[dict] = []

        # By event id (ledger row first for status)
        row = await self.repos.events.get_by_event_id(reference)
        links: list = []
        if row is not None:
            links = await self.repos.provenance.for_event(reference)
            history.append(
                {
                    "event_id": row.event_id,
                    "event_type": row.event_type,
                    "source_id": row.source_id,
                    "occurred_at": row.occurred_at.isoformat(),
                    "received_at": row.received_at.isoformat(),
                    "status": row.processing_status,
                }
            )
        else:
            # By edge or entity uuid
            links = await self.repos.provenance.for_edge(reference)
            if not links:
                links = await self.repos.provenance.for_entity(reference)
            if not links:
                raise NotFoundError(f"no provenance found for '{reference}'")

        for link in links:
            history.append(
                {
                    "event_id": link.event_id,
                    "idempotency_key": link.idempotency_key,
                    "episode_uuid": link.episode_uuid,
                    "source_id": link.source_id,
                    "source_uri": link.source_uri,
                    "source_revision": link.source_revision,
                    "content_hash": link.content_hash,
                    "occurred_at": link.occurred_at.isoformat() if link.occurred_at else None,
                    "entity_uuid": link.entity_uuid,
                    "edge_uuid": link.edge_uuid,
                    "fact": link.fact,
                    "ontology_version": link.ontology_version,
                }
            )
        # The head record is the richest provenance entry (a link, not the
        # bare ledger row), so every consumer sees source_uri etc.
        head = next((h for h in history if h.get("source_uri")), history[0])
        return ProvenanceResponse(reference=reference, provenance=head, history=history)

    # -- ingestion execution (worker path) --------------------------------------

    async def process_event(self, event_row_id: str) -> dict:
        """Worker entry: process one ledger row through the graph.

        Reconciliation (ADR-008): if the graph already holds an episode for
        this idempotency key, do NOT re-ingest — mark complete.
        """
        async with self.repos.cp.session() as s:
            row = await s.get(KbEvent, event_row_id)
        if row is None:
            raise KeyError(event_row_id)

        # Legal lifecycle path (idempotent under worker races: a second
        # worker reconciling a completed/completing row skips ahead).
        status = row.processing_status
        if status in (IngestionEventState.COMPLETED.value, IngestionEventState.GRAPH_WRITTEN.value):
            pass  # reconciled or mid-completion elsewhere
        else:
            if status == IngestionEventState.ACCEPTED.value:
                await self.repos.events.set_status(row.id, IngestionEventState.QUEUED)
                status = IngestionEventState.QUEUED.value
            if status == IngestionEventState.QUEUED.value:
                await self.repos.events.set_status(row.id, IngestionEventState.PROCESSING)

        if await episode_exists_for(self.graphiti, row.namespace, row.idempotency_key):
            await self._advance_to_completed(row.id)
            return {"reconciled": True, "entities": 0, "edges": 0}

        # The full envelope is persisted atomically with the row (Gate 1b).
        payload = json.loads(row.event_payload or "{}")
        if payload:
            event = KnowledgeEvent(**payload)
        else:
            # Legacy/reference-only rows: minimal envelope.
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

        outcome = await ingest_event(
            self.graphiti, event, row.namespace, identity_sync=identity_sync
        )
        prov = provenance_for(event, outcome.episode_uuid)

        # Retrievable provenance (Gate 4): one link per written edge/entity.
        base = {
            "namespace": row.namespace,
            "event_id": row.event_id,
            "idempotency_key": row.idempotency_key,
            "episode_uuid": outcome.episode_uuid,
            "source_id": event.source.system_id,
            "source_uri": event.source.canonical_uri,
            "source_revision": (
                str(event.source.revision) if event.source.revision is not None else None
            ),
            "content_hash": event.content.content_hash,
            "occurred_at": event.timestamps.occurred_at,
            "received_at": event.timestamps.recorded_at,
            "ontology_version": prov.ontology_version,
        }
        links = []
        for edge in outcome.edges_written_refs:
            links.append({**base, "edge_uuid": edge.uuid, "fact": edge.fact})
        for entity_uuid in outcome.entities_written_refs:
            links.append({**base, "entity_uuid": entity_uuid})
        await self.repos.provenance.record_batch(links)

        await self._advance_to_completed(row.id)

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
            reason=(
                f"entities={outcome.entities_written} "
                f"edges={outcome.edges_written} "
                f"quarantined={len(outcome.edges_quarantined)}"
            ),
            after_reference=prov.summary(),
        )
        return {
            "reconciled": False,
            "entities": outcome.entities_written,
            "edges": outcome.edges_written,
            "quarantined": len(outcome.edges_quarantined),
        }

    async def _advance_to_completed(self, row_id: str) -> None:
        """GRAPH_WRITTEN -> INDEX_VERIFIED -> COMPLETED, tolerating a parallel
        worker that already advanced some steps."""
        from ecosystem_kb.core.lifecycle import can_transition_ingestion

        async with self.repos.cp.session() as s:
            from ecosystem_kb.adapters.postgres.models import KbEvent

            current = IngestionEventState(
                (await s.get(KbEvent, row_id)).processing_status
            )
        for target in (
            IngestionEventState.GRAPH_WRITTEN,
            IngestionEventState.INDEX_VERIFIED,
            IngestionEventState.COMPLETED,
        ):
            if current == target:
                continue
            if can_transition_ingestion(current, target):
                await self.repos.events.set_status(row_id, target)
                current = target

    def is_retryable(self, exc: Exception) -> bool:
        """Correct classification (review P1): infrastructure errors retry,
        validation/policy errors dead-letter immediately."""
        if isinstance(exc, _RETRYABLE):
            return True
        # Any redis/network-flavored exception is transient.
        name = type(exc).__module__ + "." + type(exc).__name__
        return "redis" in name.lower() or "asyncio" in name.lower() and "Timeout" in type(exc).__name__
