"""Shared API response envelope + error contract (spec §22)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: str = Field(description="Machine-readable error code")
    message: str = Field(description="Human-readable explanation")
    detail: dict = Field(default_factory=dict)


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "ok"
    service: str = "ecosystem-kb"
    version: str = "0.1.0"


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ready: bool
    checks: dict = Field(default_factory=dict)
