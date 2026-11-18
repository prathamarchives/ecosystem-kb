"""Graphiti client factory: builds the engine with our ontology injected.

Production: FalkorDriver against the compose-stack FalkorDB, real LLM/embedder
providers. Tests/dev: embedded FalkorDB + deterministic clients (ADR-004,
ADR-011). Either way the SAME driver code path runs.
"""

from __future__ import annotations

from typing import Any

from graphiti_core.cross_encoder.client import CrossEncoderClient
from graphiti_core.driver.falkordb_driver import FalkorDriver
from graphiti_core.embedder.client import EmbedderClient
from graphiti_core.graphiti import Graphiti
from graphiti_core.llm_client.client import LLMClient

from ecosystem_kb.adapters.graphiti.fakes import (
    DeterministicEmbedder,
    DeterministicReranker,
    MarkerExtractionLLM,
)
from ecosystem_kb.core.config import KbSettings


def build_llm_client(settings: KbSettings) -> LLMClient:
    if settings.llm_provider == "fake":
        return MarkerExtractionLLM()
    if settings.llm_provider == "openai":
        from graphiti_core.llm_client.config import LLMConfig
        from graphiti_core.llm_client.openai_client import OpenAIClient

        return OpenAIClient(
            config=LLMConfig(
                api_key=settings.llm_api_key,
                model=settings.llm_model,
                small_model=settings.llm_model,
            )
        )
    raise ValueError(f"unsupported llm provider: {settings.llm_provider}")


def build_embedder(settings: KbSettings) -> EmbedderClient:
    if settings.llm_provider == "fake":
        return DeterministicEmbedder()
    from graphiti_core.embedder.client import EmbedderConfig
    from graphiti_core.embedder.openai import OpenAIEmbedder

    return OpenAIEmbedder(config=EmbedderConfig(embedding_dim=1536))


def build_cross_encoder(settings: KbSettings) -> CrossEncoderClient:
    if settings.llm_provider == "fake":
        return DeterministicReranker()
    from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient

    return OpenAIRerankerClient()


def build_graphiti(
    settings: KbSettings,
    *,
    falkor_client: Any | None = None,
    host: str | None = None,
    port: int | None = None,
) -> Graphiti:
    """Assemble Graphiti with our injected clients.

    falkor_client lets tests pass an falkordb.asyncio.FalkorDB connected to
    the embedded server; production builds its own from settings.
    """
    driver = FalkorDriver(
        host=host or settings.graph_host,
        port=port or settings.graph_port,
        password=settings.graph_password or None,
        falkor_db=falkor_client,
        database=settings.graph_database,
    )
    return Graphiti(
        graph_driver=driver,
        llm_client=build_llm_client(settings),
        embedder=build_embedder(settings),
        cross_encoder=build_cross_encoder(settings),
    )
