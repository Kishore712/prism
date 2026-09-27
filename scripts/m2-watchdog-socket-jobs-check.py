"""One bounded live-DB Jobs/watchdog-socket fault rehearsal on the private host.

Run as root only on the dedicated v7 Linux/Kata host during a maintenance
window. Uses one existing owner chat whose immutable files exactly match the
public synthetic Document fixture. The trusted test driver joins the installed
identity-service cgroup and submits the real fixed JSON action through Jobs;
the installed watchdog socket receives the real register/attach/renew calls.
This is not an OIDC/browser test. One live synthetic run and audit events remain.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import re
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

BASE = Path("/var/lib/prism/identity-pilot/checks/m2-systemd-cgroup-check.py")
BASE_SHA256 = "ad47761d678762d4ce036e5be402348ac7de83464ab2e3574fb024afe406b2e8"
DB = Path("/var/lib/prism-identity/service-state/demo.sqlite")
RESOLVER = Path("/usr/local/libexec/prism-identity-uncertain-resolution.py")
RESOLVER_SHA256 = "85bb030f23189398f0b7956367c440cc04938670c92ffc7b8fa709a0dfd9a97c"
PROJECT = "document-handoff"
FIXTURE = {
    "README.md": "7b515d4b3a25719f20fea966fa78b50cdaf6c6ac5f048c45e1c65c7474e5af44",
    "config/release.json": "751144f3e65c4499341d3b56540d35bde8e5d0221956427a45588dfa5371df4b",
    "docs/release.md": "7184635a68f82316ffd39960d60905a8b9c6e9092a273e276198ce8100f7ca79",
}
REQUIRED = (
    "pinned_v7_units_and_helpers",
    "synthetic_owner_chat_exact",
    "live_idle_and_namespace_empty",
    "installed_watchdog_socket_healthy",
    "maintenance_shields_up",
    "driver_in_real_service_cgroup",
    "real_jobs_row_created",
    "exact_owned_resource_running",
    "worker_and_client_in_service_cgroup",
    "lease_fault_applied_after_observation",
    "installed_watchdog_stopped_service",
    "exact_resource_absent_within_30s",
    "run_uncertain_null",
    "no_late_recreation",
    "resolver_inspected_and_resolved",
    "final_namespace_and_cgroup_drained",
    "live_state_scoped_to_one_synthetic_run",
    "service_and_watchdog_restored",
)


def pinned_module(path: Path, expected: str, name: str):
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o700
        or hashlib.sha256(path.read_bytes()).hexdigest() != expected
    ):
        raise RuntimeError("Pinned root-only helper differs")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Pinned helper cannot be imported")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pinned_resolver() -> None:
    info = RESOLVER.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o700
        or hashlib.sha256(RESOLVER.read_bytes()).hexdigest() != RESOLVER_SHA256
    ):
        raise RuntimeError("Pinned uncertain-run resolver differs")


def db_read(query: str, params: tuple = ()):
    with closing(sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=3)) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(query, params)]


def chat_candidates() -> list[str]:
    rows = db_read(
        "SELECT c.id,c.owner,c.project,r.manifest,c.created FROM owner_chats c "
        "JOIN owner_revisions r ON r.id=c.revision WHERE c.project=? ORDER BY c.created,c.id",
        (PROJECT,),
    )
    selected = []
    for row in rows:
        manifest = json.loads(row["manifest"])
        files = manifest.get("files")
        action = manifest.get("action")
        if (
            manifest.get("mode") != "verify"
            or not isinstance(files, list)
            or len(files) != len(FIXTURE)
            or any(not isinstance(item, dict) for item in files)
            or {item.get("name"): item.get("sha256") for item in files} != FIXTURE
            or not isinstance(action, dict)
            or action.get("id") != "json-check"
            or action.get("profile") != "reference-linux"
        ):
            continue
        used = db_read("SELECT count(*) AS n FROM runs WHERE session=?", (row["id"],))[
            0
        ]["n"]
        if used < 6:
            selected.append(row["id"])
    return selected


def live_row(chat: str, key: str) -> dict | None:
    rows = db_read(
        "SELECT id,status,result,runtime_profile,runtime_resource,runtime_token "
        "FROM runs WHERE session=? AND request_key=?",
        (chat, key),
    )
    if len(rows) > 1:
        raise RuntimeError("Synthetic run identity is ambiguous")
    return rows[0] if rows else None


def driver_status(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def driver(release: Path, chat: str, key: str, status_path: Path) -> int:
    if sys.stdin.buffer.readline() != b"go\n":
        return 2
    stage = "load_pinned_source"
    try:
        base = pinned_module(BASE, BASE_SHA256, "prism_cgroup_check_pinned_driver")
        observed = base.load_observed()
        observed.verified_modules(release)
        sys.path.insert(0, str(release / "src"))
        stage = "select_owner_chat"
        if chat not in chat_candidates():
            raise RuntimeError("Approved synthetic owner chat changed")
        from prism.jobs import Jobs
        from prism.owner import OwnerIdentity, OwnerWorkspace
        from prism.reference_runtime import RuntimeRegistry
        from prism.sharing import Store

        owner = db_read(
            "SELECT owner FROM owner_chats WHERE id=? AND project=?", (chat, PROJECT)
        )
        if len(owner) != 1:
            raise RuntimeError("Synthetic owner scope is unavailable")
        stage = "initialize_workspace"
        store = Store(DB)
        workspace = OwnerWorkspace(store)
        workspace.sources[PROJECT] = object()  # Exact immutable chat is checked above.
        stage = "activate_runtime"
        registry = RuntimeRegistry(profile="reference-linux")
        registry.activate()
        stage = "submit_jobs"
        jobs = Jobs(workspace, registry=registry, recover=False)
        jobs.submit_json_check(chat, OwnerIdentity(owner[0]["owner"], PROJECT), key)
        status_path.write_text('{"submitted":true}\n', encoding="ascii")
        status_path.chmod(0o600)
        while True:
            time.sleep(1)
    except Exception as exc:  # noqa: BLE001 - only the error type is retained.
        status_path.write_text(
            json.dumps({"error_kind": type(exc).__name__, "error_stage": stage}),
            encoding="ascii",
        )
        status_path.chmod(0o600)
        return 2


def process_exact(observed, pid: int, resource: str, token: str, group: str) -> bool:
    identity = observed.process_identity(pid)
    if identity is None or identity[1] in ("Z", "X"):
        return False
    try:
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        cgroup = Path(f"/proc/{pid}/cgroup").read_text(encoding="ascii").strip()
    except OSError:
        return False
    return (
        b"reference-linux-local" in argv
        and resource.encode() in argv
        and token.encode() in argv
        and cgroup == "0::" + group
    )


def signal_exact(pidfd: int, signum: int) -> None:
    signal.pidfd_send_signal(pidfd, signum, None, 0)


def uncertain_and_null(chat: str, key: str) -> bool:
    row = live_row(chat, key)
    return row is not None and row["status"] == "uncertain" and row["result"] is None


def resolver_action(action: str, run: str) -> dict:
    completed = subprocess.run(
        ["/usr/bin/python3", str(RESOLVER), action, run],
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LC_ALL": "C"},
        capture_output=True,
        timeout=30,
        check=True,
    )
    result = json.loads(completed.stdout)
    if result.get("run") != run or result.get("ready") is not True:
        raise RuntimeError("Exact run resolver response differs")
    return result


def run_check(release: Path) -> dict:
    checks = {name: False for name in REQUIRED}
    report = {
        "kind": "m2-watchdog-socket-jobs-check",
        "schema": 1,
        "test_only": True,
        "oidc_browser_tested": False,
        "model_calls": 0,
        "pilot_ready": False,
        "checks": checks,
        "outcome": "failed",
        "error_kind": None,
        "error_phase": None,
        "driver_error_kind": None,
        "driver_error_stage": None,
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
    base = observed = host_watchdog = runtime = root = proc = None
    chat = key = run = resource = token = None
    driver_fd = worker_fd = None
    client = None
    frozen = []
    baseline = None
    shield_changed = False
    faulted = False
    phase = "verify"
    try:
        base = pinned_module(BASE, BASE_SHA256, "prism_cgroup_check_pinned")
        observed = base.load_observed()
        host_watchdog, reference_runtime = observed.verified_modules(release)
        pinned_resolver()
        main_pid = base.active_unit_identity(observed, release)
        main_identity = observed.process_identity(main_pid)
        if main_identity is None or main_identity[1] in ("Z", "X"):
            raise RuntimeError("Installed service main process differs")
        checks["pinned_v7_units_and_helpers"] = True
        baseline = base.live_counts()
        runtime = reference_runtime.ReferenceLinuxRuntime()
        if (
            baseline["pending"]
            or baseline["runs"] >= 24
            or runtime.all_resources()
            or base.shield_state()
        ):
            raise RuntimeError("Live service is not idle and serving")
        group = "/system.slice/" + base.SERVICE
        if set(host_watchdog._group_processes(group)) != {main_pid}:
            raise RuntimeError("Identity cgroup contains unrelated work")
        checks["live_idle_and_namespace_empty"] = True
        candidates = chat_candidates()
        if not candidates:
            raise RuntimeError("Exact synthetic owner fixture is unavailable")
        chat = candidates[0]
        checks["synthetic_owner_chat_exact"] = True
        host_watchdog.WatchdogClient().health()
        socket = host_watchdog.SOCKET
        info = socket.lstat()
        if (
            not stat.S_ISSOCK(info.st_mode)
            or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise RuntimeError("Installed watchdog socket differs")
        checks["installed_watchdog_socket_healthy"] = True
        phase = "maintenance"
        shield_changed = True
        base.command("/usr/bin/tailscale", "set", "--shields-up=true")
        if not base.wait_for(lambda: base.shield_state() is True, 5):
            raise RuntimeError("Maintenance Shields Up not confirmed")
        checks["maintenance_shields_up"] = True
        if (
            base.live_counts() != baseline
            or runtime.all_resources()
            or set(host_watchdog._group_processes(group)) != {main_pid}
        ):
            raise RuntimeError("Live state changed during maintenance admission")
        root = Path(tempfile.mkdtemp(prefix="prism-watchdog-jobs-"))
        root.chmod(0o700)
        key = "m2-socket-" + uuid.uuid4().hex
        status_path = root / "driver-status.json"
        phase = "driver"
        proc = subprocess.Popen(
            [
                str(release / "venv/bin/python"),
                str(Path(__file__).resolve()),
                "--release",
                str(release),
                "--driver",
                chat,
                key,
                str(status_path),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd="/",
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
        )
        driver_fd = os.pidfd_open(proc.pid, 0)
        driver_start = observed.process_identity(proc.pid)
        if driver_start is None or driver_start[1] in ("Z", "X") or proc.stdin is None:
            raise RuntimeError("Test driver identity unavailable")
        base.CGROUP.joinpath("cgroup.procs").write_text(
            str(proc.pid) + "\n", encoding="ascii"
        )
        if host_watchdog._cgroup(proc.pid) != group:
            raise RuntimeError("Driver is outside installed service cgroup")
        checks["driver_in_real_service_cgroup"] = True
        proc.stdin.write(b"go\n")
        proc.stdin.close()
        phase = "wait_for_run"
        deadline = time.monotonic() + 25
        row = None
        while time.monotonic() < deadline:
            row = live_row(chat, key)
            if row is not None:
                break
            if proc.poll() is not None:
                driver_failure = driver_status(status_path) or {}
                report["driver_error_kind"] = driver_failure.get("error_kind")
                report["driver_error_stage"] = driver_failure.get("error_stage")
                raise RuntimeError("Test driver exited before Jobs submission")
            time.sleep(0.05)
        if row is None:
            raise RuntimeError("Jobs did not create the exact synthetic run")
        run, resource, token = row["id"], row["runtime_resource"], row["runtime_token"]
        if (
            not re.fullmatch(r"[0-9a-f]{32}", run)
            or resource != "prism-m23-run-" + run
            or not isinstance(token, str)
            or not re.fullmatch(r"[0-9a-f]{32}", token)
            or row["runtime_profile"] != "reference-linux"
        ):
            raise RuntimeError("Jobs reserved a different runtime identity")
        checks["real_jobs_row_created"] = True
        phase = "observe_running"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            row = live_row(chat, key)
            if row is None or row["status"] in (
                "completed",
                "failed",
                "cancelled",
                "uncertain",
            ):
                report["outcome"] = "inconclusive"
                return report
            try:
                running = observed.owned_running(runtime, resource, token)
            except reference_runtime.EngineError as exc:
                if str(exc) != "The named reference resource could not be inspected.":
                    raise
                running = False
            if running:
                clients = observed.discover_exact_clients(resource, token)
                children = observed.children(proc.pid)
                if (
                    len(clients) == 1
                    and len(children) == 1
                    and process_exact(observed, children[0], resource, token, group)
                    and observed.children(children[0]) == [clients[0].pid]
                    and host_watchdog._cgroup(clients[0].pid) == group
                ):
                    client = clients[0]
                    worker_pid = children[0]
                    worker_fd = os.pidfd_open(worker_pid, 0)
                    checks["exact_owned_resource_running"] = True
                    checks["worker_and_client_in_service_cgroup"] = True
                    break
                observed.close_clients(clients)
            time.sleep(0.02)
        if not checks["exact_owned_resource_running"]:
            report["outcome"] = "inconclusive"
            return report
        phase = "fault"
        if live_row(chat, key)["status"] != "running" or not observed.expiry_ready(
            runtime, resource, token, [client]
        ):
            report["outcome"] = "inconclusive"
            return report
        signal_exact(client.pidfd, signal.SIGSTOP)
        frozen.append(client.pidfd)
        signal_exact(worker_fd, signal.SIGSTOP)
        frozen.append(worker_fd)
        if not observed.owned_running(runtime, resource, token):
            report["outcome"] = "inconclusive"
            return report
        fault_at = time.monotonic()
        signal_exact(driver_fd, signal.SIGSTOP)
        frozen.append(driver_fd)
        faulted = True
        checks["lease_fault_applied_after_observation"] = True
        phase = "watchdog_expiry"
        if not base.wait_for(lambda: observed.gone(main_pid, main_identity[0]), 25):
            raise RuntimeError("Installed watchdog did not stop service main process")
        checks["installed_watchdog_stopped_service"] = True
        deadline = fault_at + 30
        while time.monotonic() < deadline:
            if runtime.inspect_owned(resource, token) is None:
                checks["exact_resource_absent_within_30s"] = True
                break
            time.sleep(0.1)
        checks["run_uncertain_null"] = base.wait_for(
            lambda: uncertain_and_null(chat, key), 10
        )
        checks["no_late_recreation"] = checks[
            "exact_resource_absent_within_30s"
        ] and not base.wait_for(
            lambda: runtime.inspect_owned(resource, token) is not None, 5
        )
    except Exception as exc:  # noqa: BLE001 - only exception type enters summary.
        report["error_kind"] = type(exc).__name__
        report["error_phase"] = phase
    finally:
        # Resume only exact test processes. If watchdog already killed them, the
        # pidfds refer to dead identities and signalling is harmlessly denied.
        for fd in reversed(frozen):
            try:
                signal_exact(fd, signal.SIGCONT)
            except OSError:
                pass
        if observed is not None and client is not None:
            observed.close_clients([client])
        for fd in (worker_fd, driver_fd):
            if fd is not None:
                os.close(fd)
        if run is None and proc is not None and proc.poll() is None:
            proc.terminate()
        # Remaining host recovery is performed only after exact state inspection
        # below. An unresolved or unknown run leaves Shields Up in place.
        if run is not None and base is not None and runtime is not None:
            try:
                row = live_row(chat, key)
                if faulted and base.show(base.SERVICE, "ActiveState") == "active":
                    base.command(
                        "/usr/bin/systemctl",
                        "kill",
                        "--signal=SIGKILL",
                        "--kill-who=all",
                        base.SERVICE,
                    )
                if row is not None and row["status"] in ("queued", "running"):
                    if faulted:
                        base.wait_for(
                            lambda: live_row(chat, key)["status"] == "uncertain", 12
                        )
                    else:
                        base.wait_for(
                            lambda: (
                                live_row(chat, key)["status"]
                                in ("completed", "failed", "cancelled", "uncertain")
                            ),
                            45,
                        )
                row = live_row(chat, key)
                if (
                    row is not None
                    and row["status"] in ("completed", "failed", "cancelled")
                    and not faulted
                    and proc is not None
                    and proc.poll() is None
                ):
                    proc.terminate()
                if (
                    row is not None
                    and row["status"] == "uncertain"
                    and base.wait_for(
                        lambda: (
                            base.show(base.SERVICE, "ActiveState")
                            in ("inactive", "failed")
                        ),
                        20,
                    )
                    and runtime.inspect_owned(resource, token) is None
                    and runtime.all_resources() == []
                    and observed.cgroup_tree_drained(base.SERVICE)
                    and base.shield_state() is True
                ):
                    inspected = resolver_action("inspect", run)
                    resolved = resolver_action("resolve", run)
                    checks["resolver_inspected_and_resolved"] = (
                        inspected.get("changed") is False
                        and resolved.get("changed") is True
                        and resolved.get("status") == "failed"
                        and resolved.get("execution_outcome") == "unknown"
                    )
                checks["final_namespace_and_cgroup_drained"] = (
                    runtime.all_resources() == []
                    and (
                        observed.cgroup_tree_drained(base.SERVICE) if faulted else True
                    )
                )
            except Exception as exc:  # noqa: BLE001 - leave service protected.
                report["error_kind"] = report["error_kind"] or type(exc).__name__
        if proc is not None:
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        if baseline is not None and base is not None:
            try:
                current = base.live_counts()
                checks["live_state_scoped_to_one_synthetic_run"] = (
                    current["runs"] == baseline["runs"] + (1 if run else 0)
                    and current["pending"] == 0
                    and current["grants"] == baseline["grants"]
                    and current["model_dispatches"] == baseline["model_dispatches"]
                )
                if (
                    shield_changed
                    and checks["live_state_scoped_to_one_synthetic_run"]
                    and runtime.all_resources() == []
                    and base.service_active(base.WATCHDOG)
                ):
                    state = base.show(base.SERVICE, "ActiveState")
                    if (
                        state == "active"
                        and not faulted
                        and (proc is None or proc.poll() is not None)
                    ):
                        base.command("/usr/bin/tailscale", "set", "--shields-up=false")
                    elif state in ("inactive", "failed") and (
                        checks["resolver_inspected_and_resolved"]
                        or (
                            run is None and checks["final_namespace_and_cgroup_drained"]
                        )
                    ):
                        base.command(
                            "/usr/bin/systemctl", "start", base.SERVICE, timeout=380
                        )
                    checks["service_and_watchdog_restored"] = (
                        base.service_active(base.SERVICE)
                        and base.service_active(base.WATCHDOG)
                        and base.shield_state() is False
                    )
            except Exception as exc:  # noqa: BLE001 - leave Shields Up on failure.
                report["error_kind"] = report["error_kind"] or type(exc).__name__
        if root is not None:
            if checks["service_and_watchdog_restored"]:
                try:
                    shutil.rmtree(root)
                except OSError as exc:
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
    parser.add_argument("--driver", nargs=3, metavar=("CHAT", "KEY", "STATUS_PATH"))
    args = parser.parse_args()
    if args.driver:
        return driver(
            args.release, args.driver[0], args.driver[1], Path(args.driver[2])
        )
    report = run_check(args.release)
    print(
        "PRISM_WATCHDOG_JOBS_RESULT " + json.dumps(report, sort_keys=True), flush=True
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
