"""Ingestion policies (LAW-010, LAW-011): secret detection and content gates.

Deterministic code. No LLM judgment flows into these decisions (LAW-017).
Imported content is data, never instructions.
"""

from __future__ import annotations

import re

from contracts.events import KnowledgeEvent

from ecosystem_kb.core.exceptions import SecretContentError

# Credential-shaped patterns. Deliberately broad on assignment context and
# key-material structure; the secret scan runs on event bodies AND payloads.
_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key_id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("assignment_of_secret", re.compile(
        r"(?im)^\s*(api[_-]?key|apikey|secret|password|passwd|token|access[_-]?key|"
        r"client[_-]?secret|private[_-]?key)\s*[:=]\s*\S.{7,}\s*$"
    )),
    ("bearer_header", re.compile(r"(?i)authorization\s*:\s*bearer\s+[A-Za-z0-9._/-]{15,}")),
]

_PROMPT_INJECTION_MARKERS = re.compile(
    r"(?i)(ignore (all |previous |prior )?(instructions|rules)|disregard (the )?(system|above)|"
    r"you are now|new instructions:|SYSTEM PROMPT:|reveal (your )?system prompt)"
)


def scan_for_secrets(text: str) -> list[str]:
    """Return the labels of credential-shaped patterns found in text."""
    found: list[str] = []
    for label, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            found.append(label)
    return found


def scan_for_injection(text: str) -> bool:
    """Heuristic marker that content is attempting instruction override (LAW-011).
    Detection is for flagging/quarantine review — content is never executed."""
    return bool(_PROMPT_INJECTION_MARKERS.search(text))


class IngestionPolicy:
    """The deterministic gate an event passes after schema validation."""

    def __init__(self, max_body_chars: int = 100_000) -> None:
        self.max_body_chars = max_body_chars

    def check(self, event: KnowledgeEvent) -> list[str]:
        """Raise SecretContentError on violations; return advisory flags."""
        texts: list[str] = []
        if event.content.body:
            texts.append(event.content.body)
        if event.payload:
            import json

            texts.append(json.dumps(event.payload, default=str))

        for text in texts:
            hits = scan_for_secrets(text)
            if hits:
                raise SecretContentError(
                    f"event content matches secret patterns: {', '.join(sorted(set(hits)))}"
                )
            if scan_for_injection(text):
                return ["prompt_injection_suspected"]

        if event.content.body and len(event.content.body) > self.max_body_chars:
            raise SecretContentError("event body exceeds maximum size")
        return []
