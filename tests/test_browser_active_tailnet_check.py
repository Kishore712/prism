"""Offline scope and run selection checks for the browser active-fault observer."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import tempfile
import time
import types
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts/m2-browser-active-tailnet-check.py"
)
spec = importlib.util.spec_from_file_location("browser_active_tailnet", SCRIPT)
assert spec is not None and spec.loader is not None
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

SESSION, VERSION, GRANT, RUN = (char * 32 for char in "abcd")
FIXTURE = {"config/release.json": "e" * 64}


class BrowserActiveSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.db_path = Path(self.temporary.name) / "test.sqlite"
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.executescript("""
                CREATE TABLE sessions(id,version,mode,expires,grant_revision,grant_id);
                CREATE TABLE grants(id,version,mode,action,revision,expires,revoked);
                CREATE TABLE versions(id,approved,revoked,manifest);
                CREATE TABLE runs(id,session,request_key,status,created,finished,result,error,action,
                    runtime_profile,runtime_resource,runtime_token);
                CREATE TABLE events(kind,resource,outcome);
            """)
            db.execute(
                "INSERT INTO sessions VALUES(?,?,?,?,?,?)",
                (SESSION, VERSION, "verify", time.time() + 3600, 1, GRANT),
            )
            db.execute(
                "INSERT INTO grants VALUES(?,?,?,?,?,?,?)",
                (GRANT, VERSION, "verify", "json-check", 1, time.time() + 3600, 0),
            )
            manifest = {
                "mode": "verify",
                "action": {
                    "id": "json-check",
                    "profile": "reference-linux",
                    "program_sha256": "f" * 64,
                },
                "files": [{"name": "config/release.json", "sha256": "e" * 64}],
            }
            db.execute(
                "INSERT INTO versions VALUES(?,?,?,?)",
                (VERSION, 1, 0, json.dumps(manifest)),
            )

    def insert_run(self, *, created=None, action="json-check", status="running"):
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute(
                "INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    RUN,
                    SESSION,
                    "browser-generated-key",
                    status,
                    time.time() if created is None else created,
                    None,
                    None,
                    None,
                    action,
                    "reference-linux",
                    "prism-m23-run-" + RUN,
                    "f" * 32,
                ),
            )

    def test_exact_live_grant_and_synthetic_files_required(self):
        self.assertEqual(
            check.read_scope(self.db_path, SESSION, VERSION, GRANT, FIXTURE)["id"],
            SESSION,
        )
        with self.assertRaises(RuntimeError):
            check.read_scope(self.db_path, SESSION, VERSION, GRANT, {"other": "e" * 64})
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute("UPDATE grants SET revision=2")
        with self.assertRaises(RuntimeError):
            check.read_scope(self.db_path, SESSION, VERSION, GRANT, FIXTURE)

    def test_only_a_new_exact_fixed_run_is_selected(self):
        armed = time.time()
        self.insert_run()
        rows = check.read_new_runs(self.db_path, SESSION, armed)
        self.assertEqual(len(rows), 1)
        check.validate_new_run(rows[0], armed_at=armed, session=SESSION)
        self.assertEqual(check.read_exact_run(self.db_path, SESSION, RUN)["id"], RUN)
        with self.assertRaises(RuntimeError):
            check.validate_new_run(rows[0], armed_at=armed, session="0" * 32)

    def test_old_or_wrong_action_run_is_refused(self):
        armed = time.time()
        self.insert_run(created=armed - 1, action="shell")
        self.assertEqual(check.read_new_runs(self.db_path, SESSION, armed), [])
        row = check.read_exact_run(self.db_path, SESSION, RUN)
        with self.assertRaises(RuntimeError):
            check.validate_new_run(row, armed_at=armed - 2, session=SESSION)

    def test_unsupported_host_does_not_load_pinned_host_helper(self):
        with (
            patch.object(check.platform, "system", return_value="Darwin"),
            patch.object(
                check, "pinned_active", side_effect=AssertionError("live helper loaded")
            ),
        ):
            result = check.run(Path("/irrelevant"), SESSION, VERSION, GRANT)
        self.assertEqual(result["error_kind"], "UnsupportedHost")
        self.assertFalse(result["fault_applied"])

    def test_current_release_replaces_only_verified_old_pins(self):
        helper = types.SimpleNamespace(
            TRANSFER_SHA256="e11ea7a88a2b029f73e2374d47f271af178ff0604febbe9a47e1b4c9259d4f27",
            SOURCE_SHA256="fee499e336567fd4c1f9a564c3fc2983fd520eb18d33e191d5ae29b267e1e16d",
            WATCHDOG_SHA256="f853945bd8c384101effbcfb494e368f4b880156b649456459650a045a6f0cab",
            LIFECYCLE_SHA256="79e3db10b3683bc65292f449b88798d4edb8674a6f8ea63c8cd08d8cd64c9c0f",
        )
        current = Path("/var/lib/prism/identity-pilot/app-releases") / (
            "service-" + check.RELEASE_TRANSFER_SHA256
        )
        check.configure_release_pins(helper, current)
        self.assertEqual(helper.TRANSFER_SHA256, check.RELEASE_TRANSFER_SHA256)
        self.assertEqual(helper.SOURCE_SHA256, check.RELEASE_SOURCE_SHA256)
        self.assertEqual(helper.WATCHDOG_SHA256, check.RELEASE_WATCHDOG_SHA256)
        self.assertEqual(helper.LIFECYCLE_SHA256, check.RELEASE_LIFECYCLE_SHA256)
        with self.assertRaises(RuntimeError):
            check.configure_release_pins(helper, current)

    def test_preflight_cli_returns_success(self):
        argv = [
            "check",
            "--release",
            "/var/lib/prism/identity-pilot/app-releases/service-"
            + check.RELEASE_TRANSFER_SHA256,
            "--session",
            SESSION,
            "--version",
            VERSION,
            "--grant",
            GRANT,
            "--preflight-only",
        ]
        with (
            patch.object(sys, "argv", argv),
            patch.object(check, "run", return_value={"passed": True}) as run,
            patch("builtins.print"),
        ):
            self.assertEqual(check.main(), 0)
        self.assertTrue(run.call_args.kwargs["preflight_only"])

    def test_terminal_completion_race_requires_result_and_exact_event(self):
        self.insert_run()
        result = {
            "action": "json-check",
            "profile": "reference-linux",
            "runtime_handler": "io.containerd.kata.v2",
            "program_sha256": "f" * 64,
            "cleaned_up": True,
            "exit_code": 0,
        }
        with closing(sqlite3.connect(self.db_path)) as db, db:
            db.execute(
                "UPDATE runs SET status='completed',finished=?,result=? WHERE id=?",
                (time.time(), json.dumps(result), RUN),
            )
            db.execute(
                "INSERT INTO events VALUES('run_finished',?,?)", (RUN, "completed")
            )
        row = check.read_exact_run(self.db_path, SESSION, RUN)
        kwargs = {
            "session": SESSION,
            "run_id": RUN,
            "resource": "prism-m23-run-" + RUN,
            "token": "f" * 32,
            "program_sha256": "f" * 64,
        }
        self.assertTrue(check.terminal_event_exists(self.db_path, RUN, "completed"))
        self.assertTrue(check.terminal_race_proven(row, event_exists=True, **kwargs))
        self.assertFalse(check.terminal_race_proven(row, event_exists=False, **kwargs))
        self.assertFalse(
            check.terminal_race_proven(
                row, event_exists=True, **{**kwargs, "token": "0" * 32}
            )
        )
        result["cleaned_up"] = False
        row["result"] = json.dumps(result)
        self.assertFalse(check.terminal_race_proven(row, event_exists=True, **kwargs))

    def test_cleanup_error_cannot_report_passed(self):
        report = {
            "outcome": "passed",
            "checks": {"service_restored": True},
            "error_kind": "ClientCloseError",
        }
        check.finalize_report(report)
        self.assertFalse(report["passed"])
        self.assertEqual(report["outcome"], "failed")


if __name__ == "__main__":
    unittest.main()
