"""Request dependency: authenticate every /v1 route via bearer token."""

from __future__ import annotations

from contracts.clients import AuthenticatedClient
from fastapi import Request

from ecosystem_kb.core.exceptions import AuthenticationError


async def authenticated_client(request: Request) -> AuthenticatedClient:
    """Resolve the client from the Authorization header on every request.

    Namespace and scopes always come from this server-side record —
    caller-supplied values can only narrow within it (SYS-S-003).
    """
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise AuthenticationError("missing bearer token")
    token = header[7:].strip()
    client = await request.app.state.auth.authenticate_token(token)
    request.state.client = client
    return client
