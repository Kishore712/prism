"""Offline scope and run selection checks for the browser active-fault observer."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import stat
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
            RESOLVER=check.OLD_RESOLVER,
            RESOLVER_SHA256=check.OLD_RESOLVER_SHA256,
        )
        current = Path("/var/lib/prism/identity-pilot/app-releases") / (
            "service-" + check.RELEASE_TRANSFER_SHA256
        )
        check.configure_release_pins(helper, current)
        self.assertEqual(helper.TRANSFER_SHA256, check.RELEASE_TRANSFER_SHA256)
        self.assertEqual(helper.SOURCE_SHA256, check.RELEASE_SOURCE_SHA256)
        self.assertEqual(helper.WATCHDOG_SHA256, check.RELEASE_WATCHDOG_SHA256)
        self.assertEqual(helper.LIFECYCLE_SHA256, check.RELEASE_LIFECYCLE_SHA256)
        self.assertEqual(helper.RESOLVER, check.RELEASE_RESOLVER)
        self.assertEqual(helper.RESOLVER_SHA256, check.RELEASE_RESOLVER_SHA256)
        with self.assertRaises(RuntimeError):
            check.configure_release_pins(helper, current)

    def test_v9_pins_match_files_and_old_resolver_identity_is_required(self):
        root = SCRIPT.parents[1]
        self.assertEqual(
            hashlib.sha256(
                (root / "scripts/gcp/identity-service-lifecycle.py").read_bytes()
            ).hexdigest(),
            check.RELEASE_LIFECYCLE_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(
                (root / "scripts/gcp/identity-uncertain-resolution-v9.py").read_bytes()
            ).hexdigest(),
            check.RELEASE_RESOLVER_SHA256,
        )
        helper = types.SimpleNamespace(
            TRANSFER_SHA256=check.OLD_TRANSFER_SHA256,
            SOURCE_SHA256=check.OLD_SOURCE_SHA256,
            WATCHDOG_SHA256=check.OLD_WATCHDOG_SHA256,
            LIFECYCLE_SHA256=check.OLD_LIFECYCLE_SHA256,
            RESOLVER=check.OLD_RESOLVER,
            RESOLVER_SHA256="0" * 64,
        )
        release = Path("/var/lib/prism/identity-pilot/app-releases") / (
            "service-" + check.RELEASE_TRANSFER_SHA256
        )
        with self.assertRaisesRegex(RuntimeError, "baseline differs"):
            check.configure_release_pins(helper, release)
        self.assertEqual(helper.RESOLVER, check.OLD_RESOLVER)

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

    def test_resolver_requires_exact_current_v9_pins(self):
        transfer = check.RELEASE_TRANSFER_SHA256
        resolver = types.SimpleNamespace(
            TRANSFER=transfer,
            SOURCE=check.RELEASE_SOURCE_SHA256,
            LIFECYCLE_SHA256=check.RELEASE_LIFECYCLE_SHA256,
            UPDATE=f"/var/lib/prism/identity-pilot/service-updates/{transfer}/installed.json",
            RELEASE=f"/var/lib/prism/identity-pilot/app-releases/service-{transfer}",
        )
        release = Path("/var/lib/prism/identity-pilot/app-releases") / (
            "service-" + check.RELEASE_TRANSFER_SHA256
        )
        check.configure_resolver_pins(resolver, release)
        self.assertEqual(resolver.TRANSFER, check.RELEASE_TRANSFER_SHA256)
        self.assertEqual(resolver.SOURCE, check.RELEASE_SOURCE_SHA256)
        self.assertEqual(resolver.LIFECYCLE_SHA256, check.RELEASE_LIFECYCLE_SHA256)
        self.assertEqual(resolver.RELEASE, str(release))
        resolver.TRANSFER = check.OLD_TRANSFER_SHA256
        with self.assertRaisesRegex(RuntimeError, "v9 resolver release pins differ"):
            check.configure_resolver_pins(resolver, release)

    def test_actual_v9_resolver_pins_are_accepted_without_mutation(self):
        path = SCRIPT.parents[1] / "scripts/gcp/identity-uncertain-resolution-v9.py"
        spec = importlib.util.spec_from_file_location("browser_current_v9", path)
        resolver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(resolver)
        release = Path(resolver.RELEASE)
        before = (
            resolver.TRANSFER,
            resolver.SOURCE,
            resolver.LIFECYCLE_SHA256,
            resolver.UPDATE,
            resolver.RELEASE,
        )
        check.configure_resolver_pins(resolver, release)
        self.assertEqual(
            before,
            (
                resolver.TRANSFER,
                resolver.SOURCE,
                resolver.LIFECYCLE_SHA256,
                resolver.UPDATE,
                resolver.RELEASE,
            ),
        )

    def test_partial_observation_keeps_only_bounded_categories_on_error(self):
        elapsed = {"driver": None, "worker": None, "secret": "private"}
        reconstruction = {"first_trigger": None, "candidates": [], "truncated": False}
        report = {"phase": "observe_fail_closed", "outcome": "failed", "passed": False}

        class Active:
            def observe_job_termination(self, *, elapsed, reconstruction_evidence):
                elapsed["worker"] = 1.23456
                reconstruction_evidence["first_trigger"] = {
                    "branch": "same_container_identity",
                    "elapsed_seconds": 1.5,
                    "private_path": "/secret/path",
                }
                reconstruction_evidence["candidates"] = [
                    {
                        "command_category": "qemu",
                        "exit_observed_seconds": 2.1,
                        "pid": 123,
                        "argv": "private command",
                    }
                ]
                raise RuntimeError("private identifier")

        try:
            check.observe_with_partial_report(
                Active(), report, elapsed=elapsed, reconstruction=reconstruction
            )
        except RuntimeError as exc:
            check.record_primary_failure(report, exc)
        else:
            self.fail("Observation exception was swallowed")
        check.finalize_report(report)
        self.assertEqual(report["failure_origin"], "observe_job_termination")
        self.assertEqual(report["error_kind"], "RuntimeError")
        self.assertEqual(report["elapsed_seconds"], {"worker": 1.235})
        self.assertEqual(report["reconstruction_summary"]["candidate_count"], 1)
        self.assertIn(
            "observation_exception",
            report["reconstruction_summary"]["rejection_reasons"],
        )
        self.assertEqual(
            report["reconstruction_summary"]["candidate_categories"]["qemu"], 1
        )
        self.assertEqual(report["outcome"], "failed")
        self.assertFalse(report["passed"])
        self.assertNotIn("private", json.dumps(report))
        self.assertNotIn("123", json.dumps(report))

    def test_completed_reconstruction_summary_retains_only_aggregates(self):
        report = {}
        evidence = {
            "first_trigger": {
                "branch": "same_container_identity",
                "elapsed_seconds": 1.4,
            },
            "candidates": [
                {
                    "pid": 123,
                    "command_category": "exact_delete_helper",
                    "trusted_cleanup": True,
                    "exit_observed_seconds": 2.0,
                },
                {
                    "pid": 456,
                    "command_category": "exact_delete_helper",
                    "trusted_cleanup": False,
                    "exit_observed_seconds": None,
                },
            ],
            "truncated": False,
            "ambiguous": True,
            "first_scan_elapsed_seconds": 0.1,
        }
        check.partial_observation(
            report,
            {"driver": 1.23, "unknown": "/private/path"},
            evidence,
            completed=True,
        )
        summary = report["reconstruction_summary"]
        self.assertEqual(report["elapsed_seconds"], {"driver": 1.23})
        self.assertEqual(summary["candidate_count"], 2)
        self.assertEqual(summary["trusted_cleanup_count"], 1)
        self.assertEqual(summary["untrusted_cleanup_count"], 1)
        self.assertTrue(summary["ambiguous"])
        self.assertFalse(summary["scan_continuity_verified"])
        self.assertEqual(
            summary["rejection_reasons"],
            ["scan_ambiguous", "candidate_not_trusted", "candidate_exit_unobserved"],
        )
        self.assertNotIn("123", json.dumps(report))
        self.assertNotIn("/private/path", json.dumps(report))

    def test_huge_elapsed_integer_is_omitted_without_masking_observation_error(self):
        self.assertIsNone(check.bounded_seconds(10**1000))
        report = {"phase": "observe_fail_closed", "outcome": "failed", "passed": False}
        check.partial_observation(
            report,
            {"driver": 10**1000, "worker": 1.25},
            {
                "first_trigger": {"elapsed_seconds": 10**1000},
                "candidates": [],
                "truncated": False,
            },
        )
        self.assertEqual(report["elapsed_seconds"], {"worker": 1.25})
        self.assertIsNone(
            report["reconstruction_summary"]["first_trigger_elapsed_seconds"]
        )

    def test_failed_reconstruction_retains_private_evidence_without_path_in_report(
        self,
    ):
        evidence = {
            "first_trigger": None,
            "candidates": [{"pid": 123}],
            "truncated": False,
        }
        report = {}
        helper_path = SCRIPT.parents[1] / "scripts/m2-tailnet-down-active-check.py"
        helper_spec = importlib.util.spec_from_file_location(
            "browser_active_retention_helper", helper_path
        )
        assert helper_spec is not None and helper_spec.loader is not None
        helper = importlib.util.module_from_spec(helper_spec)
        helper_spec.loader.exec_module(helper)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            real_lstat = Path.lstat

            def root_lstat(path):
                info = real_lstat(path)
                return types.SimpleNamespace(
                    st_mode=info.st_mode,
                    st_uid=0,
                    st_nlink=info.st_nlink,
                    st_size=info.st_size,
                )

            class Active:
                def write_reconstruction_evidence(self, root, value):
                    self.root = root
                    self.value = value
                    helper.write_reconstruction_evidence(root, value)

            active = Active()
            with (
                patch.object(check.os, "geteuid", return_value=0),
                patch.object(check, "EVIDENCE_PARENT", directory),
                patch.object(check.Path, "lstat", root_lstat),
            ):
                check.retain_failed_reconstruction(active, report, evidence)
            self.assertEqual(active.root.parent, directory)
            self.assertIs(active.value, evidence)
            self.assertEqual(report["evidence_ref"], active.root.name)
            self.assertRegex(
                report["evidence_ref"], r"^prism-browser-reconstruction-[0-9a-f]{32}-"
            )
            self.assertEqual(stat.S_IMODE(active.root.stat().st_mode), 0o700)
            self.assertEqual(
                json.loads((active.root / "reconstruction-evidence.json").read_text()),
                evidence,
            )
        self.assertTrue(report["evidence_retained"])
        self.assertNotIn(str(directory), json.dumps(report))
        with (
            patch.object(check.os, "geteuid", return_value=0),
            self.assertRaisesRegex(RuntimeError, "not bounded"),
        ):
            check.retain_failed_reconstruction(active, {}, {"candidates": [{}] * 17})

    def test_retention_refuses_symlink_and_does_not_publish_reference(self):
        evidence = {
            "first_trigger": None,
            "candidates": [{"pid": 123}],
            "truncated": False,
        }
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            real_lstat = Path.lstat

            def root_lstat(path):
                info = real_lstat(path)
                return types.SimpleNamespace(
                    st_mode=info.st_mode,
                    st_uid=0,
                    st_nlink=info.st_nlink,
                    st_size=info.st_size,
                )

            class Active:
                def write_reconstruction_evidence(self, root, _value):
                    (root / "reconstruction-evidence.json").symlink_to(
                        parent / "target"
                    )

            report = {}
            with (
                patch.object(check.os, "geteuid", return_value=0),
                patch.object(check, "EVIDENCE_PARENT", parent),
                patch.object(check.Path, "lstat", root_lstat),
                self.assertRaisesRegex(RuntimeError, "file differs"),
            ):
                check.retain_failed_reconstruction(Active(), report, evidence)
            self.assertNotIn("evidence_ref", report)
            self.assertNotIn("evidence_retained", report)

    def test_retention_refuses_symlink_parent_and_oversized_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            alias = parent / "alias"
            alias.symlink_to(parent, target_is_directory=True)
            with (
                patch.object(check.os, "geteuid", return_value=0),
                patch.object(check, "EVIDENCE_PARENT", alias),
                self.assertRaisesRegex(RuntimeError, "parent differs"),
            ):
                check.retain_failed_reconstruction(
                    types.SimpleNamespace(), {}, {"candidates": [{"pid": 123}]}
                )
        evidence = {"candidates": [{"private": "x" * check.MAX_RECONSTRUCTION_BYTES}]}
        with (
            patch.object(check.os, "geteuid", return_value=0),
            self.assertRaisesRegex(RuntimeError, "size bound"),
        ):
            check.retain_failed_reconstruction(types.SimpleNamespace(), {}, evidence)

    def test_observation_error_survives_retention_failure(self):
        elapsed = {"worker": None}
        evidence = {"first_trigger": None, "candidates": [], "truncated": False}
        report = {"phase": "observe_fail_closed", "outcome": "failed", "passed": False}

        class Active:
            def observe_job_termination(self, *, reconstruction_evidence, **_kwargs):
                reconstruction_evidence["ambiguous"] = True
                raise ValueError("private observation detail")

        with (
            patch.object(
                check,
                "retain_failed_reconstruction",
                side_effect=OSError("private retention detail"),
            ),
            self.assertRaisesRegex(ValueError, "private observation detail") as raised,
        ):
            check.observe_with_partial_report(
                Active(), report, elapsed=elapsed, reconstruction=evidence
            )
        check.record_primary_failure(report, raised.exception)
        check.finalize_report(report)
        self.assertEqual(report["error_kind"], "ValueError")
        self.assertEqual(report["evidence_retention_error_kind"], "OSError")
        self.assertEqual(
            report["reconstruction_summary"]["rejection_reasons"],
            ["scan_ambiguous", "observation_exception"],
        )
        self.assertNotIn("private", json.dumps(report))
        self.assertFalse(report["passed"])

    def test_first_trigger_before_candidate_capture_retains_partial_trace(self):
        evidence = {"first_trigger": None, "candidates": [], "truncated": False}
        report = {"phase": "observe_fail_closed", "outcome": "failed", "passed": False}

        class Active:
            def observe_job_termination(self, *, reconstruction_evidence, **_kwargs):
                reconstruction_evidence["first_trigger"] = {
                    "branch": "same_container_identity",
                    "elapsed_seconds": 0.75,
                    "private_path": "/secret",
                }
                raise LookupError("private candidate identity failure")

        active = Active()
        with (
            patch.object(check, "retain_failed_reconstruction") as retain,
            self.assertRaisesRegex(
                LookupError, "private candidate identity failure"
            ) as raised,
        ):
            check.observe_with_partial_report(
                active, report, elapsed={}, reconstruction=evidence
            )
        retain.assert_called_once_with(active, report, evidence)
        self.assertIsNotNone(evidence["first_trigger"])
        check.record_primary_failure(report, raised.exception)
        check.finalize_report(report)
        self.assertEqual(report["error_kind"], "LookupError")
        self.assertEqual(
            report["reconstruction_summary"]["rejection_reasons"],
            ["candidate_identity_unrecorded", "observation_exception"],
        )
        self.assertNotIn("/secret", json.dumps(report))
        self.assertFalse(report["passed"])

    def test_strict_reconstruction_reasons_distinguish_trusted_and_unknown(self):
        report = {}
        evidence = {
            "first_trigger": {"branch": "new_shim_descendant", "elapsed_seconds": 0.4},
            "candidates": [
                {
                    "command_category": "other",
                    "trusted_cleanup": False,
                    "exit_observed_seconds": None,
                    "argv": "private",
                }
            ],
            "truncated": False,
        }
        check.partial_observation(
            report, {}, evidence, completed=True, whole_group=False, reconstructed=True
        )
        self.assertEqual(
            report["reconstruction_summary"]["rejection_reasons"],
            [
                "candidate_not_trusted",
                "candidate_exit_unobserved",
                "whole_group_unverified",
                "reconstruction_detected",
            ],
        )
        self.assertEqual(
            report["reconstruction_summary"]["first_trigger_branch"],
            "new_shim_descendant",
        )
        self.assertNotIn("private", json.dumps(report))
        evidence["first_trigger"]["branch"] = "same_container_identity"
        evidence["candidates"] = [
            {
                "command_category": "exact_delete_helper",
                "trusted_cleanup": True,
                "exit_observed_seconds": 1.2,
            }
        ]
        check.partial_observation(
            report, {}, evidence, completed=True, whole_group=True, reconstructed=True
        )
        self.assertEqual(
            report["reconstruction_summary"]["rejection_reasons"],
            ["reconstruction_detected"],
        )
        self.assertEqual(report["reconstruction_summary"]["trusted_cleanup_count"], 1)

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
