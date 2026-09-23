"""Exercise the approved M2.3 reference Linux runtime on its dedicated host."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import platform
import re
import tempfile
import threading
import time
import traceback
from pathlib import Path

from prism.conversation import Conversations
from prism.engine import EngineError
from prism.handoff import Handoffs, HandoffSelection
from prism.jobs import Jobs
from prism.owner import OwnerIdentity, OwnerWorkspace
from prism.projects import ProjectSource
from prism.reference_runtime import (
    HANDLER,
    PROFILE,
    ReferenceLinuxRuntime,
    RuntimeRegistry,
)
from prism.runtime import OUTPUT_LIMIT, probe_program
from prism.sharing import Denied, Store, ident, packed

PROBE_METADATA = (
    "import json,platform,pathlib;print(json.dumps({"
    '"guest_kernel":platform.release(),'
    '"guest_boot_id":pathlib.Path("/proc/sys/kernel/random/boot_id").read_text().strip()}),flush=True)\n'
)


def bounded_exception(exc, checks):
    """Return a bounded, de-identified exception chain for host acceptance."""
    chain = []
    current = exc
    for _ in range(4):
        if current is None:
            break
        if isinstance(current, EngineError):
            message = " ".join(str(current).split())
        elif isinstance(current, OSError):
            message = f"errno={current.errno}" if current.errno is not None else ""
        else:
            message = ""
        message = re.sub(r"\b[0-9a-f]{32,64}\b", "<redacted-id>", message)
        message = re.sub(r"(?<!\w)/(?:[^\s'\"),]+)", "<redacted-path>", message)[:512]
        frames = []
        for frame in traceback.extract_tb(current.__traceback__)[-8:]:
            basename = Path(frame.filename).name
            if basename in {"reference_runtime.py", "m2-linux-runtime-check.py"}:
                frames.append(
                    {"file": basename, "function": frame.name, "line": frame.lineno}
                )
        chain.append(
            {"type": type(current).__name__, "message": message, "frames": frames}
        )
        current = current.__cause__ or current.__context__
    stage = (
        "reference_runtime_host_checks"
        if "host_checks_completed" not in checks
        else (
            "initial_namespace_inspection"
            if "initial_namespace_empty" not in checks
            else "integration_exercise"
        )
    )
    return {"stage": stage, "chain": chain}


class Validation:
    def __init__(self):
        self.checks: dict[str, bool] = {}
        self.details: dict[str, object] = {}
        self.boot_ids: list[str] = []

    def check(self, name: str, condition: bool, detail=None):
        passed = bool(condition)
        self.checks[name] = passed
        if detail is not None:
            self.details[name] = detail
        print(f"PRISM_M23_CHECK {name}: {'PASS' if passed else 'FAIL'}", flush=True)
        return passed

    def guest(self, name: str, kernel, boot_id):
        valid = (
            isinstance(kernel, str)
            and bool(kernel)
            and kernel != platform.release()
            and isinstance(boot_id, str)
            and bool(boot_id)
        )
        self.check(name, valid)
        if valid:
            self.boot_ids.append(boot_id)
        return valid


def write_project(root: Path, project_id: str) -> ProjectSource:
    root.mkdir()
    (root / "valid.json").write_text('{"items":[1,2],"ready":true}\n', encoding="utf-8")
    (root / "invalid.json").write_text('{"items":[1,}\n', encoding="utf-8")
    (root / "README.md").write_text(
        "# Synthetic runtime acceptance project\n", encoding="utf-8"
    )
    (root / ".prism-project.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "id": project_id,
                "title": "Synthetic JSON runtime acceptance",
                "files": ["valid.json", "invalid.json", "README.md"],
                "action": "json-check",
            }
        ),
        encoding="utf-8",
    )
    return ProjectSource.from_manifest(
        (root / ".prism-project.json").resolve(), action_profile=PROFILE
    )


def denied(call, *, status=None):
    try:
        call()
    except Denied as exc:
        return status is None or exc.status == status
    return False


def wait_run(jobs, session, actor, run, timeout=90):
    deadline = time.monotonic() + timeout
    while run["status"] in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.05)
        run = jobs.get(session, actor, run["id"])
    return run


def wait_dispatch_observation(store, runtime, run_id, timeout=20, poll=0.05):
    """Wait until one reserved run is owned by the live Kata runtime or finishes."""
    deadline = time.monotonic() + timeout
    latest = {
        "status": "missing",
        "resource": None,
        "token": None,
        "owned_running": False,
    }
    while time.monotonic() < deadline:
        with store.connect() as db:
            row = db.execute(
                "SELECT status,runtime_resource,runtime_token FROM runs WHERE id=?",
                (run_id,),
            ).fetchone()
        if row is None:
            return latest
        latest = {
            "status": row["status"],
            "resource": row["runtime_resource"],
            "token": row["runtime_token"],
            "owned_running": False,
        }
        if row["status"] not in ("queued", "running"):
            return latest
        if (
            row["runtime_resource"]
            and row["runtime_token"]
            and (
                owned := runtime.inspect_owned(
                    row["runtime_resource"], row["runtime_token"]
                )
            )
            is not None
            and isinstance(owned.get("State"), dict)
            and owned["State"].get("Status") == "running"
        ):
            latest["owned_running"] = True
            return latest
        time.sleep(poll)
    return latest


def cancelled_after_active_revoke(active_at_revoke, record, resource_absent):
    """Accept cancellation only with a trusted terminal record and exact cleanup."""
    return bool(
        active_at_revoke
        and record
        and record.get("status") == "cancelled"
        and record.get("result") is None
        and record.get("error")
        == "Execution was cancelled and its container was removed."
        and resource_absent
    )


def run_count(store):
    with store.connect() as db:
        return db.execute("SELECT count(*) FROM runs").fetchone()[0]


def guest_helpers():
    """Read process names only on the dedicated host; never collect arguments."""
    found = []
    for path in Path("/proc").glob("[0-9]*/comm"):
        try:
            name = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            continue
        if name.startswith(("qemu-system", "virtiofsd", "containerd-shim")):
            found.append(name)
    return sorted(found)


def parse_probe_metadata(result):
    first, separator, remainder = result.stdout.partition("\n")
    if not separator:
        raise EngineError("The boundary probe omitted guest provenance.")
    try:
        metadata = json.loads(first)
    except ValueError as exc:
        raise EngineError(
            "The boundary probe returned invalid guest provenance."
        ) from exc
    if set(metadata) != {"guest_kernel", "guest_boot_id"}:
        raise EngineError("The boundary probe returned incomplete guest provenance.")
    return metadata, remainder


def direct_probe(runtime, action, arguments, *, timeout, cancel=None):
    program = PROBE_METADATA + probe_program()
    result = runtime._execute(
        program,
        arguments,
        timeout=timeout,
        cancel=cancel,
        name="prism-m23-run-" + ident(),
        token=ident(),
        result_action=action,
    )
    metadata, output = parse_probe_metadata(result)
    return result, metadata, output


def check_boundaries(validation, runtime, root):
    canary = root / "host-only-canary"
    canary.write_text("HOST_ONLY_M23_ACCEPTANCE_CANARY\n", encoding="utf-8")
    os.chmod(canary, 0o600)
    canary_hash = hashlib.sha256(canary.read_bytes()).hexdigest()
    previous_canary = os.environ.get("PRISM_PRIVATE_CANARY")
    os.environ["PRISM_PRIVATE_CANARY"] = "PRISM_SYNTHETIC_ENVIRONMENT_CANARY"

    try:
        for index in range(2):
            result, metadata, output = direct_probe(
                runtime,
                "boundaries",
                ["boundaries", str(canary), "rlimit"],
                timeout=15,
            )
            validation.guest(
                f"boundary_guest_identity_{index + 1}",
                metadata.get("guest_kernel"),
                metadata.get("guest_boot_id"),
            )
            try:
                checks = json.loads(output)
            except ValueError:
                checks = {}
            expected = {
                "non_root",
                "no_new_privileges",
                "capabilities_dropped",
                "seccomp_active",
                "host_canary_absent",
                "credentials_absent",
                "control_sockets_absent",
                "session_marker_absent",
                "root_read_only",
                "no_active_external_interface",
                "external_network_denied",
                "metadata_network_denied",
                "scratch_writable",
                "memory_limit",
                "swap_disabled",
                "process_limit",
                "process_limit_cannot_be_raised",
                "cpu_limit",
                "scratch_limit",
            }
            validation.check(
                f"boundary_policy_{index + 1}",
                result.exit_code == 0
                and result.stop_reason == "exited"
                and result.cleaned_up
                and set(checks) == expected
                and all(checks.values()),
                sorted(name for name in expected if checks.get(name) is not True),
            )
    finally:
        if previous_canary is None:
            os.environ.pop("PRISM_PRIVATE_CANARY", None)
        else:
            os.environ["PRISM_PRIVATE_CANARY"] = previous_canary
    validation.check(
        "host_canary_unchanged",
        hashlib.sha256(canary.read_bytes()).hexdigest() == canary_hash,
    )

    result, metadata, output = direct_probe(
        runtime, "processes", ["processes"], timeout=15
    )
    validation.guest(
        "process_probe_guest_identity",
        metadata.get("guest_kernel"),
        metadata.get("guest_boot_id"),
    )
    try:
        process_result = json.loads(output)
    except ValueError:
        process_result = {}
    validation.check(
        "process_limit_enforced",
        result.exit_code == 0
        and result.stop_reason == "exited"
        and result.cleaned_up
        and process_result.get("process_creation_blocked") is True
        and 1 <= process_result.get("children_created", 0) < 40,
    )

    result, metadata, _ = direct_probe(runtime, "flood", ["flood"], timeout=15)
    validation.guest(
        "flood_probe_guest_identity",
        metadata.get("guest_kernel"),
        metadata.get("guest_boot_id"),
    )
    validation.check(
        "output_limit_enforced",
        result.stop_reason == "output_limit"
        and result.output_limited
        and result.cleaned_up
        and len(result.stdout.encode("utf-8")) + len(result.stderr.encode("utf-8"))
        <= OUTPUT_LIMIT,
    )

    result, metadata, output = direct_probe(runtime, "sleep", ["sleep"], timeout=10)
    validation.guest(
        "timeout_probe_guest_identity",
        metadata.get("guest_kernel"),
        metadata.get("guest_boot_id"),
    )
    validation.check(
        "timeout_terminates_child",
        result.stop_reason == "timeout"
        and result.cleaned_up
        and '"child_started": true' in output,
    )

    cancellation = threading.Event()
    cancel_name = "prism-m23-run-" + ident()
    cancel_token = ident()
    running_observed = threading.Event()

    def cancel_after_running():
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            info = runtime.inspect_owned(cancel_name, cancel_token)
            if info and info.get("State", {}).get("Running"):
                running_observed.set()
                time.sleep(1)
                cancellation.set()
                return
            time.sleep(0.05)

    watcher = threading.Thread(target=cancel_after_running, daemon=True)
    watcher.start()
    try:
        result = runtime._execute(
            PROBE_METADATA + probe_program(),
            ["sleep"],
            timeout=20,
            cancel=cancellation,
            name=cancel_name,
            token=cancel_token,
            result_action="sleep",
        )
        metadata, output = parse_probe_metadata(result)
    finally:
        cancellation.set()
        watcher.join(timeout=2)
    validation.guest(
        "cancel_probe_guest_identity",
        metadata.get("guest_kernel"),
        metadata.get("guest_boot_id"),
    )
    validation.check(
        "cancel_terminates_child",
        running_observed.is_set()
        and result.stop_reason == "cancelled"
        and result.cleaned_up
        and '"child_started": true' in output,
    )
    validation.check("boundary_namespace_empty", runtime.all_resources() == [])


def approved_session(store, manifest):
    version = store.candidate(manifest)
    store.approve(version["id"], version["digest"])
    return version, store.new_session(version["id"], "reviewer")


def check_jobs(validation, runtime, registry, root):
    source = write_project(root / "project", "m23-runtime-check")
    store = Store(root / "state.sqlite")
    workspace = OwnerWorkspace(store)
    workspace.register_project(source)
    owner = OwnerIdentity("owner", source.project_id)
    conversation = workspace.new_conversation(
        owner, files=["valid.json", "invalid.json"]
    )
    validation.check(
        "owner_conversation_reference_profile",
        conversation["manifest"]["action"].get("profile") == PROFILE,
    )

    owner_jobs = Jobs(workspace, recover=False, registry=registry)
    Conversations(workspace, owner_jobs, recover=False)
    owner_run = owner_jobs.submit_json_check(
        conversation["id"], owner, "owner-json-check"
    )
    owner_run = wait_run(owner_jobs, conversation["id"], owner, owner_run)
    validation.check("owner_job_completed", owner_run["status"] == "completed")
    owner_result = owner_run.get("result") or {}
    validation.guest(
        "owner_job_guest_identity",
        owner_result.get("guest_kernel"),
        owner_result.get("guest_boot_id"),
    )
    validation.check(
        "owner_job_runtime_provenance",
        owner_run.get("runtime_profile") == PROFILE
        and owner_result.get("profile") == PROFILE
        and owner_result.get("runtime_handler") == HANDLER
        and owner_result.get("cleaned_up") is True,
    )

    checkpoint = ident()
    checkpoint_time = time.time()
    answer = {
        "answer": "The selected synthetic JSON fixtures are ready for reviewed sharing.",
        "claims": [],
        "citations": [],
        "run_references": [owner_run["id"]],
        "limitations": ["This is a fixed syntax check, not project code execution."],
        "pending_request_id": None,
    }
    with store.connect() as db:
        db.execute(
            "INSERT INTO turns(id,session,request_key,question,status,answer,created,finished) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                checkpoint,
                conversation["id"],
                "manual-reviewed-checkpoint",
                "Prepare the selected JSON syntax-check evidence for review.",
                "completed",
                packed(answer),
                checkpoint_time,
                checkpoint_time,
            ),
        )
    selected_ids = [file["id"] for file in conversation["manifest"]["files"]]
    candidate = Handoffs(workspace).freeze(
        conversation["id"],
        owner,
        HandoffSelection(
            checkpoint=checkpoint,
            purpose="Review the selected synthetic JSON syntax-check evidence.",
            summary="Two selected JSON fixtures were checked by the fixed action.",
            open_questions="",
            files=selected_ids,
            runs=[owner_run["id"]],
            excerpts=[{"turn": checkpoint, "part": "answer"}],
            mode="verify",
        ),
    )
    validation.check(
        "schema2_reviewed_handoff",
        candidate["manifest"]["schema"] == 2
        and packed(candidate["manifest"]["action"])
        == packed(conversation["manifest"]["action"])
        and candidate["manifest"]["context"]["runs"][0]["action"] == "json-check"
        and len(candidate["manifest"]["files"]) == 2,
    )
    owner_jobs.shutdown()
    store.approve(candidate["id"], candidate["digest"])
    session = store.new_session(candidate["id"], "reviewer")
    jobs = Jobs(store, recover=False, registry=registry)
    run = jobs.submit_json_check(session["id"], "reviewer", "review-json-check")
    run = wait_run(jobs, session["id"], "reviewer", run)
    validation.check("reviewer_job_completed", run["status"] == "completed")
    result = run.get("result") or {}
    validation.guest(
        "reviewer_job_guest_identity",
        result.get("guest_kernel"),
        result.get("guest_boot_id"),
    )
    validation.check(
        "reviewer_job_runtime_provenance",
        run.get("runtime_profile") == PROFILE
        and result.get("profile") == PROFILE
        and result.get("runtime_handler") == HANDLER
        and result.get("cleaned_up") is True
        and result.get("program_sha256")
        == candidate["manifest"]["action"]["program_sha256"],
    )
    output_files = (result.get("output") or {}).get("files", [])
    validation.check(
        "valid_and_invalid_json",
        [item.get("valid") for item in output_files] == [False, True]
        and [
            {"id": item.get("id"), "sha256": item.get("sha256")}
            for item in output_files
        ]
        == run["parameters"]["files"],
    )
    before_repeat = run_count(store)
    repeated = jobs.submit_json_check(session["id"], "reviewer", "review-json-check")
    validation.check(
        "idempotent_request",
        repeated["id"] == run["id"] and run_count(store) == before_repeat,
    )

    for field, replacement in (
        ("profile", "development"),
        ("runtime_handler", "forged-handler"),
        ("profile_revision", 2),
    ):
        forged = copy.deepcopy(candidate["manifest"])
        forged["action"][field] = replacement
        _, forged_session = approved_session(store, forged)
        before = run_count(store)
        validation.check(
            f"forged_{field}_denied",
            denied(
                lambda s=forged_session, f=field: jobs.submit_json_check(
                    s["id"], "reviewer", "forged-" + f
                ),
                status=409,
            )
            and run_count(store) == before,
        )

    _, revoked_session = approved_session(store, candidate["manifest"])
    store.revoke(revoked_session["version"])
    validation.check(
        "revoked_grant_denied",
        denied(
            lambda: jobs.submit_json_check(
                revoked_session["id"], "reviewer", "revoked-run"
            )
        ),
    )

    _, expired_session = approved_session(store, candidate["manifest"])
    with store.connect() as db:
        db.execute(
            "UPDATE sessions SET expires=? WHERE id=?",
            (time.time() - 1, expired_session["id"]),
        )
    validation.check(
        "expired_grant_denied",
        denied(
            lambda: jobs.submit_json_check(
                expired_session["id"], "reviewer", "expired-run"
            )
        ),
    )

    dispatch_version, dispatch_session = approved_session(store, candidate["manifest"])
    dispatch_run = jobs.submit_json_check(
        dispatch_session["id"], "reviewer", "revoke-during-dispatch"
    )
    dispatch_observation = wait_dispatch_observation(store, runtime, dispatch_run["id"])
    active_at_revoke = dispatch_observation["owned_running"]
    validation.check(
        "revoke_dispatch_precondition",
        active_at_revoke,
        (
            "owned_running"
            if active_at_revoke
            else "completed_before_revoke"
            if dispatch_observation["status"] == "completed"
            else "not_observed"
        ),
    )
    store.revoke(dispatch_version["id"])
    deadline = time.monotonic() + 45
    raw_dispatch = None
    while time.monotonic() < deadline:
        with store.connect() as db:
            row = db.execute(
                "SELECT status,result,error FROM runs WHERE id=?",
                (dispatch_run["id"],),
            ).fetchone()
            raw_dispatch = dict(row) if row else None
        if raw_dispatch and raw_dispatch["status"] not in ("queued", "running"):
            break
        time.sleep(0.05)
    dispatch_status = raw_dispatch["status"] if raw_dispatch else "missing"
    outcome = (
        "cancelled_after_active_revoke"
        if active_at_revoke and dispatch_status == "cancelled"
        else "revoke_after_completion"
        if dispatch_status == "completed"
        else dispatch_status
    )
    resource_absent = bool(
        active_at_revoke
        and dispatch_status == "cancelled"
        and runtime.inspect_owned(
            dispatch_observation["resource"], dispatch_observation["token"]
        )
        is None
    )
    cancelled_cleanly = cancelled_after_active_revoke(
        active_at_revoke, raw_dispatch, resource_absent
    )
    validation.check(
        "revoke_during_dispatch_terminal",
        cancelled_cleanly,
        outcome,
    )
    validation.check(
        "revoked_dispatch_access_ends",
        denied(lambda: jobs.get(dispatch_session["id"], "reviewer", dispatch_run["id"]))
        and denied(
            lambda: jobs.submit_json_check(
                dispatch_session["id"], "reviewer", "after-revoke"
            )
        ),
    )
    if dispatch_status == "completed" and raw_dispatch and raw_dispatch["result"]:
        dispatch_result = json.loads(raw_dispatch["result"])
        validation.guest(
            "completed_revoked_dispatch_guest_identity",
            dispatch_result.get("guest_kernel"),
            dispatch_result.get("guest_boot_id"),
        )
    jobs.shutdown()
    validation.check("jobs_namespace_empty", runtime.all_resources() == [])
    return candidate["manifest"]


def check_restart_reconciliation(validation, runtime, registry, root, manifest):
    store = Store(root / "restart-state.sqlite")
    _, session = approved_session(store, manifest)
    initializer = Jobs(store, recover=False, registry=registry)
    initializer.shutdown()

    run_id = ident()
    resource = runtime.resource_name(run_id)
    token = ident()
    files = {file["name"]: file for file in manifest["files"]}
    selected = [files[name] for name in manifest["action"]["required_inputs"]]
    parameters = {
        "files": [{"id": file["id"], "sha256": file["sha256"]} for file in selected]
    }
    direct = {}

    def abandoned_worker():
        try:
            direct["result"] = runtime._execute(
                PROBE_METADATA + probe_program(),
                ["sleep"],
                timeout=30,
                cancel=None,
                name=resource,
                token=token,
                result_action="sleep",
            )
        except Exception as exc:  # noqa: BLE001 - preserve the recovery observation.
            direct["error"] = type(exc).__name__

    thread = threading.Thread(target=abandoned_worker, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while (
        time.monotonic() < deadline and runtime.inspect_owned(resource, token) is None
    ):
        time.sleep(0.05)
    observed = runtime.inspect_owned(resource, token) is not None
    with store.connect() as db:
        db.execute(
            "INSERT INTO runs(id,session,request_key,seed,status,created,action,parameters,"
            "runtime_profile,runtime_resource,runtime_token) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                session["id"],
                "restart-interrupted-run",
                0,
                "running",
                time.time(),
                "json-check",
                packed(parameters),
                PROFILE,
                resource,
                token,
            ),
        )
    recovered = Jobs(store, recover=True, registry=registry)
    thread.join(timeout=15)
    record = recovered.get(session["id"], "reviewer", run_id)
    validation.check("restart_resource_was_observed", observed)
    validation.check(
        "restart_stays_uncertain",
        record["status"] == "uncertain"
        and record["result"] is None
        and record["finished"] is not None,
    )
    validation.check(
        "restart_cleanup_exact_resource",
        not thread.is_alive()
        and runtime.inspect_owned(resource, token) is None
        and runtime.all_resources() == [],
    )
    validation.check(
        "restart_does_not_replay",
        run_count(store) == 1
        and denied(
            lambda: recovered.submit_json_check(
                session["id"], "reviewer", "after-uncertain-run"
            ),
            status=503,
        ),
    )
    recovered.shutdown()


def main():
    validation = Validation()
    report = {
        "kind": "m2-linux-runtime-check",
        "schema": 1,
        "model_calls": 0,
        "pilot_ready": False,
    }
    runtime = None
    try:
        if not validation.check(
            "dedicated_host",
            platform.system() == "Linux"
            and platform.machine() == "x86_64"
            and os.geteuid() == 0,
        ):
            raise RuntimeError("The dedicated reference host gate failed.")
        initial_helpers = guest_helpers()
        if not validation.check(
            "initial_guest_helpers_absent", not initial_helpers, initial_helpers
        ):
            raise RuntimeError(
                "Guest helper processes already exist on the dedicated host."
            )
        runtime = ReferenceLinuxRuntime()
        validation.check("host_checks_completed", True)
        if not validation.check(
            "initial_namespace_empty", runtime.all_resources() == []
        ):
            raise RuntimeError("The dedicated runtime namespace is not empty.")
        registry = RuntimeRegistry(profile=PROFILE, reference=runtime)

        with tempfile.TemporaryDirectory(prefix="prism-m23-check-") as folder:
            root = Path(folder).resolve()
            startup = Jobs(
                Store(root / "startup-state.sqlite"),
                recover=True,
                registry=registry,
            )
            startup.shutdown()
            readiness = registry.readiness or {}
            if not validation.check(
                "reference_readiness",
                readiness.get("ready") is True
                and readiness.get("profile") == PROFILE
                and readiness.get("runtime_handler") == HANDLER
                and bool(readiness.get("image_id")),
            ):
                raise RuntimeError("Reference readiness did not pass.")
            if not validation.guest(
                "readiness_guest_identity",
                readiness.get("guest_kernel"),
                readiness.get("guest_boot_id"),
            ):
                raise RuntimeError("Reference readiness omitted guest identity.")
            check_boundaries(validation, runtime, root)
            manifest = check_jobs(validation, runtime, registry, root)
            check_restart_reconciliation(validation, runtime, registry, root, manifest)
        validation.check("final_namespace_empty", runtime.all_resources() == [])
        for _ in range(20):
            if not guest_helpers():
                break
            time.sleep(0.25)
        remaining_helpers = guest_helpers()
        validation.check(
            "guest_helpers_removed", not remaining_helpers, remaining_helpers
        )
        validation.check(
            "fresh_kata_boot_per_run",
            bool(validation.boot_ids)
            and len(set(validation.boot_ids)) == len(validation.boot_ids),
            {
                "observed": len(validation.boot_ids),
                "distinct": len(set(validation.boot_ids)),
            },
        )
    except Exception as exc:  # noqa: BLE001 - always emit the bounded host report.
        validation.check(
            "execution_completed",
            False,
            bounded_exception(exc, validation.checks),
        )
        if runtime is not None:
            try:
                validation.check(
                    "failure_namespace_empty", runtime.all_resources() == []
                )
            except Exception:  # noqa: BLE001 - report cleanup failure without masking it.
                validation.check("failure_namespace_empty", False)

    report.update(
        checks=validation.checks,
        details=validation.details,
        probe_sha256=hashlib.sha256(probe_program().encode()).hexdigest(),
        passed=bool(validation.checks) and all(validation.checks.values()),
    )
    destination = os.environ.get("PRISM_M23_RESULT_PATH")
    if destination:
        with Path(destination).open("x", encoding="utf-8") as output:
            json.dump(report, output, indent=2, sort_keys=True)
            output.write("\n")
    print("PRISM_M23_RESULT " + json.dumps(report, sort_keys=True), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
