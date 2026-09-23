#!/usr/bin/env python3
"""Render and enforce the private guest's one-service Tailscale lifecycle."""

import argparse
import hashlib
import ipaddress
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from urllib.parse import urlsplit

BASE = Path("/var/lib/prism/identity-pilot")
INSTALLED = BASE / "oneboot/installed.json"
UPDATE_ROOT = BASE / "service-updates"
UNIT = Path("/etc/systemd/system/prism-identity-service.service")
OWNER_UID = 0
HELPER = "/usr/local/libexec/prism-identity-service-guest.py"
LIFECYCLE = "/usr/local/libexec/prism-identity-service-lifecycle.py"
CONFIG = "/var/lib/prism-identity/oidc-service-config.json"
CLIENT_SECRET = "/var/lib/prism-identity/oidc-client-secret"
CERT = "/var/lib/prism-identity/tls/cert.pem"
KEY = "/var/lib/prism-identity/tls/key.pem"
MODEL_KEY = "/var/lib/prism-identity/openai-model-key"
DATA = "/var/lib/prism-identity/service-state"
PROJECT_MANIFEST = "/var/lib/prism-identity/project/.prism-project.json"
PROJECT_INVENTORY_SHA256 = (
    "737348aaef852355c5a16381fafc96f0c6df0931edafddc93a77906d064db525"
)
RESTORE = "/usr/local/libexec/prism-identify-tailnet-restore"
HOST_RE = re.compile(r"[a-z0-9-]+\.[a-z0-9-]+\.ts\.net\Z")
TIMER_RE = re.compile(r"prism-identify-restore-[a-f0-9]{32}\.timer\Z")
RUN_RE = re.compile(r"[a-f0-9]{32}\Z")
BUNDLE_RE = re.compile(r"[a-f0-9]{64}\Z")
PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ENV = {"PATH": PATH, "LC_ALL": "C"}
SOURCE_FILES = frozenset(
    (
        "README.md",
        "pyproject.toml",
        "src/prism/__init__.py",
        "src/prism/__main__.py",
        "src/prism/cli.py",
        "src/prism/conversation.py",
        "src/prism/demo.py",
        "src/prism/doctor.py",
        "src/prism/engine.py",
        "src/prism/fixtures/__init__.py",
        "src/prism/fixtures/json_check.py",
        "src/prism/fixtures/probe.py",
        "src/prism/handoff.py",
        "src/prism/identity.py",
        "src/prism/identity_bootstrap.py",
        "src/prism/identity_preflight.py",
        "src/prism/jobs.py",
        "src/prism/owner.py",
        "src/prism/projects.py",
        "src/prism/reference_runtime.py",
        "src/prism/runtime.py",
        "src/prism/selftest.py",
        "src/prism/sharing.py",
        "src/prism/static/app.css",
        "src/prism/static/app.js",
        "src/prism/static/index.html",
        "src/prism/webapp.py",
        "src/prism/worker.py",
        "uv.lock",
    )
)


def checked_directory(path):
    """Reject symlinks in every ancestor and require private release parents."""
    path = Path(path)
    for directory in reversed((path, *path.parents)):
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("An installation parent is not a regular directory.")
        if (
            directory == BASE.parent or directory == BASE or BASE in directory.parents
        ) and (info.st_uid != OWNER_UID or info.st_mode & 0o022):
            raise ValueError("An installation parent is not root controlled.")


def run(*argv, timeout=15):
    return subprocess.run(
        argv, env=ENV, capture_output=True, text=True, timeout=timeout, check=True
    )


def bind_ip(value):
    address = ipaddress.ip_address(value)
    if address.version != 4 or address not in ipaddress.ip_network("100.64.0.0/10"):
        raise ValueError("Expected one fixed tailnet IPv4 address.")
    return str(address)


def fixed_host():
    path = Path(CONFIG)
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != OWNER_UID
        or info.st_mode & 0o077
        or info.st_size > 16384
    ):
        raise ValueError("Expected the owner-only service configuration.")
    value = json.loads(path.read_text(encoding="utf-8"))
    origin = value.get("public_origin")
    if not isinstance(origin, str):
        raise TypeError("Expected the fixed private service origin.")
    parsed = urlsplit(origin)
    host = parsed.hostname
    if (
        not host
        or not HOST_RE.fullmatch(host)
        or origin != f"https://{host}:8443"
        or value.get("redirect_uri") != origin + "/auth/oidc/callback"
        or not value.get("owner_subject")
    ):
        raise ValueError("Expected the fixed private service origin.")
    return host


def installation(record, expected_bundle=None, expected_run=None):
    marker = Path(record)
    update = marker != INSTALLED
    if update and (
        marker.name != "installed.json"
        or marker.parent.parent != UPDATE_ROOT
        or not BUNDLE_RE.fullmatch(marker.parent.name)
    ):
        raise ValueError("Expected a fixed service update marker.")
    checked_directory(marker.parent)
    directory = marker.parent.lstat()
    if (
        not stat.S_ISDIR(directory.st_mode)
        or directory.st_uid != OWNER_UID
        or directory.st_mode & 0o077
    ):
        raise ValueError("Installation directory is not private.")

    def read_json(path, maximum):
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != OWNER_UID
            or info.st_nlink != 1
            or info.st_mode & 0o077
            or info.st_size > maximum
        ):
            raise ValueError("Installation record is not private or bounded.")
        return json.loads(path.read_text(encoding="utf-8"))

    installed = read_json(marker, 4096)
    if not isinstance(installed, dict):
        raise TypeError("Invalid installed marker.")
    bundle = installed.get("source_tar_sha256" if update else "bundle_sha256")
    run_id = installed.get("base_run_id" if update else "run_id")
    marker_keys = (
        {
            "source_tar_sha256",
            "transfer_tar_sha256",
            "base_bundle_sha256",
            "base_run_id",
        }
        if update
        else {"run_id", "bundle_sha256"}
    )
    if (
        set(installed) != marker_keys
        or not isinstance(run_id, str)
        or not RUN_RE.fullmatch(run_id)
        or not isinstance(bundle, str)
        or not BUNDLE_RE.fullmatch(bundle)
        or (expected_bundle is not None and bundle != expected_bundle)
        or (expected_run is not None and run_id != expected_run)
    ):
        raise ValueError("Installed identity differs from the fixed unit.")
    manifest = read_json(marker.parent / "manifest.json", 16384)
    if not isinstance(manifest, dict):
        raise TypeError("Invalid installation manifest.")
    if not update:
        ledger = read_json(marker.parent / "ledger.json", 4096)
        if (
            not isinstance(ledger, dict)
            or set(ledger) != {"run_id", "next_seq", "complete"}
            or ledger.get("run_id") != run_id
            or ledger.get("next_seq") != 6
            or ledger.get("complete") is not True
        ):
            raise ValueError(
                "Installation ledger is incomplete or differs from the marker."
            )
    hashes = manifest.get("file_sha256")
    if not isinstance(hashes, dict) or set(hashes) != SOURCE_FILES:
        raise ValueError("Installation manifest differs from the marker.")
    if update:
        transfer = installed.get("transfer_tar_sha256")
        kind = manifest.get("kind")
        patch_names = {
            "prism_service_update_v1": {"src/prism/webapp.py"},
            "prism_service_update_v2": {
                "src/prism/webapp.py",
                "src/prism/static/app.js",
            },
            "prism_service_update_v3": {
                "src/prism/identity.py",
                "src/prism/webapp.py",
                "src/prism/static/app.js",
            },
        }.get(kind)
        expected_manifest_keys = {
            "kind",
            "base_bundle_sha256",
            "base_run_id",
            "transfer_tar_sha256",
            "source_tar_sha256",
            "webapp_sha256",
            "file_sha256",
            "release_name",
        }
        if (
            patch_names is None
            or set(manifest)
            != expected_manifest_keys
            | (
                {"app_js_sha256"}
                if kind in ("prism_service_update_v2", "prism_service_update_v3")
                else set()
            )
            | ({"identity_sha256"} if kind == "prism_service_update_v3" else set())
            or not isinstance(transfer, str)
            or not BUNDLE_RE.fullmatch(transfer)
            or marker.parent.name != transfer
            or manifest.get("source_tar_sha256") != bundle
            or manifest.get("transfer_tar_sha256") != transfer
            or manifest.get("base_run_id") != run_id
            or manifest.get("base_bundle_sha256") != installed.get("base_bundle_sha256")
            or manifest.get("release_name") != "service-" + transfer
            or manifest.get("webapp_sha256") != hashes["src/prism/webapp.py"]
            or (
                kind in ("prism_service_update_v2", "prism_service_update_v3")
                and manifest.get("app_js_sha256") != hashes["src/prism/static/app.js"]
            )
            or (
                kind == "prism_service_update_v3"
                and manifest.get("identity_sha256") != hashes["src/prism/identity.py"]
            )
        ):
            raise ValueError("Service update manifest differs from the marker.")
        _, old_bundle, old_run = installation(INSTALLED)
        if old_bundle != installed["base_bundle_sha256"] or old_run != run_id:
            raise ValueError("Service update baseline changed.")
        old_manifest = read_json(INSTALLED.parent / "manifest.json", 16384)
        if any(
            hashes[name] != old_manifest["file_sha256"][name]
            for name in SOURCE_FILES - patch_names
        ):
            raise ValueError("Service update changed another source.")
        archive = marker.parent / "source.tar"
        info = archive.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != OWNER_UID
            or info.st_nlink != 1
            or info.st_mode & 0o077
            or info.st_size > 64 * 1024 * 1024
            or hashlib.sha256(archive.read_bytes()).hexdigest() != bundle
        ):
            raise ValueError("Service source tar hash mismatch.")
        with tarfile.open(archive, "r:") as tar:
            members = tar.getmembers()
            if len(members) != 29 or {item.name for item in members} != SOURCE_FILES:
                raise ValueError("Service source tar inventory mismatch.")
            for item in members:
                stream = tar.extractfile(item) if item.isfile() else None
                if (
                    stream is None
                    or hashlib.sha256(stream.read()).hexdigest() != hashes[item.name]
                ):
                    raise ValueError("Service source tar content mismatch.")
        release = BASE / "app-releases" / ("service-" + transfer)
    else:
        if (
            manifest.get("kind") != "prism_oneboot_app_v1"
            or manifest.get("run_id") != run_id
            or manifest.get("bundle_sha256") != bundle
            or manifest.get("raw_sha256") != bundle
        ):
            raise ValueError("Installation manifest differs from the marker.")
        release = BASE / "app-releases" / f"{bundle}-{run_id}"
    checked_directory(release)
    info = release.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != OWNER_UID
        or info.st_mode & 0o077
    ):
        raise ValueError("Installed release directory is unavailable.")
    for name in sorted(SOURCE_FILES):
        expected = hashes[name]
        if (
            not isinstance(name, str)
            or not isinstance(expected, str)
            or not BUNDLE_RE.fullmatch(expected)
        ):
            raise ValueError("Invalid source hash entry.")
        relative = Path(name)
        if relative.is_absolute() or any(
            part in (".", "..") for part in relative.parts
        ):
            raise ValueError("Invalid installed source path.")
        source = release / relative
        checked_directory(source.parent)
        info = source.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != OWNER_UID
            or info.st_nlink != 1
            or info.st_mode & 0o077
            or info.st_size > 16 * 1024 * 1024
        ):
            raise ValueError("Installed source hash mismatch.")
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise ValueError("Installed source hash mismatch.")
    return str(release), bundle, run_id


def model_budget(value):
    if type(value) is not int or value not in (0, 100, 1000):
        raise ValueError(
            "The private service model allowance must be 0, 100, or 1000 cents."
        )
    if value:
        info = Path(MODEL_KEY).lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != OWNER_UID
            or info.st_nlink != 1
            or info.st_mode & 0o077
            or not 20 <= info.st_size <= 4096
        ):
            raise ValueError("Expected a private Prism model key file.")
    return value


def project_inventory(expected=None):
    argv = ["/usr/bin/python3", HELPER, "project-inventory"]
    if expected is not None:
        if not BUNDLE_RE.fullmatch(expected):
            raise ValueError("Invalid fixed project inventory hash.")
        argv += ["--sha256", expected]
    value = run(*argv).stdout.strip()
    if not BUNDLE_RE.fullmatch(value) or (expected is not None and value != expected):
        raise ValueError("Fixed project inventory is unavailable or changed.")
    return value


def unit_text(host, ip, record, release, bundle, run_id, budget=0, project_sha256=None):
    if project_sha256 is not None and not BUNDLE_RE.fullmatch(project_sha256):
        raise ValueError("Invalid fixed project inventory hash.")
    common = f"--hostname {host} --bind-host {ip}"
    project = f" --project-sha256 {project_sha256}" if project_sha256 else ""
    pinned = (
        f"{common} --installed-record {record} --bundle-sha256 {bundle} "
        f"--run-id {run_id}{project}"
    )
    prism = (
        f"{common} --release {release} --oidc-config {CONFIG} "
        f"--client-secret-file {CLIENT_SECRET} --tls-cert-file {CERT} "
        f"--tls-key-file {KEY}"
    )
    model = f" --model-budget-cents {budget}"
    return f"""[Unit]
Description=Prism private named identity service
Requires=tailscaled.service prism-identify-boot-restore.service
BindsTo=tailscaled.service
After=tailscaled.service prism-identify-boot-restore.service

[Service]
Type=exec
User=root
UMask=0077
Restart=no
TimeoutStartSec=360
TimeoutStopSec=240
StandardOutput=null
StandardError=journal
ExecStartPre=/usr/bin/python3 {LIFECYCLE} prepare {pinned}
ExecStartPre=/usr/bin/python3 {HELPER} preflight {prism}{model}{project}
ExecStart=/usr/bin/python3 {LIFECYCLE} serve {pinned}{model}
ExecStartPost=/usr/bin/python3 {LIFECYCLE} open {pinned}
ExecStopPost=/usr/bin/python3 {LIFECYCLE} close {common}
"""


def render(args):
    if os.geteuid() != 0:
        raise ValueError("Guest service rendering requires root.")
    host = fixed_host()
    ip = bind_ip(args.bind_host)
    selected = Path(args.installed_record) if args.installed_record else INSTALLED
    release, bundle, run_id = installation(selected)
    budget = model_budget(args.model_budget_cents)
    project_sha256 = None
    if args.enable_project:
        project_sha256 = project_inventory(PROJECT_INVENTORY_SHA256)
        if project_sha256 != PROJECT_INVENTORY_SHA256:
            raise ValueError("The installed project differs from the reviewed fixture.")
    target = Path(args.output)
    if target != UNIT or target.is_symlink():
        raise ValueError("Expected the fixed new service unit path.")
    descriptor = os.open(
        target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(
                unit_text(
                    host, ip, selected, release, bundle, run_id, budget, project_sha256
                )
            )
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    print(
        "Private service unit rendered; it has no Install section and is not enabled."
    )


def guard_ready():
    if not Path(RESTORE).is_file():
        raise ValueError("The established tailnet restore guard is missing.")
    run("systemctl", "is-active", "--quiet", "tailscaled.service")
    run("systemctl", "is-active", "--quiet", "prism-identify-boot-restore.service")
    unit = run("systemctl", "cat", "tailscaled.service").stdout
    if f"ExecStartPost={RESTORE}" not in unit:
        raise ValueError("The tailscaled restart guard is missing.")


def old_timer_absent():
    listed = run(
        "systemctl",
        "list-units",
        "--all",
        "--type=timer",
        "--plain",
        "--no-legend",
        "prism-identify-restore-*.timer",
    ).stdout
    for line in listed.splitlines():
        unit = line.split(maxsplit=1)[0]
        if (
            TIMER_RE.fullmatch(unit)
            and subprocess.run(
                ("systemctl", "is-active", "--quiet", unit),
                env=ENV,
                timeout=10,
                check=False,
            ).returncode
            == 0
        ):
            raise ValueError("A prior identify restore timer is still active.")


def tailnet_state(host, ip, shield):
    status = json.loads(run("tailscale", "status", "--json").stdout)
    prefs = json.loads(run("tailscale", "debug", "prefs").stdout)
    own = status.get("Self") or {}
    addresses = own.get("TailscaleIPs") or []
    if (
        status.get("BackendState") != "Running"
        or own.get("Online") is not True
        or own.get("DNSName", "").lower().rstrip(".") != host
        or addresses.count(ip) != 1
        or own.get("Tags") != ["tag:prism-host"]
        or prefs.get("ShieldsUp") is not shield
        or prefs.get("RunSSH") is not False
        or prefs.get("RouteAll") is not False
        or prefs.get("AdvertiseRoutes")
        or prefs.get("ExitNodeID")
        or prefs.get("ExitNodeIP")
    ):
        raise ValueError("The fixed private tailnet state is unavailable.")


def emergency_block():
    # Do not synchronously stop a BindsTo dependency from ExecStopPost: systemd
    # orders the Prism stop before tailscaled, which could deadlock that wait.
    for argv in (
        (
            "systemctl",
            "kill",
            "--kill-who=main",
            "--signal=SIGKILL",
            "tailscaled.service",
        ),
        ("systemctl", "stop", "--no-block", "tailscaled.service"),
    ):
        try:
            run(*argv, timeout=5)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
    for _ in range(10):
        try:
            prefs = json.loads(run("tailscale", "debug", "prefs", timeout=3).stdout)
            if prefs.get("ShieldsUp") is True:
                return
        except (
            OSError,
            ValueError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
        ):
            pass
        try:
            pid = run(
                "systemctl",
                "show",
                "--value",
                "--property=MainPID",
                "tailscaled.service",
                timeout=3,
            ).stdout.strip()
            if pid == "0":
                return
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
        time.sleep(0.2)
    raise ValueError("Tailnet inbound could not be independently blocked.")


def close(host, ip):
    try:
        run(RESTORE, timeout=40)
        tailnet_state(host, ip, True)
        return
    except (
        OSError,
        ValueError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ):
        pass
    try:
        run("tailscale", "set", "--shields-up=true", timeout=20)
        tailnet_state(host, ip, True)
        return
    except (
        OSError,
        ValueError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ):
        emergency_block()


def prepare(host, ip, record, bundle, run_id, project_sha256=None):
    close(host, ip)
    guard_ready()
    old_timer_absent()
    installation(record, bundle, run_id)
    if project_sha256 is not None:
        project_inventory(project_sha256)
    print("Private service guard and closed tailnet state passed.")


def open_service(host, ip, record, bundle, run_id, project_sha256=None):
    try:
        installation(record, bundle, run_id)
        if project_sha256 is not None:
            project_inventory(project_sha256)
        guard_ready()
        old_timer_absent()
        tailnet_state(host, ip, True)
        for attempt in range(10):
            try:
                run(
                    "/usr/bin/python3",
                    HELPER,
                    "selfcheck",
                    "--hostname",
                    host,
                    "--bind-host",
                    ip,
                    timeout=8,
                )
                break
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                if attempt == 9:
                    raise
                time.sleep(1)
        old_timer_absent()
        tailnet_state(host, ip, True)
        run("tailscale", "set", "--shields-up=false", timeout=20)
        tailnet_state(host, ip, False)
        run(
            "/usr/bin/python3",
            HELPER,
            "selfcheck",
            "--hostname",
            host,
            "--bind-host",
            ip,
            timeout=8,
        )
    except BaseException:
        close(host, ip)
        raise
    print("Private service opened after local HTTPS identity check.")


def serve(host, ip, record, bundle, run_id, budget=0, project_sha256=None):
    model_budget(budget)
    release, _, _ = installation(record, bundle, run_id)
    if project_sha256 is not None:
        project_inventory(project_sha256)
    guard_ready()
    old_timer_absent()
    tailnet_state(host, ip, True)
    argv = [
        "/usr/bin/python3",
        HELPER,
        "serve",
        "--hostname",
        host,
        "--bind-host",
        ip,
        "--release",
        release,
        "--oidc-config",
        CONFIG,
        "--client-secret-file",
        CLIENT_SECRET,
        "--tls-cert-file",
        CERT,
        "--tls-key-file",
        KEY,
        "--data-dir",
        DATA,
        "--model-budget-cents",
        str(budget),
    ]
    if project_sha256 is not None:
        argv += ["--project-sha256", project_sha256]
    os.execve("/usr/bin/python3", argv, ENV)


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    actions = root.add_subparsers(dest="action", required=True)
    for name in ("render", "prepare", "serve", "open", "close"):
        item = actions.add_parser(name)
        item.add_argument("--bind-host", required=True)
        if name == "render":
            item.add_argument("--output", required=True)
            item.add_argument("--installed-record")
            item.add_argument("--model-budget-cents", type=int, default=0)
            item.add_argument("--enable-project", action="store_true")
        elif name != "close":
            item.add_argument("--installed-record", required=True)
            item.add_argument("--bundle-sha256", required=True)
            item.add_argument("--run-id", required=True)
            item.add_argument("--hostname", required=True)
            item.add_argument("--project-sha256")
            if name == "serve":
                item.add_argument("--model-budget-cents", type=int, default=0)
        else:
            item.add_argument("--hostname", required=True)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.action == "render":
            render(args)
        else:
            if os.geteuid() != 0:
                raise ValueError("Guest service control requires root.")
            ip = bind_ip(args.bind_host)
            if not HOST_RE.fullmatch(args.hostname):
                raise ValueError("Invalid fixed private host.")
            if args.action == "close":
                close(args.hostname, ip)
            else:
                host = fixed_host()
                if args.hostname != host:
                    raise ValueError(
                        "Unit host differs from the fixed service configuration."
                    )
                if args.action == "prepare":
                    prepare(
                        host,
                        ip,
                        args.installed_record,
                        args.bundle_sha256,
                        args.run_id,
                        args.project_sha256,
                    )
                elif args.action == "serve":
                    serve(
                        host,
                        ip,
                        args.installed_record,
                        args.bundle_sha256,
                        args.run_id,
                        args.model_budget_cents,
                        args.project_sha256,
                    )
                else:
                    open_service(
                        host,
                        ip,
                        args.installed_record,
                        args.bundle_sha256,
                        args.run_id,
                        args.project_sha256,
                    )
    except (
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        tarfile.TarError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        print(f"Prism service lifecycle failed: {type(exc).__name__}.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
