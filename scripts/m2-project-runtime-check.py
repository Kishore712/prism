"""Run the real fixed JSON action against fresh synthetic imported-project state."""

import json
import tempfile
import time
from pathlib import Path

from prism.jobs import Jobs
from prism.owner import OwnerIdentity, OwnerWorkspace
from prism.projects import ProjectSource
from prism.sharing import Denied, Store


def write_project(root, project_id, invalid=False):
    root.mkdir()
    (root / "valid.json").write_text('{"items":[1,2],"ready":true}\n')
    (root / "invalid.json").write_text('{"items":[1,}\n' if invalid else "[1,2,3]\n")
    (root / ".prism-project.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "id": project_id,
                "title": "Synthetic JSON project",
                "files": ["valid.json", "invalid.json"],
                "action": "json-check",
            }
        )
    )
    return ProjectSource.from_manifest((root / ".prism-project.json").resolve())


def denied(call):
    try:
        call()
    except Denied:
        return True
    return False


def main():
    checks = {}
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder).resolve()
        source = write_project(root / "one", "runtime-check", invalid=True)
        other = write_project(root / "two", "other-project")
        store = Store(root / "state.sqlite")
        workspace = OwnerWorkspace(store)
        workspace.register_project(source)
        workspace.register_project(other)
        owner = OwnerIdentity("owner", "runtime-check")
        conversation = workspace.new_conversation(
            owner, files=["valid.json", "invalid.json"]
        )
        checks["cross_project_denied"] = denied(
            lambda: workspace.session(
                conversation["id"], OwnerIdentity("owner", "other-project")
            )
        )

        manifest = conversation["manifest"]
        version = store.candidate(manifest)
        store.approve(version["id"], version["digest"])
        session = store.new_session(version["id"], "reviewer")
        jobs = Jobs(store)
        run = jobs.submit_json_check(session["id"], "reviewer", "runtime-json-check")
        deadline = time.monotonic() + 30
        while run["status"] in ("queued", "running") and time.monotonic() < deadline:
            time.sleep(0.05)
            run = jobs.get(session["id"], "reviewer", run["id"])
        jobs.shutdown()
        checks["completed"] = run["status"] == "completed"
        if run["status"] == "completed":
            files = run["result"]["output"]["files"]
            checks["valid_and_invalid"] = [item["valid"] for item in files] == [
                False,
                True,
            ]
            checks["input_provenance"] = [
                {"id": item["id"], "sha256": item["sha256"]} for item in files
            ] == run["parameters"]["files"]
            checks["source_hash"] = (
                run["result"]["program_sha256"] == manifest["action"]["program_sha256"]
            )
            checks["invalid_text_not_echoed"] = '{"items":[1,}' not in json.dumps(
                run["result"]["output"]
            )

        repeat_jobs = Jobs(store, recover=False)
        repeated = repeat_jobs.submit_json_check(
            session["id"], "reviewer", "runtime-json-check"
        )
        checks["duplicate_request_id"] = repeated["id"] == run["id"]
        inspect_version = store.candidate(
            source.freeze(["valid.json"], "Inspect selected JSON safely.", "inspect")
        )
        store.approve(inspect_version["id"], inspect_version["digest"])
        inspect_session = store.new_session(inspect_version["id"], "reviewer")
        inspect_jobs = Jobs(store, recover=False)
        checks["inspect_denied"] = denied(
            lambda: inspect_jobs.submit_json_check(
                inspect_session["id"], "reviewer", "inspect-json-check"
            )
        )
        store.revoke(version["id"])
        checks["revoke_denied"] = denied(
            lambda: store.session(session["id"], "reviewer")
        )
        repeat_jobs.shutdown()
        inspect_jobs.shutdown()

    report = {
        "kind": "m2-project-runtime-check",
        "checks": checks,
        "passed": bool(checks) and all(checks.values()),
        "model_calls": 0,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
