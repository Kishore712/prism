"""Scoped-input and conversational computation checks; scripted runtime/model doubles."""

import copy
import unittest
from unittest.mock import patch

import test_computation
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from prism.conversation import Conversations, Scope
from prism.jobs import Jobs
from prism.sharing import Denied, digest, packed
from prism.workspace import with_workspace


class ComputationAgentTests(unittest.IsolatedAsyncioTestCase):
    setUp = test_computation.ComputationTests.setUp
    inputs = test_computation.ComputationTests.inputs
    result = test_computation.ComputationTests.result

    def narrow(self):
        self.manifest = with_workspace(
            self.base,
            self.editable,
            python_entrypoint="analysis.py",
            python_outputs=["results/metrics.json", "results/data-quality.json"],
            python_inputs=[
                "analysis.py",
                "analysis-plan.json",
                "data/pilot.csv",
                "requirements.md",
            ],
        )
        version = self.store.candidate(self.manifest)
        self.store.approve(version["id"], version["digest"])
        self.session = self.store.new_session(version["id"], "reviewer")

    def complete(self):
        parameters, actual = self.result()
        key = "a" * 32
        result = {
            "action": "python-workspace",
            "inputs": parameters,
            "output": actual,
            "profile": "development",
            "image": self.manifest["action"]["image"],
            "program_sha256": self.manifest["action"]["program_sha256"],
            "exit_code": 0,
            "cleaned_up": True,
        }
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO runs(id,session,request_key,seed,status,created,action,parameters,result,runtime_profile) VALUES(?,?,?,0,'completed',0,'python-workspace',?,?,'development')",
                (
                    key,
                    self.session["id"],
                    "scripted-only",
                    packed(parameters),
                    packed(result),
                ),
            )
        return key

    async def test_selected_inputs_and_report_only_reuse_then_plan_invalidates(self):
        self.narrow()
        _, encoded = self.inputs()
        self.assertNotIn("report.md", encoded)  # Also check decoded bytes below.
        from prism.computation import decode_payload

        self.assertEqual(
            {f["name"] for f in decode_payload(encoded)["inputs"]},
            {"analysis.py", "analysis-plan.json", "data/pilot.csv", "requirements.md"},
        )
        key = self.complete()
        self.ws.apply_computation(self.session["id"], "reviewer", key, 0)
        report = next(f for f in self.manifest["files"] if f["name"] == "report.md")
        self.ws.edit(
            self.session["id"], "reviewer", report["id"], "Review pending.\n", 1
        )
        self.assertEqual(
            self.ws.matching_computation(self.session["id"], "reviewer", 2)["id"], key
        )
        service = Conversations(self.store, self.jobs, recover=False)
        with patch.object(
            self.jobs,
            "submit_workspace_python",
            side_effect=AssertionError("Unexpected rerun"),
        ):
            result = await service.compute(
                Scope(service, self.session["id"], "reviewer", "test"),
                self.entrypoint,
                2,
            )
        self.assertTrue(result["computation_reuse"]["reused"])
        self.assertFalse(result["computation_reuse"]["executed_now"])
        plan = next(
            f for f in self.manifest["files"] if f["name"] == "analysis-plan.json"
        )
        self.ws.edit(self.session["id"], "reviewer", plan["id"], '{"new":0.7}\n', 2)
        self.assertIsNone(
            self.ws.matching_computation(self.session["id"], "reviewer", 3)
        )
        with self.assertRaises(Denied):
            await service.compute(
                Scope(service, self.session["id"], "reviewer", "test"),
                self.entrypoint,
                2,
            )

    async def test_input_policy_tampering_and_entrypoint_exclusion_denied(self):
        self.narrow()
        for values in (
            [],
            ["report.md"],
            ["analysis.py", "private/secret"],
            ["analysis.py", "results/metrics.json"],
            ["analysis.py", "analysis.py"],
        ):
            with self.assertRaises(Denied):
                with_workspace(
                    self.base,
                    self.editable,
                    python_entrypoint="analysis.py",
                    python_outputs=["results/metrics.json"],
                    python_inputs=values,
                )
        bad = copy.deepcopy(self.manifest)
        bad["workspace"]["inputs"] = [
            next(f["id"] for f in bad["files"] if f["name"] == "report.md")
        ]
        with self.assertRaises(Denied):
            self.store.checked_manifest(
                {"manifest": packed(bad), "digest": digest(packed(bad))}
            )

    async def test_tool_catalog_permission_and_final_answer_side_effect_denial(self):
        self.narrow()
        available = []

        async def respond(messages, info):
            available.append({t.name for t in info.function_tools})
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        info.output_tools[0].name,
                        {
                            "answer": "Need your target weights.",
                            "claims": [],
                            "citations": [],
                            "run_references": [],
                            "limitations": ["Synthetic only."],
                            "pending_request_id": None,
                        },
                    )
                ]
            )

        service = Conversations(
            self.store, self.jobs, test_model=FunctionModel(respond)
        )
        answer = await service.ask(
            self.session["id"],
            "reviewer",
            "Weights are missing; clarify.",
            "test-catalog-01",
        )
        self.assertEqual(answer["status"], "completed", answer)
        self.assertIn("compute_workspace", available[0])
        from prism.conversation import PythonNoBackgroundAnswer

        self.assertEqual(
            PythonNoBackgroundAnswer.model_json_schema()["$defs"][
                "PythonNoBackgroundClaim"
            ]["properties"]["kind"]["enum"],
            ["workspace", "new_run", "interpretation"],
        )
        self.assertEqual(service.request_limit(self.session), 16)
        old = self.store.candidate(with_workspace(self.base, ["report.md"]))
        self.store.approve(old["id"], old["digest"])
        session = self.store.new_session(old["id"], "reviewer")
        await service.ask(session["id"], "reviewer", "Clarify only.", "test-catalog-02")
        self.assertNotIn("compute_workspace", available[-1])
        self.assertEqual(service.request_limit(session), 5)

        async def mixed(messages, info):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "compute_workspace",
                        {"entrypoint": self.entrypoint, "expected_revision": 0},
                    ),
                    ToolCallPart(
                        info.output_tools[0].name,
                        {
                            "answer": "Done.",
                            "claims": [],
                            "citations": [],
                            "run_references": [],
                            "limitations": [],
                            "pending_request_id": None,
                        },
                    ),
                ]
            )

        service.test_model = FunctionModel(mixed)
        with patch.object(
            service,
            "compute",
            side_effect=AssertionError("Side effect with final answer"),
        ):
            await service.ask(
                self.session["id"], "reviewer", "Test mixed final.", "test-final-01"
            )
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM runs").fetchone()[0], 0)
        self.store.revoke(self.session["version"])
        with self.assertRaises(Denied):
            await service.compute(
                Scope(service, self.session["id"], "reviewer", "test"),
                self.entrypoint,
                0,
            )

    async def test_python_delivery_rejects_stale_return_but_old_download_survives(self):
        from prism.conversation import Answer

        self.narrow()
        key = self.complete()
        self.ws.apply_computation(self.session["id"], "reviewer", key, 0)
        returned = self.ws.prepare_return(self.session["id"], "reviewer", 1)
        self.assertEqual(returned["computation_run"], key)
        old_bytes = self.ws.download(self.session["id"], "reviewer", returned["id"])
        report = next(f for f in self.manifest["files"] if f["name"] == "report.md")
        self.ws.edit(
            self.session["id"], "reviewer", report["id"], "Pending review.\n", 1
        )
        service = Conversations(self.store, self.jobs, recover=False)
        answer = Answer(
            answer="Delivered.",
            claims=[],
            citations=[],
            run_references=[],
            return_references=[returned["id"]],
            limitations=[],
            pending_request_id=None,
        )
        with self.assertRaises(Denied):
            service.validate_answer(
                Scope(service, self.session["id"], "reviewer", "test"), answer
            )
        self.assertEqual(
            old_bytes, self.ws.download(self.session["id"], "reviewer", returned["id"])
        )
        latest = self.ws.prepare_return(self.session["id"], "reviewer", 2)
        answer.return_references = [latest["id"]]
        service.validate_answer(
            Scope(service, self.session["id"], "reviewer", "test"), answer
        )

    async def test_atomic_computed_delivery_rejects_changed_inputs(self):
        self.narrow()
        key = self.complete()
        self.ws.apply_computation(self.session["id"], "reviewer", key, 0)
        plan = next(
            f for f in self.manifest["files"] if f["name"] == "analysis-plan.json"
        )
        self.ws.edit(
            self.session["id"],
            "reviewer",
            plan["id"],
            '{"target_weights":{"new":0.7,"returning":0.3}}\n',
            1,
        )
        with self.assertRaises(Denied):
            self.ws.prepare_return(
                self.session["id"], "reviewer", 2, computation_run=key
            )
        self.assertEqual(self.ws.returns(self.session["id"], "reviewer"), [])

    async def test_python_transport_envelope_reauth_and_finite_run_configuration(self):
        import httpx2

        from prism.conversation import ENDPOINT, MODEL, GuardedTransport

        self.narrow()
        for value in (True, 0, 257, "64"):
            with self.assertRaises(ValueError):
                Jobs(self.store, global_run_limit=value)
        service = Conversations(self.store, self.jobs, budget_cents=None, recover=False)
        turn = "b" * 32
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO turns(id,session,request_key,question,status,created) VALUES(?,?,?,'Test','running',0)",
                (turn, self.session["id"], turn),
            )
        sent = []

        class NoNetwork(httpx2.AsyncBaseTransport):
            async def handle_async_request(self, request):
                sent.append(True)
                return httpx2.Response(200, json={"status": "completed"})

        transport = GuardedTransport(
            Scope(service, self.session["id"], "reviewer", turn), NoNetwork()
        )
        body = {
            "model": MODEL,
            "store": False,
            "max_output_tokens": 6000,
            "input": "x" * 40000,
        }
        await transport.handle_async_request(
            httpx2.Request("POST", ENDPOINT, json=body)
        )
        self.assertEqual(len(sent), 1)
        for changed in (
            {**body, "max_output_tokens": 1500},
            {**body, "tools": [{"type": "web_search"}]},
            {**body, "input": "x" * (128 * 1024)},
        ):
            with self.assertRaises(Denied):
                await transport.handle_async_request(
                    httpx2.Request("POST", ENDPOINT, json=changed)
                )
        self.store.revoke(self.session["version"])
        with self.assertRaises(Denied):
            await transport.handle_async_request(
                httpx2.Request("POST", ENDPOINT, json=body)
            )
        self.assertEqual(len(sent), 1)

    async def test_failed_worker_diagnostics_are_bounded_and_never_importable(self):
        import io
        import json

        self.narrow()
        with patch.object(self.jobs, "_launch"):
            run = self.jobs.submit_workspace_python(
                self.session["id"], "reviewer", self.entrypoint, 0, "failed-worker"
            )
        record = {
            "cleaned_up": True,
            "program_sha256": self.manifest["action"]["program_sha256"],
            "stop_reason": "exited",
            "exit_code": 1,
            "output_limited": False,
            "stdout": "untrusted" * 900,
            "stderr": "KeyError: 'wrong_plan_key'",
            "elapsed_seconds": 0.02,
        }

        class FakeProcess:
            stdin = io.BytesIO()
            stdout = io.BytesIO(json.dumps(record).encode())
            returncode = 0

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

        with patch("prism.jobs.subprocess.Popen", return_value=FakeProcess()):
            self.jobs._execute(
                run["id"],
                self.session["id"],
                "reviewer",
                "python-workspace",
                "",
                self.manifest["action"]["program_sha256"],
                run["parameters"],
                "development",
                None,
                None,
            )
        actual = self.jobs.get(self.session["id"], "reviewer", run["id"])
        self.assertEqual(actual["status"], "failed")
        self.assertEqual(len(actual["result"]["diagnostics"]["stdout"]), 4096)
        self.assertIn("wrong_plan_key", actual["result"]["diagnostics"]["stderr"])
        self.assertTrue(actual["result"]["diagnostics"]["untrusted"])
        with self.assertRaises(Denied):
            self.ws.apply_computation(self.session["id"], "reviewer", run["id"], 0)
        self.assertIsNone(
            self.ws.matching_computation(self.session["id"], "reviewer", 0)
        )

    async def test_operator_run_allowance_preserves_history_and_still_stops(self):
        self.narrow()
        with self.store.connect() as db:
            for i in range(32):
                db.execute(
                    "INSERT INTO runs(id,session,request_key,seed,status,created) VALUES(?,?,?,0,'completed',0)",
                    (f"{i:032x}", "historical-synthetic", str(i)),
                )
        with self.assertRaises(Denied):
            self.jobs.submit_workspace_python(
                self.session["id"], "reviewer", self.entrypoint, 0, "default-full"
            )
        bounded = Jobs(self.store, global_run_limit=34)
        self.addCleanup(bounded.shutdown)
        with patch.object(bounded, "_launch"):
            for i in (1, 2):
                run = bounded.submit_workspace_python(
                    self.session["id"],
                    "reviewer",
                    self.entrypoint,
                    0,
                    "finite-" + str(i),
                )
                with self.store.connect() as db:
                    db.execute(
                        "UPDATE runs SET status='completed' WHERE id=?", (run["id"],)
                    )
            with self.assertRaises(Denied):
                bounded.submit_workspace_python(
                    self.session["id"], "reviewer", self.entrypoint, 0, "finite-3"
                )
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM runs").fetchone()[0], 34)
        self.assertEqual(self.manifest["action"]["runs_per_session"], 6)

    async def test_operator_version_allowance_preserves_frozen_history(self):
        from prism.sharing import Store

        for value in (True, 0, 257):
            with self.assertRaises(ValueError):
                Store(self.store.path, version_limit=value)
        for _ in range(23):
            self.store.candidate(self.manifest)
        with self.assertRaises(Denied):
            self.store.candidate(self.manifest)
        expanded = Store(self.store.path, version_limit=26)
        expanded.candidate(self.manifest)
        expanded.candidate(self.manifest)
        with self.assertRaises(Denied):
            expanded.candidate(self.manifest)
        with expanded.connect() as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM versions").fetchone()[0], 26
            )
        self.assertEqual(
            expanded.session(self.session["id"], "reviewer")["manifest"], self.manifest
        )

    async def test_repeat_compute_key_does_not_claim_new_execution(self):
        self.narrow()
        key = self.complete()
        with self.store.connect() as db:
            db.execute(
                "UPDATE runs SET request_key='same-turn:python:0' WHERE id=?", (key,)
            )
        service = Conversations(self.store, self.jobs, recover=False)
        with patch.object(
            self.jobs, "_launch", side_effect=AssertionError("Duplicate execution")
        ):
            result = await service.compute(
                Scope(service, self.session["id"], "reviewer", "same-turn"),
                self.entrypoint,
                0,
                import_results=False,
                force_run=True,
            )
        self.assertTrue(result["computation_reuse"]["idempotent_replay"])
        self.assertFalse(result["computation_reuse"]["executed_now"])

    async def test_real_agent_loop_dispatches_scoped_compute_and_import(self):
        self.narrow()
        service = None
        key = None
        calls = 0

        async def respond(messages, info):
            nonlocal calls
            calls += 1
            if calls == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "compute_workspace",
                            {"entrypoint": self.entrypoint, "expected_revision": 0},
                        )
                    ]
                )
            self.assertIn("computation_reuse", repr(messages))
            self.assertIn(key, repr(messages))
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        info.output_tools[0].name,
                        {
                            "answer": "The scripted computation completed.",
                            "claims": [
                                {"kind": "new_run", "text": "The run completed."}
                            ],
                            "citations": [],
                            "run_references": [key],
                            "limitations": [
                                "Runtime double; no real numerical validation."
                            ],
                            "pending_request_id": None,
                        },
                    )
                ]
            )

        service = Conversations(
            self.store, self.jobs, test_model=FunctionModel(respond)
        )

        def submit(*args):
            nonlocal key
            key = self.complete()
            return self.jobs.get(self.session["id"], "reviewer", key)

        with patch.object(self.jobs, "submit_workspace_python", side_effect=submit):
            answer = await service.ask(
                self.session["id"],
                "reviewer",
                "Compute the approved script.",
                "test-compute-01",
            )
        self.assertEqual(answer["status"], "completed", answer)
        self.assertEqual(self.ws.state(self.session["id"], "reviewer")["revision"], 1)
        self.assertEqual(answer["answer"]["run_references"], [key])
