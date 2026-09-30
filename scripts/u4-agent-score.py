"""Independent synthetic output/artifact scoring; never executes project code on host."""

import argparse
import csv
import hashlib
import io
import json
import math
import random
import re
import tempfile
import time
import zipfile
from pathlib import Path

from prism.jobs import Jobs
from prism.projects import ProjectSource
from prism.sharing import Store, digest
from prism.workspace import Workspaces, with_workspace

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples/pilot-decision-review"


def oracle(weights):
    raw = list(csv.DictReader((FIXTURE / "data/pilot.csv").open()))
    unique = {tuple(row.items()): row for row in raw if row["user_id"]}
    rows = list(unique.values())
    groups = {
        (v, s): sorted(
            [r for r in rows if (r["variant"], r["segment"]) == (v, s)],
            key=lambda r: r["user_id"],
        )
        for v in ("A", "B")
        for s in ("new", "returning")
    }

    def mean(group, field):
        return sum(float(r[field]) for r in group) / len(group)

    standardized = {
        v: {
            "conversion_rate": sum(
                weights[s] * mean(groups[v, s], "converted") for s in weights
            ),
            "revenue_per_user": sum(
                weights[s] * mean(groups[v, s], "revenue") for s in weights
            ),
        }
        for v in ("A", "B")
    }
    rng = random.Random(17)
    boot = {"conversion_rate": [], "revenue_per_user": []}
    for _ in range(2000):
        sample = {k: [g[rng.randrange(len(g))] for _ in g] for k, g in groups.items()}
        for metric, field in (
            ("conversion_rate", "converted"),
            ("revenue_per_user", "revenue"),
        ):
            boot[metric].append(
                sum(
                    weights[s]
                    * (mean(sample["B", s], field) - mean(sample["A", s], field))
                    for s in weights
                )
            )

    def quantile(values, p):
        x = sorted(values)
        i = (len(x) - 1) * p
        lo = int(i)
        return x[lo] + (x[min(lo + 1, len(x) - 1)] - x[lo]) * (i - lo)

    return (
        rows,
        groups,
        standardized,
        {k: [quantile(v, 0.025), quantile(v, 0.975)] for k, v in boot.items()},
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--initial", type=Path, required=True)
    parser.add_argument("--followup", type=Path, required=True)
    parser.add_argument("--final", type=Path, required=True)
    parser.add_argument("--query", type=Path, required=True)
    parser.add_argument("--clarification", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    records = {
        n: json.loads(getattr(args, n).read_text())
        for n in ("initial", "followup", "final", "query", "clarification")
    }
    checks = []

    def check(name, condition):
        checks.append({"id": name, "passed": bool(condition)})

    def close(x, y):
        return type(x) in (int, float) and math.isclose(
            x, y, rel_tol=1e-10, abs_tol=1e-10
        )

    for name, weights in (
        ("initial", {"new": 0.5, "returning": 0.5}),
        ("followup", {"new": 0.7, "returning": 0.3}),
    ):
        record = records[name]
        f = {f["name"]: f["text"] for f in record["files"]}
        m = json.loads(f["results/metrics.json"])
        q = json.loads(f["results/data-quality.json"])
        rows, groups, standardized, intervals = oracle(weights)
        check(name + "_completed_answer", record["turn"]["status"] == "completed")
        check(
            name + "_cleaning_counts",
            all(
                q[k] == v
                for k, v in {
                    "raw_rows": 168,
                    "exact_duplicates_removed": 6,
                    "missing_id_rows": 2,
                    "eligible_users": 160,
                    "conflicting_user_ids": [],
                    "conservation_check": True,
                }.items()
            ),
        )
        check(name + "_target_weights", m["target_weights"] == weights)
        for v in ("A", "B"):
            for label, selected, actual in [
                ("overall", [r for r in rows if r["variant"] == v], m["overall"][v])
            ] + [(s, groups[v, s], m["segments"][s][v]) for s in weights]:
                n = len(selected)
                c = sum(int(r["converted"]) for r in selected)
                rev = sum(float(r["revenue"]) for r in selected)
                check(
                    name + "_" + v + "_" + label,
                    all(
                        close(actual[k], val)
                        for k, val in {
                            "users": n,
                            "conversions": c,
                            "revenue": rev,
                            "conversion_rate": c / n,
                            "revenue_per_user": rev / n,
                        }.items()
                    ),
                )
            check(
                name + "_standardized_" + v,
                all(
                    close(m["standardized"][v][k], val)
                    for k, val in standardized[v].items()
                ),
            )
        check(
            name + "_difference",
            all(
                close(m["difference"][k], standardized["B"][k] - standardized["A"][k])
                for k in intervals
            ),
        )
        check(
            name + "_bootstrap",
            m["bootstrap"]["seed"] == 17
            and m["bootstrap"]["replicates"] == 2000
            and all(
                all(
                    close(a, b)
                    for a, b in zip(m["bootstrap"]["interval_95"][k], val, strict=True)
                )
                for k, val in intervals.items()
            ),
        )
        run_ids = {
            r["id"]
            for r in record["runs"]
            if r["status"] == "completed" and r["result"]["cleaned_up"] is True
        }
        check(
            name + "_actual_run_reference",
            (
                bool(record["turn"]["answer"]["run_references"])
                and set(record["turn"]["answer"]["run_references"]) <= run_ids
            )
            or (
                name == "followup"
                and record["workspace"]["matching_computation"]["id"] in run_ids
            ),
        )
        check(
            name + "_current_return",
            bool(record["turn"]["answer"]["return_references"])
            and all(
                any(
                    r["id"] == rid
                    and r["revision"] == record["workspace"]["revision"]
                    and r["computation_run"] in run_ids
                    for r in record["returns"]
                )
                for rid in record["turn"]["answer"]["return_references"]
            ),
        )
    ws = Workspaces(Store(args.data_dir / "demo.sqlite"))
    first = records["initial"]
    final = records["final"]
    check(
        "new_target_one_new_run",
        len(records["followup"]["runs"]) == len(first["runs"]) + 1,
    )
    for name in ("final", "query", "clarification"):
        check(
            name + "_no_new_execution",
            len(records[name]["runs"]) == len(records["followup"]["runs"]),
        )
    check(
        "report_only_reuse",
        final["workspace"]["matching_computation"] is not None
        and final["workspace"]["revision"]
        > records["followup"]["workspace"]["revision"],
    )
    for name in ("query", "clarification"):
        check(
            name + "_completed_without_mutation",
            records[name]["turn"]["status"] == "completed"
            and records[name]["workspace"] == final["workspace"]
            and records[name]["artifact_hashes"] == final["artifact_hashes"],
        )
    check(
        "clarification_no_access_request",
        records["clarification"]["turn"]["answer"]["pending_request_id"] is None
        and records["clarification"]["access_requests"] == [],
    )
    report = next(f["text"] for f in final["files"] if f["name"] == "report.md")
    check(
        "report_review_and_units",
        "pending" in report.lower()
        and "percentage point" in report.lower()
        and "synthetic" in report.lower()
        and "causal" in report.lower()
        and re.search(r"5(?:\.0+)? percentage points", report.lower()),
    )
    for name, record in records.items():
        for rid, expected in record["artifact_hashes"].items():
            data = ws.download(record["session"], "reviewer", rid)
            check(
                name + "_immutable_" + rid, hashlib.sha256(data).hexdigest() == expected
            )
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                payload = json.loads(archive.read("prism-return.json"))
                check(
                    name + "_zip_hashes_" + rid,
                    all(
                        digest(archive.read("files/" + f["name"]).decode())
                        == f["sha256"]
                        for f in payload["files"]
                    ),
                )
    original = ws.store.session(first["session"], "reviewer")["manifest"]["files"]
    check(
        "owner_source_unchanged",
        all(
            digest((FIXTURE / f["name"]).read_bytes().decode("utf-8")) == f["sha256"]
            for f in original
        ),
    )
    check(
        "unshared_canary_absent",
        "PRISM_UNSHARED_PILOT_CANARY" not in json.dumps(records),
    )
    # Execute a private counterfactual copy only via the actual bounded Jobs/worker.
    base = ProjectSource.from_manifest(FIXTURE / ".prism-project.json").freeze(
        list(ProjectSource.from_manifest(FIXTURE / ".prism-project.json").names),
        "Synthetic conflict negative check",
        "inspect",
    )
    generated = next(f["text"] for f in final["files"] if f["name"] == "analysis.py")
    for f in base["files"]:
        if f["name"] == "analysis.py":
            f["text"] = generated
        if f["name"] == "data/pilot.csv":
            f["text"] += "A-new-001,B,new,1,40\n"
        f["sha256"] = digest(f["text"])
        f["lines"] = len(f["text"].splitlines())
    manifest = with_workspace(
        base,
        [
            "analysis.py",
            "report.md",
            "analysis-plan.json",
            "results/metrics.json",
            "results/data-quality.json",
        ],
        python_entrypoint="analysis.py",
        python_outputs=["results/metrics.json", "results/data-quality.json"],
        python_inputs=[
            "analysis.py",
            "analysis-plan.json",
            "requirements.md",
            "data/pilot.csv",
        ],
    )
    with tempfile.TemporaryDirectory(prefix="prism-u4-conflict-") as tmp:
        store = Store(Path(tmp) / "state.sqlite")
        candidate = store.candidate(manifest)
        store.approve(candidate["id"], candidate["digest"])
        session = store.new_session(candidate["id"], "reviewer")
        jobs = Jobs(store)
        try:
            run = jobs.submit_workspace_python(
                session["id"],
                "reviewer",
                manifest["action"]["entrypoint"],
                0,
                "conflict-negative",
            )
            deadline = time.monotonic() + 60
            while (
                run["status"] in ("running", "queued") and time.monotonic() < deadline
            ):
                time.sleep(0.1)
                run = jobs.get(session["id"], "reviewer", run["id"])
            check(
                "actual_conflict_blocks_with_cleanup",
                run["status"] == "failed"
                and run["result"]["cleaned_up"] is True
                and run["result"]["exit_code"] != 0
                and "conflict" in json.dumps(run["result"]["diagnostics"]).lower(),
            )
            check(
                "failed_conflict_no_import",
                Workspaces(store).state(session["id"], "reviewer")["revision"] == 0,
            )
        finally:
            jobs.shutdown()
    report = {
        "schema": 1,
        "scope": "Independent synthetic U4 local scoring and one actual development-runtime counterfactual; no model calls by this scorer",
        "passed": all(c["passed"] for c in checks),
        "pilot_ready": False,
        "checks": checks,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "checks": len(checks),
                "failures": [c["id"] for c in checks if not c["passed"]],
            }
        )
    )
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
