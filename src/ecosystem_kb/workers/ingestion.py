"""Ingestion worker (Gate 2, review P1): lease-guarded, recoverable execution.

Contract:
  - Atomic claim (repo-side SKIP LOCKED on Postgres).
  - Heartbeat: the lease is extended periodically while processing; a worker
    that dies loses its job to lease recovery, which re-queues it.
  - Ownership-guarded completion: a worker that lost its job cannot complete
    or fail it.
  - Retry classification (services.is_retryable): transient infra errors
    retry with exponential backoff; validation/policy errors dead-letter.
  - The worker is the ONLY writer to the graph.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid

from ecosystem_kb.core.config import KbSettings

logger = logging.getLogger(__name__)


class IngestionWorker:
    def __init__(self, services, settings: KbSettings | None = None) -> None:
        self.services = services
        self.settings = settings or services.settings
        self.worker_id = f"ingestion-{uuid.uuid4().hex[:8]}"
        self._stop = asyncio.Event()

    async def run_once(self) -> int:
        """Claim and process up to one job. Returns jobs processed (0 or 1)."""
        job = await self.services.repos.events.claim_next_job(
            self.worker_id, lease_seconds=self.settings.worker_lease_seconds
        )
        if job is None:
            return 0

        # Heartbeat loop: extend the lease while the job runs.
        heartbeat_stop = asyncio.Event()
        lease_seconds = max(5, int(self.settings.worker_lease_seconds))

        async def _heartbeat():
            interval = max(1.0, lease_seconds / 3)
            while not heartbeat_stop.is_set():
                await asyncio.sleep(interval)
                with contextlib.suppress(Exception):
                    await self.services.repos.events.extend_lease(
                        job.id, self.worker_id, lease_seconds
                    )

        heartbeat = asyncio.create_task(_heartbeat())
        try:
            result = await self.services.process_event(job.event_row_id)
            completed = await self.services.repos.events.complete_job(
                job.id, self.worker_id
            )
            if not completed:
                logger.warning(
                    "job %s completed processing but ownership was lost; "
                    "another worker owns it now",
                    job.id,
                )
                return 0
            logger.info("job %s done: %s", job.id, result)
            return 1
        except Exception as exc:
            retryable = self.services.is_retryable(exc)
            outcome = await self.services.repos.events.fail_job(
                job.id,
                self.worker_id,
                str(exc),
                retryable,
                self.settings.worker_max_attempts,
            )
            logger.warning(
                "job %s %s (retryable=%s): %s", job.id, outcome, retryable, exc
            )
            return 0
        finally:
            heartbeat_stop.set()
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def run(self, poll_interval: float = 0.5) -> None:
        """Run until stopped. Passes are cheap when the queue is empty."""
        while not self._stop.is_set():
            processed = await self.run_once()
            if processed == 0:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=poll_interval)
                except TimeoutError:
                    pass

    def stop(self) -> None:
        self._stop.set()
