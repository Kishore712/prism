"""Host watchdog protocol and recovery, without a live systemd or Kata host."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from prism.host_watchdog import HostWatchdog, WatchdogError, _kill_service


class Runtime:
    def __init__(self):
        self.resources = []
        self.cleaned = []
        self.fail = False

    def reconcile(self, resource, token, *, settle_seconds):
        if self.fail:
            raise WatchdogError("management failed")
        self.cleaned.append((resource, token))

    def all_resources(self):
        return self.resources

    def inspect_owned(self, resource, token):
        return None


class HostWatchdogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "runs.sqlite"
        self.run = "a" * 32
        self.token = "b" * 32
        self.resource = "prism-m23-run-" + self.run
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute(
                "CREATE TABLE runs (id TEXT, status TEXT, runtime_profile TEXT, runtime_resource TEXT, runtime_token TEXT, finished REAL, result TEXT, error TEXT)"
            )
            db.execute(
                "CREATE TABLE events (id INTEGER PRIMARY KEY, at REAL, kind TEXT, actor TEXT, resource TEXT, outcome TEXT)"
            )
            db.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)",
                (
                    self.run,
                    "running",
                    "reference-linux",
                    self.resource,
                    self.token,
                    None,
                    None,
                    None,
                ),
            )
        self.runtime = Runtime()
        self.kills = []
        self.now = [10.0]
        self.watchdog = HostWatchdog(
            self.db,
            runtime=self.runtime,
            kill_service=lambda: self.kills.append(True),
            clock=lambda: self.now[0],
        )
        self.identity = {
            "run": self.run,
            "resource": self.resource,
            "token": self.token,
        }

    def dispatch(self, op, **extra):
        return self.watchdog.dispatch({"op": op, **self.identity, **extra}, 123, 0)

    def test_registration_attach_renew_and_terminal_release(self):
        with (
            patch(
                "prism.host_watchdog._cgroup",
                return_value="/system.slice/prism-identity-service.service",
            ),
            patch(
                "prism.host_watchdog._service_cgroup",
                return_value="/system.slice/prism-identity-service.service",
            ),
            patch("prism.host_watchdog._start_time", return_value=456),
        ):
            self.dispatch("register")
            self.assertEqual(self.watchdog.active["worker_pid"], None)
            with self.assertRaises(WatchdogError):
                self.dispatch("renew")
            self.dispatch("attach", pid=321)
            self.now[0] = 12.0
            self.dispatch("renew")
            self.assertEqual(self.watchdog.active["deadline"], 15.0)
            self.dispatch("finishing")
            with closing(sqlite3.connect(self.db)) as db, db:
                db.execute("UPDATE runs SET status='completed' WHERE id=?", (self.run,))
            self.dispatch("release")
            self.assertIsNone(self.watchdog.active)

    def test_unfinished_attached_worker_cannot_release_uncertain_run(self):
        with (
            patch(
                "prism.host_watchdog._cgroup",
                return_value="/system.slice/prism-identity-service.service",
            ),
            patch(
                "prism.host_watchdog._service_cgroup",
                return_value="/system.slice/prism-identity-service.service",
            ),
            patch("prism.host_watchdog._start_time", return_value=456),
        ):
            self.dispatch("register")
            self.dispatch("attach", pid=321)
            with closing(sqlite3.connect(self.db)) as db, db:
                db.execute("UPDATE runs SET status='uncertain'")
            with self.assertRaises(WatchdogError):
                self.dispatch("release")
        self.assertIsNotNone(self.watchdog.active)

    def test_wrong_token_and_unrelated_peer_are_denied(self):
        with (
            patch(
                "prism.host_watchdog._cgroup",
                return_value="/system.slice/prism-identity-service.service",
            ),
            patch(
                "prism.host_watchdog._service_cgroup",
                return_value="/system.slice/prism-identity-service.service",
            ),
            patch("prism.host_watchdog._start_time", return_value=456),
        ):
            with self.assertRaises(WatchdogError):
                self.watchdog.dispatch({"op": "register", **self.identity}, 123, 1000)
            with self.assertRaises(WatchdogError):
                self.dispatch("register", token="c" * 32)
            with self.assertRaises(WatchdogError):
                self.watchdog.dispatch(
                    {"op": "register", **self.identity, "command": "rm"}, 123, 0
                )
            self.assertIsNone(self.watchdog.active)

    def test_lease_loss_kills_service_then_reconciles_exact_identity(self):
        with (
            patch(
                "prism.host_watchdog._cgroup",
                return_value="/system.slice/prism-identity-service.service",
            ),
            patch(
                "prism.host_watchdog._service_cgroup",
                return_value="/system.slice/prism-identity-service.service",
            ),
            patch("prism.host_watchdog._start_time", return_value=456),
            patch("prism.host_watchdog.time.sleep"),
        ):
            self.dispatch("register")
            self.dispatch("attach", pid=321)
            self.now[0] = 14.0
            self.watchdog.tick()
        self.assertEqual(len(self.kills), 1)
        self.assertEqual(self.runtime.cleaned, [(self.resource, self.token)] * 3)
        with closing(sqlite3.connect(self.db)) as db, db:
            status, result = db.execute("SELECT status,result FROM runs").fetchone()
        self.assertEqual((status, result), ("uncertain", None))

    def test_management_failure_blocks_health(self):
        self.runtime.fail = True
        with patch("prism.host_watchdog.time.sleep"), self.assertRaises(WatchdogError):
            self.watchdog.startup()
        self.assertTrue(self.watchdog.blocked)
        with self.assertRaises(WatchdogError):
            self.watchdog.dispatch({"op": "health"}, 123, 0)

    def test_expiry_cleanup_failure_is_fail_stop(self):
        self.runtime.fail = True
        with (
            patch(
                "prism.host_watchdog._cgroup",
                return_value="/system.slice/prism-identity-service.service",
            ),
            patch(
                "prism.host_watchdog._service_cgroup",
                return_value="/system.slice/prism-identity-service.service",
            ),
            patch("prism.host_watchdog._start_time", return_value=456),
        ):
            self.dispatch("register")
            self.dispatch("attach", pid=321)
            self.now[0] = 14.0
            with self.assertRaises(WatchdogError):
                self.watchdog.tick()
        self.assertTrue(self.watchdog.blocked)
        self.assertEqual(len(self.kills), 1)

    def test_fresh_host_without_database_answers_health(self):
        self.db.unlink()
        self.watchdog.startup()
        self.assertEqual(self.kills, [])
        self.watchdog.dispatch({"op": "health"}, 123, 0)

    def test_missing_service_pid_is_reconciled(self):
        with (
            patch(
                "prism.host_watchdog._cgroup",
                return_value="/system.slice/prism-identity-service.service",
            ),
            patch(
                "prism.host_watchdog._service_cgroup",
                return_value="/system.slice/prism-identity-service.service",
            ),
            patch(
                "prism.host_watchdog._start_time",
                side_effect=[456, WatchdogError("gone")],
            ),
            patch("prism.host_watchdog.time.sleep"),
        ):
            self.dispatch("register")
            self.watchdog.tick()
        self.assertEqual(len(self.kills), 1)
        self.assertEqual(self.runtime.cleaned, [(self.resource, self.token)] * 3)

    def test_worker_disappears_while_service_renews(self):
        worker_checks = [789, WatchdogError("gone"), WatchdogError("gone")]

        def process_start(pid):
            return 456 if pid == 123 else worker_checks.pop(0)

        with (
            patch(
                "prism.host_watchdog._cgroup",
                return_value="/system.slice/prism-identity-service.service",
            ),
            patch(
                "prism.host_watchdog._service_cgroup",
                return_value="/system.slice/prism-identity-service.service",
            ),
            patch("prism.host_watchdog._start_time", side_effect=process_start),
            patch("prism.host_watchdog.time.sleep"),
        ):
            self.dispatch("register")
            self.dispatch("attach", pid=321)
            self.watchdog.tick()
            self.now[0] = 12.6
            self.watchdog.tick()
        self.assertEqual(len(self.kills), 1)
        self.assertEqual(len(self.runtime.cleaned), 3)

    def test_finishing_worker_can_exit_before_terminal_release(self):
        worker_checks = [789, WatchdogError("gone")]

        def process_start(pid):
            return 456 if pid == 123 else worker_checks.pop(0)

        with (
            patch(
                "prism.host_watchdog._cgroup",
                return_value="/system.slice/prism-identity-service.service",
            ),
            patch(
                "prism.host_watchdog._service_cgroup",
                return_value="/system.slice/prism-identity-service.service",
            ),
            patch("prism.host_watchdog._start_time", side_effect=process_start),
        ):
            self.dispatch("register")
            self.dispatch("attach", pid=321)
            self.dispatch("finishing")
            self.now[0] = 11.0
            self.watchdog.tick()
        self.assertEqual(self.kills, [])
        self.assertIsNotNone(self.watchdog.active)

    def test_terminal_row_with_resource_is_revoked_on_lease_loss(self):
        with (
            patch(
                "prism.host_watchdog._cgroup",
                return_value="/system.slice/prism-identity-service.service",
            ),
            patch(
                "prism.host_watchdog._service_cgroup",
                return_value="/system.slice/prism-identity-service.service",
            ),
            patch("prism.host_watchdog._start_time", return_value=456),
            patch("prism.host_watchdog.time.sleep"),
            patch.object(self.runtime, "inspect_owned", return_value={"Id": "owned"}),
        ):
            self.dispatch("register")
            self.dispatch("attach", pid=321)
            with closing(sqlite3.connect(self.db)) as db, db:
                db.execute("UPDATE runs SET status='completed',result='{}'")
            self.now[0] = 14.0
            self.watchdog.tick()
        with closing(sqlite3.connect(self.db)) as db, db:
            self.assertEqual(
                db.execute("SELECT status,result FROM runs").fetchone(),
                ("uncertain", None),
            )
        self.assertEqual(len(self.kills), 1)

    def test_startup_refuses_unrelated_resource_identity(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute(
                "UPDATE runs SET runtime_resource='prism-m23-run-' || ?", ("c" * 32,)
            )
        with self.assertRaises(WatchdogError):
            self.watchdog.startup()
        self.assertTrue(self.watchdog.blocked)
        self.assertEqual(self.runtime.cleaned, [])

    def test_restart_reconciles_terminal_row_with_exact_resource(self):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE runs SET status='completed',result='{}'")
        self.runtime.resources = [self.resource]

        def reconcile(resource, token, *, settle_seconds):
            self.assertEqual((resource, token), (self.resource, self.token))
            self.runtime.cleaned.append((resource, token))
            self.runtime.resources.clear()

        self.runtime.reconcile = reconcile
        with patch("prism.host_watchdog.time.sleep"):
            self.watchdog.startup()
        self.assertEqual(len(self.kills), 1)
        self.assertEqual(len(self.runtime.cleaned), 3)
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(
                db.execute("SELECT status,result FROM runs").fetchone(),
                ("uncertain", None),
            )
            event = db.execute(
                "SELECT kind,actor,resource,outcome FROM events ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(
            event, ("run_reclassified", "host-watchdog", self.run, "uncertain")
        )

    def test_failed_service_without_cgroup_is_safe_only_if_drained(self):
        from types import SimpleNamespace

        shown = SimpleNamespace(stdout=b"failed\n")
        with (
            patch("prism.host_watchdog.subprocess.run", return_value=shown) as command,
            patch("prism.host_watchdog._service_cgroup", return_value=None),
            patch("prism.host_watchdog._group_drained", return_value=True),
        ):
            _kill_service()
        self.assertEqual(command.call_count, 1)
        with (
            patch("prism.host_watchdog.subprocess.run", return_value=shown),
            patch("prism.host_watchdog._service_cgroup", return_value=None),
            patch("prism.host_watchdog._group_drained", return_value=False),
            self.assertRaises(WatchdogError),
        ):
            _kill_service()

    def test_abort_catches_resource_created_after_first_absent_check(self):
        seen = []

        def reconcile(resource, token, *, settle_seconds):
            self.assertEqual((resource, token), (self.resource, self.token))
            seen.append(len(seen))
            if len(seen) == 2:
                self.runtime.resources.append(resource)
            if self.runtime.resources:
                self.runtime.resources.clear()

        self.runtime.reconcile = reconcile
        with (
            patch(
                "prism.host_watchdog._cgroup",
                return_value="/system.slice/prism-identity-service.service",
            ),
            patch(
                "prism.host_watchdog._service_cgroup",
                return_value="/system.slice/prism-identity-service.service",
            ),
            patch("prism.host_watchdog._start_time", return_value=456),
            patch("prism.host_watchdog.time.sleep"),
        ):
            self.dispatch("register")
            self.dispatch("attach", pid=321)
            self.dispatch("abort")
        self.assertEqual(seen, [0, 1, 2])
        self.assertEqual(len(self.kills), 1)
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(
                db.execute("SELECT status,result FROM runs").fetchone(),
                ("uncertain", None),
            )


if __name__ == "__main__":
    unittest.main()
