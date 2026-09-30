"""Exact-input reuse and follow-up tools; scripted models are not live evidence."""

import copy
import io
import json
import unittest
import zipfile
from unittest.mock import patch

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import FunctionModel
from test_workspace_agent import Fixture

from prism.conversation import Conversations
from prism.sharing import Denied, packed


class CheckedFixture(Fixture):
    def checked(self, valid=True):
        self.edit()
        with patch.object(self.jobs, "_launch"):
            run = self.jobs.submit_workspace_check(
                self.session, "reviewer", 1, "followup-initial-check"
            )
        self.complete_double(run, valid)
        return run


class FollowupPolicyTests(CheckedFixture, unittest.TestCase):
    def test_document_only_revision_reuses_exact_json_and_preserves_old_return(self):
        run = self.checked()
        old = self.workspace.prepare_return(self.session, "reviewer", 1)
        before = self.workspace.download(self.session, "reviewer", old["id"])
        self.workspace.edit(
            self.session, "reviewer", self.ids["docs/release.md"], "Reviewer memo\n", 1
        )
        match = self.workspace.matching_check(self.session, "reviewer", 2)
        self.assertEqual(match["id"], run["id"])
        self.assertEqual(
            (match["checked_revision"], match["applies_to_revision"]), (1, 2)
        )
        result = self.workspace.prepare_return(self.session, "reviewer", 2)
        self.assertTrue(result["validated"])
        self.assertTrue(result["reused_check"])
        self.assertEqual(result["checked_revision"], 1)
        self.assertEqual(
            before, self.workspace.download(self.session, "reviewer", old["id"])
        )
        with zipfile.ZipFile(
            io.BytesIO(self.workspace.download(self.session, "reviewer", result["id"]))
        ) as archive:
            payload = json.loads(archive.read("prism-return.json"))
        self.assertEqual(payload["revision"], 2)
        self.assertEqual(payload["verification"]["parameters"]["workspace_revision"], 1)
        self.assertTrue(payload["verification"]["input_hashes_match"])

    def test_download_bytes_ignore_wall_clock_and_return_catalog_uses_snapshot_revision(
        self,
    ):
        self.checked()
        ret = self.workspace.prepare_return(self.session, "reviewer", 1)
        with patch(
            "zipfile.time.localtime", return_value=(2026, 1, 1, 0, 0, 0, 0, 1, 0)
        ):
            first = self.workspace.download(self.session, "reviewer", ret["id"])
        with patch(
            "zipfile.time.localtime", return_value=(2027, 1, 1, 0, 0, 0, 0, 1, 0)
        ):
            second = self.workspace.download(self.session, "reviewer", ret["id"])
        self.assertEqual(first, second)
        with zipfile.ZipFile(io.BytesIO(first)) as archive:
            self.assertTrue(
                all(f.date_time == (1980, 1, 1, 0, 0, 0) for f in archive.infolist())
            )
        self.workspace.edit(
            self.session, "reviewer", self.ids["docs/release.md"], "Memo\n", 1
        )
        new = self.workspace.prepare_return(self.session, "reviewer", 2)
        self.assertTrue(all(r["revision"] == 2 for r in new["workspace_references"]))
        self.assertEqual(
            self.workspace.get_return(self.session, "reviewer", ret["id"])[
                "workspace_references"
            ],
            ret["workspace_references"],
        )

    def test_changed_json_invalidates_check_but_identical_bytes_can_be_reused(self):
        self.checked()
        saved = self.workspace.read(
            self.session, "reviewer", self.ids["config/release.json"]
        )["text"]
        self.edit("{}", 1)
        self.assertIsNone(self.workspace.matching_check(self.session, "reviewer", 2))
        self.assertFalse(
            self.workspace.prepare_return(self.session, "reviewer", 2)["validated"]
        )
        self.edit(saved, 2)
        self.assertEqual(
            self.workspace.matching_check(self.session, "reviewer", 3)[
                "checked_revision"
            ],
            1,
        )

    def test_matching_failed_syntax_is_not_success(self):
        self.checked(valid=False)
        self.workspace.edit(
            self.session, "reviewer", self.ids["docs/release.md"], "Memo\n", 1
        )
        result = self.workspace.prepare_return(self.session, "reviewer", 2)
        self.assertTrue(result["check_completed"])
        self.assertFalse(result["validated"])
        self.assertTrue(result["reused_check"])

    def test_provenance_cleanup_and_output_mismatch_are_not_reusable(self):
        run = self.checked()
        with self.store.connect() as db:
            original = json.loads(
                db.execute(
                    "SELECT result FROM runs WHERE id=?", (run["id"],)
                ).fetchone()[0]
            )
        variants = []
        for key, value in [
            ("program_sha256", "0" * 64),
            ("profile", "reference-linux"),
            ("image", "unapproved"),
            ("cleaned_up", False),
            ("exit_code", False),
            ("inputs", {}),
        ]:
            result = copy.deepcopy(original)
            result[key] = value
            variants.append(result)
        result = copy.deepcopy(original)
        result["output"]["files"][0]["sha256"] = "0" * 64
        variants.append(result)
        for result in variants:
            with self.subTest(result=result):
                with self.store.connect() as db:
                    db.execute(
                        "UPDATE runs SET result=? WHERE id=?",
                        (packed(result), run["id"]),
                    )
                self.assertIsNone(
                    self.workspace.matching_check(self.session, "reviewer", 1)
                )

    def test_pending_and_uncertain_results_are_not_reused_or_retried(self):
        run = self.checked()
        for status in ("running", "uncertain"):
            with self.subTest(status=status):
                with self.store.connect() as db:
                    db.execute(
                        "UPDATE runs SET status=? WHERE id=?", (status, run["id"])
                    )
                self.assertIsNone(
                    self.workspace.matching_check(self.session, "reviewer", 1)
                )
                self.assertFalse(
                    self.workspace.prepare_return(self.session, "reviewer", 1)[
                        "validated"
                    ]
                )
                with patch.object(self.jobs, "_launch") as launch:
                    with self.assertRaises(Denied):
                        self.jobs.submit_workspace_check(
                            self.session, "reviewer", 1, "blocked-" + status
                        )
                    launch.assert_not_called()

    def test_reuse_is_session_bound_and_reauthorized(self):
        self.checked()
        other = self.store.new_session(self.version["id"], "reviewer")["id"]
        self.assertIsNone(self.workspace.matching_check(other, "reviewer", 0))
        with self.assertRaises(Denied):
            self.workspace.matching_check(self.session, "reviewer", 0)
        self.store.revoke(self.version["id"])
        with self.assertRaises(Denied):
            self.workspace.matching_check(self.session, "reviewer", 1)


class FollowupAgentTests(CheckedFixture, unittest.IsolatedAsyncioTestCase):
    async def test_agent_document_edit_and_return_reuse_without_launch(self):
        run = self.checked()
        calls = 0
        reuse = None

        async def respond(messages, info):
            nonlocal calls, reuse
            calls += 1
            if calls == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "inspect_workspace", {"file_ids": list(self.ids.values())}
                        )
                    ]
                )
            if calls == 2:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "edit_workspace",
                            {
                                "updates": [
                                    {
                                        "id": self.ids["docs/release.md"],
                                        "text": "Reviewer memo\n",
                                    }
                                ],
                                "expected_revision": 1,
                            },
                        )
                    ]
                )
            if calls == 3:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "check_workspace",
                            {"expected_revision": 2, "prepare_return": True},
                        )
                    ]
                )
            for message in messages:
                for part in message.parts:
                    if (
                        isinstance(part, ToolReturnPart)
                        and part.tool_name == "check_workspace"
                    ):
                        reuse = part.content["check_reuse"]
            ret = self.workspace.returns(self.session, "reviewer")[0]
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        info.output_tools[0].name,
                        {
                            "answer": "Documentation updated. Reused unchanged JSON inputs; no new execution.",
                            "claims": [
                                {
                                    "kind": "new_run",
                                    "text": "Existing JSON syntax result reused.",
                                }
                            ],
                            "citations": [],
                            "run_references": [run["id"]],
                            "return_references": [ret["id"]],
                            "limitations": [
                                "JSON syntax does not validate documentation."
                            ],
                            "pending_request_id": None,
                        },
                    )
                ]
            )

        service = Conversations(
            self.store, self.jobs, test_model=FunctionModel(respond)
        )
        with patch.object(self.jobs, "_launch") as launch:
            turn = await service.ask(
                self.session,
                "reviewer",
                "Revise docs, check and return",
                "followup-docs",
            )
        self.assertEqual(turn["status"], "completed", turn)
        launch.assert_not_called()
        self.assertTrue(reuse["reused"])
        self.assertFalse(reuse["executed_now"])
        self.assertEqual(
            (reuse["checked_revision"], reuse["applies_to_revision"]), (1, 2)
        )
        self.assertEqual(len(self.jobs.list(self.session, "reviewer")), 1)

    async def test_stale_unchanged_file_reference_gets_current_catalog_without_tools(
        self,
    ):
        self.checked()
        calls = 0
        key = self.ids["config/release.json"]
        old_hash = self.workspace.read(self.session, "reviewer", key)["sha256"]

        async def respond(messages, info):
            nonlocal calls
            calls += 1
            if calls == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "edit_workspace",
                            {
                                "updates": [
                                    {
                                        "id": self.ids["docs/release.md"],
                                        "text": "Memo\n",
                                    }
                                ],
                                "expected_revision": 1,
                            },
                        )
                    ]
                )
            if calls == 3:
                self.assertEqual(info.function_tools, [])
                retries = [
                    p.content
                    for m in messages
                    if isinstance(m, ModelRequest)
                    for p in m.parts
                    if isinstance(p, RetryPromptPart)
                ]
                self.assertIn('"revision":2', str(retries))
                self.assertIn(old_hash, str(retries))
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        info.output_tools[0].name,
                        {
                            "answer": "Configuration unchanged; documentation revised.",
                            "claims": [
                                {
                                    "kind": "workspace",
                                    "text": "Configuration remains unchanged.",
                                }
                            ],
                            "citations": [],
                            "run_references": [],
                            "workspace_references": [
                                {
                                    "file_id": key,
                                    "revision": 1 if calls == 2 else 2,
                                    "sha256": old_hash,
                                }
                            ],
                            "limitations": [
                                "Syntax alone does not establish semantics."
                            ],
                            "pending_request_id": None,
                        },
                    )
                ]
            )

        service = Conversations(
            self.store, self.jobs, test_model=FunctionModel(respond)
        )
        with patch.object(self.jobs, "_launch") as launch:
            turn = await service.ask(
                self.session, "reviewer", "Update docs only", "stale-catalog"
            )
        self.assertEqual(turn["status"], "completed", turn)
        self.assertEqual(calls, 3)
        launch.assert_not_called()
        self.assertEqual(turn["answer"]["workspace_references"][0]["revision"], 2)

    async def test_explicit_fresh_check_still_launches(self):
        self.checked()
        calls = 0

        async def respond(messages, info):
            nonlocal calls
            calls += 1
            if calls == 1:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "check_workspace",
                            {"expected_revision": 1, "force_run": True},
                        )
                    ]
                )
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        info.output_tools[0].name,
                        {
                            "answer": "Fresh check requested.",
                            "claims": [],
                            "citations": [],
                            "run_references": [],
                            "limitations": [
                                "Syntax checking does not establish semantic correctness."
                            ],
                            "pending_request_id": None,
                        },
                    )
                ]
            )

        service = Conversations(
            self.store, self.jobs, test_model=FunctionModel(respond)
        )

        def complete(
            run, session, actor, action, argument, program_hash, parameters, *rest
        ):
            self.complete_double({"id": run, "parameters": parameters})

        with patch.object(self.jobs, "_launch", side_effect=complete) as launch:
            turn = await service.ask(
                self.session, "reviewer", "Actually run a new check", "fresh-check"
            )
        self.assertEqual(turn["status"], "completed", turn)
        launch.assert_called_once()
        self.assertEqual(len(self.jobs.list(self.session, "reviewer")), 2)
