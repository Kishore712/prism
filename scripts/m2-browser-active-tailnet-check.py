"""Root-only, bounded observer for one externally submitted recipient JSON run.

Arm this on the pinned private host before the recipient uses Chrome. The
browser operator supplies the exact session, version, and grant;
this program never submits a job. An ordinary fixed json-check may finish
before observation, in which case the result is inconclusive and no fault is
applied. Output deliberately omits identifiers, tokens, and private paths.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import signal
import sqlite3
import stat
import subprocess
import time
from contextlib import closing
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

# Installed, root-owned helper from the fifth synthetic active fault. Its hash
# and release pins are checked before its functions are used.

PINNED_ACTIVE = Path(
    "/var/lib/prism/identity-pilot/checks/m2-tailnet-down-active-check.py"
)
PINNED_ACTIVE_SHA256 = (
    "ba6e76783fc668b5a158d22f0cbd9b90cfe3813d56a1fdc60550cf8e6eea1ce1"
)
RELEASE_TRANSFER_SHA256 = (
    "47add41b5778bcd9d2a5f6408df2c8312325474952047a5b12b362005dfce7f2"
)
RELEASE_SOURCE_SHA256 = (
    "8213a7cae3bfa6a242e1d5fcd3afece9e3526894c27e633f3f29963b97ccd66f"
)
RELEASE_WATCHDOG_SHA256 = (
    "1103b8cf469e2a8c00ebbd5ab371cde4c231b5a2825a74cb8c576afddb8edd1d"
)
RELEASE_LIFECYCLE_SHA256 = (
    "5190646db23fa36be0a6216e0c67f0d98703e35d5af2a53508a87aa7d4663897"
)
OLD_TRANSFER_SHA256 = "e11ea7a88a2b029f73e2374d47f271af178ff0604febbe9a47e1b4c9259d4f27"
OLD_SOURCE_SHA256 = "fee499e336567fd4c1f9a564c3fc2983fd520eb18d33e191d5ae29b267e1e16d"
OLD_LIFECYCLE_SHA256 = (
    "79e3db10b3683bc65292f449b88798d4edb8674a6f8ea63c8cd08d8cd64c9c0f"
)
OLD_WATCHDOG_SHA256 = "f853945bd8c384101effbcfb494e368f4b880156b649456459650a045a6f0cab"
MAX_ARM_SECONDS = 30
MAX_OBSERVE_SECONDS = 12
HEX32 = re.compile(r"[0-9a-f]{32}\Z")
FAILURE_STAGES = frozenset(
    {
        "host",
        "await_external_browser_submission",
        "observe_runtime",
        "fault_edge",
        "tailnet_down",
        "observe_fail_closed",
    }
)
FAILURE_ORIGINS = frozenset(
    {
        "observe_job_termination",
        "unexpected_group_member",
        "update_candidate_exits",
        "same_container_candidates",
        "process_identity",
        "process_argv",
        "candidate_lineage",
        "candidate_command_identity",
        "safe_command_category",
        "group_gone",
        "pinned_alive",
        "inspect_owned",
        "all_resources",
        "cgroup_tree_drained",
        "read_exact_run",
        "db_read",
        "command",
        "show",
        "wait_for",
    }
)


def pinned_active():
    import hashlib
    import stat

    info = PINNED_ACTIVE.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o700
        or hashlib.sha256(PINNED_ACTIVE.read_bytes()).hexdigest()
        != PINNED_ACTIVE_SHA256
    ):
        raise RuntimeError("Pinned active-fault helper differs")
    spec = spec_from_file_location("prism_pinned_active_browser", PINNED_ACTIVE)
    if spec is None or spec.loader is None:
        raise RuntimeError("Pinned active-fault helper cannot be loaded")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configure_release_pins(active, release: Path) -> None:
    """Adapt the verified older diagnostic helper to the reviewed current release."""
    if (
        active.TRANSFER_SHA256 != OLD_TRANSFER_SHA256
        or active.SOURCE_SHA256 != OLD_SOURCE_SHA256
        or active.WATCHDOG_SHA256 != OLD_WATCHDOG_SHA256
        or active.LIFECYCLE_SHA256 != OLD_LIFECYCLE_SHA256
    ):
        raise RuntimeError("Verified helper release pin baseline differs")
    if release.name != "service-" + RELEASE_TRANSFER_SHA256:
        raise RuntimeError("Current reviewed release path required")
    active.TRANSFER_SHA256 = RELEASE_TRANSFER_SHA256
    active.SOURCE_SHA256 = RELEASE_SOURCE_SHA256
    active.WATCHDOG_SHA256 = RELEASE_WATCHDOG_SHA256
    active.LIFECYCLE_SHA256 = RELEASE_LIFECYCLE_SHA256


def configure_resolver_pins(resolver, release: Path) -> None:
    if (
        resolver.TRANSFER != OLD_TRANSFER_SHA256
        or resolver.SOURCE != OLD_SOURCE_SHA256
        or resolver.LIFECYCLE_SHA256 != OLD_LIFECYCLE_SHA256
        or resolver.UPDATE
        != "/var/lib/prism/identity-pilot/service-updates/"
        + OLD_TRANSFER_SHA256
        + "/installed.json"
        or resolver.RELEASE
        != "/var/lib/prism/identity-pilot/app-releases/service-" + OLD_TRANSFER_SHA256
        or release.name != "service-" + RELEASE_TRANSFER_SHA256
    ):
        raise RuntimeError("Verified resolver release pin baseline differs")
    resolver.TRANSFER = RELEASE_TRANSFER_SHA256
    resolver.SOURCE = RELEASE_SOURCE_SHA256
    resolver.LIFECYCLE_SHA256 = RELEASE_LIFECYCLE_SHA256
    resolver.UPDATE = (
        "/var/lib/prism/identity-pilot/service-updates/"
        + RELEASE_TRANSFER_SHA256
        + "/installed.json"
    )
    resolver.RELEASE = str(release)


def current_resolver(active, release: Path):
    """Load only the root-owned v8 resolver and retarget its reviewed pins in memory."""
    import hashlib

    path = active.RESOLVER
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o700
        or hashlib.sha256(path.read_bytes()).hexdigest() != active.RESOLVER_SHA256
    ):
        raise RuntimeError("Pinned root-only v8 resolver differs")
    spec = spec_from_file_location("prism_current_root_resolver", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Pinned root-only v8 resolver cannot be loaded")
    resolver = module_from_spec(spec)
    spec.loader.exec_module(resolver)
    configure_resolver_pins(resolver, release)
    return resolver


def inspect_and_resolve_current(resolver, run_id: str) -> bool:
    inspected = resolver.execute("inspect", run_id)
    if inspected != {
        "run": run_id,
        "ready": True,
        "action": "inspect",
        "changed": False,
    }:
        return False
    resolved = resolver.execute("resolve", run_id)
    return resolved == {
        "run": run_id,
        "ready": True,
        "action": "resolve",
        "changed": True,
        "status": "failed",
        "execution_outcome": "unknown",
    }


def ready_to_restart_service(base, observed, runtime, active, resource, token, group):
    """Recheck exact runtime and process absence immediately before serving."""
    return (
        runtime.all_resources() == []
        and runtime.inspect_owned(resource, token) is None
        and observed.cgroup_tree_drained(base.SERVICE)
        and not active.same_container_candidates(group["container_id"])
    )


def inspect_during_creation(runtime, reference_runtime, resource, token):
    """Treat only the known creation-window inspect race as not yet visible."""
    try:
        return runtime.inspect_owned(resource, token)
    except reference_runtime.EngineError as exc:
        if str(exc) != "The named reference resource could not be inspected.":
            raise
        return None


def tailnet_daemon_absent(
    base, observed, *, net_root: Path = Path("/sys/class/net")
) -> bool:
    """Recognize a closed daemon without trusting its stale CLI status."""
    if not net_root.is_dir() or net_root.is_symlink():
        return False
    return (
        base.show("tailscaled.service", "ActiveState") in ("inactive", "failed")
        and base.show("tailscaled.service", "MainPID") == "0"
        and base.show("tailscaled.service", "ControlPID") == "0"
        and observed.cgroup_tree_drained("tailscaled.service")
        and not os.path.lexists(net_root / "tailscale0")
    )


def ensure_tailnet_closed(base, observed, active) -> str:
    try:
        if tailnet_daemon_absent(base, observed):
            return "daemon_absent_verified"
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        # A failed observation grants no exception to the strict close helper.
        pass
    active.force_tailnet_closed(base)
    return "pinned_helper_confirmed"


def close_after_recovery_error(base, observed, active, report) -> None:
    """Keep closure attempts independent when the normal fallback itself fails."""
    try:
        active.fail_closed_after_unstable_recovery(
            base,
            service_drained=lambda: observed.cgroup_tree_drained(base.SERVICE),
        )
    except Exception as close_exc:  # noqa: BLE001 - report category only.
        report["closure_error_kind"] = type(close_exc).__name__
        try:
            report["emergency_tailnet_close"] = ensure_tailnet_closed(
                base, observed, active
            )
        except Exception as force_exc:  # noqa: BLE001 - report category only.
            report["emergency_tailnet_close_error_kind"] = type(force_exc).__name__
    report["outcome"] = "failed"


def watchdog_stopped_and_drained(base, observed) -> bool:
    return (
        base.show(base.WATCHDOG, "ActiveState") in ("inactive", "failed")
        and base.show(base.WATCHDOG, "MainPID") == "0"
        and base.show(base.WATCHDOG, "ControlPID") == "0"
        and observed.cgroup_tree_drained(base.WATCHDOG)
    )


def recover_tailnet_after_close_failure(base, observed, active, host_watchdog):
    """Recover behind Shields Up after an ExecStopPost failure, or refuse."""
    try:
        base.command("/usr/bin/systemctl", "stop", base.WATCHDOG, timeout=300)
    except subprocess.CalledProcessError:
        # A failed close hook can make systemctl return nonzero after the unit
        # actually stopped. Trust only the independent unit/cgroup observation.
        pass
    if not base.wait_for(lambda: watchdog_stopped_and_drained(base, observed), 10):
        raise RuntimeError("Watchdog unit or cgroup did not stop")
    if not observed.cgroup_tree_drained(base.SERVICE):
        raise RuntimeError("Service cgroup is not drained")
    base.command("/usr/bin/systemctl", "reset-failed", "tailscaled.service")
    base.command("/usr/bin/systemctl", "start", "tailscaled.service", timeout=60)
    if not base.service_active("tailscaled.service"):
        raise RuntimeError("Tailscaled did not start")
    if not observed.cgroup_tree_drained(base.SERVICE):
        raise RuntimeError("Service cgroup changed before tailnet up")
    shielded, unshielded = active.offline_tailnet_prefs(base)
    if unshielded:
        base.command("/usr/bin/tailscale", "set", "--shields-up=true")
        shielded, _ = active.offline_tailnet_prefs(base)
    if not shielded:
        raise RuntimeError("Offline Shields Up is not verified")
    if not watchdog_stopped_and_drained(base, observed):
        raise RuntimeError("Watchdog restarted before tailnet up")
    try:
        base.command("/usr/bin/timeout", "25", "/usr/bin/tailscale", "up", timeout=30)
        if not base.wait_for(lambda: active.tailnet_online(base), 15):
            raise RuntimeError("Tailnet did not return online")
        if base.shield_state() is not True:
            raise RuntimeError("Online Shields Up was lost")
    except Exception:
        ensure_tailnet_closed(base, observed, active)
        raise
    if (
        not observed.cgroup_tree_drained(base.SERVICE)
        or base.shield_state() is not True
    ):
        raise RuntimeError("Service drain or Shields Up changed before watchdog start")
    base.command("/usr/bin/systemctl", "start", base.WATCHDOG, timeout=60)
    if not base.service_active(base.WATCHDOG) or not active.wait_watchdog_socket(
        base, host_watchdog
    ):
        raise RuntimeError("Watchdog did not become healthy")


def read_scope(
    db_path: Path, session: str, version: str, grant: str, fixture: dict
) -> dict:
    """Read only the exact currently authorized synthetic recipient scope."""
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)) as db:
        db.row_factory = sqlite3.Row
        rows = list(
            db.execute(
                "SELECT s.id,s.version,s.mode,s.expires,s.grant_revision,"
                "g.id AS grant_id,g.version AS grant_version,g.mode AS grant_mode,"
                "g.action,g.revision,g.expires AS grant_expires,g.revoked,"
                "v.approved,v.revoked AS version_revoked,v.manifest "
                "FROM sessions s JOIN grants g ON g.id=s.grant_id "
                "JOIN versions v ON v.id=s.version WHERE s.id=? AND g.id=?",
                (session, grant),
            )
        )
    if len(rows) != 1:
        raise RuntimeError("Exact recipient session and grant are unavailable")
    row = dict(rows[0])
    manifest = json.loads(row.pop("manifest"))
    action = manifest.get("action")
    files = manifest.get("files")
    now = time.time()
    if (
        row["version"] != version
        or row["grant_version"] != version
        or row["mode"] != "verify"
        or row["grant_mode"] != "verify"
        or row["action"] != "json-check"
        or row["revision"] != row["grant_revision"]
        or row["revoked"] != 0
        or row["approved"] != 1
        or row["version_revoked"] != 0
        or row["expires"] <= now
        or row["grant_expires"] <= now
        or manifest.get("mode") != "verify"
        or not isinstance(action, dict)
        or action.get("id") != "json-check"
        or action.get("profile") != "reference-linux"
        or not isinstance(files, list)
        or len(files) != len(fixture)
        or any(not isinstance(item, dict) for item in files)
        or {item.get("name"): item.get("sha256") for item in files} != fixture
    ):
        raise RuntimeError("Recipient scope is not the approved fixed JSON action")
    program_hash = action.get("program_sha256")
    if not isinstance(program_hash, str) or not re.fullmatch(
        r"[0-9a-f]{64}", program_hash
    ):
        raise RuntimeError("Approved JSON program hash is unavailable")
    row["program_sha256"] = program_hash
    return row


def read_new_runs(db_path: Path, session: str, armed_at: float) -> list[dict]:
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)) as db:
        db.row_factory = sqlite3.Row
        rows = list(
            db.execute(
                "SELECT id,session,request_key,status,created,finished,result,error,action,"
                "runtime_profile,runtime_resource,runtime_token FROM runs "
                "WHERE session=? AND created>=? ORDER BY created,id",
                (session, armed_at),
            )
        )
    return [dict(row) for row in rows]


def read_exact_run(db_path: Path, session: str, run_id: str) -> dict | None:
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)) as db:
        db.row_factory = sqlite3.Row
        rows = list(
            db.execute(
                "SELECT id,session,request_key,status,created,finished,result,error,action,"
                "runtime_profile,runtime_resource,runtime_token FROM runs "
                "WHERE session=? AND id=?",
                (session, run_id),
            )
        )
    return dict(rows[0]) if len(rows) == 1 else None


def validate_new_run(row: dict, *, armed_at: float, session: str) -> None:
    if (
        row["session"] != session
        or not isinstance(row["request_key"], str)
        or not 8 <= len(row["request_key"]) <= 80
        or row["created"] < armed_at
        or row["action"] != "json-check"
        or row["runtime_profile"] != "reference-linux"
        or not HEX32.fullmatch(row["id"])
        or row["runtime_resource"] != "prism-m23-run-" + row["id"]
        or not isinstance(row["runtime_token"], str)
        or not HEX32.fullmatch(row["runtime_token"])
    ):
        raise RuntimeError("New browser-requested run identity differs")


def terminal_event_exists(db_path: Path, run_id: str, status: str) -> bool:
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)) as db:
        count = db.execute(
            "SELECT count(*) FROM events WHERE kind='run_finished' "
            "AND resource=? AND outcome=?",
            (run_id, status),
        ).fetchone()[0]
    return count == 1


def terminal_race_proven(
    row: dict,
    *,
    session: str,
    run_id: str,
    resource: str,
    token: str,
    program_sha256: str,
    event_exists: bool,
) -> bool:
    if (
        not event_exists
        or row["id"] != run_id
        or row["session"] != session
        or row["runtime_resource"] != resource
        or row["runtime_token"] != token
        or row["runtime_profile"] != "reference-linux"
        or row["action"] != "json-check"
        or row["status"] not in ("completed", "failed", "cancelled")
        or not isinstance(row["finished"], (int, float))
        or row["finished"] < row["created"]
    ):
        return False
    if row["status"] == "completed":
        try:
            result = json.loads(row["result"])
        except (TypeError, ValueError):
            return False
        return (
            isinstance(result, dict)
            and result.get("action") == "json-check"
            and result.get("profile") == "reference-linux"
            and result.get("runtime_handler") == "io.containerd.kata.v2"
            and result.get("program_sha256") == program_sha256
            and result.get("cleaned_up") is True
            and result.get("exit_code") == 0
        )
    return (
        row["result"] is None and isinstance(row["error"], str) and bool(row["error"])
    )


def finalize_report(report: dict) -> None:
    if any(
        report.get(name) is not None
        for name in (
            "error_kind",
            "recovery_error_kind",
            "closure_error_kind",
            "emergency_tailnet_close_error_kind",
        )
    ):
        report["outcome"] = "failed"
        report["passed"] = False
        return
    report["passed"] = report["outcome"] == "preflight_passed" or (
        report["outcome"] == "passed"
        and report["checks"].get("service_restored") is True
    )


def fixed_failure_origin(exc: Exception) -> str:
    """Expose only a reviewed function category, never traceback paths or text."""
    frame = exc.__traceback__
    if frame is None:
        return "unknown"
    while frame.tb_next is not None:
        frame = frame.tb_next
    name = frame.tb_frame.f_code.co_name
    if name == "<lambda>":
        return "observation_callback"
    return name if name in FAILURE_ORIGINS else "other"


def record_primary_failure(report: dict, exc: Exception) -> None:
    report["error_kind"] = type(exc).__name__
    report["failure_stage"] = (
        report["phase"] if report["phase"] in FAILURE_STAGES else "other"
    )
    report["failure_origin"] = fixed_failure_origin(exc)


def run(
    release: Path, session: str, version: str, grant: str, *, preflight_only=False
) -> dict:
    report = {
        "kind": "m2-browser-active-tailnet-check",
        "schema": 1,
        "browser_channel_proven_by_host": False,
        "root_submitted_job": False,
        "fault_applied": False,
        "outcome": "failed",
        "passed": False,
        "error_kind": None,
        "phase": "host",
        "checks": {},
        "pilot_ready": False,
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
    active = base = observed = host_watchdog = runtime = resolver = None
    main_fd = worker_fd = None
    client = group = None
    faulted = False
    row = None
    recovering = False
    old_handlers = {}

    def interrupted(signum, _frame):
        if not recovering:
            raise RuntimeError("Operator interrupted the diagnostic")

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            old_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, interrupted)
        active = pinned_active()
        configure_release_pins(active, release)
        parent = active.parent_module()
        base = parent.pinned_module(
            parent.BASE, parent.BASE_SHA256, "prism_browser_base"
        )
        observed = base.load_observed()
        host_watchdog, reference_runtime = active.pinned_modules(release, observed)
        resolver = current_resolver(active, release)
        main_pid = base.active_unit_identity(observed, release)
        main_identity = active.process_identity(main_pid)
        if main_identity is None or main_identity[1] in ("Z", "X"):
            raise RuntimeError("Installed service process identity unavailable")
        main_fd = os.pidfd_open(main_pid, 0)
        main = {"pid": main_pid, "start": main_identity[0], "fd": main_fd}
        runtime = reference_runtime.ReferenceLinuxRuntime()
        if (
            base.live_counts()["pending"]
            or runtime.all_resources() != []
            or base.shield_state() is not False
            or not active.tailnet_online(base)
            or set(host_watchdog._group_processes("/system.slice/" + base.SERVICE))
            != {main_pid}
            or not active.watchdog_socket_healthy(host_watchdog)
        ):
            raise RuntimeError("Service is not idle, healthy, and tailnet online")
        db_path = parent.DB
        scope = read_scope(db_path, session, version, grant, parent.FIXTURE)
        report["checks"]["pinned_idle_scope"] = True
        if preflight_only:
            report["outcome"] = "preflight_passed"
            report["passed"] = True
            return report
        armed_at = time.time()
        report["phase"] = "await_external_browser_submission"
        deadline = time.monotonic() + MAX_ARM_SECONDS
        while time.monotonic() < deadline:
            candidates = read_new_runs(db_path, session, armed_at)
            if len(candidates) > 1:
                raise RuntimeError("More than one run appeared after arming")
            if candidates:
                row = candidates[0]
                validate_new_run(row, armed_at=armed_at, session=session)
                scope = read_scope(db_path, session, version, grant, parent.FIXTURE)
                report["checks"]["new_exact_granted_run"] = True
                break
            time.sleep(0.025)
        else:
            report["outcome"] = "inconclusive"
            return report
        resource, token = row["runtime_resource"], row["runtime_token"]
        run_id, key = row["id"], row["request_key"]
        group_name = "/system.slice/" + base.SERVICE
        report["phase"] = "observe_runtime"
        deadline = time.monotonic() + MAX_OBSERVE_SECONDS
        worker_pid = worker_start = None
        while time.monotonic() < deadline:
            row = read_exact_run(db_path, session, run_id)
            if row is None or row["status"] in (
                "completed",
                "failed",
                "cancelled",
                "uncertain",
            ):
                report["outcome"] = "inconclusive"
                return report
            info = inspect_during_creation(runtime, reference_runtime, resource, token)
            state = info.get("State", {}) if info else {}
            running = isinstance(state, dict) and (
                state.get("Status") == "running" or state.get("Running") is True
            )
            if running:
                children = active.children_all_threads(main_pid)
                clients = observed.discover_exact_clients(resource, token)
                try:
                    if len(children) == len(clients) == 1:
                        pid = children[0]
                        identity = observed.process_identity(pid)
                        if (
                            identity is not None
                            and identity[1] not in ("Z", "X")
                            and parent.process_exact(
                                observed, pid, resource, token, group_name
                            )
                            and active.children_all_threads(pid) == [clients[0].pid]
                            and host_watchdog._cgroup(clients[0].pid) == group_name
                        ):
                            bound = active.bind_kata_group(info)
                            if bound is not None:
                                worker_fd = os.pidfd_open(pid, 0)
                                worker_pid, worker_start = pid, identity[0]
                                client, group = clients[0], bound
                                clients = []
                                break
                finally:
                    observed.close_clients(clients)
            time.sleep(0.02)
        else:
            report["outcome"] = "inconclusive"
            return report
        report["checks"]["runtime_worker_client_kata_bound"] = True
        report["phase"] = "fault_edge"
        scope = read_scope(db_path, session, version, grant, parent.FIXTURE)
        row = read_exact_run(db_path, session, run_id)
        if (
            row is None
            or row["status"] != "running"
            or row["runtime_resource"] != resource
            or row["runtime_token"] != token
            or not active.pinned_alive(main)
            or not active.exact_fault_edge(
                parent,
                observed,
                host_watchdog,
                runtime,
                chat=session,
                key=key,
                run=row["id"],
                resource=resource,
                token=token,
                driver_pid=main_pid,
                worker_pid=worker_pid,
                worker_start=worker_start,
                worker_fd=worker_fd,
                client=client,
                group=group_name,
                group_snapshot=group,
            )
            or base.shield_state() is not False
            or not base.service_active("tailscaled.service")
        ):
            report["outcome"] = "inconclusive"
            return report
        report["checks"]["exact_running_fault_edge"] = True
        report["phase"] = "tailnet_down"
        faulted = True
        fault_at = time.monotonic()
        base.command("/usr/bin/tailscale", "down", timeout=10)
        report["checks"]["tailnet_stopped"] = active.stopped_backend_observed(base)
        if not report["checks"]["tailnet_stopped"]:
            raise RuntimeError("Tailnet stopped state unverified")
        report["fault_applied"] = True
        report["phase"] = "observe_fail_closed"
        elapsed = {
            name: None
            for name in (
                "driver",
                "worker",
                "client",
                "guest_qemu",
                "kata_shim",
                "pre_fault_pinned_group",
                "container_metadata",
                "uncertain_null",
            )
        }
        named = {
            "driver": main,
            "worker": {"pid": worker_pid, "start": worker_start, "fd": worker_fd},
            "client": {"pid": client.pid, "start": client.start, "fd": client.pidfd},
            "guest_qemu": next(
                p for p in group["processes"] if p["pid"] == group["qemu_pid"]
            ),
            "kata_shim": next(
                p for p in group["processes"] if p["pid"] == group["shim_pid"]
            ),
        }
        reconstruction = {"first_trigger": None, "candidates": [], "truncated": False}
        stopped, whole_group, reconstructed = active.observe_job_termination(
            fault_at=fault_at,
            group_snapshot=group,
            named=named,
            main_gone=lambda: not active.pinned_alive(main),
            elapsed=elapsed,
            uncertain_null=lambda: (
                (r := read_exact_run(db_path, session, run_id)) is not None
                and r["status"] == "uncertain"
                and r["result"] is None
            ),
            reconstruction_evidence=reconstruction,
            metadata_absent=lambda: runtime.inspect_owned(resource, token) is None,
            final_absent=lambda: (
                runtime.inspect_owned(resource, token) is None
                and runtime.all_resources() == []
                and observed.cgroup_tree_drained(base.SERVICE)
                and not active.same_container_candidates(group["container_id"])
            ),
        )
        report["checks"].update(
            {
                "watchdog_stopped_service": stopped,
                "whole_group_stopped_within_30s": whole_group,
                "run_uncertain_null": elapsed["uncertain_null"] is not None,
                "resource_absent": runtime.inspect_owned(resource, token) is None,
                "no_group_reconstruction": not reconstructed,
            }
        )
        report["elapsed_seconds"] = {k: v for k, v in elapsed.items() if v is not None}
        report["outcome"] = "passed" if all(report["checks"].values()) else "failed"
    except Exception as exc:  # noqa: BLE001 - never disclose exception text.
        record_primary_failure(report, exc)
    finally:
        recovering = True
        if observed is not None and client is not None:
            try:
                observed.close_clients([client])
            except Exception:  # noqa: BLE001 - recovery still must run.
                report["error_kind"] = report["error_kind"] or "ClientCloseError"
        if active is not None:
            try:
                active.close_group(group)
            except Exception:  # noqa: BLE001 - recovery still must run.
                report["error_kind"] = report["error_kind"] or "GroupCloseError"
        for fd in (worker_fd, main_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    report["error_kind"] = report["error_kind"] or "FdCloseError"
        if faulted and active is not None and base is not None and observed is not None:
            report["phase"] = "recovery"
            recovery_stage = "close_tailnet"
            try:
                # Never reopen inbound traffic while service work is unresolved.
                report["tailnet_close"] = ensure_tailnet_closed(base, observed, active)
                recovery_stage = "stop_service"
                if base.service_active(base.SERVICE):
                    base.command(
                        "/usr/bin/systemctl",
                        "kill",
                        "--signal=SIGKILL",
                        "--kill-who=all",
                        base.SERVICE,
                        timeout=10,
                    )
                base.command("/usr/bin/systemctl", "stop", base.SERVICE, timeout=60)
                if not base.wait_for(
                    lambda: observed.cgroup_tree_drained(base.SERVICE), 30
                ):
                    raise RuntimeError("Service cgroup did not drain")
                recovery_stage = "namespace_drain"
                if runtime.all_resources() != []:
                    raise RuntimeError("Reference namespace is not empty")
                recovery_stage = "restore_tailnet_watchdog"
                recover_tailnet_after_close_failure(
                    base, observed, active, host_watchdog
                )
                recovery_stage = "resolve_run"
                row = read_exact_run(parent.DB, session, run_id)
                if (
                    row is not None
                    and row["status"] == "uncertain"
                    and row["result"] is None
                ):
                    if not inspect_and_resolve_current(resolver, row["id"]):
                        raise RuntimeError("Exact run resolver refused")
                    row = read_exact_run(parent.DB, session, run_id)
                    if (
                        row is None
                        or row["status"] != "failed"
                        or row["result"] is not None
                    ):
                        raise RuntimeError("Exact run was not resolved failed/null")
                    report["checks"]["uncertain_run_resolved"] = True
                elif row is not None and terminal_race_proven(
                    row,
                    session=session,
                    run_id=run_id,
                    resource=resource,
                    token=token,
                    program_sha256=scope["program_sha256"],
                    event_exists=terminal_event_exists(
                        parent.DB, run_id, row["status"]
                    ),
                ):
                    report["outcome"] = "inconclusive"
                    report["checks"]["terminal_race_proven"] = True
                else:
                    raise RuntimeError("Exact run is not safely reconciled")
                recovery_stage = "pre_start_absence"
                if not ready_to_restart_service(
                    base, observed, runtime, active, resource, token, group
                ):
                    raise RuntimeError("Runtime or service process reappeared")
                report["checks"]["final_resource_and_process_absence"] = True
                recovery_stage = "start_service"
                base.command("/usr/bin/systemctl", "start", base.SERVICE, timeout=380)
                recovery_stage = "stability_observation"
                stable, stability = active.observe_recovery_stability(
                    base, host_watchdog, runtime
                )
                report["recovery_stability"] = stability
                if not stable:
                    raise RuntimeError("Restored service is not stable")
                report["checks"]["service_restored"] = True
            except Exception as exc:  # noqa: BLE001 - fail closed, no secret details.
                report["recovery_error_kind"] = type(exc).__name__
                report["recovery_error_stage"] = recovery_stage
                close_after_recovery_error(base, observed, active, report)
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
        finalize_report(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True, type=Path)
    parser.add_argument("--session", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--grant", required=True)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    for value in (args.session, args.version, args.grant):
        if not HEX32.fullmatch(value):
            parser.error("Expected an exact 32-character identifier")
    result = run(
        args.release,
        args.session,
        args.version,
        args.grant,
        preflight_only=args.preflight_only,
    )
    print(
        "PRISM_BROWSER_ACTIVE_RESULT " + json.dumps(result, sort_keys=True), flush=True
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
