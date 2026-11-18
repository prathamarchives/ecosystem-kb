"""Variable model helpers (spec Part IV).

VariableDefinition (the entity) lives in entities.py; this module adds the
semantic layer: scope-qualified canonical keys, unit checks, and the
definition-vs-value boundary (definitions in the KB, values in owning
systems — never secrets, LAW-010).
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

from ontology.enums import (
    VariableDataType,
    VariableKind,
    VariableMutability,
    VariableScope,
    VariableSensitivity,
)

# Owner segments may themselves be canonical keys (e.g. model:demand), so
# the slug charset includes ":" beyond the mandatory "variable:" prefix.
CANONICAL_VARIABLE_KEY = re.compile(r"^variable:[a-z0-9][a-z0-9._:/-]*$")


def variable_canonical_key(scope: VariableScope, owner: str, name: str) -> str:
    """Canonical identity for a variable: scope + owner + name.

    Example: variable:system/ecosystem-kb/api_timeout
    Two variables with the same display name in different scopes are
    DIFFERENT variables (spec §6.2).
    """
    owner_slug = owner.strip().lower().replace(" ", "-")
    name_slug = name.strip().lower().replace(" ", "_")
    key = f"variable:{scope.value}/{owner_slug}/{name_slug}"
    if not CANONICAL_VARIABLE_KEY.match(key):
        raise ValueError(f"derived canonical key is malformed: {key}")
    return key


NUMERIC_TYPES = {
    VariableDataType.INTEGER,
    VariableDataType.NUMBER,
    VariableDataType.DECIMAL,
}


class VariableConstraints(BaseModel):
    minimum: float | int | None = None
    maximum: float | int | None = None
    allowed_values: list[str] | None = None
    pattern: str | None = None


class VariableDefinitionSpec(BaseModel):
    """Creation spec for a VariableDefinition entity — validates the semantic
    categories before an entity is constructed."""

    name: str = Field(min_length=1, max_length=100)
    symbol: str | None = None
    kind: VariableKind
    data_type: VariableDataType
    unit: str | None = Field(default=None, description="Required for numeric types in principle")
    scope: VariableScope = VariableScope.SYSTEM
    description: str = ""
    owner_reference: str | None = None
    mutability: VariableMutability = VariableMutability.CONFIGURATION_CONTROLLED
    constraints: VariableConstraints = Field(default_factory=VariableConstraints)
    default_value: str | int | float | bool | None = None
    sensitivity: VariableSensitivity = VariableSensitivity.INTERNAL
    canonical_source: str | None = None

    @field_validator("name")
    @classmethod
    def name_must_be_identifier_like(cls, v: str) -> str:
        if not re.match(r"^[A-Za-z][A-Za-z0-9_.-]*$", v):
            raise ValueError("variable name must be identifier-like (letters, digits, . _ -)")
        return v

    @field_validator("default_value")
    @classmethod
    def default_matches_type(cls, v, info):
        data = info.data.get("data_type")
        if v is None or data is None:
            return v
        if data == VariableDataType.INTEGER and not isinstance(v, int):
            raise ValueError("integer variable requires an int default")
        if data == VariableDataType.BOOLEAN and not isinstance(v, bool):
            raise ValueError("boolean variable requires a bool default")
        if data in (VariableDataType.NUMBER, VariableDataType.DECIMAL) and not isinstance(
            v, (int, float)
        ):
            raise ValueError("numeric variable requires a numeric default")
        return v

    def derived_canonical_key(self) -> str:
        owner = self.owner_reference or "ecosystem"
        return variable_canonical_key(self.scope, owner, self.name)


# Where variable VALUES live (spec §6.5) — documentation as data, used by
# ingestion policy to reject value-bearing payloads that belong elsewhere.
VALUE_STORAGE_AUTHORITY: dict[str, str] = {
    "current_application_configuration": "owning application's configuration store",
    "historical_configuration_changes": "git or control-plane audit records",
    "experiment_parameter_sets": "experiment registry",
    "high_volume_state_measurements": "telemetry / time-series storage",
    "dataset_observations": "dataset storage",
    "secrets": "dedicated secret manager (NEVER the KB)",
    "variable_definitions": "KB ontology and source registry",
}
