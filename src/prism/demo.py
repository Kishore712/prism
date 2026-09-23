"""Start the loopback demo or an explicitly configured direct-TLS identity service."""

import fcntl
import os
import urllib.parse
from pathlib import Path

import uvicorn

from prism.projects import ProjectSource
from prism.reference_runtime import RuntimeRegistry
from prism.sharing import Denied, Source, Store, prepare_source
from prism.webapp import DemoAuth, create_app


def serve(
    directory,
    port,
    measurements=False,
    key_file=None,
    allow_openai=False,
    model_budget_cents=None,
    projects=(),
    runtime_profile="development",
    oidc_config=None,
    oidc_client_secret_file=None,
    bind_host="127.0.0.1",
    tls_cert_file=None,
    tls_key_file=None,
):
    if bool(key_file) != allow_openai:
        raise ValueError(
            "External inference requires both --allow-openai and an explicit --openai-key-file. No environment credentials are read."
        )
    key = None
    if key_file:
        from prism.conversation import read_key

        try:
            key = read_key(Path(key_file))
        except OSError as exc:
            raise ValueError(
                "The explicitly configured Prism key file could not be read."
            ) from exc
    identity_mode = oidc_config is not None
    if model_budget_cents is None:
        model_budget_cents = 0 if identity_mode else 100
    if type(model_budget_cents) is not int or not (
        model_budget_cents == 0 or 5 <= model_budget_cents <= 10000
    ):
        raise ValueError(
            "Set zero to disable external model calls, or an explicit allowance between 5 and 10000 cents."
        )
    if identity_mode:
        from prism.identity import OIDCAuth, OIDCConfig, read_client_secret
        from prism.identity_preflight import validate_tls_identity

        config = OIDCConfig.from_file(oidc_config)
        if not tls_cert_file or not tls_key_file:
            raise ValueError(
                "The named identity service requires an explicit TLS certificate and key."
            )
        configured_port = urllib.parse.urlsplit(config.public_origin).port or 443
        if port != configured_port:
            raise ValueError(
                "The listening port must match the canonical OIDC public origin."
            )
        validate_tls_identity(
            tls_cert_file,
            tls_key_file,
            urllib.parse.urlsplit(config.public_origin).hostname,
        )
        client_secret = (
            read_client_secret(oidc_client_secret_file)
            if oidc_client_secret_file
            else None
        )
    elif (
        any((oidc_client_secret_file, tls_cert_file, tls_key_file))
        or bind_host != "127.0.0.1"
    ):
        raise ValueError(
            "TLS and non-loopback binding belong only to the explicit OIDC identity mode."
        )
    root = Path(directory).absolute()
    try:
        project_sources = [
            ProjectSource.from_manifest(Path(item), action_profile=runtime_profile)
            for item in projects
        ]
    except Denied as exc:
        raise ValueError(exc.message) from exc
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise ValueError("Use a dedicated regular directory for synthetic demo state.")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    marker_name = "identity-service-v1" if identity_mode else "synthetic-demo-v1"
    marker_text = (
        "Prism named identity service state. Private pilot ready: false.\n"
        if identity_mode
        else "Prism synthetic local demo only. No personal data.\n"
    )
    marker = root / marker_name
    if not marker.exists():
        if any(root.iterdir()):
            raise ValueError("The demo directory must be empty on first use.")
        prepare_source(root / "source")
        marker.write_text(marker_text)
    elif marker.is_symlink() or marker.read_text() != marker_text:
        raise ValueError("Unrecognized demo directory.")
    os.chmod(root, 0o700)
    for name in ("source", "demo.sqlite", "server.lock"):
        if (root / name).is_symlink():
            raise ValueError("Demo state must not use symbolic links.")
    static = Path(__file__).parent / "static"
    if not all((static / name).is_file() for name in ("app.js", "app.css")):
        raise ValueError(
            "Build the frontend first: cd frontend && npm ci --ignore-scripts && npm run build"
        )
    with (root / "server.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(
                "This demo directory already has a running service."
            ) from exc
        store = Store(root / "demo.sqlite", measurements=measurements)
        registry = RuntimeRegistry(profile=runtime_profile)
        auth = (
            OIDCAuth(store, config, client_secret=client_secret)
            if identity_mode
            else DemoAuth()
        )
        app = create_app(
            store,
            Source(root / "source"),
            port=port,
            auth=auth,
            key=key,
            model_budget_cents=model_budget_cents,
            project_sources=project_sources,
            runtime_registry=registry,
        )
        print(
            "Local handoff demonstration with a retained synthetic fixture. Private pilot ready: false.",
            flush=True,
        )
        if key and model_budget_cents == 0:
            print(
                "External inference credential configured, but model calls are blocked because this identity-service increment has no external-call allowance.",
                flush=True,
            )
        elif key:
            print(
                f"External inference: OpenAI GPT-5.4 mini. Questions, current-session history, selected project evidence and tool results may leave this computer. Owner chat requires its own disclosure acknowledgement; collaborator context stays limited to its reviewed version. Shared application allowance: ${model_budget_cents / 100:.2f}; store=false is not zero provider retention.",
                flush=True,
            )
        if identity_mode:
            print(
                f"Named identity mode: {config.public_origin}. Invitations are recipient-bound; no local capability login is enabled.",
                flush=True,
            )
        else:
            print(
                "Keep these local capability links private. They expire when the service stops.",
                flush=True,
            )
            for actor, token in auth.tokens.items():
                page = "owner" if actor == "owner" else "review"
                print(
                    f"{actor.title()}: http://127.0.0.1:{port}/{page}#token={token}",
                    flush=True,
                )
        uvicorn.run(
            app,
            host=bind_host,
            port=port,
            access_log=False,
            log_level="warning",
            proxy_headers=False,
            timeout_keep_alive=5,
            limit_concurrency=32,
            ssl_certfile=tls_cert_file,
            ssl_keyfile=tls_key_file,
        )
