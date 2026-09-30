"""Actual SQLite/API workspace boundaries; no model or runtime evidence."""

import copy
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from prism.jobs import Jobs
from prism.sharing import (
    Denied,
    NamedPrincipal,
    Source,
    Store,
    digest,
    ident,
    packed,
    prepare_source,
)
from prism.webapp import DemoAuth, create_app
from prism.workspace import EDIT_LIMIT, Workspaces, with_workspace


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        prepare_source(self.root / "source")
        self.source = Source(self.root / "source")
        self.store = Store(self.root / "state.sqlite")
        self.workspace = Workspaces(self.store)
        self.manifest = with_workspace(
            self.source.freeze(
                ["overview.md", "baseline.json"],
                "Revise the project overview",
                "inspect",
            ),
            ["overview.md"],
        )
        self.version = self.store.candidate(self.manifest)
        self.store.approve(self.version["id"], self.version["digest"])
        self.session = self.store.new_session(self.version["id"], "reviewer")
        self.file_id = next(
            file["id"]
            for file in self.manifest["files"]
            if file["name"] == "overview.md"
        )
        self.readonly = next(
            file["id"]
            for file in self.manifest["files"]
            if file["name"] == "baseline.json"
        )

    def edit(self, text="A useful revision.\n", revision=0):
        return self.workspace.edit(
            self.session["id"], "reviewer", self.file_id, text, revision
        )

    def test_edit_diff_restart_and_source_preservation(self):
        before = (self.source.root / "overview.md").read_bytes()
        self.assertEqual(self.edit()["revision"], 1)
        changes = self.workspace.diff(self.session["id"], "reviewer")
        self.assertIn("+A useful revision.", changes["changes"][0]["diff"])
        self.assertFalse(changes["validated"])
        self.assertEqual(
            changes["changes"][0]["after_sha256"], digest("A useful revision.\n")
        )
        self.assertEqual((self.source.root / "overview.md").read_bytes(), before)
        self.assertEqual(
            self.store.owner_version(self.version["id"])["digest"],
            self.version["digest"],
        )
        self.assertEqual(
            self.store.evidence(self.session["id"], "reviewer", self.file_id)["sha256"],
            digest(before.decode()),
        )
        restarted = Workspaces(Store(self.store.path))
        self.assertEqual(
            restarted.read(self.session["id"], "reviewer", self.file_id)["text"],
            "A useful revision.\n",
        )
        self.assertNotIn("A useful revision", packed(self.store.activity()))

    def test_readonly_private_and_path_arguments_are_denied(self):
        for value in (
            self.readonly,
            digest("private-notes.txt")[:24],
            "../source/overview.md",
            "/etc/passwd",
            "overview.md",
        ):
            with self.subTest(value=value), self.assertRaises(Denied):
                self.workspace.edit(self.session["id"], "reviewer", value, "changed", 0)
        with self.assertRaises(Denied):
            self.workspace.read(
                self.session["id"], "reviewer", digest("private-notes.txt")[:24]
            )
        self.assertEqual(
            self.workspace.state(self.session["id"], "reviewer")["revision"], 0
        )

    def test_separate_session_observer_and_inspect_only(self):
        second = self.store.new_session(self.version["id"], "reviewer")
        self.edit()
        self.assertNotEqual(
            self.workspace.read(second["id"], "reviewer", self.file_id)["text"],
            "A useful revision.\n",
        )
        observer = self.store.new_session(self.version["id"], "observer")
        for actor, session in (
            ("observer", observer["id"]),
            ("observer", self.session["id"]),
            ("owner", self.session["id"]),
        ):
            with self.assertRaises(Denied):
                self.workspace.state(session, actor)
        self.assertEqual(
            self.store.session(observer["id"], "observer")["mode"], "inspect"
        )

    def test_stale_revision_and_concurrent_saves(self):
        def save(text):
            try:
                return self.edit(text)
            except Denied as exc:
                return exc.status

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(save, ["one\n", "two\n"]))
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertIn(409, results)
        with self.assertRaises(Denied) as failed:
            self.edit("stale", 0)
        self.assertEqual(failed.exception.status, 409)

    def test_text_limits_utf8_lines_controls_and_unchanged_state(self):
        for text in ("中" * 11000, "x\n" * 2001, "a\x00b", "x\x1by", "\ud800"):
            with self.subTest(text=repr(text[:10])), self.assertRaises(Denied):
                self.edit(text)
        self.assertEqual(
            self.workspace.state(self.session["id"], "reviewer")["revision"], 0
        )
        self.edit("Readable Unicode: café 中文\n")

    def test_edit_allowance_noop_and_restore(self):
        original = self.workspace.read(self.session["id"], "reviewer", self.file_id)[
            "text"
        ]
        self.assertEqual(self.edit(original), {"revision": 0, "changed": False})
        self.edit()
        self.edit(original, 1)
        self.assertEqual(
            self.workspace.diff(self.session["id"], "reviewer")["changes"], []
        )
        for revision in range(2, EDIT_LIMIT):
            self.edit(f"revision {revision}\n", revision)
        with self.assertRaises(Denied) as failed:
            self.edit("over quota", EDIT_LIMIT)
        self.assertEqual(failed.exception.status, 429)

    def test_final_newline_change_is_visible(self):
        original = self.workspace.read(self.session["id"], "reviewer", self.file_id)[
            "text"
        ]
        self.edit(original.rstrip("\n"))
        change = self.workspace.diff(self.session["id"], "reviewer")["changes"][0]
        self.assertTrue(change["before_final_newline"])
        self.assertFalse(change["after_final_newline"])
        self.assertNotEqual(change["before_sha256"], change["after_sha256"])

    def test_total_workspace_quota_and_frozen_copy_after_source_link_swap(self):
        root = self.root / "large"
        root.mkdir()
        names = ["editable.md", "a.txt", "b.txt", "c.txt"]
        for name in names:
            (root / name).write_text("x" * (8000 if name == "editable.md" else 30000))
        source = Source(root, names=names)
        manifest = with_workspace(
            source.freeze(names, "Review bounded copies", "inspect"), ["editable.md"]
        )
        candidate = self.store.candidate(manifest)
        self.store.approve(candidate["id"], candidate["digest"])
        session = self.store.new_session(candidate["id"], "reviewer")
        file_id = manifest["workspace"]["editable"][0]
        with self.assertRaises(Denied):
            self.workspace.edit(session["id"], "reviewer", file_id, "x" * 32768, 0)
        self.assertEqual(self.workspace.state(session["id"], "reviewer")["revision"], 0)
        # Swapping the owner source path cannot redirect reads of approved bytes.
        (root / "editable.md").unlink()
        (root / "editable.md").symlink_to(self.source.root / "private-notes.txt")
        text = self.workspace.read(session["id"], "reviewer", file_id)["text"]
        self.assertEqual(text, "x" * 8000)
        self.assertNotIn("PRISM_UNSHARED_ORCHID", text)

    def test_revocation_expiry_blocks_all_workspace_operations(self):
        self.edit()
        self.store.revoke(self.version["id"])
        for operation in (self.workspace.state, self.workspace.diff):
            with self.assertRaises(Denied):
                operation(self.session["id"], "reviewer")
        with self.assertRaises(Denied):
            self.workspace.read(self.session["id"], "reviewer", self.file_id)
        with self.assertRaises(Denied):
            self.edit("blocked", 1)

    def test_named_grants_downgrade_cross_identity_expiry_and_revoke(self):
        principal = NamedPrincipal("https://accounts.example", "recipient-one")

        def redeem(mode="continue"):
            invitation = self.store.create_invitation(
                self.version["id"], principal, mode=mode, expires_in=3600
            )
            return self.store.redeem_invitation(invitation["token"], principal)

        session = redeem()
        self.workspace.edit(
            session["id"], principal, self.file_id, "Named working copy\n", 0
        )
        with self.assertRaises(Denied):
            self.workspace.read(
                session["id"], NamedPrincipal(principal.issuer, "other"), self.file_id
            )
        downgraded = redeem("inspect")
        with self.assertRaises(Denied):
            self.workspace.state(downgraded["id"], principal)
        self.store.revoke_grant(session["grant_id"])
        with self.assertRaises(Denied):
            self.workspace.diff(session["id"], principal)
        expiring = redeem()
        with self.store.connect() as db:
            db.execute("UPDATE sessions SET expires=0 WHERE id=?", (expiring["id"],))
        with self.assertRaises(Denied):
            self.workspace.state(expiring["id"], principal)

    def test_missing_edit_permission_and_malformed_policy(self):
        base = self.source.freeze(["overview.md"], "Review material", "inspect")
        for selected in ([], ["private-notes.txt"], ["overview.md", "overview.md"]):
            with self.assertRaises(Denied):
                with_workspace(base, selected)
        plain = self.store.candidate(base)
        self.store.approve(plain["id"], plain["digest"])
        with self.assertRaises(Denied):
            self.store.create_invitation(
                plain["id"],
                NamedPrincipal("https://accounts.example", "one"),
                mode="continue",
                expires_in=300,
            )
        bad = copy.deepcopy(self.manifest)
        bad["workspace"]["editable"] = ["not-a-file"]
        with self.assertRaises(Denied):
            Store.checked_manifest(
                {"manifest": packed(bad), "digest": digest(packed(bad))}
            )
        bad = copy.deepcopy(self.manifest)
        bad["schema"] = 1
        with self.assertRaises(Denied):
            Store.checked_manifest(
                {"manifest": packed(bad), "digest": digest(packed(bad))}
            )

    def test_continue_does_not_enable_execution(self):
        jobs = Jobs(self.store)
        self.addCleanup(jobs.shutdown)
        with self.assertRaises(Denied):
            jobs.submit(self.session["id"], "reviewer", 7, "not-permitted")
        with self.assertRaises(Denied):
            jobs.submit_json_check(self.session["id"], "reviewer", "not-permitted-json")


class WorkspaceAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        prepare_source(self.root / "source")
        self.store = Store(self.root / "state.sqlite")
        self.auth = DemoAuth()
        self.app = create_app(self.store, Source(self.root / "source"), auth=self.auth)
        self.client = TestClient(self.app, base_url="http://127.0.0.1:8765")
        self.addCleanup(self.client.close)
        self.addCleanup(self.app.state.owner_jobs.shutdown)
        self.headers = {"Origin": "http://127.0.0.1:8765"}
        self.login("owner")
        response = self.client.post(
            "/api/owner/projects/paired-evaluation/candidates",
            json={
                "files": ["overview.md", "baseline.json"],
                "purpose": "Revise the overview",
                "mode": "continue",
                "editable_files": ["overview.md"],
            },
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.version = response.json()
        self.file_id = self.version["manifest"]["workspace"]["editable"][0]
        response = self.client.post(
            f"/api/owner/versions/{self.version['id']}/approve",
            json={"digest": self.version["digest"]},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.login("reviewer")
        response = self.client.post(
            "/api/review/sessions",
            json={"version": self.version["id"]},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.session = response.json()
        self.path = f"/api/review/sessions/{self.session['id']}/workspace"

    def login(self, actor):
        result = self.client.post(
            "/api/login", json={"token": self.auth.tokens[actor]}, headers=self.headers
        )
        self.headers["X-Prism-CSRF"] = result.json()["csrf"]

    def test_api_edit_diff_and_forbidden_file(self):
        route = self.path + "/files/" + self.file_id
        self.assertEqual(self.client.get(self.path).json()["revision"], 0)
        self.assertEqual(
            self.client.post(
                route,
                json={"text": "Updated text\n", "expected_revision": 0},
                headers=self.headers,
            ).status_code,
            200,
        )
        self.assertEqual(self.client.get(route).json()["text"], "Updated text\n")
        self.assertEqual(len(self.client.get(self.path + "/diff").json()["changes"]), 1)
        self.assertEqual(
            self.client.post(
                self.path + "/files/" + digest("baseline.json")[:24],
                json={"text": "write", "expected_revision": 1},
                headers=self.headers,
            ).status_code,
            403,
        )

    def test_api_csrf_role_forgery_and_cross_session(self):
        route = self.path + "/files/" + self.file_id
        self.assertEqual(
            self.client.post(
                route,
                json={"text": "x", "expected_revision": 0},
                headers={"Origin": self.headers["Origin"]},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                route,
                json={"text": "x", "expected_revision": 0, "editable": True},
                headers=self.headers,
            ).status_code,
            422,
        )
        self.login("observer")
        self.assertEqual(self.client.get(self.path).status_code, 403)
        self.assertEqual(
            self.client.post(
                route, json={"text": "x", "expected_revision": 0}, headers=self.headers
            ).status_code,
            403,
        )

    def test_route_specific_bounded_body_and_utf8_limit(self):
        route = self.path + "/files/" + self.file_id
        result = self.client.post(
            route,
            json={"text": "x" * 20000, "expected_revision": 0},
            headers=self.headers,
        )
        self.assertEqual(result.status_code, 200, result.text)
        result = self.client.post(
            route,
            json={"text": "中" * 11000, "expected_revision": 1},
            headers=self.headers,
        )
        self.assertEqual(result.status_code, 400)
        self.assertEqual(
            self.client.post(
                route,
                content='{"text":"' + "x" * (256 * 1024) + '"}',
                headers={**self.headers, "Content-Type": "application/json"},
            ).status_code,
            413,
        )
        self.assertEqual(
            self.client.post(
                "/api/review/sessions",
                content='{"version":"' + "x" * 20000 + '"}',
                headers={**self.headers, "Content-Type": "application/json"},
            ).status_code,
            413,
        )

    def test_contextual_handoff_remaps_editable_ids(self):
        from prism.handoff import HandoffSelection
        from prism.owner import MODEL_POLICY, OwnerIdentity

        actor = OwnerIdentity("owner", "paired-evaluation")
        chat = self.app.state.owner_workspace.new_conversation(actor, MODEL_POLICY)
        checkpoint = ident()
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO turns(id,session,request_key,question,status,answer,created,finished) VALUES(?,?,?,?,?,?,?,?)",
                (
                    checkpoint,
                    chat["id"],
                    "synthetic-checkpoint",
                    "Review the overview",
                    "completed",
                    packed(
                        {
                            "answer": "Review this synthetic project.",
                            "citations": [],
                            "run_references": [],
                        }
                    ),
                    time.time() - 2,
                    time.time() - 1,
                ),
            )
        source_id = next(
            file["id"]
            for file in chat["manifest"]["files"]
            if file["name"] == "overview.md"
        )
        candidate = self.app.state.handoffs.freeze(
            chat["id"],
            actor,
            HandoffSelection(
                checkpoint=checkpoint,
                purpose="Revise documentation",
                summary="Synthetic reviewed background",
                open_questions="",
                files=[source_id],
                runs=[],
                excerpts=[{"turn": checkpoint, "part": "answer"}],
                mode="continue",
                editable_files=[source_id],
            ),
        )
        self.assertEqual(candidate["manifest"]["schema"], 3)
        share_id = candidate["manifest"]["files"][0]["id"]
        self.assertNotEqual(share_id, source_id)
        self.assertEqual(candidate["manifest"]["workspace"]["editable"], [share_id])
        self.assertEqual(
            candidate["manifest"]["context"]["summary"], "Synthetic reviewed background"
        )
