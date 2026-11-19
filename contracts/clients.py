"""Client identity and authorization contract (spec §23, LAW-009).

Scopes are server-side. The gateway derives namespace from the authenticated
client; callers never choose group_ids (SYS-S-003).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

READ = "kb:read"
WRITE = "kb:write"
REVIEW = "kb:review"
MAINTAIN = "kb:maintain"
ADMIN = "kb:admin"

ALL_SCOPES = [READ, WRITE, REVIEW, MAINTAIN, ADMIN]

# Which scopes a role bundles (used by bootstrap to create sane clients).
ROLE_SCOPES: dict[str, list[str]] = {
    "reader": [READ],
    "contributor": [READ, WRITE],
    "reviewer": [READ, WRITE, REVIEW],
    "maintainer": [READ, WRITE, REVIEW, MAINTAIN],
    "administrator": ALL_SCOPES,
}


class AuthzAction(StrEnum):
    SEARCH = "search"
    SUBMIT_EVENT = "submit_event"
    GET_ENTITY = "get_entity"
    GET_CONTEXT = "get_context"
    GET_PROVENANCE = "get_provenance"
    PROPOSE_UPDATE = "propose_update"
    REVIEW = "review"
    MAINTENANCE_REPORT = "maintenance_report"
    APPROVE_MAINTENANCE = "approve_maintenance"
    APPLY_MAINTENANCE = "apply_maintenance"
    ADMIN = "admin"


ACTION_SCOPES: dict[AuthzAction, str] = {
    AuthzAction.SEARCH: READ,
    AuthzAction.GET_ENTITY: READ,
    AuthzAction.GET_CONTEXT: READ,
    AuthzAction.GET_PROVENANCE: READ,
    AuthzAction.SUBMIT_EVENT: WRITE,
    AuthzAction.PROPOSE_UPDATE: WRITE,
    AuthzAction.REVIEW: REVIEW,
    AuthzAction.MAINTENANCE_REPORT: MAINTAIN,
    AuthzAction.APPROVE_MAINTENANCE: MAINTAIN,
    AuthzAction.APPLY_MAINTENANCE: MAINTAIN,
    AuthzAction.ADMIN: ADMIN,
}


class ClientRegistration(BaseModel):
    """Register an integrating application (control-plane kb_clients)."""

    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(min_length=3, max_length=60, description="Stable client identity")
    client_name: str
    scopes: list[str] = Field(description="Subset of kb:* scopes")
    allowed_namespaces: list[str] = Field(
        description="Server-enforced namespaces this client may touch"
    )
    allowed_sources: list[str] = Field(
        default_factory=lambda: ["*"],
        description="Source system IDs this client may submit on behalf of",
    )
    default_namespace: str | None = Field(
        default=None, description="Explicit default; must be within allowed_namespaces"
    )
    status: str = "active"


class ClientCredentials(ClientRegistration):
    """Returned once at registration; the secret is a bearer token the client
    keeps. Secrets live outside the graph (LAW-010) and are stored only as a
    hash in the control plane."""

    token: str = Field(description="Bearer token — shown once")


class AuthenticatedClient(BaseModel):
    """What every request handler sees after authn. Namespace is derived
    server-side from this record, never from request parameters."""

    model_config = ConfigDict(extra="forbid")

    client_id: str
    scopes: list[str]
    allowed_namespaces: list[str]
    allowed_sources: list[str] = Field(default_factory=lambda: ["*"])
    default_namespace: str | None = None
    status: str = "active"

    def resolved_namespace(self, settings_default: str) -> str:
        """Explicit default wins; else first allowed; must be a member."""
        ns = self.default_namespace or (
            self.allowed_namespaces[0] if self.allowed_namespaces else settings_default
        )
        return ns

    def can(self, action: AuthzAction) -> bool:
        return ACTION_SCOPES[action] in self.scopes

    def namespace_allowed(self, namespace: str) -> bool:
        return namespace in self.allowed_namespaces

    def may_submit_for_source(self, source_id: str) -> bool:
        """Source-ownership boundary (review P1): clients may only submit
        events on behalf of sources they own. '*' is dev/legacy only."""
        return "*" in self.allowed_sources or source_id in self.allowed_sources


def utcnow() -> datetime:
    return datetime.now(UTC)
