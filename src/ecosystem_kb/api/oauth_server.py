"""First-party OAuth 2.1 Authorization Server service (runbook Step 7).

Ecosystem KB is the issuer. Owner identity comes from the kb_session
cookie (Step 6); this service issues opaque, resource-bound, hashed,
DB-backed tokens. Authlib supplies protocol primitives (PKCE S256
challenge verification, token generation); all state and policy live
here and in the control plane.

Design decisions (runbook §5.1-5.2, §7):
  - Authorization Code grant only, PKCE S256 required, no client secrets
    for public MCP clients (token_endpoint_auth_method=none).
  - Exact redirect URI matching. No wildcards, ever.
  - The canonical `resource` must equal KB_MCP_RESOURCE on authorize,
    token and every /mcp request.
  - Access tokens 1h, refresh tokens 30d rotating on use; refresh replay
    revokes the whole family (detected via consumed_at).
  - Codes: random, one-use, 5 minutes, stored hashed.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from authlib.integrations.base_client.errors import OAuthError
from authlib.oauth2.rfc7636.challenge import create_s256_code_challenge

from ecosystem_kb.adapters.postgres.oauth_repositories import (
    AuthorizationCodeRepository,
    ConsentRepository,
    OauthClientRepository,
    PendingAuthorizationRepository,
    SessionRepository,
    TokenRepository,
    _aware,
    new_request_id,
    sha256_hex,
)
from ecosystem_kb.core.config import KbSettings
from ecosystem_kb.core.exceptions import AuthenticationError

logger = logging.getLogger(__name__)

ALLOWED_SCOPES = ("kb:read", "kb:write", "offline_access")
DEFAULT_SCOPES = ("kb:read", "kb:write")


class OauthProtocolError(Exception):
    """Protocol violation with an RFC 6749 error code. `redirect=False`
    means the error must NOT be sent back to the (untrusted) redirect URI."""

    def __init__(self, error: str, description: str, status: int = 400, redirect: bool = True) -> None:
        super().__init__(description)
        self.error = error
        self.description = description
        self.status = status
        self.redirect = redirect


def verify_pkce_s256(verifier: str, challenge: str) -> bool:
    """S256 check per RFC 7636: base64url(sha256(ascii(verifier)))."""
    if not verifier or not challenge:
        return False
    computed = create_s256_code_challenge(verifier)
    return secrets.compare_digest(computed, challenge)


@dataclass
class TokenPair:
    access_token: str
    refresh_token: str | None
    token_type: str
    expires_in: int
    scope: str


class AuthorizationServer:
    """Protocol facade over the OAuth repositories. One instance per app."""

    def __init__(self, repos, settings: KbSettings) -> None:
        self.sessions: SessionRepository = repos["sessions"]
        self.clients: OauthClientRepository = repos["clients"]
        self.pending: PendingAuthorizationRepository = repos["pending"]
        self.codes: AuthorizationCodeRepository = repos["codes"]
        self.tokens: TokenRepository = repos["tokens"]
        self.consents: ConsentRepository = repos["consents"]
        self.audit = repos["audit"]
        self.settings = settings

    # -- registration (Step 7.4) ------------------------------------------

    async def resolve_client(self, client_id: str) -> object | None:
        """Find a registered client, or fetch+register a CIMD document when
        client_id is an HTTPS URL (client_id_metadata_document)."""
        row = await self.clients.get(client_id)
        if row is not None:
            return row
        if client_id.startswith("https://"):
            metadata = await self._fetch_cimd(client_id)
            if metadata is not None:
                return await self.clients.upsert(
                    client_id=client_id,
                    client_name=metadata["client_name"],
                    registration_mode="cimd",
                    redirect_uris=metadata["redirect_uris"],
                    token_endpoint_auth_method=metadata.get(
                        "token_endpoint_auth_method", "none"
                    ),
                    metadata_url=client_id,
                    metadata_hash=sha256_hex(repr(metadata)),
                )
        return None

    async def _fetch_cimd(self, url: str) -> dict | None:
        """Hardened HTTPS fetch of a Client ID Metadata Document (SSRF-safe).
        Overrides in tests; production fetch is strict."""
        from ecosystem_kb.api.cimd_fetcher import fetch_cimd_document

        try:
            return await fetch_cimd_document(url)
        except Exception as exc:  # noqa: BLE001 — registration must fail soft
            logger.warning("CIMD fetch failed for %s: %s", url, exc)
            return None

    async def register_dcr(self, payload: dict) -> dict:
        """RFC 7591 dynamic registration. Public clients only: no client
        secrets are generated (runbook §5.4)."""
        redirect_uris = payload.get("redirect_uris")
        if not isinstance(redirect_uris, list) or not redirect_uris:
            raise OauthProtocolError("invalid_redirect_uri", "redirect_uris required")
        for uri in redirect_uris:
            if not isinstance(uri, str) or not uri.startswith(("https://", "http://localhost:", "http://127.0.0.1:")):
                raise OauthProtocolError(
                    "invalid_redirect_uri", "redirect URIs must be https or loopback"
                )
        grant_types = payload.get("grant_types") or ["authorization_code", "refresh_token"]
        if any(g not in ("authorization_code", "refresh_token") for g in grant_types):
            raise OauthProtocolError("unsupported_grant_type", "unsupported grant type")
        response_types = payload.get("response_types") or ["code"]
        if response_types != ["code"]:
            raise OauthProtocolError("unsupported_response_type", "only 'code' is supported")
        auth_method = payload.get("token_endpoint_auth_method", "none")
        if auth_method != "none":
            raise OauthProtocolError(
                "invalid_client_metadata",
                "only public clients (token_endpoint_auth_method=none) are supported",
            )
        scopes = [s for s in (payload.get("scope") or "").split() if s in ALLOWED_SCOPES]
        client_id = "client-" + secrets.token_urlsafe(16)
        row = await self.clients.upsert(
            client_id=client_id,
            client_name=payload.get("client_name") or client_id,
            registration_mode="dcr",
            redirect_uris=redirect_uris,
            token_endpoint_auth_method="none",
            grant_types=grant_types,
            response_types=response_types,
            scopes=scopes or list(DEFAULT_SCOPES) + ["offline_access"],
        )
        await self.audit.record(
            actor="oauth:server",
            action="client_registered",
            resource_type="oauth_client",
            resource_id=client_id,
            reason=f"mode=dcr redirect_uris={len(redirect_uris)}",
        )
        return {
            "client_id": row.client_id,
            "client_name": row.client_name,
            "redirect_uris": row.redirect_uris,
            "token_endpoint_auth_method": "none",
            "grant_types": row.grant_types,
            "response_types": row.response_types,
        }

    # -- authorization endpoint (Step 7.6) ---------------------------------

    async def begin_authorization(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        state: str | None,
        code_challenge: str | None,
        code_challenge_method: str | None,
        resource: str | None,
        scope: str | None,
        response_type: str | None,
    ) -> tuple[object, str]:
        """Validate an authorization request; returns (client, request_id).

        The request is persisted server-side; the browser carries only the
        opaque request_id through login. Raises OauthProtocolError with
        `redirect` set when the error must go back to the client."""
        if response_type != "code":
            raise OauthProtocolError("unsupported_response_type", "response_type must be 'code'")
        client = await self.resolve_client(client_id)
        if client is None or client.disabled_at is not None:
            raise OauthProtocolError(
                "unauthorized_client", "unknown client", status=400, redirect=False
            )
        if redirect_uri not in (client.redirect_uris or []):
            # Never redirect a mismatched URI (RFC 6749 §4.1.2.1).
            raise OauthProtocolError(
                "invalid_redirect_uri", "redirect_uri not registered", redirect=False
            )
        if not code_challenge or code_challenge_method != "S256":
            raise OauthProtocolError(
                "invalid_request", "PKCE S256 code_challenge required"
            )
        canonical = self.settings.canonical_mcp_resource
        if not resource or not canonical or resource.rstrip("/") != canonical:
            raise OauthProtocolError(
                "invalid_target", "the resource parameter must be the KB MCP resource"
            )
        scopes = [s for s in (scope or " ".join(DEFAULT_SCOPES)).split() if s]
        unknown = [s for s in scopes if s not in ALLOWED_SCOPES]
        if unknown:
            raise OauthProtocolError("invalid_scope", f"unknown scopes: {unknown}")
        if "offline_access" in scopes and "offline_access" not in (client.scopes or ALLOWED_SCOPES) and client.registration_mode == "pre_registered":
            raise OauthProtocolError("invalid_scope", "offline_access not permitted for this client")

        request_id = new_request_id()
        await self.pending.create(
            request_hash=sha256_hex(request_id),
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=state,
            code_challenge=code_challenge,
            code_challenge_method="S256",
            resource=canonical,
            scopes=scopes,
        )
        return client, request_id

    async def pending_for(self, request_id: str):
        row = await self.pending.get(request_id)
        if row is None or _aware(row.expires_at) < datetime.now(UTC) or row.status != "pending":
            return None
        return row

    async def existing_consent_scopes(self, owner_subject: str, client_id: str, resource: str):
        row = await self.consents.get(owner_subject, client_id, resource)
        return list(row.scopes or []) if row else None

    async def decide(
        self, request_id: str, owner_subject: str, session_id: str, approve: bool
    ) -> tuple[str, str, str | None]:
        """Owner decision on a pending authorization. Returns
        (redirect_uri, code, state) on approve; (redirect_uri, error, state)
        on deny. Raises when the pending request is gone/expired/bound
        elsewhere."""
        row = await self.pending_for(request_id)
        if row is None:
            raise OauthProtocolError("invalid_request", "authorization request expired")
        if row.session_id is not None and row.session_id != session_id:
            raise OauthProtocolError("invalid_request", "authorization request bound to another session")
        await self.pending.set_status(request_id, "approved" if approve else "denied")
        if not approve:
            await self.audit.record(
                actor=f"owner:{owner_subject}",
                action="oauth_consent_denied",
                resource_type="oauth_client",
                resource_id=row.client_id,
            )
            return row.redirect_uri, "access_denied", row.state
        await self.consents.upsert(owner_subject, row.client_id, row.resource, list(row.scopes))
        code = await self.codes.create(
            client_id=row.client_id,
            redirect_uri=row.redirect_uri,
            code_challenge=row.code_challenge,
            code_challenge_method=row.code_challenge_method,
            owner_subject=owner_subject,
            session_id=session_id,
            resource=row.resource,
            scopes=list(row.scopes),
            ttl_seconds=self.settings.auth_code_seconds,
        )
        await self.audit.record(
            actor=f"owner:{owner_subject}",
            action="oauth_consent_granted",
            resource_type="oauth_client",
            resource_id=row.client_id,
            reason=f"scopes={','.join(row.scopes)} resource={row.resource}",
        )
        return row.redirect_uri, code, row.state

    # -- token endpoint (Step 7.1) -----------------------------------------

    async def exchange_code(
        self,
        *,
        grant_type: str,
        code: str | None,
        redirect_uri: str | None,
        client_id: str | None,
        code_verifier: str | None,
        resource: str | None,
    ) -> TokenPair:
        if grant_type != "authorization_code":
            raise OauthProtocolError(
                "unsupported_grant_type", "only authorization_code and refresh_token"
            )
        if not code or not client_id or not redirect_uri or not code_verifier:
            raise OauthProtocolError("invalid_request", "missing required parameters")
        row = await self.codes.consume(code)
        if row is None:
            raise OauthProtocolError("invalid_grant", "code invalid, expired or reused")
        if row.client_id != client_id or row.redirect_uri != redirect_uri:
            raise OauthProtocolError("invalid_grant", "code does not match client/redirect")
        canonical = self.settings.canonical_mcp_resource
        if not resource or not canonical or resource.rstrip("/") != canonical or row.resource != canonical:
            raise OauthProtocolError("invalid_target", "resource mismatch")
        if not verify_pkce_s256(code_verifier, row.code_challenge):
            raise OauthProtocolError("invalid_grant", "PKCE verification failed")
        # offline_access rides ON the stored scopes (it is a permission
        # marker, not a knowledge scope) so refresh can re-issue it; the
        # client mapping strips it later.
        return await self._issue_pair(
            client_id=row.client_id,
            owner_subject=row.owner_subject,
            resource=row.resource,
            scopes=list(row.scopes or []),
            with_refresh="offline_access" in (row.scopes or []),
        )

    async def refresh(self, *, grant_type: str, refresh_token: str | None, resource: str | None, client_id: str | None) -> TokenPair:
        if grant_type != "refresh_token":
            raise OauthProtocolError("unsupported_grant_type", "unsupported grant type")
        if not refresh_token:
            raise OauthProtocolError("invalid_request", "refresh_token required")
        # Validate client binding and resource BEFORE consuming: a request
        # with the wrong resource or client must not burn the token.
        preview = await self.tokens.find(refresh_token, "refresh")
        if preview is not None:
            if client_id and preview.client_id != client_id:
                raise OauthProtocolError("invalid_grant", "refresh token belongs to another client")
            canonical = self.settings.canonical_mcp_resource
            if not resource or not canonical or resource.rstrip("/") != canonical or preview.resource != canonical:
                raise OauthProtocolError("invalid_target", "resource mismatch")
        row = await self.tokens.consume_refresh(refresh_token)
        if row is None:
            # Possible replay of an already-rotated token: revoke the family.
            found = preview or await self.tokens.find(refresh_token, "refresh")
            if found is not None:
                count = await self.tokens.revoke_family(found.family_id)
                await self.audit.record(
                    actor="oauth:server",
                    action="token_family_revoked",
                    resource_type="oauth_token_family",
                    resource_id=found.family_id,
                    reason=f"refresh replay detected; revoked {count} tokens",
                )
            raise OauthProtocolError("invalid_grant", "refresh token invalid or replayed")
        await self.tokens.revoke_family(row.family_id)  # rotation: old pair dies now
        return await self._issue_pair(
            client_id=row.client_id,
            owner_subject=row.owner_subject,
            resource=row.resource,
            scopes=list(row.scopes or []),
            with_refresh="offline_access" in (row.scopes or []),
            family_id=row.family_id,
        )

    async def _issue_pair(
        self,
        *,
        client_id: str,
        owner_subject: str,
        resource: str,
        scopes: list[str],
        with_refresh: bool,
        family_id: str | None = None,
    ) -> TokenPair:
        family = family_id or secrets.token_urlsafe(16)
        access, _ = await self.tokens.issue(
            token_type="access",
            client_id=client_id,
            owner_subject=owner_subject,
            resource=resource,
            scopes=scopes,
            family_id=family,
            ttl_seconds=self.settings.access_token_seconds,
        )
        refresh = None
        if with_refresh:
            refresh, _ = await self.tokens.issue(
                token_type="refresh",
                client_id=client_id,
                owner_subject=owner_subject,
                resource=resource,
                scopes=scopes,
                family_id=family,
                ttl_seconds=self.settings.refresh_token_seconds,
            )
        await self.clients.touch_used(client_id)
        return TokenPair(
            access_token=access,
            refresh_token=refresh,
            token_type="Bearer",
            expires_in=self.settings.access_token_seconds,
            scope=" ".join(scopes),
        )

    # -- revocation (Step 7.1) ---------------------------------------------

    async def revoke_token(self, token: str) -> bool:
        """RFC 7009. Unknown tokens return 200 (no information leak)."""
        row = await self.tokens.find(token, "access") or await self.tokens.find(token, "refresh")
        if row is None:
            return True
        count = await self.tokens.revoke_family(row.family_id)
        await self.audit.record(
            actor="oauth:server",
            action="token_revoked",
            resource_type="oauth_token_family",
            resource_id=row.family_id,
            reason=f"revoked {count} tokens",
        )
        return True

    async def revoke_client(self, client_id: str, *, actor: str) -> int:
        """Dashboard revocation: every token for this client stops working
        immediately (direct indexed DB writes, no cache)."""
        await self.clients.disable(client_id)
        count = await self.tokens.revoke_client(client_id)
        await self.audit.record(
            actor=actor,
            action="client_revoked",
            resource_type="oauth_client",
            resource_id=client_id,
            reason=f"revoked {count} tokens",
        )
        return count

    # -- resource validation (Step 8) ---------------------------------------

    async def validate_access_token(self, token: str) -> object:
        """Full MCP bearer check: hash lookup, active, client enabled,
        resource bound to THIS server's canonical resource."""
        row = await self.tokens.find(token, "access")
        if row is None or not await self.tokens.is_active(row):
            raise AuthenticationError("invalid or expired token")
        client = await self.clients.get(row.client_id)
        if client is None or client.disabled_at is not None:
            raise AuthenticationError(f"client '{row.client_id}' is disabled")
        canonical = self.settings.canonical_mcp_resource
        if canonical and row.resource.rstrip("/") != canonical:
            raise AuthenticationError("token is not valid for this resource")
        await self.tokens.mark_used(row.id)
        return row


# Re-exports used by routes.
_ = (OAuthError, hashlib)  # noqa: F401 — kept for protocol debugging
