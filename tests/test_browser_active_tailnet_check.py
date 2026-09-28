"""Offline scope and run selection checks for the browser active-fault observer."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
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

    def test_resolver_retargets_only_verified_v8_baseline(self):
        old = check.OLD_TRANSFER_SHA256
        resolver = types.SimpleNamespace(
            TRANSFER=old,
            SOURCE=check.OLD_SOURCE_SHA256,
            LIFECYCLE_SHA256=check.OLD_LIFECYCLE_SHA256,
            UPDATE=f"/var/lib/prism/identity-pilot/service-updates/{old}/installed.json",
            RELEASE=f"/var/lib/prism/identity-pilot/app-releases/service-{old}",
        )
        release = Path("/var/lib/prism/identity-pilot/app-releases") / (
            "service-" + check.RELEASE_TRANSFER_SHA256
        )
        check.configure_resolver_pins(resolver, release)
        self.assertEqual(resolver.TRANSFER, check.RELEASE_TRANSFER_SHA256)
        self.assertEqual(resolver.SOURCE, check.RELEASE_SOURCE_SHA256)
        self.assertEqual(resolver.LIFECYCLE_SHA256, check.RELEASE_LIFECYCLE_SHA256)
        self.assertEqual(resolver.RELEASE, str(release))
        with self.assertRaises(RuntimeError):
            check.configure_resolver_pins(resolver, release)

    def test_resolver_requires_exact_inspect_and_resolve_responses(self):
        answers = [
            {"run": RUN, "ready": True, "action": "inspect", "changed": False},
            {
                "run": RUN,
                "ready": True,
                "action": "resolve",
                "changed": True,
                "status": "failed",
                "execution_outcome": "unknown",
            },
        ]
        resolver = types.SimpleNamespace(execute=lambda *_: answers.pop(0))
        self.assertTrue(check.inspect_and_resolve_current(resolver, RUN))
        resolver.execute = lambda *_: {"run": RUN, "ready": False}
        self.assertFalse(check.inspect_and_resolve_current(resolver, RUN))

    def test_watchdog_stop_nonzero_requires_drained_unit_before_recovery(self):
        calls = []

        class Base:
            SERVICE = "service"
            WATCHDOG = "watchdog"

            def command(self, *args, **_kwargs):
                calls.append(args)
                if args == ("/usr/bin/systemctl", "stop", self.WATCHDOG):
                    raise subprocess.CalledProcessError(1, args)

            def show(self, _unit, field):
                return {"ActiveState": "failed", "MainPID": "0", "ControlPID": "0"}[
                    field
                ]

            def wait_for(self, predicate, _seconds):
                return predicate()

            def service_active(self, unit):
                return unit in ("tailscaled.service", self.WATCHDOG)

            def shield_state(self):
                return True

        base = Base()
        observed = types.SimpleNamespace(cgroup_tree_drained=lambda _unit: True)
        active = types.SimpleNamespace(
            offline_tailnet_prefs=lambda _base: (True, False),
            tailnet_online=lambda _base: True,
            wait_watchdog_socket=lambda *_: True,
            force_tailnet_closed=lambda *_: self.fail("unexpected fallback"),
        )
        check.recover_tailnet_after_close_failure(base, observed, active, object())
        self.assertIn(("/usr/bin/systemctl", "start", base.WATCHDOG), calls)
        observed.cgroup_tree_drained = lambda unit: unit != base.WATCHDOG
        with self.assertRaises(RuntimeError):
            check.recover_tailnet_after_close_failure(base, observed, active, object())
        self.assertEqual(calls[-1], ("/usr/bin/systemctl", "stop", base.WATCHDOG))

    def test_restart_gate_refuses_resource_or_process_reappearance(self):
        base = types.SimpleNamespace(SERVICE="service")
        observed = types.SimpleNamespace(cgroup_tree_drained=lambda _: True)
        runtime = types.SimpleNamespace(
            all_resources=list, inspect_owned=lambda *_: None
        )
        active = types.SimpleNamespace(same_container_candidates=lambda _: [])
        args = (
            base,
            observed,
            runtime,
            active,
            "resource",
            "token",
            {"container_id": "c"},
        )
        self.assertTrue(check.ready_to_restart_service(*args))
        runtime.all_resources = lambda: ["unexpected"]
        self.assertFalse(check.ready_to_restart_service(*args))
        runtime.all_resources = list
        runtime.inspect_owned = lambda *_: {"State": {"Status": "running"}}
        self.assertFalse(check.ready_to_restart_service(*args))
        runtime.inspect_owned = lambda *_: None
        observed.cgroup_tree_drained = lambda _: False
        self.assertFalse(check.ready_to_restart_service(*args))
        observed.cgroup_tree_drained = lambda _: True
        active.same_container_candidates = lambda _: [123]
        self.assertFalse(check.ready_to_restart_service(*args))

    def test_only_known_pre_running_inspect_race_is_temporary(self):
        class EngineError(Exception):
            pass

        reference = types.SimpleNamespace(EngineError=EngineError)
        runtime = types.SimpleNamespace(
            inspect_owned=lambda *_: {"State": {"Status": "running"}}
        )
        self.assertEqual(
            check.inspect_during_creation(runtime, reference, "resource", "token"),
            {"State": {"Status": "running"}},
        )

        def known_race(*_):
            raise EngineError("The named reference resource could not be inspected.")

        runtime.inspect_owned = known_race
        self.assertIsNone(
            check.inspect_during_creation(runtime, reference, "resource", "token")
        )

        def other_error(*_):
            raise EngineError("The reference runtime is unavailable.")

        runtime.inspect_owned = other_error
        with self.assertRaises(EngineError):
            check.inspect_during_creation(runtime, reference, "resource", "token")

    def test_primary_failure_keeps_original_stage_with_bounded_origin(self):
        def observe_job_termination():
            raise RuntimeError("secret value and private path must not be reported")

        try:
            observe_job_termination()
        except RuntimeError as exc:
            report = {"phase": "observe_fail_closed"}
            check.record_primary_failure(report, exc)
        report["phase"] = "recovery"
        self.assertEqual(report["failure_stage"], "observe_fail_closed")
        self.assertEqual(report["failure_origin"], "observe_job_termination")
        self.assertEqual(report["error_kind"], "RuntimeError")
        self.assertNotIn("secret", str(report))
        self.assertNotIn("private path", str(report))

        def unknown_function():
            raise RuntimeError("untrusted function name")

        try:
            unknown_function()
        except RuntimeError as exc:
            self.assertEqual(check.fixed_failure_origin(exc), "other")

    def test_closure_failure_still_forces_tailnet_closed(self):
        calls = []
        active = types.SimpleNamespace(
            fail_closed_after_unstable_recovery=lambda *_args, **_kwargs: (
                calls.append("fallback"),
                (_ for _ in ()).throw(RuntimeError("close failed")),
            ),
            force_tailnet_closed=lambda *_: calls.append("force"),
        )
        report = {"outcome": "passed", "checks": {"service_restored": True}}
        observed = types.SimpleNamespace(cgroup_tree_drained=lambda _: True)
        with patch.object(check, "tailnet_daemon_absent", return_value=False):
            check.close_after_recovery_error(
                types.SimpleNamespace(SERVICE="service"), observed, active, report
            )
        self.assertEqual(calls, ["fallback", "force"])
        self.assertEqual(report["closure_error_kind"], "RuntimeError")
        check.finalize_report(report)
        self.assertFalse(report["passed"])

        def force_fails(_base):
            raise OSError("force failed")

        active.force_tailnet_closed = force_fails
        report = {"outcome": "passed", "checks": {"service_restored": True}}
        with patch.object(check, "tailnet_daemon_absent", return_value=False):
            check.close_after_recovery_error(
                types.SimpleNamespace(SERVICE="service"), observed, active, report
            )
        self.assertEqual(report["emergency_tailnet_close_error_kind"], "OSError")
        check.finalize_report(report)
        self.assertFalse(report["passed"])

    def test_already_dead_tailnet_requires_unit_cgroup_and_interface_absence(self):
        root = Path(self.temporary.name) / "net"
        root.mkdir()
        unit = {"ActiveState": "failed", "MainPID": "0", "ControlPID": "0"}
        base = types.SimpleNamespace(show=lambda _unit, field: unit[field])
        observed = types.SimpleNamespace(cgroup_tree_drained=lambda _unit: True)
        self.assertTrue(check.tailnet_daemon_absent(base, observed, net_root=root))
        (root / "tailscale0").symlink_to("missing-interface")
        self.assertFalse(check.tailnet_daemon_absent(base, observed, net_root=root))
        (root / "tailscale0").unlink()
        unit["MainPID"] = "123"
        self.assertFalse(check.tailnet_daemon_absent(base, observed, net_root=root))
        unit["MainPID"] = "0"
        observed.cgroup_tree_drained = lambda _unit: False
        self.assertFalse(check.tailnet_daemon_absent(base, observed, net_root=root))

    def test_verified_dead_daemon_skips_old_pid_dependent_helper(self):
        active = types.SimpleNamespace(
            force_tailnet_closed=lambda *_: self.fail("old helper must not run")
        )
        with patch.object(check, "tailnet_daemon_absent", return_value=True):
            self.assertEqual(
                check.ensure_tailnet_closed(object(), object(), active),
                "daemon_absent_verified",
            )
        calls = []
        active.force_tailnet_closed = lambda *_: calls.append("strict")
        with patch.object(
            check, "tailnet_daemon_absent", side_effect=OSError("state unavailable")
        ):
            self.assertEqual(
                check.ensure_tailnet_closed(object(), object(), active),
                "pinned_helper_confirmed",
            )
        self.assertEqual(calls, ["strict"])


if __name__ == "__main__":
    unittest.main()
