"""Real synthetic M1 job path, with no model calls or private data."""

import hashlib
import json
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from prism.jobs import Jobs
from prism.sharing import DEMO_FILES, Denied, Source, Store, prepare_source


def main():
    checks = []

    def check(name, condition):
        checks.append({"id": name, "passed": bool(condition)})
        if not condition:
            raise RuntimeError("Failed: " + name)

    with tempfile.TemporaryDirectory(prefix="prism-m1-synthetic-") as directory:
        root = Path(directory)
        prepare_source(root / "source")
        before = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (root / "source").iterdir()
        }
        store = Store(root / "state.sqlite")
        candidate = store.candidate(
            Source(root / "source").freeze(
                [n for n in DEMO_FILES if n != "private-notes.txt"],
                "Verify synthetic paired differences",
                "verify",
            )
        )
        store.approve(candidate["id"], candidate["digest"])
        session = store.new_session(candidate["id"], "reviewer")
        jobs = Jobs(store)

        def run(seed, key):
            record = jobs.submit(session["id"], "reviewer", seed, key)
            deadline = time.monotonic() + 45
            while (
                record["status"] in ("queued", "running")
                and time.monotonic() < deadline
            ):
                time.sleep(0.1)
                record = jobs.get(session["id"], "reviewer", record["id"])
            check("actual_run_seed_" + str(seed), record["status"] == "completed")
            check("confirmed_cleanup_seed_" + str(seed), record["result"]["cleaned_up"])
            return record

        try:
            baseline = run(7, "baseline-verification")
            repeated = jobs.submit(
                session["id"], "reviewer", 7, "baseline-verification"
            )
            check("duplicate_submission_same_run", baseline["id"] == repeated["id"])
            changed = run(23, "changed-seed-verification")
            check(
                "changed_interval_same_mean",
                baseline["result"]["output"]["bootstrap_interval"]
                != changed["result"]["output"]["bootstrap_interval"]
                and baseline["result"]["output"]["mean_difference"]
                == changed["result"]["output"]["mean_difference"],
            )
            for name, attempt in [
                (
                    "invalid_seed_denied",
                    lambda: jobs.submit(
                        session["id"], "reviewer", "7; id", "invalid-seed"
                    ),
                ),
                (
                    "other_recipient_result_denied",
                    lambda: jobs.get(session["id"], "observer", baseline["id"]),
                ),
            ]:
                try:
                    attempt()
                except Denied:
                    check(name, True)
                else:
                    check(name, False)
            observer = store.new_session(candidate["id"], "observer")
            try:
                jobs.submit(observer["id"], "observer", 23, "forbidden-inspect")
            except Denied:
                check("inspect_execution_denied", True)
            else:
                check("inspect_execution_denied", False)
            check(
                "source_files_unchanged",
                before
                == {
                    p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in (root / "source").iterdir()
                },
            )
            store.revoke(candidate["id"])
            try:
                jobs.get(session["id"], "reviewer", baseline["id"])
            except Denied:
                check("revoked_result_delivery_denied", True)
            else:
                check("revoked_result_delivery_denied", False)
            print(
                json.dumps(
                    {
                        "recorded_at": datetime.now(timezone.utc).isoformat(),
                        "kind": "m1_real_synthetic_jobs",
                        "profile": "development",
                        "pilot_ready": False,
                        "model_calls": 0,
                        "passed": True,
                        "checks": checks,
                        "baseline": baseline["result"],
                        "new_run": changed["result"],
                    },
                    indent=2,
                )
            )
        finally:
            jobs.shutdown()


if __name__ == "__main__":
    main()
