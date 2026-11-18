"""Bearer-token authentication + scope/namespace authorization middleware."""

from __future__ import annotations

from contracts.clients import AuthenticatedClient
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ecosystem_kb.adapters.postgres.repositories import Repositories
from ecosystem_kb.core.config import KbSettings
from ecosystem_kb.core.exceptions import AuthenticationError

_bearer = HTTPBearer(auto_error=False)


class AuthService:
    """Token → control-plane client record → AuthenticatedClient.

    The bearer token is looked up by SHA-256 hash; the plaintext never
    touches the database (LAW-009, LAW-010).
    """

    def __init__(self, repos: Repositories, settings: KbSettings) -> None:
        self.repos = repos
        self.settings = settings

    async def authenticate_token(self, token: str) -> AuthenticatedClient:
        """Token -> client (used by the MCP mount, which reads the header itself)."""
        if not token:
            raise AuthenticationError("missing bearer token")
        row = await self.repos.clients.find_by_token(token)
        if row is None:
            if token in self.settings.token_list:
                return AuthenticatedClient(
                    client_id="local-dev",
                    scopes=["kb:read", "kb:write", "kb:review", "kb:maintain", "kb:admin"],
                    allowed_namespaces=self.settings.namespace_list,
                )
            raise AuthenticationError("invalid token")
        if row.status != "active":
            raise AuthenticationError(f"client '{row.client_id}' is {row.status}")
        return AuthenticatedClient(
            client_id=row.client_id,
            scopes=list(row.scopes or []),
            allowed_namespaces=list(row.allowed_namespaces or []),
        )

    async def authenticate(
        self,
        credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> AuthenticatedClient:
        if credentials is None or not credentials.credentials:
            raise AuthenticationError("missing bearer token")
        row = await self.repos.clients.find_by_token(credentials.credentials)
        if row is None:
            # Dev-mode fallback: tokens from settings act as admin clients.
            if credentials.credentials in self.settings.token_list:
                return AuthenticatedClient(
                    client_id="local-dev",
                    scopes=["kb:read", "kb:write", "kb:review", "kb:maintain", "kb:admin"],
                    allowed_namespaces=self.settings.namespace_list,
                )
            raise AuthenticationError("invalid token")
        if row.status != "active":
            raise AuthenticationError(f"client '{row.client_id}' is {row.status}")
        return AuthenticatedClient(
            client_id=row.client_id,
            scopes=list(row.scopes or []),
            allowed_namespaces=list(row.allowed_namespaces or []),
        )


def client_from_request(request: Request) -> AuthenticatedClient:
    client = getattr(request.state, "client", None)
    if client is None:
        raise AuthenticationError("no authenticated client on request")
    return client
