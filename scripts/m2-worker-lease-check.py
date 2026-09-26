"""Exercise one Jobs-to-worker lease after a controller crash on a Kata host.

Run only from the installed release on the dedicated reference Linux host. The
test uses its own temporary SQLite database and synthetic project. It never
opens the service database or signals the service process.

This tests one isolated controller PID. It does not establish systemd cgroup
behavior or termination of descendants forked after the observed client tree.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import prism
from prism.jobs import Jobs
from prism.projects import ProjectSource
from prism.reference_runtime import PROFILE, ReferenceLinuxRuntime, RuntimeRegistry
from prism.sharing import NamedPrincipal, Store

OBSERVE_SECONDS = 20
ABSENCE_SECONDS = 30
LATE_SECONDS = 5
POLL_SECONDS = 0.05
RECIPIENT = NamedPrincipal("https://synthetic.example/worker-lease", "test-recipient")
REQUIRED = (
    "dedicated_linux_host",
    "initial_namespace_empty",
    "reference_ready",
    "one_run_reserved",
    "exact_owned_running_observed",
    "worker_and_client_observed",
    "own_controller_killed",
    "owned_resource_absent_within_30s",
    "no_late_recreation",
    "controller_worker_client_terminated",
    "recovery_uncertain_without_result",
    "recovery_did_not_replay",
    "final_namespace_empty",
    "cleanup_confirmed",
)


def write_project(root: Path) -> ProjectSource:
    root.mkdir()
    (root / "valid.json").write_text('{"synthetic":true,"value":7}\n', encoding="utf-8")
    (root / ".prism-project.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "id": "worker-lease-check",
                "title": "Synthetic worker lease check",
                "files": ["valid.json"],
                "action": "json-check",
            }
        ),
        encoding="utf-8",
    )
    return ProjectSource.from_manifest(
        (root / ".prism-project.json").resolve(), action_profile=PROFILE
    )


def row_for_only_run(store: Store):
    with store.connect() as db:
        rows = list(
            db.execute(
                "SELECT id,status,result,runtime_profile,runtime_resource,runtime_token "
                "FROM runs"
            )
        )
    return dict(rows[0]) if len(rows) == 1 else None, len(rows)


def process_identity(pid: int):
    """Return Linux start time and state; start time guards against PID reuse."""
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


def no_live_process(pid: int, start: str) -> bool:
    identity = process_identity(pid)
    return identity is None or identity[0] != start or identity[1] in ("Z", "X")


def controller(root: Path, session_id: str) -> int:
    # This process is deliberately the only Jobs lease owner. Its stdout and
    # stderr are discarded by the independent supervisor.
    store = Store(root / "state.sqlite")
    registry = RuntimeRegistry(profile=PROFILE, reference=ReferenceLinuxRuntime())
    registry.activate()
    if not registry.readiness or registry.readiness.get("ready") is not True:
        return 2
    jobs = Jobs(store, recover=False, registry=registry)
    jobs.submit_json_check(session_id, RECIPIENT, "worker-lease-json-check")
    time.sleep(OBSERVE_SECONDS + ABSENCE_SECONDS + LATE_SECONDS + 10)
    return 3  # A live controller must be killed by the supervisor first.


def run_check() -> dict:
    checks = {name: False for name in REQUIRED}
    report = {
        "kind": "m2-worker-lease-check",
        "schema": 1,
        "model_calls": 0,
        "pilot_ready": False,
        "checks": checks,
        "outcome": "failed",
        "error_kind": None,
        "evidence_retained": False,
        "passed": False,
    }
    if (
        platform.system() != "Linux"
        or platform.machine() != "x86_64"
        or os.geteuid() != 0
    ):
        return report | {"error_kind": "UnsupportedHost", "passed": False}
    checks["dedicated_linux_host"] = True

    runtime = store = root = child = None
    run_id = resource = token = None
    observed = False
    tracked = []
    child_start = None
    try:
        runtime = ReferenceLinuxRuntime()
        checks["initial_namespace_empty"] = runtime.all_resources() == []
        if not checks["initial_namespace_empty"]:
            raise RuntimeError("Reference namespace occupied")
        registry = RuntimeRegistry(profile=PROFILE, reference=runtime)
        registry.activate()
        checks["reference_ready"] = bool(
            registry.readiness and registry.readiness.get("ready") is True
        )
        if not checks["reference_ready"]:
            raise RuntimeError("Reference readiness failed")

        root = Path(tempfile.mkdtemp(prefix="prism-worker-lease-")).resolve()
        os.chmod(root, 0o700)
        source = write_project(root / "project")
        store = Store(root / "state.sqlite")
        Jobs(
            store, recover=False, registry=registry
        )  # Create only the isolated run table.
        candidate = store.candidate(
            source.freeze(["valid.json"], "Check one synthetic JSON file.", "verify"),
            project_id=source.project_id,
        )
        store.approve(candidate["id"], candidate["digest"])
        invitation = store.create_invitation(
            candidate["id"], RECIPIENT, mode="verify", expires_in=300
        )
        session = store.redeem_invitation(invitation["token"], RECIPIENT)
        child = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--controller",
                str(root),
                session["id"],
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd="/",
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "LANG": "C.UTF-8",
                "PYTHONPATH": str(Path(prism.__file__).resolve().parent.parent),
            },
        )
        identity = process_identity(child.pid)
        if identity is None:
            raise RuntimeError("Child controller identity unavailable")
        child_start = identity[0]

        deadline = time.monotonic() + OBSERVE_SECONDS
        while time.monotonic() < deadline and child.poll() is None:
            row, count = row_for_only_run(store)
            checks["one_run_reserved"] = count == 1
            if row:
                run_id = row["id"]
                resource, token = row["runtime_resource"], row["runtime_token"]
                exact = bool(
                    row["runtime_profile"] == PROFILE
                    and resource == runtime.resource_name(run_id)
                    and token
                )
                if exact and row["status"] == "running":
                    info = runtime.inspect_owned(resource, token)
                    state = info.get("State", {}) if info else {}
                    observed = isinstance(state, dict) and (
                        state.get("Status") == "running" or state.get("Running") is True
                    )
                    if observed:
                        worker_pids = children(child.pid)
                        if len(worker_pids) == 1:
                            client_pids = children(worker_pids[0])
                            identities = [
                                process_identity(pid)
                                for pid in (worker_pids[0], *client_pids)
                            ]
                            if client_pids and all(identities):
                                tracked = [(worker_pids[0], identities[0][0])] + [
                                    (pid, identity[0])
                                    for pid, identity in zip(
                                        client_pids, identities[1:], strict=True
                                    )
                                ]
                                checks["worker_and_client_observed"] = True
                                break
            time.sleep(POLL_SECONDS)
        checks["exact_owned_running_observed"] = observed
        if not observed or not checks["worker_and_client_observed"]:
            report["outcome"] = "inconclusive"
            return report

        live_row, live_count = row_for_only_run(store)
        if (
            live_count != 1
            or live_row is None
            or live_row["id"] != run_id
            or live_row["status"] != "running"
            or runtime.inspect_owned(resource, token) is None
        ):
            report["outcome"] = "inconclusive"
            return report

        # The PID and start time are from the Popen child created above. No
        # other PID, process group, unit, or service is ever signalled.
        current_identity = process_identity(child.pid)
        if (
            child.poll() is not None
            or current_identity is None
            or current_identity[0] != child_start
        ):
            report["outcome"] = "inconclusive"
            return report
        killed_at = time.monotonic()
        child.kill()
        child.wait(timeout=5)
        checks["own_controller_killed"] = child.returncode == -signal.SIGKILL

        deadline = killed_at + ABSENCE_SECONDS
        while time.monotonic() <= deadline:
            if (
                runtime.inspect_owned(resource, token) is None
                and time.monotonic() <= deadline
            ):
                checks["owned_resource_absent_within_30s"] = True
                break
            time.sleep(POLL_SECONDS)
        if checks["owned_resource_absent_within_30s"]:
            late_deadline = time.monotonic() + LATE_SECONDS
            checks["no_late_recreation"] = True
            while time.monotonic() < late_deadline:
                if runtime.inspect_owned(resource, token) is not None:
                    checks["no_late_recreation"] = False
                    break
                time.sleep(POLL_SECONDS)
        checks["controller_worker_client_terminated"] = bool(
            no_live_process(child.pid, child_start)
            and tracked
            and all(no_live_process(pid, start) for pid, start in tracked)
        )

        before, count_before = row_for_only_run(store)
        if before and count_before == 1:
            recovered = Jobs(
                store,
                recover=True,
                registry=RuntimeRegistry(profile=PROFILE, reference=runtime),
            )
            after, count_after = row_for_only_run(store)
            checks["recovery_uncertain_without_result"] = bool(
                after
                and after["id"] == run_id
                and after["status"] == "uncertain"
                and after["result"] is None
            )
            checks["recovery_did_not_replay"] = bool(
                count_after == count_before == 1
                and not recovered.threads
                and after
                and after["id"] == run_id
            )
        checks["final_namespace_empty"] = runtime.all_resources() == []
    except Exception as exc:  # noqa: BLE001 - only exception type enters bounded output.
        report["error_kind"] = type(exc).__name__
    finally:
        early_inconclusive = report["outcome"] == "inconclusive"
        if child is not None and child.poll() is None:
            # Only the exact Popen child is eligible for termination.
            try:
                if (
                    process_identity(child.pid)
                    and process_identity(child.pid)[0] == child_start
                ):
                    child.kill()
                    child.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired) as exc:
                report["error_kind"] = report["error_kind"] or type(exc).__name__
        if store is not None and runtime is not None and (not resource or not token):
            try:
                last_row, count = row_for_only_run(store)
                if count == 1 and last_row is not None:
                    run_id = last_row["id"]
                    if (
                        last_row["runtime_profile"] == PROFILE
                        and last_row["runtime_resource"]
                        == runtime.resource_name(run_id)
                        and last_row["runtime_token"]
                    ):
                        resource = last_row["runtime_resource"]
                        token = last_row["runtime_token"]
            except Exception as exc:  # noqa: BLE001 - exact ownership stays unknown.
                report["error_kind"] = report["error_kind"] or type(exc).__name__
        exact_cleanup_identity = False
        if runtime is not None and run_id is not None and resource and token:
            try:
                exact_cleanup_identity = resource == runtime.resource_name(run_id)
            except ValueError:
                report["error_kind"] = report["error_kind"] or "ValueError"
        if exact_cleanup_identity:
            try:
                runtime.reconcile(resource, token, settle_seconds=3)
            except Exception as exc:  # noqa: BLE001 - preserve evidence if cleanup fails.
                report["error_kind"] = report["error_kind"] or type(exc).__name__
            if early_inconclusive:
                # A worker orphaned before observation may create its resource
                # after the first exact cleanup. Watch through the lease window,
                # and only reconcile this stored owned identity if it appears.
                late_deadline = time.monotonic() + LATE_SECONDS
                while time.monotonic() < late_deadline:
                    try:
                        if runtime.inspect_owned(resource, token) is not None:
                            runtime.reconcile(resource, token, settle_seconds=3)
                    except Exception as exc:  # noqa: BLE001
                        report["error_kind"] = (
                            report["error_kind"] or type(exc).__name__
                        )
                        break
                    time.sleep(POLL_SECONDS)
        if runtime is not None and checks["initial_namespace_empty"]:
            try:
                checks["final_namespace_empty"] = runtime.all_resources() == []
            except Exception as exc:  # noqa: BLE001
                report["error_kind"] = report["error_kind"] or type(exc).__name__
        # A clear namespace alone does not prove the observed worker/client
        # processes exited. Keep that distinction even on inconclusive runs.
        tracked_terminated = bool(tracked) and all(
            no_live_process(pid, start) for pid, start in tracked
        )
        checks["cleanup_confirmed"] = bool(
            (child is None or child.poll() is not None)
            and (child is None or tracked_terminated)
            and checks["final_namespace_empty"]
            and not early_inconclusive
        )
        if root is not None:
            passed = report["error_kind"] is None and all(checks.values())
            if passed:
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
    if len(sys.argv) == 4 and sys.argv[1] == "--controller":
        try:
            return controller(Path(sys.argv[2]), sys.argv[3])
        except Exception:  # noqa: BLE001 - supervisor reports bounded failure.
            return 2
    if len(sys.argv) != 1:
        return 2
    report = run_check()
    print("PRISM_WORKER_LEASE_RESULT " + json.dumps(report, sort_keys=True), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
