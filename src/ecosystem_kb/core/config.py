"""Core configuration (pydantic-settings). Local defaults are dev-safe."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class KbSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KB_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

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
    embedder_model: str = "fake-embedder"

    # Limits
    max_event_body_chars: int = 100_000
    worker_lease_seconds: int = 300
    worker_max_attempts: int = 5

    @property
    def token_list(self) -> list[str]:
        return [t.strip() for t in self.api_tokens.split(",") if t.strip()]

    @property
    def namespace_list(self) -> list[str]:
        return [n.strip() for n in self.allowed_namespaces.split(",") if n.strip()]


@lru_cache
def get_settings() -> KbSettings:
    return KbSettings()
