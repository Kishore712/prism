"""One-shot operator bootstrap for discovering a verified OIDC issuer and subject."""

import json
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import uvicorn
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict

from prism.identity import (
    ALLOWED_ALGORITHMS,
    FixedOIDCTransport,
    _https_url,
    read_client_secret,
    validate_id_token,
)
from prism.identity_preflight import validate_tls_identity
from prism.sharing import Denied, digest


@dataclass(frozen=True)
class IdentifyOIDCConfig:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    client_id: str
    redirect_uri: str
    public_origin: str
    algorithms: tuple[str, ...] = ("RS256",)

    def __post_init__(self):
        for name in (
            "issuer",
            "authorization_endpoint",
            "token_endpoint",
            "jwks_uri",
            "redirect_uri",
            "public_origin",
        ):
            _https_url(
                getattr(self, name),
                origin=name == "public_origin",
                endpoint=name
                in ("authorization_endpoint", "token_endpoint", "jwks_uri"),
            )
        if self.public_origin.endswith("/") or self.redirect_uri != (
            self.public_origin + "/auth/oidc/identify/callback"
        ):
            raise ValueError(
                "The identify callback must be the canonical HTTPS origin plus /auth/oidc/identify/callback."
            )
        if not isinstance(self.client_id, str) or not 1 <= len(self.client_id) <= 256:
            raise ValueError("OIDC client_id is required.")
        if (
            not isinstance(self.algorithms, tuple)
            or not self.algorithms
            or len(set(self.algorithms)) != len(self.algorithms)
            or not set(self.algorithms).issubset(ALLOWED_ALGORITHMS)
        ):
            raise ValueError("Choose a non-empty fixed asymmetric OIDC algorithm set.")

    @classmethod
    def from_file(cls, path):
        path = Path(path)
        try:
            if (
                path.is_symlink()
                or not path.is_file()
                or path.stat().st_size > 16 * 1024
            ):
                raise ValueError(
                    "Use a regular bounded OIDC identify configuration file."
                )
            value = json.loads(path.read_text())
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                "Use a valid bounded OIDC identify configuration file."
            ) from exc
        allowed = set(cls.__dataclass_fields__)
        if not isinstance(value, dict) or set(value) - allowed:
            raise ValueError(
                "The OIDC identify configuration contains unsupported fields."
            )
        if "algorithms" in value:
            value["algorithms"] = tuple(value["algorithms"])
        try:
            return cls(**value)
        except TypeError as exc:
            raise ValueError("The OIDC identify configuration is incomplete.") from exc


class StartInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str


class BootstrapIdentity:
    cookie_name = "__Host-prism_identify"

    def __init__(
        self,
        config,
        *,
        client_secret=None,
        transport=None,
        clock=time.time,
        lifetime_seconds=300,
    ):
        self.config = config
        self.client_secret = client_secret
        self.transport = transport or FixedOIDCTransport()
        self.clock = clock
        self.start_token = secrets.token_urlsafe(32)
        self.start_hash = digest(self.start_token)
        self.start_expires = clock() + lifetime_seconds
        self.pending = None
        self.result = None
        self.lock = threading.Lock()

    def begin(self, token):
        with self.lock:
            if (
                self.result is not None
                or self.pending is not None
                or self.start_hash is None
                or self.clock() >= self.start_expires
                or not isinstance(token, str)
                or not 20 <= len(token) <= 256
                or not secrets.compare_digest(digest(token), self.start_hash)
            ):
                raise Denied("This identity bootstrap request is unavailable.", 401)
            state = secrets.token_urlsafe(32)
            nonce = secrets.token_urlsafe(32)
            verifier = secrets.token_urlsafe(48)
            binding = secrets.token_urlsafe(32)
            self.pending = {
                "state_hash": digest(state),
                "binding_hash": digest(binding),
                "nonce": nonce,
                "verifier": verifier,
                "expires": self.start_expires,
            }
            self.start_hash = None
        query = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": self.config.client_id,
                "redirect_uri": self.config.redirect_uri,
                "scope": "openid",
                "state": state,
                "nonce": nonce,
                "code_challenge": create_s256_code_challenge(verifier),
                "code_challenge_method": "S256",
            }
        )
        return self.config.authorization_endpoint + "?" + query, binding

    def callback(self, *, state, code, binding):
        with self.lock:
            pending = self.pending
            if (
                pending is None
                or self.clock() >= pending["expires"]
                or not isinstance(binding, str)
                or not 20 <= len(binding) <= 256
                or not secrets.compare_digest(digest(binding), pending["binding_hash"])
            ):
                raise Denied("This identity bootstrap request is unavailable.", 401)
            if (
                not isinstance(state, str)
                or not 20 <= len(state) <= 256
                or not secrets.compare_digest(digest(state), pending["state_hash"])
            ):
                raise Denied("This identity bootstrap request is unavailable.", 401)
            self.pending = None
        if not isinstance(code, str) or not 1 <= len(code) <= 2048:
            raise Denied("The identity provider did not return a usable code.", 401)
        token = self.transport.token(
            self.config.token_endpoint,
            {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self.config.client_id,
                "redirect_uri": self.config.redirect_uri,
                "code_verifier": pending["verifier"],
            },
            client_id=self.config.client_id,
            client_secret=self.client_secret,
        )
        principal = validate_id_token(
            self.config,
            self.transport,
            token.get("id_token"),
            pending["nonce"],
            clock=self.clock,
        )
        with self.lock:
            if self.clock() >= self.start_expires:
                raise Denied("This identity bootstrap request is unavailable.", 401)
            self.result = {"issuer": principal.issuer, "subject": principal.subject}
        return self.result


def create_identify_app(bootstrap, *, on_success=lambda result: None):
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    origin = bootstrap.config.public_origin
    canonical_host = urllib.parse.urlsplit(origin).netloc

    @app.middleware("http")
    async def boundary(request, call_next):
        if (
            request.headers.get("host") != canonical_host
            or request.url.scheme != "https"
        ):
            return JSONResponse(
                {"detail": "Use the configured HTTPS origin."}, status_code=403
            )
        if request.method == "POST":
            if (
                request.headers.get("origin") != origin
                or request.headers.get("content-type", "").split(";")[0]
                != "application/json"
            ):
                return JSONResponse(
                    {"detail": "Use the same-origin JSON interface."}, status_code=403
                )
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 16 * 1024:
                    return JSONResponse(
                        {"detail": "Request exceeds 16 KiB."}, status_code=413
                    )
            request._body = bytes(body)
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
            }
        )
        return response

    @app.exception_handler(Denied)
    async def denied(request, exc):
        return JSONResponse({"detail": exc.message}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse(
            {"detail": "Invalid input or unexpected fields."}, status_code=422
        )

    @app.get("/identify")
    def identify_page():
        return HTMLResponse(
            "<!doctype html><meta charset=utf-8><title>Prism identity bootstrap</title>"
            "<p>Starting verified identity discovery…</p><script src=/identify.js></script>"
        )

    @app.get("/identify.js")
    def identify_script():
        return PlainTextResponse(
            "const token=new URLSearchParams(location.hash.slice(1)).get('token');"
            "history.replaceState(null,'',location.pathname);"
            "fetch('/api/identify',{method:'POST',headers:{'content-type':'application/json'},"
            "body:JSON.stringify({token})}).then(async r=>{if(!r.ok)throw 0;"
            "location.assign((await r.json()).authorization_url)}).catch(()=>document.body.textContent='Identity bootstrap failed.');",
            media_type="application/javascript",
        )

    @app.post("/api/identify")
    def identify_start(body: StartInput, request: Request):
        authorization_url, binding = bootstrap.begin(body.token)
        response = JSONResponse({"authorization_url": authorization_url})
        response.set_cookie(
            bootstrap.cookie_name,
            binding,
            secure=True,
            httponly=True,
            samesite="lax",
            path="/",
            max_age=300,
        )
        return response

    @app.get("/auth/oidc/identify/callback")
    def identify_callback(request: Request, state: str = "", code: str = ""):
        result = bootstrap.callback(
            state=state,
            code=code,
            binding=request.cookies.get(bootstrap.cookie_name),
        )
        on_success(result)
        response = HTMLResponse(
            "<!doctype html><meta charset=utf-8><title>Identity verified</title>"
            "<p>Identity verified. Return to the operator terminal.</p>"
        )
        response.delete_cookie(
            bootstrap.cookie_name,
            path="/",
            secure=True,
            httponly=True,
            samesite="lax",
        )
        return response

    return app


def run_identity_bootstrap(
    *,
    oidc_config,
    bind_host,
    port,
    tls_cert_file,
    tls_key_file,
    oidc_client_secret_file=None,
    lifetime_seconds=300,
):
    config = IdentifyOIDCConfig.from_file(oidc_config)
    configured_port = urllib.parse.urlsplit(config.public_origin).port or 443
    if port != configured_port:
        raise ValueError("The listening port must match the canonical OIDC origin.")
    validate_tls_identity(
        tls_cert_file,
        tls_key_file,
        urllib.parse.urlsplit(config.public_origin).hostname,
    )
    secret = (
        read_client_secret(oidc_client_secret_file) if oidc_client_secret_file else None
    )
    bootstrap = BootstrapIdentity(
        config, client_secret=secret, lifetime_seconds=lifetime_seconds
    )
    server = None

    def complete(result):
        print(json.dumps(result, sort_keys=True), flush=True)
        server.should_exit = True

    app = create_identify_app(bootstrap, on_success=complete)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=bind_host,
            port=port,
            access_log=False,
            log_level="warning",
            proxy_headers=False,
            timeout_keep_alive=5,
            timeout_graceful_shutdown=1,
            ssl_certfile=tls_cert_file,
            ssl_keyfile=tls_key_file,
        )
    )
    print(config.public_origin + "/identify#token=" + bootstrap.start_token, flush=True)
    timer = threading.Timer(
        lifetime_seconds, lambda: setattr(server, "should_exit", True)
    )
    timer.daemon = True
    timer.start()
    try:
        server.run()
    finally:
        timer.cancel()
    if bootstrap.result is None:
        raise ValueError("Identity bootstrap ended without a verified identity.")
    return bootstrap.result
