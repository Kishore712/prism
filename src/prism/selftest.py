"""Real, finite synthetic probes. Results never enable private-pilot access."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import platform
import tempfile
import threading
import time

from prism.engine import Engine, IMAGE
from prism.runtime import DevelopmentRuntime, OUTPUT_LIMIT


@contextmanager
def synthetic_environment():
    key = "PRISM_PRIVATE_CANARY"
    previous = os.environ.get(key)
    os.environ[key] = "PRISM_SYNTHETIC_ENVIRONMENT_CANARY"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def run_selftest(engine: Engine, progress=lambda message: None) -> dict:
    runtime = DevelopmentRuntime(engine)
    checks, runs = [], []

    def record(name, passed, detail):
        checks.append({"id": name, "status": "pass" if passed else "fail", "detail": detail})
        progress(f"{name}: {'PASS' if passed else 'FAIL'}")

    def run(action, argument=None, **kwargs):
        result = runtime.run(action, argument, **kwargs)
        data = result.as_dict()
        # Keep operational metadata, not raw probe stdout or temporary source paths.
        data.pop("stdout")
        data.pop("stderr")
        runs.append(data)
        return result

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="prism-synthetic-source-") as temporary, synthetic_environment():
        canary = Path(temporary) / "excluded-canary.txt"
        canary.write_text("PRISM_SYNTHETIC_PRIVATE_CANARY_DO_NOT_IMPORT")
        digest = hashlib.sha256(canary.read_bytes()).hexdigest()
        first = run("evaluate", 7).json_output()
        repeat = run("evaluate", 7).json_output()
        changed = run("evaluate", 19).json_output()
        record("real_evaluation", first["synthetic"] and first["samples"] == 8, "Executed the fixed evaluation inside a constrained container.")
        record("repeatable_evaluation", first == repeat, "The same input produced the same structured result.")
        record("parameter_change", changed["seed"] == 19 and changed != first, "A second allowed seed was actually executed.")
        boundaries = run("boundaries", str(canary)).json_output()
        for name, passed in boundaries.items():
            record(name, passed is True, "Observed by the real synthetic probe inside its container.")
        second = run("boundaries", str(canary)).json_output()
        record("separate_scratch", second["session_marker_absent"], "The second container cannot see the first container's scratch marker.")
        record("source_unchanged", hashlib.sha256(canary.read_bytes()).hexdigest() == digest, "Synthetic host source was never mounted and its digest is unchanged.")
        process = run("processes").json_output()
        record("process_exhaustion", process["process_creation_blocked"], "Finite child creation reached the configured process ceiling.")
        memory = run("memory")
        record("memory_exhaustion", memory.oom_killed and memory.exit_code != 0, "The engine reported an OOM kill for allocation beyond the memory ceiling.")
        flood = run("flood")
        record("output_flood", flood.stop_reason == "output_limit" and flood.output_limited, f"The collector retained at most {OUTPUT_LIMIT} bytes and stopped the task.")
        timeout = run("sleep", timeout=1.5)
        record("wall_timeout", timeout.stop_reason == "timeout" and timeout.exit_code != 0 and timeout.cleaned_up and '"is_child": true' in timeout.stdout, "A task with an observed child process was killed and removed at its deadline.")
        cancel = threading.Event()
        timer = threading.Timer(1, cancel.set)
        timer.start()
        try:
            cancelled = run("sleep", cancel=cancel)
        finally:
            timer.cancel()
            timer.join()
        record("cancellation", cancelled.stop_reason == "cancelled" and cancelled.exit_code != 0 and cancelled.cleaned_up and '"is_child": true' in cancelled.stdout, "Cancellation stopped and removed a task with an observed child process.")
    record("cleanup", all(run["cleaned_up"] for run in runs) and not runtime.owned, "Every created container was confirmed absent after cleanup.")
    return {
        "schema_version": 1,
        "kind": "synthetic_runtime_selftest",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "environment": {"controller_os": platform.system(), "controller_arch": platform.machine(), "python": platform.python_version(), "engine": engine.version()},
        "profile": "development",
        "passed": all(check["status"] == "pass" for check in checks),
        "pilot_ready": False,
        "fixture_image": IMAGE,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "evaluation_example": first,
        "checks": checks,
        "runs": runs,
        "unverified": ["Kata/VM-per-recipient isolation", "OIDC and grants", "host lease loss and worker crash", "cross-recipient authorization", "aggregate budgets and persistent artifact quotas", "real model interaction"],
        "notice": "Synthetic container development checks only; no private-pilot security claim or access activation.",
    }
