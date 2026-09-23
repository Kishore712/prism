"""M2.2 local-project import, action and API boundary checks."""

import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from prism.conversation import Conversations
from prism.handoff import Handoffs, HandoffSelection
from prism.jobs import Jobs
from prism.owner import OwnerIdentity, OwnerWorkspace
from prism.projects import ProjectSource, json_check_program
from prism.sharing import Denied, Source, Store, digest, ident, packed, prepare_source
from prism.webapp import DemoAuth, create_app


def make_project(root, *, project_id="documents", action="json-check", files=None):
    root.mkdir(parents=True)
    (root / "README.md").write_text("# Synthetic documents\n")
    (root / "valid.json").write_text('{"ready":true,"items":[1,2]}\n')
    (root / "invalid.json").write_text('{"ready":}\n')
    (root / "unlisted.txt").write_text("PRISM_UNLISTED_CANARY\n")
    config = {
        "schema": 1,
        "id": project_id,
        "title": "Synthetic documents",
        "files": files or ["README.md", "valid.json", "invalid.json"],
        "action": action,
    }
    manifest = root / ".prism-project.json"
    manifest.write_text(json.dumps(config))
    return manifest


class ProjectImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def test_manifest_catalog_freeze_and_selected_action_inputs(self):
        source = ProjectSource.from_manifest(make_project(self.root / "project"))
        self.assertEqual(source.project_id, "documents")
        self.assertEqual(source.source_kind, "local-project")
        self.assertEqual(
            [item["name"] for item in source.catalog()],
            ["README.md", "valid.json", "invalid.json"],
        )
        frozen = source.freeze(
            ["README.md", "valid.json"], "Review selected documents.", "verify"
        )
        self.assertEqual(frozen["action"]["required_inputs"], ["valid.json"])
        self.assertNotIn("PRISM_UNLISTED_CANARY", repr(frozen))
        inspected = source.freeze(
            ["README.md"], "Review selected documents.", "inspect"
        )
        self.assertIsNone(inspected["action"])
        with self.assertRaises(Denied):
            source.freeze(["README.md"], "Review selected documents.", "verify")
        (source.root / "valid.json").write_text('{"changed":true}\n')
        self.assertIn('"ready":true', frozen["files"][1]["text"])

    def test_rejects_ambiguous_manifests_links_and_unsafe_ancestors(self):
        project = self.root / "project"
        manifest = make_project(project)
        manifest.write_text(
            '{"schema":1,"schema":1,"id":"documents","title":"Docs",'
            '"files":["README.md"],"action":null}'
        )
        with self.assertRaises(Denied):
            ProjectSource.from_manifest(manifest)

        manifest = make_project(self.root / "hardlink")
        os.link(manifest.parent / "README.md", manifest.parent / "copy.md")
        manifest.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "id": "hardlink",
                    "title": "Hard link",
                    "files": ["README.md"],
                    "action": None,
                }
            )
        )
        with self.assertRaises(Denied):
            ProjectSource.from_manifest(manifest)

        real = self.root / "real"
        make_project(real)
        linked = self.root / "linked"
        linked.symlink_to(real, target_is_directory=True)
        with self.assertRaises(Denied):
            ProjectSource.from_manifest(linked / ".prism-project.json")

        manifest = make_project(self.root / "badpath", files=["../outside.txt"])
        with self.assertRaises(Denied):
            ProjectSource.from_manifest(manifest)

    def test_rejects_relative_oversized_binary_and_duplicate_paths(self):
        manifest = make_project(self.root / "project")
        with self.assertRaises(Denied):
            ProjectSource.from_manifest(Path(".prism-project.json"))
        (manifest.parent / "valid.json").write_bytes(b"\xff")
        with self.assertRaises(Denied):
            ProjectSource.from_manifest(manifest)
        manifest = make_project(self.root / "surrogate", files=["bad\ud800.json"])
        with self.assertRaises(Denied):
            ProjectSource.from_manifest(manifest)

    def test_legacy_bootstrap_action_without_required_inputs_still_runs(self):
        prepare_source(self.root / "fixture")
        store = Store(self.root / "legacy.sqlite")
        manifest = Source(self.root / "fixture").freeze(
            ["observations.csv", "baseline.json", "limitations.md"],
            "Review legacy synthetic evidence.",
            "verify",
        )
        manifest["action"].pop("required_inputs")
        candidate = store.candidate(manifest)
        store.approve(candidate["id"], candidate["digest"])
        session = store.new_session(candidate["id"], "reviewer")
        jobs = Jobs(store)
        self.addCleanup(jobs.shutdown)
        with patch.object(jobs, "_launch") as launch:
            run = jobs.submit(session["id"], "reviewer", 7, "legacy-seed-007")
        self.assertEqual(run["seed"], 7)
        launch.assert_called_once()
        manifest = make_project(
            self.root / "duplicate", files=["README.md", "README.md"]
        )
        with self.assertRaises(Denied):
            ProjectSource.from_manifest(manifest)

    def test_fixed_program_reports_validity_without_echoing_invalid_text(self):
        payload = [
            {"id": "a" * 24, "sha256": "b" * 64, "text": '{"x":1}'},
            {"id": "c" * 24, "sha256": "d" * 64, "text": '{"secret":}'},
            {"id": "e" * 24, "sha256": "f" * 64, "text": "NaN"},
            {"id": "1" * 24, "sha256": "2" * 64, "text": "1e999"},
        ]
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()
        completed = subprocess.run(
            [sys.executable, "-I", "-c", json_check_program(), encoded],
            check=True,
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        )
        output = json.loads(completed.stdout)
        self.assertEqual(
            [item["valid"] for item in output["files"]], [True, False, False, False]
        )
        self.assertEqual(output["files"][1]["error"]["reason"], "syntax")
        self.assertEqual(output["files"][2]["error"]["reason"], "unsupported_number")
        self.assertEqual(output["files"][3]["error"]["reason"], "unsupported_number")
        self.assertNotIn("secret", completed.stdout)

    def test_json_payload_bound_is_checked_before_candidate_creation(self):
        project = self.root / "large"
        project.mkdir()
        names = []
        for number in range(3):
            name = f"large-{number}.json"
            names.append(name)
            (project / name).write_text('"' + ("界" * 10000) + '"\n')
        manifest = project / ".prism-project.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "id": "large-json",
                    "title": "Large JSON",
                    "files": names,
                    "action": "json-check",
                }
            )
        )
        source = ProjectSource.from_manifest(manifest)
        with self.assertRaisesRegex(Denied, "too large"):
            source.freeze(names, "Check selected large JSON.", "verify")


class ProjectApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        prepare_source(self.root / "fixture")
        self.source = ProjectSource.from_manifest(make_project(self.root / "project"))
        self.store = Store(self.root / "state.sqlite")
        self.auth = DemoAuth()
        self.app = create_app(
            self.store,
            Source(self.root / "fixture"),
            auth=self.auth,
            project_sources=(self.source,),
        )
        self.client = TestClient(self.app, base_url="http://127.0.0.1:8765")
        login = self.client.post(
            "/api/login",
            json={"token": self.auth.tokens["owner"]},
            headers={"origin": "http://127.0.0.1:8765"},
        )
        self.csrf = login.json()["csrf"]
        self.headers = {
            "origin": "http://127.0.0.1:8765",
            "x-prism-csrf": self.csrf,
        }

    def tearDown(self):
        self.app.state.owner_jobs.shutdown()

    def test_catalog_names_state_and_explicit_conversation_selection(self):
        state = self.client.get("/api/owner/state").json()
        self.assertEqual(state["runtime_profile"], "development")
        projects = {item["id"]: item for item in state["projects"]}
        self.assertEqual(projects["paired-evaluation"]["source_kind"], "synthetic")
        self.assertEqual(projects["documents"]["source_kind"], "local-project")
        catalog = self.client.get("/api/owner/projects/documents/catalog").json()
        self.assertEqual(catalog["action"], "json-check")
        self.assertNotIn("unlisted", repr(catalog))

        missing = self.client.post(
            "/api/owner/projects/documents/conversations", json={}, headers=self.headers
        )
        self.assertEqual(missing.status_code, 400)
        inspect = self.client.post(
            "/api/owner/projects/documents/conversations",
            json={"files": ["README.md"]},
            headers=self.headers,
        )
        self.assertEqual(inspect.status_code, 200, inspect.text)
        self.assertEqual(inspect.json()["mode"], "inspect")
        self.assertIsNone(inspect.json()["manifest"]["action"])
        verify = self.client.post(
            "/api/owner/projects/documents/conversations",
            json={"files": ["valid.json"]},
            headers=self.headers,
        )
        self.assertEqual(verify.status_code, 200, verify.text)
        self.assertEqual(
            verify.json()["manifest"]["action"]["required_inputs"], ["valid.json"]
        )

    def test_project_candidate_roles_cross_project_and_duplicate_ids(self):
        response = self.client.post(
            "/api/owner/projects/documents/candidates",
            json={
                "files": ["README.md", "valid.json"],
                "purpose": "Share selected documents.",
                "mode": "verify",
            },
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            [item["name"] for item in response.json()["manifest"]["files"]],
            ["README.md", "valid.json"],
        )
        self.assertEqual(
            self.client.get("/api/owner/projects/missing/catalog").status_code, 403
        )
        duplicate = ProjectSource.from_manifest(make_project(self.root / "duplicate"))
        with self.assertRaises(ValueError):
            create_app(
                Store(self.root / "duplicate.sqlite"),
                Source(self.root / "fixture"),
                project_sources=(self.source, duplicate),
            )


class ProjectJobAndAgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.source = ProjectSource.from_manifest(make_project(self.root / "project"))
        self.store = Store(self.root / "state.sqlite")
        candidate = self.store.candidate(
            self.source.freeze(["valid.json"], "Check selected JSON.", "verify")
        )
        self.store.approve(candidate["id"], candidate["digest"])
        self.session = self.store.new_session(candidate["id"], "reviewer")
        self.jobs = Jobs(self.store)
        self.addCleanup(self.jobs.shutdown)

    def test_json_job_idempotency_policy_forgery_and_revoke(self):
        launched = []
        with patch.object(
            self.jobs, "_launch", side_effect=lambda *args: launched.append(args)
        ):
            first = self.jobs.submit_json_check(
                self.session["id"], "reviewer", "json-request-001"
            )
            second = self.jobs.submit_json_check(
                self.session["id"], "reviewer", "json-request-001"
            )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(launched), 1)
        self.assertEqual(first["action"], "json-check")
        self.assertNotIn("seed", first)
        self.assertEqual(
            first["parameters"]["files"][0]["id"],
            self.session["manifest"]["files"][0]["id"],
        )
        with self.store.connect() as db:
            row = db.execute(
                "SELECT manifest FROM versions WHERE id=?", (self.session["version"],)
            ).fetchone()
            manifest = json.loads(row["manifest"])
            manifest["action"]["memory_mib"] = 1024
            db.execute("DROP TRIGGER immutable_version")
            body = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
            db.execute(
                "UPDATE versions SET manifest=?,digest=? WHERE id=?",
                (body, digest(body), self.session["version"]),
            )
        with self.assertRaises(Denied):
            self.jobs.submit_json_check(
                self.session["id"], "reviewer", "json-request-002"
            )

    def test_historical_json_inputs_are_remapped_and_missing_input_is_denied(self):
        workspace = OwnerWorkspace(self.store)
        workspace.register_project(self.source)
        owner_jobs = Jobs(workspace, recover=False)
        self.addCleanup(owner_jobs.shutdown)
        Conversations(workspace, owner_jobs, recover=False)
        actor = OwnerIdentity("owner", "documents")
        chat = workspace.new_conversation(
            actor, files=["valid.json", "invalid.json"]
        )
        files = {item["name"]: item for item in chat["manifest"]["files"]}
        run_id, turn_id = ident(), ident()
        parameters = {
            "files": [
                {"id": files[name]["id"], "sha256": files[name]["sha256"]}
                for name in ("invalid.json", "valid.json")
            ]
        }
        output = {
            "action": "json-check",
            "files": [
                {
                    "id": item["id"],
                    "sha256": item["sha256"],
                    "valid": True,
                    "type": "object",
                    "counts": {},
                    "error": None,
                }
                for item in parameters["files"]
            ],
        }
        result = {
            "action": "json-check",
            "inputs": parameters,
            "output": output,
            "output_sha256": digest(packed(output)),
            "image": chat["manifest"]["action"]["image"],
            "image_id": "fixture-image",
            "program_sha256": chat["manifest"]["action"]["program_sha256"],
            "exit_code": 0,
            "elapsed_seconds": 0.1,
            "cleaned_up": True,
            "profile": "development",
        }
        answer = {
            "answer": "Stored synthetic JSON result.",
            "claims": [],
            "citations": [],
            "run_references": [run_id],
            "limitations": [],
            "pending_request_id": None,
        }
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO turns(id,session,request_key,question,status,answer,created,finished) VALUES(?,?,?,?,?,?,?,?)",
                (turn_id, chat["id"], ident(), "Check JSON", "completed", packed(answer), 10, 20),
            )
            db.execute(
                "INSERT INTO runs(id,session,request_key,seed,status,created,finished,result,action,parameters) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (run_id, chat["id"], ident(), 0, "completed", 12, 15, packed(result), "json-check", packed(parameters)),
            )
        service = Handoffs(workspace)

        def selection(selected):
            return HandoffSelection(
                checkpoint=turn_id,
                purpose="Review the JSON check.",
                summary="The fixed checker completed.",
                open_questions="",
                files=[files[name]["id"] for name in selected],
                runs=[run_id],
                excerpts=[{"turn": turn_id, "part": "answer", "omit_references": True}],
                mode="verify",
            )

        with self.assertRaisesRegex(Denied, "every file"):
            service.freeze(chat["id"], actor, selection(["valid.json"]))
        shared = service.freeze(
            chat["id"], actor, selection(["valid.json", "invalid.json"])
        )
        serialized = packed(shared["manifest"])
        for file in files.values():
            self.assertNotIn(file["id"], serialized)
        historical = shared["manifest"]["context"]["runs"][0]
        self.assertEqual(
            historical["parameters"]["files"], historical["inputs"]
        )

    async def test_model_dispatch_exposes_only_json_tool_and_uses_registered_job(self):
        seen = []
        completed = {
            "id": "1" * 32,
            "session": self.session["id"],
            "version": self.session["version"],
            "action": "json-check",
            "parameters": {"files": []},
            "status": "completed",
            "created": 0,
            "finished": 1,
            "result": {"output": {"action": "json-check", "files": []}},
            "error": None,
        }

        async def respond(messages, info):
            seen.append([tool.name for tool in info.function_tools])
            if len(seen) == 1:
                return ModelResponse(
                    parts=[ToolCallPart("check_json", {}, tool_call_id="check")]
                )
            answer = {
                "answer": "The registered JSON check completed.",
                "claims": [{"kind": "new_run", "text": "The check completed."}],
                "citations": [],
                "run_references": [completed["id"]],
                "historical_run_references": [],
                "context_references": [],
                "limitations": [],
                "pending_request_id": None,
            }
            return ModelResponse(
                parts=[ToolCallPart(info.output_tools[0].name, answer)]
            )

        service = Conversations(
            self.store, self.jobs, test_model=FunctionModel(respond)
        )
        with (
            patch.object(
                self.jobs, "submit_json_check", return_value=completed
            ) as submit,
            patch.object(self.jobs, "get", return_value=completed),
        ):
            result = await service.ask(
                self.session["id"], "reviewer", "Run the JSON check.", "agent-json-001"
            )
        self.assertEqual(result["status"], "completed", result)
        self.assertIn("check_json", seen[0])
        self.assertNotIn("submit_verification", seen[0])
        submit.assert_called_once()

    async def test_json_run_reference_failure_gets_one_final_only_correction(self):
        seen = []
        completed = {
            "id": "2" * 32,
            "session": self.session["id"],
            "version": self.session["version"],
            "action": "json-check",
            "parameters": {"files": []},
            "status": "completed",
            "created": 0,
            "finished": 1,
            "result": {"output": {"action": "json-check", "files": []}},
            "error": None,
        }

        async def respond(messages, info):
            seen.append([tool.name for tool in info.function_tools])
            if len(seen) == 1:
                return ModelResponse(
                    parts=[ToolCallPart("check_json", {}, tool_call_id="check")]
                )
            answer = {
                "answer": "The JSON check completed.",
                "claims": [
                    {
                        "kind": "reported" if len(seen) == 2 else "new_run",
                        "text": "The JSON check completed.",
                    }
                ],
                "citations": [],
                "run_references": [] if len(seen) == 2 else [completed["id"]],
                "historical_run_references": [],
                "context_references": [],
                "limitations": [],
                "pending_request_id": None,
            }
            return ModelResponse(
                parts=[ToolCallPart(info.output_tools[0].name, answer)]
            )

        service = Conversations(
            self.store, self.jobs, test_model=FunctionModel(respond)
        )
        with (
            patch.object(
                self.jobs, "submit_json_check", return_value=completed
            ) as submit,
            patch.object(self.jobs, "get", return_value=completed),
        ):
            result = await service.ask(
                self.session["id"],
                "reviewer",
                "Run and report the JSON check.",
                "agent-json-repair",
            )
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["usage"]["requests"], 3)
        self.assertEqual(result["answer"]["run_references"], [completed["id"]])
        self.assertEqual(seen[2], [])
        submit.assert_called_once()

    async def test_existing_current_session_run_is_not_historical(self):
        run_id = ident()
        parameters = {
            "files": [
                {
                    "id": self.session["manifest"]["files"][0]["id"],
                    "sha256": self.session["manifest"]["files"][0]["sha256"],
                }
            ]
        }
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO runs(id,session,request_key,seed,status,created,finished,result,action,parameters) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    self.session["id"],
                    ident(),
                    0,
                    "completed",
                    1,
                    2,
                    packed(
                        {
                            "action": "json-check",
                            "inputs": parameters,
                            "output": {"action": "json-check", "files": []},
                        }
                    ),
                    "json-check",
                    packed(parameters),
                ),
            )
        seen = []

        async def respond(messages, info):
            seen.append(([tool.name for tool in info.function_tools], repr(messages)))
            answer = {
                "answer": "The existing current-session run completed.",
                "claims": [
                    {
                        "kind": "historical_run" if len(seen) == 1 else "new_run",
                        "text": "The run completed.",
                    }
                ],
                "citations": [],
                "run_references": [] if len(seen) == 1 else [run_id],
                "historical_run_references": [],
                "context_references": [],
                "limitations": [],
                "pending_request_id": None,
            }
            return ModelResponse(
                parts=[ToolCallPart(info.output_tools[0].name, answer)]
            )

        service = Conversations(
            self.store, self.jobs, test_model=FunctionModel(respond)
        )
        before = len(self.jobs.list(self.session["id"], "reviewer"))
        result = await service.ask(
            self.session["id"],
            "reviewer",
            "Report the existing JSON run.",
            "existing-json-repair",
        )
        after = len(self.jobs.list(self.session["id"], "reviewer"))
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["answer"]["run_references"], [run_id])
        self.assertEqual(before, after)
        self.assertEqual(seen[1][0], [])
        self.assertIn('"scope":"current_session"', seen[0][1])

    async def test_reference_repair_does_not_relax_scope_completion_or_revoke(self):
        other = self.store.new_session(self.session["version"], "reviewer")
        foreign_id, queued_id = ident(), ident()
        with self.store.connect() as db:
            for run_id, session_id, status in (
                (foreign_id, other["id"], "completed"),
                (queued_id, self.session["id"], "queued"),
            ):
                db.execute(
                    "INSERT INTO runs(id,session,request_key,seed,status,created,action,parameters) VALUES(?,?,?,?,?,?,?,?)",
                    (run_id, session_id, ident(), 0, status, 1, "json-check", "{}"),
                )

        async def run_bad(reference, key):
            calls = []

            async def respond(messages, info):
                calls.append([tool.name for tool in info.function_tools])
                answer = {
                    "answer": "Unsupported run claim.",
                    "claims": [{"kind": "new_run", "text": "Unsupported."}],
                    "citations": [],
                    "run_references": [reference],
                    "historical_run_references": [],
                    "context_references": [],
                    "limitations": [],
                    "pending_request_id": None,
                }
                return ModelResponse(
                    parts=[ToolCallPart(info.output_tools[0].name, answer)]
                )

            service = Conversations(
                self.store, self.jobs, test_model=FunctionModel(respond)
            )
            return await service.ask(
                self.session["id"], "reviewer", "Make a run claim.", key
            ), calls

        foreign, foreign_calls = await run_bad(foreign_id, "foreign-run-answer")
        self.assertEqual(foreign["status"], "failed")
        self.assertEqual(len(foreign_calls), 1)
        queued, queued_calls = await run_bad(queued_id, "queued-run-answer")
        self.assertEqual(queued["status"], "failed")
        self.assertEqual(len(queued_calls), 2)
        self.assertEqual(queued_calls[1], [])

        calls = []

        async def revoke_during_repair(messages, info):
            calls.append([tool.name for tool in info.function_tools])
            if len(calls) == 2:
                self.store.revoke(self.session["version"])
            answer = {
                "answer": "Invalid reported claim.",
                "claims": [{"kind": "reported", "text": "Unsupported."}],
                "citations": [],
                "run_references": [],
                "historical_run_references": [],
                "context_references": [],
                "limitations": [],
                "pending_request_id": None,
            }
            return ModelResponse(
                parts=[ToolCallPart(info.output_tools[0].name, answer)]
            )

        service = Conversations(
            self.store, self.jobs, test_model=FunctionModel(revoke_during_repair)
        )
        with self.assertRaises(Denied):
            await service.ask(
                self.session["id"],
                "reviewer",
                "Trigger revoke during repair.",
                "revoke-during-repair",
            )
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1], [])

    async def test_action_cannot_share_a_response_with_invalid_final_output(self):
        calls = []

        async def respond(messages, info):
            calls.append([tool.name for tool in info.function_tools])
            invalid = {
                "answer": "Unsupported final answer.",
                "claims": [{"kind": "reported", "text": "Unsupported."}],
                "citations": [],
                "run_references": [],
                "historical_run_references": [],
                "context_references": [],
                "limitations": [],
                "pending_request_id": None,
            }
            return ModelResponse(
                parts=[
                    ToolCallPart("check_json", {}, tool_call_id="side-effect"),
                    ToolCallPart(info.output_tools[0].name, invalid, tool_call_id="final"),
                ]
            )

        service = Conversations(
            self.store, self.jobs, test_model=FunctionModel(respond)
        )
        with patch.object(self.jobs, "submit_json_check") as submit:
            result = await service.ask(
                self.session["id"],
                "reviewer",
                "Do not mix execution and an invalid final.",
                "mixed-final-action",
            )
        self.assertEqual(result["status"], "failed")
        submit.assert_not_called()
