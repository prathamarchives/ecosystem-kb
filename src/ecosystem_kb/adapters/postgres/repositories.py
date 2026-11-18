"""Async control-plane repositories over the 8 tables.

Idempotency at ingress (LAW-008), job claiming with leases (ADR-008),
append-only audit (LAW-014), token-hash authn records (LAW-009).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from ecosystem_kb.adapters.postgres.models import (
    ALL_TABLES,
    Base,
    KbAlias,
    KbAuditLog,
    KbClient,
    KbEntityIdentity,
    KbEvent,
    KbIngestionJob,
    KbReviewQueue,
    KbSource,
)
from ecosystem_kb.core.lifecycle import IngestionEventState, transition_ingestion


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


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

    async def insert_event(
        self,
        *,
        event_id: str,
        idempotency_key: str,
        schema_version: str,
        source_id: str,
        source_revision: str | None,
        event_type: str,
        namespace: str,
        payload_reference: str,
        content_hash: str | None,
        occurred_at: datetime,
        received_at: datetime,
    ) -> tuple[KbEvent, bool]:
        """Returns (row, created). created=False means idempotent duplicate."""
        async with self.cp.session() as s:
            existing = await s.scalar(
                select(KbEvent).where(KbEvent.idempotency_key == idempotency_key)
            )
            if existing is not None:
                return existing, False
            row = KbEvent(
                event_id=event_id,
                idempotency_key=idempotency_key,
                schema_version=schema_version,
                source_id=source_id,
                source_revision=source_revision,
                event_type=event_type,
                namespace=namespace,
                payload_reference=payload_reference,
                content_hash=content_hash,
                occurred_at=occurred_at,
                received_at=received_at,
                processing_status=IngestionEventState.ACCEPTED.value,
            )
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return row, True

    async def get_by_event_id(self, event_id: str) -> KbEvent | None:
        async with self.cp.session() as s:
            return await s.scalar(select(KbEvent).where(KbEvent.event_id == event_id))

    async def get_by_idempotency_key(self, key: str) -> KbEvent | None:
        async with self.cp.session() as s:
            return await s.scalar(select(KbEvent).where(KbEvent.idempotency_key == key))

    async def set_status(self, event_row_id: str, target: IngestionEventState) -> None:
        async with self.cp.session() as s:
            row = await s.get(KbEvent, event_row_id)
            if row is None:
                raise KeyError(event_row_id)
            row.processing_status = transition_ingestion(
                IngestionEventState(row.processing_status), target
            ).value
            await s.commit()

    async def force_status(self, event_row_id: str, target: IngestionEventState) -> None:
        """Maintenance path: set without transition validation (quarantine release)."""
        async with self.cp.session() as s:
            row = await s.get(KbEvent, event_row_id)
            if row is None:
                raise KeyError(event_row_id)
            row.processing_status = target.value
            await s.commit()

    async def enqueue_job(self, event_row_id: str) -> KbIngestionJob:
        async with self.cp.session() as s:
            job = KbIngestionJob(event_row_id=event_row_id, status="queued")
            s.add(job)
            await s.commit()
            await s.refresh(job)
            return job

    async def claim_next_job(
        self, worker_id: str, lease_seconds: int = 300
    ) -> KbIngestionJob | None:
        """Lease-based claim: oldest queued job, atomically marked processing."""
        async with self.cp.session() as s:
            job = await s.scalar(
                select(KbIngestionJob)
                .where(KbIngestionJob.status == "queued")
                .order_by(KbIngestionJob.id)
                .limit(1)
            )
            if job is None:
                return None
            job.status = "processing"
            job.worker_id = worker_id
            job.attempt_count += 1
            job.started_at = datetime.now(UTC)
            job.lease_expires_at = datetime.now(UTC) + timedelta(
                seconds=lease_seconds
            )
            await s.commit()
            await s.refresh(job)
            return job

    async def complete_job(self, job_id: str) -> None:
        async with self.cp.session() as s:
            await s.execute(
                update(KbIngestionJob)
                .where(KbIngestionJob.id == job_id)
                .values(
                    status="completed",
                    completed_at=datetime.now(UTC),
                    lease_expires_at=None,
                )
            )
            await s.commit()

    async def fail_job(
        self, job_id: str, error: str, retryable: bool, max_attempts: int = 5
    ) -> str:
        """Returns 'requeued' | 'failed_final'."""
        async with self.cp.session() as s:
            job = await s.get(KbIngestionJob, job_id)
            if job is None:
                raise KeyError(job_id)
            job.last_error = error[-2000:]
            if retryable and job.attempt_count < max_attempts:
                job.status = "queued"
                job.lease_expires_at = None
                outcome = "requeued"
            else:
                job.status = "failed"
                job.lease_expires_at = None
                outcome = "failed_final"
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
        self, canonical_key: str, entity_type: str, canonical_name: str, graphiti_uuid: str | None = None
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
        """All identities whose canonical name or alias matches (ambiguity check)."""
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
        self, client_id: str, client_name: str, scopes: list[str], allowed_namespaces: list[str]
    ) -> str:
        """Creates the client and returns the plaintext token ONCE."""
        token = secrets.token_urlsafe(32)
        async with self.cp.session() as s:
            s.add(
                KbClient(
                    client_id=client_id,
                    client_name=client_name,
                    token_hash=hash_token(token),
                    scopes=scopes,
                    allowed_namespaces=allowed_namespaces,
                )
            )
            await s.commit()
        return token

    async def find_by_token(self, token: str) -> KbClient | None:
        async with self.cp.session() as s:
            return await s.scalar(
                select(KbClient).where(KbClient.token_hash == hash_token(token))
            )


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
