"""Loopback-only synthetic demo. Local capability actors are not pilot identity."""

import asyncio
import json
import secrets
import time
import urllib.parse
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from prism.conversation import Conversations
from prism.engine import EngineError
from prism.handoff import Handoffs, HandoffSelection
from prism.identity import OIDCAuth
from prism.jobs import Jobs
from prism.owner import MODEL_POLICY, OwnerConversations, OwnerIdentity, OwnerWorkspace
from prism.projects import ProjectSource
from prism.sharing import Denied, NamedPrincipal, Source, Store


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Login(Input):
    token: str = Field(min_length=32, max_length=128)


class Candidate(Input):
    files: list[str] = Field(min_length=1, max_length=8)
    purpose: str = Field(min_length=5, max_length=1000)
    mode: Literal["inspect", "verify"]


class Approval(Input):
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class SessionInput(Input):
    version: str = Field(pattern=r"^[0-9a-f]{32}$")


class RunInput(Input):
    seed: int = Field(ge=0, le=1000)
    request_key: str = Field(min_length=8, max_length=80)


class JsonRunInput(Input):
    action: Literal["json-check"]
    request_key: str = Field(min_length=8, max_length=80)


class QuestionInput(Input):
    question: str = Field(min_length=1, max_length=1500)
    request_key: str = Field(min_length=8, max_length=80)


class AccessInput(Input):
    description: str = Field(min_length=5, max_length=500)


class InvitationInput(Input):
    recipient_issuer: str = Field(min_length=8, max_length=2048)
    recipient_subject: str = Field(min_length=1, max_length=512)
    mode: Literal["inspect", "verify"]
    expires_in: int = Field(ge=300, le=86400)


class InvitationStart(Input):
    token: str = Field(min_length=40, max_length=128)


class OwnerConversationInput(Input):
    model_policy: Literal["openai-owner-project-v1"] | None = None
    files: list[str] | None = Field(default=None, min_length=1, max_length=8)


class DemoAuth:
    """Ephemeral local credentials; no passwords, federation, or named identity."""

    def __init__(self):
        self.owner_key = "owner"
        self.tokens = {
            actor: secrets.token_urlsafe(32)
            for actor in ("owner", "reviewer", "observer")
        }
        self.cookies = {}
        self.csrf = {}

    def exchange(self, token):
        for actor, expected in self.tokens.items():
            if secrets.compare_digest(expected, token):
                cookie = secrets.token_urlsafe(32)
                self.cookies[cookie] = actor
                self.csrf[cookie] = secrets.token_urlsafe(32)
                return actor, cookie, self.csrf[cookie]
        raise Denied("A current local demo credential is required.", 401)

    def authenticate(self, request, *, owner=False):
        cookie = request.cookies.get("prism_owner" if owner else "prism_review", "")
        actor = self.cookies.get(cookie)
        if actor not in (("owner",) if owner else ("reviewer", "observer")):
            raise Denied("Open the local link supplied by the owner to sign in.", 401)
        if request.method not in ("GET", "HEAD") and not secrets.compare_digest(
            request.headers.get("x-prism-csrf", ""), self.csrf.get(cookie, "missing")
        ):
            raise Denied("This request requires the current session's form token.")
        return actor


def create_app(
    store: Store,
    source: Source,
    *,
    port=8765,
    auth=None,
    jobs=None,
    conversations=None,
    key=None,
    model_budget_cents=None,
    project_sources=(),
    runtime_registry=None,
):
    auth = auth or DemoAuth()
    identity_mode = isinstance(auth, OIDCAuth)
    if model_budget_cents is None:
        model_budget_cents = 0 if identity_mode else 100
    origin = auth.config.public_origin if identity_mode else f"http://127.0.0.1:{port}"
    canonical_host = (
        urllib.parse.urlsplit(origin).netloc if identity_mode else f"127.0.0.1:{port}"
    )
    if jobs is None:
        jobs = Jobs(store, registry=runtime_registry)
    elif runtime_registry is not None and jobs.registry is not runtime_registry:
        raise ValueError("Jobs and the application must share one runtime registry.")
    runtime_registry = jobs.registry
    conversations = conversations or Conversations(
        store, jobs, key=key, budget_cents=model_budget_cents
    )
    owner_workspace = OwnerWorkspace(store)
    owner_workspace.register_project(source, owner=auth.owner_key)
    seen = {"paired-evaluation"}
    for project_source in project_sources:
        if (
            not isinstance(project_source, ProjectSource)
            or project_source.project_id in seen
        ):
            raise ValueError(
                "Configured project IDs must be unique and may not replace the synthetic fixture."
            )
        seen.add(project_source.project_id)
        owner_workspace.register_project(project_source, owner=auth.owner_key)
    handoffs = Handoffs(owner_workspace)
    # Both services use the same ledger, execution slots and aggregate limits.
    # Startup recovery ran above; constructing another scoped adapter must not
    # mark already admitted work as interrupted or uncertain.
    owner_jobs = Jobs(
        owner_workspace,
        socket=jobs.socket,
        recover=False,
        registry=runtime_registry,
    )
    owner_conversations = OwnerConversations(
        owner_workspace,
        owner_jobs,
        key=conversations.key,
        test_model=conversations.test_model,
        budget_cents=conversations.budget_cents,
        recover=False,
    )
    if identity_mode:

        def activation_check(invitation):
            activation = store.invitation_activation(invitation)
            action = activation["action"]
            if activation["mode"] == "verify":
                if not isinstance(action, dict):
                    raise Denied("The invitation has no reviewed action.", 409)
                try:
                    runtime_registry.assert_ready(action.get("profile"))
                except (EngineError, ValueError):
                    raise Denied(
                        "The approved runtime profile is not ready on this host.", 503
                    ) from None

        auth.activation_check = activation_check

    def owner_identity(request, project):
        return OwnerIdentity(auth.authenticate(request, owner=True), project)

    def review_identity(request, session=None):
        if identity_mode and session is not None:
            return auth.authenticate_review(request, session)
        return auth.authenticate(request)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await asyncio.to_thread(jobs.shutdown)
        await asyncio.to_thread(owner_jobs.shutdown)

    app = FastAPI(
        title="Prism synthetic local demo",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.auth, app.state.store = auth, store
    app.state.owner_workspace = owner_workspace
    app.state.owner_conversations = owner_conversations
    app.state.owner_jobs = owner_jobs
    app.state.handoffs = handoffs
    static = Path(__file__).parent / "static"

    @app.middleware("http")
    async def boundary(request, call_next):
        if request.headers.get("host") != canonical_host or (
            identity_mode and request.url.scheme != "https"
        ):
            return JSONResponse(
                {"detail": "Use the configured loopback address."}, status_code=403
            )
        oidc_return = (
            identity_mode
            and request.method == "GET"
            and request.url.path == "/auth/oidc/callback"
        )
        oidc_landing = (
            identity_mode
            and request.method == "GET"
            and request.url.path
            in ("/owner", "/review", "/invite", "/identify", "/identify/completed")
            and request.headers.get("sec-fetch-mode") == "navigate"
            and request.headers.get("sec-fetch-dest") == "document"
        )
        if request.headers.get("origin") not in (None, origin) or (
            request.headers.get("sec-fetch-site") == "cross-site"
            and not (oidc_return or oidc_landing)
        ):
            return JSONResponse(
                {"detail": "Cross-origin access is not allowed."}, status_code=403
            )
        if request.method not in ("GET", "HEAD"):
            if (
                request.headers.get("origin") != origin
                or request.headers.get("content-type", "").split(";")[0]
                != "application/json"
            ):
                return JSONResponse(
                    {"detail": "Use the same-origin JSON interface."}, status_code=403
                )
            # Bound actual received bytes, including transfer-encoded requests.
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
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            }
        )
        if identity_mode:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.exception_handler(Denied)
    async def denied(request, exc):
        # Do not log request text, attempted file IDs, token values or raw paths.
        if exc.status == 403:
            with store.connect() as db:
                store.event(db, "access_denied", "local_client", "api", "denied")
        return JSONResponse({"detail": exc.message}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse(
            {"detail": "Invalid input or unexpected fields. Check the allowed values."},
            status_code=422,
        )

    @app.get("/")
    def home():
        return RedirectResponse("/owner")

    @app.get("/owner")
    @app.get("/review")
    @app.get("/invite")
    @app.get("/identify")
    @app.get("/identify/completed")
    def index():
        return FileResponse(static / "index.html")

    @app.get("/assets/{name}")
    def asset(name: str):
        if name not in ("app.js", "app.css") or not (static / name).is_file():
            raise Denied("Build the local interface before starting the demo.", 404)
        return FileResponse(static / name)

    @app.get("/api/auth/mode")
    def auth_mode():
        return {
            "identity_mode": identity_mode,
            "owner_login": "/auth/oidc/owner" if identity_mode else None,
        }

    @app.post("/api/login")
    def login(body: Login):
        if identity_mode:
            raise Denied("Use the configured identity provider.", 404)
        actor, cookie, csrf = auth.exchange(body.token)
        response = JSONResponse({"actor": actor, "csrf": csrf})
        owner = actor == "owner"
        response.set_cookie(
            "prism_owner" if owner else "prism_review",
            cookie,
            httponly=True,
            samesite="strict",
            path="/api/owner" if owner else "/api/review",
            max_age=3600,
        )
        return response

    @app.get("/auth/oidc/owner")
    def oidc_owner_start():
        if not identity_mode:
            raise Denied("Named identity mode is not configured.", 404)
        login = auth.begin(owner=True)
        response = RedirectResponse(login["authorization_url"], status_code=303)
        response.set_cookie(
            auth.login_cookie_name,
            login["binding"],
            secure=True,
            httponly=True,
            samesite="lax",
            path="/",
            max_age=300,
        )
        return response

    @app.post("/api/auth/oidc/invitation")
    def oidc_invitation_start(body: InvitationStart):
        if not identity_mode:
            raise Denied("Named identity mode is not configured.", 404)
        login = auth.begin(invitation_token=body.token)
        response = JSONResponse({"authorization_url": login["authorization_url"]})
        response.set_cookie(
            auth.login_cookie_name,
            login["binding"],
            secure=True,
            httponly=True,
            samesite="lax",
            path="/",
            max_age=300,
        )
        return response

    @app.post("/api/auth/oidc/discovery")
    def oidc_discovery_start(body: InvitationStart):
        if not identity_mode:
            raise Denied("Named identity mode is not configured.", 404)
        login = auth.begin(discovery_token=body.token)
        response = JSONResponse({"authorization_url": login["authorization_url"]})
        response.set_cookie(
            auth.login_cookie_name,
            login["binding"],
            secure=True,
            httponly=True,
            samesite="lax",
            path="/",
            max_age=300,
        )
        return response

    @app.get("/auth/oidc/callback")
    def oidc_callback(request: Request, state: str = "", code: str = ""):
        if not identity_mode:
            raise Denied("Named identity mode is not configured.", 404)
        result = auth.callback(
            state=state,
            code=code,
            binding=request.cookies.get(auth.login_cookie_name),
            old_cookie=request.cookies.get(auth.cookie_name),
        )
        target = (
            "/identify/completed"
            if result["role"] == "discovery"
            else "/owner"
            if result["role"] == "owner"
            else "/review"
        )
        response = RedirectResponse(target, status_code=303)
        response.delete_cookie(
            auth.login_cookie_name, path="/", secure=True, httponly=True, samesite="lax"
        )
        if result["role"] != "discovery":
            response.set_cookie(
                auth.cookie_name,
                result["cookie"],
                secure=True,
                httponly=True,
                samesite="lax",
                path="/",
                max_age=result["max_age"],
            )
        return response

    @app.post("/api/owner/identity-discoveries")
    def create_identity_discovery(request: Request):
        auth.authenticate(request, owner=True)
        if not identity_mode:
            raise Denied("Named identity mode is not configured.", 404)
        result = auth.create_discovery()
        result["url"] = origin + "/identify#token=" + result.pop("token")
        return result

    @app.get("/api/owner/identity-discoveries/{identifier}")
    def get_identity_discovery(identifier: str, request: Request):
        auth.authenticate(request, owner=True)
        if not identity_mode:
            raise Denied("Named identity mode is not configured.", 404)
        return auth.discovery(identifier)

    @app.get("/api/owner/state")
    def owner_state(request: Request):
        actor = auth.authenticate(request, owner=True)
        browser = auth.browser_state(request, owner=True) if identity_mode else None
        return {
            "actor": actor,
            "csrf": browser["csrf"]
            if identity_mode
            else auth.csrf[request.cookies["prism_owner"]],
            "catalog": source.catalog(),
            "projects": owner_workspace.projects(actor),
            "owner_model_policy": MODEL_POLICY,
            "versions": store.versions(owner=True),
            "activity": store.activity(),
            "sessions": store.owner_sessions(),
            "measurements": store.measurements,
            "pilot_ready": False,
            "runtime_profile": runtime_registry.profile,
            "model": conversations.route(),
            "requests": conversations.requests(),
            "identity_mode": identity_mode,
            "invitations": store.invitations() if identity_mode else [],
            "demo_links": {}
            if identity_mode
            else {
                role: f"{origin}/review#token={auth.tokens[role]}"
                for role in ("reviewer", "observer")
            },
        }

    @app.get("/api/owner/projects/{project}/conversations")
    def owner_chats(project: str, request: Request):
        return owner_workspace.conversations(owner_identity(request, project))

    @app.get("/api/owner/projects/{project}/catalog")
    def owner_catalog(project: str, request: Request):
        return owner_workspace.catalog(owner_identity(request, project))

    @app.post("/api/owner/projects/{project}/conversations")
    def new_owner_chat(project: str, body: OwnerConversationInput, request: Request):
        identity = owner_identity(request, project)
        source_for_chat = owner_workspace.sources.get(project)
        if (
            source_for_chat is not None
            and getattr(source_for_chat, "action_profile", None) == "reference-linux"
            and body.files
            and any(name.lower().endswith(".json") for name in body.files)
        ):
            try:
                runtime_registry.assert_ready("reference-linux")
            except (EngineError, ValueError):
                raise Denied(
                    "The approved runtime profile is not ready on this host.", 503
                ) from None
        return owner_workspace.new_conversation(identity, body.model_policy, body.files)

    @app.get("/api/owner/projects/{project}/conversations/{session}")
    def owner_chat(project: str, session: str, request: Request):
        return owner_workspace.session(session, owner_identity(request, project))

    @app.get("/api/owner/projects/{project}/conversations/{session}/turns")
    def owner_turns(project: str, session: str, request: Request):
        return owner_conversations.history(session, owner_identity(request, project))

    @app.post("/api/owner/projects/{project}/conversations/{session}/turns")
    async def owner_ask(
        project: str, session: str, body: QuestionInput, request: Request
    ):
        return await owner_conversations.ask(
            session, owner_identity(request, project), body.question, body.request_key
        )

    @app.get(
        "/api/owner/projects/{project}/conversations/{session}/evidence/{evidence_id}"
    )
    def owner_evidence(
        project: str,
        session: str,
        evidence_id: str,
        request: Request,
        start: int = 1,
        end: int = 120,
    ):
        return owner_workspace.evidence(
            session, owner_identity(request, project), evidence_id, start, end
        )

    @app.get("/api/owner/projects/{project}/conversations/{session}/search")
    def owner_search(project: str, session: str, request: Request, query: str):
        return owner_workspace.search(session, owner_identity(request, project), query)

    @app.get("/api/owner/projects/{project}/conversations/{session}/runs")
    def owner_runs(project: str, session: str, request: Request):
        return owner_jobs.list(session, owner_identity(request, project))

    @app.post("/api/owner/projects/{project}/conversations/{session}/runs")
    def owner_run(
        project: str,
        session: str,
        body: RunInput | JsonRunInput,
        request: Request,
    ):
        actor = owner_identity(request, project)
        if isinstance(body, JsonRunInput):
            return owner_jobs.submit_json_check(session, actor, body.request_key)
        return owner_jobs.submit(session, actor, body.seed, body.request_key)

    @app.get("/api/owner/projects/{project}/conversations/{session}/runs/{run_id}")
    def owner_result(project: str, session: str, run_id: str, request: Request):
        return owner_jobs.get(session, owner_identity(request, project), run_id)

    @app.get("/api/owner/projects/{project}/conversations/{session}/handoff-source")
    def handoff_source(project: str, session: str, request: Request):
        return handoffs.source(session, owner_identity(request, project))

    @app.post("/api/owner/projects/{project}/conversations/{session}/handoffs")
    def handoff_candidate(
        project: str, session: str, body: HandoffSelection, request: Request
    ):
        return handoffs.freeze(session, owner_identity(request, project), body)

    @app.post("/api/owner/candidates")
    def candidate(body: Candidate, request: Request):
        auth.authenticate(request, owner=True)
        return store.candidate(source.freeze(body.files, body.purpose, body.mode))

    @app.post("/api/owner/projects/{project}/candidates")
    def project_candidate(project: str, body: Candidate, request: Request):
        return owner_workspace.candidate(
            owner_identity(request, project), body.files, body.purpose, body.mode
        )

    @app.get("/api/owner/versions/{version}")
    def owner_version(version: str, request: Request):
        auth.authenticate(request, owner=True)
        return store.owner_version(version)

    @app.get("/api/owner/versions/{version}/handoff-draft")
    def handoff_draft(version: str, request: Request):
        return handoffs.draft(version, auth.authenticate(request, owner=True))

    @app.post("/api/owner/versions/{version}/approve")
    def approve(version: str, body: Approval, request: Request):
        auth.authenticate(request, owner=True)
        result = store.approve(version, body.digest)
        store.measure("candidate_review_seconds", time.time() - result["created"])
        return result

    @app.get("/api/owner/invitations")
    def invitations(request: Request):
        auth.authenticate(request, owner=True)
        if not identity_mode:
            raise Denied("Named identity mode is not configured.", 404)
        return store.invitations()

    @app.post("/api/owner/versions/{version}/invitations")
    def create_invitation(version: str, body: InvitationInput, request: Request):
        auth.authenticate(request, owner=True)
        if not identity_mode or body.recipient_issuer != auth.config.issuer:
            raise Denied("Choose a recipient at the configured issuer.", 400)
        created = store.create_invitation(
            version,
            NamedPrincipal(body.recipient_issuer, body.recipient_subject),
            mode=body.mode,
            expires_in=body.expires_in,
        )
        created["url"] = origin + "/invite#token=" + created.pop("token")
        return created

    @app.post("/api/owner/grants/{grant}/revoke")
    def revoke_grant(grant: str, request: Request):
        auth.authenticate(request, owner=True)
        if not identity_mode:
            raise Denied("Named identity mode is not configured.", 404)
        store.revoke_grant(grant)
        return {"status": "revoked"}

    @app.get("/api/owner/sessions/{session}/turns")
    def owner_history(session: str, request: Request):
        auth.authenticate(request, owner=True)
        return conversations.owner_history(session)

    @app.post("/api/owner/versions/{version}/revoke")
    def revoke(version: str, request: Request):
        auth.authenticate(request, owner=True)
        store.revoke(version)
        return {
            "status": "revoked",
            "notice": "Future access is blocked. Already seen or saved information cannot be recalled.",
        }

    @app.get("/api/review/state")
    def review_state(request: Request):
        actor = review_identity(request)
        browser = auth.browser_state(request) if identity_mode else None
        if identity_mode:
            current = store.session(browser["review_session"], actor)
            versions = [
                value for value in store.versions() if value["id"] == current["version"]
            ]
        else:
            versions = store.versions()
        return {
            "actor": actor.key if isinstance(actor, NamedPrincipal) else actor,
            "csrf": browser["csrf"]
            if identity_mode
            else auth.csrf[request.cookies["prism_review"]],
            "versions": versions,
            "session": browser["review_session"] if identity_mode else None,
            "identity_mode": identity_mode,
            "model": conversations.route(),
            "pilot_ready": False,
            "runtime_profile": runtime_registry.profile,
        }

    @app.post("/api/review/sessions")
    def new_session(body: SessionInput, request: Request):
        actor = review_identity(request)
        if identity_mode:
            raise Denied("The invitation already created the only authorized session.")
        with store.connect() as db:
            version = db.execute(
                "SELECT manifest FROM versions WHERE id=? AND approved=1 AND revoked=0",
                (body.version,),
            ).fetchone()
        if version is not None:
            manifest = json.loads(version["manifest"])
            action = manifest.get("action")
            if actor == "reviewer" and manifest.get("mode") == "verify" and action:
                try:
                    runtime_registry.assert_ready(action.get("profile"))
                except (EngineError, ValueError):
                    raise Denied(
                        "The approved runtime profile is not ready on this host.", 503
                    ) from None
        return store.new_session(body.version, actor)

    @app.get("/api/review/sessions/{session}")
    def session_state(session: str, request: Request):
        return store.session(session, review_identity(request, session))

    @app.get("/api/review/sessions/{session}/background")
    def background(session: str, request: Request):
        return store.background(session, review_identity(request, session))

    @app.get("/api/review/sessions/{session}/historical-runs/{run_id}")
    def historical_run(session: str, run_id: str, request: Request):
        return store.historical_run(session, review_identity(request, session), run_id)

    @app.get("/api/review/sessions/{session}/evidence/{evidence_id}")
    def evidence(
        session: str, evidence_id: str, request: Request, start: int = 1, end: int = 120
    ):
        return store.evidence(
            session, review_identity(request, session), evidence_id, start, end
        )

    @app.get("/api/review/sessions/{session}/search")
    def search(session: str, request: Request, query: str):
        return store.search(session, review_identity(request, session), query)

    @app.get("/api/review/sessions/{session}/runs")
    def runs(session: str, request: Request):
        return jobs.list(session, review_identity(request, session))

    @app.post("/api/review/sessions/{session}/runs")
    def run(session: str, body: RunInput | JsonRunInput, request: Request):
        actor = review_identity(request, session)
        if isinstance(body, JsonRunInput):
            return jobs.submit_json_check(session, actor, body.request_key)
        return jobs.submit(session, actor, body.seed, body.request_key)

    @app.get("/api/review/sessions/{session}/runs/{run_id}")
    def result(session: str, run_id: str, request: Request):
        return jobs.get(session, review_identity(request, session), run_id)

    @app.get("/api/review/sessions/{session}/turns")
    def turns(session: str, request: Request):
        return conversations.history(session, review_identity(request, session))

    @app.post("/api/review/sessions/{session}/turns")
    async def ask(session: str, body: QuestionInput, request: Request):
        return await conversations.ask(
            session, review_identity(request, session), body.question, body.request_key
        )

    @app.post("/api/review/sessions/{session}/requests")
    def request_access(session: str, body: AccessInput, request: Request):
        return conversations.request_access(
            session, review_identity(request, session), body.description
        )

    @app.get("/api/review/sessions/{session}/requests")
    def access_requests(session: str, request: Request):
        return conversations.requests(session, review_identity(request, session))

    return app
