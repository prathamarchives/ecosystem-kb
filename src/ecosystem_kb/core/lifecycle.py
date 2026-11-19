"""Lifecycle state machines (spec §11, §12).

Two independent tracks:
  - IngestionEventState: processing lifecycle of an event (not epistemic).
  - ReviewState: epistemic condition of extracted knowledge.

Both are enforced here, deterministically — the only path for transitions.
"""

from __future__ import annotations

from enum import StrEnum


class IngestionEventState(StrEnum):
    RECEIVED = "received"
    VALIDATING = "validating"
    REJECTED = "rejected"
    ACCEPTED = "accepted"
    QUEUED = "queued"
    PROCESSING = "processing"
    RETRYABLE_FAILURE = "retryable_failure"
    QUARANTINED = "quarantined"
    GRAPH_WRITTEN = "graph_written"
    INDEX_VERIFIED = "index_verified"
    COMPLETED = "completed"
    FAILED_FINAL = "failed_final"
    SUPERSEDED = "superseded"
    WITHDRAWN = "withdrawn"


_INGESTION_TRANSITIONS: dict[IngestionEventState, set[IngestionEventState]] = {
    IngestionEventState.RECEIVED: {
        IngestionEventState.VALIDATING,
        IngestionEventState.REJECTED,
    },
    IngestionEventState.VALIDATING: {
        IngestionEventState.REJECTED,
        IngestionEventState.ACCEPTED,
    },
    IngestionEventState.REJECTED: set(),  # terminal
    IngestionEventState.ACCEPTED: {
        IngestionEventState.QUEUED,
        IngestionEventState.REJECTED,
    },
    IngestionEventState.QUEUED: {
        IngestionEventState.PROCESSING,
        IngestionEventState.WITHDRAWN,
    },
    IngestionEventState.PROCESSING: {
        IngestionEventState.RETRYABLE_FAILURE,
        IngestionEventState.QUARANTINED,
        IngestionEventState.GRAPH_WRITTEN,
        IngestionEventState.FAILED_FINAL,
    },
    IngestionEventState.RETRYABLE_FAILURE: {
        IngestionEventState.QUEUED,
        IngestionEventState.FAILED_FINAL,
    },
    IngestionEventState.QUARANTINED: {
        IngestionEventState.QUEUED,  # released after review
        IngestionEventState.FAILED_FINAL,
    },
    IngestionEventState.GRAPH_WRITTEN: {
        IngestionEventState.INDEX_VERIFIED,
        IngestionEventState.RETRYABLE_FAILURE,
    },
    IngestionEventState.INDEX_VERIFIED: {
        IngestionEventState.COMPLETED,
        IngestionEventState.RETRYABLE_FAILURE,
    },
    IngestionEventState.COMPLETED: set(),  # terminal
    IngestionEventState.FAILED_FINAL: set(),  # terminal
    IngestionEventState.SUPERSEDED: set(),  # terminal
    IngestionEventState.WITHDRAWN: set(),  # terminal
}

TERMINAL_INGESTION_STATES = {
    s for s, targets in _INGESTION_TRANSITIONS.items() if not targets
}


class ReviewState(StrEnum):
    UNREVIEWED = "unreviewed"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    DISPUTED = "disputed"
    REJECTED = "rejected"
    RETRACTED = "retracted"


_REVIEW_TRANSITIONS: dict[ReviewState, set[ReviewState]] = {
    ReviewState.UNREVIEWED: {ReviewState.REVIEWED},
    ReviewState.REVIEWED: {
        ReviewState.APPROVED,
        ReviewState.DISPUTED,
        ReviewState.REJECTED,
    },
    ReviewState.APPROVED: {
        ReviewState.DISPUTED,  # a previously approved claim can be disputed later
        ReviewState.RETRACTED,
    },
    ReviewState.DISPUTED: {
        ReviewState.REVIEWED,  # re-review after new evidence
        ReviewState.RETRACTED,
    },
    ReviewState.REJECTED: {ReviewState.REVIEWED},
    ReviewState.RETRACTED: set(),
}


class InvalidTransition(ValueError):
    def __init__(self, machine: str, current: str, attempted: str) -> None:
        super().__init__(
            f"illegal {machine} transition: {current} -> {attempted}"
        )
        self.machine = machine
        self.current = current
        self.attempted = attempted


def transition_ingestion(
    current: IngestionEventState, target: IngestionEventState
) -> IngestionEventState:
    allowed = _INGESTION_TRANSITIONS.get(IngestionEventState(current), set())
    if target not in allowed:
        raise InvalidTransition("ingestion", current, target)
    return target


def transition_review(current: ReviewState, target: ReviewState) -> ReviewState:
    allowed = _REVIEW_TRANSITIONS.get(ReviewState(current), set())
    if target not in allowed:
        raise InvalidTransition("review", current, target)
    return target


def can_transition_ingestion(
    current: IngestionEventState, target: IngestionEventState
) -> bool:
    return target in _INGESTION_TRANSITIONS.get(IngestionEventState(current), set())
