"""Core configuration (pydantic-settings). Local defaults are dev-safe."""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# One-release alias: KB_PUBLIC_URL -> KB_PUBLIC_BASE_URL (runbook Step 5).
# Keep only until the owner's .env is confirmed migrated, then delete.


class KbSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KB_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # 'production' disables the static dev-token fallback (review P1).
    environment: str = "development"

    # Control plane
    database_url: str = "sqlite+aiosqlite:///./data/kb_dev.db"

    # Graph store
    graph_host: str = "127.0.0.1"
    graph_port: int = 6379
    graph_password: str | None = None
    graph_database: str = "ecosystem_prod"

    # Gateway
    api_tokens: str = "local-dev-token-1"
    default_namespace: str = "ecosystem_prod"
    allowed_namespaces: str = "ecosystem_dev,ecosystem_test,ecosystem_prod"

    # Inference (production requires a real provider; tests use deterministic fakes)
    llm_provider: str = "fake"
    llm_model: str = "fake-extractor"
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    embedder_model: str = "fake-embedder"
    embedding_dim: int = 1536
    reranker: str = "fake"

    # Public origin + MCP resource (runbook Step 5). base URL is origin only:
    # scheme + host, no path, no trailing slash. mcp_resource is the exact
    # canonical resource URI (/mcp, no trailing slash). When mcp_resource is
    # not set it is derived from the public base URL.
    public_base_url: str | None = None
    mcp_resource: str | None = None
    supabase_url: str | None = None
    supabase_anon_key: str | None = None

    # Owner identity for first-party sessions (runbook Step 6): the immutable
    # Supabase auth user UUID (sub), never an email.
    owner_supabase_subject: str | None = None

    # Session lifetime (days) for the owner browser session (Step 6: 7-day
    # absolute lifetime initially).
    owner_session_days: int = 7

    # OAuth token lifetimes (runbook Step 7): authorization codes 5 minutes,
    # access tokens 1 hour, refresh tokens 30 days, rotating on every use.
    auth_code_seconds: int = 300
    access_token_seconds: int = 3600
    refresh_token_seconds: int = 30 * 24 * 3600

    # Compatibility gates (runbook Step 8). Both default false; the
    # Supabase-user-JWT-as-MCP-token path is REMOVED, not flagged.
    allow_static_tokens: bool = False
    allow_legacy_client_tokens: bool = False

    # Limits
    max_event_body_chars: int = 100_000
    worker_lease_seconds: int = 300
    worker_max_attempts: int = 5

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in ("production", "prod")

    @property
    def canonical_mcp_resource(self) -> str | None:
        """The canonical MCP resource URI: explicit setting wins, else
        derived from the public base URL as <base>/mcp."""
        if self.mcp_resource:
            return self.mcp_resource.rstrip("/")
        if self.public_base_url:
            return self.public_base_url.rstrip("/") + "/mcp"
        return None

    def validate_runtime_safety(self) -> None:
        """Fail fast on unsafe production configurations (review P1 +
        runbook Step 5: URL/config contract validation)."""
        if self.is_production:
            if not self.public_base_url or not self.public_base_url.startswith("https://"):
                raise RuntimeError(
                    "KB_PUBLIC_BASE_URL must be an HTTPS origin in production "
                    "(scheme + host, no path, no trailing slash)"
                )
            if self.public_base_url != self.public_base_url.rstrip("/"):
                raise RuntimeError("KB_PUBLIC_BASE_URL must not have a trailing slash")
            resource = self.canonical_mcp_resource
            if not resource or not resource.startswith(self.public_base_url + "/"):
                raise RuntimeError(
                    "KB_MCP_RESOURCE must belong to the KB_PUBLIC_BASE_URL origin"
                )
            if self.allow_static_tokens:
                raise RuntimeError(
                    "KB_ALLOW_STATIC_TOKENS=true is not permitted in production"
                )
            weak = [t for t in self.token_list if len(t) < 32]
            if weak and self.allow_legacy_client_tokens:
                raise RuntimeError(
                    "refusing to start in production with weak static dev tokens; "
                    "register real clients and remove KB_API_TOKENS"
                )
            if "local-dev-token-1" in self.token_list and self.allow_legacy_client_tokens:
                raise RuntimeError("known development token present in production config")

    @property
    def token_list(self) -> list[str]:
        return [t.strip() for t in self.api_tokens.split(",") if t.strip()]

    @property
    def namespace_list(self) -> list[str]:
        return [n.strip() for n in self.allowed_namespaces.split(",") if n.strip()]


def _apply_public_url_alias() -> None:
    """One-release alias: KB_PUBLIC_URL -> KB_PUBLIC_BASE_URL when only the
    old name is set. Logs a deprecation warning (runbook Step 5)."""
    new = os.environ.get("KB_PUBLIC_BASE_URL")
    old = os.environ.get("KB_PUBLIC_URL")
    if old and not new:
        os.environ["KB_PUBLIC_BASE_URL"] = old
        logger.warning(
            "KB_PUBLIC_URL is deprecated; use KB_PUBLIC_BASE_URL (alias applied)"
        )


_apply_public_url_alias()


@lru_cache
def get_settings() -> KbSettings:
    return KbSettings()
