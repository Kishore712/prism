"""Owner-hosted OAuth 2.1 authorization server for recipient identity.

The recipient's ChatGPT or Claude connector performs the standard MCP
authorization flow (protected-resource metadata, dynamic client registration,
authorization code + PKCE, resource indicator). The only Prism-specific step is
the consent page, where the recipient redeems a one-time invitation in a
browser. That step:

* keeps the invitation out of the chat transcript and out of model-visible
  tool arguments;
* creates a grant bound to a freshly minted recipient principal; and
* by default leaves the grant ``pending`` until the owner confirms who the
  recipient is (``prism grant approve``).

Access and refresh tokens are opaque, stored only as hashes, and merely
identify a grant. They never authorize by themselves: every tool call
re-verifies the full grant -> version -> share -> snapshot chain.
"""

from __future__ import annotations

import html
import json
import secrets
from urllib.parse import parse_qs, urlsplit

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from sqlalchemy.exc import SQLAlchemyError
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from ...clock import to_epoch, utc_after, utc_now
from ...database import PrismDatabase, PrismUnitOfWork
from ...exceptions import AuthorizationError, PrismError
from ...models.sharing import GrantApproval, GrantStatus
from ...repositories.oauth import CodeRecord, TokenRecord
from ...security import capability_hash
from ...services.recipient_access import RecipientAccessService


SCOPE = "prism:read"
CONSENT_PATH = "/prism/consent"
MAX_CONSENT_ATTEMPTS = 5
MAX_FORM_BYTES = 8_192
DEFAULT_MAX_CLIENTS = 500


class PrismAuthorizationCode(AuthorizationCode):
    grant_id: str
    principal_id: str


class PrismRefreshToken(RefreshToken):
    grant_id: str
    family_id: str


class PrismAccessToken(AccessToken):
    grant_id: str


def _secret() -> str:
    return secrets.token_urlsafe(32)


class PrismOAuthProvider:
    """SQLite-backed ``OAuthAuthorizationServerProvider`` for Prism grants."""

    def __init__(
        self,
        database: PrismDatabase,
        access: RecipientAccessService,
        *,
        public_url: str,
        resource_url: str,
        access_token_seconds: int = 3_600,
        refresh_token_seconds: int = 14 * 24 * 3_600,
        code_seconds: int = 300,
        ticket_seconds: int = 900,
        max_clients: int = DEFAULT_MAX_CLIENTS,
    ) -> None:
        self._database = database
        self._access = access
        self._public_url = public_url.rstrip("/")
        self._resource_url = resource_url.rstrip("/")
        self._access_seconds = access_token_seconds
        self._refresh_seconds = refresh_token_seconds
        self._code_seconds = code_seconds
        self._ticket_seconds = ticket_seconds
        self._max_clients = max_clients

    # Client registration -------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with PrismUnitOfWork(self._database) as unit:
            raw = unit.oauth.get_client_json(client_id)
        if raw is None:
            return None
        return OAuthClientInformationFull.model_validate_json(raw)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if client_info.client_id is None:
            raise RegistrationError("invalid_client_metadata", "client_id is required")
        for uri in client_info.redirect_uris or ():
            parsed = urlsplit(str(uri))
            if parsed.scheme not in {"https", "http"} or parsed.fragment:
                raise RegistrationError(
                    "invalid_redirect_uri",
                    "Redirect URIs must be HTTPS (or loopback HTTP) without fragments",
                )
            if parsed.scheme == "http" and parsed.hostname not in {
                "localhost",
                "127.0.0.1",
                "::1",
            }:
                raise RegistrationError(
                    "invalid_redirect_uri",
                    "Non-loopback redirect URIs must use HTTPS",
                )
        with PrismUnitOfWork(self._database) as unit:
            if unit.oauth.client_count() >= self._max_clients:
                raise RegistrationError(
                    "invalid_client_metadata",
                    "This Prism host cannot register more clients",
                )
            unit.oauth.save_client(
                client_info.client_id,
                client_info.model_dump_json(),
            )
            unit.commit()

    # Authorization ---------------------------------------------------------

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        if params.resource is not None and params.resource.rstrip("/") != self._resource_url:
            raise AuthorizeError("invalid_target", "Unknown resource indicator")
        if params.scopes and not set(params.scopes).issubset({SCOPE}):
            raise AuthorizeError("invalid_scope", "Only the prism:read scope exists")
        ticket = _secret()
        assert client.client_id is not None
        with PrismUnitOfWork(self._database) as unit:
            unit.oauth.purge_expired(utc_now())
            unit.oauth.create_ticket(
                capability_hash("OAuthTicket", ticket),
                client.client_id,
                params.model_dump_json(),
                utc_after(self._ticket_seconds),
            )
            unit.commit()
        return f"{self._public_url}{CONSENT_PATH}?ticket={ticket}"

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> PrismAuthorizationCode | None:
        with PrismUnitOfWork(self._database) as unit:
            record = unit.oauth.get_code(capability_hash("OAuthCode", authorization_code))
        if (
            record is None
            or record.used
            or record.client_id != client.client_id
            or record.expires_at <= utc_now()
        ):
            return None
        return PrismAuthorizationCode(
            code=authorization_code,
            scopes=json.loads(record.scopes_json),
            expires_at=float(to_epoch(record.expires_at)),
            client_id=record.client_id,
            code_challenge=record.code_challenge,
            redirect_uri=record.redirect_uri,
            redirect_uri_provided_explicitly=record.redirect_uri_provided_explicitly,
            resource=record.resource,
            subject=record.principal_id,
            grant_id=record.grant_id,
            principal_id=record.principal_id,
        )

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: PrismAuthorizationCode,
    ) -> OAuthToken:
        assert client.client_id is not None
        try:
            with PrismUnitOfWork(self._database) as unit:
                if not unit.oauth.mark_code_used(
                    capability_hash("OAuthCode", authorization_code.code)
                ):
                    raise TokenError("invalid_grant", "Authorization code already used")
                unit.commit()
        except SQLAlchemyError as exc:
            raise TokenError("invalid_grant", "Authorization could not be completed") from exc
        self._require_valid_grant(authorization_code.grant_id)
        return self._issue_pair(
            client_id=client.client_id,
            grant_id=authorization_code.grant_id,
            principal_id=authorization_code.principal_id,
            resource=authorization_code.resource or self._resource_url,
            scopes=authorization_code.scopes,
            family_id=f"fam_{secrets.token_hex(13)}",
        )

    # Refresh ------------------------------------------------------------------

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> PrismRefreshToken | None:
        with PrismUnitOfWork(self._database) as unit:
            record = unit.oauth.get_token(
                capability_hash("OAuthRefresh", refresh_token),
                "refresh",
            )
        if (
            record is None
            or record.client_id != client.client_id
            or record.expires_at <= utc_now()
        ):
            return None
        return PrismRefreshToken(
            token=refresh_token,
            client_id=record.client_id,
            scopes=json.loads(record.scopes_json),
            expires_at=to_epoch(record.expires_at),
            resource=record.resource,
            subject=record.principal_id,
            grant_id=record.grant_id,
            family_id=record.family_id,
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: PrismRefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        assert client.client_id is not None
        if scopes and not set(scopes).issubset(set(refresh_token.scopes)):
            raise TokenError("invalid_scope", "Scope cannot be broadened on refresh")
        token_hash = capability_hash("OAuthRefresh", refresh_token.token)
        with PrismUnitOfWork(self._database) as unit:
            record = unit.oauth.get_token(token_hash, "refresh")
            if record is None:
                raise TokenError("invalid_grant", "Unknown refresh token")
            if record.revoked_at is not None:
                # A rotated token was presented again: treat the family as stolen.
                unit.oauth.revoke_family(record.family_id)
                unit.commit()
                raise TokenError("invalid_grant", "Refresh token was already used")
            unit.oauth.revoke_token(token_hash)
            unit.commit()
        self._require_valid_grant(refresh_token.grant_id)
        assert refresh_token.subject is not None
        return self._issue_pair(
            client_id=client.client_id,
            grant_id=refresh_token.grant_id,
            principal_id=refresh_token.subject,
            resource=refresh_token.resource or self._resource_url,
            scopes=list(refresh_token.scopes),
            family_id=refresh_token.family_id,
        )

    # Bearer verification ------------------------------------------------------

    async def load_access_token(self, token: str) -> PrismAccessToken | None:
        with PrismUnitOfWork(self._database) as unit:
            record = unit.oauth.get_token(capability_hash("OAuthAccess", token), "access")
        if (
            record is None
            or record.revoked_at is not None
            or record.expires_at <= utc_now()
        ):
            return None
        return PrismAccessToken(
            token=token,
            client_id=record.client_id,
            scopes=json.loads(record.scopes_json),
            expires_at=to_epoch(record.expires_at),
            resource=record.resource,
            subject=record.principal_id,
            claims={"iss": self._public_url, "grant_id": record.grant_id},
            grant_id=record.grant_id,
        )

    async def revoke_token(self, token: PrismAccessToken | PrismRefreshToken) -> None:
        kind = "refresh" if isinstance(token, PrismRefreshToken) else "access"
        namespace = "OAuthAccess" if kind == "access" else "OAuthRefresh"
        with PrismUnitOfWork(self._database) as unit:
            record = unit.oauth.get_token(capability_hash(namespace, token.token), kind)
            if record is not None:
                unit.oauth.revoke_family(record.family_id)
                unit.commit()

    # Consent step (called by the consent routes) ------------------------------

    def load_ticket(self, raw_ticket: str):
        with PrismUnitOfWork(self._database) as unit:
            return unit.oauth.get_ticket(capability_hash("OAuthTicket", raw_ticket))

    def complete_ticket(self, raw_ticket: str, grant_id: str, principal_id: str) -> str:
        """Consume a ticket and return the client redirect carrying a fresh code."""

        ticket_hash = capability_hash("OAuthTicket", raw_ticket)
        with PrismUnitOfWork(self._database) as unit:
            ticket = unit.oauth.get_ticket(ticket_hash)
            if ticket is None or ticket.expires_at <= utc_now():
                raise AuthorizationError("This authorization request has expired")
            if not unit.oauth.consume_ticket(ticket_hash):
                raise AuthorizationError("This authorization request was already used")
            params = AuthorizationParams.model_validate_json(ticket.params_json)
            code = _secret()
            unit.oauth.create_code(
                CodeRecord(
                    code_hash=capability_hash("OAuthCode", code),
                    client_id=ticket.client_id,
                    grant_id=grant_id,
                    principal_id=principal_id,
                    code_challenge=params.code_challenge,
                    redirect_uri=str(params.redirect_uri),
                    redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
                    resource=params.resource or self._resource_url,
                    scopes_json=json.dumps([SCOPE]),
                    used=False,
                    expires_at=utc_after(self._code_seconds),
                )
            )
            unit.commit()
        return construct_redirect_uri(
            str(params.redirect_uri),
            code=code,
            state=params.state,
            iss=self._public_url,
        )

    def deny_ticket(self, raw_ticket: str) -> str | None:
        """Consume a ticket and return an ``access_denied`` client redirect."""

        ticket_hash = capability_hash("OAuthTicket", raw_ticket)
        with PrismUnitOfWork(self._database) as unit:
            ticket = unit.oauth.get_ticket(ticket_hash)
            if ticket is None:
                return None
            unit.oauth.consume_ticket(ticket_hash)
            unit.commit()
        params = AuthorizationParams.model_validate_json(ticket.params_json)
        return construct_redirect_uri(
            str(params.redirect_uri),
            error="access_denied",
            error_description="The recipient could not be authorized",
            state=params.state,
            iss=self._public_url,
        )

    def record_failed_attempt(self, raw_ticket: str) -> int:
        ticket_hash = capability_hash("OAuthTicket", raw_ticket)
        with PrismUnitOfWork(self._database) as unit:
            attempts = unit.oauth.record_failed_attempt(ticket_hash)
            if attempts >= MAX_CONSENT_ATTEMPTS:
                unit.oauth.consume_ticket(ticket_hash)
            unit.commit()
        return attempts

    def principal_for_grant(self, grant_id: str) -> str:
        with PrismUnitOfWork(self._database) as unit:
            return unit.grants.get(grant_id).principal_id or ""

    def attach_grant(self, raw_ticket: str, grant_id: str) -> None:
        with PrismUnitOfWork(self._database) as unit:
            unit.oauth.attach_grant(capability_hash("OAuthTicket", raw_ticket), grant_id)
            unit.commit()

    # Internals ------------------------------------------------------------------

    def _require_valid_grant(self, grant_id: str) -> None:
        try:
            self._access.verify_grant(grant_id)
        except PrismError as exc:
            raise TokenError("invalid_grant", "This Prism share is no longer available") from exc

    def _issue_pair(
        self,
        *,
        client_id: str,
        grant_id: str,
        principal_id: str,
        resource: str,
        scopes: list[str],
        family_id: str,
    ) -> OAuthToken:
        access = _secret()
        refresh = _secret()
        scopes_json = json.dumps(scopes)
        with PrismUnitOfWork(self._database) as unit:
            for kind, namespace, value, seconds in (
                ("access", "OAuthAccess", access, self._access_seconds),
                ("refresh", "OAuthRefresh", refresh, self._refresh_seconds),
            ):
                unit.oauth.create_token(
                    TokenRecord(
                        token_hash=capability_hash(namespace, value),
                        kind=kind,
                        client_id=client_id,
                        grant_id=grant_id,
                        principal_id=principal_id,
                        resource=resource,
                        scopes_json=scopes_json,
                        family_id=family_id,
                        expires_at=utc_after(seconds),
                        revoked_at=None,
                    )
                )
            unit.commit()
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=self._access_seconds,
            scope=" ".join(scopes),
            refresh_token=refresh,
        )


# --------------------------------------------------------------------------
# Consent page
# --------------------------------------------------------------------------

_PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; "
        "base-uri 'none'"
    ),
}

_STYLE = (
    "body{font:16px/1.5 system-ui,sans-serif;max-width:34rem;margin:3rem auto;"
    "padding:0 1rem;color:#1a1a1a}label{display:block;margin-top:1rem;"
    "font-weight:600}input{width:100%;padding:.5rem;margin-top:.25rem;"
    "box-sizing:border-box;font:inherit}button{margin-top:1.25rem;padding:.6rem 1rem;"
    "font:inherit}.warn{background:#fff4e5;border:1px solid #f0c36d;padding:.75rem;"
    "border-radius:6px}.err{color:#b00020}.mono{font-family:ui-monospace,monospace}"
)


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    document = (
        "<!doctype html><html lang=en><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{_STYLE}</style>"
        f"<h1>{html.escape(title)}</h1>{body}</html>"
    )
    return HTMLResponse(document, status_code=status, headers=_PAGE_HEADERS)


def _error_page(message: str, status: int = 400) -> HTMLResponse:
    return _page("Prism", f"<p class=err>{html.escape(message)}</p>", status)


class ConsentController:
    """HTTP handlers for the invitation-redemption step of authorization."""

    def __init__(
        self,
        provider: PrismOAuthProvider,
        access: RecipientAccessService,
        *,
        owner_label: str,
    ) -> None:
        self._provider = provider
        self._access = access
        self._owner_label = owner_label

    async def get(self, request: Request) -> Response:
        ticket = request.query_params.get("ticket", "")
        loaded = await self._usable_ticket(ticket)
        if isinstance(loaded, Response):
            return loaded
        client = await self._provider.get_client(loaded.client_id)
        return self._form(ticket, client)

    async def post(self, request: Request) -> Response:
        body = await request.body()
        if len(body) > MAX_FORM_BYTES:
            return _error_page("The request was too large.", 413)
        form = {
            key: values[0]
            for key, values in parse_qs(body.decode("utf-8", "replace")).items()
            if values
        }
        ticket = form.get("ticket", "")
        loaded = await self._usable_ticket(ticket)
        if isinstance(loaded, Response):
            return loaded
        client = await self._provider.get_client(loaded.client_id)

        grant_id = loaded.grant_id
        if grant_id is None:
            try:
                redeemed = self._access.redeem_invitation(
                    form.get("invitation_token", "").strip(),
                    form.get("display_name", ""),
                )
            except AuthorizationError:
                attempts = self._provider.record_failed_attempt(ticket)
                if attempts >= MAX_CONSENT_ATTEMPTS:
                    return _error_page(
                        "Too many failed attempts. Start the connection again.", 429
                    )
                return self._form(
                    ticket,
                    client,
                    error="The invitation is invalid, expired, or already used.",
                    status=400,
                )
            except PrismError as exc:
                return self._form(ticket, client, error=str(exc), status=400)
            self._provider.attach_grant(ticket, redeemed.grant_id)
            grant_id = redeemed.grant_id
            principal_id = redeemed.principal_id
        else:
            principal_id = ""
        return self._finish(ticket, grant_id, principal_id, client)

    # ------------------------------------------------------------------

    def _finish(self, ticket: str, grant_id: str, principal_id: str, client) -> Response:
        try:
            approval, status = self._access.grant_approval(grant_id)
        except PrismError:
            return _error_page("This Prism share is unavailable.", 403)
        if status is not GrantStatus.ACTIVE or approval is GrantApproval.DENIED:
            target = self._provider.deny_ticket(ticket)
            if target is None:
                return _error_page("This authorization request has expired.")
            return RedirectResponse(target, status_code=302, headers=_PAGE_HEADERS)
        if approval is GrantApproval.PENDING:
            return _page(
                "Waiting for the owner",
                (
                    f"<p>{html.escape(self._owner_label)} needs to confirm that "
                    "this connection is yours. Ask them to approve it, then continue.</p>"
                    "<form method=post action=" + CONSENT_PATH + ">"
                    f"<input type=hidden name=ticket value='{html.escape(ticket, quote=True)}'>"
                    "<button type=submit>Check again</button></form>"
                ),
                202,
            )
        if not principal_id:
            # Resumed after owner approval: recover the principal from the grant.
            principal_id = self._provider.principal_for_grant(grant_id)
        try:
            target = self._provider.complete_ticket(ticket, grant_id, principal_id)
        except AuthorizationError as exc:
            return _error_page(str(exc))
        return RedirectResponse(target, status_code=302, headers=_PAGE_HEADERS)

    async def _usable_ticket(self, ticket: str):
        if not ticket or len(ticket) > 200:
            return _error_page("This authorization request is invalid or has expired.")
        record = self._provider.load_ticket(ticket)
        if (
            record is None
            or record.consumed
            or record.expires_at <= utc_now()
            or record.failed_attempts >= MAX_CONSENT_ATTEMPTS
        ):
            return _error_page("This authorization request is invalid or has expired.")
        return record

    def _form(
        self,
        ticket: str,
        client: OAuthClientInformationFull | None,
        *,
        error: str | None = None,
        status: int = 200,
    ) -> HTMLResponse:
        name = html.escape(client.client_name or "an application") if client else "an application"
        hosts = sorted(
            {urlsplit(str(uri)).netloc for uri in (client.redirect_uris or [])}
        ) if client else []
        where = html.escape(", ".join(hosts) or "unknown")
        error_html = f"<p class=err>{html.escape(error)}</p>" if error else ""
        return _page(
            "Open a Prism share",
            (
                f"<p><b>{name}</b> wants to read one conversation snapshot that "
                f"{html.escape(self._owner_label)} chose to share with you.</p>"
                f"<p class=warn>Sign-in result will be sent to: <span class=mono>{where}</span>. "
                "Continue only if you started this connection.</p>"
                f"{error_html}"
                "<form method=post action=" + CONSENT_PATH + " autocomplete=off>"
                f"<input type=hidden name=ticket value='{html.escape(ticket, quote=True)}'>"
                "<label>Your name<input name=display_name maxlength=80 required></label>"
                "<label>Invitation code"
                "<input name=invitation_token maxlength=200 required "
                "autocapitalize=off spellcheck=false class=mono></label>"
                "<button type=submit>Continue</button></form>"
            ),
            status,
        )
