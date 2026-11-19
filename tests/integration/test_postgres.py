"""PostgreSQL integration tests against a REAL server (pgserver).

Covers the review's P0 (production image path) and Gate 1 CI requirement:
migrations run on live Postgres, the asyncpg engine works, atomic submit
holds under a real database, and SKIP LOCKED claiming works for real.
"""

import asyncio

import pytest
import pytest_asyncio

pytestmark = pytest.mark.postgres


_SERVER = None


def _pgserver():
    """One Postgres per test run. pgserver servers register in a shared
    runtime directory; multiple instances in one process fight over cleanup,
    so tests share a single server and use distinct DATABASES for isolation."""
    global _SERVER
    if _SERVER is None:
        import tempfile
        from pathlib import Path

        from pgserver import PostgresServer

        _SERVER = PostgresServer(
            Path(tempfile.mkdtemp(prefix="ekb_pg_")), cleanup_mode=None
        )
    return _SERVER


@pytest.fixture
def pg_uri():
    return _pgserver().get_uri()


@pytest_asyncio.fixture
async def pg_engine(pg_uri):
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(pg_uri.replace("postgresql://", "postgresql+asyncpg://"))
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def pg_control_plane(pg_uri):
    """Fresh schema per test via a distinct database."""
    import psycopg

    from ecosystem_kb.adapters.postgres.repositories import ControlPlane

    dbname = f"ekb_test_{__import__('uuid').uuid4().hex[:8]}"
    with psycopg.connect(pg_uri, autocommit=True) as c:
        c.execute(f"CREATE DATABASE {dbname}")
    # pg_uri shape: postgresql://postgres:@/postgres?host=/tmp/xxx —
    # swap the path segment, keep the query intact.
    base, _, query = pg_uri.partition("?")
    head, _, _ = base.rpartition("/")
    db_uri = f"{head}/{dbname}"
    if query:
        db_uri += f"?{query}"
    cp = ControlPlane(db_uri.replace("postgresql://", "postgresql+asyncpg://"))
    yield cp
    await cp.dispose()
    with psycopg.connect(pg_uri, autocommit=True) as c:
        c.execute(f"DROP DATABASE {dbname}")


class TestMigrationsOnPostgres:
    async def test_migrations_apply_and_record(self, pg_engine):
        from sqlalchemy import text

        from ecosystem_kb.adapters.postgres.migrations import latest_version, load_migrations, migrate

        applied = await migrate(pg_engine)
        assert applied == [m.version for m in load_migrations()]
        # Idempotent rerun.
        assert await migrate(pg_engine) == []
        async with pg_engine.begin() as conn:
            rows = (await conn.execute(
                text("SELECT version FROM kb_schema_migrations ORDER BY version")
            )).scalars()
        assert list(rows) == [1, 2]
        assert latest_version() == 2

    async def test_orm_columns_match_migrations(self, pg_engine):
        """Schema parity: every ORM column exists in the migrated database."""
        from sqlalchemy import text

        from ecosystem_kb.adapters.postgres.models import ALL_TABLES

        async with pg_engine.begin() as conn:
            cols = (
                await conn.execute(
                    text(
                        "SELECT table_name, column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name LIKE 'kb_%'"
                    )
                )
            ).fetchall()
        db_cols = {(r[0], r[1]) for r in cols}
        for table in ALL_TABLES:
            for col in table.__table__.columns:
                assert (table.__tablename__, col.name) in db_cols, (
                    f"ORM column {table.__tablename__}.{col.name} missing from migrations"
                )


class TestAtomicityOnPostgres:
    async def test_submit_is_atomic(self, pg_control_plane):
        from ecosystem_kb.adapters.postgres.repositories import Repositories, SubmitOutcome

        await pg_control_plane.create_all()
        repos = Repositories(pg_control_plane)
        row, outcome = await repos.events.submit_event(
            event_id="11111111-1111-1111-1111-111111111111",
            idempotency_key="pg:test:1",
            schema_version="kb.event.v1",
            source_id="system:pg",
            source_revision=None,
            event_type="knowledge.note.committed",
            namespace="ecosystem-prod",
            content_reference="pg://ref",
            content_hash=None,
            occurred_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            received_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            serialized_event='{"event_type": "knowledge.note.committed"}',
        )
        assert outcome == SubmitOutcome.CREATED
        assert row.event_payload == '{"event_type": "knowledge.note.committed"}'

        # Exactly one job, atomically created.
        counts = await pg_control_plane.table_counts()
        assert counts["kb_ingestion_jobs"] == 1

        # Same payload again: duplicate, no second job.
        row2, outcome2 = await repos.events.submit_event(
            event_id="22222222-2222-2222-2222-222222222222",
            idempotency_key="pg:test:1",
            schema_version="kb.event.v1",
            source_id="system:pg",
            source_revision=None,
            event_type="knowledge.note.committed",
            namespace="ecosystem-prod",
            content_reference="pg://ref",
            content_hash=None,
            occurred_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            received_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            serialized_event='{"event_type": "knowledge.note.committed"}',
        )
        assert outcome2 == SubmitOutcome.DUPLICATE
        counts = await pg_control_plane.table_counts()
        assert counts["kb_ingestion_jobs"] == 1

        # Different payload, same key: conflict.
        from ecosystem_kb.core.exceptions import ConflictError

        with pytest.raises(ConflictError):
            await repos.events.submit_event(
                event_id="33333333-3333-3333-3333-333333333333",
                idempotency_key="pg:test:1",
                schema_version="kb.event.v1",
                source_id="system:pg",
                source_revision=None,
                event_type="knowledge.note.revised",
                namespace="ecosystem-prod",
                content_reference="pg://ref",
                content_hash=None,
                occurred_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
                received_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
                serialized_event='{"event_type": "DIFFERENT"}',
            )

    async def test_concurrent_claims_are_exclusive(self, pg_control_plane):
        """SKIP LOCKED under real concurrency: N workers, N jobs, each job
        processed exactly once."""
        from ecosystem_kb.adapters.postgres.repositories import Repositories

        await pg_control_plane.create_all()
        repos = Repositories(pg_control_plane)
        dt = __import__("datetime")
        now = dt.datetime.now(dt.UTC)
        for i in range(8):
            await repos.events.submit_event(
                event_id=f"44444444-4444-4444-4444-44444444444{i}",
                idempotency_key=f"pg:race:{i}",
                schema_version="kb.event.v1",
                source_id="system:pg",
                source_revision=None,
                event_type="knowledge.note.committed",
                namespace="ecosystem-prod",
                content_reference="pg://ref",
                content_hash=None,
                occurred_at=now,
                received_at=now,
                serialized_event=f'{{"i": {i}}}',
            )

        claimed: list[str] = []
        lock = asyncio.Lock()

        async def claimer(tag: str):
            while True:
                job = await repos.events.claim_next_job(f"pg-worker-{tag}")
                if job is None:
                    return
                async with lock:
                    claimed.append(job.id)

        await asyncio.gather(*(claimer(str(i)) for i in range(4)))
        assert len(claimed) == 8
        assert len(set(claimed)) == 8, "SKIP LOCKED must prevent double claims"

    async def test_lease_recovery_on_postgres(self, pg_control_plane):
        from ecosystem_kb.adapters.postgres.repositories import Repositories

        await pg_control_plane.create_all()
        repos = Repositories(pg_control_plane)
        dt = __import__("datetime")
        now = dt.datetime.now(dt.UTC)
        await repos.events.submit_event(
            event_id="55555555-5555-5555-5555-555555555555",
            idempotency_key="pg:lease:1",
            schema_version="kb.event.v1",
            source_id="system:pg",
            source_revision=None,
            event_type="knowledge.note.committed",
            namespace="ecosystem-prod",
            content_reference="pg://ref",
            content_hash=None,
            occurred_at=now,
            received_at=now,
            serialized_event="{}",
        )
        dead_job = await repos.events.claim_next_job("pg-dead", lease_seconds=0)
        assert dead_job is not None
        await asyncio.sleep(0.05)
        revived = await repos.events.claim_next_job("pg-alive")
        assert revived is not None and revived.id == dead_job.id
        assert await repos.events.complete_job(dead_job.id, "pg-alive")
        assert not await repos.events.complete_job(dead_job.id, "pg-dead")

    async def test_maintenance_actions_durable(self, pg_control_plane):
        from ecosystem_kb.adapters.postgres.repositories import Repositories

        await pg_control_plane.create_all()
        repos = Repositories(pg_control_plane)
        action = {
            "action_id": "ENTITY_MERGE:system:a:system:b",
            "operation": "ENTITY_MERGE",
            "namespace": "ecosystem-prod",
            "target": "system:a",
            "candidates": ["system:b"],
            "evidence": ["test"],
            "reason": "pg durability",
            "risk_level": "medium",
            "reversibility": "reversible",
            "proposal_version": "abc123",
        }
        await repos.maintenance.upsert_action(action)
        row = await repos.maintenance.get(action["action_id"])
        assert row is not None and row.proposal_version == "abc123"
        await repos.maintenance.decide(action["action_id"], "approved", "pg-test", "ok")
        row = await repos.maintenance.get(action["action_id"])
        assert row.approval_status == "approved"
        # Re-proposal with a different version after review -> conflict.
        from ecosystem_kb.core.exceptions import ConflictError

        with pytest.raises(ConflictError):
            await repos.maintenance.upsert_action({**action, "proposal_version": "zzz"})
