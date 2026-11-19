"""Async control-plane repositories.

Gate 1b (review P1):
  - submit is ONE transaction: ledger row + full envelope payload + job.
  - idempotency compares request fingerprints: same key + same payload is a
    duplicate; same key + different payload is a CONFLICT.
  - source-ownership authorization (client.allowed_sources).
Gate 2 (review P1):
  - atomic job claim via UPDATE...RETURNING (SKIP LOCKED on Postgres).
  - FIFO by created_at/next_attempt_at, not UUID order.
  - lease recovery: expired processing leases are re-queued on claim.
  - ownership-guarded completion; bounded backoff; dead-letter state.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ecosystem_kb.adapters.postgres.models import (
    ALL_TABLES,
    Base,
    KbAlias,
    KbAuditLog,
    KbClient,
    KbEntityIdentity,
    KbEvent,
    KbIngestionJob,
    KbMaintenanceAction,
    KbProvenanceLink,
    KbReviewQueue,
    KbSource,
)
from ecosystem_kb.core.exceptions import ConflictError
from ecosystem_kb.core.lifecycle import IngestionEventState, transition_ingestion


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def request_fingerprint(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()


class SubmitOutcome(StrEnum):
    CREATED = "created"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"


class ControlPlane:
    """One engine + session factory over the control plane."""

    def __init__(self, database_url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(database_url, echo=False)
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def drop_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    async def dispose(self) -> None:
        await self.engine.dispose()

    def session(self) -> AsyncSession:
        return self.sessionmaker()

    async def table_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        async with self.session() as s:
            for table in ALL_TABLES:
                count = await s.scalar(select(func.count()).select_from(table))
                out[table.__tablename__] = int(count or 0)
        return out


class EventRepository:
    """kb_events + kb_ingestion_jobs: the ledger and the queue."""

    def __init__(self, cp: ControlPlane) -> None:
        self.cp = cp

    async def submit_event(
        self,
        *,
        event_id: str,
        idempotency_key: str,
        schema_version: str,
        source_id: str,
        source_revision: str | None,
        event_type: str,
        namespace: str,
        content_reference: str,
        content_hash: str | None,
        occurred_at: datetime,
        received_at: datetime,
        serialized_event: str,
        initial_status: str = IngestionEventState.ACCEPTED.value,
        flags: dict | None = None,
    ) -> tuple[KbEvent, SubmitOutcome]:
        """Atomic acceptance: ledger row + full envelope + job in ONE commit.

        Returns (row, outcome). DUPLICATE = same key + same fingerprint.
        CONFLICT = same key + different payload (caller surfaces 409).
        """
        fingerprint = request_fingerprint(serialized_event)
        async with self.cp.session() as s:
            async with s.begin():
                existing = await s.scalar(
                    select(KbEvent).where(KbEvent.idempotency_key == idempotency_key)
                )
                if existing is not None:
                    if existing.request_fingerprint == fingerprint:
                        return existing, SubmitOutcome.DUPLICATE
                    raise ConflictError(
                        f"idempotency key '{idempotency_key}' was already used "
                        "for a different payload"
                    )
                row = KbEvent(
                    event_id=event_id,
                    idempotency_key=idempotency_key,
                    schema_version=schema_version,
                    source_id=source_id,
                    source_revision=source_revision,
                    event_type=event_type,
                    namespace=namespace,
                    payload_reference=content_reference,
                    event_payload=serialized_event,
                    request_fingerprint=fingerprint,
                    content_hash=content_hash,
                    occurred_at=occurred_at,
                    received_at=received_at,
                    processing_status=initial_status,
                    flags=flags or {},
                )
                s.add(row)
                await s.flush()
                s.add(KbIngestionJob(event_row_id=row.id, status="queued"))
                await s.flush()
            await s.refresh(row)
            return row, SubmitOutcome.CREATED

    async def get_by_event_id(self, event_id: str) -> KbEvent | None:
        async with self.cp.session() as s:
            return await s.scalar(select(KbEvent).where(KbEvent.event_id == event_id))

    async def get_by_idempotency_key(self, key: str) -> KbEvent | None:
        async with self.cp.session() as s:
            return await s.scalar(select(KbEvent).where(KbEvent.idempotency_key == key))

    async def set_status(self, event_row_id: str, target: IngestionEventState) -> None:
        from ecosystem_kb.core.lifecycle import can_transition_ingestion

        async with self.cp.session() as s:
            row = await s.get(KbEvent, event_row_id)
            if row is None:
                raise KeyError(event_row_id)
            current = IngestionEventState(row.processing_status)
            # Tolerant under worker races: a same-state or already-advanced
            # row is a no-op, not an error. Illegal BACKWARD moves still raise.
            if current == target:
                return
            if not can_transition_ingestion(current, target):
                # Another worker advanced it further; accept if terminal-bound.
                if target in (
                    IngestionEventState.GRAPH_WRITTEN,
                    IngestionEventState.INDEX_VERIFIED,
                    IngestionEventState.COMPLETED,
                ) and current in (
                    IngestionEventState.GRAPH_WRITTEN,
                    IngestionEventState.INDEX_VERIFIED,
                    IngestionEventState.COMPLETED,
                ):
                    return
                row.processing_status = transition_ingestion(current, target).value
            else:
                row.processing_status = transition_ingestion(current, target).value
            await s.commit()

    async def force_status(self, event_row_id: str, target: IngestionEventState) -> None:
        """Maintenance path: set without transition validation (quarantine release)."""
        async with self.cp.session() as s:
            row = await s.get(KbEvent, event_row_id)
            if row is None:
                raise KeyError(event_row_id)
            row.processing_status = target.value
            await s.commit()

    # -- job queue (Gate 2) ----------------------------------------------------

    async def queue_stats(self) -> dict[str, int]:
        """Queue observability (plug-and-play plan §H): depth per status plus
        the age of the oldest queued job."""
        async with self.cp.session() as s:
            rows = await s.execute(
                select(KbIngestionJob.status, func.count()).group_by(KbIngestionJob.status)
            )
            stats = {status: count for status, count in rows.all()}
            stats.setdefault("queued", 0)
            oldest = await s.scalar(
                select(func.min(KbIngestionJob.created_at)).where(KbIngestionJob.status == "queued")
            )
            if oldest is not None:
                stats["oldest_queued_age_seconds"] = int(
                    (datetime.now(UTC) - oldest).total_seconds()
                )
            return stats

    async def claim_next_job(
        self, worker_id: str, lease_seconds: int = 300
    ) -> KbIngestionJob | None:
        """Atomic claim.

        1. Recover expired leases: processing jobs past lease_expires_at are
           re-queued (crashed workers lose their jobs).
        2. Claim the oldest visible queued job atomically via
           UPDATE ... RETURNING. On Postgres the candidate CTE uses
           FOR UPDATE SKIP LOCKED so competing workers never take the same
           row; on SQLite single-writer transactions keep the claim atomic
           for our test workloads.
        """
        now = datetime.now(UTC)
        async with self.cp.session() as s:
            # Lease-recovery sweep first (idempotent, cheap).
            await s.execute(
                update(KbIngestionJob)
                .where(
                    KbIngestionJob.status == "processing",
                    KbIngestionJob.lease_expires_at.is_not(None),
                    KbIngestionJob.lease_expires_at < now,
                )
                .values(
                    status="queued",
                    worker_id=None,
                    lease_expires_at=None,
                    next_attempt_at=now,
                )
            )
            await s.commit()

        is_pg = self.cp.engine.dialect.name == "postgresql"
        if is_pg:
            claim_sql = text(
                """
                WITH candidate AS (
                    SELECT id FROM kb_ingestion_jobs
                    WHERE status = 'queued' AND next_attempt_at <= :now
                    ORDER BY created_at, id
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE kb_ingestion_jobs j
                SET status = 'processing',
                    worker_id = :worker_id,
                    attempt_count = j.attempt_count + 1,
                    started_at = :now,
                    lease_expires_at = :lease_until
                FROM candidate
                WHERE j.id = candidate.id
                RETURNING j.id
                """
            )
            params = {
                "now": now,
                "worker_id": worker_id,
                "lease_until": now + timedelta(seconds=lease_seconds),
            }
            async with self.cp.session() as s:
                result = await s.execute(claim_sql, params)
                job_id = result.scalar()
                await s.commit()
                if job_id is None:
                    return None
                return await s.get(KbIngestionJob, job_id)

        # SQLite path: one transaction selects and flips the oldest job.
        async with self.cp.session() as s:
            async with s.begin():
                job = (
                    await s.execute(
                        select(KbIngestionJob)
                        .where(
                            KbIngestionJob.status == "queued",
                            KbIngestionJob.next_attempt_at <= now,
                        )
                        .order_by(KbIngestionJob.created_at, KbIngestionJob.id)
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if job is None:
                    return None
                job.status = "processing"
                job.worker_id = worker_id
                job.attempt_count += 1
                job.started_at = now
                job.lease_expires_at = now + timedelta(seconds=lease_seconds)
            await s.refresh(job)
            return job

    async def extend_lease(self, job_id: str, worker_id: str, lease_seconds: int = 300) -> bool:
        """Heartbeat: only the owning worker can extend its lease."""
        now = datetime.now(UTC)
        async with self.cp.session() as s:
            result = await s.execute(
                update(KbIngestionJob)
                .where(
                    KbIngestionJob.id == job_id,
                    KbIngestionJob.worker_id == worker_id,
                    KbIngestionJob.status == "processing",
                )
                .values(lease_expires_at=now + timedelta(seconds=lease_seconds))
            )
            await s.commit()
            return result.rowcount > 0

    async def complete_job(self, job_id: str, worker_id: str) -> bool:
        """Ownership-guarded completion: a stale worker cannot complete a job
        that was recovered and finished by someone else."""
        async with self.cp.session() as s:
            result = await s.execute(
                update(KbIngestionJob)
                .where(
                    KbIngestionJob.id == job_id,
                    KbIngestionJob.worker_id == worker_id,
                    KbIngestionJob.status == "processing",
                )
                .values(
                    status="completed",
                    completed_at=datetime.now(UTC),
                    lease_expires_at=None,
                )
            )
            await s.commit()
            return result.rowcount > 0

    async def fail_job(
        self,
        job_id: str,
        worker_id: str,
        error: str,
        retryable: bool,
        max_attempts: int = 5,
        base_backoff_seconds: float = 2.0,
    ) -> str:
        """Ownership-guarded failure. Returns 'requeued' | 'dead_letter' | 'lost_ownership'.

        Exponential backoff: attempt n waits base * 2^(n-1) seconds.
        Exhausted or non-retryable jobs go to 'dead_letter' (distinct from
        'failed'), surfaced by ops tooling.
        """
        async with self.cp.session() as s:
            job = await s.get(KbIngestionJob, job_id)
            if job is None:
                raise KeyError(job_id)
            if job.worker_id != worker_id or job.status != "processing":
                return "lost_ownership"
            job.last_error = error[-2000:]
            if retryable and job.attempt_count < max_attempts:
                job.status = "queued"
                backoff = base_backoff_seconds * (2 ** (job.attempt_count - 1))
                job.next_attempt_at = datetime.now(UTC) + timedelta(seconds=backoff)
                job.lease_expires_at = None
                job.worker_id = None
                outcome = "requeued"
            else:
                job.status = "dead_letter"
                job.lease_expires_at = None
                outcome = "dead_letter"
            await s.commit()
            return outcome


class SourceRepository:
    def __init__(self, cp: ControlPlane) -> None:
        self.cp = cp

    async def upsert(self, **values) -> KbSource:
        async with self.cp.session() as s:
            row = await s.scalar(
                select(KbSource).where(KbSource.source_id == values["source_id"])
            )
            if row is None:
                row = KbSource(**values)
                s.add(row)
            else:
                for k, v in values.items():
                    setattr(row, k, v)
                row.updated_at = datetime.now(UTC)
            await s.commit()
            await s.refresh(row)
            return row

    async def get(self, source_id: str) -> KbSource | None:
        async with self.cp.session() as s:
            return await s.scalar(select(KbSource).where(KbSource.source_id == source_id))


class IdentityRepository:
    def __init__(self, cp: ControlPlane) -> None:
        self.cp = cp

    async def upsert_identity(
        self,
        canonical_key: str,
        entity_type: str,
        canonical_name: str,
        graphiti_uuid: str | None = None,
    ) -> KbEntityIdentity:
        async with self.cp.session() as s:
            row = await s.scalar(
                select(KbEntityIdentity).where(KbEntityIdentity.canonical_key == canonical_key)
            )
            if row is None:
                row = KbEntityIdentity(
                    canonical_key=canonical_key,
                    entity_type=entity_type,
                    canonical_name=canonical_name,
                    graphiti_uuid=graphiti_uuid,
                )
                s.add(row)
            else:
                row.canonical_name = canonical_name
                if graphiti_uuid:
                    row.graphiti_uuid = graphiti_uuid
                row.updated_at = datetime.now(UTC)
            await s.commit()
            await s.refresh(row)
            return row

    async def get(self, canonical_key: str) -> KbEntityIdentity | None:
        async with self.cp.session() as s:
            return await s.scalar(
                select(KbEntityIdentity).where(KbEntityIdentity.canonical_key == canonical_key)
            )

    async def add_alias(
        self,
        canonical_key: str,
        alias_value: str,
        alias_source: str = "extraction",
        confidence_basis: str = "normalized",
    ) -> None:
        async with self.cp.session() as s:
            exists = await s.scalar(
                select(KbAlias).where(
                    KbAlias.canonical_key == canonical_key,
                    KbAlias.alias_value == alias_value,
                )
            )
            if exists is None:
                s.add(
                    KbAlias(
                        canonical_key=canonical_key,
                        alias_value=alias_value,
                        alias_source=alias_source,
                        confidence_basis=confidence_basis,
                    )
                )
                await s.commit()

    async def aliases_for(self, canonical_key: str) -> list[KbAlias]:
        async with self.cp.session() as s:
            res = await s.scalars(
                select(KbAlias).where(KbAlias.canonical_key == canonical_key)
            )
            return list(res)

    async def find_by_name(self, name: str) -> list[KbEntityIdentity]:
        async with self.cp.session() as s:
            by_name = await s.scalars(
                select(KbEntityIdentity).where(KbEntityIdentity.canonical_name == name)
            )
            keys = {r.canonical_key for r in by_name}
            alias_keys = await s.scalars(
                select(KbAlias.canonical_key).where(KbAlias.alias_value == name)
            )
            keys |= set(alias_keys)
            if not keys:
                return []
            rows = await s.scalars(
                select(KbEntityIdentity).where(KbEntityIdentity.canonical_key.in_(keys))
            )
            return list(rows)


class AuditRepository:
    """Append-only (LAW-014). No update/delete methods exist on purpose."""

    def __init__(self, cp: ControlPlane) -> None:
        self.cp = cp

    async def record(
        self,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: str,
        reason: str = "",
        before_reference: str | None = None,
        after_reference: str | None = None,
    ) -> None:
        async with self.cp.session() as s:
            s.add(
                KbAuditLog(
                    actor=actor,
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    reason=reason,
                    before_reference=before_reference,
                    after_reference=after_reference,
                )
            )
            await s.commit()

    async def for_resource(self, resource_type: str, resource_id: str) -> list[KbAuditLog]:
        async with self.cp.session() as s:
            res = await s.scalars(
                select(KbAuditLog)
                .where(
                    KbAuditLog.resource_type == resource_type,
                    KbAuditLog.resource_id == resource_id,
                )
                .order_by(KbAuditLog.occurred_at)
            )
            return list(res)


class ClientRepository:
    def __init__(self, cp: ControlPlane) -> None:
        self.cp = cp

    async def register(
        self,
        client_id: str,
        client_name: str,
        scopes: list[str],
        allowed_namespaces: list[str],
        allowed_sources: list[str] | None = None,
    ) -> str:
        """Creates the client and returns the plaintext token ONCE.
        allowed_sources: source_ids this client may submit on behalf of;
        ['*'] means any (legacy/dev clients only)."""
        token = secrets.token_urlsafe(32)
        async with self.cp.session() as s:
            s.add(
                KbClient(
                    client_id=client_id,
                    client_name=client_name,
                    token_hash=hash_token(token),
                    scopes=scopes,
                    allowed_namespaces=allowed_namespaces,
                    allowed_sources=allowed_sources or ["*"],
                )
            )
            await s.commit()
        return token

    async def find_by_token(self, token: str) -> KbClient | None:
        async with self.cp.session() as s:
            return await s.scalar(
                select(KbClient).where(KbClient.token_hash == hash_token(token))
            )

    async def get(self, client_id: str) -> KbClient | None:
        async with self.cp.session() as s:
            return await s.scalar(select(KbClient).where(KbClient.client_id == client_id))

    async def ensure_admin_client(
        self, client_id: str = "kb-admin", token: str | None = None
    ) -> str:
        """Idempotently ensure an administrator client exists.

        Used by `kb bootstrap --seed-admin` so the host CLI has a credential
        for the admin API without any direct database access. If the client
        already exists, nothing changes and "" is returned — the stored
        token hash is the authority and the CLI keeps its copy in the
        untracked .env.
        """
        token = token or secrets.token_urlsafe(32)
        async with self.cp.session() as s:
            row = await s.scalar(select(KbClient).where(KbClient.client_id == client_id))
            if row is not None:
                return ""
            s.add(
                KbClient(
                    client_id=client_id,
                    client_name="Host CLI administrator",
                    token_hash=hash_token(token),
                    scopes=["kb:read", "kb:write", "kb:review", "kb:maintain", "kb:admin"],
                    allowed_namespaces=["*"],
                    allowed_sources=["*"],
                )
            )
            await s.commit()
        return token

    async def list_clients(self) -> list[KbClient]:
        """All registered clients (plug-and-play: kb client list)."""
        async with self.cp.session() as s:
            res = await s.scalars(select(KbClient).order_by(KbClient.created_at))
            return list(res)

    async def set_status(self, client_id: str, status: str, *, actor: str) -> KbClient | None:
        """Revoke ('revoked') or reactivate ('active') a client. Audited (LAW-014)."""
        row, before = None, None
        async with self.cp.session() as s:
            row = await s.scalar(select(KbClient).where(KbClient.client_id == client_id))
            if row is None:
                return None
            before = row.status
            row.status = status
            await s.commit()
            await s.refresh(row)
        await AuditRepository(self.cp).record(
            actor=actor,
            action=f"client.{status}",
            resource_type="client",
            resource_id=client_id,
            reason=f"status {before} -> {status}",
        )
        return row

    async def rotate_token(self, client_id: str, *, actor: str) -> str | None:
        """Issue a fresh token for a client; old token stops working immediately.
        Returns the plaintext ONCE, or None when the client does not exist."""
        token = secrets.token_urlsafe(32)
        async with self.cp.session() as s:
            row = await s.scalar(select(KbClient).where(KbClient.client_id == client_id))
            if row is None:
                return None
            row.token_hash = hash_token(token)
            row.rotated_at = datetime.now(UTC)
            await s.commit()
        await AuditRepository(self.cp).record(
            actor=actor,
            action="client.token_rotated",
            resource_type="client",
            resource_id=client_id,
            reason="token rotation",
        )
        return token


class ReviewRepository:
    def __init__(self, cp: ControlPlane) -> None:
        self.cp = cp

    async def enqueue(
        self,
        item_type: str,
        item_reference: str,
        proposed_action: str = "",
        reason: str = "",
        evidence_reference: str = "",
    ) -> KbReviewQueue:
        async with self.cp.session() as s:
            row = KbReviewQueue(
                item_type=item_type,
                item_reference=item_reference,
                proposed_action=proposed_action,
                reason=reason,
                evidence_reference=evidence_reference,
            )
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return row

    async def pending(self, item_type: str | None = None) -> list[KbReviewQueue]:
        async with self.cp.session() as s:
            q = select(KbReviewQueue).where(KbReviewQueue.status == "pending")
            if item_type:
                q = q.where(KbReviewQueue.item_type == item_type)
            res = await s.scalars(q.order_by(KbReviewQueue.created_at))
            return list(res)

    async def decide(self, review_id: str, decision: str, reviewer: str) -> KbReviewQueue | None:
        async with self.cp.session() as s:
            row = await s.get(KbReviewQueue, review_id)
            if row is None:
                return None
            row.status = decision
            row.reviewer = reviewer
            row.reviewed_at = datetime.now(UTC)
            await s.commit()
            await s.refresh(row)
            return row


class MaintenanceRepository:
    """Durable maintenance proposals (Gate 4): everything survives restarts."""

    def __init__(self, cp: ControlPlane) -> None:
        self.cp = cp

    async def upsert_action(self, action: dict) -> KbMaintenanceAction:
        async with self.cp.session() as s:
            row = await s.get(KbMaintenanceAction, action["action_id"])
            if row is None:
                row = KbMaintenanceAction(**action)
                s.add(row)
            else:
                # Immutable once reviewed: a stale re-proposal cannot
                # overwrite a decision in flight.
                if (
                    row.proposal_version != action["proposal_version"]
                    and row.approval_status != "pending"
                ):
                    raise ConflictError(
                        f"action {action['action_id']} changed after review; "
                        "re-propose under a new action id"
                    )
                if row.approval_status == "pending":
                    for k in ("candidates", "evidence", "reason", "risk_level", "reversibility"):
                        if k in action:
                            setattr(row, k, action[k])
                row.updated_at = datetime.now(UTC)
            await s.commit()
            await s.refresh(row)
            return row

    async def get(self, action_id: str) -> KbMaintenanceAction | None:
        async with self.cp.session() as s:
            return await s.get(KbMaintenanceAction, action_id)

    async def decide(
        self, action_id: str, decision: str, reviewer: str, reason: str
    ) -> KbMaintenanceAction:
        async with self.cp.session() as s:
            row = await s.get(KbMaintenanceAction, action_id)
            if row is None:
                raise KeyError(action_id)
            if row.approval_status != "pending":
                raise ConflictError(f"action {action_id} already {row.approval_status}")
            row.approval_status = decision
            row.reviewer = reviewer
            row.reviewed_at = datetime.now(UTC)
            row.review_reason = reason
            row.updated_at = datetime.now(UTC)
            await s.commit()
            await s.refresh(row)
            return row

    async def begin_apply(self, action_id: str, operator: str) -> KbMaintenanceAction:
        async with self.cp.session() as s:
            row = await s.get(KbMaintenanceAction, action_id)
            if row is None:
                raise KeyError(action_id)
            row.apply_attempts += 1
            row.execution_status = "running"
            row.executed_by = operator
            row.updated_at = datetime.now(UTC)
            await s.commit()
            await s.refresh(row)
            return row

    async def finish_apply(self, action_id: str, status: str) -> KbMaintenanceAction:
        async with self.cp.session() as s:
            row = await s.get(KbMaintenanceAction, action_id)
            if row is None:
                raise KeyError(action_id)
            row.execution_status = status
            if status == "completed":
                row.executed_at = datetime.now(UTC)
            row.updated_at = datetime.now(UTC)
            await s.commit()
            await s.refresh(row)
            return row


class ProvenanceRepository:
    """kb_provenance_links: the retrievable provenance index (Gate 4)."""

    def __init__(self, cp: ControlPlane) -> None:
        self.cp = cp

    async def record_batch(self, links: list[dict]) -> int:
        if not links:
            return 0
        async with self.cp.session() as s:
            for payload in links:
                s.add(KbProvenanceLink(**payload))
            await s.commit()
        return len(links)

    async def for_edge(self, edge_uuid: str) -> list[KbProvenanceLink]:
        async with self.cp.session() as s:
            res = await s.scalars(
                select(KbProvenanceLink)
                .where(KbProvenanceLink.edge_uuid == edge_uuid)
                .order_by(KbProvenanceLink.occurred_at)
            )
            return list(res)

    async def for_entity(self, entity_uuid: str) -> list[KbProvenanceLink]:
        async with self.cp.session() as s:
            res = await s.scalars(
                select(KbProvenanceLink)
                .where(KbProvenanceLink.entity_uuid == entity_uuid)
                .order_by(KbProvenanceLink.occurred_at)
            )
            return list(res)

    async def for_event(self, event_id: str) -> list[KbProvenanceLink]:
        async with self.cp.session() as s:
            res = await s.scalars(
                select(KbProvenanceLink)
                .where(KbProvenanceLink.event_id == event_id)
                .order_by(KbProvenanceLink.occurred_at)
            )
            return list(res)


class Repositories:
    """Bundle passed around services."""

    def __init__(self, cp: ControlPlane):
        self.cp = cp
        self.events = EventRepository(cp)
        self.sources = SourceRepository(cp)
        self.identities = IdentityRepository(cp)
        self.audit = AuditRepository(cp)
        self.clients = ClientRepository(cp)
        self.reviews = ReviewRepository(cp)
        self.maintenance = MaintenanceRepository(cp)
        self.provenance = ProvenanceRepository(cp)
