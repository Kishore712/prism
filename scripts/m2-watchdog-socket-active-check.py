"""Test-only live-DB Jobs/socket fault check with a bounded guest delay.

The installed v7 service is never modified. A root-private copy of its verified
Python source changes only the synthetic JSON fixture to sleep in the guest.
The copied Jobs/worker/controller code and installed watchdog socket are real.
One synthetic project/chat/run remains in the live database for audit. This
trusted root test does not exercise OIDC, browser authorization, or the
production JSON program hash.
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
import traceback
import uuid
from contextlib import closing
from pathlib import Path

PARENT = Path("/var/lib/prism/identity-pilot/checks/m2-watchdog-socket-jobs-check.py")
PARENT_SHA256 = "c6acc70cd82f6b6bbaff35f71a6c044c2fc963c8afde6df3cb99ced727c11b7e"
LAB_PROJECT = "m2-watchdog-lab"
LAB_OWNER = "test-only-root-driver"
LAB_FILE = "config/release.json"
LAB_JSON = '{"release":"synthetic-watchdog-check","approved":false}\n'
ORIGINAL_FIXTURE_SHA256 = (
    "aa36c641a81f4032549d5890a8d1e50899ec0076c23677a31c038a7bf55e3480"
)
FIXTURE_PATH = Path("src/prism/fixtures/json_check.py")
REQUIRED = (
    "pinned_v7_units_and_helpers",
    "live_idle_and_namespace_empty",
    "installed_watchdog_socket_healthy",
    "lab_project_scope_bounded",
    "maintenance_shields_up",
    "driver_in_real_service_cgroup",
    "test_only_source_diff_exact",
    "canonical_lab_jobs_row_created",
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
SHIELD_REQUIRED = tuple(
    name for name in REQUIRED if name != "maintenance_shields_up"
) + ("serving_shields_down_at_fault", "watchdog_close_raised_shields")


def parent_module():
    info = PARENT.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o700
        or hashlib.sha256(PARENT.read_bytes()).hexdigest() != PARENT_SHA256
    ):
        raise RuntimeError("Pinned root-only parent harness differs")
    spec = importlib.util.spec_from_file_location("prism_socket_jobs_pinned", PARENT)
    if spec is None or spec.loader is None:
        raise RuntimeError("Pinned parent harness cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def delayed_fixture(original: str) -> str:
    if hashlib.sha256(original.encode()).hexdigest() != ORIGINAL_FIXTURE_SHA256:
        raise RuntimeError("The v7 JSON fixture differs")
    import_marker = "import sys\n"
    entry_marker = (
        "    if len(sys.argv) != 2 or len(sys.argv[1]) > 100 * 1024:\n"
        "        return 2\n"
    )
    if original.count(import_marker) != 1 or original.count(entry_marker) != 1:
        raise RuntimeError("The v7 JSON fixture shape differs")
    variant = original.replace(import_marker, import_marker + "import time\n", 1)
    return variant.replace(entry_marker, entry_marker + "    time.sleep(20)\n", 1)


def test_source_copy(release: Path, root: Path) -> Path:
    """Copy only manifest-pinned v7 package files; alter one fixed fixture."""
    manifest_path = (
        release.parent.parent
        / "service-updates"
        / release.name.removeprefix("service-")
        / "manifest.json"
    )
    hashes = json.loads(manifest_path.read_text(encoding="utf-8"))["file_sha256"]
    copied = []
    source_root = root / "src"
    for relative, expected in hashes.items():
        if not relative.startswith("src/prism/"):
            continue
        source = release / relative
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise RuntimeError("Pinned v7 package source differs")
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        copied.append(relative)
    if len(copied) < 20 or str(FIXTURE_PATH) not in copied:
        raise RuntimeError("Pinned v7 package inventory is incomplete")
    target = root / FIXTURE_PATH
    target.write_text(
        delayed_fixture(target.read_text(encoding="utf-8")), encoding="utf-8"
    )
    if not source_root.joinpath("prism/__init__.py").is_file():
        raise RuntimeError("Test package has no entry point")
    return source_root


def lab_counts(parent) -> dict:
    projects = parent.db_read(
        "SELECT owner FROM owner_projects WHERE id=?", (LAB_PROJECT,)
    )
    return {
        "projects": len(projects),
        "owner_matches": not projects or projects[0]["owner"] == LAB_OWNER,
        "chats": parent.db_read("SELECT count(*) AS n FROM owner_chats")[0]["n"],
        "lab_chats": parent.db_read(
            "SELECT count(*) AS n FROM owner_chats WHERE project=?", (LAB_PROJECT,)
        )[0]["n"],
    }


def remove_empty_lab_project(parent) -> None:
    """Undo only a project-only setup failure before a chat or run existed."""
    with closing(sqlite3.connect(parent.DB, timeout=5)) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT owner FROM owner_projects WHERE id=?", (LAB_PROJECT,)
        ).fetchone()
        if row is None:
            db.commit()
            return
        if (
            row != (LAB_OWNER,)
            or db.execute(
                "SELECT 1 FROM owner_revisions WHERE project=? UNION "
                "SELECT 1 FROM owner_chats WHERE project=?",
                (LAB_PROJECT, LAB_PROJECT),
            ).fetchone()
        ):
            raise RuntimeError("Test-only project has dependent state")
        db.execute(
            "DELETE FROM owner_projects WHERE id=? AND owner=?",
            (LAB_PROJECT, LAB_OWNER),
        )
        db.commit()


def driver(release: Path, key: str, status_path: Path) -> int:
    if sys.stdin.buffer.readline() != b"go\n":
        return 2
    stage = "verify_v7"
    try:
        parent = parent_module()
        base = parent.pinned_module(
            parent.BASE, parent.BASE_SHA256, "prism_active_base"
        )
        observed = base.load_observed()
        observed.verified_modules(release)
        stage = "build_test_source"
        root = status_path.parent
        source_root = test_source_copy(release, root)
        project_file = root / "project" / LAB_FILE
        project_file.parent.mkdir(parents=True)
        project_file.write_text(LAB_JSON, encoding="ascii")
        # The v7 verifier imported the installed package. Discard only those
        # imports before loading the byte-matched test copy in this process.
        for name in tuple(sys.modules):
            if name == "prism" or name.startswith("prism."):
                del sys.modules[name]
        sys.path.insert(0, str(source_root))
        from prism.jobs import Jobs
        from prism.owner import OwnerIdentity, OwnerWorkspace
        from prism.projects import ProjectSource
        from prism.reference_runtime import RuntimeRegistry
        from prism.sharing import Store

        package = importlib.util.find_spec("prism")
        if (
            package is None
            or Path(package.origin).resolve() != source_root / "prism/__init__.py"
        ):
            raise RuntimeError("Test source import did not resolve to verified copy")
        stage = "activate_runtime"
        source = ProjectSource(
            project_file.parents[1],
            project_id=LAB_PROJECT,
            title="Synthetic watchdog diagnostic",
            names=(LAB_FILE,),
            action="json-check",
            action_profile="reference-linux",
        )
        frozen = source.freeze(
            [LAB_FILE], "Synthetic host-watchdog test only.", "verify"
        )
        if (
            frozen["action"]["id"] != "json-check"
            or frozen["action"]["profile"] != "reference-linux"
        ):
            raise RuntimeError("Test action is not the fixed reference JSON check")
        registry = RuntimeRegistry(profile="reference-linux")
        registry.activate()
        store = Store(parent.DB)
        workspace = OwnerWorkspace(store)
        jobs = Jobs(workspace, registry=registry, recover=False)
        jobs.watchdog.health()
        scope = lab_counts(parent)
        if (
            not scope["owner_matches"]
            or scope["projects"] > 1
            or scope["lab_chats"] > 3
        ):
            raise RuntimeError("Test-only project scope differs")
        stage = "create_lab_chat"
        workspace.register_project(
            source,
            project=LAB_PROJECT,
            owner=LAB_OWNER,
            title="Synthetic watchdog diagnostic",
        )
        actor = OwnerIdentity(LAB_OWNER, LAB_PROJECT)
        chat = workspace.new_conversation(actor, files=[LAB_FILE])["id"]
        write_status(status_path, {"chat": chat, "source_exact": True})
        stage = "submit_jobs"
        jobs.submit_json_check(chat, actor, key)
        write_status(
            status_path, {"chat": chat, "submitted": True, "source_exact": True}
        )
        while True:
            time.sleep(1)
    except Exception as exc:  # noqa: BLE001 - report only diagnostic type and stage.
        previous = {}
        try:
            previous = json.loads(status_path.read_text(encoding="ascii"))
        except (FileNotFoundError, ValueError):
            pass
        previous.update(error_kind=type(exc).__name__, error_stage=stage)
        write_status(status_path, previous)
        return 2


def write_status(path: Path, value: dict) -> None:
    fd, name = tempfile.mkstemp(prefix=".status-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            stream.write(json.dumps(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def status_file(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="ascii"))
    except (FileNotFoundError, ValueError):
        return {}


def children_all_threads(pid: int, *, proc_root: Path = Path("/proc")) -> list[int]:
    """Find children spawned from any thread in an exact process."""
    task_root = proc_root / str(pid) / "task"
    try:
        threads = task_root.iterdir()
        result = set()
        for thread in threads:
            if not thread.name.isdecimal():
                continue
            try:
                raw = (thread / "children").read_text(encoding="ascii")
            except OSError:
                continue
            result.update(int(value) for value in raw.split())
        return sorted(result)
    except (OSError, ValueError):
        return []


def guard_stopped_service(base, *, faulted: bool) -> bool:
    """Fail closed if a fault left the service stopped without Shields Up."""
    try:
        state = base.show(base.SERVICE, "ActiveState")
    except Exception:  # noqa: BLE001 - unknown service state needs the guard.
        state = "unknown"
    if not faulted and state == "active":
        return False
    try:
        shield = base.shield_state()
    except Exception:  # noqa: BLE001 - unknown tailnet state needs the guard.
        shield = None
    if shield is True:
        return False
    base.command("/usr/bin/tailscale", "set", "--shields-up=true")
    return True


def preflight_only(release: Path) -> dict:
    if platform.system() != "Linux" or os.geteuid() != 0:
        raise RuntimeError("Preflight requires the root Linux test host")
    parent = parent_module()
    base = parent.pinned_module(
        parent.BASE, parent.BASE_SHA256, "prism_active_preflight_base"
    )
    observed = base.load_observed()
    observed.verified_modules(release)
    root = Path(tempfile.mkdtemp(prefix="prism-active-preflight-"))
    root.chmod(0o700)
    try:
        source_root = test_source_copy(release, root)
        for name in tuple(sys.modules):
            if name == "prism" or name.startswith("prism."):
                del sys.modules[name]
        sys.path.insert(0, str(source_root))
        from prism.jobs import Jobs  # noqa: F401 - import is the boundary check.
        from prism.owner import OwnerWorkspace  # noqa: F401 - same boundary.
        from prism.projects import ProjectSource

        package = importlib.util.find_spec("prism")
        if (
            package is None
            or Path(package.origin).resolve() != source_root / "prism/__init__.py"
        ):
            raise RuntimeError("Preflight import did not resolve to test source")
        project_file = root / "project" / LAB_FILE
        project_file.parent.mkdir(parents=True)
        project_file.write_text(LAB_JSON, encoding="ascii")
        source = ProjectSource(
            project_file.parents[1],
            project_id=LAB_PROJECT,
            title="Synthetic watchdog diagnostic",
            names=(LAB_FILE,),
            action="json-check",
            action_profile="reference-linux",
        )
        action = source.freeze(
            [LAB_FILE], "Synthetic host-watchdog test only.", "verify"
        )["action"]
        if action["id"] != "json-check" or action["profile"] != "reference-linux":
            raise RuntimeError("Preflight synthetic action differs")
        return {
            "pinned_v7_source": True,
            "test_copy_only_fixture_changed": True,
            "test_imports": True,
            "test_action": True,
            "live_database_touched": False,
            "service_or_network_changed": False,
        }
    finally:
        shutil.rmtree(root)


def run_check(release: Path, *, prove_shield_close: bool = False) -> dict:
    required = SHIELD_REQUIRED if prove_shield_close else REQUIRED
    checks = {name: False for name in required}
    report = {
        "kind": (
            "m2-watchdog-shield-transition-check"
            if prove_shield_close
            else "m2-watchdog-socket-active-check"
        ),
        "schema": 1,
        "test_only_source_variant": True,
        "production_json_program_tested": False,
        "oidc_browser_tested": False,
        "model_calls": 0,
        "pilot_ready": False,
        "checks": checks,
        "outcome": "failed",
        "passed": False,
        "error_kind": None,
        "error_phase": None,
        "error_line": None,
        "driver_error_kind": None,
        "driver_error_stage": None,
        "evidence_retained": False,
        "fallback_shield_applied": False,
        "observation": {
            "inspect_calls": 0,
            "max_inspect_milliseconds": 0,
            "resource_running_seen": False,
            "exact_client_seen": False,
            "worker_child_seen": False,
            "worker_signature_seen": False,
            "client_parent_match": False,
            "client_cgroup_match": False,
            "states": [],
        },
    }
    if (
        platform.system() != "Linux"
        or platform.machine() != "x86_64"
        or os.geteuid() != 0
        or not hasattr(os, "pidfd_open")
        or not hasattr(signal, "pidfd_send_signal")
    ):
        report["error_kind"] = "UnsupportedHost"
        return report
    parent = base = observed = host_watchdog = runtime = root = proc = None
    baseline = lab_baseline = None
    chat = run = resource = token = None
    driver_fd = worker_fd = None
    client = None
    frozen = []
    shield_changed = faulted = False
    phase = "verify"
    try:
        parent = parent_module()
        base = parent.pinned_module(
            parent.BASE, parent.BASE_SHA256, "prism_active_base_main"
        )
        observed = base.load_observed()
        host_watchdog, reference_runtime = observed.verified_modules(release)
        parent.pinned_resolver()
        main_pid = base.active_unit_identity(observed, release)
        main_identity = observed.process_identity(main_pid)
        if main_identity is None or main_identity[1] in ("Z", "X"):
            raise RuntimeError("Installed service main process differs")
        if (
            hashlib.sha256((release / FIXTURE_PATH).read_bytes()).hexdigest()
            != ORIGINAL_FIXTURE_SHA256
        ):
            raise RuntimeError("Installed v7 JSON program differs")
        checks["pinned_v7_units_and_helpers"] = True
        baseline = base.live_counts()
        lab_baseline = lab_counts(parent)
        runtime = reference_runtime.ReferenceLinuxRuntime()
        group = "/system.slice/" + base.SERVICE
        if (
            baseline["pending"]
            or baseline["runs"] >= 24
            or lab_baseline["chats"] >= 47
            or lab_baseline["lab_chats"] > 3
            or runtime.all_resources()
            or base.shield_state()
            or set(host_watchdog._group_processes(group)) != {main_pid}
        ):
            raise RuntimeError("Live service is not idle and serving")
        checks["live_idle_and_namespace_empty"] = True
        if lab_baseline["projects"] > 1 or not lab_baseline["owner_matches"]:
            raise RuntimeError("Test-only project scope differs")
        checks["lab_project_scope_bounded"] = True
        host_watchdog.WatchdogClient().health()
        info = host_watchdog.SOCKET.lstat()
        if (
            not stat.S_ISSOCK(info.st_mode)
            or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise RuntimeError("Installed watchdog socket differs")
        checks["installed_watchdog_socket_healthy"] = True
        phase = "maintenance"
        shield_changed = True
        if prove_shield_close:
            if base.shield_state() is not False:
                raise RuntimeError("Service is not currently accepting tailnet traffic")
        else:
            base.command("/usr/bin/tailscale", "set", "--shields-up=true")
            if not base.wait_for(lambda: base.shield_state() is True, 5):
                raise RuntimeError("Maintenance Shields Up not confirmed")
            checks["maintenance_shields_up"] = True
        if base.live_counts() != baseline or lab_counts(parent) != lab_baseline:
            raise RuntimeError("Live state changed during maintenance admission")
        root = Path(tempfile.mkdtemp(prefix="prism-active-socket-"))
        root.chmod(0o700)
        status_path = root / "driver-status.json"
        key = "m2-active-" + uuid.uuid4().hex
        phase = "driver"
        proc = subprocess.Popen(
            [
                str(release / "venv/bin/python"),
                str(Path(__file__).resolve()),
                "--release",
                str(release),
                "--driver",
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
        if observed.process_identity(proc.pid) is None or proc.stdin is None:
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
        while time.monotonic() < deadline:
            status = status_file(status_path)
            if status.get("chat"):
                chat = status["chat"]
                checks["test_only_source_diff_exact"] = (
                    status.get("source_exact") is True
                )
                row = parent.live_row(chat, key)
                if row is not None:
                    run, resource, token = (
                        row["id"],
                        row["runtime_resource"],
                        row["runtime_token"],
                    )
            if status.get("error_kind") or proc.poll() is not None:
                report["driver_error_kind"] = status.get("error_kind")
                report["driver_error_stage"] = status.get("error_stage")
                raise RuntimeError("Test driver exited before task submission")
            if run is not None:
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("Test-only Jobs did not create the exact run")
        if (
            not re.fullmatch(r"[0-9a-f]{32}", run)
            or resource != "prism-m23-run-" + run
            or not isinstance(token, str)
            or not re.fullmatch(r"[0-9a-f]{32}", token)
            or row["runtime_profile"] != "reference-linux"
        ):
            raise RuntimeError("Jobs reserved a different runtime identity")
        checks["canonical_lab_jobs_row_created"] = True
        phase = "observe_running"
        deadline = time.monotonic() + 15
        observation = report["observation"]
        while time.monotonic() < deadline:
            row = parent.live_row(chat, key)
            if row is None or row["status"] in (
                "completed",
                "failed",
                "cancelled",
                "uncertain",
            ):
                report["outcome"] = "inconclusive"
                return report
            started_inspect = time.monotonic()
            try:
                info = runtime.inspect_owned(resource, token)
            except reference_runtime.EngineError as exc:
                if str(exc) != "The named reference resource could not be inspected.":
                    raise
                info = None
            observation["inspect_calls"] += 1
            observation["max_inspect_milliseconds"] = max(
                observation["max_inspect_milliseconds"],
                round((time.monotonic() - started_inspect) * 1000),
            )
            state = info.get("State", {}) if info else {}
            name = state.get("Status", "absent") if isinstance(state, dict) else "other"
            if name not in ("absent", "created", "running", "exited", "stopped"):
                name = "other"
            if name not in observation["states"]:
                observation["states"].append(name)
            running = isinstance(state, dict) and (
                state.get("Status") == "running" or state.get("Running") is True
            )
            if running:
                observation["resource_running_seen"] = True
                clients = observed.discover_exact_clients(resource, token)
                children = children_all_threads(proc.pid)
                observation["exact_client_seen"] |= len(clients) == 1
                observation["worker_child_seen"] |= len(children) == 1
                worker_exact = len(children) == 1 and parent.process_exact(
                    observed, children[0], resource, token, group
                )
                observation["worker_signature_seen"] |= worker_exact
                child_match = (
                    len(clients) == 1
                    and len(children) == 1
                    and children_all_threads(children[0]) == [clients[0].pid]
                )
                observation["client_parent_match"] |= child_match
                client_group_match = (
                    len(clients) == 1 and host_watchdog._cgroup(clients[0].pid) == group
                )
                observation["client_cgroup_match"] |= client_group_match
                if (
                    len(clients) == 1
                    and len(children) == 1
                    and worker_exact
                    and child_match
                    and client_group_match
                ):
                    client = clients[0]
                    worker_fd = os.pidfd_open(children[0], 0)
                    checks["exact_owned_resource_running"] = True
                    checks["worker_and_client_in_service_cgroup"] = True
                    break
                observed.close_clients(clients)
            time.sleep(0.02)
        if not checks["exact_owned_resource_running"]:
            report["outcome"] = "inconclusive"
            return report
        phase = "fault"
        if parent.live_row(chat, key)[
            "status"
        ] != "running" or not observed.expiry_ready(runtime, resource, token, [client]):
            report["outcome"] = "inconclusive"
            return report
        if prove_shield_close and base.shield_state() is not False:
            report["outcome"] = "inconclusive"
            return report
        parent.signal_exact(client.pidfd, signal.SIGSTOP)
        frozen.append(client.pidfd)
        parent.signal_exact(worker_fd, signal.SIGSTOP)
        frozen.append(worker_fd)
        if not observed.owned_running(runtime, resource, token):
            report["outcome"] = "inconclusive"
            return report
        if prove_shield_close:
            checks["serving_shields_down_at_fault"] = base.shield_state() is False
            if not checks["serving_shields_down_at_fault"]:
                report["outcome"] = "inconclusive"
                return report
        fault_at = time.monotonic()
        parent.signal_exact(driver_fd, signal.SIGSTOP)
        frozen.append(driver_fd)
        faulted = True
        checks["lease_fault_applied_after_observation"] = True
        phase = "watchdog_expiry"
        if not base.wait_for(lambda: observed.gone(main_pid, main_identity[0]), 25):
            raise RuntimeError("Installed watchdog did not stop service main process")
        checks["installed_watchdog_stopped_service"] = True
        if prove_shield_close:
            checks["watchdog_close_raised_shields"] = base.wait_for(
                lambda: base.shield_state() is True, 25
            )
            if not checks["watchdog_close_raised_shields"]:
                raise RuntimeError("Service close hook did not enable Shields Up")
        while time.monotonic() < fault_at + 30:
            if runtime.inspect_owned(resource, token) is None:
                checks["exact_resource_absent_within_30s"] = True
                break
            time.sleep(0.1)
        checks["run_uncertain_null"] = base.wait_for(
            lambda: parent.uncertain_and_null(chat, key), 10
        )
        checks["no_late_recreation"] = checks[
            "exact_resource_absent_within_30s"
        ] and not base.wait_for(
            lambda: runtime.inspect_owned(resource, token) is not None, 5
        )
    except Exception as exc:  # noqa: BLE001 - only exception type enters summary.
        report["error_kind"] = type(exc).__name__
        report["error_phase"] = phase
        report["error_line"] = next(
            (
                frame.lineno
                for frame in reversed(traceback.extract_tb(exc.__traceback__))
                if frame.filename == __file__
            ),
            None,
        )
    finally:
        if prove_shield_close and shield_changed and base is not None:
            try:
                report["fallback_shield_applied"] = guard_stopped_service(
                    base, faulted=faulted
                )
            except Exception as exc:  # noqa: BLE001 - preserve the failure result.
                report["error_kind"] = report["error_kind"] or type(exc).__name__
        for fd in reversed(frozen):
            try:
                parent.signal_exact(fd, signal.SIGCONT)
            except OSError:
                pass
        if observed is not None and client is not None:
            observed.close_clients([client])
        for fd in (worker_fd, driver_fd):
            if fd is not None:
                os.close(fd)
        if run is None and proc is not None and proc.poll() is None:
            proc.terminate()
        if (
            run is not None
            and parent is not None
            and base is not None
            and runtime is not None
        ):
            try:
                row = parent.live_row(chat, key)
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
                        base.wait_for(lambda: parent.uncertain_and_null(chat, key), 12)
                    else:
                        base.wait_for(
                            lambda: (
                                parent.live_row(chat, key)["status"]
                                in ("completed", "failed", "cancelled", "uncertain")
                            ),
                            45,
                        )
                row = parent.live_row(chat, key)
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
                    inspected = parent.resolver_action("inspect", run)
                    resolved = parent.resolver_action("resolve", run)
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
        if (
            parent is not None
            and chat is None
            and run is None
            and lab_baseline is not None
            and lab_baseline["projects"] == 0
        ):
            try:
                remove_empty_lab_project(parent)
            except Exception as exc:  # noqa: BLE001 - keep private service shielded.
                report["error_kind"] = report["error_kind"] or type(exc).__name__
        if baseline is not None and base is not None:
            try:
                current = base.live_counts()
                current_lab = lab_counts(parent)
                checks["live_state_scoped_to_one_synthetic_run"] = (
                    current["runs"] == baseline["runs"] + (1 if run else 0)
                    and current["pending"] == 0
                    and current["grants"] == baseline["grants"]
                    and current["model_dispatches"] == baseline["model_dispatches"]
                    and current_lab["projects"]
                    == lab_baseline["projects"]
                    + (1 if chat and lab_baseline["projects"] == 0 else 0)
                    and current_lab["chats"]
                    == lab_baseline["chats"] + (1 if chat else 0)
                    and current_lab["lab_chats"]
                    == lab_baseline["lab_chats"] + (1 if chat else 0)
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
                        if not prove_shield_close:
                            base.command(
                                "/usr/bin/tailscale", "set", "--shields-up=false"
                            )
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
    report["passed"] = (
        report["error_kind"] is None
        and not report["fallback_shield_applied"]
        and all(checks.values())
    )
    if report["passed"]:
        report["outcome"] = "passed"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True, type=Path)
    parser.add_argument("--driver", nargs=2, metavar=("KEY", "STATUS_PATH"))
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--prove-shield-close", action="store_true")
    args = parser.parse_args()
    if args.preflight_only:
        if args.driver:
            parser.error("Choose one test mode")
        print(
            "PRISM_WATCHDOG_ACTIVE_PREFLIGHT "
            + json.dumps(preflight_only(args.release), sort_keys=True)
        )
        return 0
    if args.driver:
        return driver(args.release, args.driver[0], Path(args.driver[1]))
    report = run_check(args.release, prove_shield_close=args.prove_shield_close)
    print(
        "PRISM_WATCHDOG_ACTIVE_RESULT " + json.dumps(report, sort_keys=True), flush=True
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
