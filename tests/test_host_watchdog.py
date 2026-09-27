"""Host watchdog protocol and recovery, without a live systemd or Kata host."""

import json
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from prism.engine import EngineError
from prism.host_watchdog import (
    CLOSE_RESIDUAL_SECONDS,
    TAILNET_PROBE_SECONDS,
    HostWatchdog,
    WatchdogError,
    _group_processes,
    _kill_service,
    _tailnet_ready,
    _verified_close_residuals,
)


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
        self.probes = []
        self.now = [10.0]
        self.watchdog = HostWatchdog(
            self.db,
            runtime=self.runtime,
            kill_service=lambda: self.kills.append(True),
            clock=lambda: self.now[0],
            tailnet_probe=lambda: self.probes.append(True),
        )
        self.identity = {
            "run": self.run,
            "resource": self.resource,
            "token": self.token,
        }

    def dispatch(self, op, **extra):
        return self.watchdog.dispatch({"op": op, **self.identity, **extra}, 123, 0)

    def test_tailnet_probe_accepts_fixed_identity_and_both_shield_phases(self):
        status = {
            "BackendState": "Running",
            "Self": {
                "Online": True,
                "DNSName": "pilot.example.ts.net.",
                "TailscaleIPs": ["100.100.100.100"],
                "Tags": ["tag:prism-host"],
            },
        }
        prefs = {
            "ShieldsUp": True,
            "RunSSH": False,
            "RouteAll": False,
            "AdvertiseRoutes": [],
            "ExitNodeID": "",
            "ExitNodeIP": "",
        }

        def probe():
            outputs = [
                SimpleNamespace(stdout=json.dumps(item).encode())
                for item in (status, prefs)
            ]
            with patch(
                "prism.host_watchdog.subprocess.run", side_effect=outputs
            ) as run:
                _tailnet_ready("pilot.example.ts.net", "100.100.100.100")
            self.assertEqual(run.call_count, 2)
            self.assertTrue(
                all(
                    0 < call.kwargs["timeout"] <= TAILNET_PROBE_SECONDS
                    for call in run.call_args_list
                )
            )

        probe()
        prefs["ShieldsUp"] = False
        probe()
        status["BackendState"] = "Stopped"
        with self.assertRaises(WatchdogError):
            probe()
        status["BackendState"] = "Running"
        status["Self"]["TailscaleIPs"] = ["100.100.100.101"]
        with self.assertRaises(WatchdogError):
            probe()
        status["Self"]["TailscaleIPs"] = ["100.100.100.100"]
        prefs["RunSSH"] = True
        with self.assertRaises(WatchdogError):
            probe()

    def test_tailnet_probe_timeout_is_loss(self):
        with (
            patch(
                "prism.host_watchdog.subprocess.run",
                side_effect=subprocess.TimeoutExpired("tailscale", 1),
            ),
            self.assertRaises(WatchdogError),
        ):
            _tailnet_ready("pilot.example.ts.net", "100.100.100.100")

    def test_tailnet_probe_uses_remaining_shared_deadline(self):
        status = {
            "BackendState": "Running",
            "Self": {
                "Online": True,
                "DNSName": "pilot.example.ts.net.",
                "TailscaleIPs": ["100.100.100.100"],
                "Tags": ["tag:prism-host"],
            },
        }
        prefs = {
            "ShieldsUp": False,
            "RunSSH": False,
            "RouteAll": False,
            "AdvertiseRoutes": [],
            "ExitNodeID": "",
            "ExitNodeIP": "",
        }
        now = [10.0]
        timeouts = []

        def completed(argv, **kwargs):
            timeouts.append(kwargs["timeout"])
            now[0] += 0.35
            data = status if argv[1] == "status" else prefs
            return SimpleNamespace(stdout=json.dumps(data).encode())

        with patch("prism.host_watchdog.subprocess.run", side_effect=completed):
            _tailnet_ready(
                "pilot.example.ts.net", "100.100.100.100", clock=lambda: now[0]
            )
        self.assertEqual(len(timeouts), 2)
        self.assertAlmostEqual(timeouts[0], TAILNET_PROBE_SECONDS)
        self.assertAlmostEqual(timeouts[1], TAILNET_PROBE_SECONDS - 0.35)

    def test_tailnet_probe_exhaustion_skips_second_command(self):
        now = [10.0]

        def delayed(_argv, **_kwargs):
            now[0] += TAILNET_PROBE_SECONDS
            return SimpleNamespace(stdout=b"{}")

        with (
            patch("prism.host_watchdog.subprocess.run", side_effect=delayed) as run,
            self.assertRaisesRegex(WatchdogError, "deadline elapsed"),
        ):
            _tailnet_ready(
                "pilot.example.ts.net", "100.100.100.100", clock=lambda: now[0]
            )
        self.assertEqual(run.call_count, 1)

    def test_tailnet_loss_without_active_run_stops_service(self):
        self.db.unlink()
        self.watchdog.startup()
        self.assertEqual(len(self.probes), 1)
        self.watchdog.tailnet_probe = lambda: (_ for _ in ()).throw(
            WatchdogError("BackendState Stopped")
        )
        self.now[0] += 1.1
        with self.assertRaisesRegex(WatchdogError, "tailnet state was lost"):
            self.watchdog.tick()
        self.assertEqual(self.kills, [True])
        self.assertTrue(self.watchdog.blocked)
        with self.assertRaises(WatchdogError):
            self.watchdog.dispatch({"op": "health"}, 123, 0)

    def test_tailnet_loss_on_startup_stops_service(self):
        self.db.unlink()
        self.watchdog.tailnet_probe = lambda: (_ for _ in ()).throw(
            WatchdogError("BackendState Stopped")
        )
        with self.assertRaisesRegex(WatchdogError, "tailnet state was lost"):
            self.watchdog.startup()
        self.assertEqual(self.kills, [True])
        self.assertTrue(self.watchdog.blocked)

    def test_tailnet_loss_with_active_run_stops_and_cleans_exact_resource(self):
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
            self.watchdog.tailnet_probe = lambda: (_ for _ in ()).throw(
                WatchdogError("down")
            )
            with self.assertRaises(WatchdogError):
                self.watchdog.tick()
        self.assertEqual(self.kills, [True])
        self.assertEqual(self.runtime.cleaned, [(self.resource, self.token)] * 3)
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(
                db.execute("SELECT status,result FROM runs").fetchone(),
                ("uncertain", None),
            )

    def test_tailnet_stop_failure_reports_fixed_stage_and_category(self):
        self.db.unlink()
        self.watchdog.kill_service = lambda: (_ for _ in ()).throw(
            WatchdogError("Residual close helper identity remained unavailable")
        )
        self.watchdog.tailnet_probe = lambda: (_ for _ in ()).throw(
            WatchdogError("down")
        )
        with self.assertRaisesRegex(
            WatchdogError,
            r"service termination unverified \(category=close-helper-unsettled\)",
        ):
            self.watchdog.tick()
        self.assertTrue(self.watchdog.blocked)
        self.assertEqual(self.runtime.cleaned, [])

    def test_tailnet_stage_error_does_not_echo_untrusted_exception_text(self):
        self.db.unlink()
        self.watchdog.kill_service = lambda: (_ for _ in ()).throw(
            OSError("private token /secret")
        )
        self.watchdog.tailnet_probe = lambda: (_ for _ in ()).throw(
            WatchdogError("down")
        )
        with self.assertRaisesRegex(
            WatchdogError, r"service termination unverified \(category=os\)"
        ) as captured:
            self.watchdog.tick()
        self.assertNotIn("/secret", str(captured.exception))

    def test_tailnet_cleanup_failure_reports_runtime_category_without_details(self):
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
        self.runtime.reconcile = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            EngineError("private resource path /secret")
        )
        self.watchdog.tailnet_probe = lambda: (_ for _ in ()).throw(
            WatchdogError("down")
        )
        with self.assertRaisesRegex(
            WatchdogError,
            r"exact resource cleanup unverified \(category=runtime\)",
        ) as captured:
            self.watchdog.tick()
        self.assertEqual(self.kills, [True])
        self.assertTrue(self.watchdog.blocked)
        self.assertNotIn("/secret", str(captured.exception))

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

    def test_close_helper_control_pid_race_retries_exact_identity(self):
        now = [10.0]
        pauses = []

        def sleep(seconds):
            pauses.append(seconds)
            now[0] += seconds

        helper = (
            b"/usr/bin/python3\0/usr/local/libexec/prism-identity-service-lifecycle.py\0"
            b"close\0--hostname\0pilot.example.ts.net\0--bind-host\0"
            b"100.100.100.100\0"
        )
        with (
            patch("prism.host_watchdog._group_processes", return_value={222: 456}),
            patch(
                "prism.host_watchdog.subprocess.run",
                side_effect=[
                    SimpleNamespace(stdout=b"0\n"),
                    SimpleNamespace(stdout=b"222\n"),
                ],
            ) as command,
            patch("prism.host_watchdog._start_time", return_value=456),
            patch.object(Path, "read_bytes", return_value=helper),
        ):
            _verified_close_residuals(
                "/system.slice/prism-identity-service.service",
                clock=lambda: now[0],
                sleep=sleep,
            )
        self.assertEqual(command.call_count, 2)
        self.assertEqual(pauses, [0.1])

    def test_close_helper_unsettled_control_pid_is_bounded_fail_stop(self):
        now = [10.0]

        def sleep(seconds):
            now[0] += seconds

        with (
            patch("prism.host_watchdog._group_processes", return_value={222: 456}),
            patch(
                "prism.host_watchdog.subprocess.run",
                return_value=SimpleNamespace(stdout=b"0\n"),
            ) as command,
            self.assertRaisesRegex(WatchdogError, "identity remained unavailable"),
        ):
            _verified_close_residuals(
                "/system.slice/prism-identity-service.service",
                clock=lambda: now[0],
                sleep=sleep,
            )
        self.assertLessEqual(now[0] - 10.0, CLOSE_RESIDUAL_SECONDS + 0.1)
        self.assertLessEqual(command.call_count, 23)

    def test_close_helper_cgroup_drain_race_is_safe(self):
        now = [10.0]
        with (
            patch(
                "prism.host_watchdog._group_processes", side_effect=[{222: 456}, {}]
            ) as processes,
            patch(
                "prism.host_watchdog.subprocess.run",
                return_value=SimpleNamespace(stdout=b"0\n"),
            ),
        ):
            _verified_close_residuals(
                "/system.slice/prism-identity-service.service",
                clock=lambda: now[0],
                sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
            )
        self.assertEqual(processes.call_count, 2)

    def test_close_helper_exit_during_second_starttime_retries_group(self):
        now = [10.0]
        with (
            patch(
                "prism.host_watchdog._group_processes", side_effect=[{222: 456}, {}]
            ) as processes,
            patch(
                "prism.host_watchdog.subprocess.run",
                return_value=SimpleNamespace(stdout=b"222\n"),
            ),
            patch(
                "prism.host_watchdog._start_time",
                side_effect=WatchdogError("Process has exited"),
            ),
        ):
            _verified_close_residuals(
                "/system.slice/prism-identity-service.service",
                clock=lambda: now[0],
                sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
            )
        self.assertEqual(processes.call_count, 2)
        self.assertEqual(now[0], 10.1)

    def test_close_helper_empty_cmdline_after_exit_retries_group(self):
        now = [10.0]
        with (
            patch(
                "prism.host_watchdog._group_processes", side_effect=[{222: 456}, {}]
            ) as processes,
            patch(
                "prism.host_watchdog.subprocess.run",
                return_value=SimpleNamespace(stdout=b"222\n"),
            ),
            patch(
                "prism.host_watchdog._start_time",
                side_effect=[456, WatchdogError("Process has exited")],
            ),
            patch.object(Path, "read_bytes", return_value=b""),
        ):
            _verified_close_residuals(
                "/system.slice/prism-identity-service.service",
                clock=lambda: now[0],
                sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
            )
        self.assertEqual(processes.call_count, 2)
        self.assertEqual(now[0], 10.1)

    def test_close_helper_rejects_unexpected_survivor_without_retry(self):
        now = [10.0]
        with (
            patch("prism.host_watchdog._group_processes", return_value={222: 456}),
            patch(
                "prism.host_watchdog.subprocess.run",
                return_value=SimpleNamespace(stdout=b"222\n"),
            ) as command,
            patch("prism.host_watchdog._start_time", return_value=456),
            patch.object(Path, "read_bytes", return_value=b"/bin/sh\0"),
            self.assertRaisesRegex(WatchdogError, "not the fixed close helper"),
        ):
            _verified_close_residuals(
                "/system.slice/prism-identity-service.service",
                clock=lambda: now[0],
                sleep=lambda _: self.fail("retried unknown process"),
            )
        self.assertEqual(command.call_count, 1)

    def test_group_inspection_never_drops_live_unknown_process(self):
        def read_text(path):
            return "123\n" if path.name == "cgroup.procs" else "123 (test) S 1\n"

        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "rglob", return_value=[Path("/fake/cgroup.procs")]),
            patch.object(Path, "read_text", autospec=True, side_effect=read_text),
            patch(
                "prism.host_watchdog._start_time", side_effect=WatchdogError("unknown")
            ),
            self.assertRaisesRegex(WatchdogError, "process identity is unavailable"),
        ):
            _group_processes("/system.slice/prism-identity-service.service")

    def test_inactive_service_accepts_only_verified_close_residuals(self):
        group = "/system.slice/prism-identity-service.service"
        with (
            patch(
                "prism.host_watchdog.subprocess.run",
                return_value=SimpleNamespace(stdout=b"inactive\n"),
            ),
            patch("prism.host_watchdog._service_cgroup", return_value=group),
            patch("prism.host_watchdog._group_drained", return_value=False),
            patch("prism.host_watchdog._verified_close_residuals") as verify,
        ):
            _kill_service()
        verify.assert_called_once_with(group)

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
