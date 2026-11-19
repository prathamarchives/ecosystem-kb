"""Supabase Auth JWT verification for MCP/REST bearer tokens.

Static client tokens remain the primary/fallback mechanism (LAW-009). When
KB_SUPABASE_URL is configured, a token that is not a registered static client
is verified as a Supabase-issued JWT against the project's public JWKS. This
lets MCP OAuth clients authenticate as Supabase users without a shared secret.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import jwt


class SupabaseJWTVerifier:
    """Verify Supabase access tokens using the project's public JWKS."""

    def __init__(self, project_url: str, anon_key: str | None = None) -> None:
        self.base = project_url.rstrip("/")
        self.issuer = f"{self.base}/auth/v1"
        self.jwks_url = f"{self.issuer}/.well-known/jwks.json"
        self.anon_key = anon_key
        self._keys: dict[str, Any] = {}
        self._fetched_at = 0.0

    async def _get_key(self, kid: str) -> Any:
        if kid not in self._keys or time.time() - self._fetched_at > 3600:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(self.jwks_url)
                r.raise_for_status()
                self._keys = {k["kid"]: k for k in r.json().get("keys", [])}
                self._fetched_at = time.time()
        key = self._keys.get(kid)
        if key is None:
            raise ValueError("unknown signing key")
        return jwt.algorithms.ECAlgorithm.from_jwk(key) if key.get("kty") == "EC" else jwt.algorithms.RSAAlgorithm.from_jwk(key)

    async def verify(self, token: str) -> dict[str, Any]:
        header = jwt.get_unverified_header(token)
        key = await self._get_key(header["kid"])
        return jwt.decode(
            token,
            key,
            algorithms=["ES256", "RS256"],
            issuer=self.issuer,
            options={"verify_aud": False},
        )
