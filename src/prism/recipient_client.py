"""Recipient-side command-line client for a Prism share.

This is a real MCP client that performs the *same* OAuth 2.1 + PKCE flow a
ChatGPT or Claude connector performs, so a recipient on another machine can use
a share without any chat product. It exists to prove the end-to-end path and to
help debug a share; it is not a security boundary of its own.

State (registered client + tokens) is kept per server URL in a ``0600`` file.
"""

from __future__ import annotations

import asyncio
import getpass
import hashlib
import json
import os
import queue
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.auth.oauth2 import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)

from .exceptions import PrismError, ValidationError


DEFAULT_STATE_DIR = Path("~/.prism-recipient")
CONNECT_TIMEOUT_SECONDS = 600.0
POLL_SECONDS = 3.0


class RecipientClientError(PrismError):
    code = "RECIPIENT_CLIENT_ERROR"
    exit_code = 20


# --------------------------------------------------------------------------
# Token storage
# --------------------------------------------------------------------------


class FileTokenStorage:
    """Persist OAuth client info and tokens for one server in a private file."""

    def __init__(self, state_dir: Path, server_url: str) -> None:
        directory = state_dir.expanduser()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
        name = hashlib.sha256(server_url.encode("utf-8")).hexdigest()[:20]
        self.path = directory / f"{name}.json"

    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _write(self, data: dict[str, Any]) -> None:
        temporary = self.path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.replace(temporary, self.path)
        os.chmod(self.path, 0o600)

    async def get_tokens(self) -> OAuthToken | None:
        raw = self._read().get("tokens")
        return OAuthToken.model_validate(raw) if raw else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        data = self._read()
        data["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        self._write(data)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        raw = self._read().get("client_info")
        return OAuthClientInformationFull.model_validate(raw) if raw else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        data = self._read()
        data["client_info"] = client_info.model_dump(mode="json", exclude_none=True)
        self._write(data)

    def forget(self) -> None:
        self.path.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Browser-less consent (headless mode)
# --------------------------------------------------------------------------


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _request(url: str, data: bytes | None = None) -> tuple[int, dict[str, str], str]:
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"} if data else {},
        method="POST" if data is not None else "GET",
    )
    opener = urllib.request.build_opener(_NoRedirects)
    try:
        with opener.open(request, timeout=30) as response:
            return response.status, dict(response.headers), response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read().decode("utf-8", "replace")
    except urllib.error.URLError as error:
        raise RecipientClientError(f"Could not reach the Prism host: {error.reason}") from error


def _code_from_location(location: str) -> AuthorizationCodeResult:
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)
    if "error" in query:
        reason = query.get("error_description", query["error"])[0]
        raise RecipientClientError(f"The owner's Prism host refused access: {reason}")
    if "code" not in query:
        raise RecipientClientError("The authorization response carried no code")
    return AuthorizationCodeResult(
        code=query["code"][0],
        state=query.get("state", [None])[0],
        iss=query.get("iss", [None])[0],
    )


def headless_authorize(
    authorization_url: str,
    invitation: str,
    display_name: str,
    *,
    timeout: float = CONNECT_TIMEOUT_SECONDS,
    poll_seconds: float = POLL_SECONDS,
    log=print,
) -> AuthorizationCodeResult:
    """Do the browser consent step from the terminal: redeem, then wait for approval."""

    status, headers, _ = _request(authorization_url)
    location = headers.get("Location") or headers.get("location")
    if status not in {301, 302, 303, 307} or not location:
        raise RecipientClientError("The Prism host did not start an authorization request")
    if "error=" in location and "/prism/consent" not in location:
        return _code_from_location(location)
    parsed = urllib.parse.urlsplit(location)
    ticket = urllib.parse.parse_qs(parsed.query).get("ticket", [""])[0]
    endpoint = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    if not ticket:
        raise RecipientClientError("The Prism host returned no consent ticket")

    body = urllib.parse.urlencode(
        {"ticket": ticket, "invitation_token": invitation, "display_name": display_name}
    ).encode()
    deadline = time.monotonic() + timeout
    announced = False
    while True:
        status, headers, page = _request(endpoint, body)
        location = headers.get("Location") or headers.get("location")
        if status == 302 and location:
            return _code_from_location(location)
        if status == 202:
            if not announced:
                log("Waiting for the owner to approve this connection…")
                announced = True
            if time.monotonic() > deadline:
                raise RecipientClientError("Timed out waiting for the owner to approve")
            time.sleep(poll_seconds)
            body = urllib.parse.urlencode({"ticket": ticket}).encode()
            continue
        if status == 429:
            raise RecipientClientError("Too many failed attempts; start again")
        raise RecipientClientError(
            "The invitation was not accepted (invalid, expired, or already used)"
            if status == 400
            else f"Unexpected response from the Prism host (HTTP {status})"
        )


# --------------------------------------------------------------------------
# Browser mode
# --------------------------------------------------------------------------


class _CallbackServer:
    def __init__(self) -> None:
        self.results: queue.Queue[dict[str, list[str]]] = queue.Queue()
        results = self.results

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                if urllib.parse.urlsplit(self.path).path == "/callback":
                    results.put(query)
                    body = b"Prism: you can close this tab and return to the terminal."
                    self.send_response(200)
                else:
                    body = b"Not found"
                    self.send_response(404)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.port}/callback"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


# --------------------------------------------------------------------------
# Provider construction and calls
# --------------------------------------------------------------------------


def _client_metadata(redirect_uri: str) -> OAuthClientMetadata:
    return OAuthClientMetadata(
        client_name="Prism recipient CLI",
        redirect_uris=[redirect_uri],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
        scope="prism:read",
    )


def _validated_url(server_url: str) -> str:
    parsed = urllib.parse.urlsplit(server_url)
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme not in {"https", "http"} or (parsed.scheme == "http" and not loopback):
        raise ValidationError("The Prism server URL must be HTTPS (or loopback HTTP)")
    if not parsed.path.rstrip("/").endswith("/mcp"):
        raise ValidationError("The Prism server URL must end in /mcp")
    return server_url.rstrip("/")


async def connect(
    server_url: str,
    state_dir: Path = DEFAULT_STATE_DIR,
    *,
    headless: bool = False,
    display_name: str | None = None,
    invitation: str | None = None,
    open_browser: bool = True,
    log=print,
) -> dict[str, Any]:
    """Authorize this machine against a share and return its manifest."""

    server_url = _validated_url(server_url)
    storage = FileTokenStorage(state_dir, server_url)
    storage.forget()  # a fresh connect always starts a fresh authorization
    callback: _CallbackServer | None = None
    loop = asyncio.get_running_loop()

    if headless:
        redirect_uri = f"http://127.0.0.1:{secrets.randbelow(20_000) + 20_000}/callback"
        code_box: list[AuthorizationCodeResult] = []
        secret = invitation or os.environ.get("PRISM_INVITATION") or getpass.getpass(
            "Invitation code (input hidden): "
        )
        name = display_name or input("Your name: ").strip()

        async def redirect_handler(url: str) -> None:
            result = await loop.run_in_executor(
                None, lambda: headless_authorize(url, secret.strip(), name, log=log)
            )
            code_box.append(result)

        async def callback_handler() -> AuthorizationCodeResult:
            return code_box.pop()

    else:
        callback = _CallbackServer()
        redirect_uri = callback.redirect_uri

        async def redirect_handler(url: str) -> None:
            log("Open this URL to redeem your invitation and connect:")
            log(f"  {url}")
            if open_browser:
                webbrowser.open(url)

        async def callback_handler() -> AuthorizationCodeResult:
            query = await loop.run_in_executor(
                None, lambda: callback.results.get(True, CONNECT_TIMEOUT_SECONDS)  # type: ignore[union-attr]
            )
            if "error" in query:
                raise RecipientClientError(
                    "The owner's Prism host refused access: "
                    + query.get("error_description", query["error"])[0]
                )
            return AuthorizationCodeResult(
                code=query["code"][0],
                state=query.get("state", [None])[0],
                iss=query.get("iss", [None])[0],
            )

    provider = OAuthClientProvider(
        server_url,
        _client_metadata(redirect_uri),
        storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )
    try:
        return await _call(server_url, provider, "prism_get_manifest", {})
    finally:
        if callback is not None:
            callback.close()


async def call_tool(
    server_url: str,
    state_dir: Path,
    tool: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Call one tool with previously stored credentials (refreshing if needed)."""

    server_url = _validated_url(server_url)
    storage = FileTokenStorage(state_dir, server_url)

    async def redirect_handler(_url: str) -> None:
        raise RecipientClientError(
            "Not connected to this share. Run `prism recipient connect` first."
        )

    async def callback_handler() -> AuthorizationCodeResult:  # pragma: no cover
        raise RecipientClientError("Not connected to this share.")

    provider = OAuthClientProvider(
        server_url,
        _client_metadata("http://127.0.0.1:1/callback"),
        storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )
    return await _call(server_url, provider, tool, arguments)


async def _call(
    server_url: str,
    provider: OAuthClientProvider,
    tool: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    async with httpx2.AsyncClient(auth=provider, timeout=60.0) as client:
        async with streamable_http_client(server_url, http_client=client) as streams:
            read_stream, write_stream = streams
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool, arguments)
    if result.is_error:
        text = result.content[0].text if result.content else "tool error"
        try:
            error = json.loads(text)["error"]
            raise RecipientClientError(f"[{error['code']}] {error['message']}")
        except (json.JSONDecodeError, KeyError, TypeError):
            raise RecipientClientError(text) from None
    return dict(result.structured_content or {})
