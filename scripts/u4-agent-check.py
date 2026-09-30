"""Explicit opt-in live-model U4 walkthrough, original ledger, synthetic files only."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from pydantic_ai import capture_run_messages
from pydantic_ai.messages import ModelResponse, ToolCallPart

from prism.conversation import Conversations, read_key
from prism.jobs import Jobs
from prism.projects import ProjectSource
from prism.sharing import Store
from prism.workspace import Workspaces, with_workspace

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples/pilot-decision-review"


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-openai", action="store_true", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument(
        "--session", help="Resume a previously created session without resetting it."
    )
    parser.add_argument(
        "--question",
        default="Complete the pilot review according to requirements.md: repair the incomplete analysis script, actually calculate the cleaned segment and standardized results and seeded bootstrap interval, inspect the outputs, revise the report with limitations and pending human review, and prepare a return package. Do not publish or deploy anything.",
    )
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument(
        "--run-limit",
        type=int,
        default=32,
        help="Explicit finite local lifetime execution allowance; preserves old runs.",
    )
    args = parser.parse_args()
    store = Store(args.data_dir / "demo.sqlite")
    jobs = Jobs(store, global_run_limit=args.run_limit)
    try:
        service = Conversations(
            store, jobs, key=read_key(args.key_file), budget_cents=None, recover=False
        )
        if args.session:
            session = store.session(args.session, "reviewer")
        else:
            source = ProjectSource.from_manifest(FIXTURE / ".prism-project.json")
            base = source.freeze(
                list(source.names),
                "Complete a qualified synthetic pilot decision review",
                "inspect",
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
                python_inputs=[
                    "requirements.md",
                    "data/pilot.csv",
                    "analysis.py",
                    "analysis-plan.json",
                ],
            )
            candidate = store.candidate(manifest)
            store.approve(candidate["id"], candidate["digest"])
            session = store.new_session(candidate["id"], "reviewer")
        print(
            json.dumps(
                {"session": session["id"], "version": session["version"]},
                sort_keys=True,
            ),
            flush=True,
        )
        with capture_run_messages() as messages:
            result = await service.ask(
                session["id"],
                "reviewer",
                args.question,
                "u4-live-" + __import__("uuid").uuid4().hex,
            )
        ws = Workspaces(store)
        record = {
            "schema": 1,
            "evidence": "real model and development runtime; trusted driver bypasses browser entry",
            "session": session["id"],
            "version": session["version"],
            "question": args.question,
            "turn": result,
            "workspace": ws.state(session["id"], "reviewer"),
            "runs": jobs.list(session["id"], "reviewer"),
            "returns": ws.returns(session["id"], "reviewer"),
        }
        record["tool_trace"] = [
            {
                "tool": p.tool_name,
                "arguments": {
                    k: v
                    for k, v in p.args_as_dict().items()
                    if k
                    in (
                        "file_ids",
                        "entrypoint",
                        "expected_revision",
                        "import_results",
                        "force_run",
                        "prepare_return",
                        "run_id",
                    )
                },
            }
            for m in messages
            if isinstance(m, ModelResponse)
            for p in m.parts
            if isinstance(p, ToolCallPart)
        ]
        record["access_requests"] = service.requests(session["id"], "reviewer")
        record["files"] = [
            ws.read(session["id"], "reviewer", f["id"])
            for f in record["workspace"]["files"]
        ]
        record["artifact_hashes"] = {
            r["id"]: hashlib.sha256(
                ws.download(session["id"], "reviewer", r["id"])
            ).hexdigest()
            for r in record["returns"]
        }
        args.record.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "error": result.get("error"),
                    "usage": result.get("usage"),
                    "answer": result.get("answer"),
                    "record": str(args.record),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    finally:
        jobs.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
