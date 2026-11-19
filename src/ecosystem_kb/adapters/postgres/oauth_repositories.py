"""OAuth 2.1 store + repositories (runbook Step 7).

All state lives in the KB control plane (PostgreSQL / SQLite). Opaque
values (session ids, codes, tokens) are random url-safe strings; only
their SHA-256 hex digests are persisted. The repositories below are the
single interface used by the AS service and the dashboard.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from ecosystem_kb.adapters.postgres.oauth_models import (
    KbSession,
    OauthAuthorizationCode,
    OauthClient,
    OauthConsent,
    OauthPendingAuthorization,
    OauthToken,
)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; normalize to UTC-aware for comparison."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def new_token() -> str:
    return "kb_" + secrets.token_urlsafe(32)


def new_code() -> str:
    return "kbc_" + secrets.token_urlsafe(32)


def new_session_token() -> str:
    return "kbs_" + secrets.token_urlsafe(32)


def new_request_id() -> str:
    return "kbr_" + secrets.token_urlsafe(24)


class SessionRepository:
    """Owner browser sessions (runbook Step 6)."""

    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory

    async def create(self, owner_subject: str, ttl_days: int, user_agent_hash: str | None = None) -> str:
        token = new_session_token()
        row = KbSession(
            token_hash=sha256_hex(token),
            owner_subject=owner_subject,
            expires_at=datetime.now(UTC) + timedelta(days=ttl_days),
            user_agent_hash=user_agent_hash,
        )
        async with self.session_factory() as s:
            s.add(row)
            await s.commit()
        return token

    async def resolve(self, token: str) -> KbSession | None:
        """Active (unrevoked, unexpired) session for a cookie value, or None."""
        async with self.session_factory() as s:
            row = (
                await s.execute(
                    select(KbSession).where(KbSession.token_hash == sha256_hex(token))
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            if row.revoked_at is not None or _aware(row.expires_at) < _now():
                return None
        return row

    async def touch(self, token: str) -> None:
        async with self.session_factory() as s:
            await s.execute(
                update(KbSession)
                .where(KbSession.token_hash == sha256_hex(token))
                .values(last_seen_at=datetime.now(UTC))
            )
            await s.commit()

    async def revoke(self, token: str) -> bool:
        async with self.session_factory() as s:
            result = await s.execute(
                update(KbSession)
                .where(
                    KbSession.token_hash == sha256_hex(token),
                    KbSession.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(UTC))
            )
            await s.commit()
            return result.rowcount > 0


class OauthClientRepository:
    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory

    async def upsert(
        self,
        *,
        client_id: str,
        client_name: str,
        registration_mode: str,
        redirect_uris: list[str],
        token_endpoint_auth_method: str = "none",
        grant_types: list[str] | None = None,
        response_types: list[str] | None = None,
        scopes: list[str] | None = None,
        metadata_url: str | None = None,
        metadata_hash: str | None = None,
    ) -> OauthClient:
        async with self.session_factory() as s:
            row = (
                await s.execute(select(OauthClient).where(OauthClient.client_id == client_id))
            ).scalar_one_or_none()
            if row is None:
                row = OauthClient(
                    client_id=client_id,
                    client_name=client_name,
                    registration_mode=registration_mode,
                    redirect_uris=redirect_uris,
                    token_endpoint_auth_method=token_endpoint_auth_method,
                    grant_types=grant_types or ["authorization_code", "refresh_token"],
                    response_types=response_types or ["code"],
                    scopes=scopes or ["kb:read", "kb:write", "offline_access"],
                )
                s.add(row)
            else:
                row.client_name = client_name
                row.redirect_uris = redirect_uris
                row.registration_mode = registration_mode
                row.token_endpoint_auth_method = token_endpoint_auth_method
                if metadata_hash:
                    row.metadata_hash = metadata_hash
                    row.metadata_validated_at = datetime.now(UTC)
                if metadata_url:
                    row.metadata_url = metadata_url
            await s.commit()
            await s.refresh(row)
            return row

    async def get(self, client_id: str) -> OauthClient | None:
        async with self.session_factory() as s:
            return (
                await s.execute(select(OauthClient).where(OauthClient.client_id == client_id))
            ).scalar_one_or_none()

    async def touch_used(self, client_id: str) -> None:
        async with self.session_factory() as s:
            await s.execute(
                update(OauthClient)
                .where(OauthClient.client_id == client_id)
                .values(last_used_at=datetime.now(UTC))
            )
            await s.commit()

    async def list_all(self) -> list[OauthClient]:
        async with self.session_factory() as s:
            return list((await s.execute(select(OauthClient))).scalars())

    async def disable(self, client_id: str) -> OauthToken | None:
        async with self.session_factory() as s:
            row = (
                await s.execute(select(OauthClient).where(OauthClient.client_id == client_id))
            ).scalar_one_or_none()
            if row is None:
                return None
            row.disabled_at = datetime.now(UTC)
            await s.commit()
            return row


class PendingAuthorizationRepository:
    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory

    async def create(
        self,
        *,
        request_hash: str,
        client_id: str,
        redirect_uri: str,
        state: str | None,
        code_challenge: str,
        code_challenge_method: str,
        resource: str,
        scopes: list[str],
        ttl_seconds: int = 900,
    ) -> OauthPendingAuthorization:
        row = OauthPendingAuthorization(
            request_hash=request_hash,
            client_id=client_id,
            redirect_uri=redirect_uri,
            state=state,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            resource=resource,
            scopes=scopes,
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
        )
        async with self.session_factory() as s:
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return row

    async def get(self, request_id: str) -> OauthPendingAuthorization | None:
        async with self.session_factory() as s:
            return (
                await s.execute(
                    select(OauthPendingAuthorization).where(
                        OauthPendingAuthorization.request_hash == sha256_hex(request_id)
                    )
                )
            ).scalar_one_or_none()

    async def bind_session(self, request_id: str, session_id: str) -> None:
        async with self.session_factory() as s:
            await s.execute(
                update(OauthPendingAuthorization)
                .where(OauthPendingAuthorization.request_hash == sha256_hex(request_id))
                .values(session_id=session_id)
            )
            await s.commit()

    async def set_status(self, request_id: str, status: str) -> None:
        async with self.session_factory() as s:
            await s.execute(
                update(OauthPendingAuthorization)
                .where(OauthPendingAuthorization.request_hash == sha256_hex(request_id))
                .values(status=status)
            )
            await s.commit()


class AuthorizationCodeRepository:
    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory

    async def create(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        code_challenge_method: str,
        owner_subject: str,
        session_id: str | None,
        resource: str,
        scopes: list[str],
        ttl_seconds: int,
    ) -> str:
        code = new_code()
        row = OauthAuthorizationCode(
            code_hash=sha256_hex(code),
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            owner_subject=owner_subject,
            session_id=session_id,
            resource=resource,
            scopes=scopes,
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
        )
        async with self.session_factory() as s:
            s.add(row)
            await s.commit()
        return code

    async def consume(self, code: str) -> OauthAuthorizationCode | None:
        """Atomically mark used and return the row; None when invalid,
        expired or already used (one-use enforcement)."""
        async with self.session_factory() as s:
            row = (
                await s.execute(
                    select(OauthAuthorizationCode).where(
                        OauthAuthorizationCode.code_hash == sha256_hex(code)
                    )
                )
            ).scalar_one_or_none()
            if row is None or row.used_at is not None or _aware(row.expires_at) < _now():
                return None
            row.used_at = datetime.now(UTC)
            await s.commit()
            return row


class TokenRepository:
    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory

    async def issue(
        self,
        *,
        token_type: str,
        client_id: str,
        owner_subject: str,
        resource: str,
        scopes: list[str],
        family_id: str,
        ttl_seconds: int,
        replaces_token_id: str | None = None,
    ) -> tuple[str, OauthToken]:
        token = new_token()
        row = OauthToken(
            token_hash=sha256_hex(token),
            token_type=token_type,
            client_id=client_id,
            owner_subject=owner_subject,
            resource=resource,
            scopes=scopes,
            family_id=family_id,
            replaces_token_id=replaces_token_id,
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
        )
        async with self.session_factory() as s:
            s.add(row)
            await s.commit()
            await s.refresh(row)
        return token, row

    async def find(self, token: str, token_type: str) -> OauthToken | None:
        async with self.session_factory() as s:
            row = (
                await s.execute(
                    select(OauthToken).where(OauthToken.token_hash == sha256_hex(token))
                )
            ).scalar_one_or_none()
            if row is None or row.token_type != token_type:
                return None
        return row

    async def is_active(self, row: OauthToken) -> bool:
        return (
            row.revoked_at is None
            and _aware(row.expires_at) > _now()
            and (row.consumed_at is None or row.token_type == "access")
        )

    async def mark_used(self, token_id: str) -> None:
        async with self.session_factory() as s:
            await s.execute(
                update(OauthToken)
                .where(OauthToken.id == token_id)
                .values(last_used_at=datetime.now(UTC))
            )
            await s.commit()

    async def consume_refresh(self, token: str) -> OauthToken | None:
        """Atomically consume a refresh token (rotation); None when invalid,
        expired, revoked or already consumed (replay)."""
        async with self.session_factory() as s:
            row = (
                await s.execute(
                    select(OauthToken).where(OauthToken.token_hash == sha256_hex(token))
                )
            ).scalar_one_or_none()
            if (
                row is None
                or row.token_type != "refresh"
                or row.revoked_at is not None
                or row.consumed_at is not None
                or _aware(row.expires_at) < _now()
            ):
                return None
            row.consumed_at = datetime.now(UTC)
            await s.commit()
            return row

    async def revoke_family(self, family_id: str) -> int:
        async with self.session_factory() as s:
            result = await s.execute(
                update(OauthToken)
                .where(OauthToken.family_id == family_id, OauthToken.revoked_at.is_(None))
                .values(revoked_at=datetime.now(UTC))
            )
            await s.commit()
            return result.rowcount or 0

    async def revoke_client(self, client_id: str) -> int:
        async with self.session_factory() as s:
            result = await s.execute(
                update(OauthToken)
                .where(OauthToken.client_id == client_id, OauthToken.revoked_at.is_(None))
                .values(revoked_at=datetime.now(UTC))
            )
            await s.commit()
            return result.rowcount or 0

    async def active_counts(self, client_id: str) -> dict[str, int]:
        async with self.session_factory() as s:
            rows = (
                (
                    await s.execute(
                        select(OauthToken).where(
                            OauthToken.client_id == client_id,
                            OauthToken.revoked_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
        out = {"access": 0, "refresh": 0}
        for r in rows:
            if _aware(r.expires_at) > _now():
                out[r.token_type] = out.get(r.token_type, 0) + 1
        return out


class ConsentRepository:
    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory

    async def upsert(self, owner_subject: str, client_id: str, resource: str, scopes: list[str]):
        async with self.session_factory() as s:
            row = (
                await s.execute(
                    select(OauthConsent).where(
                        OauthConsent.owner_subject == owner_subject,
                        OauthConsent.client_id == client_id,
                        OauthConsent.resource == resource,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                row = OauthConsent(
                    owner_subject=owner_subject,
                    client_id=client_id,
                    resource=resource,
                    scopes=scopes,
                )
                s.add(row)
            else:
                row.scopes = scopes
                row.updated_at = datetime.now(UTC)
                row.revoked_at = None
            await s.commit()
            await s.refresh(row)
            return row

    async def get(self, owner_subject: str, client_id: str, resource: str) -> OauthConsent | None:
        async with self.session_factory() as s:
            row = (
                await s.execute(
                    select(OauthConsent).where(
                        OauthConsent.owner_subject == owner_subject,
                        OauthConsent.client_id == client_id,
                        OauthConsent.resource == resource,
                    )
                )
            ).scalar_one_or_none()
            if row is None or row.revoked_at is not None:
                return None
        return row

    async def revoke(self, owner_subject: str, client_id: str) -> int:
        async with self.session_factory() as s:
            result = await s.execute(
                update(OauthConsent)
                .where(
                    OauthConsent.owner_subject == owner_subject,
                    OauthConsent.client_id == client_id,
                    OauthConsent.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(UTC))
            )
            await s.commit()
            return result.rowcount or 0

    async def list_for_owner(self, owner_subject: str) -> list[OauthConsent]:
        async with self.session_factory() as s:
            return list(
                (
                    await s.execute(
                        select(OauthConsent).where(
                            OauthConsent.owner_subject == owner_subject,
                            OauthConsent.revoked_at.is_(None),
                        )
                    )
                )
                .scalars()
            )
