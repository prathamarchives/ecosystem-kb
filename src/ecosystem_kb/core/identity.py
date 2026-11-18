"""Canonical identity utilities (spec §8, LAW-005).

Graphiti UUIDs are storage details. Our identity standard is the canonical
key `type:slug`, maintained in the control-plane registry with aliases and
graph-UUID mappings. Renames never create entities; merges preserve aliases.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

CANONICAL_KEY_PATTERN = re.compile(r"^[a-z]+:[a-z0-9][a-z0-9._/-]*$")


def slugify(text: str) -> str:
    """Identity-safe slug: unicode-folded, lowercase, separator-normalized."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9._/-]+", "-", folded.lower()).strip("-")
    return slug or "unnamed"


def canonical_key(entity_type_prefix: str, name: str) -> str:
    """Build `type:slug` from a display name. Stability comes from the
    registry, not from the name: resolvers look keys up by alias first."""
    key = f"{entity_type_prefix}:{slugify(name)}"
    if not CANONICAL_KEY_PATTERN.match(key):
        raise ValueError(f"canonical key malformed: {key!r}")
    return key


class IdentityRecord(BaseModel):
    """One row of the identity registry (control-plane kb_entity_identity)."""

    model_config = ConfigDict(extra="forbid")

    canonical_key: str
    graphiti_uuid: str | None = None
    entity_type: str
    canonical_name: str
    status: str = "active"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AliasRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alias_id: str
    canonical_key: str
    alias_value: str
    alias_source: str = Field(default="extraction", description="extraction | merge | manual")
    confidence_basis: str = Field(
        default="unverified", description="exact | normalized | llm_proposed"
    )
    review_status: str = "unreviewed"


class AliasResolver:
    """In-memory alias index over registry rows; the control plane loads it.

    Rules (spec §8): aliases resolve to an existing canonical identity;
    ambiguous matches require review, never a silent pick.
    """

    def __init__(self) -> None:
        self._by_alias: dict[str, list[str]] = {}
        self._records: dict[str, IdentityRecord] = {}

    def register(self, record: IdentityRecord) -> None:
        self._records[record.canonical_key] = record
        self.add_alias(record.canonical_key, record.canonical_name, "exact", "registration")

    def add_alias(
        self,
        canonical_key: str,
        alias: str,
        confidence: str = "normalized",
        source: str = "extraction",
    ) -> None:
        key = self._normalize(alias)
        targets = self._by_alias.setdefault(key, [])
        if canonical_key not in targets:
            targets.append(canonical_key)

    def resolve(self, name: str) -> str | None:
        """Unambiguous match or None. Multiple distinct targets => None
        (ambiguous identities must go to review, LAW-005)."""
        targets = self._by_alias.get(self._normalize(name), [])
        distinct = {t for t in targets}
        if len(distinct) == 1:
            return next(iter(distinct))
        return None

    def ambiguous(self, name: str) -> list[str]:
        targets = set(self._by_alias.get(self._normalize(name), []))
        return sorted(targets) if len(targets) > 1 else []

    @staticmethod
    def _normalize(name: str) -> str:
        return re.sub(r"\s+", " ", slugify(name).replace("-", " ")).strip()

    def merge(self, source_key: str, target_key: str) -> list[str]:
        """Merge source into target; source's aliases now point at target.
        Returns the re-pointed alias list (for the audit record)."""
        moved: list[str] = []
        for alias, targets in self._by_alias.items():
            if source_key in targets:
                targets.remove(source_key)
                if target_key not in targets:
                    targets.append(target_key)
                moved.append(alias)
        if source_key in self._records:
            rec = self._records.pop(source_key)
            rec.status = "merged"
            self._records[source_key] = rec
        return moved
