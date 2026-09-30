"""Real authorization/SQLite/API checks; runtime doubles are labeled explicitly."""

import base64
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from prism.computation import accepted_output, decode_payload
from prism.jobs import Jobs
from prism.projects import ProjectSource
from prism.sharing import Denied, Source, Store, digest, packed, prepare_source
from prism.webapp import DemoAuth, create_app
from prism.workspace import Workspaces, with_workspace

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "examples/pilot-decision-review/.prism-project.json"
)


class ComputationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "state.sqlite")
        self.source = ProjectSource.from_manifest(FIXTURE)
        self.base = self.source.freeze(
            list(self.source.names), "Audit synthetic pilot calculations", "inspect"
        )
        self.editable = [
            "analysis.py",
            "analysis-plan.json",
            "report.md",
            "results/metrics.json",
            "results/data-quality.json",
        ]
        self.manifest = with_workspace(
            self.base,
            self.editable,
            python_entrypoint="analysis.py",
            python_outputs=["results/metrics.json", "results/data-quality.json"],
        )
        self.version = self.store.candidate(self.manifest)
        self.store.approve(self.version["id"], self.version["digest"])
        self.session = self.store.new_session(self.version["id"], "reviewer")
        self.ws = Workspaces(self.store)
        self.jobs = Jobs(self.store)
        self.addCleanup(self.jobs.shutdown)
        self.entrypoint = self.manifest["action"]["entrypoint"]

    def inputs(self, revision=0):
        with self.store.connect() as db:
            return self.ws.computation_inputs(
                db, self.session["id"], "reviewer", self.entrypoint, revision
            )

    def test_explicit_approval_canonical_policy_and_old_grants(self):
        old = self.store.candidate(with_workspace(self.base, ["report.md"]))
        self.store.approve(old["id"], old["digest"])
        session = self.store.new_session(old["id"], "reviewer")
        with self.assertRaises(Denied):
            self.jobs.submit_workspace_python(
                session["id"], "reviewer", self.entrypoint, 0, "old-grant"
            )
        with self.assertRaises(Denied):
            self.jobs.submit_json_check(session["id"], "reviewer", "no-check")
        for field, value in (
            ("cpu", 8),
            ("profile", "reference-linux"),
            ("entrypoint", "f" * 24),
            ("network", "host"),
        ):
            bad = copy.deepcopy(self.manifest)
            bad["action"][field] = value
            with self.assertRaises(Denied):
                Store.checked_manifest(
                    {"manifest": packed(bad), "digest": digest(packed(bad))}
                )
        with self.assertRaises(Denied):
            with_workspace(
                self.base,
                ["report.md"],
                python_entrypoint="analysis.py",
                python_outputs=["results/metrics.json"],
            )

    def test_exact_revision_actor_entrypoint_and_frozen_bytes(self):
        parameters, encoded = self.inputs()
        value = decode_payload(encoded)
        self.assertEqual(value["entrypoint"], "analysis.py")
        self.assertNotIn("private/", encoded)
        self.assertNotIn("PRISM_UNSHARED_PILOT_CANARY", packed(value))
        new = "print('approved revised bytes')\n"
        self.ws.edit(self.session["id"], "reviewer", self.entrypoint, new, 0)
        with self.assertRaises(Denied):
            self.inputs(0)
        later, encoded = self.inputs(1)
        self.assertNotEqual(parameters["files"], later["files"])
        self.assertIn(new, [f["text"] for f in decode_payload(encoded)["inputs"]])
        for actor, script in (
            ("observer", self.entrypoint),
            ("reviewer", "f" * 24),
            ("reviewer", "../analysis.py"),
        ):
            with self.assertRaises(Denied):
                self.jobs.submit_workspace_python(
                    self.session["id"], actor, script, 1, "denied-input"
                )
        self.assertNotEqual((FIXTURE.parent / "analysis.py").read_text(), new)

    def test_worker_payload_rejects_paths_duplicate_ids_digest_and_limits(self):
        _, encoded = self.inputs()
        value = decode_payload(encoded)

        def reject(changed):
            with self.assertRaises(ValueError):
                decode_payload(base64.b64encode(packed(changed).encode()).decode())

        for name in ("/etc/passwd", "../secrets", "data/../../private"):
            changed = copy.deepcopy(value)
            changed["inputs"][0]["name"] = name
            reject(changed)
        changed = copy.deepcopy(value)
        changed["inputs"][0]["sha256"] = "0" * 64
        reject(changed)
        changed = copy.deepcopy(value)
        changed["outputs"][0]["id"] = value["inputs"][0]["id"]
        reject(changed)
        changed = copy.deepcopy(value)
        changed["outputs"][0]["name"] = "elsewhere.json"
        reject(changed)
        changed = copy.deepcopy(value)
        changed["entrypoint"] = "../analysis.py"
        reject(changed)
        with self.assertRaises(ValueError):
            decode_payload("x" * (160 * 1024 + 1))

    def result(self):
        parameters, _ = self.inputs()
        actual = {
            "action": "python-workspace",
            "files": [
                {
                    **o,
                    "text": '{"synthetic":true}\n',
                    "sha256": digest('{"synthetic":true}\n'),
                }
                for o in parameters["outputs"]
            ],
            "stdout": "",
            "stderr": "",
        }
        return parameters, actual

    def test_untrusted_output_ingestion_denies_paths_sizes_hashes_nonfinite(self):
        parameters, actual = self.result()
        self.assertEqual(accepted_output(actual, parameters), actual)
        for field, value in (
            ("name", "../../secret.json"),
            ("id", "f" * 24),
            ("sha256", "0" * 64),
            ("text", "x" * 32769),
        ):
            bad = copy.deepcopy(actual)
            bad["files"][0][field] = value
            with self.assertRaises((ValueError, Denied)):
                accepted_output(bad, parameters)
        for text in (
            '{"x":NaN}',
            '{"x":Infinity}',
            '{"nested":[1e309]}',
            '{"nested":{"x":-1e309}}',
            "42",
            '"scalar"',
        ):
            bad = copy.deepcopy(actual)
            bad["files"][0].update(text=text, sha256=digest(text))
            with self.assertRaises(ValueError):
                accepted_output(bad, parameters)
        bad = copy.deepcopy(actual)
        bad["files"].append(copy.deepcopy(bad["files"][0]))
        with self.assertRaises(ValueError):
            accepted_output(bad, parameters)

    def test_scripted_completion_atomic_import_immutable_return_and_stale(self):
        parameters, actual = self.result()
        action = self.manifest["action"]
        result = {
            "action": "python-workspace",
            "inputs": parameters,
            "output": actual,
            "profile": "development",
            "image": action["image"],
            "program_sha256": action["program_sha256"],
            "exit_code": 0,
            "cleaned_up": True,
        }
        key = "a" * 32
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
        self.assertIsNone(
            self.ws.state(self.session["id"], "reviewer")["matching_computation"]
        )
        self.ws.apply_computation(self.session["id"], "reviewer", key, 0)
        self.assertEqual(self.ws.state(self.session["id"], "reviewer")["revision"], 1)
        item = self.ws.prepare_return(self.session["id"], "reviewer", 1)
        self.assertEqual(item["computation_run"], key)
        old = self.ws.download(self.session["id"], "reviewer", item["id"])
        with self.assertRaises(Denied):
            self.ws.apply_computation(self.session["id"], "reviewer", key, 1)
        second = self.store.new_session(self.version["id"], "reviewer")
        with self.assertRaises(Denied):
            self.ws.apply_computation(second["id"], "reviewer", key, 0)
        self.ws.edit(self.session["id"], "reviewer", self.entrypoint, "changed\n", 1)
        self.assertIsNone(
            self.ws.state(self.session["id"], "reviewer")["matching_computation"]
        )
        self.assertEqual(
            old, self.ws.download(self.session["id"], "reviewer", item["id"])
        )
        self.store.revoke(self.version["id"])
        with self.assertRaises(Denied):
            self.ws.apply_computation(self.session["id"], "reviewer", key, 2)
        with self.assertRaises(Denied):
            self.ws.download(self.session["id"], "reviewer", item["id"])

    def test_reference_profile_never_falls_back_and_reservation_rechecks(self):
        # Real Jobs admission, fake launch only: not execution evidence.
        parameters, encoded = self.inputs()
        with patch.object(self.jobs, "_launch") as launch:
            self.ws.edit(self.session["id"], "reviewer", self.entrypoint, "new\n", 0)
            with self.assertRaises(Denied):
                self.jobs._submit(
                    self.session["id"],
                    "reviewer",
                    "python-workspace",
                    parameters,
                    "race-check",
                    encoded,
                )
            launch.assert_not_called()
        with patch.object(self.jobs.registry, "assert_ready", side_effect=ValueError()):
            with self.assertRaises(Denied) as denied:
                self.jobs.submit_workspace_python(
                    self.session["id"], "reviewer", self.entrypoint, 1, "profile-check"
                )
            self.assertEqual(denied.exception.status, 503)


class ComputationAPITests(unittest.TestCase):
    def test_explicit_owner_policy_review_reviewer_endpoint_and_body_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
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

                def login(actor):
                    headers["X-Prism-CSRF"] = client.post(
                        "/api/login",
                        json={"token": auth.tokens[actor]},
                        headers=headers,
                    ).json()["csrf"]

                login("owner")
                response = client.post(
                    "/api/owner/projects/pilot-decision-review/candidates",
                    headers=headers,
                    json={
                        "files": list(ProjectSource.from_manifest(FIXTURE).names),
                        "purpose": "Compute bounded pilot results",
                        "mode": "continue",
                        "editable_files": [
                            "analysis.py",
                            "results/metrics.json",
                            "results/data-quality.json",
                        ],
                        "python_entrypoint": "analysis.py",
                        "python_outputs": [
                            "results/metrics.json",
                            "results/data-quality.json",
                        ],
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                version = response.json()
                self.assertEqual(version["manifest"]["workspace"]["schema"], 3)
                client.post(
                    f"/api/owner/versions/{version['id']}/approve",
                    headers=headers,
                    json={"digest": version["digest"]},
                )
                login("reviewer")
                session = client.post(
                    "/api/review/sessions",
                    headers=headers,
                    json={"version": version["id"]},
                ).json()
                path = f"/api/review/sessions/{session['id']}/workspace/python"
                body = {
                    "entrypoint": version["manifest"]["action"]["entrypoint"],
                    "expected_revision": 0,
                    "request_key": "typed-request",
                }
                with patch.object(Jobs, "_launch") as launch:
                    response = client.post(path, json=body, headers=headers)
                    self.assertEqual(response.status_code, 200, response.text)
                    launch.assert_called_once()
                self.assertEqual(
                    client.post(
                        path, json={**body, "shell": "whoami"}, headers=headers
                    ).status_code,
                    422,
                )
                self.assertEqual(
                    client.post(
                        path, json=body, headers={"Origin": headers["Origin"]}
                    ).status_code,
                    403,
                )
                login("observer")
                self.assertEqual(
                    client.post(path, json=body, headers=headers).status_code, 403
                )


class ContextualPythonHandoffTests(unittest.TestCase):
    def test_owner_context_remaps_script_and_output_ids_with_explicit_capability(self):
        from prism.handoff import HandoffSelection
        from prism.owner import OwnerIdentity
        from prism.sharing import ident

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prepare_source(root / "source")
            store = Store(root / "state.sqlite")
            app = create_app(
                store,
                Source(root / "source"),
                project_sources=[ProjectSource.from_manifest(FIXTURE)],
            )
            self.addCleanup(app.state.owner_jobs.shutdown)
            actor = OwnerIdentity("owner", "pilot-decision-review")
            chat = app.state.owner_workspace.new_conversation(
                actor, files=list(ProjectSource.from_manifest(FIXTURE).names)
            )
            checkpoint = ident()
            # A seeded completed excerpt is test setup, not real inference evidence.
            with store.connect() as db:
                db.execute(
                    "INSERT INTO turns(id,session,request_key,question,status,answer,created,finished) VALUES(?,?,?,'Synthetic test context','completed',?,0,1)",
                    (
                        checkpoint,
                        chat["id"],
                        "handoff-test",
                        packed(
                            {
                                "answer": "Review the synthetic pilot. Approval pending.",
                                "citations": [],
                                "run_references": [],
                            }
                        ),
                    ),
                )
            ids = {f["name"]: f["id"] for f in chat["manifest"]["files"]}
            version = app.state.handoffs.freeze(
                chat["id"],
                actor,
                HandoffSelection(
                    checkpoint=checkpoint,
                    purpose="Compute bounded pilot results",
                    summary="Synthetic owner context; pending approval",
                    open_questions="",
                    files=list(ids.values()),
                    runs=[],
                    excerpts=[{"turn": checkpoint, "part": "answer"}],
                    mode="continue",
                    editable_files=[
                        ids[n]
                        for n in (
                            "analysis.py",
                            "results/metrics.json",
                            "results/data-quality.json",
                        )
                    ],
                    python_entrypoint=ids["analysis.py"],
                    python_outputs=[
                        ids["results/metrics.json"],
                        ids["results/data-quality.json"],
                    ],
                ),
            )
            action = version["manifest"]["action"]
            self.assertNotEqual(action["entrypoint"], ids["analysis.py"])
            shared = {f["name"]: f["id"] for f in version["manifest"]["files"]}
            self.assertEqual(action["entrypoint"], shared["analysis.py"])
            self.assertEqual(
                action["outputs"],
                [
                    {"id": shared[n], "name": n}
                    for n in sorted(
                        ("results/metrics.json", "results/data-quality.json")
                    )
                ],
            )
