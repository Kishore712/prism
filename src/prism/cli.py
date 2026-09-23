"""CLI for owner-operated host diagnostics and synthetic selftests."""

import argparse
import json
import sys

from prism import __version__
from prism.doctor import diagnose
from prism.engine import IMAGE, Engine, EngineError
from prism.selftest import run_selftest


def parser():
    root = argparse.ArgumentParser(
        prog="prism",
        description="Prism: host validation and an explicit synthetic local demonstration.",
    )
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    for command, help_text in [
        ("doctor", "Read-only host diagnostics"),
        ("selftest", "Run bounded synthetic probes in a local development container"),
    ]:
        child = commands.add_parser(command, help=help_text)
        child.add_argument(
            "--profile", choices=["development", "pilot"], default="development"
        )
        child.add_argument(
            "--socket",
            help="Explicit absolute local Unix socket; remote endpoints are rejected",
        )
        child.add_argument(
            "--json",
            action="store_true",
            help="Machine-readable output (progress uses stderr)",
        )
    runtime = commands.add_parser(
        "runtime", help="Explicit preparation of the fixed synthetic image"
    )
    operations = runtime.add_subparsers(dest="operation", required=True)
    prepare = operations.add_parser(
        "prepare", help="Download only the pinned public Python fixture image"
    )
    prepare.add_argument("--socket")
    prepare.add_argument("--json", action="store_true")
    demo = commands.add_parser(
        "demo", help="Start the synthetic-only loopback sharing demo"
    )
    demo.add_argument(
        "--data-dir",
        default=".prism-demo",
        help="Dedicated local synthetic state directory",
    )
    demo.add_argument("--port", type=int, default=8765)
    demo.add_argument(
        "--measurements",
        action="store_true",
        help="Enable optional local numeric research measurements",
    )
    demo.add_argument(
        "--allow-openai",
        action="store_true",
        help="Explicitly allow the documented external inference route",
    )
    demo.add_argument(
        "--model-budget-cents",
        type=int,
        default=100,
        help="Total local model reservation allowance in US cents (default: 100); changing it never resets prior reservations",
    )
    demo.add_argument(
        "--openai-key-file",
        help="Explicit owner-only Prism API credential file; no environment-key lookup",
    )
    demo.add_argument(
        "--project",
        action="append",
        default=[],
        help="Absolute path to a supported .prism-project.json manifest; repeatable",
    )
    demo.add_argument(
        "--runtime-profile",
        choices=["development", "reference-linux"],
        default="development",
        help="Trusted local runtime profile for configured project actions",
    )
    identity = commands.add_parser(
        "identity-service",
        help="Start the explicitly configured direct-TLS named identity service",
    )
    identity.add_argument("--data-dir", default=".prism-identity")
    identity.add_argument("--bind-host", required=True)
    identity.add_argument("--port", type=int, required=True)
    identity.add_argument("--oidc-config", required=True)
    identity.add_argument("--oidc-client-secret-file")
    identity.add_argument(
        "--allow-openai",
        action="store_true",
        help="Explicitly allow the configured external inference route",
    )
    identity.add_argument(
        "--model-budget-cents",
        type=int,
        default=0,
        help="Persistent aggregate model reservation ceiling in cents (default: 0); changing it never resets or adds to prior reservations",
    )
    identity.add_argument("--openai-key-file")
    identity.add_argument("--tls-cert-file", required=True)
    identity.add_argument("--tls-key-file", required=True)
    identity.add_argument("--project", action="append", default=[])
    identity.add_argument(
        "--runtime-profile",
        choices=["development", "reference-linux"],
        default="development",
    )
    preflight = commands.add_parser(
        "identity-preflight",
        help="Validate named identity-service configuration without starting it",
    )
    preflight.add_argument("--bind-host", required=True)
    preflight.add_argument("--port", type=int, required=True)
    preflight.add_argument("--oidc-config", required=True)
    preflight.add_argument("--oidc-client-secret-file")
    preflight.add_argument("--tls-cert-file", required=True)
    preflight.add_argument("--tls-key-file", required=True)
    preflight.add_argument("--project", action="append", default=[])
    preflight.add_argument(
        "--runtime-profile",
        choices=["development", "reference-linux"],
        default="development",
    )
    preflight.add_argument(
        "--probe-idp-tls",
        action="store_true",
        help="Explicitly perform TLS handshakes to fixed configured IdP hosts; no HTTP requests are sent",
    )
    preflight.add_argument("--json", action="store_true")
    bootstrap = commands.add_parser(
        "identity-bootstrap",
        help="Run one bounded OIDC flow to display the operator's verified issuer and subject",
    )
    bootstrap.add_argument("--bind-host", default="127.0.0.1")
    bootstrap.add_argument("--port", type=int, required=True)
    bootstrap.add_argument("--oidc-config", required=True)
    bootstrap.add_argument("--oidc-client-secret-file")
    bootstrap.add_argument("--tls-cert-file", required=True)
    bootstrap.add_argument("--tls-key-file", required=True)
    return root


def display(report, as_json):
    if as_json:
        print(json.dumps(report, indent=2))
        return
    print(
        f"Prism {report.get('kind', 'runtime_preparation')} [{report.get('profile', 'development')}]"
    )
    for check in report.get("checks", []):
        detail = check.get("detail", "")
        print(f"  {check['status'].upper():10} {check['id']}: {detail}".rstrip())
        if check.get("remedy"):
            print(f"             Next: {check['remedy']}")
    if "image_id" in report:
        print(f"  Image: {report['image_id']}")
    if "passed" in report:
        print(f"Synthetic selftest: {'PASS' if report['passed'] else 'FAIL'}")
    if "prerequisites_ready" in report:
        print(f"Development prerequisites ready: {report['prerequisites_ready']}")
    print("Private pilot ready: false")
    print(
        report.get(
            "notice",
            "Only the synthetic fixture image was prepared; no job was started.",
        )
    )


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "identity-bootstrap":
            if not 1024 <= args.port <= 65535:
                raise ValueError("Use an unprivileged port between 1024 and 65535.")
            from prism.identity_bootstrap import run_identity_bootstrap

            run_identity_bootstrap(
                oidc_config=args.oidc_config,
                bind_host=args.bind_host,
                port=args.port,
                tls_cert_file=args.tls_cert_file,
                tls_key_file=args.tls_key_file,
                oidc_client_secret_file=args.oidc_client_secret_file,
            )
            return 0
        if args.command == "identity-preflight":
            if not 1024 <= args.port <= 65535:
                raise ValueError("Use an unprivileged port between 1024 and 65535.")
            from prism.identity_preflight import identity_preflight

            report = identity_preflight(
                oidc_config=args.oidc_config,
                bind_host=args.bind_host,
                port=args.port,
                tls_cert_file=args.tls_cert_file,
                tls_key_file=args.tls_key_file,
                oidc_client_secret_file=args.oidc_client_secret_file,
                projects=args.project,
                runtime_profile=args.runtime_profile,
                network=args.probe_idp_tls,
            )
            display(report, args.json)
            return 0
        if args.command in ("demo", "identity-service"):
            if not 1024 <= args.port <= 65535:
                raise ValueError("Use an unprivileged port between 1024 and 65535.")
            from prism.demo import serve

            if args.command == "demo":
                serve(
                    args.data_dir,
                    args.port,
                    args.measurements,
                    args.openai_key_file,
                    args.allow_openai,
                    args.model_budget_cents,
                    args.project,
                    args.runtime_profile,
                )
            else:
                serve(
                    args.data_dir,
                    args.port,
                    key_file=args.openai_key_file,
                    allow_openai=args.allow_openai,
                    model_budget_cents=args.model_budget_cents,
                    projects=args.project,
                    runtime_profile=args.runtime_profile,
                    oidc_config=args.oidc_config,
                    oidc_client_secret_file=args.oidc_client_secret_file,
                    bind_host=args.bind_host,
                    tls_cert_file=args.tls_cert_file,
                    tls_key_file=args.tls_key_file,
                )
            return 0
        engine = Engine(args.socket)
        if args.command == "doctor":
            report = diagnose(engine, args.profile)
            code = 0 if report["prerequisites_ready"] else 2
        elif args.command == "runtime":
            if not args.json:
                print(
                    f"Preparing pinned public fixture image: {IMAGE}",
                    file=sys.stderr,
                    flush=True,
                )
            report = {
                "kind": "runtime_preparation",
                "pilot_ready": False,
                **engine.prepare(),
            }
            code = 0
        elif args.profile == "pilot":
            report = {
                "kind": "runtime_selftest",
                "profile": "pilot",
                "passed": False,
                "pilot_ready": False,
                "checks": [],
                "notice": "Pilot runtime is not implemented. No container was started; no development fallback is allowed.",
            }
            code = 2
        else:
            report = run_selftest(
                engine, lambda line: print(line, file=sys.stderr, flush=True)
            )
            code = 0 if report["passed"] else 1
        display(report, args.json)
        return code
    except (EngineError, ValueError) as exc:
        error = {"error": str(exc), "pilot_ready": False}
        print(
            json.dumps(error) if getattr(args, "json", False) else f"Prism: {exc}",
            file=sys.stdout if getattr(args, "json", False) else sys.stderr,
        )
        return 2
    except KeyboardInterrupt:
        print(
            "Interrupted. Check the reported cleanup outcome before retrying.",
            file=sys.stderr,
        )
        return 130
