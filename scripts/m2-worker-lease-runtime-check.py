"""Test the installed worker lease against one synthetic Kata sleeper.

This is a direct, test-only lease/runtime check. It does not exercise approved
Jobs submission or prove systemd cgroup behavior or all later descendants.
Run only as root on the dedicated Linux/Kata host with an installed v6 release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

OBSERVE_SECONDS = 20
ABSENCE_SECONDS = 30
LATE_SECONDS = 5
POLL_SECONDS = 0.05
SLEEP_PROGRAM = "import time;time.sleep(12)\n"
CHECK_NAMES = (
    "dedicated_linux_host",
    "verified_installed_v6_source",
    "initial_namespace_empty",
    "reference_ready",
    "exact_owned_running_observed",
    "worker_and_client_observed",
    "own_controller_killed",
    "owned_resource_absent_within_30s",
    "worker_and_client_terminated",
    "no_late_recreation",
    "final_namespace_empty",
    "cleanup_confirmed",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verified_modules(release: Path):
    """Import only source files matching the installed v6 release manifest."""
    release = release.resolve(strict=True)
    if not release.is_dir() or not release.name.startswith("service-"):
        raise ValueError("Installed release path required")
    transfer = release.name.removeprefix("service-")
    if len(transfer) != 64 or any(c not in "0123456789abcdef" for c in transfer):
        raise ValueError("Invalid installed release name")
    manifest_path = (
        release.parent.parent / "service-updates" / transfer / "manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("kind") != "prism_service_update_v6"
        or manifest.get("release_name") != release.name
        or manifest.get("transfer_tar_sha256") != transfer
        or not isinstance(manifest.get("file_sha256"), dict)
    ):
        raise ValueError("Installed v6 manifest required")
    for relative in (
        "src/prism/__init__.py",
        "src/prism/worker.py",
        "src/prism/reference_runtime.py",
        "src/prism/engine.py",
        "src/prism/runtime.py",
    ):
        source = release / relative
        if source.is_symlink() or source.resolve(strict=True) != source:
            raise ValueError("Release source must be a regular installed file")
        if digest(source) != manifest["file_sha256"].get(relative):
            raise ValueError("Installed source digest mismatch")
    if manifest.get("worker_sha256") != manifest["file_sha256"]["src/prism/worker.py"]:
        raise ValueError("Installed worker digest mismatch")
    source_root = release / "src"
    sys.path.insert(0, str(source_root))
    from prism import reference_runtime, worker

    if (
        Path(reference_runtime.__file__).resolve()
        != source_root / "prism/reference_runtime.py"
        or Path(worker.__file__).resolve() != source_root / "prism/worker.py"
    ):
        raise ValueError("Installed worker import mismatch")
    return reference_runtime, worker, source_root


def process_identity(pid: int):
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        fields = stat[stat.rfind(")") + 2 :].split()
        return fields[19], fields[0]
    except (OSError, IndexError, ValueError):
        return None


def children(pid: int):
    try:
        raw = Path(f"/proc/{pid}/task/{pid}/children").read_text(encoding="ascii")
        return [int(value) for value in raw.split()]
    except (OSError, ValueError):
        return []


def terminated(pid: int, start: str) -> bool:
    current = process_identity(pid)
    return current is None or current[0] != start or current[1] in ("Z", "X")


def worker_child(release: Path, name: str, token: str, lease_fd: int) -> int:
    reference_runtime, worker, _ = verified_modules(release)
    cancelled = threading.Event()
    if not worker._start_lease_monitor(str(lease_fd), cancelled):
        return 2
    runtime = reference_runtime.ReferenceLinuxRuntime()
    runtime._execute(
        SLEEP_PROGRAM,
        [],
        timeout=20,
        cancel=cancelled,
        name=name,
        token=token,
    )
    return 0


def controller(release: Path, name: str, token: str) -> int:
    verified_modules(release)
    read_fd, write_fd = os.pipe()
    child = None
    try:
        os.write(write_fd, b"L")
        child = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--release",
                str(release),
                "--worker-child",
                name,
                token,
                str(read_fd),
            ],
            pass_fds=(read_fd,),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd="/",
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
        )
        os.close(read_fd)
        read_fd = -1
        deadline = time.monotonic() + 25
        while child.poll() is None and time.monotonic() < deadline:
            os.write(write_fd, b"L")
            time.sleep(0.5)
        return 0 if child.wait(timeout=5) == 0 else 3
    finally:
        if read_fd >= 0:
            os.close(read_fd)
        os.close(write_fd)


def run_check(release: Path) -> dict:
    checks = {name: False for name in CHECK_NAMES}
    report = {
        "kind": "m2-worker-lease-runtime-check",
        "schema": 1,
        "test_only_program": True,
        "approved_jobs_e2e": False,
        "systemd_cgroup_proven": False,
        "model_calls": 0,
        "pilot_ready": False,
        "checks": checks,
        "outcome": "failed",
        "error_kind": None,
        "error_phase": None,
        "observation_transient_inspect_count": 0,
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
    runtime = root = controller_process = None
    name = token = None
    controller_start = None
    tracked = []
    phase = "verify_release"
    try:
        reference_runtime, _, _ = verified_modules(release)
        checks["verified_installed_v6_source"] = True
        phase = "initial_namespace"
        runtime = reference_runtime.ReferenceLinuxRuntime()
        checks["initial_namespace_empty"] = runtime.all_resources() == []
        if not checks["initial_namespace_empty"]:
            raise RuntimeError("Reference namespace occupied")
        registry = reference_runtime.RuntimeRegistry(
            profile=reference_runtime.PROFILE, reference=runtime
        )
        phase = "readiness"
        registry.activate()
        checks["reference_ready"] = bool(
            registry.readiness and registry.readiness.get("ready") is True
        )
        if not checks["reference_ready"]:
            raise RuntimeError("Reference readiness failed")
        root = Path(tempfile.mkdtemp(prefix="prism-lease-runtime-")).resolve()
        os.chmod(root, 0o700)
        name = runtime.resource_name(uuid.uuid4().hex)
        token = uuid.uuid4().hex
        evidence = root / "owned-identity.json"
        evidence.write_text(json.dumps({"name": name, "token": token}) + "\n")
        evidence.chmod(0o600)
        controller_process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--release",
                str(release),
                "--controller",
                name,
                token,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd="/",
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
        )
        identity = process_identity(controller_process.pid)
        if identity is None:
            raise RuntimeError("Controller identity unavailable")
        controller_start = identity[0]
        deadline = time.monotonic() + OBSERVE_SECONDS
        phase = "observe_running"
        while time.monotonic() < deadline and controller_process.poll() is None:
            try:
                info = runtime.inspect_owned(name, token)
            except reference_runtime.EngineError as exc:
                # `ps` can observe the newly named Kata resource before
                # `inspect` has metadata. Retry only this precise creation
                # race; every other management/ownership error remains fatal.
                if str(exc) != "The named reference resource could not be inspected.":
                    raise
                report["observation_transient_inspect_count"] += 1
                time.sleep(POLL_SECONDS)
                continue
            state = info.get("State", {}) if info else {}
            running = isinstance(state, dict) and (
                state.get("Status") == "running" or state.get("Running") is True
            )
            if running:
                checks["exact_owned_running_observed"] = True
                workers = children(controller_process.pid)
                if len(workers) == 1:
                    clients = children(workers[0])
                    identities = [
                        process_identity(pid) for pid in (workers[0], *clients)
                    ]
                    if clients and all(identities):
                        tracked = [(workers[0], identities[0][0])] + [
                            (pid, observed[0])
                            for pid, observed in zip(
                                clients, identities[1:], strict=True
                            )
                        ]
                        checks["worker_and_client_observed"] = True
                        break
            time.sleep(POLL_SECONDS)
        if not checks["worker_and_client_observed"]:
            report["outcome"] = "inconclusive"
            return report
        phase = "pre_kill_identity"
        current = process_identity(controller_process.pid)
        if (
            controller_process.poll() is not None
            or current is None
            or current[0] != controller_start
            or runtime.inspect_owned(name, token) is None
        ):
            report["outcome"] = "inconclusive"
            return report
        killed_at = time.monotonic()
        phase = "kill_controller"
        controller_process.kill()  # Only the supervisor's own Popen child.
        controller_process.wait(timeout=5)
        checks["own_controller_killed"] = (
            controller_process.returncode == -signal.SIGKILL
        )
        deadline = killed_at + ABSENCE_SECONDS
        phase = "observe_absence"
        while time.monotonic() <= deadline:
            if (
                runtime.inspect_owned(name, token) is None
                and time.monotonic() <= deadline
            ):
                checks["owned_resource_absent_within_30s"] = True
                break
            time.sleep(POLL_SECONDS)
        if checks["owned_resource_absent_within_30s"]:
            checks["no_late_recreation"] = True
            late_deadline = time.monotonic() + LATE_SECONDS
            while time.monotonic() < late_deadline:
                if runtime.inspect_owned(name, token) is not None:
                    checks["no_late_recreation"] = False
                    break
                time.sleep(POLL_SECONDS)
        checks["worker_and_client_terminated"] = bool(
            tracked and all(terminated(pid, start) for pid, start in tracked)
        )
        phase = "final_namespace"
        checks["final_namespace_empty"] = runtime.all_resources() == []
    except Exception as exc:  # noqa: BLE001 - only bounded type enters output.
        report["error_kind"] = type(exc).__name__
        report["error_phase"] = phase
    finally:
        if controller_process is not None and controller_process.poll() is None:
            try:
                current = process_identity(controller_process.pid)
                if current and current[0] == controller_start:
                    controller_process.kill()
                    controller_process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired) as exc:
                report["error_kind"] = report["error_kind"] or type(exc).__name__
        if runtime is not None and name and token:
            try:
                runtime.reconcile(name, token, settle_seconds=3)
                # Also watch failures: an orphan may create the exact resource
                # after the first reconciliation. Never kill another resource.
                if report["outcome"] != "passed" and not checks["no_late_recreation"]:
                    late_deadline = time.monotonic() + LATE_SECONDS
                    while time.monotonic() < late_deadline:
                        if runtime.inspect_owned(name, token) is not None:
                            runtime.reconcile(name, token, settle_seconds=3)
                        time.sleep(POLL_SECONDS)
            except Exception as exc:  # noqa: BLE001
                report["error_kind"] = report["error_kind"] or type(exc).__name__
        if runtime is not None and checks["initial_namespace_empty"]:
            try:
                checks["final_namespace_empty"] = runtime.all_resources() == []
            except Exception as exc:  # noqa: BLE001
                report["error_kind"] = report["error_kind"] or type(exc).__name__
        checks["cleanup_confirmed"] = bool(
            controller_process is not None
            and controller_process.poll() is not None
            and tracked
            and all(terminated(pid, start) for pid, start in tracked)
            and checks["no_late_recreation"]
            and checks["final_namespace_empty"]
            and report["outcome"] != "inconclusive"
        )
        if root is not None:
            if report["error_kind"] is None and all(checks.values()):
                try:
                    shutil.rmtree(root)
                except OSError as exc:
                    checks["cleanup_confirmed"] = False
                    report["error_kind"] = type(exc).__name__
            else:
                report["evidence_retained"] = True
    report["passed"] = report["error_kind"] is None and all(checks.values())
    if report["passed"]:
        report["outcome"] = "passed"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--controller", nargs=2, metavar=("NAME", "TOKEN"))
    parser.add_argument("--worker-child", nargs=3, metavar=("NAME", "TOKEN", "FD"))
    args = parser.parse_args()
    if args.controller:
        try:
            return controller(args.release, *args.controller)
        except Exception:  # noqa: BLE001 - supervisor keeps bounded report.
            return 2
    if args.worker_child:
        try:
            name, token, fd = args.worker_child
            return worker_child(args.release, name, token, int(fd))
        except Exception:  # noqa: BLE001 - supervisor keeps bounded report.
            return 3
    report = run_check(args.release)
    print(
        "PRISM_WORKER_LEASE_RUNTIME_RESULT " + json.dumps(report, sort_keys=True),
        flush=True,
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
