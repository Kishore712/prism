"""Actual scoped tools/SQLite/API, scripted model and worker doubles; no live LLM/Kata claim."""

import base64
import copy
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import httpx2
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from prism.conversation import MODEL, Answer, Conversations, Scope
from prism.jobs import Jobs
from prism.projects import ProjectSource
from prism.sharing import (
    Denied,
    NamedPrincipal,
    Source,
    Store,
    digest,
    packed,
    prepare_source,
)
from prism.webapp import DemoAuth, create_app
from prism.workspace import Workspaces, with_workspace

FIXTURE = (
    Path(__file__).resolve().parents[1] / "examples/agent-workspace/.prism-project.json"
)


class Fixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = ProjectSource.from_manifest(FIXTURE)
        self.store = Store(self.root / "state.sqlite")
        self.workspace = Workspaces(self.store)
        self.jobs = Jobs(self.store)
        self.addCleanup(self.jobs.shutdown)
        self.manifest = with_workspace(
            self.source.freeze(
                ["requirements.md", "config/release.json", "docs/release.md"],
                "Reconcile package with approved requirements, validate JSON and return for review",
                "verify",
            ),
            ["config/release.json", "docs/release.md"],
            check=True,
        )
        self.version = self.store.candidate(self.manifest)
        self.store.approve(self.version["id"], self.version["digest"])
        self.session = self.store.new_session(self.version["id"], "reviewer")["id"]
        self.ids = {f["name"]: f["id"] for f in self.manifest["files"]}

    def edit(
        self, text='{"approved":false,"release":"synthetic-2026-09"}\n', revision=0
    ):
        return self.workspace.edit(
            self.session, "reviewer", self.ids["config/release.json"], text, revision
        )

    def complete_double(self, run, valid=True):
        parameters = run["parameters"]
        result = {
            "inputs": parameters,
            "cleaned_up": True,
            "profile": self.manifest["action"]["profile"],
            "program_sha256": self.manifest["action"]["program_sha256"],
            "image": self.manifest["action"]["image"],
            "exit_code": 0,
            "output": {
                "action": "json-check",
                "files": [{**f, "valid": valid} for f in parameters["files"]],
            },
        }
        with self.store.connect() as db:
            db.execute(
                "UPDATE runs SET status='completed',result=? WHERE id=?",
                (packed(result), run["id"]),
            )


class WorkspacePolicyTests(Fixture, unittest.TestCase):
    def test_saved_reference_metadata_matches_exact_updated_bytes(self):
        key = self.ids["config/release.json"]
        result = self.edit("{}\n")
        self.assertEqual(
            result["workspace_references"],
            [{"file_id": key, "revision": 1, "sha256": digest("{}\n")}],
        )
        ref = result["workspace_references"][0]
        actual = self.workspace.read(self.session, "reviewer", key)
        self.assertEqual(ref["sha256"], actual["sha256"])
        self.assertEqual(ref["revision"], actual["revision"])
        self.edit('{"next": true}\n', 1)
        self.assertEqual(ref["sha256"], digest("{}\n"))
        self.assertNotEqual(
            ref["sha256"], self.workspace.read(self.session, "reviewer", key)["sha256"]
        )

    def test_explicit_validation_policy_and_canonical_program(self):
        base = self.source.freeze(
            ["config/release.json"], "Review configuration", "verify"
        )
        no_check = with_workspace(base, ["config/release.json"])
        self.assertIsNone(no_check["action"])
        bad = copy.deepcopy(self.manifest)
        bad["action"]["program"] = "print('forged')"
        with self.assertRaises(Denied):
            Store.checked_manifest(
                {"manifest": packed(bad), "digest": digest(packed(bad))}
            )
        bad = copy.deepcopy(self.manifest)
        bad["workspace"]["schema"] = 1
        with self.assertRaises(Denied):
            Store.checked_manifest(
                {"manifest": packed(bad), "digest": digest(packed(bad))}
            )

    def test_named_continue_action_binding_and_inspect_downgrade(self):
        principal = NamedPrincipal("https://accounts.example", "synthetic-recipient")
        invitation = self.store.create_invitation(
            self.version["id"], principal, mode="continue", expires_in=300
        )
        activation = self.store.invitation_activation(invitation["id"])
        self.assertEqual(activation["action"]["id"], "json-check")
        session = self.store.redeem_invitation(invitation["token"], principal)
        with self.store.connect() as db:
            row = db.execute(
                "SELECT action FROM grants WHERE id=?", (session["grant_id"],)
            ).fetchone()
            self.assertEqual(row["action"], "json-check")
            db.execute(
                "UPDATE grants SET action=NULL WHERE id=?", (session["grant_id"],)
            )
        with self.assertRaises(Denied):
            self.workspace.state(session["id"], principal)
        invitation = self.store.create_invitation(
            self.version["id"], principal, mode="inspect", expires_in=300
        )
        self.assertIsNone(self.store.invitation_activation(invitation["id"])["action"])
        downgraded = self.store.redeem_invitation(invitation["token"], principal)
        with self.assertRaises(Denied):
            self.jobs.submit_workspace_check(
                downgraded["id"], principal, 0, "downgraded-check"
            )

    def test_atomic_multi_edit_and_read_bounds(self):
        config = self.ids["config/release.json"]
        readonly = self.ids["requirements.md"]
        with self.assertRaises(Denied):
            self.workspace.edit_many(
                self.session,
                "reviewer",
                [{"id": config, "text": "{}"}, {"id": readonly, "text": "bad"}],
                0,
            )
        self.assertEqual(self.workspace.state(self.session, "reviewer")["revision"], 0)
        self.workspace.edit_many(
            self.session,
            "reviewer",
            [
                {"id": config, "text": "{}"},
                {"id": self.ids["docs/release.md"], "text": "Updated\n"},
            ],
            0,
        )
        self.assertEqual(
            len(self.workspace.diff(self.session, "reviewer")["changes"]), 2
        )
        for ids in ([config, config], ["/etc/passwd"], []):
            with self.assertRaises(Denied):
                self.workspace.inspect(self.session, "reviewer", ids)
        self.edit("x" * 13000, 1)
        with self.assertRaises(Denied):
            self.workspace.inspect(self.session, "reviewer", [config])

    def test_exact_revision_payload_idempotency_and_stale_denial(self):
        self.edit()
        with patch.object(self.jobs, "_launch") as launch:
            run = self.jobs.submit_workspace_check(
                self.session, "reviewer", 1, "check-workspace-1"
            )
            inputs = json.loads(base64.b64decode(launch.call_args.args[4]))
            self.assertEqual(
                inputs[0]["text"], '{"approved":false,"release":"synthetic-2026-09"}\n'
            )
            self.assertEqual(inputs[0]["sha256"], digest(inputs[0]["text"]))
            self.assertEqual(run["parameters"]["workspace_revision"], 1)
            self.assertEqual(
                self.jobs.submit_workspace_check(
                    self.session, "reviewer", 1, "check-workspace-1"
                )["id"],
                run["id"],
            )
            self.assertEqual(launch.call_count, 1)
            self.edit("{}", 1)
            with self.assertRaises(Denied):
                self.jobs.submit_workspace_check(
                    self.session, "reviewer", 1, "check-workspace-old"
                )
        # Runtime still sees the captured revision, never newer mutable bytes.
        self.assertNotEqual(
            inputs[0]["text"],
            self.workspace.read(
                self.session, "reviewer", self.ids["config/release.json"]
            )["text"],
        )

    def test_input_race_and_forged_payload_denied_before_launch(self):
        with self.store.connect() as db:
            parameters, argument = self.workspace.check_inputs(
                db, self.session, "reviewer", 0
            )
        self.edit()
        with patch.object(self.jobs, "_launch") as launch:
            with self.assertRaises(Denied):
                self.jobs._submit(
                    self.session,
                    "reviewer",
                    "json-check",
                    parameters,
                    "race-check-key",
                    argument,
                )
            with self.store.connect() as db:
                parameters, argument = self.workspace.check_inputs(
                    db, self.session, "reviewer", 1
                )
            with self.assertRaises(Denied):
                self.jobs._submit(
                    self.session,
                    "reviewer",
                    "json-check",
                    parameters,
                    "forged-payload-key",
                    argument + "a",
                )
            launch.assert_not_called()

    def test_return_exact_download_diff_and_revision_validation(self):
        self.edit()
        unvalidated = self.workspace.prepare_return(self.session, "reviewer", 1)
        self.assertFalse(unvalidated["validated"])
        with patch.object(self.jobs, "_launch"):
            run = self.jobs.submit_workspace_check(
                self.session, "reviewer", 1, "check-return-key"
            )
        self.complete_double(run)
        validated = self.workspace.prepare_return(self.session, "reviewer", 1)
        self.assertTrue(validated["validated"])
        self.assertEqual(validated["run_id"], run["id"])
        self.assertNotEqual(validated["id"], unvalidated["id"])
        self.assertEqual(
            self.workspace.prepare_return(self.session, "reviewer", 1), validated
        )
        self.edit("{}", 1)
        new = self.workspace.prepare_return(self.session, "reviewer", 2)
        self.assertFalse(new["validated"])
        with zipfile.ZipFile(
            io.BytesIO(
                self.workspace.download(self.session, "reviewer", validated["id"])
            )
        ) as archive:
            payload = json.loads(archive.read("prism-return.json"))
            self.assertEqual(payload["revision"], 1)
            self.assertEqual(len(payload["changes"]), 1)
            for f in payload["files"]:
                self.assertEqual(
                    digest(archive.read("files/" + f["name"]).decode()), f["sha256"]
                )
        # Freeze changed revisions still needs current authorization and revision.
        with self.assertRaises(Denied):
            self.workspace.prepare_return(self.session, "reviewer", 1)
        self.store.revoke(self.version["id"])
        with self.assertRaises(Denied):
            self.workspace.download(self.session, "reviewer", validated["id"])

    def test_invalid_json_check_is_completed_but_not_validated(self):
        self.edit("bad JSON")
        with patch.object(self.jobs, "_launch"):
            run = self.jobs.submit_workspace_check(
                self.session, "reviewer", 1, "invalid-check-key"
            )
        self.complete_double(run, valid=False)
        result = self.workspace.prepare_return(self.session, "reviewer", 1)
        self.assertTrue(result["check_completed"])
        self.assertFalse(result["validated"])

    def test_cross_session_return_and_revoked_run_denied(self):
        result = self.workspace.prepare_return(self.session, "reviewer", 0)
        second = self.store.new_session(self.version["id"], "reviewer")["id"]
        with self.assertRaises(Denied):
            self.workspace.download(second, "reviewer", result["id"])
        self.store.revoke(self.version["id"])
        with patch.object(self.jobs, "_launch") as launch, self.assertRaises(Denied):
            self.jobs.submit_workspace_check(
                self.session, "reviewer", 0, "revoked-check-key"
            )
        launch.assert_not_called()

    def test_return_quota_persists_and_noop_does_not_consume(self):
        for revision in range(6):
            result = self.workspace.prepare_return(self.session, "reviewer", revision)
            self.assertEqual(
                result,
                self.workspace.prepare_return(self.session, "reviewer", revision),
            )
            self.edit(str(revision), revision)
        with self.assertRaises(Denied):
            Workspaces(Store(self.store.path)).prepare_return(
                self.session, "reviewer", 6
            )

    def test_answer_workspace_references_stale_forged_and_missing(self):
        service = Conversations(self.store, self.jobs)
        scope = Scope(service, self.session, "reviewer", "test")
        file = self.workspace.read(
            self.session, "reviewer", self.ids["config/release.json"]
        )
        answer = {
            "answer": "Working configuration",
            "claims": [{"kind": "workspace", "text": "Working configuration"}],
            "citations": [],
            "run_references": [],
            "limitations": [],
            "pending_request_id": None,
            "workspace_references": [
                {"file_id": file["id"], "revision": 0, "sha256": file["sha256"]}
            ],
        }
        service.validate_answer(scope, Answer(**answer))
        for refs in (
            [],
            [{"file_id": "a" * 24, "revision": 0, "sha256": file["sha256"]}],
            [{"file_id": file["id"], "revision": 0, "sha256": "a" * 64}],
        ):
            with self.assertRaises(Denied) as error:
                service.validate_answer(
                    scope, Answer(**{**answer, "workspace_references": refs})
                )
            self.assertEqual(error.exception.status, 502)
        self.edit()
        with self.assertRaises(Denied):
            service.validate_answer(scope, Answer(**answer))
        with self.assertRaises(Denied):
            service.validate_answer(
                scope,
                Answer(
                    **{
                        **answer,
                        "claims": [],
                        "workspace_references": [],
                        "return_references": ["a" * 32],
                    }
                ),
            )


class WorkspaceLoopTests(Fixture, unittest.IsolatedAsyncioTestCase):
    async def test_file_only_sdk_schema_and_background_repair_without_network(self):
        captured = []
        answer = {
            "answer": "Only the selected files are available; no reviewed background.",
            "claims": [],
            "citations": [],
            "run_references": [],
            "limitations": [],
            "pending_request_id": None,
        }

        class FakeProvider(httpx2.AsyncBaseTransport):
            async def handle_async_request(inner, request):
                data = json.loads(await request.aread())
                captured.append(data)
                output = next(t for t in data["tools"] if t["name"] == "final_result")
                # Deliberately violate strict mode to exercise the independent
                # local validator and its bounded final-only correction.
                candidate = {
                    **answer,
                    "context_references": ["summary"] if len(captured) == 1 else [],
                    "historical_run_references": [],
                }
                return httpx2.Response(
                    200,
                    json={
                        "id": "resp_synthetic",
                        "object": "response",
                        "created_at": 0,
                        "status": "completed",
                        "model": MODEL,
                        "output": [
                            {
                                "type": "function_call",
                                "id": "fc_synthetic",
                                "call_id": "call_" + str(len(captured)),
                                "name": output["name"],
                                "arguments": json.dumps(candidate),
                                "status": "completed",
                            }
                        ],
                        "usage": {
                            "input_tokens": 100,
                            "output_tokens": 100,
                            "total_tokens": 200,
                            "input_tokens_details": {"cached_tokens": 0},
                            "output_tokens_details": {"reasoning_tokens": 0},
                        },
                    },
                )

        service = Conversations(self.store, self.jobs, key="sk-synthetic-test-only")
        with patch(
            "prism.conversation.httpx2.AsyncHTTPTransport", return_value=FakeProvider()
        ):
            result = await service.ask(
                self.session, "reviewer", "Explain available material", "file-only-sdk"
            )
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(len(captured), 2)
        self.assertEqual(result["answer"]["context_references"], [])
        for data in captured:
            output = next(t for t in data["tools"] if t["name"] == "final_result")
            self.assertTrue(output["strict"])
            properties = output["parameters"]["properties"]
            self.assertEqual(properties["context_references"]["maxItems"], 0)
            self.assertEqual(properties["historical_run_references"]["maxItems"], 0)
            self.assertIn("context_references", output["parameters"]["required"])
            self.assertEqual(data["max_output_tokens"], 1500)
            self.assertFalse(data["store"])
        self.assertEqual([t["name"] for t in captured[1]["tools"]], ["final_result"])
        self.assertEqual(self.workspace.state(self.session, "reviewer")["revision"], 0)
        self.assertEqual(self.jobs.list(self.session, "reviewer"), [])
        self.assertEqual(self.workspace.returns(self.session, "reviewer"), [])

    async def test_fifth_request_is_final_correction_without_tools(self):
        available = []

        async def respond(messages, info):
            available.append([t.name for t in info.function_tools])
            if len(available) <= 3:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "inspect_workspace", {"file_ids": list(self.ids.values())}
                        )
                    ]
                )
            answer = {
                "answer": "Files read; no edits or check were requested.",
                "claims": [],
                "citations": [],
                "run_references": [],
                "limitations": [],
                "pending_request_id": None,
                "return_references": ["a" * 32] if len(available) == 4 else [],
            }
            return ModelResponse(
                parts=[ToolCallPart(info.output_tools[0].name, answer)]
            )

        service = Conversations(
            self.store, self.jobs, test_model=FunctionModel(respond)
        )
        result = await service.ask(
            self.session, "reviewer", "Read the current files", "fifth-final-repair"
        )
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["usage"]["requests"], 5)
        self.assertEqual(
            result["usage"]["reference_failures"],
            ["The model referenced an unavailable return."],
        )
        self.assertEqual(available[3:], [[], []])
        self.assertEqual(self.workspace.state(self.session, "reviewer")["revision"], 0)
        self.assertEqual(self.workspace.returns(self.session, "reviewer"), [])

    async def test_fifth_dispatch_stays_bounded_and_preserves_reservations(self):
        service = Conversations(self.store, self.jobs, budget_cents=30)
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO turns(id,session,request_key,question,status,created) VALUES(?,?,?,?,?,?)",
                ("bounded-turn", self.session, "bounded", "Read", "running", 0),
            )
        scope = Scope(service, self.session, "reviewer", "bounded-turn")
        for _ in range(5):
            service.reserve_dispatch(scope)
        with self.assertRaises(Denied):
            service.reserve_dispatch(scope)
        restarted = Conversations(self.store, self.jobs, budget_cents=30, recover=False)
        self.assertEqual(restarted.route()["reserved_cents"], 25)
        self.assertEqual(restarted.route()["budget_cents"], 30)
        with self.assertRaises(Denied):
            restarted.reserve_dispatch(
                Scope(restarted, self.session, "reviewer", "bounded-turn")
            )

    async def test_combined_check_does_not_return_failed_or_changed_revision(self):
        for outcome in ("failed", "changed", "revoked"):
            with self.subTest(outcome=outcome):
                # Each case uses an independent session within this fixture.
                session = self.store.new_session(self.version["id"], "reviewer")["id"]
                requests = 0
                tool_result = None

                async def respond(messages, info):
                    nonlocal requests, tool_result
                    requests += 1
                    if requests == 1:
                        return ModelResponse(
                            parts=[
                                ToolCallPart(
                                    "check_workspace",
                                    {"expected_revision": 0, "prepare_return": True},
                                )
                            ]
                        )
                    for message in messages:
                        for part in message.parts:
                            if isinstance(part, ToolReturnPart):
                                tool_result = part.content
                    return ModelResponse(
                        parts=[
                            ToolCallPart(
                                info.output_tools[0].name,
                                {
                                    "answer": "No checked package was returned.",
                                    "claims": [],
                                    "citations": [],
                                    "run_references": [],
                                    "limitations": [
                                        "Check did not produce a deliverable."
                                    ],
                                    "pending_request_id": None,
                                },
                            )
                        ]
                    )

                def launch(
                    run,
                    _session,
                    actor,
                    action,
                    argument,
                    sha,
                    params,
                    *args,
                    case=outcome,
                    target_session=session,
                ):
                    if case == "failed":
                        with self.store.connect() as db:
                            db.execute(
                                "UPDATE runs SET status='failed',error='test failure' WHERE id=?",
                                (run,),
                            )
                    else:
                        self.complete_double({"id": run, "parameters": params})
                        if case == "changed":
                            self.workspace.edit(
                                target_session,
                                actor,
                                self.ids["config/release.json"],
                                "{}",
                                0,
                            )
                        else:
                            self.store.revoke(self.version["id"])

                service = Conversations(
                    self.store, self.jobs, test_model=FunctionModel(respond)
                )
                with patch.object(self.jobs, "_launch", side_effect=launch):
                    if outcome == "revoked":
                        with self.assertRaises(Denied):
                            await service.ask(
                                session,
                                "reviewer",
                                "Check and return",
                                "combined-" + outcome,
                            )
                        result = {"status": "interrupted"}
                    else:
                        result = await service.ask(
                            session,
                            "reviewer",
                            "Check and return",
                            "combined-" + outcome,
                        )
                with self.store.connect() as db:
                    self.assertEqual(
                        db.execute(
                            "SELECT COUNT(*) FROM workspace_returns WHERE session=?",
                            (session,),
                        ).fetchone()[0],
                        0,
                    )
                if outcome == "failed":
                    self.assertEqual(result["status"], "completed", result)
                    self.assertIsNone(tool_result["workspace_return"])
                else:
                    self.assertNotEqual(result["status"], "completed", result)

    async def test_four_request_task_completion_actual_tools_scripted_model(self):
        requests = 0
        target = self.ids["config/release.json"]
        note = self.ids["docs/release.md"]
        run_id = None
        return_id = None

        async def respond(messages, info):
            nonlocal requests, run_id, return_id
            requests += 1
            if requests == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "inspect_workspace", {"file_ids": list(self.ids.values())}
                        )
                    ]
                )
            if requests == 2:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "edit_workspace",
                            {
                                "updates": [
                                    {
                                        "id": target,
                                        "text": '{"release":"synthetic-2026-09","audience":["reviewer"],"approved":false,"documents":2}\n',
                                    },
                                    {
                                        "id": note,
                                        "text": "# Release notes\n\nRelease: synthetic-2026-09\nAudience: reviewer\nOwner approval: pending\n",
                                    },
                                ],
                                "expected_revision": 0,
                            },
                        )
                    ]
                )
            if requests == 3:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "check_workspace",
                            {"expected_revision": 1, "prepare_return": True},
                            tool_call_id="check",
                        ),
                    ]
                )
            run_id = self.jobs.list(self.session, "reviewer")[0]["id"]
            return_id = self.workspace.returns(self.session, "reviewer")[0]["id"]
            file = self.workspace.read(self.session, "reviewer", target)
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        info.output_tools[0].name,
                        {
                            "answer": "Reconciled both copies and returned revision 1 for owner review. JSON syntax passed.",
                            "claims": [
                                {
                                    "kind": "workspace",
                                    "text": "Reconciled working copies",
                                },
                                {
                                    "kind": "new_run",
                                    "text": "JSON syntax check completed",
                                },
                            ],
                            "citations": [],
                            "run_references": [run_id],
                            "return_references": [return_id],
                            "workspace_references": [
                                {
                                    "file_id": target,
                                    "revision": 1,
                                    "sha256": file["sha256"],
                                }
                            ],
                            "limitations": [
                                "Scripted model and worker doubles; no semantic or live-model validation."
                            ],
                            "pending_request_id": None,
                        },
                    )
                ]
            )

        def launch(
            run, session, actor, action, argument, program_hash, parameters, *args
        ):
            # Deliberately a runtime double; tests real controller/tool binding, not engine execution.
            self.complete_double({"id": run, "parameters": parameters})

        service = Conversations(
            self.store, self.jobs, test_model=FunctionModel(respond)
        )
        with patch.object(self.jobs, "_launch", side_effect=launch):
            result = await service.ask(
                self.session,
                "reviewer",
                "Reconcile both files, check JSON and return the package",
                "workspace-task-1",
            )
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(requests, 4)
        self.assertEqual(result["answer"]["return_references"], [return_id])
        self.assertTrue(
            self.workspace.get_return(self.session, "reviewer", return_id)["validated"]
        )
        self.assertEqual(
            len(self.workspace.diff(self.session, "reviewer")["changes"]), 2
        )
        self.assertEqual(
            self.source.read_bytes("config/release.json").decode(),
            self.manifest["files"][1]["text"]
            if self.manifest["files"][1]["name"] == "config/release.json"
            else next(
                f["text"]
                for f in self.manifest["files"]
                if f["name"] == "config/release.json"
            ),
        )

    async def test_failed_final_answer_retains_and_reports_saved_edits(self):
        rounds = 0

        async def respond(messages, info):
            nonlocal rounds
            rounds += 1
            if rounds == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "edit_workspace",
                            {
                                "updates": [
                                    {
                                        "id": self.ids["config/release.json"],
                                        "text": "{}",
                                    }
                                ],
                                "expected_revision": 0,
                            },
                        )
                    ]
                )
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        info.output_tools[0].name,
                        {
                            "answer": "Invalid reference",
                            "claims": [],
                            "citations": [],
                            "run_references": [],
                            "return_references": ["a" * 32],
                            "limitations": [],
                            "pending_request_id": None,
                        },
                    )
                ]
            )

        service = Conversations(
            self.store, self.jobs, test_model=FunctionModel(respond)
        )
        result = await service.ask(
            self.session, "reviewer", "Edit the copy", "failed-after-edit"
        )
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["answer"])
        self.assertTrue(result["usage"]["partial"])
        self.assertGreater(result["usage"]["requests"], 0)
        self.assertIn("not rolled back", result["error"])
        self.assertEqual(self.workspace.state(self.session, "reviewer")["revision"], 1)
        self.assertFalse(self.workspace.returns(self.session, "reviewer"))

    async def test_workspace_tools_hidden_for_inspect_and_unapproved_execution(self):
        seen = []

        async def respond(messages, info):
            seen.extend(t.name for t in info.function_tools)
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        info.output_tools[0].name,
                        {
                            "answer": "No edit is authorized",
                            "claims": [],
                            "citations": [],
                            "run_references": [],
                            "limitations": [],
                            "pending_request_id": None,
                        },
                    )
                ]
            )

        observer = self.store.new_session(self.version["id"], "observer")["id"]
        service = Conversations(
            self.store, self.jobs, test_model=FunctionModel(respond)
        )
        result = await service.ask(
            observer, "observer", "What can I do?", "observer-tool-list"
        )
        self.assertEqual(result["status"], "completed", result)
        self.assertNotIn("edit_workspace", seen)
        self.assertNotIn("check_workspace", seen)
        manual = with_workspace(
            self.source.freeze(
                ["config/release.json"], "Edit only, no execution", "inspect"
            ),
            ["config/release.json"],
        )
        v = self.store.candidate(manual)
        self.store.approve(v["id"], v["digest"])
        session = self.store.new_session(v["id"], "reviewer")["id"]
        seen.clear()
        await service.ask(session, "reviewer", "Capabilities?", "manual-tool-list")
        self.assertIn("edit_workspace", seen)
        self.assertNotIn("check_workspace", seen)

    async def test_final_answer_repair_cannot_repeat_edits_or_exports(self):
        rounds = 0

        async def respond(messages, info):
            nonlocal rounds
            rounds += 1
            if rounds == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            info.output_tools[0].name,
                            {
                                "answer": "Forged return",
                                "claims": [],
                                "citations": [],
                                "run_references": [],
                                "return_references": ["a" * 32],
                                "limitations": [],
                                "pending_request_id": None,
                            },
                        )
                    ]
                )
            self.assertFalse(info.function_tools)
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "edit_workspace",
                        {
                            "updates": [
                                {"id": self.ids["config/release.json"], "text": "{}"}
                            ],
                            "expected_revision": 0,
                        },
                    )
                ]
            )

        service = Conversations(
            self.store, self.jobs, test_model=FunctionModel(respond)
        )
        result = await service.ask(
            self.session, "reviewer", "Forge return then write", "repair-tool-block"
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.workspace.state(self.session, "reviewer")["revision"], 0)
        self.assertFalse(self.workspace.returns(self.session, "reviewer"))


class WorkspaceHTTPTests(unittest.TestCase):
    def test_actual_api_policy_download_csrf_and_wrong_role(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            prepare_source(root / "source")
            store = Store(root / "state.sqlite")
            auth = DemoAuth()
            app = create_app(
                store,
                Source(root / "source"),
                auth=auth,
                project_sources=[ProjectSource.from_manifest(FIXTURE)],
            )
            self.addCleanup(app.state.owner_jobs.shutdown)
            with TestClient(app, base_url="http://127.0.0.1:8765") as client:
                headers = {"Origin": "http://127.0.0.1:8765"}
                headers["X-Prism-CSRF"] = client.post(
                    "/api/login", json={"token": auth.tokens["owner"]}, headers=headers
                ).json()["csrf"]
                candidate = client.post(
                    "/api/owner/projects/agent-workspace/candidates",
                    json={
                        "files": [
                            "requirements.md",
                            "config/release.json",
                            "docs/release.md",
                        ],
                        "purpose": "Reconcile release package",
                        "mode": "continue",
                        "editable_files": ["config/release.json", "docs/release.md"],
                        "check_workspace": True,
                    },
                    headers=headers,
                )
                self.assertEqual(candidate.status_code, 200, candidate.text)
                version = candidate.json()
                self.assertEqual(version["manifest"]["workspace"]["schema"], 2)
                self.assertEqual(
                    client.post(
                        f"/api/owner/versions/{version['id']}/approve",
                        json={"digest": version["digest"]},
                        headers=headers,
                    ).status_code,
                    200,
                )
                headers["X-Prism-CSRF"] = client.post(
                    "/api/login",
                    json={"token": auth.tokens["reviewer"]},
                    headers=headers,
                ).json()["csrf"]
                session = client.post(
                    "/api/review/sessions",
                    json={"version": version["id"]},
                    headers=headers,
                ).json()["id"]
                path = f"/api/review/sessions/{session}/workspace"
                item = client.post(
                    path + "/returns", json={"expected_revision": 0}, headers=headers
                ).json()
                route = path + f"/returns/{item['id']}/download"
                downloaded = client.get(route)
                self.assertEqual(downloaded.status_code, 200)
                self.assertEqual(downloaded.headers["cache-control"], "no-store")
                with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
                    self.assertIn("files/config/release.json", archive.namelist())
                self.assertEqual(
                    client.post(
                        path + "/check",
                        json={"expected_revision": 0, "request_key": "csrf-denied"},
                        headers={"Origin": headers["Origin"]},
                    ).status_code,
                    403,
                )
                client.cookies.clear()
                headers["X-Prism-CSRF"] = client.post(
                    "/api/login", json={"token": auth.tokens["owner"]}, headers=headers
                ).json()["csrf"]
                self.assertEqual(client.get(route).status_code, 401)
                store.revoke(version["id"])
                headers["X-Prism-CSRF"] = client.post(
                    "/api/login",
                    json={"token": auth.tokens["reviewer"]},
                    headers=headers,
                ).json()["csrf"]
                self.assertEqual(client.get(route).status_code, 403)
