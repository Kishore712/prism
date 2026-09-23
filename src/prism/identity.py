"""Provider-neutral OIDC identity for the separately configured HTTPS mode."""

import base64
import json
import os
import secrets
import ssl
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from authlib.oauth2.rfc7636 import create_s256_code_challenge
from authlib.oidc.core import CodeIDToken
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

from prism.sharing import Denied, NamedPrincipal, digest

ALLOWED_ALGORITHMS = frozenset({"RS256", "PS256", "ES256"})


def _https_url(value, *, origin=False, endpoint=False):
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("OIDC URLs must be fixed HTTPS URLs.")
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or (endpoint and parsed.query)
        or (origin and (parsed.query or parsed.path not in ("", "/")))
    ):
        raise ValueError(
            "OIDC URLs must be fixed HTTPS URLs without userinfo or fragments."
        )
    return value


@dataclass(frozen=True)
class OIDCConfig:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    client_id: str
    redirect_uri: str
    public_origin: str
    owner_subject: str
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
        if (
            self.public_origin.endswith("/")
            or self.redirect_uri != self.public_origin + "/auth/oidc/callback"
        ):
            raise ValueError(
                "The OIDC callback must be the canonical HTTPS origin plus /auth/oidc/callback."
            )
        if not isinstance(self.client_id, str) or not 1 <= len(self.client_id) <= 256:
            raise ValueError("OIDC client_id is required.")
        if (
            not isinstance(self.owner_subject, str)
            or not 1 <= len(self.owner_subject) <= 512
        ):
            raise ValueError("The exact owner OIDC subject is required.")
        if (
            not isinstance(self.algorithms, tuple)
            or not self.algorithms
            or len(set(self.algorithms)) != len(self.algorithms)
            or not set(self.algorithms).issubset(ALLOWED_ALGORITHMS)
        ):
            raise ValueError("Choose a non-empty fixed asymmetric OIDC algorithm set.")

    @classmethod
    def from_file(cls, path):
        try:
            path = Path(path)
            if (
                path.is_symlink()
                or not path.is_file()
                or path.stat().st_size > 16 * 1024
            ):
                raise ValueError("Use a regular bounded OIDC configuration file.")
            value = json.loads(path.read_text())
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Use a valid bounded OIDC configuration file.") from exc
        allowed = {
            "issuer",
            "authorization_endpoint",
            "token_endpoint",
            "jwks_uri",
            "client_id",
            "redirect_uri",
            "public_origin",
            "owner_subject",
            "algorithms",
        }
        if not isinstance(value, dict) or set(value) - allowed:
            raise ValueError("The OIDC configuration contains unsupported fields.")
        if "algorithms" in value:
            value["algorithms"] = tuple(value["algorithms"])
        try:
            return cls(**value)
        except TypeError as exc:
            raise ValueError("The OIDC configuration is incomplete.") from exc


def read_client_secret(path):
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = __import__("os").fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or not 20 <= info.st_size <= 4096
        ):
            raise ValueError(
                "Use an owner-only (0600) regular OIDC client secret file."
            )
        try:
            secret = os.read(fd, 4097).decode().strip()
        except UnicodeError as exc:
            raise ValueError("The OIDC client secret file is invalid.") from exc
        if not secret or any(char.isspace() for char in secret):
            raise ValueError("The OIDC client secret file is invalid.")
        return secret
    finally:
        os.close(fd)


class FixedOIDCTransport:
    """Small fixed-endpoint transport; redirects and oversized bodies are rejected."""

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    def __init__(self, *, ssl_context=None):
        self.opener = urllib.request.build_opener(
            self.NoRedirect(),
            urllib.request.HTTPSHandler(
                context=ssl_context or ssl.create_default_context()
            ),
        )

    def _read_json(self, request):
        try:
            with self.opener.open(request, timeout=10) as response:
                if response.status != 200:
                    raise Denied("The identity provider rejected this login.", 401)
                body = response.read(256 * 1024 + 1)
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            raise Denied(
                "The configured identity provider is unavailable.", 503
            ) from exc
        if len(body) > 256 * 1024:
            raise Denied("The identity provider response is too large.", 503)
        try:
            value = json.loads(body)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise Denied(
                "The identity provider returned an invalid response.", 503
            ) from exc
        if not isinstance(value, dict):
            raise Denied("The identity provider returned an invalid response.", 503)
        return value

    def token(self, url, form, *, client_id, client_secret=None):
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        if client_secret is not None:
            basic_id = urllib.parse.quote(client_id, safe="")
            basic_secret = urllib.parse.quote(client_secret, safe="")
            encoded = base64.b64encode(f"{basic_id}:{basic_secret}".encode()).decode()
            headers["Authorization"] = "Basic " + encoded
        return self._read_json(
            urllib.request.Request(
                url,
                urllib.parse.urlencode(form).encode(),
                headers=headers,
                method="POST",
            )
        )

    def jwks(self, url):
        return self._read_json(
            urllib.request.Request(
                url, headers={"Accept": "application/json"}, method="GET"
            )
        )


def validate_id_token(config, transport, id_token, nonce, *, clock=time.time):
    """Validate a signed OIDC ID token and return its exact named principal."""
    if not isinstance(id_token, str) or len(id_token) > 32 * 1024:
        raise Denied("The identity provider returned an invalid identity token.", 401)
    try:
        jwks = transport.jwks(config.jwks_uri)
        keys = KeySet.import_key_set(jwks)
        token = jwt.decode(id_token, keys, algorithms=config.algorithms)
        claims = token.claims
        now = clock()
        CodeIDToken(
            claims,
            token.header,
            options={
                "iss": {"essential": True, "value": config.issuer},
                "sub": {"essential": True},
                "aud": {"essential": True, "value": config.client_id},
                "exp": {"essential": True},
                "iat": {"essential": True},
                "nonce": {"essential": True, "value": nonce},
            },
            params={"nonce": nonce, "client_id": config.client_id},
        ).validate(now=int(now), leeway=30)
        if (
            not isinstance(claims["sub"], str)
            or not 1 <= len(claims["sub"]) <= 512
            or any(ord(char) < 32 for char in claims["sub"])
            or type(claims["iat"]) not in (int, float)
            or type(claims["exp"]) not in (int, float)
            or claims["iat"] > now + 30
            or claims["iat"] < now - 600
            or (
                isinstance(claims["aud"], list)
                and len(claims["aud"]) > 1
                and claims.get("azp") != config.client_id
            )
        ):
            raise ValueError("invalid identity claims")
    except (JoseError, KeyError, TypeError, ValueError) as exc:
        raise Denied("The identity token failed strict validation.", 401) from exc
    return NamedPrincipal(config.issuer, claims["sub"])


class OIDCAuth:
    """Server-side OIDC state and sessions for the explicit HTTPS identity mode."""

    cookie_name = "__Host-prism_identity"
    login_cookie_name = "__Host-prism_oidc_login"

    def __init__(
        self,
        store,
        config: OIDCConfig,
        *,
        client_secret=None,
        transport=None,
        clock=time.time,
        activation_check=None,
    ):
        self.store = store
        self.config = config
        self.client_secret = client_secret
        self.transport = transport or FixedOIDCTransport()
        self.clock = clock
        self.activation_check = activation_check
        self.owner = NamedPrincipal(config.issuer, config.owner_subject)
        self.owner_key = self.owner.key
        with store.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS oidc_pending (
                    state_hash TEXT PRIMARY KEY, binding_hash TEXT NOT NULL,
                    nonce TEXT NOT NULL, verifier TEXT NOT NULL,
                    purpose TEXT NOT NULL, invitation_id TEXT,
                    expires REAL NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS identity_sessions (
                    cookie_hash TEXT PRIMARY KEY, issuer TEXT NOT NULL,
                    subject TEXT NOT NULL, role TEXT NOT NULL,
                    review_session TEXT, csrf TEXT NOT NULL,
                    expires REAL NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS oidc_discoveries (
                    id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL, issuer TEXT, subject TEXT,
                    expires REAL NOT NULL, created REAL NOT NULL,
                    completed REAL);
            """)
            db.execute("DELETE FROM oidc_discoveries WHERE expires<=?", (self.clock(),))

    def create_discovery(self):
        now = self.clock()
        identifier, token = secrets.token_hex(16), secrets.token_urlsafe(32)
        with self.store.connect() as db:
            db.execute("DELETE FROM oidc_discoveries WHERE expires<=?", (now,))
            if db.execute("SELECT count(*) FROM oidc_discoveries").fetchone()[0] >= 16:
                raise Denied("Too many identity checks are pending.", 429)
            db.execute(
                "INSERT INTO oidc_discoveries VALUES(?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    digest(token),
                    "created",
                    None,
                    None,
                    now + 900,
                    now,
                    None,
                ),
            )
        return {"id": identifier, "token": token, "expires": now + 900}

    def discovery(self, identifier):
        if (
            not isinstance(identifier, str)
            or len(identifier) != 32
            or any(char not in "0123456789abcdef" for char in identifier)
        ):
            raise Denied()
        with self.store.connect() as db:
            db.execute("DELETE FROM oidc_discoveries WHERE expires<=?", (self.clock(),))
            row = db.execute(
                "SELECT id,status,issuer,subject,expires,completed FROM oidc_discoveries "
                "WHERE id=? AND expires>?",
                (identifier, self.clock()),
            ).fetchone()
        if row is None:
            raise Denied("This identity check is unavailable.", 404)
        return dict(row)

    def begin(self, *, invitation_token=None, discovery_token=None, owner=False):
        if sum((bool(owner), bool(invitation_token), bool(discovery_token))) != 1:
            raise Denied("Choose one login purpose.", 400)
        invitation_id = None
        if invitation_token:
            invitation_id = self.store.invitation(invitation_token)["id"]
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        verifier, binding = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        now = self.clock()
        with self.store.connect() as db:
            db.execute("DELETE FROM oidc_pending WHERE expires<=?", (now,))
            if db.execute("SELECT count(*) FROM oidc_pending").fetchone()[0] >= 64:
                raise Denied(
                    "Too many login attempts are pending. Try again later.", 429
                )
            if discovery_token:
                if (
                    not isinstance(discovery_token, str)
                    or not 40 <= len(discovery_token) <= 128
                ):
                    raise Denied("This identity check is unavailable.", 401)
                row = db.execute(
                    "SELECT id FROM oidc_discoveries WHERE token_hash=? "
                    "AND status='created' AND expires>?",
                    (digest(discovery_token), now),
                ).fetchone()
                if row is None:
                    raise Denied("This identity check is unavailable.", 401)
                invitation_id = row["id"]
                db.execute(
                    "UPDATE oidc_discoveries SET status='started' WHERE id=?",
                    (invitation_id,),
                )
            db.execute(
                "INSERT INTO oidc_pending VALUES(?,?,?,?,?,?,?,?)",
                (
                    digest(state),
                    digest(binding),
                    nonce,
                    verifier,
                    "owner"
                    if owner
                    else "discovery"
                    if discovery_token
                    else "invitation",
                    invitation_id,
                    now + 300,
                    now,
                ),
            )
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
        return {
            "authorization_url": self.config.authorization_endpoint + "?" + query,
            "binding": binding,
        }

    def _consume_pending(self, state, binding):
        if (
            not isinstance(state, str)
            or not 20 <= len(state) <= 256
            or not isinstance(binding, str)
            or not 20 <= len(binding) <= 256
        ):
            raise Denied("This login attempt is unavailable.", 401)
        now = self.clock()
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM oidc_pending WHERE state_hash=? AND expires>?",
                (digest(state), now),
            ).fetchone()
            if row is None or not secrets.compare_digest(
                row["binding_hash"], digest(binding)
            ):
                raise Denied("This login attempt is unavailable.", 401)
            db.execute("DELETE FROM oidc_pending WHERE state_hash=?", (digest(state),))
            return dict(row)

    def _identity(self, id_token, nonce):
        return validate_id_token(
            self.config,
            self.transport,
            id_token,
            nonce,
            clock=self.clock,
        )

    def callback(self, *, state, code, binding, old_cookie=None):
        pending = self._consume_pending(state, binding)
        if not isinstance(code, str) or not 1 <= len(code) <= 2048:
            raise Denied("The identity provider did not return a usable code.", 401)
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "code_verifier": pending["verifier"],
        }
        token = self.transport.token(
            self.config.token_endpoint,
            form,
            client_id=self.config.client_id,
            client_secret=self.client_secret,
        )
        principal = self._identity(token.get("id_token"), pending["nonce"])
        if pending["purpose"] == "discovery":
            now = self.clock()
            with self.store.connect() as db:
                cursor = db.execute(
                    "UPDATE oidc_discoveries SET status='complete',issuer=?,subject=?,"
                    "completed=? WHERE id=? AND status='started' AND expires>?",
                    (
                        principal.issuer,
                        principal.subject,
                        now,
                        pending["invitation_id"],
                        now,
                    ),
                )
                if cursor.rowcount != 1:
                    raise Denied("This identity check is unavailable.", 401)
            return {"role": "discovery"}
        if pending["purpose"] == "owner":
            if principal != self.owner:
                raise Denied("This identity is not the configured owner.", 403)
            role, review_session = "owner", None
            session_expires = self.clock() + 3600
        else:
            if self.activation_check is None:
                raise Denied("Named-session activation policy is unavailable.", 503)
            self.activation_check(pending["invitation_id"])
            state_row = self.store.redeem_invitation_id(
                pending["invitation_id"], principal
            )
            role, review_session = "review", state_row["id"]
            session_expires = state_row["expires"]
        cookie, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = self.clock()
        with self.store.connect() as db:
            if old_cookie:
                db.execute(
                    "DELETE FROM identity_sessions WHERE cookie_hash=?",
                    (digest(old_cookie),),
                )
            db.execute("DELETE FROM identity_sessions WHERE expires<=?", (now,))
            db.execute(
                "INSERT INTO identity_sessions VALUES(?,?,?,?,?,?,?,?)",
                (
                    digest(cookie),
                    principal.issuer,
                    principal.subject,
                    role,
                    review_session,
                    csrf,
                    session_expires,
                    now,
                ),
            )
        return {
            "cookie": cookie,
            "csrf": csrf,
            "role": role,
            "session": review_session,
            "max_age": max(1, int(session_expires - now)),
        }

    def authenticate(self, request, *, owner=False):
        cookie = request.cookies.get(self.cookie_name, "")
        now = self.clock()
        with self.store.connect() as db:
            row = db.execute(
                "SELECT * FROM identity_sessions WHERE cookie_hash=? AND expires>?",
                (digest(cookie), now),
            ).fetchone()
        if row is None or row["role"] != ("owner" if owner else "review"):
            raise Denied("Sign in with the configured identity provider.", 401)
        principal = NamedPrincipal(row["issuer"], row["subject"])
        if owner:
            if principal != self.owner:
                raise Denied("This identity is not the configured owner.", 403)
        else:
            self.store.session(row["review_session"], principal)
        if request.method not in ("GET", "HEAD") and not secrets.compare_digest(
            request.headers.get("x-prism-csrf", ""), row["csrf"]
        ):
            raise Denied("This request requires the current session's form token.")
        return self.owner_key if owner else principal

    def browser_state(self, request, *, owner=False):
        self.authenticate(request, owner=owner)
        cookie = request.cookies[self.cookie_name]
        with self.store.connect() as db:
            row = db.execute(
                "SELECT csrf,review_session FROM identity_sessions WHERE cookie_hash=?",
                (digest(cookie),),
            ).fetchone()
        return {"csrf": row["csrf"], "review_session": row["review_session"]}

    def authenticate_review(self, request, expected_session):
        principal = self.authenticate(request)
        state = self.browser_state(request)
        if not secrets.compare_digest(state["review_session"] or "", expected_session):
            raise Denied()
        return principal
