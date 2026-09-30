"""Synthetic U4.1 real development-worker/runtime checks; never calls a model."""

import csv
import hashlib
import io
import json
import os
import random
import subprocess
import sys
import tempfile
import time
import zipfile
from collections import Counter
from pathlib import Path
from urllib.parse import urlencode

from prism.computation import payload, program, python_action
from prism.engine import API, Engine
from prism.jobs import SOURCE_ROOT, WORKER_BOOTSTRAP, Jobs
from prism.projects import ProjectSource
from prism.runtime import LABEL, DevelopmentRuntime
from prism.sharing import Store, digest
from prism.workspace import Workspaces, with_workspace

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples/pilot-decision-review"


def oracle():
    # Independent CSV/data oracle, never imports or executes the project script.
    with (FIXTURE / "data/pilot.csv").open() as stream:
        raw = list(csv.DictReader(stream))
    distinct = {tuple(row.items()) for row in raw}
    eligible = [dict(row) for row in distinct if dict(row)["user_id"]]
    assert len(raw) == 168 and len(distinct) == 162 and len(eligible) == 160
    counts = Counter((r["variant"], r["segment"]) for r in eligible)
    converted = Counter(
        (r["variant"], r["segment"]) for r in eligible if int(r["converted"])
    )
    assert counts == {
        ("A", "new"): 60,
        ("A", "returning"): 20,
        ("B", "new"): 40,
        ("B", "returning"): 40,
    }
    assert converted == {
        ("A", "new"): 9,
        ("A", "returning"): 7,
        ("B", "new"): 8,
        ("B", "returning"): 16,
    }
    grouped = {}
    for key in counts:
        grouped[key] = sorted(
            [r for r in eligible if (r["variant"], r["segment"]) == key],
            key=lambda r: r["user_id"],
        )
    rng = random.Random(17)
    differences = []
    for _ in range(2000):
        rates = {}
        for key in (("A", "new"), ("A", "returning"), ("B", "new"), ("B", "returning")):
            group = grouped[key]
            rates[key] = sum(
                int(group[rng.randrange(len(group))]["converted"]) for _ in group
            ) / len(group)
        differences.append(
            0.5
            * (
                rates[("B", "new")]
                - rates[("A", "new")]
                + rates[("B", "returning")]
                - rates[("A", "returning")]
            )
        )
    ordered = sorted(differences)

    def quantile(p):
        index = (len(ordered) - 1) * p
        lo = int(index)
        return ordered[lo] + (ordered[min(lo + 1, len(ordered) - 1)] - ordered[lo]) * (
            index - lo
        )

    return raw, {
        "raw_rows": 168,
        "exact_duplicates": 6,
        "missing_id_rows": 2,
        "eligible_users": 160,
        "standardized_conversion": {"A": 0.25, "B": 0.30},
        "difference": 0.05,
        "bootstrap_interval": [quantile(0.025), quantile(0.975)],
        "oracle_only": True,
        "full_agent_analysis_verified": False,
    }


def main():
    checks = []

    def check(name, passed):
        checks.append({"id": name, "passed": bool(passed)})
        if not passed:
            raise RuntimeError("Failed: " + name)

    raw, targets = oracle()
    check("independent_fixture_counts", True)
    engine = Engine()
    runtime = DevelopmentRuntime(engine)
    before = {
        str(p.relative_to(FIXTURE)): digest(p.read_text())
        for p in FIXTURE.rglob("*")
        if p.is_file()
    }
    with tempfile.TemporaryDirectory(prefix="prism-u4-validation-") as temporary:
        store = Store(Path(temporary) / "state.sqlite")
        ws = Workspaces(store)
        source = ProjectSource.from_manifest(FIXTURE / ".prism-project.json")
        base = source.freeze(
            list(source.names), "Compute and review synthetic pilot outputs", "inspect"
        )
        manifest = with_workspace(
            base,
            [
                "analysis.py",
                "analysis-plan.json",
                "report.md",
                "results/metrics.json",
                "results/data-quality.json",
            ],
            python_entrypoint="analysis.py",
            python_outputs=["results/metrics.json", "results/data-quality.json"],
        )
        candidate = store.candidate(manifest)
        store.approve(candidate["id"], candidate["digest"])
        session = store.new_session(candidate["id"], "reviewer")
        entrypoint = manifest["action"]["entrypoint"]
        # Explicit operator edit proves execution uses revised bytes, not the owner source.
        ws.edit(
            session["id"],
            "reviewer",
            entrypoint,
            next(f["text"] for f in base["files"] if f["name"] == "analysis.py")
            + '\nprint("revised session script")\n',
            0,
        )
        jobs = Jobs(store)
        try:
            run = jobs.submit_workspace_python(
                session["id"], "reviewer", entrypoint, 1, "real-computation"
            )
            deadline = time.monotonic() + 60
            while (
                run["status"] in ("queued", "running") and time.monotonic() < deadline
            ):
                time.sleep(0.1)
                run = jobs.get(session["id"], "reviewer", run["id"])
            check("real_jobs_worker_completed", run["status"] == "completed")
            check("real_cleanup_confirmed", run["result"]["cleaned_up"])
            check(
                "revised_script_executed",
                run["result"]["output"]["stdout"] == "revised session script\n",
            )
            actual = {
                f["name"]: json.loads(f["text"])
                for f in run["result"]["output"]["files"]
            }
            metrics = actual["results/metrics.json"]["variants"]
            for variant in ("A", "B"):
                group = [r for r in raw if r["variant"] == variant]
                check(
                    "raw_arithmetic_" + variant,
                    metrics[variant]["rows"] == len(group)
                    and metrics[variant]["conversions"]
                    == sum(int(r["converted"]) for r in group)
                    and metrics[variant]["revenue"]
                    == sum(float(r["revenue"]) for r in group),
                )
            check(
                "initial_method_incomplete_labeled",
                actual["results/data-quality.json"]
                == {"raw_rows": 168, "cleaning_performed": False},
            )
            ws.apply_computation(session["id"], "reviewer", run["id"], 1)
            item = ws.prepare_return(session["id"], "reviewer", 2)
            artifact = ws.download(session["id"], "reviewer", item["id"])
            with zipfile.ZipFile(io.BytesIO(artifact)) as archive:
                saved = json.loads(archive.read("prism-return.json"))
                check(
                    "actual_outputs_and_exact_provenance_in_zip",
                    saved["computation"]["parameters"] == run["parameters"]
                    and all(
                        archive.read("files/" + f["name"]).decode() == f["text"]
                        for f in run["result"]["output"]["files"]
                    ),
                )
            (ROOT / ".prism-demo/u4-computation-verified.zip").write_bytes(artifact)
            check(
                "stable_download",
                artifact == ws.download(session["id"], "reviewer", item["id"]),
            )
            # Observed running container before revoke; the exact resource must disappear.
            ws.edit(
                session["id"],
                "reviewer",
                entrypoint,
                "import time\ntime.sleep(20)\n",
                2,
            )
            stopped = jobs.submit_workspace_python(
                session["id"], "reviewer", entrypoint, 3, "revoke-active-computation"
            )
            endpoint = (
                API
                + "/containers/json?"
                + urlencode({"filters": json.dumps({"label": [LABEL]})})
            )
            deadline = time.monotonic() + 10
            containers = []
            while not containers and time.monotonic() < deadline:
                containers = engine.request("GET", endpoint)
                time.sleep(0.05)
            check("active_container_observed_before_revoke", len(containers) == 1)
            resource = containers[0]["Id"]
            store.revoke(candidate["id"])
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                with store.connect() as db:
                    row = db.execute(
                        "SELECT * FROM runs WHERE id=?", (stopped["id"],)
                    ).fetchone()
                if row["status"] not in ("queued", "running"):
                    break
                time.sleep(0.1)
            check(
                "revoke_cancelled_without_result",
                row["status"] == "cancelled" and row["result"] is None,
            )
            check(
                "revoked_exact_container_absent",
                not any(c["Id"] == resource for c in engine.request("GET", endpoint)),
            )
        finally:
            jobs.shutdown()

    def probe(script, *, timeout=30):
        files = [
            {"id": digest(n)[:24], "name": n, "text": t, "sha256": digest(t)}
            for n, t in (
                ("script.py", script),
                ("data.json", '{"numbers":[2,4,6]}\n'),
                ("results/result.json", "{}\n"),
            )
        ]
        action = python_action(files, "script.py", ["results/result.json"])
        _, argument = payload(action, files)
        return runtime.run_python(argument, timeout=timeout), argument

    prefix = (
        "import json,os,socket,stat,subprocess,sys,time\nfrom pathlib import Path\n"
    )
    suffix = '\nPath("results/result.json").write_text(json.dumps(result))\n'
    positive, _ = probe(
        prefix
        + """result = {}
try:
    Path("/project/data.json").write_text("mutate")
    result["readonly"] = False
except OSError:
    result["readonly"] = True
result["excluded_absent"] = not Path("/project/private/customer-notes.txt").exists()
result["no_socket"] = not Path("/var/run/docker.sock").exists()
result["no_key"] = not any("KEY" in key or "TOKEN" in key for key in os.environ)
result["network_denied"] = []
for address in ("1.1.1.1", "169.254.169.254"):
    try:
        socket.create_connection((address, 80), timeout=.3).close()
        result["network_denied"].append(False)
    except OSError:
        result["network_denied"].append(True)
result["memory_max"] = Path("/sys/fs/cgroup/memory.max").read_text().strip()
result["pids_max"] = Path("/sys/fs/cgroup/pids.max").read_text().strip()
result["cpu_max"] = Path("/sys/fs/cgroup/cpu.max").read_text().strip()
result["scratch_bytes"] = os.statvfs("/scratch").f_blocks * os.statvfs("/scratch").f_frsize
"""
        + suffix
    )
    check(
        "boundary_probe_completed_cleaned",
        positive.exit_code == 0 and positive.cleaned_up,
    )
    observations = json.loads(json.loads(positive.stdout)["files"][0]["text"])
    for name in ("readonly", "excluded_absent", "no_socket", "no_key"):
        check(name, observations[name])
    check("external_and_metadata_network_denied", all(observations["network_denied"]))
    check(
        "memory_process_cpu_scratch_limits",
        observations["memory_max"] == str(256 * 1024 * 1024)
        and observations["pids_max"] == "32"
        and observations["cpu_max"] == "100000 100000"
        and observations["scratch_bytes"] == 32 * 1024 * 1024,
    )
    for name, script in {
        "artifact_symlink_denied": 'Path("results/result.json").symlink_to("/project/data.json")',
        "parent_symlink_denied": 'Path("results").rmdir()\nPath("elsewhere").mkdir()\nPath("results").symlink_to("/scratch/elsewhere")\nPath("elsewhere/result.json").write_text("{}")',
        "special_fifo_denied": 'os.mkfifo("results/result.json")',
        "hardlink_denied": 'Path("copy.json").write_text("{}")\nos.link("copy.json", "results/result.json")',
        "oversized_artifact_denied": 'Path("results/result.json").write_text(json.dumps({"x":"a"*32768}))',
        "stdout_flood_denied": 'print("a"*10000)\nPath("results/result.json").write_text("{}")',
        "invalid_json_denied": 'Path("results/result.json").write_text("not JSON")',
    }.items():
        result, _ = probe(prefix + script + "\n")
        check(name, result.exit_code != 0 and result.cleaned_up)
    limited, _ = probe(
        prefix
        + """children=[]
try:
    for _ in range(40):
        children.append(subprocess.Popen([sys.executable,"-I","-c","import time;time.sleep(20)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
except OSError:
    pass
result={"spawned":len(children)}
for child in children:
    child.kill(); child.wait()
"""
        + suffix
    )
    check(
        "process_creation_actually_limited",
        limited.exit_code == 0
        and limited.cleaned_up
        and 10
        <= json.loads(json.loads(limited.stdout)["files"][0]["text"])["spawned"]
        < 40,
    )
    timed, argument = probe(prefix + "time.sleep(60)\n", timeout=1)
    check(
        "runtime_timeout_cleaned", timed.stop_reason == "timeout" and timed.cleaned_up
    )
    # Actual worker lease loss, rather than a runtime double. Initial heartbeat
    # starts the task; closing the controller pipe cancels and removes it.
    read_fd, write_fd = os.pipe()
    process = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-c",
            WORKER_BOOTSTRAP,
            SOURCE_ROOT,
            str(engine.path),
            "python-workspace",
            digest(program()),
            str(read_fd),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        pass_fds=(read_fd,),
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
        cwd="/",
    )
    os.close(read_fd)
    os.write(write_fd, b"L")
    process.stdin.write(argument.encode())
    process.stdin.close()
    process.stdin = None
    time.sleep(0.8)
    os.close(write_fd)
    output, _ = process.communicate(timeout=15)
    lease_result = json.loads(output)
    check(
        "worker_pipe_loss_cancelled_cleaned",
        process.returncode == 0
        and lease_result["stop_reason"] == "cancelled"
        and lease_result["cleaned_up"] is True,
    )
    check(
        "all_source_files_unchanged",
        before
        == {
            str(p.relative_to(FIXTURE)): digest(p.read_text())
            for p in FIXTURE.rglob("*")
            if p.is_file()
        },
    )
    check("no_remaining_owned_runtime_resources", runtime.owned == {})
    report = {
        "schema": 1,
        "scope": "U4.1 local development Python execution; no model calls or Kata",
        "passed": all(c["passed"] for c in checks),
        "pilot_ready": False,
        "model_calls": 0,
        "checks": checks,
        "run": run,
        "artifact_sha256": hashlib.sha256(artifact).hexdigest(),
        "runtime_boundary_observations": observations,
        "future_analysis_oracle": targets,
        "full_agent_analysis_verified": False,
    }
    destination = ROOT / "docs/validation/agent-computation-runtime.json"
    destination.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "checks": len(checks),
                "raw_variants": metrics,
                "future_analysis_oracle": targets,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
