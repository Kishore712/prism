"""Root-only active synthetic run check for a pinned Tailscale-down release.

The installed service is never modified. A root-private copy of its verified
Python source changes only the synthetic JSON fixture to sleep in the guest.
The copied Jobs/worker/controller code and installed watchdog socket are real.
One synthetic project/chat/run remains in the live database for audit. This
trusted root test does not exercise OIDC, browser authorization, or the
production JSON program hash.
"""

from __future__ import annotations

import argparse
import errno
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
TRANSFER_SHA256 = "e11ea7a88a2b029f73e2374d47f271af178ff0604febbe9a47e1b4c9259d4f27"
SOURCE_SHA256 = "fee499e336567fd4c1f9a564c3fc2983fd520eb18d33e191d5ae29b267e1e16d"
WATCHDOG_SHA256 = "f853945bd8c384101effbcfb494e368f4b880156b649456459650a045a6f0cab"
LIFECYCLE_SHA256 = "79e3db10b3683bc65292f449b88798d4edb8674a6f8ea63c8cd08d8cd64c9c0f"
LIFECYCLE = Path("/usr/local/libexec/prism-identity-service-lifecycle.py")
RESOLVER = Path("/usr/local/libexec/prism-identity-uncertain-resolution-v8.py")
RESOLVER_SHA256 = "e73d8daf0e627a2aece31ed8db71b8221fd63d97434b0f3325c0b5435e19793c"
LAB_PROJECT = "m2-watchdog-lab"
LAB_OWNER = "test-only-root-driver"
MAX_LAB_CHATS = 9
RECOVERY_STABILITY_SECONDS = 180
LAB_FILE = "config/release.json"
LAB_JSON = '{"release":"synthetic-watchdog-check","approved":false}\n'
ORIGINAL_FIXTURE_SHA256 = (
    "aa36c641a81f4032549d5890a8d1e50899ec0076c23677a31c038a7bf55e3480"
)
FIXTURE_PATH = Path("src/prism/fixtures/json_check.py")
REQUIRED = (
    "pinned_release_units_and_helpers",
    "live_idle_and_namespace_empty",
    "installed_watchdog_socket_healthy",
    "lab_project_scope_bounded",
    "serving_shields_down_at_fault",
    "driver_in_real_service_cgroup",
    "test_only_source_diff_exact",
    "canonical_lab_jobs_row_created",
    "exact_owned_resource_running",
    "worker_and_client_in_service_cgroup",
    "exact_kata_group_bound",
    "tailscale_down_after_observation",
    "installed_watchdog_stopped_service",
    "whole_job_group_stopped_within_30s",
    "exact_container_metadata_absent",
    "run_uncertain_null",
    "no_late_recreation",
    "resolver_inspected_and_resolved",
    "final_namespace_and_cgroup_drained",
    "live_state_scoped_to_one_synthetic_run",
    "recovery_order_confirmed",
    "service_and_watchdog_restored",
)


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


def pinned_modules(release: Path, observed):
    if release.name != "service-" + TRANSFER_SHA256:
        raise RuntimeError("Unexpected immutable tailnet-guard release")
    helper = LIFECYCLE.lstat()
    resolver = RESOLVER.lstat()
    if (
        not stat.S_ISREG(helper.st_mode)
        or helper.st_uid != 0
        or helper.st_nlink != 1
        or helper.st_mode & 0o022
        or hashlib.sha256(LIFECYCLE.read_bytes()).hexdigest() != LIFECYCLE_SHA256
        or not stat.S_ISREG(resolver.st_mode)
        or resolver.st_uid != 0
        or resolver.st_nlink != 1
        or stat.S_IMODE(resolver.st_mode) != 0o700
        or hashlib.sha256(RESOLVER.read_bytes()).hexdigest() != RESOLVER_SHA256
    ):
        raise RuntimeError("Pinned lifecycle or root-only resolver differs")
    observed.V7_TRANSFER_SHA256 = TRANSFER_SHA256
    observed.SOURCE_ARCHIVE_SHA256 = SOURCE_SHA256
    host_watchdog, reference_runtime = observed.verified_modules(release)
    if (
        hashlib.sha256(
            (release / "src/prism/host_watchdog.py").read_bytes()
        ).hexdigest()
        != WATCHDOG_SHA256
    ):
        raise RuntimeError("Pinned tailnet watchdog source differs")
    return host_watchdog, reference_runtime


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


def resolver_refusal_category(exc: Exception) -> str:
    if isinstance(exc, subprocess.TimeoutExpired):
        return "timeout"
    if isinstance(exc, subprocess.CalledProcessError):
        return "rejected"
    if isinstance(exc, OSError):
        return "unavailable"
    if isinstance(exc, (ValueError, UnicodeError, TypeError)):
        return "invalid_response"
    if isinstance(exc, RuntimeError):
        return "response_mismatch"
    return "other"


def watchdog_socket_healthy(host_watchdog) -> bool:
    try:
        host_watchdog.WatchdogClient().health()
        return True
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        return False


def wait_watchdog_socket(base, host_watchdog) -> bool:
    return base.wait_for(lambda: watchdog_socket_healthy(host_watchdog), 15)


def recovery_unit_identity(base, unit: str) -> tuple[int, int] | None:
    if not base.service_active(unit):
        return None
    pid = int(base.show(unit, "MainPID"))
    identity = process_identity(pid) if pid > 1 else None
    if identity is None or identity[1] in ("Z", "X"):
        return None
    return pid, identity[0]


def observe_recovery_stability(
    base, host_watchdog, runtime, *, duration: float = RECOVERY_STABILITY_SECONDS
) -> tuple[bool, dict]:
    """Read-only, bounded sampling after all three units have been restored."""
    started = time.monotonic()
    units = (base.SERVICE, base.WATCHDOG, "tailscaled.service")
    pinned = {unit: recovery_unit_identity(base, unit) for unit in units}
    summary = {
        "observed_seconds": 0.0,
        "samples": 0,
        "failure_category": None,
        "identity_evidence": "installed_watchdog_health_plus_tailnet_status_prefs",
    }
    if any(identity is None for identity in pinned.values()):
        summary["failure_category"] = "unit_unavailable"
        return False, summary
    last_sample = started
    while True:
        now = time.monotonic()
        if now - last_sample > 10:
            summary["failure_category"] = "observation_gap"
            break
        last_sample = now
        summary["samples"] += 1
        if any(recovery_unit_identity(base, unit) != pinned[unit] for unit in units):
            summary["failure_category"] = "unit_changed"
            break
        if not tailnet_online(base) or base.shield_state() is not False:
            summary["failure_category"] = "tailnet_unverified"
            break
        if not watchdog_socket_healthy(host_watchdog):
            summary["failure_category"] = "watchdog_unhealthy"
            break
        if runtime.all_resources() != []:
            summary["failure_category"] = "namespace_occupied"
            break
        completed = time.monotonic()
        summary["observed_seconds"] = round(completed - started, 3)
        if completed - now > 10:
            summary["failure_category"] = "observation_gap"
            break
        if completed - started >= duration:
            return True, summary
        time.sleep(min(5, duration - (completed - started)))
    return False, summary


def fail_closed_after_unstable_recovery(base, *, service_drained) -> None:
    """End a failed stability observation with no serving service or tailnet."""
    ingress_closed = False
    try:
        base.command("/usr/bin/tailscale", "set", "--shields-up=true", timeout=10)
        ingress_closed = base.shield_state() is True
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        pass
    if not ingress_closed:
        try:
            force_tailnet_closed(base)
            ingress_closed = True
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            pass
    if not ingress_closed:
        try:
            base.command(
                "/usr/bin/systemctl",
                "kill",
                "--signal=SIGKILL",
                "--kill-who=all",
                base.SERVICE,
                timeout=10,
            )
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            force_tailnet_closed(base)
            raise
    try:
        base.command("/usr/bin/systemctl", "stop", base.SERVICE, timeout=60)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        base.command(
            "/usr/bin/systemctl",
            "kill",
            "--signal=SIGKILL",
            "--kill-who=all",
            base.SERVICE,
            timeout=10,
        )
    if base.service_active(base.SERVICE) or not service_drained():
        force_tailnet_closed(base)
        raise RuntimeError("Identity service remained after unstable recovery")
    if not ingress_closed:
        force_tailnet_closed(base)
    base.command("/usr/bin/systemctl", "stop", base.WATCHDOG, timeout=60)
    if base.service_active(base.WATCHDOG):
        force_tailnet_closed(base)
        raise RuntimeError("Watchdog remained after unstable recovery")
    force_tailnet_closed(base)


def staged_resolver_action(stage: str, run: str, report: dict) -> dict:
    try:
        return resolver_action(stage, run)
    except Exception as exc:
        report["resolver_failure"] = {
            "stage": stage,
            "category": resolver_refusal_category(exc),
        }
        raise


def inspect_and_resolve(run: str, report: dict) -> bool:
    inspected = staged_resolver_action("inspect", run, report)
    if inspected.get("changed") is not False:
        report["resolver_failure"] = {
            "stage": "inspect",
            "category": "response_mismatch",
        }
        return False
    resolved = staged_resolver_action("resolve", run, report)
    accepted = (
        resolved.get("changed") is True
        and resolved.get("status") == "failed"
        and resolved.get("execution_outcome") == "unknown"
    )
    if not accepted:
        report["resolver_failure"] = {
            "stage": "resolve",
            "category": "response_mismatch",
        }
    return accepted


def resolver_precondition_failure(
    *, row, base, runtime, observed, host_watchdog, resource, token
) -> str | None:
    if row is None or row["status"] != "uncertain":
        return "row_not_uncertain"
    if not base.wait_for(
        lambda: base.show(base.SERVICE, "ActiveState") in ("inactive", "failed"),
        20,
    ):
        return "service_not_stopped"
    if runtime.inspect_owned(resource, token) is not None:
        return "resource_present"
    if runtime.all_resources() != []:
        return "namespace_occupied"
    if not observed.cgroup_tree_drained(base.SERVICE):
        return "service_cgroup_occupied"
    if not base.service_active(base.WATCHDOG) or not watchdog_socket_healthy(
        host_watchdog
    ):
        return "watchdog_unhealthy"
    if base.shield_state() is not True:
        return "shield_unverified"
    return None


def tailnet_backend(base) -> str:
    status = json.loads(base.command("/usr/bin/tailscale", "status", "--json").stdout)
    return status.get("BackendState", "unknown")


def stopped_backend_observed(base) -> bool:
    # The close hook may stop tailscaled before this separate observation.
    try:
        return tailnet_backend(base) == "Stopped"
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return False


def process_identity(pid: int):
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        fields = raw.rsplit(") ", 1)[1].split()
        return int(fields[19]), fields[0]
    except OSError as exc:
        if exc.errno not in (errno.ENOENT, errno.ESRCH):
            raise RuntimeError("Process identity could not be inspected") from exc
        return None
    except (ValueError, IndexError) as exc:
        raise RuntimeError("Process identity returned invalid metadata") from exc


def process_argv(
    pid: int, *, proc_root: Path = Path("/proc"), strict: bool = False
) -> list[str]:
    try:
        raw = (proc_root / str(pid) / "cmdline").read_bytes()
        return [
            part.decode("utf-8", "strict") for part in raw.rstrip(b"\0").split(b"\0")
        ]
    except OSError as exc:
        if strict and (
            exc.errno not in (errno.ENOENT, errno.ESRCH)
            or process_identity(pid) is not None
        ):
            raise RuntimeError("Process command could not be inspected") from exc
        return []
    except UnicodeError as exc:
        if strict:
            raise RuntimeError("Process command returned invalid metadata") from exc
        return []


def exact_flag(argv: list[str], flag: str) -> str | None:
    positions = [index for index, value in enumerate(argv) if value == flag]
    if len(positions) != 1 or positions[0] + 1 >= len(argv):
        return None
    return argv[positions[0] + 1]


def close_group(group_snapshot: dict | None) -> None:
    if group_snapshot is not None:
        for item in group_snapshot["processes"]:
            os.close(item["fd"])


def group_descendants(
    shim_pid: int, *, proc_root: Path = Path("/proc")
) -> list[int] | None:
    """Bound the complete process tree rooted at one exact Kata shim."""
    seen = {shim_pid}
    frontier = [shim_pid]
    while frontier:
        parent_pid = frontier.pop()
        for child_pid in children_all_threads(
            parent_pid, proc_root=proc_root, strict=True
        ):
            if child_pid in seen or len(seen) >= 128:
                return None
            seen.add(child_pid)
            frontier.append(child_pid)
    return sorted(seen)


def bind_kata_group(info: dict, *, proc_root: Path = Path("/proc")) -> dict | None:
    """Bind one owned container ID to its shim, QEMU, and full live subtree."""
    container_id = info.get("Id")
    if not isinstance(container_id, str) or not re.fullmatch(
        r"[0-9a-f]{64}", container_id
    ):
        return None
    shims = []
    try:
        for entry in proc_root.iterdir():
            if not entry.name.isdecimal():
                continue
            pid = int(entry.name)
            argv = process_argv(pid, proc_root=proc_root, strict=True)
            if not argv or Path(argv[0]).name != "containerd-shim-kata-v2":
                continue
            if (
                exact_flag(argv, "-id") == container_id
                and exact_flag(argv, "-namespace") == "prism-m0"
                and safe_command_category(argv, container_id) == "normal_shim"
            ):
                shims.append(pid)
    except OSError:
        return None
    if len(shims) != 1:
        return None
    shim_pid = shims[0]
    members = group_descendants(shim_pid, proc_root=proc_root)
    if members is None or len(members) < 2:
        return None
    qemu = []
    for pid in members:
        argv = process_argv(pid, proc_root=proc_root, strict=True)
        if argv and Path(argv[0]).name.startswith("qemu-system-"):
            qemu.append(pid)
    if len(qemu) != 1:
        return None
    if not any(
        container_id in argument
        for argument in process_argv(qemu[0], proc_root=proc_root, strict=True)[1:]
    ):
        return None
    processes = []
    try:
        for pid in members:
            identity = process_identity(pid)
            if identity is None or identity[1] in ("Z", "X"):
                return None
            fd = os.pidfd_open(pid, 0)
            processes.append({"pid": pid, "start": identity[0], "fd": fd})
            if process_identity(pid) != identity:
                return None
        if group_descendants(shim_pid, proc_root=proc_root) != members:
            return None
        result = {
            "container_id": container_id,
            "shim_pid": shim_pid,
            "qemu_pid": qemu[0],
            "processes": processes,
        }
        processes = []
        return result
    except OSError:
        return None
    finally:
        for item in processes:
            os.close(item["fd"])


def pinned_alive(item: dict) -> bool:
    identity = process_identity(item["pid"])
    if identity is None or identity[0] != item["start"] or identity[1] in ("Z", "X"):
        return False
    try:
        signal.pidfd_send_signal(item["fd"], 0, None, 0)
    except ProcessLookupError:
        return False
    return True


def exact_group_live(
    group_snapshot: dict, info: dict | None, *, proc_root: Path = Path("/proc")
) -> bool:
    if info is None or info.get("Id") != group_snapshot["container_id"]:
        return False
    members = group_descendants(group_snapshot["shim_pid"], proc_root=proc_root)
    candidates = same_container_candidates(
        group_snapshot["container_id"], proc_root=proc_root
    )
    return (
        members == [item["pid"] for item in group_snapshot["processes"]]
        and candidates
        == {
            group_snapshot["shim_pid"]: "normal_shim",
            group_snapshot["qemu_pid"]: "qemu",
        }
        and all(pinned_alive(item) for item in group_snapshot["processes"])
    )


def group_gone(group_snapshot: dict) -> bool:
    return all(not pinned_alive(item) for item in group_snapshot["processes"])


def safe_command_category(argv: list[str], container_id: str) -> str:
    """Return a fixed label; never retain argv or its path arguments."""
    if not argv:
        return "other"
    name = Path(argv[0]).name
    if (
        name == "containerd-shim-kata-v2"
        and exact_flag(argv, "-id") == container_id
        and exact_flag(argv, "-namespace") == "prism-m0"
    ):
        if argv == [
            argv[0],
            "-namespace",
            "prism-m0",
            "-address",
            "/run/containerd/containerd.sock",
            "-publish-binary",
            "/usr/local/bin/containerd",
            "-id",
            container_id,
            "-bundle",
            f"/run/containerd/io.containerd.runtime.v2.task/prism-m0/{container_id}",
            "delete",
        ]:
            return "exact_delete_helper"
        return "normal_shim" if "delete" not in argv else "other"
    if name.startswith("qemu-system-") and any(
        container_id in argument for argument in argv[1:]
    ):
        return "qemu"
    return "other"


def same_container_candidates(
    container_id: str, *, proc_root: Path = Path("/proc")
) -> dict[int, str]:
    """Rescan exact-ID Kata shims and QEMU processes, including replacements."""
    found = {}
    for entry in proc_root.iterdir():
        if not entry.name.isdecimal():
            continue
        pid = int(entry.name)
        argv = process_argv(pid, proc_root=proc_root, strict=True)
        if not argv:
            continue
        category = safe_command_category(argv, container_id)
        if category != "other" or (
            Path(argv[0]).name == "containerd-shim-kata-v2"
            and exact_flag(argv, "-id") == container_id
            and exact_flag(argv, "-namespace") == "prism-m0"
        ):
            identity = process_identity(pid)
            if identity is not None and identity[1] not in ("Z", "X"):
                found[pid] = category
    return found


def same_container_processes(
    container_id: str, *, proc_root: Path = Path("/proc")
) -> set[int]:
    return set(same_container_candidates(container_id, proc_root=proc_root))


def candidate_lineage(
    pid: int, *, proc_root: Path = Path("/proc")
) -> tuple[int | None, list[int], bool]:
    """Read bounded PID-only ancestry and the candidate start tick."""
    chain = []
    start = None
    current = pid
    for _ in range(16):
        try:
            raw = (proc_root / str(current) / "stat").read_text(encoding="ascii")
        except OSError as exc:
            if exc.errno in (errno.ENOENT, errno.ESRCH):
                return start, chain or [pid], False
            raise RuntimeError("Candidate ancestry could not be inspected") from exc
        try:
            fields = raw.rsplit(") ", 1)[1].split()
            if start is None:
                start = int(fields[19])
            parent = int(fields[1])
        except (ValueError, IndexError) as exc:
            raise RuntimeError("Candidate ancestry returned invalid metadata") from exc
        chain.append(current)
        if parent <= 1:
            if parent == 1:
                chain.append(parent)
            return start, chain, True
        current = parent
    return start, chain, False


def candidate_command_identity(
    pid: int,
    container_id: str,
    resource: str | None = None,
) -> dict:
    """Capture bounded private identity; only the exact Kata delete shape is trusted."""
    argv = process_argv(pid, strict=True)
    name = Path(argv[0]).name[:64] if argv else None
    if name is not None and not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name):
        name = None
    try:
        executable = os.readlink(f"/proc/{pid}/exe")
    except OSError as exc:
        if exc.errno not in (errno.ENOENT, errno.ESRCH):
            raise RuntimeError("Candidate executable could not be inspected") from exc
        executable = None
    exe_name = Path(executable).name[:64] if executable else None
    if exe_name is not None and not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", exe_name):
        exe_name = None
    result = {
        "argv_basename": name,
        "argument_count": len(argv),
        "argv_sha256": hashlib.sha256("\0".join(argv).encode("utf-8")).hexdigest(),
        "executable_basename": exe_name,
        "executable_link_sha256": (
            hashlib.sha256(executable.encode("utf-8")).hexdigest()
            if executable is not None
            else None
        ),
        "trusted_delete_shape": bool(
            argv
            and argv[0] == "/usr/local/bin/containerd-shim-kata-v2"
            and executable == "/opt/kata/bin/containerd-shim-kata-v2"
            and safe_command_category(argv, container_id) == "exact_delete_helper"
        ),
    }
    if name == "nerdctl":
        fixed_prefix = [
            "/usr/local/bin/nerdctl",
            "--address",
            "/run/containerd/containerd.sock",
            "--namespace",
            "prism-m0",
        ]
        suffix = (
            argv[len(fixed_prefix) :]
            if argv[: len(fixed_prefix)] == fixed_prefix
            else []
        )
        exact_resource = bool(
            resource is not None and suffix and suffix[-1] == resource
        )
        named_filter = bool(
            len(suffix) == 4
            and suffix[:3] == ["ps", "-aq", "--filter"]
            and resource is not None
            and suffix[3] == "name=" + resource
        )
        fixed_management = bool(
            exact_resource
            and (
                suffix == ["inspect", suffix[-1]]
                or suffix == ["kill", "--signal", "KILL", suffix[-1]]
                or suffix == ["rm", "--force", suffix[-1]]
            )
            or suffix == ["ps", "-a", "--format", "{{.Names}}"]
            or named_filter
        )
        try:
            cgroup = Path(f"/proc/{pid}/cgroup").read_text(encoding="ascii")
        except OSError as exc:
            if exc.errno not in (errno.ENOENT, errno.ESRCH):
                raise RuntimeError("Candidate cgroup could not be inspected") from exc
            cgroup = None
        if cgroup is not None and (
            len(cgroup) > 512 or any(ord(char) < 32 and char != "\n" for char in cgroup)
        ):
            raise RuntimeError("Candidate cgroup returned invalid metadata")
        bounded_argv = bool(
            len(argv) <= 16
            and sum(map(len, argv)) <= 1024
            and all(
                len(argument) <= 256 and all(32 <= ord(char) < 127 for char in argument)
                for argument in argv
            )
        )
        labels = {
            "/usr/local/bin/nerdctl": "nerdctl_binary",
            "--address": "address_flag",
            "/run/containerd/containerd.sock": "containerd_socket",
            "--namespace": "namespace_flag",
            "prism-m0": "prism_namespace",
            "inspect": "inspect",
            "ps": "ps",
            "rm": "rm",
            "kill": "kill",
            "wait": "wait",
            "logs": "logs",
            "start": "start",
            "stop": "stop",
            "delete": "delete",
            "--force": "force_flag",
        }
        argument_fingerprints = [
            {
                "index": index,
                "length": len(argument),
                "sha256": hashlib.sha256(argument.encode("utf-8")).hexdigest(),
                "known_label": (
                    "owned_resource"
                    if resource is not None and argument == resource
                    else "container_id"
                    if argument == container_id
                    else labels.get(argument)
                ),
            }
            for index, argument in enumerate(argv[:16])
        ]
        result["nerdctl_private"] = {
            "fixed_management_shape": fixed_management,
            "exact_argv": (argv if bounded_argv and fixed_management else None),
            "raw_capture_bounded": bounded_argv,
            "argument_fingerprints": argument_fingerprints,
            "exe_path": executable if executable and len(executable) <= 256 else None,
            "cgroup": cgroup,
        }
    return result


def candidate_chain_starts(chain: list[int]) -> list[int] | None:
    starts = []
    for pid in chain:
        identity = process_identity(pid)
        if identity is None or identity[1] in ("Z", "X"):
            return None
        starts.append(identity[0])
    return starts


def record_reconstruction(
    evidence: dict,
    detections: list[tuple[int, str, str]],
    fault_at: float,
    container_id: str,
    candidate_fds: dict,
    resource: str | None = None,
) -> None:
    observed_at = round(time.monotonic() - fault_at, 3)
    if evidence["first_trigger"] is None:
        branches = {item[1] for item in detections}
        evidence["first_trigger"] = {
            "branch": branches.pop() if len(branches) == 1 else "simultaneous_branches",
            "elapsed_seconds": observed_at,
        }
    for pid, branch, category in detections:
        fd = None
        if hasattr(os, "pidfd_open"):
            try:
                fd = os.pidfd_open(pid, 0)
                candidate_fds[("pending", pid)] = fd
            except ProcessLookupError:
                pass
        start, chain, complete = candidate_lineage(pid)
        if any(
            item["pid"] == pid and item["starttime"] == start
            for item in evidence["candidates"]
        ):
            if fd is not None:
                os.close(fd)
                candidate_fds.pop(("pending", pid), None)
            continue
        if len(evidence["candidates"]) >= 16:
            evidence["truncated"] = True
            if fd is not None:
                os.close(fd)
                candidate_fds.pop(("pending", pid), None)
            continue
        chain_starts = (
            candidate_chain_starts(chain) if fd is not None and complete else None
        )
        command = candidate_command_identity(pid, container_id, resource)
        end_start, end_chain, end_complete = candidate_lineage(pid)
        end_chain_starts = (
            candidate_chain_starts(end_chain)
            if fd is not None and end_complete
            else None
        )
        identity = process_identity(pid) if fd is not None else None
        if "nerdctl_private" in command:
            final_command = candidate_command_identity(pid, container_id, resource)
            command["nerdctl_private"]["identity_stable"] = bool(
                start is not None
                and start == end_start
                and chain == end_chain
                and chain_starts is not None
                and chain_starts == end_chain_starts
                and identity is not None
                and identity[0] == start
                and command["nerdctl_private"] == final_command.get("nerdctl_private")
            )
        trusted = bool(
            category == "exact_delete_helper"
            and branch == "same_container_identity"
            and complete
            and end_complete
            and start is not None
            and start == end_start
            and chain == end_chain
            and chain_starts is not None
            and chain_starts == end_chain_starts
            and identity is not None
            and identity[0] == start
            and identity[1] not in ("Z", "X")
            and command["trusted_delete_shape"]
            and fd is not None
        )
        if fd is not None:
            candidate_fds.pop(("pending", pid), None)
            candidate_fds[(pid, start)] = fd
        evidence["candidates"].append(
            {
                "pid": pid,
                "starttime": start,
                "parent_chain": chain,
                "parent_starttimes": chain_starts,
                "lineage_complete": complete,
                "command_category": category,
                **command,
                "trusted_cleanup": trusted,
                "trigger_branch": branch,
                "first_observed_seconds": observed_at,
                "exit_observed_seconds": observed_at if start is None else None,
            }
        )


def update_candidate_exits(
    evidence: dict, fault_at: float, container_id: str | None = None
) -> None:
    for candidate in evidence["candidates"]:
        if candidate["exit_observed_seconds"] is not None:
            continue
        identity = process_identity(candidate["pid"])
        if (
            identity is None
            or identity[0] != candidate["starttime"]
            or identity[1] in ("Z", "X")
        ):
            candidate["exit_observed_seconds"] = round(time.monotonic() - fault_at, 3)
        elif candidate.get("trusted_cleanup") and container_id is not None:
            start, chain, complete = candidate_lineage(candidate["pid"])
            command = candidate_command_identity(candidate["pid"], container_id)
            if (
                not complete
                or start != candidate["starttime"]
                or chain != candidate["parent_chain"]
                or candidate_chain_starts(chain) != candidate["parent_starttimes"]
                or not command["trusted_delete_shape"]
            ):
                candidate["trusted_cleanup"] = False


def summarize_reconstruction(evidence: dict) -> dict:
    """The stdout-safe projection contains no process identifiers or paths."""
    categories = {
        name: 0 for name in ("normal_shim", "exact_delete_helper", "qemu", "other")
    }
    exited = []
    for candidate in evidence["candidates"]:
        category = candidate["command_category"]
        categories[category if category in categories else "other"] += 1
        if isinstance(candidate["exit_observed_seconds"], (int, float)):
            exited.append(candidate["exit_observed_seconds"])
    first = evidence["first_trigger"] or {}
    branch = first.get("branch")
    if branch not in (
        "new_shim_descendant",
        "same_container_identity",
        "shim_subtree_ambiguous",
        "simultaneous_branches",
    ):
        branch = None
    relative = first.get("elapsed_seconds")
    return {
        "first_trigger_branch": branch,
        "first_trigger_elapsed_seconds": relative
        if isinstance(relative, (int, float))
        else None,
        "candidate_count": len(evidence["candidates"]),
        "candidate_categories": categories,
        "truncated": evidence["truncated"] is True,
        "candidate_exit_count": len(exited),
        "last_candidate_exit_seconds": max(exited, default=None),
        "first_scan_elapsed_seconds": evidence.get("first_scan_elapsed_seconds"),
        "max_scan_gap_seconds": evidence.get("max_scan_gap_seconds"),
        "scan_continuity_verified": not evidence.get("ambiguous", False),
    }


def unexpected_group_member(
    group_snapshot: dict,
    *,
    evidence: dict | None = None,
    fault_at: float | None = None,
    candidate_fds: dict | None = None,
) -> bool:
    """A replacement or new descendant invalidates the pinned-group proof."""
    original_identities = {
        item["pid"]: item.get("start") for item in group_snapshot["processes"]
    }
    original = set(original_identities)
    shim = next(
        item
        for item in group_snapshot["processes"]
        if item["pid"] == group_snapshot["shim_pid"]
    )
    descendants = set()
    if pinned_alive(shim):
        members = group_descendants(group_snapshot["shim_pid"])
        if members is None:
            if evidence is not None:
                evidence["ambiguous"] = True
            if evidence is not None and evidence["first_trigger"] is None:
                evidence["first_trigger"] = {
                    "branch": "shim_subtree_ambiguous",
                    "elapsed_seconds": round(time.monotonic() - fault_at, 3),
                }
            return True
        descendants = set(members) - original
    candidates = same_container_candidates(group_snapshot["container_id"])
    same_id = set(candidates) - original
    for pid in set(candidates) & original:
        start = original_identities[pid]
        if start is not None:
            identity = process_identity(pid)
            if identity is not None and identity[0] != start:
                same_id.add(pid)
        if pid == group_snapshot["shim_pid"] and candidates[pid] != "normal_shim":
            same_id.add(pid)
        if pid == group_snapshot["qemu_pid"] and candidates[pid] != "qemu":
            same_id.add(pid)
    detections = [
        (
            pid,
            "new_shim_descendant" if pid in descendants else "same_container_identity",
            candidates.get(pid)
            or safe_command_category(
                process_argv(pid, strict=True), group_snapshot["container_id"]
            ),
        )
        for pid in sorted(descendants | same_id)
    ]
    if detections and evidence is not None:
        fds = candidate_fds if candidate_fds is not None else {}
        try:
            record_reconstruction(
                evidence,
                detections,
                fault_at,
                group_snapshot["container_id"],
                fds,
                group_snapshot.get("resource"),
            )
        finally:
            if candidate_fds is None:
                for fd in fds.values():
                    os.close(fd)
    return bool(detections)


def observe_job_termination(
    *,
    fault_at: float,
    group_snapshot: dict,
    named: dict,
    main_gone,
    elapsed: dict,
    uncertain_null=None,
    reconstruction_evidence: dict | None = None,
    final_absent=None,
    metadata_absent=None,
) -> tuple[bool, bool, bool]:
    """Observe the whole 30-second window; accept only a final strict absence proof."""
    service_gone = False
    reconstructed = False
    deadline = fault_at + 30
    candidate_fds = {}
    last_scan_completed = fault_at
    next_metadata_probe = fault_at
    try:
        while True:
            now = time.monotonic()
            if reconstruction_evidence is not None:
                if reconstruction_evidence.get("first_scan_elapsed_seconds") is None:
                    reconstruction_evidence["first_scan_elapsed_seconds"] = round(
                        now - fault_at, 3
                    )
                gap = now - last_scan_completed
                reconstruction_evidence["max_scan_gap_seconds"] = round(
                    max(reconstruction_evidence.get("max_scan_gap_seconds", 0), gap), 3
                )
                if gap > 1.0:
                    reconstruction_evidence["ambiguous"] = True
            service_gone |= main_gone()
            if (
                uncertain_null is not None
                and elapsed["uncertain_null"] is None
                and uncertain_null()
            ):
                elapsed["uncertain_null"] = round(time.monotonic() - fault_at, 3)
            for name, item in named.items():
                if elapsed[name] is None and not pinned_alive(item):
                    elapsed[name] = round(time.monotonic() - fault_at, 3)
            reconstructed |= unexpected_group_member(
                group_snapshot,
                evidence=reconstruction_evidence,
                fault_at=fault_at,
                candidate_fds=candidate_fds,
            )
            if reconstruction_evidence is not None:
                update_candidate_exits(
                    reconstruction_evidence,
                    fault_at,
                    group_snapshot.get("container_id"),
                )
            if (
                metadata_absent is not None
                and elapsed["container_metadata"] is None
                and now >= next_metadata_probe
            ):
                if metadata_absent():
                    elapsed["container_metadata"] = round(
                        time.monotonic() - fault_at, 3
                    )
                next_metadata_probe = time.monotonic() + 0.5
            all_pinned_gone = (
                service_gone
                and group_gone(group_snapshot)
                and all(not pinned_alive(item) for item in named.values())
            )
            completed_at = time.monotonic()
            if reconstruction_evidence is not None:
                scan_duration = completed_at - now
                reconstruction_evidence["max_scan_gap_seconds"] = round(
                    max(
                        reconstruction_evidence.get("max_scan_gap_seconds", 0),
                        scan_duration,
                    ),
                    3,
                )
                if scan_duration > 1.0:
                    reconstruction_evidence["ambiguous"] = True
            last_scan_completed = completed_at
            if all_pinned_gone and elapsed["pre_fault_pinned_group"] is None:
                elapsed["pre_fault_pinned_group"] = round(completed_at - fault_at, 3)
            if now >= deadline:
                if final_absent is None:
                    return service_gone, False, reconstructed
                # The final scan runs after t=30, even when all pinned PIDs exited early.
                reconstructed |= unexpected_group_member(
                    group_snapshot,
                    evidence=reconstruction_evidence,
                    fault_at=fault_at,
                    candidate_fds=candidate_fds,
                )
                if reconstruction_evidence is not None:
                    update_candidate_exits(
                        reconstruction_evidence,
                        fault_at,
                        group_snapshot.get("container_id"),
                    )
                final_started = time.monotonic()
                final_clean = final_absent()
                final_duration = time.monotonic() - final_started
                if reconstruction_evidence is not None:
                    reconstruction_evidence["max_scan_gap_seconds"] = round(
                        max(
                            reconstruction_evidence.get("max_scan_gap_seconds", 0),
                            final_duration,
                        ),
                        3,
                    )
                    if final_duration > 1.0:
                        reconstruction_evidence["ambiguous"] = True
                reconstructed |= unexpected_group_member(
                    group_snapshot,
                    evidence=reconstruction_evidence,
                    fault_at=fault_at,
                    candidate_fds=candidate_fds,
                )
                if reconstruction_evidence is not None:
                    update_candidate_exits(
                        reconstruction_evidence,
                        fault_at,
                        group_snapshot.get("container_id"),
                    )
                final_pinned_gone = (
                    main_gone()
                    and group_gone(group_snapshot)
                    and all(not pinned_alive(item) for item in named.values())
                )
                if reconstruction_evidence is not None:
                    terminal_gap = time.monotonic() - final_started
                    reconstruction_evidence["max_scan_gap_seconds"] = round(
                        max(
                            reconstruction_evidence.get("max_scan_gap_seconds", 0),
                            terminal_gap,
                        ),
                        3,
                    )
                    if terminal_gap > 1.0:
                        reconstruction_evidence["ambiguous"] = True
                evidence_ok = (
                    reconstruction_evidence is not None
                    and not reconstruction_evidence["truncated"]
                    and not reconstruction_evidence.get("ambiguous")
                    and (
                        not reconstructed or bool(reconstruction_evidence["candidates"])
                    )
                    and all(
                        item.get("trusted_cleanup")
                        and item["exit_observed_seconds"] is not None
                        and item["exit_observed_seconds"] <= 30
                        for item in reconstruction_evidence["candidates"]
                    )
                )
                return (
                    service_gone,
                    bool(
                        all_pinned_gone
                        and final_pinned_gone
                        and elapsed["pre_fault_pinned_group"] is not None
                        and elapsed["pre_fault_pinned_group"] <= 30
                        and evidence_ok
                        and final_clean
                        and time.monotonic() >= deadline
                    ),
                    reconstructed,
                )
            time.sleep(min(0.05, max(0, deadline - completed_at)))
    finally:
        for fd in candidate_fds.values():
            os.close(fd)


def force_tailnet_closed(base):
    """Confirm down, or stop tailscaled and confirm its main process exited."""
    try:
        pid = int(base.show("tailscaled.service", "MainPID"))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        pid = 0
    identity = None
    pidfd = None
    if pid > 1:
        try:
            identity = process_identity(pid)
        except RuntimeError:
            pass
        if hasattr(os, "pidfd_open"):
            try:
                pidfd = os.pidfd_open(pid, 0)
            except OSError:
                pass
    try:
        try:
            base.command("/usr/bin/tailscale", "down", timeout=10)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            pass
        else:
            if stopped_backend_observed(base):
                return
        base.command("/usr/bin/systemctl", "stop", "tailscaled.service", timeout=60)

        def stopped():
            if (
                base.show("tailscaled.service", "ActiveState")
                not in ("inactive", "failed")
                or base.show("tailscaled.service", "MainPID") != "0"
                or pid <= 1
            ):
                return False
            if pidfd is not None:
                try:
                    signal.pidfd_send_signal(pidfd, 0, None, 0)
                except ProcessLookupError:
                    return True
                except OSError:
                    return False
            try:
                current = process_identity(pid)
            except RuntimeError:
                return False
            return (
                current is None
                or current[1] in ("Z", "X")
                or (identity is not None and current[0] != identity[0])
            )

        if not base.wait_for(stopped, 10):
            raise RuntimeError("Tailscaled termination could not be verified")
    finally:
        if pidfd is not None:
            os.close(pidfd)


def offline_tailnet_prefs(base) -> tuple[bool, bool]:
    status = json.loads(base.command("/usr/bin/tailscale", "status", "--json").stdout)
    prefs = json.loads(base.command("/usr/bin/tailscale", "debug", "prefs").stdout)
    if status.get("BackendState") != "Stopped" or prefs.get("WantRunning") is not False:
        raise RuntimeError("Tailnet is not stopped with WantRunning false")
    return prefs.get("ShieldsUp") is True, prefs.get("ShieldsUp") is False


def tailnet_online(base) -> bool:
    status = json.loads(base.command("/usr/bin/tailscale", "status", "--json").stdout)
    own = status.get("Self") or {}
    return status.get("BackendState") == "Running" and own.get("Online") is True


def exact_fault_edge(
    parent,
    observed,
    host_watchdog,
    runtime,
    *,
    chat,
    key,
    run,
    resource,
    token,
    driver_pid,
    worker_pid,
    worker_start,
    worker_fd,
    client,
    group,
    group_snapshot,
) -> bool:
    """Recheck exact run and pidfd-backed identities immediately before down."""
    row = parent.live_row(chat, key)
    if (
        row is None
        or row["id"] != run
        or row["status"] != "running"
        or row["runtime_resource"] != resource
        or row["runtime_token"] != token
        or children_all_threads(driver_pid) != [worker_pid]
        or children_all_threads(worker_pid) != [client.pid]
        or (observed.process_identity(worker_pid) or (None,))[0] != worker_start
        or not parent.process_exact(observed, worker_pid, resource, token, group)
        or not observed.client_live(client, resource, token)
        or host_watchdog._cgroup(client.pid) != group
    ):
        return False
    try:
        info = runtime.inspect_owned(resource, token)
        state = info.get("State", {}) if info else {}
        if (
            not isinstance(state, dict)
            or not (state.get("Status") == "running" or state.get("Running") is True)
            or not exact_group_live(group_snapshot, info)
        ):
            return False
        signal.pidfd_send_signal(worker_fd, 0, None, 0)
        signal.pidfd_send_signal(client.pidfd, 0, None, 0)
    except OSError:
        return False
    return True


def recover_tailnet(base, *, service_drained, on_step=None):
    """Keep the watchdog stopped until tailscaled is online and shielded."""

    def step(name):
        if on_step is not None:
            on_step(name)

    step("stop_watchdog")
    base.command("/usr/bin/systemctl", "stop", base.WATCHDOG, timeout=60)
    if base.service_active(base.WATCHDOG):
        raise RuntimeError("Watchdog did not stop before tailnet restoration")
    if not service_drained():
        raise RuntimeError(
            "Service cgroup is not fully drained before tailscaled start"
        )
    step("reset_tailscaled_failure")
    base.command("/usr/bin/systemctl", "reset-failed", "tailscaled.service")
    step("start_tailscaled")
    base.command("/usr/bin/systemctl", "start", "tailscaled.service", timeout=60)
    if not base.service_active("tailscaled.service"):
        raise RuntimeError("Tailscaled did not start")
    if not service_drained():
        raise RuntimeError("Service cgroup is not fully drained before tailnet up")
    step("verify_offline_shield")
    shielded, unshielded = offline_tailnet_prefs(base)
    if unshielded:
        base.command("/usr/bin/tailscale", "set", "--shields-up=true")
        shielded, _ = offline_tailnet_prefs(base)
    if not shielded:
        raise RuntimeError("Offline tailnet ShieldsUp true is unverified")
    step("tailscale_up")
    try:
        base.command("/usr/bin/timeout", "25", "/usr/bin/tailscale", "up", timeout=30)
        if not base.wait_for(lambda: tailnet_online(base), 15):
            raise RuntimeError("Tailnet did not return to online Running state")
        shield = base.shield_state()
        if shield is not True:
            raise RuntimeError("Online tailnet lost its preverified ShieldsUp state")
    except Exception:
        force_tailnet_closed(base)
        raise
    step("start_watchdog")
    base.command("/usr/bin/systemctl", "start", base.WATCHDOG, timeout=60)
    if not base.service_active(base.WATCHDOG):
        raise RuntimeError("Watchdog did not restart")


def delayed_fixture(original: str) -> str:
    if hashlib.sha256(original.encode()).hexdigest() != ORIGINAL_FIXTURE_SHA256:
        raise RuntimeError("The pinned JSON fixture differs")
    import_marker = "import sys\n"
    entry_marker = (
        "    if len(sys.argv) != 2 or len(sys.argv[1]) > 100 * 1024:\n"
        "        return 2\n"
    )
    if original.count(import_marker) != 1 or original.count(entry_marker) != 1:
        raise RuntimeError("The pinned JSON fixture shape differs")
    variant = original.replace(import_marker, import_marker + "import time\n", 1)
    return variant.replace(entry_marker, entry_marker + "    time.sleep(20)\n", 1)


def test_source_copy(release: Path, root: Path) -> Path:
    """Copy only manifest-pinned package files; alter one fixed fixture."""
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
            raise RuntimeError("Pinned package source differs")
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        copied.append(relative)
    if len(copied) < 20 or str(FIXTURE_PATH) not in copied:
        raise RuntimeError("Pinned package inventory is incomplete")
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


def lab_capacity_available(existing_chats: int) -> bool:
    return isinstance(existing_chats, int) and 0 <= existing_chats < MAX_LAB_CHATS


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
    stage = "verify_release"
    try:
        parent = parent_module()
        base = parent.pinned_module(
            parent.BASE, parent.BASE_SHA256, "prism_active_base"
        )
        observed = base.load_observed()
        pinned_modules(release, observed)
        stage = "build_test_source"
        root = status_path.parent
        source_root = test_source_copy(release, root)
        project_file = root / "project" / LAB_FILE
        project_file.parent.mkdir(parents=True)
        project_file.write_text(LAB_JSON, encoding="ascii")
        # The release verifier imported the installed package. Discard only those
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
            or not lab_capacity_available(scope["lab_chats"])
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


def write_reconstruction_evidence(root: Path, evidence: dict) -> None:
    """Keep PID-level trace inside the root-owned retained evidence directory."""
    directory = root.lstat()
    if (
        not stat.S_ISDIR(directory.st_mode)
        or directory.st_uid != os.geteuid()
        or stat.S_IMODE(directory.st_mode) != 0o700
    ):
        raise RuntimeError("Root-private evidence directory differs")
    path = root / "reconstruction-evidence.json"
    write_status(path, evidence)
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise RuntimeError("Root-private reconstruction evidence differs")


def finish_evidence_root(
    root: Path, evidence: dict, report: dict, checks: dict
) -> None:
    report["reconstruction"] = summarize_reconstruction(evidence)
    try:
        write_reconstruction_evidence(root, evidence)
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        report["error_kind"] = report["error_kind"] or type(exc).__name__
    if report["error_kind"] is None and all(checks.values()):
        try:
            shutil.rmtree(root)
        except OSError as exc:
            report["error_kind"] = report["error_kind"] or type(exc).__name__
            report["evidence_retained"] = True
    else:
        report["evidence_retained"] = True


def status_file(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="ascii"))
    except (FileNotFoundError, ValueError):
        return {}


def children_all_threads(
    pid: int, *, proc_root: Path = Path("/proc"), strict: bool = False
) -> list[int]:
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
            except OSError as exc:
                if strict:
                    raise RuntimeError(
                        "Process children could not be inspected"
                    ) from exc
                continue
            result.update(int(value) for value in raw.split())
        return sorted(result)
    except (OSError, ValueError) as exc:
        if strict:
            raise RuntimeError("Process children could not be inspected") from exc
        return []


def preflight_only(release: Path) -> dict:
    if platform.system() != "Linux" or os.geteuid() != 0:
        raise RuntimeError("Preflight requires the root Linux test host")
    parent = parent_module()
    base = parent.pinned_module(
        parent.BASE, parent.BASE_SHA256, "prism_active_preflight_base"
    )
    observed = base.load_observed()
    pinned_modules(release, observed)
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
            "pinned_release_source": True,
            "test_copy_only_fixture_changed": True,
            "test_imports": True,
            "test_action": True,
            "live_database_touched": False,
            "service_or_network_changed": False,
        }
    finally:
        shutil.rmtree(root)


def run_check(release: Path) -> dict:
    checks = {name: False for name in REQUIRED}
    raw_reconstruction = {
        "first_trigger": None,
        "candidates": [],
        "truncated": False,
    }
    report = {
        "kind": "m2-tailnet-down-active-check",
        "schema": 3,
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
        "resolver_failure": None,
        "recovery_stability": {
            "observed_seconds": 0.0,
            "samples": 0,
            "failure_category": "not_started",
            "identity_evidence": "installed_watchdog_health_plus_tailnet_status_prefs",
        },
        "reconstruction": summarize_reconstruction(raw_reconstruction),
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
            "kata_shim_bound": False,
            "qemu_bound": False,
            "group_reconstruction_seen": False,
            "states": [],
        },
        "elapsed_from_tailnet_down_seconds": {
            "driver": None,
            "worker": None,
            "client": None,
            "guest_qemu": None,
            "kata_shim": None,
            "pre_fault_pinned_group": None,
            "container_metadata": None,
            "uncertain_null": None,
        },
        "timing_semantics": (
            "Monotonic seconds since immediately before the tailscale down command; "
            "container metadata is sampled during the full 30-second group observation."
        ),
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
    driver_fd = worker_fd = main_fd = None
    worker_pid = worker_start = driver_start = None
    client = None
    group_snapshot = None
    faulted = False
    recovery_steps = []
    phase = "verify"
    try:
        parent = parent_module()
        base = parent.pinned_module(
            parent.BASE, parent.BASE_SHA256, "prism_active_base_main"
        )
        observed = base.load_observed()
        host_watchdog, reference_runtime = pinned_modules(release, observed)
        main_pid = base.active_unit_identity(observed, release)
        main_identity = process_identity(main_pid)
        if main_identity is None or main_identity[1] in ("Z", "X"):
            raise RuntimeError("Installed service main process differs")
        main_fd = os.pidfd_open(main_pid, 0)
        main_process = {"pid": main_pid, "start": main_identity[0], "fd": main_fd}
        if not pinned_alive(main_process):
            raise RuntimeError("Installed service main process changed")
        if (
            hashlib.sha256((release / FIXTURE_PATH).read_bytes()).hexdigest()
            != ORIGINAL_FIXTURE_SHA256
        ):
            raise RuntimeError("Installed JSON program differs")
        checks["pinned_release_units_and_helpers"] = True
        baseline = base.live_counts()
        lab_baseline = lab_counts(parent)
        runtime = reference_runtime.ReferenceLinuxRuntime()
        group = "/system.slice/" + base.SERVICE
        if (
            baseline["pending"]
            or baseline["runs"] != 21
            or baseline["grants"] != 12
            or baseline["model_dispatches"] != 77
            or lab_baseline["chats"] >= 60
            or not lab_capacity_available(lab_baseline["lab_chats"])
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
        phase = "admission"
        if base.shield_state() is not False:
            raise RuntimeError("Service is not currently accepting tailnet traffic")
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
        driver_identity = observed.process_identity(proc.pid)
        if driver_identity is None or proc.stdin is None:
            raise RuntimeError("Test driver identity unavailable")
        driver_start = driver_identity[0]
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
                    worker_identity = observed.process_identity(children[0])
                    if worker_identity is None or worker_identity[1] in ("Z", "X"):
                        observed.close_clients(clients)
                        continue
                    group_snapshot = bind_kata_group(info)
                    if group_snapshot is None:
                        observed.close_clients(clients)
                        time.sleep(0.02)
                        continue
                    group_snapshot["resource"] = resource
                    client = clients[0]
                    worker_pid = children[0]
                    worker_start = worker_identity[0]
                    worker_fd = os.pidfd_open(children[0], 0)
                    checks["exact_owned_resource_running"] = True
                    checks["worker_and_client_in_service_cgroup"] = True
                    checks["exact_kata_group_bound"] = True
                    observation["kata_shim_bound"] = True
                    observation["qemu_bound"] = True
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
        if base.shield_state() is not False:
            report["outcome"] = "inconclusive"
            return report
        if not base.service_active("tailscaled.service"):
            report["outcome"] = "inconclusive"
            return report
        if not observed.owned_running(runtime, resource, token):
            report["outcome"] = "inconclusive"
            return report
        checks["serving_shields_down_at_fault"] = base.shield_state() is False
        if not checks["serving_shields_down_at_fault"]:
            report["outcome"] = "inconclusive"
            return report
        if not exact_fault_edge(
            parent,
            observed,
            host_watchdog,
            runtime,
            chat=chat,
            key=key,
            run=run,
            resource=resource,
            token=token,
            driver_pid=proc.pid,
            worker_pid=worker_pid,
            worker_start=worker_start,
            worker_fd=worker_fd,
            client=client,
            group=group,
            group_snapshot=group_snapshot,
        ) or not pinned_alive(main_process):
            report["outcome"] = "inconclusive"
            return report
        fault_at = time.monotonic()
        faulted = True
        base.command("/usr/bin/tailscale", "down", timeout=10)
        checks["tailscale_down_after_observation"] = stopped_backend_observed(base)
        if not checks["tailscale_down_after_observation"]:
            report["outcome"] = "inconclusive"
            return report
        phase = "watchdog_expiry"
        elapsed = report["elapsed_from_tailnet_down_seconds"]
        pinned = {
            "driver": {"pid": proc.pid, "start": driver_start, "fd": driver_fd},
            "worker": {"pid": worker_pid, "start": worker_start, "fd": worker_fd},
            "client": {"pid": client.pid, "start": client.start, "fd": client.pidfd},
            "guest_qemu": next(
                item
                for item in group_snapshot["processes"]
                if item["pid"] == group_snapshot["qemu_pid"]
            ),
            "kata_shim": next(
                item
                for item in group_snapshot["processes"]
                if item["pid"] == group_snapshot["shim_pid"]
            ),
        }
        (
            checks["installed_watchdog_stopped_service"],
            checks["whole_job_group_stopped_within_30s"],
            observation["group_reconstruction_seen"],
        ) = observe_job_termination(
            fault_at=fault_at,
            group_snapshot=group_snapshot,
            named=pinned,
            main_gone=lambda: not pinned_alive(main_process),
            elapsed=elapsed,
            uncertain_null=lambda: parent.uncertain_and_null(chat, key),
            reconstruction_evidence=raw_reconstruction,
            metadata_absent=lambda: runtime.inspect_owned(resource, token) is None,
            final_absent=lambda: (
                runtime.inspect_owned(resource, token) is None
                and runtime.all_resources() == []
                and observed.cgroup_tree_drained(base.SERVICE)
                and not same_container_candidates(group_snapshot["container_id"])
            ),
        )
        phase = "container_metadata"
        checks["exact_container_metadata_absent"] = (
            elapsed["container_metadata"] is not None
        )
        if not checks["exact_container_metadata_absent"]:
            metadata_deadline = time.monotonic() + 30
            while time.monotonic() < metadata_deadline:
                if runtime.inspect_owned(resource, token) is None:
                    elapsed["container_metadata"] = round(
                        time.monotonic() - fault_at, 3
                    )
                    checks["exact_container_metadata_absent"] = True
                    break
                time.sleep(0.1)
        phase = "run_state"
        checks["run_uncertain_null"] = elapsed[
            "uncertain_null"
        ] is not None or base.wait_for(lambda: parent.uncertain_and_null(chat, key), 10)
        if checks["run_uncertain_null"] and elapsed["uncertain_null"] is None:
            elapsed["uncertain_null"] = round(time.monotonic() - fault_at, 3)
        checks["no_late_recreation"] = checks[
            "exact_container_metadata_absent"
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
        if observed is not None and client is not None:
            observed.close_clients([client])
        close_group(group_snapshot)
        for fd in (worker_fd, driver_fd, main_fd):
            if fd is not None:
                os.close(fd)
        if run is None and proc is not None and proc.poll() is None:
            proc.terminate()
        if faulted and base is not None:
            try:
                if base.show(base.SERVICE, "ActiveState") == "active":
                    base.command(
                        "/usr/bin/systemctl",
                        "kill",
                        "--signal=SIGKILL",
                        "--kill-who=all",
                        base.SERVICE,
                    )
                    report["error_kind"] = (
                        report["error_kind"] or "WatchdogDidNotStopService"
                    )
                if not base.wait_for(
                    lambda: (
                        base.show(base.SERVICE, "ActiveState") in ("inactive", "failed")
                    ),
                    20,
                ):
                    raise RuntimeError("Identity service did not stop before recovery")
                recover_tailnet(
                    base,
                    service_drained=lambda: observed.cgroup_tree_drained(base.SERVICE),
                    on_step=recovery_steps.append,
                )
                if not wait_watchdog_socket(base, host_watchdog):
                    report["resolver_failure"] = {
                        "stage": "precondition",
                        "category": "watchdog_unhealthy",
                    }
                    raise RuntimeError("Watchdog socket health was not restored")
                checks["recovery_order_confirmed"] = recovery_steps == [
                    "stop_watchdog",
                    "reset_tailscaled_failure",
                    "start_tailscaled",
                    "verify_offline_shield",
                    "tailscale_up",
                    "start_watchdog",
                ]
            except Exception as exc:  # noqa: BLE001 - leave the service stopped.
                report["error_kind"] = report["error_kind"] or type(exc).__name__
                if report["resolver_failure"] is None:
                    report["resolver_failure"] = {
                        "stage": "precondition",
                        "category": "recovery_unverified",
                    }
        if (
            run is not None
            and parent is not None
            and base is not None
            and runtime is not None
        ):
            try:
                row = parent.live_row(chat, key)
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
                    not faulted
                    and row is not None
                    and row["status"] in ("queued", "running")
                ):
                    # An inconclusive pre-fault run must not be left serving.
                    base.command("/usr/bin/tailscale", "set", "--shields-up=true")
                    base.command(
                        "/usr/bin/systemctl",
                        "kill",
                        "--signal=SIGKILL",
                        "--kill-who=all",
                        base.SERVICE,
                    )
                    report["error_kind"] = (
                        report["error_kind"] or "PreFaultRunDidNotFinish"
                    )
                    base.wait_for(lambda: parent.uncertain_and_null(chat, key), 12)
                    row = parent.live_row(chat, key)
                if (
                    row is not None
                    and row["status"] in ("completed", "failed", "cancelled")
                    and not faulted
                    and proc is not None
                    and proc.poll() is None
                ):
                    proc.terminate()
                if faulted:
                    try:
                        refusal = resolver_precondition_failure(
                            row=row,
                            base=base,
                            runtime=runtime,
                            observed=observed,
                            host_watchdog=host_watchdog,
                            resource=resource,
                            token=token,
                        )
                    except Exception as exc:
                        report["resolver_failure"] = {
                            "stage": "precondition",
                            "category": resolver_refusal_category(exc),
                        }
                        raise
                    if refusal is not None:
                        report["resolver_failure"] = {
                            "stage": "precondition",
                            "category": refusal,
                        }
                    else:
                        checks["resolver_inspected_and_resolved"] = inspect_and_resolve(
                            run, report
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
                    faulted
                    and checks["recovery_order_confirmed"]
                    and checks["live_state_scoped_to_one_synthetic_run"]
                    and checks["resolver_inspected_and_resolved"]
                    and checks["final_namespace_and_cgroup_drained"]
                    and runtime.all_resources() == []
                    and base.service_active(base.WATCHDOG)
                    and base.shield_state() is True
                ):
                    state = base.show(base.SERVICE, "ActiveState")
                    if state in ("inactive", "failed"):
                        base.command(
                            "/usr/bin/systemctl", "start", base.SERVICE, timeout=380
                        )
                    immediately_restored = (
                        base.service_active(base.SERVICE)
                        and base.service_active(base.WATCHDOG)
                        and base.shield_state() is False
                    )
                    if not immediately_restored:
                        report["recovery_stability"]["failure_category"] = (
                            "immediate_state_mismatch"
                        )
                        fail_closed_after_unstable_recovery(
                            base,
                            service_drained=lambda: observed.cgroup_tree_drained(
                                base.SERVICE
                            ),
                        )
                    if immediately_restored:
                        try:
                            stable, stability = observe_recovery_stability(
                                base, host_watchdog, runtime
                            )
                            report["recovery_stability"] = stability
                        except Exception:
                            report["recovery_stability"]["failure_category"] = (
                                "probe_error"
                            )
                            fail_closed_after_unstable_recovery(
                                base,
                                service_drained=lambda: observed.cgroup_tree_drained(
                                    base.SERVICE
                                ),
                            )
                            raise
                        if not stable:
                            fail_closed_after_unstable_recovery(
                                base,
                                service_drained=lambda: observed.cgroup_tree_drained(
                                    base.SERVICE
                                ),
                            )
                        checks["service_and_watchdog_restored"] = stable
            except Exception as exc:  # noqa: BLE001 - leave Shields Up on failure.
                report["error_kind"] = report["error_kind"] or type(exc).__name__
        if root is not None:
            finish_evidence_root(root, raw_reconstruction, report, checks)
    report["passed"] = report["error_kind"] is None and all(checks.values())
    if report["passed"]:
        report["outcome"] = "passed"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True, type=Path)
    parser.add_argument("--driver", nargs=2, metavar=("KEY", "STATUS_PATH"))
    parser.add_argument("--preflight-only", action="store_true")
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
    report = run_check(args.release)
    print(
        "PRISM_WATCHDOG_ACTIVE_RESULT " + json.dumps(report, sort_keys=True), flush=True
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
