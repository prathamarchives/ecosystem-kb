"""Bearer-token authentication + scope/namespace authorization (Step 8).

One principal resolver, no per-tool token parsing:

  - MCP (/mcp): KB OAuth access tokens ONLY. Cookies, Supabase login JWTs
    and unregistered static tokens are refused (runbook Step 8).
  - REST (/v1/*): KB OAuth bearer tokens; the dashboard API additionally
    authenticates the owner through the kb_session cookie (its routes do
    that themselves before reaching the services).
  - Legacy static client tokens (kb_clients table) are accepted only while
    KB_ALLOW_LEGACY_CLIENT_TOKENS=true (default false). Rows are preserved
    for rollback; the flag is the one-release migration bridge.

The bearer token is looked up by SHA-256 hash; the plaintext never
touches the database (LAW-009, LAW-010).
"""

from __future__ import annotations

from contracts.clients import AuthenticatedClient
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ecosystem_kb.adapters.postgres.repositories import Repositories
from ecosystem_kb.core.config import KbSettings
from ecosystem_kb.core.exceptions import AuthenticationError

_bearer = HTTPBearer(auto_error=False)


class AuthService:
    """Token → principal (OAuth client or legacy static client).

    `oauth` (the AuthorizationServer) is wired in by create_app's lifespan;
    tests can inject a stub. When absent, only legacy/static paths resolve.
    """

    def __init__(self, repos: Repositories, settings: KbSettings) -> None:
        self.repos = repos
        self.settings = settings
        self.oauth = None  # wired post-construction (AuthorizationServer)

    def _client_from_oauth_token(self, token_row) -> AuthenticatedClient:
        """Map a validated KB OAuth access-token row to an AuthenticatedClient.
        OAuth clients get the deployment's namespaces and their consented
        knowledge scopes — never admin/maintain scopes (runbook §6)."""
        scopes = [s for s in (token_row.scopes or []) if s in ("kb:read", "kb:write")]
        if not scopes:
            raise AuthenticationError("token carries no usable scope")
        return AuthenticatedClient(
            client_id=f"oauth:{token_row.client_id}",
            scopes=scopes,
            allowed_namespaces=self.settings.namespace_list,
            allowed_sources=["*"],
            default_namespace=self.settings.default_namespace
            if self.settings.default_namespace in self.settings.namespace_list
            else None,
        )

    async def authenticate_token(self, token: str) -> AuthenticatedClient:
        """Token -> client (used by the MCP mount, which reads the header itself)."""
        if not token:
            raise AuthenticationError("missing bearer token")
        # 1. KB OAuth access token (the normal path). A token that is not a
        #    valid OAuth token falls through to the static/legacy paths —
        #    only a FORMERLY-valid-but-now-revoked OAuth token raises here.
        if self.oauth is not None:
            try:
                token_row = await self.oauth.validate_access_token(token)
                return self._client_from_oauth_token(token_row)
            except AuthenticationError:
                found = await self.oauth.tokens.find(token, "access")
                if found is not None:
                    # It IS one of our OAuth tokens, just invalid/expired/
                    # revoked: do not fall through to weaker paths.
                    raise
        # 2. Legacy static client token. The pre-registered host-CLI admin
        #    client (kb-admin, seeded by `kb bootstrap --seed-admin`) remains a
        #    valid loopback credential per the runbook. Other static client
        #    tokens work in development while KB_ALLOW_STATIC_TOKENS=true,
        #    or during the migration window with KB_ALLOW_LEGACY_CLIENT_TOKENS.
        row = await self.repos.clients.find_by_token(token)
        if row is not None:
            is_admin_row = row.client_id == "kb-admin"
            static_ok = (
                (not self.settings.is_production and self.settings.allow_static_tokens)
                or self.settings.allow_legacy_client_tokens
            )
            if not is_admin_row and not static_ok:
                raise AuthenticationError(
                    "legacy static client tokens are disabled; connect via OAuth"
                )
            if row.status != "active":
                raise AuthenticationError(f"client '{row.client_id}' is {row.status}")
            return AuthenticatedClient(
                client_id=row.client_id,
                scopes=list(row.scopes or []),
                allowed_namespaces=list(row.allowed_namespaces or []),
                allowed_sources=list(getattr(row, "allowed_sources", None) or ["*"]),
            )
        # 3. Dev-mode fallback: tokens from settings act as admin clients.
        #    Production forbids this (validate_runtime_safety + default-off flag).
        if not self.settings.is_production and self.settings.allow_static_tokens:
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

    async def authenticate(
        self,
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> AuthenticatedClient:
        if credentials is None or not credentials.credentials:
            raise AuthenticationError("missing bearer token")
        return await self.authenticate_token(credentials.credentials)


def client_from_request(request: Request) -> AuthenticatedClient:
    client = getattr(request.state, "client", None)
    if client is None:
        raise AuthenticationError("no authenticated client on request")
    return client
