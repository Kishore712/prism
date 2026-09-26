"""Check one named-recipient revoke against the private reference-Linux runtime.

Run only on the dedicated Kata host. This creates an isolated synthetic project,
database, version, invitation, grant, session, and fixed JSON-check run. It
never connects to the running identity service or its database.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import tempfile
import time
from pathlib import Path

from prism.jobs import Jobs
from prism.projects import ProjectSource
from prism.reference_runtime import PROFILE, ReferenceLinuxRuntime, RuntimeRegistry
from prism.sharing import Denied, NamedPrincipal, Store

OBSERVE_SECONDS = 20
TERMINAL_SECONDS = 45
POLL_SECONDS = 0.05
REQUIRED_CHECKS = (
    "dedicated_linux_host",
    "initial_namespace_empty",
    "reference_ready",
    "exact_owned_running_observed",
    "exact_resource_bound",
    "test_grant_revoked",
    "subsequent_get_denied",
    "subsequent_submit_denied",
    "revoke_termination_within_bound",
    "terminal_cancelled_without_result",
    "exact_resource_absent",
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
                "id": "active-revoke-check",
                "title": "Synthetic active revoke check",
                "files": ["valid.json"],
                "action": "json-check",
            }
        ),
        encoding="utf-8",
    )
    return ProjectSource.from_manifest(
        (root / ".prism-project.json").resolve(), action_profile=PROFILE
    )


def denied(call) -> bool:
    try:
        call()
    except Denied:
        return True
    return False


def run_row(store: Store, run_id: str):
    with store.connect() as db:
        row = db.execute(
            "SELECT status,result,error,runtime_profile,runtime_resource,runtime_token "
            "FROM runs WHERE id=?",
            (run_id,),
        ).fetchone()
        return dict(row) if row else None


def wait_owned_running(store: Store, runtime: ReferenceLinuxRuntime, run_id: str):
    deadline = time.monotonic() + OBSERVE_SECONDS
    latest = None
    while time.monotonic() < deadline:
        latest = run_row(store, run_id)
        if latest is None or latest["status"] not in ("queued", "running"):
            break
        resource, token = latest["runtime_resource"], latest["runtime_token"]
        if resource and token:
            info = runtime.inspect_owned(resource, token)
            state = info.get("State", {}) if info else {}
            if isinstance(state, dict) and (
                state.get("Status") == "running" or state.get("Running") is True
            ):
                return True, latest
        time.sleep(POLL_SECONDS)
    return False, latest


def wait_terminal(store: Store, run_id: str):
    deadline = time.monotonic() + TERMINAL_SECONDS
    row = run_row(store, run_id)
    while (
        row and row["status"] in ("queued", "running") and time.monotonic() < deadline
    ):
        time.sleep(POLL_SECONDS)
        row = run_row(store, run_id)
    return row


def run_check() -> dict:
    checks: dict[str, bool] = {}
    report: dict = {
        "kind": "m2-active-revoke-check",
        "schema": 1,
        "model_calls": 0,
        "pilot_ready": False,
        "checks": checks,
        "terminal_status": "not_started",
        "error_kind": None,
    }
    if (
        platform.system() != "Linux"
        or platform.machine() != "x86_64"
        or os.geteuid() != 0
    ):
        checks["dedicated_linux_host"] = False
        report["passed"] = False
        return report
    checks["dedicated_linux_host"] = True

    runtime = store = jobs = temp_root = None
    run_id = grant_id = None
    revoked = False
    try:
        runtime = ReferenceLinuxRuntime()
        checks["initial_namespace_empty"] = runtime.all_resources() == []
        if not checks["initial_namespace_empty"]:
            raise RuntimeError("Reference namespace is occupied")
        registry = RuntimeRegistry(profile=PROFILE, reference=runtime)
        registry.activate()
        checks["reference_ready"] = bool(
            registry.readiness and registry.readiness.get("ready") is True
        )
        if not checks["reference_ready"]:
            raise RuntimeError("Reference readiness failed")

        temp_root = Path(tempfile.mkdtemp(prefix="prism-active-revoke-")).resolve()
        source = write_project(temp_root / "project")
        store = Store(temp_root / "state.sqlite")
        candidate = store.candidate(
            source.freeze(
                ["valid.json"], "Check one synthetic JSON file under revoke.", "verify"
            ),
            project_id=source.project_id,
        )
        store.approve(candidate["id"], candidate["digest"])
        recipient = NamedPrincipal(
            "https://synthetic.example/active-revoke", "test-recipient"
        )
        invitation = store.create_invitation(
            candidate["id"], recipient, mode="verify", expires_in=300
        )
        session = store.redeem_invitation(invitation["token"], recipient)
        grant_id = session["grant_id"]
        checks["test_grant_revoked"] = False
        jobs = Jobs(store, recover=False, registry=registry)
        submitted = jobs.submit_json_check(
            session["id"], recipient, "active-revoke-json-check"
        )
        run_id = submitted["id"]
        observed, at_revoke = wait_owned_running(store, runtime, run_id)
        checks["exact_owned_running_observed"] = observed
        checks["exact_resource_bound"] = bool(
            at_revoke
            and at_revoke["runtime_profile"] == PROFILE
            and at_revoke["runtime_resource"] == runtime.resource_name(run_id)
            and at_revoke["runtime_token"]
        )
        # The short action may finish before observation. Revoke only this
        # temporary grant anyway, and report that race as inconclusive.
        store.revoke_grant(grant_id)
        revoked = True
        checks["test_grant_revoked"] = True
        checks["subsequent_get_denied"] = denied(
            lambda: jobs.get(session["id"], recipient, run_id)
        )
        checks["subsequent_submit_denied"] = denied(
            lambda: jobs.submit_json_check(
                session["id"], recipient, "after-active-revoke"
            )
        )
        before_shutdown = wait_terminal(store, run_id)
        checks["revoke_termination_within_bound"] = bool(
            before_shutdown and before_shutdown["status"] not in ("queued", "running")
        )
    except Exception as exc:  # noqa: BLE001 - emit a bounded report for host failures.
        report["error_kind"] = type(exc).__name__
    finally:
        if store is not None and grant_id and not revoked:
            try:
                store.revoke_grant(grant_id)
                checks["test_grant_revoked"] = True
            except Exception as exc:  # noqa: BLE001 - preserve the original failure.
                report["error_kind"] = report["error_kind"] or type(exc).__name__
        shutdown_failed = False
        if jobs is not None:
            try:
                jobs.shutdown()
            except Exception as exc:  # noqa: BLE001 - retain state for inspection.
                shutdown_failed = True
                report["error_kind"] = report["error_kind"] or type(exc).__name__

        # A submit can reserve a run before returning its ID. This temporary
        # database contains at most one run, so recover that exact identity.
        if run_id is None and jobs is not None and store is not None:
            try:
                with store.connect() as db:
                    reserved = list(db.execute("SELECT id FROM runs"))
                if len(reserved) == 1:
                    run_id = reserved[0]["id"]
            except Exception as exc:  # noqa: BLE001 - retain uncertain state.
                report["error_kind"] = report["error_kind"] or type(exc).__name__

        final_row = None
        if run_id is not None and store is not None:
            try:
                final_row = run_row(store, run_id)
                report["terminal_status"] = (
                    final_row["status"] if final_row else "missing"
                )
            except Exception as exc:  # noqa: BLE001 - retain uncertain state.
                report["error_kind"] = report["error_kind"] or type(exc).__name__
        worker_alive = bool(
            jobs is not None and any(thread.is_alive() for thread in list(jobs.threads))
        )
        report["worker_alive_after_shutdown"] = worker_alive

        resource = final_row["runtime_resource"] if final_row else None
        token = final_row["runtime_token"] if final_row else None
        exact_identity = bool(
            runtime is not None
            and run_id is not None
            and final_row
            and final_row["runtime_profile"] == PROFILE
            and resource == runtime.resource_name(run_id)
            and token
        )
        owned_absent = False
        if exact_identity:
            try:
                owned_absent = runtime.inspect_owned(resource, token) is None
            except Exception:  # noqa: BLE001 - unknown ownership remains uncertain.
                owned_absent = False

        needs_reconcile = bool(
            run_id is not None
            and (
                shutdown_failed
                or worker_alive
                or final_row is None
                or final_row["status"] in ("queued", "running", "uncertain")
                or not owned_absent
            )
        )
        report["exact_reconcile_attempted"] = bool(needs_reconcile and exact_identity)
        if needs_reconcile and exact_identity:
            try:
                runtime.reconcile(resource, token, settle_seconds=3)
            except Exception as exc:  # noqa: BLE001 - retain the exact run state.
                report["reconcile_error_kind"] = type(exc).__name__
            try:
                owned_absent = runtime.inspect_owned(resource, token) is None
            except Exception:  # noqa: BLE001 - unknown ownership remains uncertain.
                owned_absent = False
        checks["exact_resource_absent"] = bool(exact_identity and owned_absent)

        namespace_empty = False
        if runtime is not None and checks.get("initial_namespace_empty"):
            try:
                namespace_empty = runtime.all_resources() == []
            except Exception as exc:  # noqa: BLE001 - do not claim empty.
                report["namespace_error_kind"] = type(exc).__name__
        checks["final_namespace_empty"] = namespace_empty
        checks["terminal_cancelled_without_result"] = bool(
            final_row
            and final_row["status"] == "cancelled"
            and final_row["result"] is None
            and not worker_alive
            and not needs_reconcile
        )
        cleanup_confirmed = bool(
            temp_root is not None
            and not shutdown_failed
            and not worker_alive
            and not needs_reconcile
            and namespace_empty
            and (
                jobs is None
                or (
                    checks["exact_resource_absent"]
                    and final_row
                    and final_row["status"] in ("cancelled", "completed")
                )
            )
        )
        checks["cleanup_confirmed"] = cleanup_confirmed
        report["cleanup_status"] = "confirmed" if cleanup_confirmed else "uncertain"
        if temp_root is not None:
            if cleanup_confirmed:
                try:
                    shutil.rmtree(temp_root)
                except OSError as exc:
                    cleanup_confirmed = False
                    checks["cleanup_confirmed"] = False
                    report["cleanup_status"] = "uncertain"
                    report["error_kind"] = report["error_kind"] or type(exc).__name__
            if not cleanup_confirmed:
                report["inspection"] = {
                    "temporary_directory": str(temp_root),
                    "database": str(temp_root / "state.sqlite"),
                    "run_id": run_id,
                    "resource": resource,
                    "token_column": "runs.runtime_token",
                    "exact_resource_absent": checks["exact_resource_absent"],
                    "namespace_empty": namespace_empty,
                }
    report["passed"] = report["error_kind"] is None and all(
        checks.get(name) is True for name in REQUIRED_CHECKS
    )
    return report


def main() -> int:
    report = run_check()
    print(
        "PRISM_ACTIVE_REVOKE_RESULT " + json.dumps(report, sort_keys=True), flush=True
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
