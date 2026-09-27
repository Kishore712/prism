"""Test-only v7 watchdog cleanup of a previously RUNNING Kata resource.

Run as root on the dedicated Linux/Kata host with --release pointing to the
installed immutable v7 release. Uses an isolated SQLite row and test processes.
The fixed service-cgroup kill is replaced with termination of the exact test
client; this does not test the installed systemd unit or Shields Up.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shlex
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
from contextlib import closing, nullcontext
from pathlib import Path
from typing import NamedTuple
from unittest.mock import patch

OBSERVE_SECONDS = 25
ABSENCE_SECONDS = 30
LATE_SECONDS = 5
POLL_SECONDS = 0.05
SLEEP_PROGRAM = "import time;time.sleep(29)\n"
V7_TRANSFER_SHA256 = "eb9e409032696fe4b6159cc2d338e548e6c1100d723a45b4b0607549f80bd501"
SOURCE_ARCHIVE_SHA256 = (
    "1f09da7467b1d76b20ad82ee039e906cd0f18befa359fd851e61d1bf437954dd"
)
RELEASE_ROOT = Path("/var/lib/prism/identity-pilot/app-releases")
SOURCE_FILES = (
    "src/prism/__init__.py",
    "src/prism/host_watchdog.py",
    "src/prism/reference_runtime.py",
    "src/prism/engine.py",
    "src/prism/runtime.py",
)
REQUIRED = (
    "dedicated_linux_host",
    "installed_v7_source_verified",
    "live_units_inactive",
    "maintenance_shields_up",
    "pidfd_signal_ready",
    "initial_namespace_empty",
    "isolated_row_registered",
    "exact_owned_running_observed",
    "exact_worker_and_client_observed",
    "watchdog_lease_renewed",
    "exact_worker_killed",
    "worker_loss_grace_proven",
    "exact_client_alive_at_expiry",
    "watchdog_expiry_called",
    "isolated_row_uncertain_null",
    "exact_resource_absent_within_30s",
    "exact_processes_terminated",
    "no_late_recreation",
    "final_namespace_empty",
    "cleanup_confirmed",
)


def verified_modules(release: Path):
    if (
        release.name != "service-" + V7_TRANSFER_SHA256
        or release.parent != RELEASE_ROOT
        or release.is_symlink()
    ):
        raise ValueError("Expected the exact installed immutable v7 release")
    release = release.resolve(strict=True)
    transfer = release.name.removeprefix("service-")
    if (
        not release.is_dir()
        or transfer != V7_TRANSFER_SHA256
        or release.parent != RELEASE_ROOT
    ):
        raise ValueError("Installed immutable release path required")
    manifest_path = (
        release.parent.parent / "service-updates" / transfer / "manifest.json"
    )
    for directory in (
        release.parent.parent,
        release.parent,
        release,
        manifest_path.parent,
    ):
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise ValueError("Installed release directory is not root-private")
    info = manifest_path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_nlink != 1
        or info.st_mode & 0o077
        or info.st_size > 16384
    ):
        raise ValueError("Installed v7 manifest is not root-private")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    hashes = manifest.get("file_sha256")
    if (
        manifest.get("kind") != "prism_service_update_v7"
        or manifest.get("release_name") != release.name
        or manifest.get("transfer_tar_sha256") != V7_TRANSFER_SHA256
        or manifest.get("source_tar_sha256") != SOURCE_ARCHIVE_SHA256
        or not isinstance(hashes, dict)
        or manifest.get("host_watchdog_sha256") != hashes.get(SOURCE_FILES[1])
    ):
        raise ValueError("Installed v7 manifest required")
    if len(hashes) != 30 or not set(SOURCE_FILES).issubset(hashes):
        raise ValueError("Installed v7 source inventory differs")
    archive = manifest_path.parent / "source.tar"
    archive_info = archive.lstat()
    if (
        not stat.S_ISREG(archive_info.st_mode)
        or archive_info.st_uid != 0
        or archive_info.st_nlink != 1
        or archive_info.st_mode & 0o077
        or archive_info.st_size > 64 * 1024 * 1024
        or hashlib.sha256(archive.read_bytes()).hexdigest() != SOURCE_ARCHIVE_SHA256
    ):
        raise ValueError("Installed v7 source archive differs")
    with tarfile.open(archive, "r:") as bundle:
        members = bundle.getmembers()
        if len(members) != 30 or {item.name for item in members} != set(hashes):
            raise ValueError("Installed v7 source archive inventory differs")
        for member in members:
            stream = bundle.extractfile(member) if member.isfile() else None
            if stream is None or member.size > 16 * 1024 * 1024:
                raise ValueError("Installed v7 source archive member differs")
            if hashlib.sha256(stream.read()).hexdigest() != hashes[member.name]:
                raise ValueError("Installed v7 source archive hash differs")
    for relative in hashes:
        if not isinstance(relative, str):
            raise TypeError("Installed v7 source path differs")
        parts = Path(relative).parts
        if (
            Path(relative).is_absolute()
            or any(part in (".", "..") for part in parts)
            or parts[0] not in ("src", "README.md", "pyproject.toml", "uv.lock")
        ):
            raise ValueError("Installed v7 source path differs")
        path = release / relative
        parent = path.parent
        while parent != release:
            parent_info = parent.lstat()
            if not source_directory_is_safe(parent_info):
                raise ValueError("Installed source directory is writable or unowned")
            parent = parent.parent
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_nlink != 1
            or info.st_mode & 0o077
            or info.st_size > 16 * 1024 * 1024
            or path.resolve(strict=True) != path
        ):
            raise ValueError("Installed source is not a regular release file")
        if hashlib.sha256(path.read_bytes()).hexdigest() != hashes.get(relative):
            raise ValueError("Installed v7 source digest mismatch")
    source_root = release / "src"
    sys.path.insert(0, str(source_root))
    from prism import host_watchdog, reference_runtime

    if (
        Path(host_watchdog.__file__).resolve() != source_root / "prism/host_watchdog.py"
        or Path(reference_runtime.__file__).resolve()
        != source_root / "prism/reference_runtime.py"
    ):
        raise ValueError("Installed v7 import mismatch")
    return host_watchdog, reference_runtime


def source_directory_is_safe(info) -> bool:
    return stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022


def process_identity(pid: int):
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        fields = raw[raw.rfind(")") + 2 :].split()
        return int(fields[19]), fields[0]
    except (OSError, ValueError, IndexError):
        return None


def children(pid: int):
    try:
        raw = Path(f"/proc/{pid}/task/{pid}/children").read_text(encoding="ascii")
        return [int(value) for value in raw.split()]
    except (OSError, ValueError):
        return []


def exact_client(pid: int, resource: str, token: str) -> bool:
    try:
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return False
    try:
        args = [item.decode("utf-8", errors="strict") for item in argv if item]
    except UnicodeError:
        return False
    return (
        bool(args)
        and Path(args[0]).name == "nerdctl"
        and "run" in args
        and any(args[i : i + 2] == ["--name", resource] for i in range(len(args) - 1))
        and any(
            args[i : i + 2] == ["--label", "org.prism.reference-run=" + token]
            for i in range(len(args) - 1)
        )
    )


def gone(pid: int, start: int) -> bool:
    current = process_identity(pid)
    return current is None or current[0] != start or current[1] in ("Z", "X")


class Client(NamedTuple):
    pid: int
    start: int
    pidfd: int


def discover_exact_clients(resource: str, token: str) -> list[Client]:
    found = []
    try:
        for entry in Path("/proc").iterdir():
            if not entry.name.isdecimal():
                continue
            pid = int(entry.name)
            if not exact_client(pid, resource, token):
                continue
            identity = process_identity(pid)
            if identity is None or identity[1] in ("Z", "X"):
                continue
            try:
                pidfd = os.pidfd_open(pid, 0)
            except ProcessLookupError:
                continue
            if process_identity(pid) != identity or not exact_client(
                pid, resource, token
            ):
                os.close(pidfd)
                continue
            found.append(Client(pid, identity[0], pidfd))
            if len(found) > 8:
                raise RuntimeError("Too many exact test clients")
        return found
    except BaseException:
        close_clients(found)
        raise


def close_clients(clients) -> None:
    for client in clients:
        try:
            os.close(client.pidfd)
        except OSError:
            pass


def client_live(client: Client, resource: str, token: str) -> bool:
    return not gone(client.pid, client.start) and exact_client(
        client.pid, resource, token
    )


def kill_client(client: Client, resource: str, token: str) -> bool:
    if not client_live(client, resource, token):
        return False
    try:
        signal.pidfd_send_signal(client.pidfd, signal.SIGKILL, None, 0)
    except ProcessLookupError:
        return False
    return True


def cleanup_test_worker(worker, worker_start) -> bool:
    if worker is None:
        return False
    if worker.poll() is not None:
        return True
    identity = process_identity(worker.pid)
    if worker_start is None or identity is None or identity[0] != worker_start[0]:
        return False
    worker.kill()
    worker.wait(timeout=5)
    return worker.poll() is not None


def cleanup_exact(runtime, resource: str, token: str, tracked) -> bool:
    """Keep looking for a late exact client/resource, even after one cleanup error."""
    if runtime is None or not resource or not token:
        return False
    clean = True
    deadline = time.monotonic() + LATE_SECONDS
    while True:
        found = []
        try:
            found = discover_exact_clients(resource, token)
            for client in found:
                if client_live(client, resource, token):
                    kill_client(client, resource, token)
        except Exception:  # noqa: BLE001 - continue bounded exact cleanup.
            clean = False
        finally:
            close_clients(found)
        try:
            runtime.reconcile(resource, token, settle_seconds=1)
        except Exception:  # noqa: BLE001 - keep looking for late creation.
            clean = False
        if time.monotonic() >= deadline:
            break
        time.sleep(0.1)
    found = []
    try:
        found = discover_exact_clients(resource, token)
        return bool(
            clean
            and not found
            and all(gone(client.pid, client.start) for client in tracked)
            and runtime.inspect_owned(resource, token) is None
            and runtime.all_resources() == []
        )
    except Exception:  # noqa: BLE001 - failed verification retains evidence.
        return False
    finally:
        close_clients(found)


def owned_running(runtime, resource: str, token: str) -> bool:
    info = runtime.inspect_owned(resource, token)
    state = info.get("State", {}) if info else {}
    return isinstance(state, dict) and (
        state.get("Status") == "running" or state.get("Running") is True
    )


def expiry_ready(runtime, resource: str, token: str, tracked) -> bool:
    return (
        len(tracked) == 1
        and client_live(tracked[0], resource, token)
        and owned_running(runtime, resource, token)
    )


def worker_loss_ready(watchdog, active, dead_at: float, now: float) -> bool:
    return bool(
        watchdog.active is active
        and active["worker_dead_at"] == dead_at
        and now > dead_at + 2.5
        and now < active["deadline"]
    )


def cgroup_tree_drained(unit: str) -> bool:
    root = Path("/sys/fs/cgroup/system.slice") / unit
    if not root.exists():
        return True
    if not root.is_dir() or root.is_symlink():
        return False
    for path in (root, *root.rglob("*")):
        if path.is_dir() and (
            path.is_symlink() or (path / "cgroup.procs").read_text().strip()
        ):
            return False
    return True


def unit_commands(lines):
    result = {}
    for line in lines:
        if not line.startswith("Exec"):
            continue
        if "=" not in line or "\\" in line or ";" in line:
            raise ValueError("Unit command is not one fixed directive")
        key, value = line.split("=", 1)
        argv = shlex.split(value, posix=True)
        if not argv:
            raise ValueError("Unit command is empty")
        result.setdefault(key, []).append(argv)
    return result


def command_options(argv, executable: str, action: str):
    if argv[:3] != ["/usr/bin/python3", executable, action] or len(argv[3:]) % 2:
        raise ValueError("Unit action differs")
    options = {}
    for key, value in zip(argv[3::2], argv[4::2], strict=True):
        if not key.startswith("--") or key in options or value.startswith("--"):
            raise ValueError("Unit arguments differ")
        options[key] = value
    return options


def unit_identity(
    service, watcher, release: Path, marker: Path, installed: dict
) -> bool:
    lifecycle = "/usr/local/libexec/prism-identity-service-lifecycle.py"
    helper = "/usr/local/libexec/prism-identity-service-guest.py"
    service_cmds, watcher_cmds = unit_commands(service), unit_commands(watcher)
    if (
        {key: len(value) for key, value in service_cmds.items()}
        != {"ExecStartPre": 3, "ExecStart": 1, "ExecStartPost": 1, "ExecStopPost": 1}
        or {key: len(value) for key, value in watcher_cmds.items()}
        != {"ExecStartPre": 1, "ExecStart": 1, "ExecStopPost": 1}
        or "BindsTo=tailscaled.service prism-identity-host-watchdog.service"
        not in service
        or "User=root" not in service
        or "User=root" not in watcher
        or set(installed)
        != {
            "source_tar_sha256",
            "transfer_tar_sha256",
            "base_bundle_sha256",
            "base_run_id",
        }
        or installed["transfer_tar_sha256"] != V7_TRANSFER_SHA256
        or installed["source_tar_sha256"] != SOURCE_ARCHIVE_SHA256
        or not isinstance(installed["base_run_id"], str)
        or len(installed["base_run_id"]) != 32
        or any(c not in "0123456789abcdef" for c in installed["base_run_id"])
        or not isinstance(installed["base_bundle_sha256"], str)
        or len(installed["base_bundle_sha256"]) != 64
        or any(c not in "0123456789abcdef" for c in installed["base_bundle_sha256"])
    ):
        return False
    try:
        service_pre = {}
        for argv in service_cmds["ExecStartPre"]:
            action = argv[2] if len(argv) >= 3 else ""
            executable = helper if action == "preflight" else lifecycle
            if action in service_pre:
                return False
            service_pre[action] = command_options(argv, executable, action)
        if set(service_pre) != {"prepare", "watchdog-ready", "preflight"}:
            return False
        actions = {
            **service_pre,
            "service_serve": command_options(
                service_cmds["ExecStart"][0], lifecycle, "serve"
            ),
            "service_open": command_options(
                service_cmds["ExecStartPost"][0], lifecycle, "open"
            ),
            "service_close": command_options(
                service_cmds["ExecStopPost"][0], lifecycle, "close"
            ),
            "watchdog_prepare": command_options(
                watcher_cmds["ExecStartPre"][0], lifecycle, "watchdog-prepare"
            ),
            "watchdog_serve": command_options(
                watcher_cmds["ExecStart"][0], lifecycle, "watchdog-serve"
            ),
            "watchdog_close": command_options(
                watcher_cmds["ExecStopPost"][0], lifecycle, "close"
            ),
        }
        for key in (
            "prepare",
            "watchdog-ready",
            "service_serve",
            "service_open",
            "watchdog_prepare",
            "watchdog_serve",
        ):
            if (
                actions[key].get("--installed-record") != str(marker)
                or actions[key].get("--bundle-sha256") != SOURCE_ARCHIVE_SHA256
                or actions[key].get("--run-id") != installed["base_run_id"]
            ):
                return False
        if any(
            actions[key].get("--release") != str(release)
            for key in ("preflight", "watchdog_prepare", "watchdog_serve")
        ):
            return False
        return all(
            actions[key].get("--db")
            == "/var/lib/prism-identity/service-state/demo.sqlite"
            for key in ("watchdog_prepare", "watchdog_serve")
        )
    except (KeyError, ValueError, IndexError):
        return False


def live_units_inactive(release: Path) -> bool:
    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}
    unit_paths = (
        Path("/etc/systemd/system/prism-identity-service.service"),
        Path("/etc/systemd/system/prism-identity-host-watchdog.service"),
    )
    for path in unit_paths:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            return False
        for field, expected in (
            ("LoadState", "loaded"),
            ("FragmentPath", str(path)),
            ("DropInPaths", ""),
            ("NeedDaemonReload", "no"),
            ("ActiveState", "inactive"),
            ("ControlGroup", ""),
            ("MainPID", "0"),
            ("ControlPID", "0"),
        ):
            result = subprocess.run(
                [
                    "/usr/bin/systemctl",
                    "show",
                    "--property=" + field,
                    "--value",
                    path.name,
                ],
                env=env,
                capture_output=True,
                timeout=3,
                check=True,
            )
            if result.stdout.decode("ascii").strip() != expected:
                return False
        if not cgroup_tree_drained(path.name):
            return False
    service = unit_paths[0].read_text(encoding="utf-8").splitlines()
    watcher = unit_paths[1].read_text(encoding="utf-8").splitlines()
    marker = (
        RELEASE_ROOT.parent / "service-updates" / V7_TRANSFER_SHA256 / "installed.json"
    )
    info = marker.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_nlink != 1
        or info.st_mode & 0o077
        or info.st_size > 4096
    ):
        return False
    installed = json.loads(marker.read_text(encoding="utf-8"))
    manifest = json.loads((marker.parent / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or not isinstance(installed, dict):
        return False
    if any(
        manifest.get(key) != installed.get(key)
        for key in (
            "source_tar_sha256",
            "transfer_tar_sha256",
            "base_bundle_sha256",
            "base_run_id",
        )
    ):
        return False
    return isinstance(installed, dict) and unit_identity(
        service, watcher, release, marker, installed
    )


def maintenance_shields_up() -> bool:
    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}
    values = []
    for args in (("status", "--json"), ("debug", "prefs")):
        result = subprocess.run(
            ["/usr/bin/tailscale", *args],
            env=env,
            capture_output=True,
            timeout=5,
            check=True,
        )
        values.append(json.loads(result.stdout))
    status, prefs = values
    own = status.get("Self") if isinstance(status, dict) else None
    return bool(
        isinstance(own, dict)
        and status.get("BackendState") == "Running"
        and own.get("Online") is True
        and own.get("Tags") == ["tag:prism-host"]
        and isinstance(prefs, dict)
        and prefs.get("ShieldsUp") is True
        and prefs.get("RunSSH") is False
        and prefs.get("RouteAll") is False
        and not prefs.get("AdvertiseRoutes")
        and not prefs.get("ExitNodeID")
        and not prefs.get("ExitNodeIP")
    )


def pidfd_signal_ready() -> bool:
    descriptor = os.pidfd_open(os.getpid(), 0)
    try:
        signal.pidfd_send_signal(descriptor, 0, None, 0)
        return True
    finally:
        os.close(descriptor)


def isolated_row(db_path: Path, run: str, resource: str, token: str):
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute(
            "CREATE TABLE runs (id TEXT PRIMARY KEY,status TEXT,runtime_profile TEXT,"
            "runtime_resource TEXT,runtime_token TEXT,finished REAL,result TEXT,error TEXT)"
        )
        db.execute(
            "CREATE TABLE events (id INTEGER PRIMARY KEY,at REAL,kind TEXT,actor TEXT,"
            "resource TEXT,outcome TEXT)"
        )
        db.execute(
            "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)",
            (run, "running", "reference-linux", resource, token, None, None, None),
        )


def sleeper(release: Path, resource: str, token: str) -> int:
    _, reference_runtime = verified_modules(release)
    runtime = reference_runtime.ReferenceLinuxRuntime()
    runtime._execute(
        SLEEP_PROGRAM,
        [],
        timeout=30,
        cancel=threading.Event(),
        name=resource,
        token=token,
    )
    return 0


def run_check(release: Path) -> dict:
    checks = {name: False for name in REQUIRED}
    report = {
        "kind": "m2-observed-watchdog-check",
        "schema": 1,
        "test_only": True,
        "real_kata_observed": False,
        "installed_systemd_service_tested": False,
        "real_service_cgroup_kill_tested": False,
        "shields_up_tested": False,
        "live_database_touched": False,
        "model_calls": 0,
        "pilot_ready": False,
        "checks": checks,
        "outcome": "failed",
        "error_kind": None,
        "error_phase": None,
        "evidence_retained": False,
        "passed": False,
    }
    if (
        platform.system() != "Linux"
        or platform.machine() != "x86_64"
        or os.geteuid() != 0
    ):
        report["error_kind"] = "UnsupportedHost"
        return report
    checks["dedicated_linux_host"] = True
    runtime = worker = watchdog = root = worker_start = None
    resource = token = None
    tracked = []
    phase = "verify_release"
    try:
        host_watchdog, reference_runtime = verified_modules(release)
        checks["installed_v7_source_verified"] = True
        phase = "live_units"
        checks["live_units_inactive"] = live_units_inactive(release.resolve())
        if not checks["live_units_inactive"]:
            raise RuntimeError("Live Prism units are not inactive and drained")
        phase = "maintenance_shields_up"
        checks["maintenance_shields_up"] = maintenance_shields_up()
        if not checks["maintenance_shields_up"]:
            raise RuntimeError("Tailnet Shields Up is not confirmed")
        phase = "pidfd_preflight"
        checks["pidfd_signal_ready"] = pidfd_signal_ready()
        if not checks["pidfd_signal_ready"]:
            raise RuntimeError("Linux pidfd signal path is unavailable")
        phase = "initial_namespace"
        runtime = reference_runtime.ReferenceLinuxRuntime()
        checks["initial_namespace_empty"] = runtime.all_resources() == []
        if not checks["initial_namespace_empty"]:
            raise RuntimeError("Reference namespace occupied")
        root = Path(tempfile.mkdtemp(prefix="prism-observed-watchdog-"))
        with nullcontext(root):
            root.chmod(0o700)
            run, token = uuid.uuid4().hex, uuid.uuid4().hex
            resource = runtime.resource_name(run)
            db_path = root / "isolated.sqlite"

            def stop_exact_test_client():
                # Substitute only the fixed service cgroup kill boundary. PID
                # start ticks prevent signaling a reused, unrelated process.
                if not expiry_ready(runtime, resource, token, tracked):
                    raise host_watchdog.WatchdogError(
                        "Exact test client was not active at watchdog expiry"
                    )
                checks["exact_client_alive_at_expiry"] = True
                if not kill_client(tracked[0], resource, token):
                    raise host_watchdog.WatchdogError(
                        "Exact test client exited before kill"
                    )

            clock_samples = []

            def observed_clock():
                value = time.monotonic()
                clock_samples.append(value)
                return value

            watchdog = host_watchdog.HostWatchdog(
                db_path,
                runtime=runtime,
                kill_service=stop_exact_test_client,
                clock=observed_clock,
            )
            watchdog.startup()
            isolated_row(db_path, run, resource, token)
            checks["isolated_row_registered"] = True
            peer = os.getpid()
            with (
                patch.object(host_watchdog, "_cgroup", return_value="test-cgroup"),
                patch.object(
                    host_watchdog, "_service_cgroup", return_value="test-cgroup"
                ),
            ):
                watchdog.dispatch(
                    {
                        "op": "register",
                        "run": run,
                        "resource": resource,
                        "token": token,
                    },
                    peer,
                    0,
                )
                worker = subprocess.Popen(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--release",
                        str(release),
                        "--sleeper",
                        resource,
                        token,
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    cwd="/",
                    env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
                )
                worker_start = process_identity(worker.pid)
                if worker_start is None:
                    raise RuntimeError("Test worker identity unavailable")
                watchdog.dispatch(
                    {
                        "op": "attach",
                        "run": run,
                        "resource": resource,
                        "token": token,
                        "pid": worker.pid,
                    },
                    peer,
                    0,
                )
            phase = "observe_running"
            deadline = time.monotonic() + OBSERVE_SECONDS
            next_renewal = time.monotonic() + 0.5
            while time.monotonic() < deadline and worker.poll() is None:
                if time.monotonic() >= next_renewal:
                    watchdog.dispatch(
                        {
                            "op": "renew",
                            "run": run,
                            "resource": resource,
                            "token": token,
                        },
                        peer,
                        0,
                    )
                    checks["watchdog_lease_renewed"] = True
                    next_renewal = time.monotonic() + 0.5
                try:
                    info = runtime.inspect_owned(resource, token)
                except reference_runtime.EngineError as exc:
                    if (
                        str(exc)
                        != "The named reference resource could not be inspected."
                    ):
                        raise
                    time.sleep(POLL_SECONDS)
                    continue
                state = info.get("State", {}) if info else {}
                if isinstance(state, dict) and (
                    state.get("Status") == "running" or state.get("Running") is True
                ):
                    checks["exact_owned_running_observed"] = True
                    report["real_kata_observed"] = True
                    clients = children(worker.pid)
                    if len(clients) == 1 and exact_client(clients[0], resource, token):
                        found = discover_exact_clients(resource, token)
                        if len(found) == 1 and found[0].pid == clients[0]:
                            tracked = found
                            checks["exact_worker_and_client_observed"] = True
                            break
                        close_clients(found)
                time.sleep(POLL_SECONDS)
            if not checks["exact_worker_and_client_observed"]:
                report["outcome"] = "inconclusive"
                return report
            phase = "fault_worker"
            info = runtime.inspect_owned(resource, token)
            state = info.get("State", {}) if info else {}
            if (
                worker.poll() is not None
                or (process_identity(worker.pid) or (None,))[0] != worker_start[0]
                or not isinstance(state, dict)
                or not (
                    state.get("Status") == "running" or state.get("Running") is True
                )
            ):
                report["outcome"] = "inconclusive"
                return report
            killed_at = time.monotonic()
            watchdog.dispatch(
                {"op": "renew", "run": run, "resource": resource, "token": token},
                peer,
                0,
            )
            checks["watchdog_lease_renewed"] = True
            if (process_identity(worker.pid) or (None,))[0] != worker_start[0]:
                raise RuntimeError("Test worker identity changed")
            worker.kill()
            worker.wait(timeout=5)
            checks["exact_worker_killed"] = worker.returncode == -signal.SIGKILL
            phase = "watchdog_expiry"
            watchdog.tick()
            active = watchdog.active
            if (
                active is None
                or active["worker_dead_at"] is None
                or active["deadline"] <= time.monotonic()
            ):
                report["outcome"] = "inconclusive"
                return report
            dead_at = active["worker_dead_at"]
            time.sleep(max(0, dead_at + 2.55 - time.monotonic()))
            if not expiry_ready(runtime, resource, token, tracked):
                report["outcome"] = "inconclusive"
                return report
            if not worker_loss_ready(watchdog, active, dead_at, time.monotonic()):
                report["outcome"] = "inconclusive"
                return report
            clock_samples.clear()
            watchdog.tick()
            checks["worker_loss_grace_proven"] = bool(
                clock_samples
                and all(
                    dead_at + 2.5 < sample < active["deadline"]
                    for sample in clock_samples
                )
            )
            if not checks["worker_loss_grace_proven"]:
                report["outcome"] = "inconclusive"
            checks["watchdog_expiry_called"] = watchdog.active is None
            phase = "verify_cleanup"
            with closing(sqlite3.connect(db_path)) as db:
                row = db.execute(
                    "SELECT status,result FROM runs WHERE id=?", (run,)
                ).fetchone()
            checks["isolated_row_uncertain_null"] = row == ("uncertain", None)
            deadline = killed_at + ABSENCE_SECONDS
            while time.monotonic() <= deadline:
                if runtime.inspect_owned(resource, token) is None:
                    checks["exact_resource_absent_within_30s"] = True
                    break
                time.sleep(POLL_SECONDS)
            checks["exact_processes_terminated"] = all(
                gone(client.pid, client.start) for client in tracked
            )
            if checks["exact_resource_absent_within_30s"]:
                checks["no_late_recreation"] = True
                deadline = time.monotonic() + LATE_SECONDS
                while time.monotonic() < deadline:
                    if runtime.inspect_owned(resource, token) is not None:
                        checks["no_late_recreation"] = False
                        break
                    time.sleep(POLL_SECONDS)
            checks["final_namespace_empty"] = runtime.all_resources() == []
    except Exception as exc:  # noqa: BLE001 - only exception type enters report.
        report["error_kind"] = type(exc).__name__
        report["error_phase"] = phase
    finally:
        worker_gone = False
        try:
            worker_gone = cleanup_test_worker(worker, worker_start)
        except Exception as exc:  # noqa: BLE001 - exact resource cleanup still runs.
            report["error_kind"] = report["error_kind"] or type(exc).__name__
        exact_clean = False
        try:
            exact_clean = cleanup_exact(runtime, resource, token, tracked)
        except Exception as exc:  # noqa: BLE001 - preserve a bounded result.
            report["error_kind"] = report["error_kind"] or type(exc).__name__
        checks["cleanup_confirmed"] = worker_gone and exact_clean
        if exact_clean:
            checks["final_namespace_empty"] = True
        close_clients(tracked)
        if root is not None:
            if checks["cleanup_confirmed"]:
                try:
                    shutil.rmtree(root)
                except OSError as exc:
                    checks["cleanup_confirmed"] = False
                    report["error_kind"] = report["error_kind"] or type(exc).__name__
                    report["evidence_retained"] = True
            else:
                report["evidence_retained"] = True
    report["passed"] = report["error_kind"] is None and all(checks.values())
    if report["passed"]:
        report["outcome"] = "passed"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True, type=Path)
    parser.add_argument("--sleeper", nargs=2, metavar=("RESOURCE", "TOKEN"))
    args = parser.parse_args()
    if args.sleeper:
        try:
            return sleeper(args.release, *args.sleeper)
        except Exception:  # noqa: BLE001 - parent emits the bounded result.
            return 2
    report = run_check(args.release)
    print(
        "PRISM_OBSERVED_WATCHDOG_RESULT " + json.dumps(report, sort_keys=True),
        flush=True,
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
