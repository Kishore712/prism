"""Local boundaries for the test-only active Jobs/watchdog rehearsal."""

import importlib.util
import sqlite3
import tempfile
import types
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/m2-watchdog-socket-active-check.py"
SPEC = importlib.util.spec_from_file_location("watchdog_socket_active_check", SCRIPT)
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class WatchdogSocketActiveCheckTests(unittest.TestCase):
    def test_shield_mode_requires_observed_transition(self):
        self.assertIn("serving_shields_down_at_fault", CHECK.SHIELD_REQUIRED)
        self.assertIn("watchdog_close_raised_shields", CHECK.SHIELD_REQUIRED)
        self.assertNotIn("maintenance_shields_up", CHECK.SHIELD_REQUIRED)
        self.assertEqual(len(CHECK.SHIELD_REQUIRED), len(CHECK.REQUIRED) + 1)

    def test_stopped_service_without_shields_gets_fallback_guard(self):
        base = types.SimpleNamespace(
            SERVICE="prism-test.service",
            show=Mock(return_value="inactive"),
            shield_state=Mock(return_value=False),
            command=Mock(),
        )
        self.assertTrue(CHECK.guard_stopped_service(base, faulted=True))
        base.command.assert_called_once_with(
            "/usr/bin/tailscale", "set", "--shields-up=true"
        )

    def test_active_service_or_existing_shield_needs_no_fallback(self):
        base = types.SimpleNamespace(
            SERVICE="prism-test.service",
            show=Mock(return_value="active"),
            shield_state=Mock(return_value=False),
            command=Mock(),
        )
        self.assertFalse(CHECK.guard_stopped_service(base, faulted=False))
        base.shield_state.assert_not_called()
        base.show.return_value = "inactive"
        base.shield_state.return_value = True
        self.assertFalse(CHECK.guard_stopped_service(base, faulted=True))
        base.command.assert_not_called()

    def test_unsupported_host_stops_before_remote_or_live_state(self):
        with (
            patch.object(CHECK.platform, "system", return_value="Darwin"),
            patch.object(
                CHECK, "parent_module", side_effect=AssertionError("host read")
            ),
        ):
            result = CHECK.run_check(Path("/missing"))
        self.assertFalse(result["passed"])
        self.assertEqual(result["error_kind"], "UnsupportedHost")

    def test_only_pinned_fixture_gets_fixed_guest_delay(self):
        original = (ROOT / CHECK.FIXTURE_PATH).read_text(encoding="utf-8")
        variant = CHECK.delayed_fixture(original)
        self.assertEqual(variant.count("time.sleep(20)"), 1)
        self.assertEqual(variant.count("import time"), 1)
        compile(variant, "json_check.py", "exec")
        with self.assertRaises(RuntimeError):
            CHECK.delayed_fixture(original + "# changed\n")

    def test_status_updates_are_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            CHECK.write_status(path, {"chat": "one"})
            self.assertEqual(CHECK.status_file(path), {"chat": "one"})
            CHECK.write_status(path, {"chat": "one", "submitted": True})
            self.assertEqual(
                CHECK.status_file(path), {"chat": "one", "submitted": True}
            )
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_children_include_background_threads(self):
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory) / "42" / "task"
            for thread, children in (("42", ""), ("47", "99 101\n")):
                entry = task / thread
                entry.mkdir(parents=True)
                (entry / "children").write_text(children, encoding="ascii")
            self.assertEqual(
                CHECK.children_all_threads(42, proc_root=Path(directory)), [99, 101]
            )

    def test_only_empty_test_project_can_be_undone(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            with closing(sqlite3.connect(path)) as db:
                db.execute(
                    "CREATE TABLE owner_projects(id TEXT PRIMARY KEY,owner TEXT)"
                )
                db.execute("CREATE TABLE owner_revisions(project TEXT)")
                db.execute("CREATE TABLE owner_chats(project TEXT)")
                db.execute(
                    "INSERT INTO owner_projects VALUES(?,?)",
                    (CHECK.LAB_PROJECT, CHECK.LAB_OWNER),
                )
                db.commit()
            parent = types.SimpleNamespace(DB=path)
            with closing(sqlite3.connect(path)) as db:
                db.execute("INSERT INTO owner_chats VALUES(?)", (CHECK.LAB_PROJECT,))
                db.commit()
            with self.assertRaises(RuntimeError):
                CHECK.remove_empty_lab_project(parent)
            with closing(sqlite3.connect(path)) as db:
                db.execute("DELETE FROM owner_chats")
                db.commit()
            CHECK.remove_empty_lab_project(parent)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(
                    db.execute("SELECT count(*) FROM owner_projects").fetchone()[0], 0
                )


if __name__ == "__main__":
    unittest.main()
