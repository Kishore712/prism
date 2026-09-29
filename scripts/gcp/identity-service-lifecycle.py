#!/usr/bin/env python3
"""Render and enforce the private guest's one-service Tailscale lifecycle."""

import argparse
import hashlib
import importlib.util
import ipaddress
import json
import os
import re
import shlex
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
WATCHDOG_UNIT = Path("/etc/systemd/system/prism-identity-host-watchdog.service")
RECOVERY_UNIT = Path("/etc/systemd/system/prism-identity-tailnet-recovery.service")
WATCHDOG_SOCKET = "/run/prism-host-watchdog/watchdog.sock"
WATCHDOG_DB = "/var/lib/prism-identity/service-state/demo.sqlite"
OWNER_UID = 0
HELPER = "/usr/local/libexec/prism-identity-service-guest.py"
LIFECYCLE = "/usr/local/libexec/prism-identity-service-lifecycle.py"
CONFIG = "/var/lib/prism-identity/oidc-service-config.json"
CLIENT_SECRET = "/var/lib/prism-identity/oidc-client-secret"
CERT = "/var/lib/prism-identity/tls/cert.pem"
KEY = "/var/lib/prism-identity/tls/key.pem"
MODEL_KEY = "/var/lib/prism-identity/openai-model-key"
DATA = "/var/lib/prism-identity/service-state"
RECOVERY_DIRECTORY = Path("/var/lib/prism-identity/offline-recovery")
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
CGROUP_ROOT = Path("/sys/fs/cgroup")
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
SOURCE_FILES_V7 = SOURCE_FILES | {"src/prism/host_watchdog.py"}
SOURCE_FILES_V8 = SOURCE_FILES_V7 | {"src/prism/demo.py", "src/prism/recovery.py"}


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
    kind = manifest.get("kind")
    source_files = (
        SOURCE_FILES_V8
        if kind == "prism_service_update_v8"
        else SOURCE_FILES_V7
        if kind == "prism_service_update_v7"
        else SOURCE_FILES
    )
    if not isinstance(hashes, dict) or set(hashes) != source_files:
        raise ValueError("Installation manifest differs from the marker.")
    if update:
        transfer = installed.get("transfer_tar_sha256")
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
            "prism_service_update_v4": {
                "src/prism/identity.py",
                "src/prism/webapp.py",
                "src/prism/static/app.js",
                "src/prism/conversation.py",
                "src/prism/sharing.py",
                "src/prism/owner.py",
                "src/prism/handoff.py",
            },
            "prism_service_update_v5": {
                "src/prism/identity.py",
                "src/prism/webapp.py",
                "src/prism/static/app.js",
                "src/prism/conversation.py",
                "src/prism/sharing.py",
                "src/prism/owner.py",
                "src/prism/handoff.py",
                "src/prism/jobs.py",
            },
            "prism_service_update_v6": {
                "src/prism/identity.py",
                "src/prism/webapp.py",
                "src/prism/static/app.js",
                "src/prism/conversation.py",
                "src/prism/sharing.py",
                "src/prism/owner.py",
                "src/prism/handoff.py",
                "src/prism/jobs.py",
                "src/prism/worker.py",
            },
            "prism_service_update_v7": {
                "src/prism/identity.py",
                "src/prism/webapp.py",
                "src/prism/static/app.js",
                "src/prism/conversation.py",
                "src/prism/sharing.py",
                "src/prism/owner.py",
                "src/prism/handoff.py",
                "src/prism/jobs.py",
                "src/prism/worker.py",
                "src/prism/host_watchdog.py",
            },
            "prism_service_update_v8": {
                "src/prism/identity.py",
                "src/prism/webapp.py",
                "src/prism/static/app.js",
                "src/prism/conversation.py",
                "src/prism/sharing.py",
                "src/prism/owner.py",
                "src/prism/handoff.py",
                "src/prism/jobs.py",
                "src/prism/worker.py",
                "src/prism/host_watchdog.py",
                "src/prism/demo.py",
                "src/prism/recovery.py",
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
                if kind
                in (
                    "prism_service_update_v2",
                    "prism_service_update_v3",
                    "prism_service_update_v4",
                    "prism_service_update_v5",
                    "prism_service_update_v6",
                    "prism_service_update_v7",
                    "prism_service_update_v8",
                )
                else set()
            )
            | (
                {"identity_sha256"}
                if kind
                in (
                    "prism_service_update_v3",
                    "prism_service_update_v4",
                    "prism_service_update_v5",
                    "prism_service_update_v6",
                    "prism_service_update_v7",
                    "prism_service_update_v8",
                )
                else set()
            )
            | (
                {
                    "conversation_sha256",
                    "sharing_sha256",
                    "owner_sha256",
                    "handoff_sha256",
                }
                if kind
                in (
                    "prism_service_update_v4",
                    "prism_service_update_v5",
                    "prism_service_update_v6",
                    "prism_service_update_v7",
                    "prism_service_update_v8",
                )
                else set()
            )
            | (
                {"jobs_sha256"}
                if kind
                in (
                    "prism_service_update_v5",
                    "prism_service_update_v6",
                    "prism_service_update_v7",
                    "prism_service_update_v8",
                )
                else set()
            )
            | (
                {"worker_sha256"}
                if kind
                in (
                    "prism_service_update_v6",
                    "prism_service_update_v7",
                    "prism_service_update_v8",
                )
                else set()
            )
            | (
                {"host_watchdog_sha256"}
                if kind in ("prism_service_update_v7", "prism_service_update_v8")
                else set()
            )
            | (
                {"demo_sha256", "recovery_sha256"}
                if kind == "prism_service_update_v8"
                else set()
            )
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
                kind
                in (
                    "prism_service_update_v2",
                    "prism_service_update_v3",
                    "prism_service_update_v4",
                    "prism_service_update_v5",
                    "prism_service_update_v6",
                    "prism_service_update_v7",
                    "prism_service_update_v8",
                )
                and manifest.get("app_js_sha256") != hashes["src/prism/static/app.js"]
            )
            or (
                kind
                in (
                    "prism_service_update_v3",
                    "prism_service_update_v4",
                    "prism_service_update_v5",
                    "prism_service_update_v6",
                    "prism_service_update_v7",
                    "prism_service_update_v8",
                )
                and manifest.get("identity_sha256") != hashes["src/prism/identity.py"]
            )
            or (
                kind
                in (
                    "prism_service_update_v4",
                    "prism_service_update_v5",
                    "prism_service_update_v6",
                    "prism_service_update_v7",
                    "prism_service_update_v8",
                )
                and any(
                    manifest.get(key + "_sha256") != hashes["src/prism/" + key + ".py"]
                    for key in ("conversation", "sharing", "owner", "handoff")
                )
            )
            or (
                kind
                in (
                    "prism_service_update_v5",
                    "prism_service_update_v6",
                    "prism_service_update_v7",
                    "prism_service_update_v8",
                )
                and manifest.get("jobs_sha256") != hashes["src/prism/jobs.py"]
            )
            or (
                kind
                in (
                    "prism_service_update_v6",
                    "prism_service_update_v7",
                    "prism_service_update_v8",
                )
                and manifest.get("worker_sha256") != hashes["src/prism/worker.py"]
            )
            or (
                kind in ("prism_service_update_v7", "prism_service_update_v8")
                and manifest.get("host_watchdog_sha256")
                != hashes["src/prism/host_watchdog.py"]
            )
            or (
                kind == "prism_service_update_v8"
                and (
                    manifest.get("demo_sha256") != hashes["src/prism/demo.py"]
                    or manifest.get("recovery_sha256")
                    != hashes["src/prism/recovery.py"]
                )
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
            if (
                len(members) != len(source_files)
                or {item.name for item in members} != source_files
            ):
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
    for name in sorted(source_files):
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


def unit_text(
    host,
    ip,
    record,
    release,
    bundle,
    run_id,
    budget=0,
    project_sha256=None,
    watchdog=False,
    recovery=False,
):
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
    dependency = " prism-identity-host-watchdog.service" if watchdog else ""
    watchdog_preflight = (
        f"ExecStartPre=/usr/bin/python3 {LIFECYCLE} watchdog-ready {pinned}\n"
        if watchdog
        else ""
    )
    recovery_preflight = (
        f"ExecStartPre=/usr/bin/python3 {LIFECYCLE} recovery-claim {pinned}\n"
        if recovery
        else ""
    )
    return f"""[Unit]
Description=Prism private named identity service
Requires=tailscaled.service prism-identify-boot-restore.service{dependency}
BindsTo=tailscaled.service{dependency}
After=tailscaled.service prism-identify-boot-restore.service{dependency}

[Service]
Type=exec
User=root
UMask=0077
Restart=no
TimeoutStartSec=360
TimeoutStopSec=240
StandardOutput=null
StandardError=journal
{recovery_preflight}ExecStartPre=/usr/bin/python3 {LIFECYCLE} prepare {pinned}
{watchdog_preflight}ExecStartPre=/usr/bin/python3 {HELPER} preflight {prism}{model}{project}
ExecStart=/usr/bin/python3 {LIFECYCLE} serve {pinned}{model}
ExecStartPost=/usr/bin/python3 {LIFECYCLE} open {pinned}
ExecStopPost=/usr/bin/python3 {LIFECYCLE} close {common}
"""


def watchdog_unit_text(host, ip, record, release, bundle, run_id, recovery=False):
    pinned = (
        f"--hostname {host} --bind-host {ip} --installed-record {record} "
        f"--bundle-sha256 {bundle} --run-id {run_id} "
        f"--release {release} --db {WATCHDOG_DB}"
    )
    on_failure = (
        "OnFailure=prism-identity-tailnet-recovery.service\n" if recovery else ""
    )
    return f"""[Unit]
Description=Prism private host watchdog
{on_failure}StartLimitIntervalSec=60
StartLimitBurst=3

[Service]
Type=exec
User=root
UMask=0077
RuntimeDirectory=prism-host-watchdog
RuntimeDirectoryMode=0700
Restart=no
RestartSec=1
TimeoutStartSec=60
TimeoutStopSec=240
StandardOutput=null
StandardError=journal
ExecStartPre=/usr/bin/python3 {LIFECYCLE} watchdog-prepare {pinned}
ExecStart=/usr/bin/python3 {LIFECYCLE} watchdog-serve {pinned}
ExecStopPost=/usr/bin/python3 {LIFECYCLE} close --hostname {host} --bind-host {ip}
"""


def recovery_unit_text(host, ip, record, bundle, run_id, project_sha256=None):
    project = f" --project-sha256 {project_sha256}" if project_sha256 else ""
    pinned = (
        f"--hostname {host} --bind-host {ip} --installed-record {record} "
        f"--bundle-sha256 {bundle} --run-id {run_id}{project}"
    )
    return f"""[Unit]
Description=Prism one-attempt offline recovery

[Service]
Type=oneshot
User=root
UMask=0077
Restart=no
TimeoutStartSec=300
TimeoutStopSec=360
StandardOutput=null
StandardError=journal
ExecStart=/usr/bin/python3 {LIFECYCLE} recovery-run {pinned}
ExecStopPost=/usr/bin/python3 {LIFECYCLE} recovery-stop-post {pinned}
"""


def render(args):
    if os.geteuid() != 0:
        raise ValueError("Guest service rendering requires root.")
    host = fixed_host()
    ip = bind_ip(args.bind_host)
    selected = Path(args.installed_record) if args.installed_record else INSTALLED
    release, bundle, run_id = installation(selected)
    watchdog = uses_watchdog(selected)
    recovery = uses_recovery(selected)
    budget = model_budget(args.model_budget_cents)
    project_sha256 = None
    if args.enable_project:
        project_sha256 = project_inventory(PROJECT_INVENTORY_SHA256)
        if project_sha256 != PROJECT_INVENTORY_SHA256:
            raise ValueError("The installed project differs from the reviewed fixture.")
    target = Path(args.output)
    if target != UNIT or target.is_symlink():
        raise ValueError("Expected the fixed new service unit path.")
    if watchdog and (WATCHDOG_UNIT.exists() or WATCHDOG_UNIT.is_symlink()):
        raise ValueError("Expected the fixed new watchdog unit path.")
    if recovery and (RECOVERY_UNIT.exists() or RECOVERY_UNIT.is_symlink()):
        raise ValueError("Expected the fixed new recovery unit path.")
    if recovery:
        _prepare_recovery_directory()
    descriptor = os.open(
        target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    watchdog_created = recovery_created = False
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(
                unit_text(
                    host,
                    ip,
                    selected,
                    release,
                    bundle,
                    run_id,
                    budget,
                    project_sha256,
                    watchdog,
                    recovery,
                )
            )
            stream.flush()
            os.fsync(stream.fileno())
        if watchdog:
            watchdog_descriptor = os.open(
                WATCHDOG_UNIT,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            watchdog_created = True
            with os.fdopen(watchdog_descriptor, "w", encoding="utf-8") as stream:
                stream.write(
                    watchdog_unit_text(
                        host, ip, selected, release, bundle, run_id, recovery
                    )
                )
                stream.flush()
                os.fsync(stream.fileno())
        if recovery:
            recovery_descriptor = os.open(
                RECOVERY_UNIT,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            recovery_created = True
            with os.fdopen(recovery_descriptor, "w", encoding="utf-8") as stream:
                stream.write(
                    recovery_unit_text(
                        host, ip, selected, bundle, run_id, project_sha256
                    )
                )
                stream.flush()
                os.fsync(stream.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        if watchdog_created:
            WATCHDOG_UNIT.unlink(missing_ok=True)
        if recovery_created:
            RECOVERY_UNIT.unlink(missing_ok=True)
        raise
    print(
        "Private service unit rendered; it has no Install section and is not enabled."
    )


def _prepare_recovery_directory():
    parent = RECOVERY_DIRECTORY.parent
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError("Recovery parent is not root private.")
    if not RECOVERY_DIRECTORY.exists() and not RECOVERY_DIRECTORY.is_symlink():
        RECOVERY_DIRECTORY.mkdir(mode=0o700)
        descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    info = RECOVERY_DIRECTORY.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise ValueError("Recovery directory is not root private.")


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


def tailnet_state(host, ip, shield, *, close_only_offline=False, strict_fresh=False):
    sampled_at = time.monotonic()
    status = json.loads(run("tailscale", "status", "--json").stdout)
    prefs = json.loads(run("tailscale", "debug", "prefs").stdout)
    # The offline exception is only for a fresh, local close observation. A
    # slow pair of reads must not confirm protection based on an old sample.
    fresh = time.monotonic() - sampled_at <= 5
    if not isinstance(status, dict) or not isinstance(prefs, dict):
        raise TypeError("The fixed private tailnet state is unavailable.")
    own = status.get("Self")
    if not isinstance(own, dict):
        raise TypeError("The fixed private tailnet state is unavailable.")
    addresses = own.get("TailscaleIPs")
    online = own.get("Online")
    dns_name = own.get("DNSName")
    if (
        status.get("BackendState") != "Running"
        or (strict_fresh and not fresh)
        or not (online is True or (close_only_offline and online is False and fresh))
        or not isinstance(dns_name, str)
        or dns_name.lower().rstrip(".") != host
        or not isinstance(addresses, list)
        or addresses.count(ip) != 1
        or own.get("Tags") != ["tag:prism-host"]
        or prefs.get("ShieldsUp") is not shield
        or prefs.get("RunSSH") is not False
        or prefs.get("RouteAll") is not False
        or "AdvertiseRoutes" not in prefs
        or prefs["AdvertiseRoutes"] not in (None, [])
        or prefs.get("ExitNodeID") != ""
        or prefs.get("ExitNodeIP") != ""
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
            main_pid = run(
                "systemctl",
                "show",
                "--value",
                "--property=MainPID",
                "tailscaled.service",
                timeout=3,
            ).stdout.strip()
            control_pid = run(
                "systemctl",
                "show",
                "--value",
                "--property=ControlPID",
                "tailscaled.service",
                timeout=3,
            ).stdout.strip()
            active = run(
                "systemctl",
                "show",
                "--value",
                "--property=ActiveState",
                "tailscaled.service",
                timeout=3,
            ).stdout.strip()
            group = run(
                "systemctl",
                "show",
                "--value",
                "--property=ControlGroup",
                "tailscaled.service",
                timeout=3,
            ).stdout.strip()
            fixed_group = "/system.slice/tailscaled.service"
            if (
                main_pid == "0"
                and control_pid == "0"
                and active in {"inactive", "failed", "deactivating"}
                and group in {"", fixed_group}
                and (group or active in {"inactive", "failed"})
            ):
                root = CGROUP_ROOT / fixed_group.lstrip("/")
                if not root.exists():
                    return
                procs = list(root.rglob("cgroup.procs"))
                if procs and not any(
                    path.read_text(encoding="ascii").strip() for path in procs
                ):
                    return
        except (
            OSError,
            UnicodeError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
        ):
            pass
        time.sleep(0.2)
    raise ValueError("Tailnet daemon termination could not be independently verified.")


def close_failure_category(exc):
    """Return a fixed journal label without exposing command output or identity."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return "timeout"
    if isinstance(exc, subprocess.CalledProcessError):
        return "command"
    if isinstance(exc, OSError):
        return "os"
    return "state"


def close(host, ip):
    restore_failure = None
    try:
        run(RESTORE, timeout=40)
        tailnet_state(host, ip, True, close_only_offline=True)
        return
    except (
        OSError,
        TypeError,
        ValueError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        restore_failure = close_failure_category(exc)
    try:
        run("tailscale", "set", "--shields-up=true", timeout=20)
        tailnet_state(host, ip, True, close_only_offline=True)
        return
    except (
        OSError,
        TypeError,
        ValueError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        try:
            print(
                "Private inbound close verification failed "
                f"(restore={restore_failure}, fallback={close_failure_category(exc)}); "
                "blocking tailnet daemon",
                file=sys.stderr,
            )
        finally:
            emergency_block()


def prepare(host, ip, record, bundle, run_id, project_sha256=None):
    close(host, ip)
    tailnet_state(host, ip, True)
    guard_ready()
    old_timer_absent()
    installation(record, bundle, run_id)
    if project_sha256 is not None:
        project_inventory(project_sha256)
    print("Private service guard and closed tailnet state passed.")


def uses_watchdog(record):
    return Path(record) != INSTALLED and json.loads(
        (Path(record).parent / "manifest.json").read_text(encoding="utf-8")
    ).get("kind") in ("prism_service_update_v7", "prism_service_update_v8")


def uses_recovery(record):
    if Path(record) == INSTALLED:
        return False
    try:
        value = json.loads(
            (Path(record).parent / "manifest.json").read_text(encoding="utf-8")
        )
    except FileNotFoundError:
        return False
    return isinstance(value, dict) and value.get("kind") == "prism_service_update_v8"


def watchdog_release(record, bundle, run_id):
    release, _, _ = installation(record, bundle, run_id)
    if not uses_watchdog(record):
        raise ValueError("The fixed release has no host watchdog.")
    return release


def verify_watchdog_unit(host, ip, record, release, bundle, run_id):
    info = WATCHDOG_UNIT.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != OWNER_UID
        or info.st_nlink != 1
        or info.st_mode & 0o022
        or info.st_size > 8192
        or WATCHDOG_UNIT.read_text(encoding="utf-8")
        != watchdog_unit_text(
            host, ip, record, release, bundle, run_id, uses_recovery(record)
        )
    ):
        raise ValueError("The fixed host watchdog unit differs from the release.")


def watchdog_command(release, action, host=None, ip=None):
    argv = [str(Path(release) / "venv/bin/python"), "-m", "prism.host_watchdog", action]
    if action == "serve":
        if host is None or ip is None:
            raise ValueError("The host watchdog requires its pinned tailnet identity.")
        argv += ["--db", WATCHDOG_DB, "--hostname", host, "--bind-host", ip]
    return argv


def watchdog_environment(release):
    return {**ENV, "PYTHONPATH": str(Path(release) / "src"), "PYTHONUNBUFFERED": "1"}


def watchdog_prepare(host, ip, record, bundle, run_id, expected_release, db):
    release = watchdog_release(record, bundle, run_id)
    if expected_release != release or db != WATCHDOG_DB:
        raise ValueError("The host watchdog unit differs from the fixed release or DB.")
    verify_watchdog_unit(host, ip, record, release, bundle, run_id)


def watchdog_serve(host, ip, record, bundle, run_id, expected_release, db):
    release = watchdog_release(record, bundle, run_id)
    if expected_release != release or db != WATCHDOG_DB:
        raise ValueError("The host watchdog unit differs from the fixed release or DB.")
    verify_watchdog_unit(host, ip, record, release, bundle, run_id)
    argv = watchdog_command(release, "serve", host, ip)
    os.execve(argv[0], argv, watchdog_environment(release))


def watchdog_ready(host, ip, record, bundle, run_id):
    release = watchdog_release(record, bundle, run_id)
    verify_watchdog_unit(host, ip, record, release, bundle, run_id)
    for attempt in range(10):
        try:
            run("systemctl", "is-active", "--quiet", WATCHDOG_UNIT.name)
            subprocess.run(
                watchdog_command(release, "health"),
                env=watchdog_environment(release),
                cwd=release,
                capture_output=True,
                text=True,
                timeout=3,
                check=True,
            )
            return
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if attempt == 9:
                raise ValueError("The fixed host watchdog is not healthy.")
            time.sleep(1)


def recovery_module(release):
    path = Path(release) / "src/prism/recovery.py"
    spec = importlib.util.spec_from_file_location("prism_recovery_release", path)
    if spec is None or spec.loader is None:
        raise ValueError("The pinned recovery source is unavailable.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _unit_loaded(path):
    for field, expected in (
        ("FragmentPath", str(path)),
        ("NeedDaemonReload", "no"),
        ("DropInPaths", ""),
    ):
        actual = run(
            "systemctl", "show", "--property=" + field, "--value", path.name
        ).stdout.strip()
        if actual != expected:
            raise ValueError("A loaded recovery unit differs from its pinned file.")


def _unit_file(path, expected):
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_nlink != 1
        or info.st_mode & 0o022
        or info.st_size > 8192
        or path.read_text(encoding="utf-8") != expected
    ):
        raise ValueError("A recovery unit differs from its pinned release.")
    _unit_loaded(path)


def _recovery_pins(host, ip, record, bundle, run_id, project_sha256=None):
    release, _, _ = installation(record, bundle, run_id)
    if not uses_recovery(record):
        raise ValueError("The pinned release has no recovery controller.")
    data = UNIT.read_text(encoding="utf-8")
    match = re.search(r"--model-budget-cents ([0-9]+)(?:\n|$)", data)
    if not match:
        raise ValueError("The fixed service budget is unavailable.")
    budget = model_budget(int(match.group(1)))
    _unit_file(
        UNIT,
        unit_text(
            host,
            ip,
            record,
            release,
            bundle,
            run_id,
            budget,
            project_sha256,
            True,
            True,
        ),
    )
    _unit_file(
        WATCHDOG_UNIT,
        watchdog_unit_text(host, ip, record, release, bundle, run_id, True),
    )
    _unit_file(
        RECOVERY_UNIT,
        recovery_unit_text(host, ip, record, bundle, run_id, project_sha256),
    )
    return release


def _recovery_unit_arguments():
    lines = UNIT.read_text(encoding="utf-8").splitlines()
    commands = [
        line.partition("=")[2]
        for line in lines
        if line.startswith("ExecStartPre=") and f" {LIFECYCLE} recovery-claim " in line
    ]
    if len(commands) != 1:
        raise ValueError("Recovery claim command is unavailable.")
    argv = shlex.split(commands[0])
    if argv[:3] != ["/usr/bin/python3", LIFECYCLE, "recovery-claim"]:
        raise ValueError("Recovery claim command differs.")
    options = {}
    rest = argv[3:]
    if len(rest) % 2:
        raise ValueError("Recovery claim arguments are invalid.")
    for name, value in zip(rest[::2], rest[1::2]):
        if name in options or not name.startswith("--") or value.startswith("--"):
            raise ValueError("Recovery claim arguments are ambiguous.")
        options[name] = value
    expected = {
        "--hostname",
        "--bind-host",
        "--installed-record",
        "--bundle-sha256",
        "--run-id",
    }
    if set(options) not in (expected, expected | {"--project-sha256"}):
        raise ValueError("Recovery claim arguments differ.")
    return tuple(
        options[name]
        for name in (
            "--hostname",
            "--bind-host",
            "--installed-record",
            "--bundle-sha256",
            "--run-id",
            "--project-sha256",
        )
        if name in options
    )


def _service_state(*, starting_pid=None):
    fields = ("ActiveState", "SubState", "MainPID", "ControlPID", "ControlGroup")
    values = {}
    for field in fields:
        values[field] = run(
            "systemctl", "show", "--property=" + field, "--value", UNIT.name
        ).stdout.strip()
    if starting_pid is None:
        if (
            values["ActiveState"] not in ("inactive", "failed")
            or values["MainPID"] != "0"
            or values["ControlPID"] != "0"
        ):
            raise ValueError("Service is not stopped.")
    elif values["ActiveState"] != "activating" or values["MainPID"] != str(
        starting_pid
    ):
        raise ValueError("The starting service process changed.")
    group = "/system.slice/" + UNIT.name
    if values["ControlGroup"] not in ("", group):
        raise ValueError("Unexpected service cgroup.")
    root = CGROUP_ROOT / group.lstrip("/")
    if root.exists():
        pids = set()
        for path in root.rglob("cgroup.procs"):
            pids.update(int(value) for value in path.read_text().split())
        expected = set() if starting_pid is None else {starting_pid}
        if starting_pid is not None and values["ControlPID"] != "0":
            control_pid = int(values["ControlPID"])
            command = (Path("/proc") / str(control_pid) / "cmdline").read_bytes()
            if not (
                command.startswith(b"/usr/bin/python3\0")
                and LIFECYCLE.encode() + b"\0open\0" in command
            ):
                raise ValueError("Unexpected service control process.")
            expected.add(control_pid)
            # systemd can run ExecStartPost while the main process performs
            # the final gate.  Its bounded status/TLS checks may have a child.
            pending = {control_pid}
            while pending:
                parent = pending.pop()
                for pid in pids - expected:
                    raw = (Path("/proc") / str(pid) / "stat").read_text()
                    parent_id = int(raw.rsplit(")", 1)[1].split()[1])
                    if parent_id != parent:
                        continue
                    argv = (Path("/proc") / str(pid) / "cmdline").read_bytes()
                    allowed = (
                        b"/usr/bin/systemctl\0",
                        b"systemctl\0",
                        b"/usr/bin/tailscale\0",
                        b"tailscale\0",
                        b"/usr/bin/python3\0" + HELPER.encode() + b"\0selfcheck\0",
                    )
                    if not argv.startswith(allowed):
                        raise ValueError("Unexpected service control child.")
                    expected.add(pid)
                    pending.add(pid)
        if pids != expected:
            raise ValueError("Service cgroup is not in the required state.")
    elif starting_pid is not None:
        raise ValueError("Starting service cgroup is unavailable.")


def _watchdog_failure_offline():
    fields = {
        name: run(
            "systemctl",
            "show",
            "--property=" + name,
            "--value",
            WATCHDOG_UNIT.name,
        ).stdout.strip()
        for name in ("ActiveState", "Result", "ExecMainCode", "ExecMainStatus")
    }
    if (
        fields["ActiveState"] != "failed"
        or fields["Result"] != "exit-code"
        or fields["ExecMainCode"] != "1"
        or fields["ExecMainStatus"] != "42"
    ):
        raise ValueError("Watchdog failure is not a durable offline close.")


def recovery_claim(host, ip, record, bundle, run_id, project_sha256=None):
    release = _recovery_pins(host, ip, record, bundle, run_id, project_sha256)
    recovery = recovery_module(release)
    recovery.directory()
    if not recovery.EPISODE.exists() and not recovery.EPISODE.is_symlink():
        return
    invocation = os.environ.get("INVOCATION_ID")
    event = recovery.episode()
    if event.get("state") != "ready":
        raise ValueError("Recovery event has no available service attempt.")
    controller = {
        field: run(
            "systemctl",
            "show",
            "--property=" + field,
            "--value",
            RECOVERY_UNIT.name,
        ).stdout.strip()
        for field in ("ActiveState", "ControlPID", "InvocationID")
    }
    if (
        controller["ActiveState"] != "activating"
        or controller["ControlPID"] != str(event["controller_pid"])
        or controller["InvocationID"] != event["controller_invocation"]
    ):
        raise ValueError("Recovery controller is no longer the live unit job.")
    recovery.claim_ready(invocation, controller["InvocationID"])
    recovery.audit("attempted", event["event_id"])


def recovery_final(host, ip, record, bundle, run_id, project_sha256=None):
    release = _recovery_pins(host, ip, record, bundle, run_id, project_sha256)
    recovery = recovery_module(release)
    recovery.directory()
    if not recovery.EPISODE.exists() and not recovery.EPISODE.is_symlink():
        return
    invocation = os.environ.get("INVOCATION_ID")
    event = recovery.episode()
    if event.get("state") != "attempted" or event.get("invocation_id") != invocation:
        raise ValueError("Recovery service attempt was not claimed.")
    _service_state(starting_pid=os.getpid())
    watchdog_ready(host, ip, record, bundle, run_id)
    tailnet_state(host, ip, True, strict_fresh=True)
    recovery.workload_clear()
    tailnet_state(host, ip, True, strict_fresh=True)
    recovery.transition("attempted", "entered", invocation)
    recovery.audit("entered", event["event_id"])


def _recovery_readiness(host, ip, record, bundle, run_id):
    run("systemctl", "is-active", "--quiet", UNIT.name)
    watchdog_ready(host, ip, record, bundle, run_id)
    tailnet_state(host, ip, False, strict_fresh=True)
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


def _recovery_failed(host, ip, recovery, event_id):
    try:
        event = recovery.episode()
        if event["event_id"] != event_id:
            raise ValueError("Recovery event changed during failure handling.")
        if event.get("state") in ("consumed", "ready", "attempted", "entered"):
            recovery.transition(event["state"], "failed", event.get("invocation_id"))
        run("systemctl", "stop", UNIT.name, timeout=45)
        close(host, ip)
        _service_state()
        tailnet_state(host, ip, True, close_only_offline=True)
        recovery.audit("failed", event_id)
    except BaseException:
        try:
            run("systemctl", "stop", UNIT.name, timeout=45)
            close(host, ip)
            _service_state()
            tailnet_state(host, ip, True, close_only_offline=True)
        finally:
            # The original failure may be a missing/corrupt event or a failed
            # archive rollback.  Stopping Prism alone cannot preserve the
            # one-attempt latch in that case.
            emergency_block()
        raise


def recovery_run(host, ip, record, bundle, run_id, project_sha256=None):
    release = _recovery_pins(host, ip, record, bundle, run_id, project_sha256)
    recovery = recovery_module(release)
    _watchdog_failure_offline()
    event = recovery.episode()
    if event.get("state") is not None:
        raise ValueError("Recovery event was already attempted.")
    event_id = event["event_id"]
    recovery.audit("seen", event_id)
    _service_state()
    guard_ready()
    start = time.monotonic()
    sample = 0
    while time.monotonic() - start < 60:
        before = time.monotonic()
        tailnet_state(host, ip, True, strict_fresh=True)
        if time.monotonic() - before > 5:
            raise ValueError("Tailnet sample was stale.")
        sample += 1
        time.sleep(max(0, min(1, start + sample - time.monotonic())))
    tailnet_state(host, ip, True, strict_fresh=True)
    recovery.audit("sample", event_id)
    with recovery.service_lock():
        _service_state()
        tailnet_state(host, ip, True, strict_fresh=True)
        recovery.workload_clear()
        recovery.audit("checked", event_id)
        recovery.transition(None, "consumed")
        recovery.audit("consumed", event_id)
    try:
        run("systemctl", "start", WATCHDOG_UNIT.name, timeout=45)
        watchdog_ready(host, ip, record, bundle, run_id)
        recovery.audit("watchdog", event_id)
        with recovery.service_lock():
            _service_state()
            tailnet_state(host, ip, True, strict_fresh=True)
            recovery.workload_clear()
            recovery.audit("rechecked", event_id)
            recovery.arm_ready(os.environ.get("INVOCATION_ID"))
        run("systemctl", "start", UNIT.name, timeout=90)
        _recovery_readiness(host, ip, record, bundle, run_id)
        recovery.audit("verified", event_id)
        recovery.audit("archive-intent", event_id)
        recovery.archive(event_id)
    except BaseException:
        _recovery_failed(host, ip, recovery, event_id)
        raise


def recovery_stop_post(host, ip, record, bundle, run_id, project_sha256=None):
    """Independent systemd cleanup when the controller is killed or fails."""
    try:
        release = _recovery_pins(host, ip, record, bundle, run_id, project_sha256)
        recovery = recovery_module(release)
        if os.environ.get("SERVICE_RESULT") == "success":
            recovery.directory()
            if recovery.EPISODE.exists() or recovery.EPISODE.is_symlink():
                raise ValueError("Successful recovery retained an active event.")
            recovery._private_file(recovery.AUDIT, 1024 * 1024)
            lines = recovery.AUDIT.read_bytes().splitlines()
            if not lines:
                raise ValueError("Recovery outcome audit is unavailable.")
            outcome = json.loads(lines[-1])
            if (
                not isinstance(outcome, dict)
                or set(outcome) != {"event", "event_id"}
                or outcome.get("event") != "archive-intent"
                or not re.fullmatch(r"[0-9a-f]{32}", outcome.get("event_id", ""))
            ):
                raise ValueError("Recovery outcome audit is incomplete.")
            archive = recovery.ROOT / ("archived-" + outcome["event_id"])
            recovery._private_file(archive, 512)
            archived = json.loads(archive.read_text(encoding="ascii"))
            if (
                archived.get("event_id") != outcome["event_id"]
                or archived.get("state") != "entered"
            ):
                raise ValueError("Recovery archive differs from the outcome.")
            _recovery_readiness(host, ip, record, bundle, run_id)
            return
        run("systemctl", "stop", UNIT.name, timeout=45)
        close(host, ip)
        _service_state()
        tailnet_state(host, ip, True, close_only_offline=True)
        if recovery.EPISODE.exists() or recovery.EPISODE.is_symlink():
            event = recovery.episode()
            if event.get("state") in (
                None,
                "consumed",
                "ready",
                "attempted",
                "entered",
            ):
                recovery.transition(
                    event.get("state"), "failed", event.get("invocation_id")
                )
            recovery.audit("failed", event["event_id"])
        else:
            raise ValueError("Failed recovery lost its durable event latch.")
    except BaseException:
        try:
            run("systemctl", "stop", UNIT.name, timeout=45)
            close(host, ip)
            _service_state()
            tailnet_state(host, ip, True, close_only_offline=True)
        finally:
            emergency_block()
        raise


def recovery_abandon(host, ip, record, bundle, run_id, event_id, project_sha256=None):
    """Explicit operator reset after a failed event, without starting Prism."""
    if not isinstance(event_id, str) or not re.fullmatch(r"[0-9a-f]{32}", event_id):
        raise ValueError("Specify the exact failed recovery event ID.")
    release = _recovery_pins(host, ip, record, bundle, run_id, project_sha256)
    recovery = recovery_module(release)
    for field, allowed in (
        ("ActiveState", {"inactive", "failed"}),
        ("ControlPID", {"0"}),
        ("MainPID", {"0"}),
    ):
        value = run(
            "systemctl",
            "show",
            "--property=" + field,
            "--value",
            RECOVERY_UNIT.name,
        ).stdout.strip()
        if value not in allowed:
            raise ValueError("Recovery controller is still active.")
    _service_state()
    guard_ready()
    tailnet_state(host, ip, True, strict_fresh=True)
    with recovery.service_lock():
        _service_state()
        tailnet_state(host, ip, True, strict_fresh=True)
        recovery.workload_clear()
        event = recovery.episode()
        if event["event_id"] != event_id or event.get("state") != "failed":
            raise ValueError("Only the exact failed event may be abandoned.")
        recovery.audit("abandon-intent", event_id)
        recovery.abandon(event_id)


def open_service(host, ip, record, bundle, run_id, project_sha256=None):
    try:
        installed = installation(record, bundle, run_id)
        recovery = recovery_module(installed[0]) if uses_recovery(record) else None
        if uses_watchdog(record):
            watchdog_ready(host, ip, record, bundle, run_id)
        if project_sha256 is not None:
            project_inventory(project_sha256)
        guard_ready()
        old_timer_absent()
        tailnet_state(host, ip, True, strict_fresh=recovery is not None)
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
        tailnet_state(host, ip, True, strict_fresh=recovery is not None)
        if recovery is not None:
            recovery.directory()
            if recovery.EPISODE.exists() or recovery.EPISODE.is_symlink():
                event = recovery.episode()
                if event.get("state") != "entered" or event.get(
                    "invocation_id"
                ) != os.environ.get("INVOCATION_ID"):
                    raise ValueError("Recovery service attempt is not entered.")
                recovery.audit("opened", event["event_id"])
        run("tailscale", "set", "--shields-up=false", timeout=20)
        tailnet_state(host, ip, False, strict_fresh=recovery is not None)
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
        if recovery is not None and (
            recovery.EPISODE.exists() or recovery.EPISODE.is_symlink()
        ):
            recovery.audit("verified", recovery.episode()["event_id"])
    except BaseException:
        close(host, ip)
        raise
    print("Private service opened after local HTTPS identity check.")


def serve(host, ip, record, bundle, run_id, budget=0, project_sha256=None):
    model_budget(budget)
    release, _, _ = installation(record, bundle, run_id)
    if uses_watchdog(record):
        watchdog_ready(host, ip, record, bundle, run_id)
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
    environment = dict(ENV)
    if uses_recovery(record):
        invocation = os.environ.get("INVOCATION_ID", "")
        if not re.fullmatch(r"[0-9a-f]{32}", invocation):
            raise ValueError("Service invocation identity is unavailable.")
        environment["INVOCATION_ID"] = invocation
        environment["PRISM_RECOVERY_GUARD"] = "1"
    os.execve("/usr/bin/python3", argv, environment)


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    actions = root.add_subparsers(dest="action", required=True)
    for name in (
        "render",
        "prepare",
        "serve",
        "open",
        "close",
        "watchdog-prepare",
        "watchdog-serve",
        "watchdog-ready",
        "recovery-claim",
        "recovery-run",
        "recovery-stop-post",
        "recovery-abandon",
    ):
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
            if name == "recovery-abandon":
                item.add_argument("--event-id", required=True)
            if name in ("watchdog-prepare", "watchdog-serve"):
                item.add_argument("--release", required=True)
                item.add_argument("--db", required=True)
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
                elif args.action == "recovery-claim":
                    recovery_claim(
                        host,
                        ip,
                        args.installed_record,
                        args.bundle_sha256,
                        args.run_id,
                        args.project_sha256,
                    )
                elif args.action == "recovery-run":
                    recovery_run(
                        host,
                        ip,
                        args.installed_record,
                        args.bundle_sha256,
                        args.run_id,
                        args.project_sha256,
                    )
                elif args.action == "recovery-stop-post":
                    recovery_stop_post(
                        host,
                        ip,
                        args.installed_record,
                        args.bundle_sha256,
                        args.run_id,
                        args.project_sha256,
                    )
                elif args.action == "recovery-abandon":
                    recovery_abandon(
                        host,
                        ip,
                        args.installed_record,
                        args.bundle_sha256,
                        args.run_id,
                        args.event_id,
                        args.project_sha256,
                    )
                elif args.action == "watchdog-prepare":
                    watchdog_prepare(
                        host,
                        ip,
                        args.installed_record,
                        args.bundle_sha256,
                        args.run_id,
                        args.release,
                        args.db,
                    )
                elif args.action == "watchdog-serve":
                    watchdog_serve(
                        host,
                        ip,
                        args.installed_record,
                        args.bundle_sha256,
                        args.run_id,
                        args.release,
                        args.db,
                    )
                elif args.action == "watchdog-ready":
                    watchdog_ready(
                        host, ip, args.installed_record, args.bundle_sha256, args.run_id
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
        RuntimeError,
    ) as exc:
        print(f"Prism service lifecycle failed: {type(exc).__name__}.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
