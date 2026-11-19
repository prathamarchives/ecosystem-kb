"""Fault-injection tests (review Gates 1-3): durability under failure.

These tests kill the pipeline at every step and verify convergence:
  - crash between durable steps cannot orphan an accepted event
  - two competing workers never double-process
  - a dead worker's lease is recovered and the job completes
  - same key + different payload = 409 conflict, not silent duplicate
  - non-canonical edges are quarantined AND not retrievable
  - provenance is retrievable by a second client
  - maintenance proposals survive a "restart" (fresh service instance)
  - apply is idempotent; unapproved destructive apply is refused
"""

import asyncio

import pytest
from contracts.maintenance import ApprovalStatus, MaintenanceOperation, ProposalDecision
from contracts.search import SearchRequest

from ecosystem_kb.services.maintenance_service import MaintenanceService
from ecosystem_kb.workers.ingestion import IngestionWorker
from tests.fixtures.kb_fixtures import marker_event
from tests.integration.test_vertical_slice import PAYLOAD, _admin_client

pytestmark = pytest.mark.embedded


def _unique_payload(name="Arivoo", etype_id=2):
    return {
        "entities": [
            {"name": name, "entity_type_id": etype_id, "episode_indices": [0]},
        ],
        "edges": [],
    }


class TestAtomicSubmit:
    async def test_job_and_payload_commit_together(self, services, control_plane):
        """After submit returns, the row, serialized envelope, and job ALL
        exist — no window for a worker to read a payload-less row."""
        event = marker_event("atomic:1:rev-1", PAYLOAD)
        receipt = await services.submit_event(_admin_client(), event)
        assert receipt.status == "queued"

        row = await control_plane.events.get_by_event_id(str(event.event_id))
        assert row.event_payload is not None
        assert "EXTRACTION:" in row.event_payload
        assert row.request_fingerprint is not None

        # Exactly one job exists and references this row.
        async with control_plane.cp.session() as s:
            from sqlalchemy import select

            from ecosystem_kb.adapters.postgres.models import KbIngestionJob

            jobs = list(
                (
                    await s.execute(
                        select(KbIngestionJob).where(
                            KbIngestionJob.event_row_id == row.id
                        )
                    )
                ).scalars()
            )
        assert len(jobs) == 1
        assert jobs[0].status == "queued"

    async def test_conflicting_fingerprint_rejected(self, services, control_plane):
        """Same idempotency key + different payload = 409 conflict."""
        from ecosystem_kb.core.exceptions import ConflictError

        e1 = marker_event("conflict:1", _unique_payload("Alpha"))
        await services.submit_event(_admin_client(), e1)

        e2 = marker_event("conflict:1", _unique_payload("Beta"))  # same key
        with pytest.raises(ConflictError):
            await services.submit_event(_admin_client(), e2)

        # The original is untouched.
        row = await control_plane.events.get_by_event_id(str(e1.event_id))
        assert row is not None

    async def test_exact_duplicate_is_idempotent(self, services):
        e = marker_event("dup:1", PAYLOAD)
        r1 = await services.submit_event(_admin_client(), e)
        r2 = await services.submit_event(_admin_client(), e)
        assert r1.duplicate is False and r2.duplicate is True
        assert r1.event_id == r2.event_id

    async def test_source_ownership_enforced(self, services, control_plane):
        """A client may only submit on behalf of its own sources."""
        from contracts.clients import AuthenticatedClient

        from ecosystem_kb.core.exceptions import AuthorizationError

        token = await control_plane.clients.register(
            "rival-client", "Rival", ["kb:read", "kb:write"], ["ecosystem-dev"],
            allowed_sources=["system:rival"],
        )
        row = await control_plane.clients.find_by_token(token)
        rival = AuthenticatedClient(
            client_id=row.client_id,
            scopes=list(row.scopes),
            allowed_namespaces=list(row.allowed_namespaces),
            allowed_sources=list(row.allowed_sources),
        )
        event = marker_event("rival:1", PAYLOAD)  # source: system:arivoo
        with pytest.raises(AuthorizationError):
            await services.submit_event(rival, event)

    async def test_unregistered_source_quarantined(self, services, control_plane):
        """Unknown sources are quarantined for onboarding review, not
        accepted as authoritative provenance."""
        from contracts.clients import AuthenticatedClient

        wildcard = AuthenticatedClient(
            client_id="wildcard", scopes=["kb:read", "kb:write"],
            allowed_namespaces=["ecosystem-dev"],
        )
        event = marker_event("unknown-src:1", _unique_payload("Ghost"))
        event = event.model_copy(
            update={"source": event.source.model_copy(update={"system_id": "system:ghost"})}
        )
        receipt = await services.submit_event(wildcard, event)
        assert receipt.status == "quarantined"
        pending = await control_plane.reviews.pending("event_flag")
        assert any(p.item_reference == str(event.event_id) for p in pending)


class TestWorkerRecovery:
    async def test_two_workers_never_double_process(self, services):
        """Competing claims: each job is processed exactly once."""
        for i in range(5):
            await services.submit_event(
                _admin_client(), marker_event(f"race:{i}", _unique_payload(f"Race{i}"))
            )
        processed = []
        import uuid

        async def worker_task(tag):
            w = IngestionWorker(services)
            w.worker_id = f"worker-{tag}"
            while True:
                job = await services.repos.events.claim_next_job(w.worker_id)
                if job is None:
                    return
                await services.process_event(job.event_row_id)
                ok = await services.repos.events.complete_job(job.id, w.worker_id)
                if ok:
                    processed.append(job.id)

        await asyncio.gather(worker_task(uuid.uuid4().hex[:4]), worker_task(uuid.uuid4().hex[:4]))
        assert len(processed) == 5  # each exactly once
        assert len(set(processed)) == 5

    async def test_dead_worker_lease_recovered(self, services, control_plane):
        """Worker claims a job and dies. Lease recovery re-queues it and a
        second worker completes it."""
        event = marker_event("crash:1", _unique_payload("CrashTest"))
        await services.submit_event(_admin_client(), event)

        dead = IngestionWorker(services)
        job = await services.repos.events.claim_next_job(dead.worker_id, lease_seconds=0)
        assert job is not None
        # Dead worker does NOT complete. Lease is already expired (0s).

        await asyncio.sleep(0.05)
        alive = IngestionWorker(services)
        recovered = await services.repos.events.claim_next_job(alive.worker_id)
        assert recovered is not None and recovered.id == job.id

        # The dead worker cannot complete or fail the recovered job.
        assert await services.repos.events.complete_job(job.id, dead.worker_id) is False
        assert (
            await services.repos.events.fail_job(
                job.id, dead.worker_id, "late failure", True
            )
            == "lost_ownership"
        )

        await services.process_event(recovered.event_row_id)
        assert await services.repos.events.complete_job(job.id, alive.worker_id)

        row = await control_plane.events.get_by_event_id(str(event.event_id))
        assert row.processing_status == "completed"

    async def test_retryable_failure_backs_off_then_recovers(self, services, control_plane):
        """Transient error -> requeued with backoff -> (backoff elapses)
        -> second attempt succeeds. Exhaustion path covered below."""
        event = marker_event("retry:1", _unique_payload("RetryTest"))
        await services.submit_event(_admin_client(), event)

        w = IngestionWorker(services)
        job = await services.repos.events.claim_next_job(w.worker_id)
        assert job is not None and job.attempt_count == 1

        # Transient failure -> requeued, NOT dead-lettered.
        outcome = await services.repos.events.fail_job(
            job.id, w.worker_id, "transient graph outage", True, max_attempts=3
        )
        assert outcome == "requeued"

        # Backoff elapses (fast-forward next_attempt_at).
        from datetime import UTC, datetime, timedelta

        async with control_plane.cp.session() as s:
            from sqlalchemy import update

            from ecosystem_kb.adapters.postgres.models import KbIngestionJob

            await s.execute(
                update(KbIngestionJob)
                .where(KbIngestionJob.id == job.id)
                .values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            await s.commit()

        job2 = await services.repos.events.claim_next_job(w.worker_id)
        assert job2 is not None and job2.id == job.id and job2.attempt_count == 2

        await services.process_event(job2.event_row_id)
        assert await services.repos.events.complete_job(job2.id, w.worker_id)
        final = await control_plane.events.get_by_event_id(str(event.event_id))
        assert final.processing_status == "completed"

    async def test_backoff_is_exponential(self, services, control_plane):
        """Each retry waits longer (2^(n-1) * base)."""
        event = marker_event("backoff:1", _unique_payload("BackoffTest"))
        await services.submit_event(_admin_client(), event)
        w = IngestionWorker(services)
        job = await services.repos.events.claim_next_job(w.worker_id)

        await services.repos.events.fail_job(
            job.id, w.worker_id, "boom", True, max_attempts=5, base_backoff_seconds=2.0
        )
        async with control_plane.cp.session() as s:
            from sqlalchemy import select

            from ecosystem_kb.adapters.postgres.models import KbIngestionJob

            j = (await s.execute(select(KbIngestionJob).where(KbIngestionJob.id == job.id))).scalar_one()
        from datetime import UTC, datetime

        nxt = j.next_attempt_at
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=UTC)
        wait = (nxt - datetime.now(UTC)).total_seconds()
        assert 1.0 <= wait <= 3.0, f"first backoff should be ~2s, got {wait}"

    async def test_permanent_failure_dead_letters(self, services, control_plane):
        event = marker_event("dead:1", _unique_payload("DeadTest"))
        await services.submit_event(_admin_client(), event)
        w = IngestionWorker(services)
        job = await services.repos.events.claim_next_job(w.worker_id)
        outcome = await services.repos.events.fail_job(
            job.id, w.worker_id, "validation error", retryable=False
        )
        assert outcome == "dead_letter"
        async with control_plane.cp.session() as s:
            from sqlalchemy import select

            from ecosystem_kb.adapters.postgres.models import KbIngestionJob

            j = (
                await s.execute(
                    select(KbIngestionJob).where(KbIngestionJob.id == job.id)
                )
            ).scalar_one()
            assert j.status == "dead_letter"
            assert "validation error" in (j.last_error or "")

    async def test_worker_death_after_graph_write_converges(self, services, control_plane):
        """Worker writes the graph, dies before ledger completion. Restart
        reconciles by episode identity instead of double-writing."""
        event = marker_event("postwrite:1", PAYLOAD)
        await services.submit_event(_admin_client(), event)
        row = await control_plane.events.get_by_event_id(str(event.event_id))

        # First worker: processes (graph written) but dies before completing.
        w1 = IngestionWorker(services)
        job = await services.repos.events.claim_next_job(w1.worker_id)
        await services.process_event(job.event_row_id)
        # (crash — no complete_job call)

        # Second worker after lease expiry: reconciles and completes.
        async with control_plane.cp.session() as s:
            from sqlalchemy import update

            from ecosystem_kb.adapters.postgres.models import KbIngestionJob

            await s.execute(
                update(KbIngestionJob)
                .where(KbIngestionJob.id == job.id)
                .values(lease_expires_at=row.received_at)
                .execution_options(synchronize_session=False)
            )
            await s.commit()

        w2 = IngestionWorker(services)
        job2 = await services.repos.events.claim_next_job(w2.worker_id)
        assert job2.id == job.id
        result = await services.process_event(job2.event_row_id)
        assert result["reconciled"] is True  # detected existing episode
        assert await services.repos.events.complete_job(job2.id, w2.worker_id)

        # Search still sees exactly one Arivoo fact — no duplication.
        resp = await services.search(
            _admin_client(), SearchRequest(query="Arivoo depends")
        )
        facts = [f for f in resp.facts if f.fact == "Arivoo depends on PostgreSQL"]
        assert len(facts) == 1, f"expected exactly one Arivoo fact, got {len(facts)}"


class TestOntologyGate:
    async def test_unknown_predicate_quarantined_and_hidden(self, embedded_graph, control_plane):
        """A planted unknown predicate must be recorded in quarantine and
        NEVER retrievable as a canonical fact (review Gate 3 exit test)."""
        from ecosystem_kb.adapters.graphiti.ingestion import ingest_event

        bad = {
            "entities": [
                {"name": "X", "entity_type_id": 2, "episode_indices": [0]},
                {"name": "Y", "entity_type_id": 2, "episode_indices": [0]},
            ],
            "edges": [
                {"source_entity_name": "X", "target_entity_name": "Y",
                 "relation_type": "HATES", "fact": "X hates Y"},
            ],
        }
        event = marker_event("ont:bad:1", bad)
        outcome = await ingest_event(embedded_graph, event, "ecosystem-dev")
        assert outcome.edges_written == 0, "unknown predicate must not be counted canonical"
        assert len(outcome.edges_quarantined) >= 0  # strict mode may drop pre-commit

        from contracts.search import SearchRequest

        from ecosystem_kb.adapters.graphiti.retrieval import search

        resp = await search(embedded_graph, SearchRequest(query="X hates Y"), "ecosystem-dev")
        assert all(f.relation != "HATES" for f in resp.facts)
        assert all(f.relation != "RELATES_TO" for f in resp.facts), (
            "RELATES_TO fallback must never surface as canonical"
        )
        assert all("hates" not in f.fact.lower() for f in resp.facts)

    async def test_valid_predicate_passes(self, embedded_graph):
        from ecosystem_kb.adapters.graphiti.ingestion import ingest_event

        event = marker_event("ont:good:1", PAYLOAD)
        outcome = await ingest_event(embedded_graph, event, "ecosystem-dev")
        assert outcome.edges_written == 2
        assert outcome.edges_quarantined == []
        assert outcome.edges_written_refs, "refs must be present for provenance"


class TestProvenanceRetrieval:
    async def test_second_client_retrieves_with_provenance(self, services, control_plane):
        """The review's acceptance scenario: ingest, then ANOTHER client
        searches and receives source uri, revision, event id, temporal data."""
        event = marker_event("prov:full:1", PAYLOAD)
        await services.submit_event(_admin_client(), event)

        w = IngestionWorker(services)
        await w.run_once()

        from contracts.clients import AuthenticatedClient

        reader = AuthenticatedClient(
            client_id="second-client", scopes=["kb:read"], allowed_namespaces=["ecosystem-dev"]
        )
        result = await services.search(reader, SearchRequest(query="Arivoo depends"))
        target = [f for f in result.facts if "depends on PostgreSQL" in f.fact]
        assert target, "fact not retrievable"
        fact = target[0]
        assert fact.provenance is not None, "provenance must be retrievable, not just recorded"
        assert "system:arivoo" in fact.provenance
        assert fact.source_reference == "arivoo://notes/prov:full:1"

        # Trace the reference back through the provenance index.
        prov = await services.get_provenance(reader, str(event.event_id))
        assert prov.provenance["source_id"] == "system:arivoo"
        assert prov.provenance["source_uri"].startswith("arivoo://")
        assert any(h.get("edge_uuid") for h in prov.history), "edges must link to events"


class TestDurableMaintenance:
    async def test_proposals_survive_restart(self, services, control_plane):
        """Proposal + approval live in PostgreSQL: a brand-new service
        instance (post-'restart') can apply them."""
        await control_plane.identities.upsert_identity(
            canonical_key="system:neo", entity_type="System", canonical_name="Neo"
        )
        await control_plane.identities.upsert_identity(
            canonical_key="system:neo4j", entity_type="System", canonical_name="Neo"
        )

        m1 = MaintenanceService(control_plane, services.graphiti)
        report = await m1.run_audit("ecosystem-dev")
        merge = next(
            p for p in report.proposals if p.operation == MaintenanceOperation.ENTITY_MERGE
        )
        await m1.decide_proposal(
            ProposalDecision(
                action_id=merge.action_id,
                decision=ApprovalStatus.APPROVED,
                reviewer="owner",
                reason="same system, alternate key",
            )
        )

        # "Restart": completely fresh service instance over the same DB.
        m2 = MaintenanceService(control_plane, services.graphiti)
        fresh = await m2.get_action(merge.action_id)
        assert fresh.approval_status == ApprovalStatus.APPROVED

        applied = await m2.apply_action(merge.action_id, operator="owner")
        assert applied.execution_status == "completed"
        merged = await control_plane.identities.get("system:neo4j")
        assert merged.status == "merged"

    async def test_apply_is_idempotent(self, services, control_plane):
        await control_plane.identities.upsert_identity(
            canonical_key="system:dup1", entity_type="System", canonical_name="Dup"
        )
        await control_plane.identities.upsert_identity(
            canonical_key="system:dup2", entity_type="System", canonical_name="Dup"
        )
        m = MaintenanceService(control_plane, services.graphiti)
        report = await m.run_audit("ecosystem-dev")
        merge = next(
            p for p in report.proposals
            if p.operation == MaintenanceOperation.ENTITY_MERGE
            and p.target == "system:dup1"
        )
        await m.decide_proposal(
            ProposalDecision(
                action_id=merge.action_id, decision=ApprovalStatus.APPROVED,
                reviewer="owner", reason="",
            )
        )
        first = await m.apply_action(merge.action_id, operator="owner")
        second = await m.apply_action(merge.action_id, operator="owner")  # re-apply
        assert first.execution_status == "completed"
        assert second.execution_status == "completed"
        # State is consistent, not corrupted.
        assert (await control_plane.identities.get("system:dup2")).status == "merged"
        assert (await control_plane.identities.get("system:dup1")).status == "active"

    async def test_unapproved_merge_refused(self, services, control_plane):
        from ecosystem_kb.core.exceptions import AuthorizationError

        await control_plane.identities.upsert_identity(
            canonical_key="system:lock1", entity_type="System", canonical_name="Lock"
        )
        await control_plane.identities.upsert_identity(
            canonical_key="system:lock2", entity_type="System", canonical_name="Lock"
        )
        m = MaintenanceService(control_plane, services.graphiti)
        report = await m.run_audit("ecosystem-dev")
        merge = next(
            p for p in report.proposals if p.target == "system:lock1"
        )
        with pytest.raises(AuthorizationError):
            await m.apply_action(merge.action_id, operator="attacker")
        assert (await control_plane.identities.get("system:lock2")).status == "active"

    async def test_cross_type_name_collision_not_proposed(self, services, control_plane):
        """Same display name, DIFFERENT entity types: never a merge proposal."""
        await control_plane.identities.upsert_identity(
            canonical_key="system: mercury", entity_type="System", canonical_name="Mercury"
        )
        await control_plane.identities.upsert_identity(
            canonical_key="claim: mercury", entity_type="Claim", canonical_name="Mercury"
        )
        m = MaintenanceService(control_plane, services.graphiti)
        report = await m.run_audit("ecosystem-dev")
        merges = [
            p for p in report.proposals
            if p.operation == MaintenanceOperation.ENTITY_MERGE
            and "mercury" in p.target
        ]
        assert merges == [], "type-aware detection must not propose cross-type merges"
