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
        self._supabase = None
        if settings.supabase_url:
            from ecosystem_kb.api.supabase_auth import SupabaseJWTVerifier

            self._supabase = SupabaseJWTVerifier(
                settings.supabase_url, settings.supabase_anon_key
            )

    async def _supabase_client(self, token: str) -> AuthenticatedClient | None:
        """Verify a Supabase-issued JWT and map its claims to a KB client."""
        if self._supabase is None:
            return None
        try:
            claims = await self._supabase.verify(token)
        except Exception:
            return None
        sub = claims.get("sub") or claims.get("user_id")
        if not sub:
            return None
        meta = claims.get("app_metadata") or {}
        scopes = meta.get("kb_scopes") or ["kb:read", "kb:write"]
        namespaces = meta.get("kb_namespaces") or self.settings.namespace_list
        if isinstance(namespaces, str):
            namespaces = [namespaces]
        return AuthenticatedClient(
            client_id=f"supabase:{sub}",
            scopes=list(scopes),
            allowed_namespaces=list(namespaces),
            allowed_sources=["*"],
            default_namespace=self.settings.default_namespace
            if self.settings.default_namespace in namespaces
            else None,
        )

    async def authenticate_token(self, token: str) -> AuthenticatedClient:
        """Token -> client (used by the MCP mount, which reads the header itself)."""
        if not token:
            raise AuthenticationError("missing bearer token")
        row = await self.repos.clients.find_by_token(token)
        if row is None:
            oauth_client = await self._supabase_client(token)
            if oauth_client is not None:
                return oauth_client
            # Static fallback tokens are a DEVELOPMENT convenience only.
            if self.settings.is_production:
                raise AuthenticationError("invalid token")
            if token in self.settings.token_list:
                return AuthenticatedClient(
                    client_id="local-dev",
                    scopes=["kb:read", "kb:write", "kb:review", "kb:maintain", "kb:admin"],
                    allowed_namespaces=self.settings.namespace_list,
                    default_namespace=self.settings.default_namespace
                    if self.settings.default_namespace in self.settings.namespace_list
                    else None,
                )
            raise AuthenticationError("invalid token")
        if row.status != "active":
            raise AuthenticationError(f"client '{row.client_id}' is {row.status}")
        return AuthenticatedClient(
            client_id=row.client_id,
            scopes=list(row.scopes or []),
            allowed_namespaces=list(row.allowed_namespaces or []),
            allowed_sources=list(getattr(row, "allowed_sources", None) or ["*"]),
        )

    async def authenticate(
        self,
        credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> AuthenticatedClient:
        if credentials is None or not credentials.credentials:
            raise AuthenticationError("missing bearer token")
        row = await self.repos.clients.find_by_token(credentials.credentials)
        if row is None:
            oauth_client = await self._supabase_client(credentials.credentials)
            if oauth_client is not None:
                return oauth_client
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
