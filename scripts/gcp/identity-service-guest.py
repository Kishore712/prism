#!/usr/bin/env python3
"""Guest-side, offline preparation and bounded local checks for private Prism OIDC."""

import argparse
import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import stat
import subprocess
import sys
import unicodedata
from pathlib import Path, PurePosixPath

GOOGLE = {
    "issuer": "https://accounts.google.com",
    "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
    "token_endpoint": "https://oauth2.googleapis.com/token",
    "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
}
HOST_RE = re.compile(r"[a-z0-9-]+\.[a-z0-9-]+\.ts\.net\Z")
MODEL_KEY = "/var/lib/prism-identity/openai-model-key"
PROJECT_ROOT = Path("/var/lib/prism-identity/project")
PROJECT_MANIFEST = PROJECT_ROOT / ".prism-project.json"
PROJECT_OWNER = 0
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


def project_inventory(expected=None):
    """Hash one fixed private project, including its exact file inventory."""
    if expected is not None and not SHA256_RE.fullmatch(expected):
        raise ValueError("Invalid fixed project inventory hash.")
    for parent in reversed((PROJECT_ROOT, *PROJECT_ROOT.parents)):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, PROJECT_OWNER):
            raise ValueError("Project directory ownership or type is invalid.")
        if parent == PROJECT_ROOT:
            if info.st_uid != PROJECT_OWNER or info.st_mode & 0o077:
                raise ValueError("Project directory must be private.")
        elif info.st_mode & 0o022 and not (
            info.st_uid == 0 and info.st_mode & stat.S_ISVTX
        ):
            raise ValueError("Project ancestor is writable by another user.")

    def read_file(path, limit):
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != PROJECT_OWNER
            or info.st_mode & 0o077
            or info.st_size > limit
        ):
            raise ValueError("Project file ownership, type, mode or size is invalid.")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(fd)
            data = os.read(fd, limit + 1)
            after = os.fstat(fd)
            if (
                len(data) != before.st_size
                or len(data) > limit
                or (
                    before.st_size,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                    before.st_nlink,
                )
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_nlink)
            ):
                raise ValueError("Project file changed while being checked.")
            return data
        finally:
            os.close(fd)

    manifest_bytes = read_file(PROJECT_MANIFEST, 16 * 1024)

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate project manifest field.")
            result[key] = value
        return result

    manifest = json.loads(
        manifest_bytes.decode("utf-8"), object_pairs_hook=unique_object
    )
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schema", "id", "title", "files", "action"}
        or type(manifest["schema"]) is not int
        or manifest["schema"] != 1
        or not isinstance(manifest["id"], str)
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?", manifest["id"])
        or not isinstance(manifest["title"], str)
        or not 1 <= len(manifest["title"]) <= 120
        or manifest["title"].strip() != manifest["title"]
        or any(unicodedata.category(char).startswith("C") for char in manifest["title"])
        or manifest["action"] != "json-check"
        or not isinstance(manifest["files"], list)
        or not 1 <= len(manifest["files"]) <= 8
        or any(not isinstance(name, str) for name in manifest["files"])
        or len(set(manifest["files"])) != len(manifest["files"])
    ):
        raise ValueError("Unsupported fixed project manifest.")
    listed = set(manifest["files"])
    if not any(name.lower().endswith(".json") for name in listed):
        raise ValueError("Fixed project needs a JSON check input.")
    for name in listed:
        path = PurePosixPath(name)
        if (
            not name
            or path.is_absolute()
            or str(path) != name
            or len(name) > 240
            or len(path.parts) > 8
            or any(
                part in (".", "..")
                or any(unicodedata.category(char).startswith("C") for char in part)
                for part in path.parts
            )
            or "\\" in name
            or "\x00" in name
        ):
            raise ValueError("Invalid fixed project file name.")

    found = set()

    def walk(directory, prefix=""):
        with os.scandir(directory) as entries:
            for entry in entries:
                relative = prefix + entry.name
                info = entry.stat(follow_symlinks=False)
                if info.st_uid != PROJECT_OWNER or info.st_mode & 0o077:
                    raise ValueError(
                        "Project entry is not private and owner controlled."
                    )
                if stat.S_ISDIR(info.st_mode):
                    prior = len(found)
                    walk(Path(directory) / entry.name, relative + "/")
                    if len(found) == prior:
                        raise ValueError("Empty project directory is unsupported.")
                elif stat.S_ISREG(info.st_mode):
                    if info.st_nlink != 1:
                        raise ValueError("Linked project file.")
                    found.add(relative)
                else:
                    raise ValueError("Unsupported project entry.")

    walk(PROJECT_ROOT)
    if not listed | {".prism-project.json"} <= found or len(found) > 16:
        raise ValueError("A listed project file is missing.")
    inventory = [[".prism-project.json", hashlib.sha256(manifest_bytes).hexdigest()]]
    total = 0
    for name in sorted(found - {".prism-project.json"}):
        data = read_file(PROJECT_ROOT / name, 32 * 1024)
        if name in listed:
            data.decode("utf-8")
        total += len(data)
        inventory.append([name, hashlib.sha256(data).hexdigest()])
    if total > 128 * 1024:
        raise ValueError("Fixed project exceeds its evidence size limit.")
    actual = hashlib.sha256(
        json.dumps(inventory, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    if expected is not None and actual != expected:
        raise ValueError("Fixed project inventory changed.")
    return actual


def private_file(path):
    candidate = Path(path)
    info = candidate.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise ValueError("Expected an owner-only regular input file.")
    return candidate


def bounded_text(path, maximum):
    candidate = private_file(path)
    if candidate.stat().st_size > maximum:
        raise ValueError("Input exceeds its size limit.")
    value = candidate.read_text(encoding="utf-8").strip()
    if not value or len(value) > maximum or any(char.isspace() for char in value):
        raise ValueError("Input must be one nonempty value without whitespace.")
    return value


def model_settings(args):
    budget = args.model_budget_cents
    if type(budget) is not int or budget not in (0, 100, 1000):
        raise ValueError(
            "The private service model allowance must be 0, 100, or 1000 cents."
        )
    if budget == 0:
        return []
    descriptor = os.open(MODEL_KEY, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or not 20 <= info.st_size <= 4096
        ):
            raise ValueError("Expected a private Prism model key file.")
        value = os.read(descriptor, 4097).decode("utf-8").strip()
        if not value.startswith("sk-") or any(char.isspace() for char in value):
            raise ValueError("Invalid private Prism model key file.")
    finally:
        os.close(descriptor)
    return ["--allow-openai", "--openai-key-file", MODEL_KEY]


def render(args):
    if not HOST_RE.fullmatch(args.hostname):
        raise ValueError("Expected the exact private tailnet DNS name.")
    client_id = bounded_text(args.client_id_file, 256)
    owner_subject = None
    if args.phase == "service":
        if not args.owner_subject_file:
            raise ValueError("Service configuration requires a verified subject file.")
        owner_subject = bounded_text(args.owner_subject_file, 512)
    elif args.owner_subject_file:
        raise ValueError("Identify configuration must omit the owner subject.")
    origin = f"https://{args.hostname}:8443"
    callback = (
        "/auth/oidc/identify/callback"
        if args.phase == "identify"
        else "/auth/oidc/callback"
    )
    config = {
        **GOOGLE,
        "client_id": client_id,
        "public_origin": origin,
        "redirect_uri": origin + callback,
        "algorithms": ["RS256"],
    }
    if owner_subject is not None:
        config["owner_subject"] = owner_subject
    target = Path(args.output)
    if not target.is_absolute() or target.is_symlink():
        raise ValueError("Output must be an absolute new regular-file path.")
    parent = target.parent.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.getuid()
        or parent.st_mode & 0o077
    ):
        raise ValueError("Output parent must be an owner-only directory.")
    descriptor = os.open(
        target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(config, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    print("Prism OIDC configuration prepared; no values displayed.")


def command(args, mode):
    model_argv = model_settings(args)
    project_argv = []
    if getattr(args, "project_sha256", None) is not None:
        project_inventory(args.project_sha256)
        project_argv = ["--project", str(PROJECT_MANIFEST)]
    if args.port != 8443 or not HOST_RE.fullmatch(args.hostname):
        raise ValueError("Expected the selected private HTTPS origin on port 8443.")
    try:
        address = ipaddress.ip_address(args.bind_host)
    except ValueError as exc:
        raise ValueError(
            "Bind host must be one explicit tailnet IPv4 address."
        ) from exc
    if address.version != 4 or address not in ipaddress.ip_network("100.64.0.0/10"):
        raise ValueError("Bind host must be one explicit tailnet IPv4 address.")
    release = Path(args.release)
    if not release.is_absolute() or release.is_symlink() or not release.is_dir():
        raise ValueError("Expected an absolute installed release directory.")
    python = release / "venv/bin/python"
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError("Installed release Python is unavailable.")
    config = private_file(args.oidc_config)
    if config.stat().st_size > 16 * 1024:
        raise ValueError("OIDC configuration exceeds its size limit.")
    value = json.loads(config.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or any(value.get(key) != expected for key, expected in GOOGLE.items())
        or value.get("algorithms") != ["RS256"]
        or value.get("public_origin") != f"https://{args.hostname}:8443"
        or value.get("redirect_uri") != value["public_origin"] + "/auth/oidc/callback"
        or not value.get("owner_subject")
        or set(value)
        != set(GOOGLE)
        | {"algorithms", "client_id", "public_origin", "redirect_uri", "owner_subject"}
    ):
        raise ValueError(
            "Service OIDC configuration does not match the selected origin."
        )
    argv = [
        str(python),
        "-m",
        "prism",
        "identity-preflight" if mode == "preflight" else "identity-service",
        "--bind-host",
        args.bind_host,
        "--port",
        "8443",
        "--oidc-config",
        str(config),
        "--oidc-client-secret-file",
        str(args.client_secret_file),
        "--tls-cert-file",
        str(args.tls_cert_file),
        "--tls-key-file",
        str(args.tls_key_file),
        "--runtime-profile",
        "reference-linux",
        *project_argv,
    ]
    if mode == "preflight":
        argv.append("--json")
    else:
        argv += [
            "--data-dir",
            str(args.data_dir),
            "--model-budget-cents",
            str(args.model_budget_cents),
            *model_argv,
        ]
    clean_env = {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "PYTHONPATH": str(release / "src"),
        "PYTHONUNBUFFERED": "1",
    }
    if mode == "preflight":
        completed = subprocess.run(
            argv,
            env=clean_env,
            cwd=release,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if completed.returncode:
            raise ValueError(
                "Prism identity preflight failed; inspect guest-local diagnostics."
            )
        report = json.loads(completed.stdout)
        if (
            report.get("local_inputs_valid") is not True
            or report.get("pilot_ready") is not False
        ):
            raise ValueError("Prism identity preflight returned an unexpected result.")
        print("Prism identity preflight passed locally; pilot readiness remains false.")
    else:
        data_dir = Path(args.data_dir)
        if (
            not data_dir.is_absolute()
            or data_dir.is_symlink()
            or data_dir.name == ".prism-demo"
        ):
            raise ValueError("Use a dedicated absolute identity state directory.")
        # The application logs its canonical origin at startup. Keep that private.
        with open(os.devnull, "w", encoding="utf-8") as sink:
            os.dup2(sink.fileno(), 1)
        os.execve(str(python), argv, clean_env)


def selfcheck(args):
    if not HOST_RE.fullmatch(args.hostname):
        raise ValueError("Expected the selected private tailnet DNS name.")
    address = ipaddress.ip_address(args.bind_host)
    if address.version != 4 or address not in ipaddress.ip_network("100.64.0.0/10"):
        raise ValueError("Expected one tailnet IPv4 address.")
    os.environ.pop("SSL_CERT_FILE", None)
    os.environ.pop("SSL_CERT_DIR", None)
    context = ssl.create_default_context()
    with (
        socket.create_connection((args.bind_host, 8443), timeout=4) as raw,
        context.wrap_socket(raw, server_hostname=args.hostname) as secured,
    ):
        secured.settimeout(4)
        secured.sendall(
            (
                "GET /api/auth/mode HTTP/1.1\r\nHost: "
                + args.hostname
                + ":8443\r\nConnection: close\r\n\r\n"
            ).encode("ascii")
        )
        response = http.client.HTTPResponse(secured)
        response.begin()
        if (
            response.status != 200
            or int(response.getheader("Content-Length", "0")) > 1024
        ):
            raise ValueError(
                "The local identity endpoint returned an unexpected response."
            )
        payload = json.loads(response.read(1025))
        if payload != {"identity_mode": True, "owner_login": "/auth/oidc/owner"}:
            raise ValueError("The local endpoint is not the named identity service.")
    print("Private HTTPS identity endpoint passed local TLS and mode checks.")


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="action", required=True)
    render_parser = commands.add_parser("render")
    render_parser.add_argument(
        "--phase", choices=("identify", "service"), required=True
    )
    render_parser.add_argument("--hostname", required=True)
    render_parser.add_argument("--client-id-file", required=True)
    render_parser.add_argument("--owner-subject-file")
    render_parser.add_argument("--output", required=True)
    for name in ("preflight", "serve"):
        item = commands.add_parser(name)
        item.add_argument("--hostname", required=True)
        item.add_argument("--bind-host", required=True)
        item.add_argument("--port", type=int, default=8443)
        item.add_argument("--release", required=True)
        item.add_argument("--oidc-config", required=True)
        item.add_argument("--client-secret-file", required=True)
        item.add_argument("--tls-cert-file", required=True)
        item.add_argument("--tls-key-file", required=True)
        item.add_argument("--model-budget-cents", type=int, default=0)
        item.add_argument("--project-sha256")
        if name == "serve":
            item.add_argument("--data-dir", required=True)
    inventory = commands.add_parser("project-inventory")
    inventory.add_argument("--sha256")
    check = commands.add_parser("selfcheck")
    check.add_argument("--hostname", required=True)
    check.add_argument("--bind-host", required=True)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.action == "render":
            render(args)
        elif args.action == "project-inventory":
            print(project_inventory(args.sha256))
        elif args.action == "selfcheck":
            selfcheck(args)
        else:
            command(args, args.action)
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        subprocess.TimeoutExpired,
    ) as exc:
        print(f"Prism guest operation failed: {type(exc).__name__}.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
