"""Repository for the owner-hosted OAuth authorization server state.

Every bearer secret (ticket handle, authorization code, access token, refresh
token) is stored only as a domain-separated hash. Rows carry no content.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..clock import utc_now
from ..database.models import (
    OAuthClientRow,
    OAuthCodeRow,
    OAuthTicketRow,
    OAuthTokenRow,
)


@dataclass(frozen=True)
class TicketRecord:
    ticket_hash: str
    client_id: str
    params_json: str
    grant_id: str | None
    failed_attempts: int
    consumed: bool
    expires_at: str


@dataclass(frozen=True)
class CodeRecord:
    code_hash: str
    client_id: str
    grant_id: str
    principal_id: str
    code_challenge: str
    redirect_uri: str
    redirect_uri_provided_explicitly: bool
    resource: str | None
    scopes_json: str
    used: bool
    expires_at: str


@dataclass(frozen=True)
class TokenRecord:
    token_hash: str
    kind: str
    client_id: str
    grant_id: str
    principal_id: str
    resource: str | None
    scopes_json: str
    family_id: str
    expires_at: str
    revoked_at: str | None


class OAuthRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # Clients -----------------------------------------------------------

    def client_count(self) -> int:
        return int(
            self._session.scalar(select(func.count()).select_from(OAuthClientRow)) or 0
        )

    def save_client(self, client_id: str, client_json: str) -> None:
        self._session.add(
            OAuthClientRow(
                client_id=client_id,
                client_json=client_json,
                created_at=utc_now(),
            )
        )
        self._session.flush()

    def get_client_json(self, client_id: str) -> str | None:
        row = self._session.get(OAuthClientRow, client_id)
        return row.client_json if row is not None else None

    # Authorization tickets ----------------------------------------------

    def create_ticket(
        self,
        ticket_hash: str,
        client_id: str,
        params_json: str,
        expires_at: str,
    ) -> None:
        self._session.add(
            OAuthTicketRow(
                ticket_hash=ticket_hash,
                client_id=client_id,
                params_json=params_json,
                grant_id=None,
                failed_attempts=0,
                consumed=False,
                created_at=utc_now(),
                expires_at=expires_at,
            )
        )
        self._session.flush()

    def get_ticket(self, ticket_hash: str) -> TicketRecord | None:
        row = self._session.get(OAuthTicketRow, ticket_hash)
        if row is None:
            return None
        return TicketRecord(
            ticket_hash=row.ticket_hash,
            client_id=row.client_id,
            params_json=row.params_json,
            grant_id=row.grant_id,
            failed_attempts=row.failed_attempts,
            consumed=bool(row.consumed),
            expires_at=row.expires_at,
        )

    def record_failed_attempt(self, ticket_hash: str) -> int:
        self._session.execute(
            update(OAuthTicketRow)
            .where(OAuthTicketRow.ticket_hash == ticket_hash)
            .values(failed_attempts=OAuthTicketRow.failed_attempts + 1)
        )
        self._session.flush()
        row = self._session.get(OAuthTicketRow, ticket_hash)
        assert row is not None
        self._session.refresh(row)
        return int(row.failed_attempts)

    def attach_grant(self, ticket_hash: str, grant_id: str) -> None:
        self._session.execute(
            update(OAuthTicketRow)
            .where(OAuthTicketRow.ticket_hash == ticket_hash)
            .values(grant_id=grant_id)
        )
        self._session.flush()

    def consume_ticket(self, ticket_hash: str) -> bool:
        """Atomically mark a ticket used; return False if it already was."""

        result = self._session.execute(
            update(OAuthTicketRow)
            .where(
                OAuthTicketRow.ticket_hash == ticket_hash,
                OAuthTicketRow.consumed.is_(False),
            )
            .values(consumed=True)
        )
        self._session.flush()
        return result.rowcount == 1

    # Authorization codes --------------------------------------------------

    def create_code(self, record: CodeRecord) -> None:
        self._session.add(
            OAuthCodeRow(
                code_hash=record.code_hash,
                client_id=record.client_id,
                grant_id=record.grant_id,
                principal_id=record.principal_id,
                code_challenge=record.code_challenge,
                redirect_uri=record.redirect_uri,
                redirect_uri_provided_explicitly=record.redirect_uri_provided_explicitly,
                resource=record.resource,
                scopes_json=record.scopes_json,
                used=False,
                created_at=utc_now(),
                expires_at=record.expires_at,
            )
        )
        self._session.flush()

    def get_code(self, code_hash: str) -> CodeRecord | None:
        row = self._session.get(OAuthCodeRow, code_hash)
        if row is None:
            return None
        return CodeRecord(
            code_hash=row.code_hash,
            client_id=row.client_id,
            grant_id=row.grant_id,
            principal_id=row.principal_id,
            code_challenge=row.code_challenge,
            redirect_uri=row.redirect_uri,
            redirect_uri_provided_explicitly=bool(row.redirect_uri_provided_explicitly),
            resource=row.resource,
            scopes_json=row.scopes_json,
            used=bool(row.used),
            expires_at=row.expires_at,
        )

    def mark_code_used(self, code_hash: str) -> bool:
        result = self._session.execute(
            update(OAuthCodeRow)
            .where(OAuthCodeRow.code_hash == code_hash, OAuthCodeRow.used.is_(False))
            .values(used=True)
        )
        self._session.flush()
        return result.rowcount == 1

    # Tokens ------------------------------------------------------------------

    def create_token(self, record: TokenRecord) -> None:
        self._session.add(
            OAuthTokenRow(
                token_hash=record.token_hash,
                kind=record.kind,
                client_id=record.client_id,
                grant_id=record.grant_id,
                principal_id=record.principal_id,
                resource=record.resource,
                scopes_json=record.scopes_json,
                family_id=record.family_id,
                created_at=utc_now(),
                expires_at=record.expires_at,
                revoked_at=None,
            )
        )
        self._session.flush()

    def get_token(self, token_hash: str, kind: str) -> TokenRecord | None:
        row = self._session.get(OAuthTokenRow, token_hash)
        if row is None or row.kind != kind:
            return None
        return TokenRecord(
            token_hash=row.token_hash,
            kind=row.kind,
            client_id=row.client_id,
            grant_id=row.grant_id,
            principal_id=row.principal_id,
            resource=row.resource,
            scopes_json=row.scopes_json,
            family_id=row.family_id,
            expires_at=row.expires_at,
            revoked_at=row.revoked_at,
        )

    def revoke_token(self, token_hash: str) -> None:
        self._session.execute(
            update(OAuthTokenRow)
            .where(
                OAuthTokenRow.token_hash == token_hash,
                OAuthTokenRow.revoked_at.is_(None),
            )
            .values(revoked_at=utc_now())
        )
        self._session.flush()

    def revoke_family(self, family_id: str) -> None:
        self._session.execute(
            update(OAuthTokenRow)
            .where(
                OAuthTokenRow.family_id == family_id,
                OAuthTokenRow.revoked_at.is_(None),
            )
            .values(revoked_at=utc_now())
        )
        self._session.flush()

    def purge_expired(self, now: str) -> None:
        self._session.execute(delete(OAuthTicketRow).where(OAuthTicketRow.expires_at <= now))
        self._session.execute(delete(OAuthCodeRow).where(OAuthCodeRow.expires_at <= now))
        self._session.execute(delete(OAuthTokenRow).where(OAuthTokenRow.expires_at <= now))
        self._session.flush()
