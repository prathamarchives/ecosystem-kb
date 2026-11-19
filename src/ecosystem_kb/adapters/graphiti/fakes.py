"""Deterministic test clients for Graphiti (ADR-011).

The marker protocol: tests embed a JSON payload after 'EXTRACTION:' in the
episode body; the fake LLM returns it shaped to graphiti's extraction
schemas. Contracts verified against graphiti-core 0.31.2 source:

  - LLMClient._generate_response(messages, response_model, max_tokens,
    model_size, *, model=None) — positional, called via tenacity retry.
  - ExtractedEntities: {extracted_entities: [{name, entity_type_id,
    episode_indices}]}
  - ExtractedEdges: {edges: [{source_entity_name, target_entity_name,
    relation_type, fact, valid_at?, invalid_at?}]}
  - SummarizedEntities: {summaries: [{name, summary}]}
  - NodeResolutions: {entity_resolutions: [...]} (dedupe)
  - EdgeDuplicate: {duplicate_facts: [int], contradicted_facts: [int]}
  - EdgeTimestamps: {valid_at, invalid_at}
  - Attribute extraction: response_model IS the entity projection class —
    fill every field with defaults/None-safe values.
  - EmbedderClient.create() must return ONE flat vector even for list input
    (graphiti passes [query]); create_batch returns one vector per string.
  - CrossEncoderClient.rank(query, passages) -> [(passage, score)].
"""

from __future__ import annotations

import hashlib
import json

from graphiti_core.cross_encoder.client import CrossEncoderClient
from graphiti_core.embedder.client import EmbedderClient
from graphiti_core.llm_client.client import LLMClient
from graphiti_core.llm_client.config import LLMConfig

MARKER = "EXTRACTION:"
_DECODER = json.JSONDecoder()


def _payload_from(messages) -> dict | None:
    """Last marker in any message; raw_decode so trailing prose is ignored."""
    for m in reversed(messages):
        content = getattr(m, "content", None) or ""
        idx = content.rfind(MARKER)
        if idx >= 0:
            try:
                obj, _ = _DECODER.raw_decode(content[idx + len(MARKER):].lstrip())
                return obj
            except json.JSONDecodeError:
                continue
    return None


def _default_for_response_model(response_model) -> dict:
    """Build a valid all-defaults instance payload for any response model.

    Uses each field's own default when one exists (None fails str validation
    on defaulted-but-not-optional fields); falls back to [] for arrays and
    None only for genuinely optional fields.
    """
    out: dict = {}
    for name, field in response_model.model_fields.items():
        if field.is_required():
            ann = field.annotation
            if ann is list or getattr(ann, "__origin__", None) is list:
                out[name] = []
            else:
                out[name] = None
        else:
            out[name] = field.get_default(call_default_factory=True)
    return out


class DeterministicEmbedder(EmbedderClient):
    """Hash-based vectors. Same text → same vector; no network."""

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        reps = self.dim // len(digest) + 1
        return [b / 255.0 for b in (digest * reps)[: self.dim]]

    async def create(self, input_data) -> list[float]:
        if isinstance(input_data, str):
            return self._vec(input_data)
        data = list(input_data)
        if data and isinstance(data[0], int):
            return self._vec(" ".join(map(str, data)))
        return self._vec(str(data[0]))

    async def create_batch(self, input_data_list) -> list[list[float]]:
        return [self._vec(t) for t in input_data_list]


class DeterministicReranker(CrossEncoderClient):
    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        return list(zip(passages, [1.0] * len(passages), strict=True))


class MarkerExtractionLLM(LLMClient):
    """Returns marker payloads for extraction calls; benign empties otherwise."""

    def __init__(self) -> None:
        super().__init__(LLMConfig(api_key="deterministic", model="marker-extraction"))

    async def _generate_response(
        self, messages, response_model=None, max_tokens=2000, model_size="medium", **kwargs
    ):
        if response_model is None:
            return {}

        model_name = getattr(response_model, "__name__", "")
        payload = _payload_from(messages)

        if model_name == "CombinedExtraction":
            # Bulk path (one call for entities + edges together).
            entities = payload.get("entities", []) if payload else []
            edges = payload.get("edges", []) if payload else []
            return {"extracted_entities": entities, "edges": edges}
        if model_name == "ExtractedEntities":
            entities = payload.get("entities", []) if payload else []
            return {"extracted_entities": entities}
        if model_name == "ExtractedEdges":
            edges = payload.get("edges", []) if payload else []
            return {"edges": edges}
        if model_name == "EdgeDuplicate":
            duplicates = payload.get("duplicate_fact_indices", []) if payload else []
            contradictions = payload.get("contradicted_fact_indices", []) if payload else []
            return {
                "duplicate_facts": [int(i) for i in duplicates],
                "contradicted_facts": [int(i) for i in contradictions],
            }
        if model_name == "NodeResolutions":
            resolutions = payload.get("resolutions", []) if payload else []
            return {"entity_resolutions": resolutions}
        if model_name == "SummarizedEntities":
            return {"summaries": []}

        # Attribute extraction (response_model == entity projection class):
        # fill from the payload's entity_attributes when present; otherwise
        # return each field's own default so cap+overlay+validate succeeds.
        if payload and "entity_attributes" in payload:
            attrs = payload["entity_attributes"]
            if isinstance(attrs, dict):
                return {k: attrs.get(k) for k in response_model.model_fields}
        return _default_for_response_model(response_model)


def episode_body_with_extraction(prose: str, payload: dict) -> str:
    """Compose an episode body carrying a deterministic extraction payload."""
    return f"{prose}\n{MARKER} {json.dumps(payload)}"
