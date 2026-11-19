"""Arivoo transactional-outbox reference integration (Gate 6).

The pattern the real Arivoo will use (spec §40, review Gate 6):
  1. Arivoo commits a note AND an outbox row in ONE transaction (its DB).
  2. A relay polls the outbox, maps each row to kb.event.v1, and POSTs to the
     KB gateway with the arivoo-producer client token.
  3. The relay marks rows sent only on a 200 (duplicate receipts included —
     an idempotent replay is a successful send).
  4. Revisions carry increasing revision numbers and the same record id, so
     the KB sees supersession rather than duplicates; the idempotency key is
     (record, revision), making replays exactly-once.

This module is the reference implementation of the relay + mapping + a
minimal Arivoo-side outbox for the acceptance test. The real Arivoo runs
this against its own schema; the mapping contract is what matters.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

ARIVOO_SOURCE_ID = "system:arivoo"


# --- Arivoo side: the outbox row its own transaction writes ----------------


class ArivooOutboxRow(BaseModel):
    """What Arivoo writes next to its note in the SAME transaction."""

    model_config = ConfigDict(extra="forbid")

    outbox_id: str = Field(default_factory=lambda: str(uuid4()))
    record_id: str = Field(description="Arivoo note id, stable across revisions")
    revision: int = Field(ge=1)
    event_type: str = Field(description="note.committed | note.revised")
    title: str
    body: str
    occurred_at: datetime
    sent_at: datetime | None = None  # relay sets this


# --- KB side: outbox row -> kb.event.v1 mapping ------------------------------


def outbox_to_kb_event(row: ArivooOutboxRow) -> dict:
    """The mapping contract. Deterministic idempotency key: (record, revision)."""
    event_type = (
        "knowledge.note.committed" if row.event_type == "note.committed"
        else "knowledge.note.revised"
    )
    return {
        "event_id": str(uuid4()),  # regenerated per relay attempt is fine:
        # dedup is by idempotency_key, not event_id
        "idempotency_key": f"arivoo:{row.record_id}:revision-{row.revision}",
        "event_type": event_type,
        "source": {
            "system_id": ARIVOO_SOURCE_ID,
            "record_id": row.record_id,
            "revision": row.revision,
            "canonical_uri": f"arivoo://notes/{row.record_id}?rev={row.revision}",
        },
        "timestamps": {
            "occurred_at": row.occurred_at.isoformat(),
            "recorded_at": datetime.now(UTC).isoformat(),
        },
        "actor": {"type": "human", "id": "arivoo-user"},
        "classification": {
            "trust_class": "human_authored",
            "review_status": "unreviewed",
            "sensitivity": "internal",
        },
        "content": {
            "content_type": "text",
            "body": f"{row.title}\n\n{row.body}",
        },
        "payload": {
            "note_title": row.title,
            "supersedes_revision": row.revision - 1 if row.revision > 1 else None,
        },
    }


class ArivooRelay:
    """Polls an outbox list and delivers to the KB gateway.

    The relay is crash-safe by construction: a row is marked sent ONLY after
    the KB confirms (200, including duplicate receipts). A crash between
    delivery and marking causes a replay, which the KB deduplicates by
    idempotency key (LAW-008).
    """

    def __init__(self, gateway_base_url: str, token: str, http=None) -> None:
        self.base = gateway_base_url.rstrip("/")
        self.token = token
        self._http = http

    def _post(self, path: str, body: dict) -> tuple[int, dict]:
        import httpx

        with httpx.Client(timeout=15.0, **(self._http or {})) as client:
            r = client.post(
                f"{self.base}{path}",
                json=body,
                headers={"Authorization": f"Bearer {self.token}"},
            )
            try:
                return r.status_code, r.json()
            except Exception:
                return r.status_code, {"raw": r.text}

    def deliver_row(self, row: ArivooOutboxRow) -> bool:
        """One outbox row -> one kb.event.v1 POST. Returns True when the KB
        accepted (created OR duplicate) — either way the row is safely sent."""
        code, receipt = self._post("/v1/events", outbox_to_kb_event(row))
        return code == 200

    def drain(self, outbox: list[ArivooOutboxRow]) -> tuple[int, int]:
        """Deliver all unsent rows. Returns (sent, failed)."""
        sent = failed = 0
        for row in outbox:
            if row.sent_at is not None:
                continue
            if self.deliver_row(row):
                row.sent_at = datetime.now(UTC)
                sent += 1
            else:
                failed += 1
        return sent, failed
