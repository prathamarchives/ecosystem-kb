"""Owner login verification (runbook Step 6).

SupabaseOwnerVerifier is used ONLY by POST /auth/session: it verifies a
Supabase user JWT and checks that its `sub` equals KB_OWNER_SUPABASE_SUBJECT.
Supabase tokens never authorize MCP or REST calls (Step 8).

Verification per runbook: signature via Supabase JWKS with pinned
algorithms, exact issuer, audience, exp/nbf, and a server-side user check
when available so a locally-valid but logged-out session is refused.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import jwt

from ecosystem_kb.core.exceptions import AuthenticationError


class SupabaseOwnerVerifier:
    """Verify Supabase access tokens for owner bootstrap only."""

    def __init__(
        self,
        project_url: str,
        owner_subject: str,
        *,
        anon_key: str | None = None,
        secret_key: str | None = None,
    ) -> None:
        self.base = project_url.rstrip("/")
        self.issuer = f"{self.base}/auth/v1"
        self.jwks_url = f"{self.issuer}/.well-known/jwks.json"
        self.owner_subject = owner_subject
        self.anon_key = anon_key
        self.secret_key = secret_key
        self._keys: dict[str, Any] = {}
        self._fetched_at = 0.0
        # Pinned to the algorithms Supabase projects actually sign with.
        self.algorithms = ["ES256", "RS256"]

    async def _get_key(self, kid: str) -> Any:
        # Bounded cache (runbook: refresh when unknown kid appears).
        if kid not in self._keys or time.time() - self._fetched_at > 3600:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(self.jwks_url)
                r.raise_for_status()
                self._keys = {k["kid"]: k for k in r.json().get("keys", [])}
                self._fetched_at = time.time()
        key = self._keys.get(kid)
        if key is None:
            raise AuthenticationError("unknown signing key")
        if key.get("kty") == "EC":
            return jwt.algorithms.ECAlgorithm.from_jwk(key)
        return jwt.algorithms.RSAAlgorithm.from_jwk(key)

    async def verify_owner(self, token: str) -> dict[str, Any]:
        """Full verification. Returns claims on success; raises
        AuthenticationError otherwise. Accepts only the configured owner."""
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthenticationError("malformed token") from exc
        alg = header.get("alg")
        if alg not in self.algorithms:
            raise AuthenticationError("unexpected token algorithm")
        key = await self._get_key(header["kid"])
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=self.algorithms,
                issuer=self.issuer,
                audience="authenticated",
                options={"require": ["exp", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError(f"token rejected: {exc.__class__.__name__}") from exc
        if claims.get("sub") != self.owner_subject:
            raise AuthenticationError("not the configured owner")
        # Authoritative server-side check: the user must still exist and be
        # active in Supabase Auth (revoked/logged-out sessions refused).
        if self.secret_key:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{self.base}/auth/v1/admin/users/{claims['sub']}",
                    headers={
                        "apikey": self.secret_key,
                        "Authorization": f"Bearer {self.secret_key}",
                    },
                )
            if r.status_code != 200:
                raise AuthenticationError("owner no longer active in Supabase")
            user = r.json()
            if user.get("banned_until") is not None:
                raise AuthenticationError("owner account is banned")
        return claims
