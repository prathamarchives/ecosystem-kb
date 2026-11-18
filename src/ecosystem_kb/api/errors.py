"""Uniform error mapping: KbError subclasses → JSON error bodies."""

from __future__ import annotations

from ecosystem_kb.core.exceptions import KbError


def error_response(exc: Exception):
    from fastapi.responses import JSONResponse

    if isinstance(exc, KbError):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": exc.error_code,
                "message": str(exc),
                "detail": {},
            },
        )
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "message": str(exc), "detail": {}},
    )
