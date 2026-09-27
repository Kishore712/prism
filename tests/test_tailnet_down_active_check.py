"""Local boundaries for the new active tailnet-down diagnostic."""

import importlib.util
import io
import json
import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/m2-tailnet-down-active-check.py"
RESOLVER = ROOT / "scripts/gcp/identity-uncertain-resolution-v8.py"


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


check = module(SCRIPT, "tailnet_down_active_check")
resolution = module(RESOLVER, "uncertain_resolution_v8")


class TailnetDownActiveCheckTests(unittest.TestCase):
    def test_release_and_resolver_pins_are_local_sources(self):
        import hashlib

        self.assertEqual(
            hashlib.sha256(
                (ROOT / "src/prism/host_watchdog.py").read_bytes()
            ).hexdigest(),
            check.WATCHDOG_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(
                (ROOT / "scripts/gcp/identity-service-lifecycle.py").read_bytes()
            ).hexdigest(),
            check.LIFECYCLE_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(RESOLVER.read_bytes()).hexdigest(), check.RESOLVER_SHA256
        )
        self.assertEqual(resolution.TRANSFER, check.TRANSFER_SHA256)
        self.assertEqual(resolution.SOURCE, check.SOURCE_SHA256)
        self.assertEqual(check.MAX_LAB_CHATS, 9)
        self.assertTrue(check.lab_capacity_available(8))
        self.assertFalse(check.lab_capacity_available(9))

    def test_raw_reconstruction_trace_is_private_and_stdout_is_aggregate_only(self):
        evidence = {
            "first_trigger": {
                "branch": "same_container_identity",
                "elapsed_seconds": 1.25,
            },
            "candidates": [
                {
                    "pid": 12345,
                    "starttime": 98765,
                    "parent_chain": [12345, 54321, 1],
                    "command_category": "exact_delete_helper",
                    "argv_basename": "private-helper",
                    "argv_sha256": "f" * 64,
                    "nerdctl_private": {
                        "exact_argv": None,
                        "argument_fingerprints": [
                            {"index": 0, "known_label": "nerdctl_binary"}
                        ],
                        "exe_path": "/usr/local/bin/nerdctl",
                        "cgroup": "0::/private",
                    },
                    "exit_observed_seconds": 1.8,
                }
            ],
            "truncated": False,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "private"
            root.mkdir(mode=0o700)
            report = {"error_kind": "ObservedFailure", "evidence_retained": False}
            check.finish_evidence_root(root, evidence, report, {"gate": False})
            path = root / "reconstruction-evidence.json"
            self.assertTrue(path.is_file())
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text()), evidence)
            self.assertTrue(report["evidence_retained"])
            self.assertEqual(report["reconstruction"]["candidate_count"], 1)
            self.assertEqual(report["reconstruction"]["candidate_exit_count"], 1)
            report["passed"] = False
            stdout = io.StringIO()
            with (
                patch.object(sys, "argv", ["active-check", "--release", "/unused"]),
                patch.object(check, "run_check", return_value=report),
                redirect_stdout(stdout),
            ):
                self.assertEqual(check.main(), 1)
            public = stdout.getvalue()
            for forbidden in (
                '"pid"',
                '"starttime"',
                '"parent_chain"',
                "12345",
                "98765",
                "private-helper",
                "f" * 64,
                "nerdctl_private",
                "/usr/local/bin/nerdctl",
                "0::/private",
                str(root),
            ):
                self.assertNotIn(forbidden, public)
            self.assertIn('"exact_delete_helper": 1', public)
            passing = {"error_kind": None, "evidence_retained": False}
            check.finish_evidence_root(root, evidence, passing, {"gate": True})
            self.assertFalse(root.exists())

    def test_reconstruction_trace_records_safe_delete_helper_and_exit(self):
        container_id = "a" * 64
        group = {
            "container_id": container_id,
            "shim_pid": 101,
            "processes": [{"pid": 101, "start": 456}, {"pid": 102, "start": 789}],
        }
        evidence = {"first_trigger": None, "candidates": [], "truncated": False}
        argv = [
            "/usr/local/bin/containerd-shim-kata-v2",
            "-namespace",
            "prism-m0",
            "-address",
            "/run/containerd/containerd.sock",
            "-publish-binary",
            "/usr/local/bin/containerd",
            "-id",
            container_id,
            "-bundle",
            f"/run/containerd/io.containerd.runtime.v2.task/prism-m0/{container_id}",
            "delete",
        ]
        self.assertEqual(
            check.safe_command_category(argv, container_id), "exact_delete_helper"
        )
        self.assertEqual(
            check.safe_command_category(argv[:-1], container_id), "normal_shim"
        )
        self.assertEqual(
            check.safe_command_category(
                ["qemu-system-x86_64", "-name", container_id], container_id
            ),
            "qemu",
        )
        clock = [1.25]
        with (
            patch.object(check.time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(check, "pinned_alive", return_value=False),
            patch.object(
                check,
                "same_container_candidates",
                return_value={303: "exact_delete_helper"},
            ),
            patch.object(
                check, "candidate_lineage", return_value=(900, [303, 200, 1], True)
            ),
        ):
            self.assertTrue(
                check.unexpected_group_member(group, evidence=evidence, fault_at=0)
            )
        self.assertEqual(
            evidence["first_trigger"],
            {"branch": "same_container_identity", "elapsed_seconds": 1.25},
        )
        candidate = evidence["candidates"][0]
        self.assertEqual(
            (
                candidate["pid"],
                candidate["starttime"],
                candidate["parent_chain"],
                candidate["command_category"],
            ),
            (303, 900, [303, 200, 1], "exact_delete_helper"),
        )
        self.assertNotIn(container_id, json.dumps(evidence))
        clock[0] = 1.8
        with (
            patch.object(check.time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(check, "process_identity", return_value=None),
        ):
            check.update_candidate_exits(evidence, 0)
        self.assertEqual(candidate["exit_observed_seconds"], 1.8)

    def test_candidate_lineage_records_only_pid_chain_and_start_tick(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def stat_row(pid, parent, start):
                fields = ["S", str(parent), *(["0"] * 17), str(start)]
                path = root / str(pid)
                path.mkdir()
                (path / "stat").write_text(
                    f"{pid} (private-command) " + " ".join(fields)
                )

            stat_row(303, 200, 900)
            stat_row(200, 1, 800)
            self.assertEqual(
                check.candidate_lineage(303, proc_root=root),
                (900, [303, 200, 1], True),
            )

    def test_pinned_group_exit_does_not_end_candidate_exit_observation(self):
        clock = [1.0]
        evidence = {
            "first_trigger": {
                "branch": "same_container_identity",
                "elapsed_seconds": 1.0,
            },
            "candidates": [
                {"pid": 303, "starttime": 900, "exit_observed_seconds": None}
            ],
            "truncated": False,
        }

        def advance(_seconds):
            clock[0] += 0.5 if clock[0] < 2 else 10

        scans = [0]

        def new_member(*_args, **_kwargs):
            scans[0] += 1
            return scans[0] == 1

        with (
            patch.object(check.time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(check.time, "sleep", side_effect=advance),
            patch.object(check, "unexpected_group_member", side_effect=new_member),
            patch.object(check, "group_gone", return_value=True),
            patch.object(
                check,
                "process_identity",
                side_effect=lambda _: (900, "S") if clock[0] < 1.5 else None,
            ),
        ):
            stopped, accepted, reconstructed = check.observe_job_termination(
                fault_at=0.0,
                group_snapshot={"processes": []},
                named={},
                main_gone=lambda: True,
                elapsed={"pre_fault_pinned_group": None},
                reconstruction_evidence=evidence,
                final_absent=lambda: True,
            )
        self.assertTrue(stopped)
        self.assertFalse(accepted)
        self.assertTrue(reconstructed)
        self.assertEqual(evidence["candidates"][0]["exit_observed_seconds"], 1.5)
        self.assertEqual(evidence["first_trigger"]["branch"], "same_container_identity")

    def test_full_window_accepts_only_verified_transient_cleanup(self):
        clock = [0.0]
        final_samples = []
        scans = [0]
        evidence = {"first_trigger": None, "candidates": [], "truncated": False}

        def scan(*_args, **_kwargs):
            scans[0] += 1
            if scans[0] == 1:
                evidence["candidates"].append(
                    {
                        "pid": 303,
                        "starttime": 900,
                        "trusted_cleanup": True,
                        "exit_observed_seconds": 0.9,
                    }
                )
                return True
            return False

        def final_absent():
            final_samples.append(clock[0])
            return True

        with (
            patch.object(check.time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(
                check.time,
                "sleep",
                side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
            ),
            patch.object(check, "unexpected_group_member", side_effect=scan),
            patch.object(check, "group_gone", return_value=True),
            patch.object(check, "update_candidate_exits"),
        ):
            stopped, accepted, reconstructed = check.observe_job_termination(
                fault_at=0,
                group_snapshot={"processes": []},
                named={},
                main_gone=lambda: True,
                elapsed={"pre_fault_pinned_group": None},
                reconstruction_evidence=evidence,
                final_absent=final_absent,
            )
        self.assertTrue(stopped)
        self.assertTrue(accepted)
        self.assertTrue(reconstructed)
        self.assertEqual(final_samples, [30.0])
        self.assertGreaterEqual(scans[0], 3)

    def test_full_window_rejects_unknown_cleanup_or_remaining_resource(self):
        for trusted, final_clean in ((False, True), (True, False)):
            with self.subTest(trusted=trusted, final_clean=final_clean):
                clock = [0.0]
                evidence = {
                    "first_trigger": None,
                    "candidates": [
                        {
                            "pid": 303,
                            "starttime": 900,
                            "trusted_cleanup": trusted,
                            "exit_observed_seconds": 1.0,
                        }
                    ],
                    "truncated": False,
                }
                with (
                    patch.object(
                        check.time,
                        "monotonic",
                        side_effect=lambda clock=clock: clock[0],
                    ),
                    patch.object(
                        check.time,
                        "sleep",
                        side_effect=lambda _, clock=clock: clock.__setitem__(0, 30.0),
                    ),
                    patch.object(check, "unexpected_group_member", return_value=True),
                    patch.object(check, "group_gone", return_value=True),
                    patch.object(check, "update_candidate_exits"),
                ):
                    _, accepted, _ = check.observe_job_termination(
                        fault_at=0,
                        group_snapshot={"processes": []},
                        named={},
                        main_gone=lambda: True,
                        elapsed={"pre_fault_pinned_group": None},
                        reconstruction_evidence=evidence,
                        final_absent=lambda final_clean=final_clean: final_clean,
                    )
                self.assertFalse(accepted)

    def test_ambiguous_shim_subtree_cannot_pass_with_empty_candidates(self):
        clock = [0.0]
        evidence = {"first_trigger": None, "candidates": [], "truncated": False}
        group = {
            "container_id": "a" * 64,
            "shim_pid": 101,
            "qemu_pid": 102,
            "processes": [{"pid": 101, "start": 1}, {"pid": 102, "start": 2}],
        }
        scans = [0]

        def descendants(_pid):
            scans[0] += 1
            return None if scans[0] == 1 else [101, 102]

        with (
            patch.object(check.time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(
                check.time, "sleep", side_effect=lambda _: clock.__setitem__(0, 30.0)
            ),
            patch.object(check, "pinned_alive", side_effect=lambda _: clock[0] < 30),
            patch.object(check, "group_descendants", side_effect=descendants),
            patch.object(check, "same_container_candidates", return_value={}),
            patch.object(check, "group_gone", return_value=True),
        ):
            _, accepted, reconstructed = check.observe_job_termination(
                fault_at=0,
                group_snapshot=group,
                named={},
                main_gone=lambda: True,
                elapsed={"pre_fault_pinned_group": None},
                reconstruction_evidence=evidence,
                final_absent=lambda: True,
            )
        self.assertFalse(accepted)
        self.assertTrue(reconstructed)
        self.assertTrue(evidence["ambiguous"])

    def test_metadata_disappearance_is_timed_during_full_window(self):
        clock = [0.0]
        elapsed = {"pre_fault_pinned_group": None, "container_metadata": None}
        with (
            patch.object(check.time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(
                check.time,
                "sleep",
                side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
            ),
            patch.object(check, "unexpected_group_member", return_value=False),
            patch.object(check, "group_gone", return_value=True),
        ):
            _, accepted, _ = check.observe_job_termination(
                fault_at=0,
                group_snapshot={"processes": []},
                named={},
                main_gone=lambda: True,
                elapsed=elapsed,
                reconstruction_evidence={
                    "first_trigger": None,
                    "candidates": [],
                    "truncated": False,
                },
                metadata_absent=lambda: True,
                final_absent=lambda: True,
            )
        self.assertTrue(accepted)
        self.assertEqual(elapsed["container_metadata"], 0.0)

    def test_front_gap_or_blocking_metadata_probe_invalidates_continuity(self):
        for first_time, probe_delay in ((2.0, 0.0), (0.0, 2.0)):
            with self.subTest(first_time=first_time, probe_delay=probe_delay):
                clock = [first_time]
                evidence = {"first_trigger": None, "candidates": [], "truncated": False}

                def probe(clock=clock, probe_delay=probe_delay):
                    clock[0] += probe_delay
                    return True

                with (
                    patch.object(
                        check.time,
                        "monotonic",
                        side_effect=lambda clock=clock: clock[0],
                    ),
                    patch.object(
                        check.time,
                        "sleep",
                        side_effect=lambda seconds, clock=clock: clock.__setitem__(
                            0, clock[0] + seconds
                        ),
                    ),
                    patch.object(check, "unexpected_group_member", return_value=False),
                    patch.object(check, "group_gone", return_value=True),
                ):
                    _, accepted, _ = check.observe_job_termination(
                        fault_at=0,
                        group_snapshot={"processes": []},
                        named={},
                        main_gone=lambda: True,
                        elapsed={
                            "pre_fault_pinned_group": None,
                            "container_metadata": None,
                        },
                        reconstruction_evidence=evidence,
                        metadata_absent=probe,
                        final_absent=lambda: True,
                    )
                self.assertFalse(accepted)
                self.assertTrue(evidence["ambiguous"])
                self.assertGreater(evidence["max_scan_gap_seconds"], 1.0)

    def test_blocking_terminal_absence_probe_cannot_pass(self):
        clock = [0.0]
        evidence = {"first_trigger": None, "candidates": [], "truncated": False}

        def slow_final():
            clock[0] += 2
            return True

        with (
            patch.object(check.time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(
                check.time,
                "sleep",
                side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
            ),
            patch.object(check, "unexpected_group_member", return_value=False),
            patch.object(check, "group_gone", return_value=True),
        ):
            _, accepted, _ = check.observe_job_termination(
                fault_at=0,
                group_snapshot={"processes": []},
                named={},
                main_gone=lambda: True,
                elapsed={"pre_fault_pinned_group": None},
                reconstruction_evidence=evidence,
                final_absent=slow_final,
            )
        self.assertFalse(accepted)
        self.assertTrue(evidence["ambiguous"])

    def test_trusted_delete_exec_change_revokes_trust(self):
        candidate = {
            "pid": 303,
            "starttime": 900,
            "parent_chain": [303, 1],
            "parent_starttimes": [900, 1],
            "trusted_cleanup": True,
            "exit_observed_seconds": None,
        }
        evidence = {"candidates": [candidate]}
        with (
            patch.object(check, "process_identity", return_value=(900, "S")),
            patch.object(
                check, "candidate_lineage", return_value=(900, [303, 1], True)
            ),
            patch.object(check, "candidate_chain_starts", return_value=[900, 1]),
            patch.object(
                check,
                "candidate_command_identity",
                return_value={"trusted_delete_shape": False},
            ),
        ):
            check.update_candidate_exits(evidence, 0, "a" * 64)
        self.assertFalse(candidate["trusted_cleanup"])
        self.assertIsNone(candidate["exit_observed_seconds"])

    def test_delete_helper_requires_fixed_shape_and_executable(self):
        container_id = "a" * 64
        argv = [
            "/usr/local/bin/containerd-shim-kata-v2",
            "-namespace",
            "prism-m0",
            "-address",
            "/run/containerd/containerd.sock",
            "-publish-binary",
            "/usr/local/bin/containerd",
            "-id",
            container_id,
            "-bundle",
            f"/run/containerd/io.containerd.runtime.v2.task/prism-m0/{container_id}",
            "delete",
        ]
        with (
            patch.object(check, "process_argv", return_value=argv),
            patch.object(
                check.os,
                "readlink",
                return_value="/opt/kata/bin/containerd-shim-kata-v2",
            ),
        ):
            self.assertTrue(
                check.candidate_command_identity(303, container_id)[
                    "trusted_delete_shape"
                ]
            )
        with (
            patch.object(check, "process_argv", return_value=argv + ["extra"]),
            patch.object(
                check.os,
                "readlink",
                return_value="/opt/kata/bin/containerd-shim-kata-v2",
            ),
        ):
            self.assertFalse(
                check.candidate_command_identity(303, container_id)[
                    "trusted_delete_shape"
                ]
            )

    def test_unknown_nerdctl_private_fingerprints_do_not_store_raw_args(self):
        resource = "prism-m23-run-" + "a" * 32
        argv = [
            "/usr/local/bin/nerdctl",
            "--address",
            "/run/containerd/containerd.sock",
            "--namespace",
            "prism-m0",
            "mystery-command",
            "opaque-value",
        ]
        with (
            patch.object(check, "process_argv", return_value=argv),
            patch.object(check.os, "readlink", return_value="/usr/local/bin/nerdctl"),
            patch.object(
                Path, "read_text", return_value="0::/system.slice/private.service\n"
            ),
        ):
            result = check.candidate_command_identity(303, "b" * 64, resource)
        private = result["nerdctl_private"]
        self.assertFalse(private["fixed_management_shape"])
        self.assertIsNone(private["exact_argv"])
        self.assertEqual(private["exe_path"], "/usr/local/bin/nerdctl")
        self.assertEqual(private["cgroup"], "0::/system.slice/private.service\n")
        self.assertEqual(
            private["argument_fingerprints"][1]["known_label"], "address_flag"
        )
        self.assertIsNone(private["argument_fingerprints"][5]["known_label"])
        self.assertNotIn("opaque-value", json.dumps(result))

    def test_fixed_nerdctl_management_shape_binds_exact_resource(self):
        resource = "prism-m23-run-" + "a" * 32
        argv = [
            "/usr/local/bin/nerdctl",
            "--address",
            "/run/containerd/containerd.sock",
            "--namespace",
            "prism-m0",
            "inspect",
            resource,
        ]
        with (
            patch.object(check, "process_argv", return_value=argv),
            patch.object(check.os, "readlink", return_value="/usr/local/bin/nerdctl"),
            patch.object(Path, "read_text", return_value="0::/private\n"),
        ):
            exact = check.candidate_command_identity(303, "b" * 64, resource)
            wrong = check.candidate_command_identity(
                303, "b" * 64, "prism-m23-run-" + "c" * 32
            )
        self.assertTrue(exact["nerdctl_private"]["fixed_management_shape"])
        self.assertEqual(exact["nerdctl_private"]["exact_argv"], argv)
        self.assertFalse(wrong["nerdctl_private"]["fixed_management_shape"])
        self.assertIsNone(wrong["nerdctl_private"]["exact_argv"])

    def test_reconstruction_passes_owned_resource_into_private_identity(self):
        resource = "prism-m23-run-" + "a" * 32
        group = {
            "container_id": "b" * 64,
            "resource": resource,
            "shim_pid": 101,
            "qemu_pid": 102,
            "processes": [{"pid": 101, "start": 1}, {"pid": 102, "start": 2}],
        }
        evidence = {"first_trigger": None, "candidates": [], "truncated": False}
        with (
            patch.object(check, "pinned_alive", return_value=False),
            patch.object(
                check, "same_container_candidates", return_value={303: "other"}
            ),
            patch.object(
                check, "candidate_lineage", return_value=(900, [303, 101, 1], True)
            ),
            patch.object(
                check,
                "candidate_command_identity",
                return_value={"trusted_delete_shape": False},
            ) as identity,
        ):
            self.assertTrue(
                check.unexpected_group_member(group, evidence=evidence, fault_at=0)
            )
        identity.assert_called_with(303, "b" * 64, resource)

    def test_recovery_stability_requires_full_window_and_detects_late_loss(self):
        for loss_at in (None, 125.0):
            with self.subTest(loss_at=loss_at):
                clock = [0.0]
                base = types.SimpleNamespace(
                    SERVICE="app.service",
                    WATCHDOG="watchdog.service",
                    service_active=lambda _: True,
                    show=lambda unit, _: {
                        "app.service": "101",
                        "watchdog.service": "102",
                        "tailscaled.service": "103",
                    }[unit],
                    shield_state=lambda: False,
                )
                runtime = types.SimpleNamespace(all_resources=list)
                with (
                    patch.object(
                        check.time,
                        "monotonic",
                        side_effect=lambda clock=clock: clock[0],
                    ),
                    patch.object(
                        check.time,
                        "sleep",
                        side_effect=lambda seconds, clock=clock: clock.__setitem__(
                            0, clock[0] + seconds
                        ),
                    ),
                    patch.object(
                        check,
                        "process_identity",
                        side_effect=lambda pid: (pid + 1000, "S"),
                    ),
                    patch.object(
                        check,
                        "tailnet_online",
                        side_effect=lambda _base, clock=clock, loss_at=loss_at: (
                            loss_at is None or clock[0] < loss_at
                        ),
                    ),
                    patch.object(check, "watchdog_socket_healthy", return_value=True),
                ):
                    stable, summary = check.observe_recovery_stability(
                        base, object(), runtime, duration=180
                    )
                self.assertEqual(stable, loss_at is None)
                if stable:
                    self.assertGreaterEqual(summary["observed_seconds"], 180)
                else:
                    self.assertEqual(summary["failure_category"], "tailnet_unverified")
                    self.assertLess(summary["observed_seconds"], 180)

    def test_unstable_recovery_closes_ingress_before_stopping_service(self):
        events = []
        state = {"app.service": True, "watchdog.service": True}

        def command(*args, **_kwargs):
            events.append(args)
            if args[:2] == ("/usr/bin/systemctl", "stop"):
                state[args[2]] = False

        base = types.SimpleNamespace(
            SERVICE="app.service",
            WATCHDOG="watchdog.service",
            command=command,
            shield_state=lambda: True,
            service_active=lambda unit: state[unit],
        )
        with patch.object(
            check,
            "force_tailnet_closed",
            side_effect=lambda _: events.append(("force_tailnet_closed",)),
        ):
            check.fail_closed_after_unstable_recovery(
                base, service_drained=lambda: True
            )
        self.assertEqual(events[0], ("/usr/bin/tailscale", "set", "--shields-up=true"))
        self.assertEqual(events[1], ("/usr/bin/systemctl", "stop", "app.service"))
        self.assertEqual(events[2], ("/usr/bin/systemctl", "stop", "watchdog.service"))
        self.assertEqual(events[3], ("force_tailnet_closed",))

    def test_unverifiable_ingress_kills_service_before_any_stop_wait(self):
        events = []
        state = {"app.service": True, "watchdog.service": True}

        def command(*args, **_kwargs):
            events.append(args)
            if args[0] == "/usr/bin/tailscale":
                raise RuntimeError("shield unavailable")
            if args[:2] == ("/usr/bin/systemctl", "kill"):
                state["app.service"] = False
            if args[:2] == ("/usr/bin/systemctl", "stop"):
                state[args[2]] = False

        base = types.SimpleNamespace(
            SERVICE="app.service",
            WATCHDOG="watchdog.service",
            command=command,
            shield_state=lambda: False,
            service_active=lambda unit: state[unit],
        )
        with (
            patch.object(
                check,
                "force_tailnet_closed",
                side_effect=RuntimeError("tailnet unavailable"),
            ),
            self.assertRaisesRegex(RuntimeError, "tailnet unavailable"),
        ):
            check.fail_closed_after_unstable_recovery(
                base, service_drained=lambda: True
            )
        self.assertEqual(
            events[1][:3], ("/usr/bin/systemctl", "kill", "--signal=SIGKILL")
        )
        self.assertEqual(events[2], ("/usr/bin/systemctl", "stop", "app.service"))
        self.assertNotIn(("/usr/bin/systemctl", "stop", "watchdog.service"), events)

    def test_watchdog_health_wait_and_resolver_error_are_bounded_and_safe(self):
        attempts = [0]

        def probe():
            attempts[0] += 1
            if attempts[0] < 3:
                raise RuntimeError("private socket path")

        host = types.SimpleNamespace(
            WatchdogClient=lambda: types.SimpleNamespace(health=probe)
        )
        waits = []

        def wait_for(predicate, seconds):
            waits.append(seconds)
            return any(predicate() for _ in range(3))

        self.assertTrue(
            check.wait_watchdog_socket(types.SimpleNamespace(wait_for=wait_for), host)
        )
        self.assertEqual(waits, [15])
        self.assertEqual(attempts[0], 3)
        report = {"resolver_failure": None}
        denied = subprocess.CalledProcessError(2, ["secret-command"], stderr=b"secret")
        with (
            patch.object(check, "resolver_action", side_effect=denied),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            check.staged_resolver_action("inspect", "a" * 32, report)
        self.assertEqual(
            report["resolver_failure"], {"stage": "inspect", "category": "rejected"}
        )
        self.assertNotIn("secret", json.dumps(report))

    def test_resolver_stages_stop_at_inspect_mismatch_or_safe_refusal(self):
        report = {"resolver_failure": None}
        with patch.object(
            check, "staged_resolver_action", return_value={"changed": True}
        ) as action:
            self.assertFalse(check.inspect_and_resolve("a" * 32, report))
        action.assert_called_once_with("inspect", "a" * 32, report)
        self.assertEqual(
            report["resolver_failure"],
            {"stage": "inspect", "category": "response_mismatch"},
        )
        report = {"resolver_failure": None}
        with patch.object(
            check,
            "staged_resolver_action",
            side_effect=[{"changed": False}, {"changed": False}],
        ):
            self.assertFalse(check.inspect_and_resolve("a" * 32, report))
        self.assertEqual(
            report["resolver_failure"],
            {"stage": "resolve", "category": "response_mismatch"},
        )

    def test_resolver_precondition_reports_fixed_category(self):
        base = types.SimpleNamespace(
            SERVICE="identity.service",
            wait_for=lambda _predicate, _seconds: False,
        )
        self.assertEqual(
            check.resolver_precondition_failure(
                row={"status": "uncertain"},
                base=base,
                runtime=object(),
                observed=object(),
                host_watchdog=object(),
                resource="resource",
                token="token",
            ),
            "service_not_stopped",
        )

    def test_recovery_stops_watchdog_before_tailnet_up_and_waits_online(self):
        calls = []
        state = {"watchdog": True, "tailscaled": False, "online": False}

        def command(*argv, **kwargs):
            calls.append(argv)
            if argv == ("/usr/bin/systemctl", "stop", "watchdog.service"):
                state["watchdog"] = False
            elif argv == ("/usr/bin/systemctl", "start", "tailscaled.service"):
                state["tailscaled"] = True
            elif argv == ("/usr/bin/timeout", "25", "/usr/bin/tailscale", "up"):
                state["online"] = True
            elif argv == ("/usr/bin/systemctl", "start", "watchdog.service"):
                state["watchdog"] = True
            if argv == ("/usr/bin/tailscale", "debug", "prefs"):
                return types.SimpleNamespace(
                    stdout=b'{"WantRunning":false,"ShieldsUp":true}'
                )
            status = {
                "BackendState": "Running" if state["online"] else "Stopped",
                "Self": {"Online": state["online"]},
            }
            return types.SimpleNamespace(stdout=json.dumps(status).encode())

        base = types.SimpleNamespace(
            WATCHDOG="watchdog.service",
            command=command,
            service_active=lambda name: (
                state["watchdog"] if name == "watchdog.service" else state["tailscaled"]
            ),
            wait_for=lambda predicate, _seconds: predicate(),
            shield_state=lambda: True,
        )
        steps = []
        check.recover_tailnet(base, service_drained=lambda: True, on_step=steps.append)
        self.assertEqual(
            steps,
            [
                "stop_watchdog",
                "reset_tailscaled_failure",
                "start_tailscaled",
                "verify_offline_shield",
                "tailscale_up",
                "start_watchdog",
            ],
        )
        self.assertEqual(calls[0], ("/usr/bin/systemctl", "stop", "watchdog.service"))
        self.assertEqual(
            calls[1], ("/usr/bin/systemctl", "reset-failed", "tailscaled.service")
        )
        self.assertEqual(
            calls[2], ("/usr/bin/systemctl", "start", "tailscaled.service")
        )
        up = ("/usr/bin/timeout", "25", "/usr/bin/tailscale", "up")
        self.assertIn(up, calls)
        self.assertNotIn("--timeout", up)
        self.assertLess(
            calls.index(("/usr/bin/tailscale", "debug", "prefs")), calls.index(up)
        )
        self.assertEqual(calls[-1], ("/usr/bin/systemctl", "start", "watchdog.service"))

    def test_recovery_does_not_start_watchdog_when_self_offline(self):
        calls = []
        state = {"up": False}

        def command(*argv, **_kwargs):
            calls.append(argv)
            if argv == ("/usr/bin/tailscale", "debug", "prefs"):
                return types.SimpleNamespace(
                    stdout=b'{"WantRunning":false,"ShieldsUp":true}'
                )
            if argv == ("/usr/bin/timeout", "25", "/usr/bin/tailscale", "up"):
                state["up"] = True
            if argv == ("/usr/bin/tailscale", "down"):
                state["up"] = False
            status = {
                "BackendState": "Running" if state["up"] else "Stopped",
                "Self": {"Online": False},
            }
            return types.SimpleNamespace(stdout=json.dumps(status).encode())

        base = types.SimpleNamespace(
            WATCHDOG="watchdog.service",
            command=command,
            show=lambda _unit, _field: "123",
            service_active=lambda name: name == "tailscaled.service",
            wait_for=lambda predicate, _seconds: predicate(),
            shield_state=lambda: True,
        )
        with self.assertRaisesRegex(RuntimeError, "online Running"):
            check.recover_tailnet(base, service_drained=lambda: True)
        self.assertFalse(
            any(
                argv == ("/usr/bin/systemctl", "start", "watchdog.service")
                for argv in calls
            )
        )

    def test_recovery_denies_occupied_service_cgroup_before_up(self):
        calls = []

        def command(*argv, **_kwargs):
            calls.append(argv)
            return types.SimpleNamespace(stdout=b"{}")

        base = types.SimpleNamespace(
            WATCHDOG="watchdog.service",
            command=command,
            service_active=lambda name: name == "tailscaled.service",
        )
        with self.assertRaisesRegex(RuntimeError, "before tailscaled start"):
            check.recover_tailnet(base, service_drained=lambda: False)
        self.assertNotIn(("/usr/bin/systemctl", "start", "tailscaled.service"), calls)
        self.assertFalse(any(argv[:1] == ("/usr/bin/timeout",) for argv in calls))

    def test_recovery_rechecks_cgroup_after_start_before_up(self):
        calls = []
        checks = iter((True, False))

        def command(*argv, **_kwargs):
            calls.append(argv)
            return types.SimpleNamespace(stdout=b"{}")

        base = types.SimpleNamespace(
            WATCHDOG="watchdog.service",
            command=command,
            service_active=lambda name: name == "tailscaled.service",
        )
        with self.assertRaisesRegex(RuntimeError, "before tailnet up"):
            check.recover_tailnet(base, service_drained=lambda: next(checks))
        self.assertIn(("/usr/bin/systemctl", "start", "tailscaled.service"), calls)
        self.assertFalse(any(argv[:1] == ("/usr/bin/timeout",) for argv in calls))

    def test_recovery_denies_unsafe_offline_prefs_before_up(self):
        for want_running, shield in ((True, True), (False, False)):
            with self.subTest(want_running=want_running, shield=shield):
                calls = []

                def command(
                    *argv, _calls=calls, _want=want_running, _shield=shield, **_kwargs
                ):
                    _calls.append(argv)
                    if argv == ("/usr/bin/tailscale", "status", "--json"):
                        return types.SimpleNamespace(
                            stdout=b'{"BackendState":"Stopped"}'
                        )
                    if argv == ("/usr/bin/tailscale", "debug", "prefs"):
                        data = {"WantRunning": _want, "ShieldsUp": _shield}
                        return types.SimpleNamespace(stdout=json.dumps(data).encode())
                    return types.SimpleNamespace(stdout=b"{}")

                base = types.SimpleNamespace(
                    WATCHDOG="watchdog.service",
                    command=command,
                    service_active=lambda name: name == "tailscaled.service",
                )
                with self.assertRaises(RuntimeError):
                    check.recover_tailnet(base, service_drained=lambda: True)
                self.assertFalse(
                    any(argv[:1] == ("/usr/bin/timeout",) for argv in calls)
                )
                if not want_running:
                    self.assertIn(
                        ("/usr/bin/tailscale", "set", "--shields-up=true"), calls
                    )

    def test_recovery_closes_tailnet_if_shield_is_lost_after_up(self):
        calls = []
        state = {"up": False}

        def command(*argv, **_kwargs):
            calls.append(argv)
            if argv == ("/usr/bin/tailscale", "debug", "prefs"):
                return types.SimpleNamespace(
                    stdout=b'{"WantRunning":false,"ShieldsUp":true}'
                )
            if argv == ("/usr/bin/timeout", "25", "/usr/bin/tailscale", "up"):
                state["up"] = True
            if argv == ("/usr/bin/tailscale", "down"):
                state["up"] = False
            status = {
                "BackendState": "Running" if state["up"] else "Stopped",
                "Self": {"Online": state["up"]},
            }
            return types.SimpleNamespace(stdout=json.dumps(status).encode())

        base = types.SimpleNamespace(
            WATCHDOG="watchdog.service",
            command=command,
            show=lambda _unit, _field: "123",
            service_active=lambda name: name == "tailscaled.service",
            wait_for=lambda predicate, _seconds: predicate(),
            shield_state=lambda: False,
        )
        with self.assertRaisesRegex(RuntimeError, "lost its preverified ShieldsUp"):
            check.recover_tailnet(base, service_drained=lambda: True)
        self.assertIn(("/usr/bin/tailscale", "down"), calls)
        self.assertNotIn(("/usr/bin/systemctl", "start", "watchdog.service"), calls)

    def test_recovery_force_closes_tailnet_when_up_command_raises(self):
        calls = []
        state = {"tailscaled": False, "up": False}

        def command(*argv, **_kwargs):
            calls.append(argv)
            if argv == ("/usr/bin/systemctl", "start", "tailscaled.service"):
                state["tailscaled"] = True
            if argv == ("/usr/bin/timeout", "25", "/usr/bin/tailscale", "up"):
                state["up"] = True
                raise subprocess.TimeoutExpired("tailscale up", 30)
            if argv == ("/usr/bin/systemctl", "stop", "tailscaled.service"):
                state["tailscaled"] = False
            if argv == ("/usr/bin/tailscale", "debug", "prefs"):
                return types.SimpleNamespace(
                    stdout=b'{"WantRunning":false,"ShieldsUp":true}'
                )
            return types.SimpleNamespace(
                stdout=json.dumps(
                    {"BackendState": "Running" if state["up"] else "Stopped"}
                ).encode()
            )

        base = types.SimpleNamespace(
            WATCHDOG="watchdog.service",
            command=command,
            show=lambda _unit, field: (
                ("123" if state["tailscaled"] else "0")
                if field == "MainPID"
                else ("active" if state["tailscaled"] else "inactive")
            ),
            service_active=lambda name: (
                name == "tailscaled.service" and state["tailscaled"]
            ),
            wait_for=lambda predicate, _seconds: predicate(),
        )
        with (
            patch.object(check, "process_identity", side_effect=[(456, "S"), None]),
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            check.recover_tailnet(base, service_drained=lambda: True)
        self.assertIn(("/usr/bin/tailscale", "down"), calls)
        self.assertIn(("/usr/bin/systemctl", "stop", "tailscaled.service"), calls)
        self.assertNotIn(("/usr/bin/systemctl", "start", "watchdog.service"), calls)

    def test_recovery_force_closes_tailnet_when_online_probe_raises(self):
        calls = []
        state = {"up": False, "status_error": False}

        def command(*argv, **_kwargs):
            calls.append(argv)
            if argv == ("/usr/bin/timeout", "25", "/usr/bin/tailscale", "up"):
                state["up"] = True
                state["status_error"] = True
            if argv == ("/usr/bin/tailscale", "down"):
                state["up"] = False
            if argv == ("/usr/bin/tailscale", "debug", "prefs"):
                return types.SimpleNamespace(
                    stdout=b'{"WantRunning":false,"ShieldsUp":true}'
                )
            if (
                argv == ("/usr/bin/tailscale", "status", "--json")
                and state["status_error"]
            ):
                state["status_error"] = False
                raise OSError("status probe unavailable")
            return types.SimpleNamespace(
                stdout=json.dumps(
                    {"BackendState": "Running" if state["up"] else "Stopped"}
                ).encode()
            )

        base = types.SimpleNamespace(
            WATCHDOG="watchdog.service",
            command=command,
            show=lambda _unit, field: "123" if field == "MainPID" else "active",
            service_active=lambda name: name == "tailscaled.service",
            wait_for=lambda predicate, _seconds: predicate(),
        )
        with (
            patch.object(check, "process_identity", return_value=(456, "S")),
            self.assertRaisesRegex(OSError, "status probe unavailable"),
        ):
            check.recover_tailnet(base, service_drained=lambda: True)
        self.assertIn(("/usr/bin/tailscale", "down"), calls)
        self.assertNotIn(("/usr/bin/systemctl", "start", "watchdog.service"), calls)

    def test_ineffective_down_stops_daemon_and_verifies_main_exit(self):
        calls = []
        state = {"active": True}

        def command(*argv, **_kwargs):
            calls.append(argv)
            if argv == ("/usr/bin/systemctl", "stop", "tailscaled.service"):
                state["active"] = False
            return types.SimpleNamespace(stdout=b'{"BackendState":"Running"}')

        def show(_unit, field):
            if field == "MainPID":
                return "123" if state["active"] else "0"
            return "active" if state["active"] else "inactive"

        base = types.SimpleNamespace(
            command=command,
            show=show,
            wait_for=lambda predicate, _seconds: predicate(),
        )
        with patch.object(check, "process_identity", side_effect=[(456, "S"), None]):
            check.force_tailnet_closed(base)
        self.assertEqual(
            calls[:2],
            [
                ("/usr/bin/tailscale", "down"),
                ("/usr/bin/tailscale", "status", "--json"),
            ],
        )
        self.assertIn(("/usr/bin/systemctl", "stop", "tailscaled.service"), calls)

    def test_initial_tailnet_proc_read_failure_still_stops_and_verifies_pidfd(self):
        calls = []
        state = {"active": True}

        def command(*argv, **_kwargs):
            calls.append(argv)
            if argv == ("/usr/bin/systemctl", "stop", "tailscaled.service"):
                state["active"] = False
            return types.SimpleNamespace(stdout=b'{"BackendState":"Running"}')

        base = types.SimpleNamespace(
            command=command,
            show=lambda _unit, field: (
                ("123" if state["active"] else "0")
                if field == "MainPID"
                else ("active" if state["active"] else "inactive")
            ),
            wait_for=lambda predicate, _seconds: predicate(),
        )
        with (
            patch.object(
                check, "process_identity", side_effect=RuntimeError("proc unreadable")
            ),
            patch.object(check.os, "pidfd_open", return_value=456, create=True),
            patch.object(
                check.signal,
                "pidfd_send_signal",
                side_effect=ProcessLookupError(),
                create=True,
            ),
            patch.object(check.os, "close") as close,
        ):
            check.force_tailnet_closed(base)
        self.assertEqual(calls[0], ("/usr/bin/tailscale", "down"))
        self.assertIn(("/usr/bin/systemctl", "stop", "tailscaled.service"), calls)
        close.assert_called_once_with(456)

    def test_unreadable_tailnet_proc_without_pidfd_never_claims_verified_stop(self):
        calls = []
        state = {"active": True}

        def command(*argv, **_kwargs):
            calls.append(argv)
            if argv == ("/usr/bin/systemctl", "stop", "tailscaled.service"):
                state["active"] = False
            return types.SimpleNamespace(stdout=b'{"BackendState":"Running"}')

        base = types.SimpleNamespace(
            command=command,
            show=lambda _unit, field: (
                ("123" if state["active"] else "0")
                if field == "MainPID"
                else ("active" if state["active"] else "inactive")
            ),
            wait_for=lambda predicate, _seconds: predicate(),
        )
        with (
            patch.object(
                check, "process_identity", side_effect=RuntimeError("proc unreadable")
            ),
            patch.object(
                check.os, "pidfd_open", side_effect=OSError("unavailable"), create=True
            ),
            self.assertRaisesRegex(RuntimeError, "termination could not be verified"),
        ):
            check.force_tailnet_closed(base)
        self.assertEqual(calls[0], ("/usr/bin/tailscale", "down"))
        self.assertIn(("/usr/bin/systemctl", "stop", "tailscaled.service"), calls)

    def test_unverified_daemon_stop_remains_failure(self):
        calls = []

        def command(*argv, **_kwargs):
            calls.append(argv)
            return types.SimpleNamespace(stdout=b'{"BackendState":"Running"}')

        base = types.SimpleNamespace(
            command=command,
            show=lambda _unit, field: "123" if field == "MainPID" else "active",
            wait_for=lambda predicate, _seconds: predicate(),
        )
        with (
            patch.object(check, "process_identity", return_value=(456, "S")),
            self.assertRaisesRegex(RuntimeError, "termination could not be verified"),
        ):
            check.force_tailnet_closed(base)
        self.assertIn(("/usr/bin/systemctl", "stop", "tailscaled.service"), calls)

    def test_unsupported_host_does_not_touch_live_state(self):
        with (
            patch.object(platform, "system", return_value="Darwin"),
            patch.object(
                check, "parent_module", side_effect=AssertionError("host read")
            ),
        ):
            report = check.run_check(Path("/missing"))
        self.assertFalse(report["passed"])
        self.assertEqual(report["error_kind"], "UnsupportedHost")

    def test_last_fault_edge_rechecks_worker_client_and_resource(self):
        row = {
            "id": "a" * 32,
            "status": "running",
            "runtime_resource": "prism-m23-run-" + "a" * 32,
            "runtime_token": "b" * 32,
        }
        parent = types.SimpleNamespace(
            live_row=lambda *_: row,
            process_exact=lambda *_: True,
        )
        observed = types.SimpleNamespace(
            process_identity=lambda _pid: (456, "S"),
            client_live=lambda *_: True,
            owned_running=lambda *_: True,
        )
        host = types.SimpleNamespace(_cgroup=lambda _pid: "/service")
        client = types.SimpleNamespace(pid=30, pidfd=300)
        arguments = {
            "chat": "chat",
            "key": "key",
            "run": row["id"],
            "resource": row["runtime_resource"],
            "token": row["runtime_token"],
            "driver_pid": 10,
            "worker_pid": 20,
            "worker_start": 456,
            "worker_fd": 200,
            "client": client,
            "group": "/service",
            "group_snapshot": {"container_id": "c" * 64},
        }
        children = lambda pid: [20] if pid == 10 else [30]
        runtime = types.SimpleNamespace(
            inspect_owned=lambda *_: {"Id": "c" * 64, "State": {"Status": "running"}}
        )
        with (
            patch.object(check, "children_all_threads", side_effect=children),
            patch.object(check, "exact_group_live", return_value=True),
            patch.object(check.signal, "pidfd_send_signal", create=True) as probe,
        ):
            self.assertTrue(
                check.exact_fault_edge(parent, observed, host, runtime, **arguments)
            )
            self.assertEqual(probe.call_count, 2)
            row["status"] = "completed"
            self.assertFalse(
                check.exact_fault_edge(parent, observed, host, runtime, **arguments)
            )
            row["status"] = "running"
            observed.process_identity = lambda _pid: (457, "S")
            self.assertFalse(
                check.exact_fault_edge(parent, observed, host, runtime, **arguments)
            )
            observed.process_identity = lambda _pid: (456, "S")
            observed.client_live = lambda *_: False
            self.assertFalse(
                check.exact_fault_edge(parent, observed, host, runtime, **arguments)
            )
        observed.client_live = lambda *_: True
        with (
            patch.object(check, "children_all_threads", side_effect=children),
            patch.object(check, "exact_group_live", return_value=False),
        ):
            self.assertFalse(
                check.exact_fault_edge(parent, observed, host, runtime, **arguments)
            )

    def test_kata_binding_requires_unique_exact_shim_and_qemu_descendant(self):
        container_id = "a" * 64
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def process(pid, argv, children=""):
                path = root / str(pid)
                (path / "task" / str(pid)).mkdir(parents=True)
                (path / "cmdline").write_bytes(
                    b"\0".join(s.encode() for s in argv) + b"\0"
                )
                (path / "task" / str(pid) / "children").write_text(children)

            process(
                101,
                [
                    "/opt/kata/bin/containerd-shim-kata-v2",
                    "-namespace",
                    "prism-m0",
                    "-id",
                    container_id,
                ],
                "102 103",
            )
            process(
                102,
                [
                    "/opt/kata/bin/qemu-system-x86_64",
                    "-name",
                    "sandbox-" + container_id,
                ],
            )
            process(103, ["/opt/kata/bin/virtiofsd"])
            with (
                patch.object(
                    check, "process_identity", side_effect=lambda pid: (pid * 10, "S")
                ),
                patch.object(
                    check.os,
                    "pidfd_open",
                    side_effect=lambda pid, _: pid + 1000,
                    create=True,
                ),
                patch.object(check.os, "close") as close,
            ):
                bound = check.bind_kata_group({"Id": container_id}, proc_root=root)
                self.assertEqual(bound["shim_pid"], 101)
                self.assertEqual(bound["qemu_pid"], 102)
                self.assertEqual(
                    [item["pid"] for item in bound["processes"]], [101, 102, 103]
                )
                check.close_group(bound)
                self.assertEqual(close.call_count, 3)
                (root / "101" / "task" / "101" / "children").write_text("103")
                self.assertIsNone(
                    check.bind_kata_group({"Id": container_id}, proc_root=root)
                )
                process(
                    104,
                    [
                        "/opt/kata/bin/qemu-system-x86_64",
                        "-name",
                        "sandbox-" + container_id,
                    ],
                )
                (root / "101" / "task" / "101" / "children").write_text("102 103 104")
                self.assertIsNone(
                    check.bind_kata_group({"Id": container_id}, proc_root=root)
                )
                (root / "101" / "task" / "101" / "children").write_text("102 103")
                process(
                    105,
                    [
                        "/opt/kata/bin/containerd-shim-kata-v2",
                        "-namespace",
                        "prism-m0",
                        "-id",
                        container_id,
                    ],
                )
                self.assertIsNone(
                    check.bind_kata_group({"Id": container_id}, proc_root=root)
                )
                self.assertIn(
                    105, check.same_container_processes(container_id, proc_root=root)
                )

    def test_kata_binding_requires_qemu_container_identity_and_complete_children(self):
        container_id = "a" * 64
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shim = root / "101"
            (shim / "task" / "101").mkdir(parents=True)
            (shim / "cmdline").write_bytes(
                b"containerd-shim-kata-v2\0-namespace\0prism-m0\0-id\0"
                + container_id.encode()
                + b"\0"
            )
            with self.assertRaisesRegex(
                RuntimeError, "children could not be inspected"
            ):
                check.group_descendants(101, proc_root=root)
            (shim / "task" / "101" / "children").write_text("102")
            qemu = root / "102"
            (qemu / "task" / "102").mkdir(parents=True)
            (qemu / "task" / "102" / "children").write_text("")
            (qemu / "cmdline").write_bytes(b"qemu-system-x86_64\0-no-id\0")
            with patch.object(check, "process_identity", return_value=(123, "S")):
                self.assertIsNone(
                    check.bind_kata_group({"Id": container_id}, proc_root=root)
                )

    def test_unreadable_proc_is_not_treated_as_process_exit(self):
        with (
            patch.object(Path, "read_text", side_effect=PermissionError("denied")),
            self.assertRaisesRegex(RuntimeError, "identity could not be inspected"),
        ):
            check.pinned_alive({"pid": 123, "start": 456, "fd": 789})
        with patch.object(Path, "read_text", side_effect=FileNotFoundError(2, "gone")):
            self.assertFalse(check.pinned_alive({"pid": 123, "start": 456, "fd": 789}))
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(check, "process_identity", return_value=(456, "S")),
                self.assertRaisesRegex(RuntimeError, "command could not be inspected"),
            ):
                check.process_argv(123, proc_root=Path(directory), strict=True)
            with patch.object(check, "process_identity", return_value=None):
                self.assertEqual(
                    check.process_argv(123, proc_root=Path(directory), strict=True), []
                )

    def test_new_descendant_or_rebuilt_shim_blocks_group_acceptance(self):
        group = {
            "container_id": "a" * 64,
            "shim_pid": 101,
            "qemu_pid": 102,
            "processes": [{"pid": 101}, {"pid": 102}],
        }
        with (
            patch.object(check, "pinned_alive", return_value=True),
            patch.object(check, "group_descendants", return_value=[101, 102, 103]),
            patch.object(
                check,
                "same_container_candidates",
                return_value={101: "normal_shim", 102: "qemu"},
            ),
        ):
            self.assertTrue(check.unexpected_group_member(group))
        with (
            patch.object(check, "pinned_alive", return_value=False),
            patch.object(
                check,
                "same_container_candidates",
                return_value={105: "exact_delete_helper"},
            ),
        ):
            self.assertTrue(check.unexpected_group_member(group))
        with (
            patch.object(check, "pinned_alive", return_value=True),
            patch.object(check, "group_descendants", return_value=[101, 102]),
            patch.object(
                check,
                "same_container_candidates",
                return_value={101: "exact_delete_helper", 102: "qemu"},
            ),
        ):
            self.assertTrue(check.unexpected_group_member(group))
        clock = [0.0]
        with (
            patch.object(check.time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(
                check.time, "sleep", side_effect=lambda _: clock.__setitem__(0, 31.0)
            ),
            patch.object(check, "group_gone", return_value=True),
            patch.object(check, "unexpected_group_member", return_value=True),
            patch.object(check, "pinned_alive", return_value=False),
        ):
            _, accepted, reconstructed = check.observe_job_termination(
                fault_at=0,
                group_snapshot=group,
                named={},
                main_gone=lambda: True,
                elapsed={"pre_fault_pinned_group": None},
            )
        self.assertFalse(accepted)
        self.assertTrue(reconstructed)

    def test_job_group_deadline_is_independent_of_container_metadata(self):
        group = {"processes": [{"pid": 101}, {"pid": 102}]}
        elapsed = {
            name: None for name in ("guest_qemu", "kata_shim", "pre_fault_pinned_group")
        }
        clock = [0.0]

        def advance(_seconds):
            clock[0] += 10

        with (
            patch.object(check.time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(check.time, "sleep", side_effect=advance),
            patch.object(
                check, "pinned_alive", side_effect=lambda item: item["pid"] == 101
            ),
            patch.object(check, "unexpected_group_member", return_value=False),
        ):
            service_gone, group_stopped, _ = check.observe_job_termination(
                fault_at=0,
                group_snapshot=group,
                named={
                    "guest_qemu": group["processes"][1],
                    "kata_shim": group["processes"][0],
                },
                main_gone=lambda: True,
                elapsed=elapsed,
            )
        self.assertTrue(service_gone)
        self.assertFalse(group_stopped)
        self.assertIsNone(elapsed["pre_fault_pinned_group"])
        self.assertIsNotNone(elapsed["guest_qemu"])
        self.assertIsNone(elapsed["kata_shim"])

    def test_worker_survival_blocks_whole_job_group_acceptance(self):
        elapsed = {"worker": None, "pre_fault_pinned_group": None}
        clock = [0.0]
        with (
            patch.object(check.time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(
                check.time, "sleep", side_effect=lambda _: clock.__setitem__(0, 31.0)
            ),
            patch.object(check, "group_gone", return_value=True),
            patch.object(check, "pinned_alive", return_value=True),
            patch.object(check, "unexpected_group_member", return_value=False),
        ):
            _, stopped, _ = check.observe_job_termination(
                fault_at=0,
                group_snapshot={"processes": []},
                named={"worker": {"pid": 20}},
                main_gone=lambda: True,
                elapsed=elapsed,
            )
        self.assertFalse(stopped)
        self.assertIsNone(elapsed["pre_fault_pinned_group"])

    def test_group_exit_observed_after_deadline_is_not_accepted(self):
        clock = [29.99]

        def group_exit(_snapshot):
            clock[0] = 30.01
            return True

        elapsed = {"pre_fault_pinned_group": None}
        with (
            patch.object(check.time, "monotonic", side_effect=lambda: clock[0]),
            patch.object(check, "group_gone", side_effect=group_exit),
            patch.object(check, "unexpected_group_member", return_value=False),
        ):
            _, accepted, _ = check.observe_job_termination(
                fault_at=0,
                group_snapshot={"processes": []},
                named={},
                main_gone=lambda: True,
                elapsed=elapsed,
            )
        self.assertFalse(accepted)
        self.assertEqual(elapsed["pre_fault_pinned_group"], 30.01)

    def test_unreadable_service_main_never_counts_as_stopped(self):
        main = {"pid": 111, "start": 456, "fd": 999}
        with (
            patch.object(check.time, "monotonic", return_value=0.0),
            patch.object(
                check, "process_identity", side_effect=RuntimeError("unreadable")
            ),
            self.assertRaisesRegex(RuntimeError, "unreadable"),
        ):
            check.observe_job_termination(
                fault_at=0.0,
                group_snapshot={"processes": []},
                named={},
                main_gone=lambda: not check.pinned_alive(main),
                elapsed={"pre_fault_pinned_group": None},
            )

    def test_unobserved_post_down_status_is_inconclusive_evidence(self):
        base = types.SimpleNamespace(
            command=lambda *_: types.SimpleNamespace(
                stdout=b'{"BackendState":"Stopped"}'
            )
        )
        self.assertTrue(check.stopped_backend_observed(base))
        base.command = lambda *_: types.SimpleNamespace(
            stdout=b'{"BackendState":"Running"}'
        )
        self.assertFalse(check.stopped_backend_observed(base))
        base.command = lambda *_: (_ for _ in ()).throw(OSError("daemon exited"))
        self.assertFalse(check.stopped_backend_observed(base))

    def test_versioned_resolver_requires_exact_tailnet_identity(self):
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
        with patch.object(
            resolution,
            "command",
            side_effect=[json.dumps(status).encode(), json.dumps(prefs).encode()],
        ):
            resolution.tailnet_closed("pilot.example.ts.net", "100.100.100.100")
        status["Self"]["TailscaleIPs"] = ["100.100.100.101"]
        with (
            patch.object(
                resolution,
                "command",
                side_effect=[json.dumps(status).encode(), json.dumps(prefs).encode()],
            ),
            self.assertRaises(resolution.ResolutionDenied),
        ):
            resolution.tailnet_closed("pilot.example.ts.net", "100.100.100.100")

    def test_versioned_resolver_inspects_then_marks_unknown_outcome(self):
        run = "a" * 32
        token = "b" * 32
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "demo.sqlite"
            with sqlite3.connect(db_path) as db:
                db.execute(
                    "CREATE TABLE runs (id TEXT PRIMARY KEY,status TEXT,runtime_profile TEXT,"
                    "runtime_resource TEXT,runtime_token TEXT,finished REAL,result TEXT,error TEXT)"
                )
                db.execute(
                    "CREATE TABLE events (id INTEGER PRIMARY KEY,at REAL,kind TEXT,"
                    "actor TEXT,resource TEXT,outcome TEXT)"
                )
                db.execute(
                    "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)",
                    (
                        run,
                        "uncertain",
                        "reference-linux",
                        "prism-m23-run-" + run,
                        token,
                        123.0,
                        None,
                        "Previous watchdog evidence",
                    ),
                )
            db_path.chmod(0o600)
            lock = db_path.with_name("server.lock")
            lock.touch(mode=0o600)
            with ExitStack() as stack:
                stack.enter_context(patch.object(resolution, "DB", db_path))
                stack.enter_context(patch.object(resolution, "OWNER_UID", os.getuid()))
                stack.enter_context(
                    patch.object(resolution.os, "geteuid", return_value=0)
                )
                stack.enter_context(
                    patch.object(
                        resolution,
                        "installed_units_pinned",
                        return_value=("pilot.example.ts.net", "100.100.100.100"),
                    )
                )
                for name in (
                    "service_stopped",
                    "tailnet_closed",
                    "watchdog_healthy",
                    "namespace_empty",
                    "no_matching_process",
                ):
                    stack.enter_context(patch.object(resolution, name))
                inspected = resolution.execute("inspect", run)
                self.assertFalse(inspected["changed"])
                resolved = resolution.execute("resolve", run)
                self.assertEqual(resolved["execution_outcome"], "unknown")
            with sqlite3.connect(db_path) as db:
                self.assertEqual(
                    db.execute("SELECT status,result,error FROM runs").fetchone(),
                    ("failed", None, resolution.ERROR),
                )


if __name__ == "__main__":
    unittest.main()
