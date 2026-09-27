"""Test the installed v7 service cgroup kill with one isolated Kata resource.

This is a disruptive, root-only maintenance check for the dedicated host. It
temporarily enables Tailscale Shields Up, moves only its own worker into the
installed identity-service cgroup, and calls the pinned v7 watchdog algorithm
with its *real* systemd kill function. The durable run row is in a temporary
SQLite database; the installed watchdog socket and live Jobs submission are
not exercised. Run only with no active live work.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import closing
from pathlib import Path

OBSERVED = Path("/var/lib/prism/identity-pilot/checks/m2-observed-watchdog-check.py")
OBSERVED_SHA256 = "de10d183193d933a059292bff6480ce0bc46b443b3439ce7e24443d80bb18c35"
SERVICE = "prism-identity-service.service"
WATCHDOG = "prism-identity-host-watchdog.service"
DB = Path("/var/lib/prism-identity/service-state/demo.sqlite")
CGROUP = Path("/sys/fs/cgroup/system.slice") / SERVICE
REQUIRED = (
    "exact_v7_and_units",
    "live_idle_and_namespace_empty",
    "maintenance_shields_up",
    "test_worker_in_real_service_cgroup",
    "exact_resource_and_client_running",
    "worker_loss_grace",
    "real_systemd_cgroup_kill",
    "service_main_and_test_client_dead",
    "shields_up_after_service_stop",
    "isolated_row_uncertain_null",
    "exact_resource_absent_within_30s",
    "no_late_recreation",
    "namespace_and_cgroup_drained",
    "live_counts_unchanged",
    "service_and_watchdog_restored",
    "cleanup_confirmed",
)


def command(
    *argv: str, timeout: int = 10, check: bool = True
) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
        capture_output=True,
        timeout=timeout,
        check=check,
    )


def show(unit: str, key: str) -> str:
    return (
        command("/usr/bin/systemctl", "show", "--property=" + key, "--value", unit)
        .stdout.decode("ascii")
        .strip()
    )


def service_active(unit: str) -> bool:
    return show(unit, "ActiveState") == "active" and int(show(unit, "MainPID")) > 1


def shield_state() -> bool:
    status = json.loads(command("/usr/bin/tailscale", "status", "--json").stdout)
    prefs = json.loads(command("/usr/bin/tailscale", "debug", "prefs").stdout)
    own = status.get("Self") or {}
    if (
        status.get("BackendState") != "Running"
        or own.get("Online") is not True
        or own.get("Tags") != ["tag:prism-host"]
        or prefs.get("RunSSH") is not False
        or prefs.get("RouteAll") is not False
        or prefs.get("AdvertiseRoutes")
        or prefs.get("ExitNodeID")
        or prefs.get("ExitNodeIP")
    ):
        raise RuntimeError("Unexpected private network state")
    value = prefs.get("ShieldsUp")
    if type(value) is not bool:
        raise RuntimeError("Unknown Shields Up state")
    return value


def live_counts() -> dict:
    info = DB.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise RuntimeError("Live database identity differs")
    with closing(sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=3)) as db:
        if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise RuntimeError("Live database integrity failed")
        return {
            "runs": db.execute("SELECT count(*) FROM runs").fetchone()[0],
            "pending": db.execute(
                "SELECT count(*) FROM runs WHERE status IN ('queued','running','uncertain')"
            ).fetchone()[0],
            "grants": db.execute("SELECT count(*) FROM grants").fetchone()[0],
            "model_dispatches": db.execute(
                "SELECT count(*) FROM model_dispatches"
            ).fetchone()[0],
        }


def load_observed():
    info = OBSERVED.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o700
        or hashlib.sha256(OBSERVED.read_bytes()).hexdigest() != OBSERVED_SHA256
    ):
        raise RuntimeError("Pinned observed-resource helper differs")
    spec = importlib.util.spec_from_file_location(
        "prism_observed_check_pinned", OBSERVED
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Pinned helper cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def active_unit_identity(observed, release: Path) -> int:
    marker = (
        observed.RELEASE_ROOT.parent
        / "service-updates"
        / observed.V7_TRANSFER_SHA256
        / "installed.json"
    )
    info = marker.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise RuntimeError("Installed record differs")
    units = []
    for name in (SERVICE, WATCHDOG):
        path = Path("/etc/systemd/system") / name
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError("Installed unit differs")
        if (
            show(name, "LoadState") != "loaded"
            or show(name, "FragmentPath") != str(path)
            or show(name, "DropInPaths")
            or show(name, "NeedDaemonReload") != "no"
            or not service_active(name)
        ):
            raise RuntimeError("Installed unit is not active and pinned")
        units.append(path.read_text(encoding="utf-8").splitlines())
    if not observed.unit_identity(
        units[0], units[1], release, marker, json.loads(marker.read_text())
    ):
        raise RuntimeError("Installed v7 unit commands differ")
    if show(SERVICE, "ControlGroup") != "/system.slice/" + SERVICE:
        raise RuntimeError("Identity service cgroup differs")
    if show(WATCHDOG, "ControlGroup") != "/system.slice/" + WATCHDOG:
        raise RuntimeError("Watchdog cgroup differs")
    if not CGROUP.is_dir() or CGROUP.is_symlink():
        raise RuntimeError("Service cgroup is unavailable")
    return int(show(SERVICE, "MainPID"))


def wait_for(predicate, seconds: float, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def run_check(release: Path) -> dict:
    checks = {key: False for key in REQUIRED}
    report = {
        "kind": "m2-systemd-cgroup-check",
        "schema": 1,
        "test_only": True,
        "installed_watchdog_socket_tested": False,
        "live_jobs_submission_tested": False,
        "direct_live_database_writes": False,
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
    observed = runtime = worker = root = worker_start = None
    tracked = []
    resource = token = None
    shield_changed = False
    baseline = None
    service_pid = service_start = None
    phase = "verify"
    try:
        observed = load_observed()
        host_watchdog, reference_runtime = observed.verified_modules(release)
        service_pid = active_unit_identity(observed, release)
        service_start = observed.process_identity(service_pid)
        if service_start is None or service_start[1] in ("Z", "X"):
            raise RuntimeError("Service process identity unavailable")
        checks["exact_v7_and_units"] = True
        baseline = live_counts()
        runtime = reference_runtime.ReferenceLinuxRuntime()
        if (
            baseline["pending"]
            or runtime.all_resources()
            or shield_state() is not False
        ):
            raise RuntimeError("Live service is not idle and serving")
        checks["live_idle_and_namespace_empty"] = True
        phase = "maintenance"
        shield_changed = True
        command("/usr/bin/tailscale", "set", "--shields-up=true")
        if not wait_for(lambda: shield_state() is True, 5):
            raise RuntimeError("Maintenance Shields Up not confirmed")
        checks["maintenance_shields_up"] = True
        if live_counts() != baseline or runtime.all_resources():
            raise RuntimeError("Live work changed before test admission")
        group = "/system.slice/" + SERVICE
        if set(host_watchdog._group_processes(group)) != {service_pid}:
            raise RuntimeError(
                "Service cgroup contains work other than its main process"
            )
        phase = "create"
        root = Path(tempfile.mkdtemp(prefix="prism-systemd-cgroup-"))
        root.chmod(0o700)
        run, token = uuid.uuid4().hex, uuid.uuid4().hex
        resource = runtime.resource_name(run)
        db_path = root / "isolated.sqlite"
        watchdog = host_watchdog.HostWatchdog(db_path, runtime=runtime)
        watchdog.startup()
        observed.isolated_row(db_path, run, resource, token)
        watchdog.dispatch(
            {"op": "register", "run": run, "resource": resource, "token": token},
            service_pid,
            0,
        )
        worker = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--release",
                str(release),
                "--worker",
                resource,
                token,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd="/",
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
        )
        worker_start = observed.process_identity(worker.pid)
        if (
            worker_start is None
            or worker_start[1] in ("Z", "X")
            or worker.stdin is None
        ):
            raise RuntimeError("Test worker identity unavailable")
        CGROUP.joinpath("cgroup.procs").write_text(
            str(worker.pid) + "\n", encoding="ascii"
        )
        if host_watchdog._cgroup(worker.pid) != "/system.slice/" + SERVICE:
            raise RuntimeError("Test worker was not placed in service cgroup")
        checks["test_worker_in_real_service_cgroup"] = True
        watchdog.dispatch(
            {
                "op": "attach",
                "run": run,
                "resource": resource,
                "token": token,
                "pid": worker.pid,
            },
            service_pid,
            0,
        )
        worker.stdin.write(b"go\n")
        worker.stdin.close()
        phase = "observe"
        deadline = time.monotonic() + observed.OBSERVE_SECONDS
        next_renewal = time.monotonic() + 0.5
        while time.monotonic() < deadline and worker.poll() is None:
            if time.monotonic() >= next_renewal:
                watchdog.dispatch(
                    {"op": "renew", "run": run, "resource": resource, "token": token},
                    service_pid,
                    0,
                )
                next_renewal = time.monotonic() + 0.5
            try:
                owned = observed.owned_running(runtime, resource, token)
            except reference_runtime.EngineError as exc:
                if str(exc) != "The named reference resource could not be inspected.":
                    raise
                owned = False
            if owned:
                found = observed.discover_exact_clients(resource, token)
                if (
                    len(found) == 1
                    and observed.children(worker.pid) == [found[0].pid]
                    and host_watchdog._cgroup(found[0].pid)
                    == "/system.slice/" + SERVICE
                ):
                    tracked = found
                    checks["exact_resource_and_client_running"] = True
                    break
                observed.close_clients(found)
            time.sleep(observed.POLL_SECONDS)
        if not checks["exact_resource_and_client_running"]:
            report["outcome"] = "inconclusive"
            return report
        phase = "fault"
        if live_counts() != baseline or not observed.expiry_ready(
            runtime, resource, token, tracked
        ):
            report["outcome"] = "inconclusive"
            return report
        watchdog.dispatch(
            {"op": "renew", "run": run, "resource": resource, "token": token},
            service_pid,
            0,
        )
        identity = observed.process_identity(worker.pid)
        if (
            identity is None
            or identity[0] != worker_start[0]
            or identity[1] in ("Z", "X")
        ):
            raise RuntimeError("Test worker identity changed")
        killed_at = time.monotonic()
        worker.kill()
        worker.wait(timeout=5)
        if worker.returncode != -signal.SIGKILL:
            raise RuntimeError("Test worker was not killed")
        watchdog.tick()
        active = watchdog.active
        if active is None or active["worker_dead_at"] is None:
            report["outcome"] = "inconclusive"
            return report
        dead_at = active["worker_dead_at"]
        time.sleep(max(0, dead_at + 2.55 - time.monotonic()))
        if not observed.worker_loss_ready(watchdog, active, dead_at, time.monotonic()):
            report["outcome"] = "inconclusive"
        else:
            checks["worker_loss_grace"] = True
        phase = "systemd_kill"
        watchdog.tick()
        checks["real_systemd_cgroup_kill"] = (
            watchdog.active is None and show(SERVICE, "ActiveState") != "active"
        )
        phase = "verify_cleanup"
        checks["service_main_and_test_client_dead"] = wait_for(
            lambda: (
                observed.gone(service_pid, service_start[0])
                and all(observed.gone(client.pid, client.start) for client in tracked)
            ),
            10,
        )
        checks["shields_up_after_service_stop"] = wait_for(
            lambda: shield_state() is True, 15
        )
        with closing(sqlite3.connect(db_path)) as db:
            row = db.execute(
                "SELECT status,result FROM runs WHERE id=?", (run,)
            ).fetchone()
        checks["isolated_row_uncertain_null"] = row == ("uncertain", None)
        while time.monotonic() <= killed_at + 30:
            if runtime.inspect_owned(resource, token) is None:
                checks["exact_resource_absent_within_30s"] = True
                break
            time.sleep(0.1)
        checks["no_late_recreation"] = (
            checks["exact_resource_absent_within_30s"]
            and wait_for(lambda: runtime.inspect_owned(resource, token) is not None, 5)
            is False
        )
        checks["namespace_and_cgroup_drained"] = (
            runtime.all_resources() == [] and observed.cgroup_tree_drained(SERVICE)
        )
    except Exception as exc:  # noqa: BLE001 - sanitized failure summary.
        report["error_kind"] = type(exc).__name__
        report["error_phase"] = phase
    finally:
        worker_gone = exact_clean = False
        try:
            worker_gone = worker is None or (
                observed is not None
                and observed.cleanup_test_worker(worker, worker_start)
            )
        except Exception as exc:  # noqa: BLE001 - continue exact cleanup.
            report["error_kind"] = report["error_kind"] or type(exc).__name__
        try:
            exact_clean = runtime is not None and (
                observed.cleanup_exact(runtime, resource, token, tracked)
                if resource is not None and token is not None and observed is not None
                else runtime.all_resources() == []
            )
        except Exception as exc:  # noqa: BLE001 - leave host protected.
            report["error_kind"] = report["error_kind"] or type(exc).__name__
        if observed is not None:
            observed.close_clients(tracked)
        checks["cleanup_confirmed"] = bool(worker_gone and exact_clean)
        if baseline is not None:
            try:
                checks["live_counts_unchanged"] = live_counts() == baseline
            except Exception as exc:  # noqa: BLE001 - do not restore on uncertainty.
                report["error_kind"] = report["error_kind"] or type(exc).__name__
        if root is not None:
            if checks["cleanup_confirmed"]:
                try:
                    shutil.rmtree(root)
                except OSError:
                    report["evidence_retained"] = True
                    checks["cleanup_confirmed"] = False
            else:
                report["evidence_retained"] = True
        if (
            shield_changed
            and checks["cleanup_confirmed"]
            and checks["live_counts_unchanged"]
        ):
            try:
                if runtime.all_resources() == [] and service_active(WATCHDOG):
                    state = show(SERVICE, "ActiveState")
                    current = (
                        observed.process_identity(service_pid)
                        if observed is not None and service_pid is not None
                        else None
                    )
                    if (
                        state == "active"
                        and current is not None
                        and current[0] == service_start[0]
                    ):
                        command("/usr/bin/tailscale", "set", "--shields-up=false")
                    elif wait_for(
                        lambda: show(SERVICE, "ActiveState") in ("inactive", "failed"),
                        30,
                    ):
                        command("/usr/bin/systemctl", "start", SERVICE, timeout=380)
                    checks["service_and_watchdog_restored"] = (
                        service_active(SERVICE)
                        and service_active(WATCHDOG)
                        and shield_state() is False
                    )
            except Exception as exc:  # noqa: BLE001 - fail closed with Shields Up.
                report["error_kind"] = report["error_kind"] or type(exc).__name__
    report["passed"] = report["error_kind"] is None and all(checks.values())
    if report["passed"]:
        report["outcome"] = "passed"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True, type=Path)
    parser.add_argument("--worker", nargs=2, metavar=("RESOURCE", "TOKEN"))
    args = parser.parse_args()
    if args.worker:
        if sys.stdin.buffer.readline() != b"go\n":
            return 2
        return load_observed().sleeper(args.release, *args.worker)
    report = run_check(args.release)
    print(
        "PRISM_SYSTEMD_CGROUP_RESULT " + json.dumps(report, sort_keys=True), flush=True
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
