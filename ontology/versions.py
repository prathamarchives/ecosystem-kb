"""Ontology versioning (LAW-013).

Ontology changes must be versioned, validated, and tested against historical
fixtures. The version constant below ships with the code; bump it whenever
entity types, relationship rules, or enums change, and record the change in
the migration notes.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class OntologyVersion(BaseModel):
    version: str
    released: date
    notes: str


ONTOLOGY_VERSION = OntologyVersion(
    version="0.1.0",
    released=date(2026, 11, 18),
    notes=(
        "Initial controlled vocabulary: 14 entity types, 16 relationship types "
        "with domain/range rules, 7-kind variable model. Baseline for EKIS v0.1 "
        "(docs/spec/EKIS-SPEC-001.md)."
    ),
)


def assert_compatible(requested: str) -> None:
    """Accept the exact version or a lower minor within the same major.

    A major mismatch means historical data may not validate; refuse it loudly
    rather than corrupting the graph (LAW-013).
    """
    current = ONTOLOGY_VERSION.version
    cur_major, cur_minor, _ = (int(p) for p in current.split("."))
    try:
        req_major, req_minor, _ = (int(p) for p in requested.split("."))
    except (ValueError, AttributeError):
        raise ValueError(f"malformed ontology version: {requested!r}") from None
    if req_major != cur_major:
        raise ValueError(
            f"ontology major-version mismatch: data written under {requested}, "
            f"code enforces {current}; run the migration first"
        )
    if (req_major, req_minor) > (cur_major, cur_minor):
        raise ValueError(
            f"ontology version {requested} is newer than code ({current}); upgrade first"
        )
