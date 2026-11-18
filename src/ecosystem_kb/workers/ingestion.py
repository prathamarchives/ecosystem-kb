"""Ingestion worker: claim jobs with leases, process, retry or fail (ADR-008).

Single ordered worker per namespace in v1. The worker is the ONLY writer to
the graph (no API-layer direct writes — AGENTS.md prohibited shortcuts).
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from ecosystem_kb.core.config import KbSettings
from ecosystem_kb.core.exceptions import GraphUnavailableError

logger = logging.getLogger(__name__)


class IngestionWorker:
    def __init__(self, services, settings: KbSettings | None = None) -> None:
        self.services = services
        self.settings = settings or services.settings
        self.worker_id = f"ingestion-{uuid.uuid4().hex[:8]}"
        self._stop = asyncio.Event()

    async def run_once(self) -> int:
        """Claim and process up to one job. Returns jobs processed."""
        job = await self.services.repos.events.claim_next_job(
            self.worker_id, lease_seconds=self.settings.worker_lease_seconds
        )
        if job is None:
            return 0
        try:
            result = await self.services.process_event(job.event_row_id)
            await self.services.repos.events.complete_job(job.id)
            logger.info("job %s done: %s", job.id, result)
            return 1
        except Exception as exc:
            retryable = not isinstance(exc, (GraphUnavailableError,))
            outcome = await self.services.repos.events.fail_job(
                job.id, str(exc), retryable, self.settings.worker_max_attempts
            )
            logger.warning("job %s %s: %s", job.id, outcome, exc)
            return 0

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
