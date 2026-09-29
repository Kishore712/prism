"""Single-use offline recovery state and the final service handoff."""

import importlib.util
import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


recovery = load("prism_recovery_test", "src/prism/recovery.py")
life = load(
    "prism_recovery_lifecycle_test", "scripts/gcp/identity-service-lifecycle.py"
)
EVENT_ID = "a" * 32
INVOCATION = "b" * 32


class DurableEpisodeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.episode = self.root / "episode"
        self.episode.write_text(
            json.dumps(
                {
                    "version": 1,
                    "event_id": EVENT_ID,
                    "category": "offline",
                }
            )
            + "\n"
        )
        patches = (
            patch.object(recovery, "ROOT", self.root),
            patch.object(recovery, "EPISODE", self.episode),
            patch.object(recovery, "AUDIT", self.root / "audit.jsonl"),
            patch.object(recovery, "directory"),
            patch.object(recovery, "_private_file"),
            patch.object(recovery, "event_lock", side_effect=lambda: nullcontext()),
        )
        for context in patches:
            context.start()
            self.addCleanup(context.stop)

    def test_one_invocation_claims_and_enters_once(self):
        recovery.transition(None, "consumed")
        recovery.transition("consumed", "ready")
        recovery.transition("ready", "attempted", INVOCATION)
        with self.assertRaises(recovery.RecoveryDenied):
            recovery.transition("ready", "attempted", "c" * 32)
        with self.assertRaises(recovery.RecoveryDenied):
            recovery.transition("attempted", "entered", "c" * 32)
        recovery.transition("attempted", "entered", INVOCATION)
        with self.assertRaises(recovery.RecoveryDenied):
            recovery.transition("attempted", "entered", INVOCATION)
        self.assertEqual(recovery.episode()["state"], "entered")

    def test_failed_attempt_cannot_replay(self):
        recovery.transition(None, "consumed")
        recovery.transition("consumed", "ready")
        recovery.transition("ready", "attempted", INVOCATION)
        recovery.transition("attempted", "failed", INVOCATION)
        with self.assertRaises(recovery.RecoveryDenied):
            recovery.transition("ready", "attempted", "c" * 32)

    def test_invalid_or_partial_event_denies(self):
        self.episode.write_text('{"version":1')
        with self.assertRaises(recovery.RecoveryDenied):
            recovery.episode()
        self.episode.write_text(
            json.dumps(
                {
                    "version": 1,
                    "event_id": EVENT_ID,
                    "category": "probe",
                }
            )
        )
        with self.assertRaises(recovery.RecoveryDenied):
            recovery.episode()

    def test_archive_fsync_failure_is_reported(self):
        recovery.transition(None, "consumed")
        recovery.transition("consumed", "ready")
        recovery.transition("ready", "attempted", INVOCATION)
        recovery.transition("attempted", "entered", INVOCATION)
        with (
            patch.object(
                recovery, "_sync_directory", side_effect=[OSError("fsync"), None]
            ),
            self.assertRaises(OSError),
        ):
            recovery.archive(EVENT_ID)
        self.assertTrue(self.episode.exists())
        self.assertFalse((self.root / ("archived-" + EVENT_ID)).exists())

    def test_stale_ready_denies_after_controller_exit_or_reboot(self):
        boot = "01234567-89ab-cdef-0123-456789abcdef"
        recovery.transition(None, "consumed")
        with (
            patch.object(recovery, "_boot_id", return_value=boot),
            patch.object(recovery, "_process_start", return_value=100),
            patch.object(recovery.time, "monotonic_ns", return_value=1_000_000_000),
        ):
            recovery.arm_ready("c" * 32)
        with (
            patch.object(recovery, "_boot_id", return_value=boot),
            patch.object(recovery, "_process_start", side_effect=FileNotFoundError),
            patch.object(recovery.time, "monotonic_ns", return_value=2_000_000_000),
            self.assertRaises(FileNotFoundError),
        ):
            recovery.claim_ready(INVOCATION, "c" * 32)
        with (
            patch.object(
                recovery,
                "_boot_id",
                return_value="fedcba98-7654-3210-fedc-ba9876543210",
            ),
            patch.object(recovery, "_process_start", return_value=100),
            self.assertRaises(recovery.RecoveryDenied),
        ):
            recovery.claim_ready(INVOCATION, "c" * 32)
        with (
            patch.object(recovery, "_boot_id", return_value=boot),
            patch.object(recovery, "_process_start", return_value=100),
            patch.object(recovery.time, "monotonic_ns", return_value=32_000_000_000),
            self.assertRaises(recovery.RecoveryDenied),
        ):
            recovery.claim_ready(INVOCATION, "c" * 32)
        self.assertEqual(recovery.episode()["state"], "ready")


class LifecycleGateTests(unittest.TestCase):
    def pins(self):
        return (
            "pilot.example.ts.net",
            "100.100.100.100",
            "/record",
            "a" * 64,
            "b" * 32,
        )

    def test_claimed_manual_or_joined_start_cannot_claim_again(self):
        event = {
            "event_id": EVENT_ID,
            "state": "ready",
            "controller_pid": 123,
            "controller_invocation": "c" * 32,
        }
        helper = SimpleNamespace(
            directory=Mock(),
            EPISODE=SimpleNamespace(exists=lambda: True, is_symlink=lambda: False),
            episode=Mock(side_effect=lambda: event.copy()),
            claim_ready=Mock(),
            audit=Mock(),
        )

        def controller_run(*args, **_kwargs):
            value = {
                "ActiveState": "activating",
                "ControlPID": "123",
                "InvocationID": "c" * 32,
            }[args[2].split("=", 1)[1]]
            return SimpleNamespace(stdout=value + "\n")

        with (
            patch.object(life, "_recovery_pins", return_value="/release"),
            patch.object(life, "recovery_module", return_value=helper),
            patch.object(life, "run", side_effect=controller_run),
            patch.dict(life.os.environ, {"INVOCATION_ID": INVOCATION}),
        ):
            life.recovery_claim(*self.pins())
            helper.claim_ready.assert_called_once_with(INVOCATION, "c" * 32)
            event["state"] = "attempted"
            with self.assertRaises(ValueError):
                life.recovery_claim(*self.pins())

    def test_manual_start_during_watchdog_handoff_cannot_open(self):
        event = {"event_id": EVENT_ID, "state": "consumed"}
        helper = SimpleNamespace(
            directory=Mock(),
            EPISODE=SimpleNamespace(exists=lambda: True, is_symlink=lambda: False),
            episode=Mock(return_value=event),
            transition=Mock(),
            audit=Mock(),
        )
        with (
            patch.object(life, "_recovery_pins", return_value="/release"),
            patch.object(life, "recovery_module", return_value=helper),
            patch.dict(life.os.environ, {"INVOCATION_ID": INVOCATION}),
        ):
            with self.assertRaises(ValueError):
                life.recovery_claim(*self.pins())
            helper.transition.assert_not_called()

    def test_final_gate_checks_before_entering(self):
        event = {
            "event_id": EVENT_ID,
            "state": "attempted",
            "invocation_id": INVOCATION,
        }
        helper = SimpleNamespace(
            directory=Mock(),
            EPISODE=SimpleNamespace(exists=lambda: True, is_symlink=lambda: False),
            episode=Mock(return_value=event),
            transition=Mock(),
            audit=Mock(),
            workload_clear=Mock(),
        )
        with (
            patch.object(life, "_recovery_pins", return_value="/release"),
            patch.object(life, "recovery_module", return_value=helper),
            patch.object(life, "_service_state") as service,
            patch.object(life, "watchdog_ready") as watchdog,
            patch.object(life, "tailnet_state") as tailnet,
            patch.dict(life.os.environ, {"INVOCATION_ID": INVOCATION}),
        ):
            life.recovery_final(*self.pins())
            service.assert_called_once_with(starting_pid=life.os.getpid())
            watchdog.assert_called_once()
            self.assertEqual(tailnet.call_count, 2)
            helper.workload_clear.assert_called_once()
            helper.transition.assert_called_once_with(
                "attempted", "entered", INVOCATION
            )

    def test_final_gate_denies_resolver_lock_or_bad_workload(self):
        event = {
            "event_id": EVENT_ID,
            "state": "attempted",
            "invocation_id": INVOCATION,
        }
        helper = SimpleNamespace(
            directory=Mock(),
            EPISODE=SimpleNamespace(exists=lambda: True, is_symlink=lambda: False),
            episode=Mock(return_value=event),
            transition=Mock(),
            audit=Mock(),
            workload_clear=Mock(side_effect=ValueError("pending")),
        )
        with (
            patch.object(life, "_recovery_pins", return_value="/release"),
            patch.object(life, "recovery_module", return_value=helper),
            patch.object(life, "_service_state"),
            patch.object(life, "watchdog_ready"),
            patch.object(life, "tailnet_state"),
            patch.dict(life.os.environ, {"INVOCATION_ID": INVOCATION}),
        ):
            with self.assertRaises(ValueError):
                life.recovery_final(*self.pins())
            helper.transition.assert_not_called()

    def test_post_start_failure_stops_and_confirms_close(self):
        event = {"event_id": EVENT_ID, "state": "entered", "invocation_id": INVOCATION}
        helper = SimpleNamespace(
            episode=Mock(return_value=event), transition=Mock(), audit=Mock()
        )
        with (
            patch.object(life, "run") as command,
            patch.object(life, "close") as close,
            patch.object(life, "_service_state") as service,
            patch.object(life, "tailnet_state") as tailnet,
        ):
            life._recovery_failed(*self.pins()[:2], helper, EVENT_ID)
            helper.transition.assert_called_once_with("entered", "failed", INVOCATION)
            command.assert_called_once_with(
                "systemctl", "stop", life.UNIT.name, timeout=45
            )
            close.assert_called_once()
            service.assert_called_once()
            tailnet.assert_called_once()

    def test_lost_latch_after_archive_fault_invokes_emergency_block(self):
        helper = SimpleNamespace(episode=Mock(side_effect=FileNotFoundError("lost")))
        with (
            patch.object(life, "run"),
            patch.object(life, "close"),
            patch.object(life, "_service_state"),
            patch.object(life, "tailnet_state"),
            patch.object(life, "emergency_block") as emergency,
            self.assertRaises(FileNotFoundError),
        ):
            life._recovery_failed(*self.pins()[:2], helper, EVENT_ID)
        emergency.assert_called_once()

    def test_controller_orders_full_attempt_once(self):
        state = {"event_id": EVENT_ID}
        order = []

        def transition(expected, target, invocation=None):
            self.assertEqual(state.get("state"), expected)
            state["state"] = target
            order.append(target)

        helper = SimpleNamespace(
            episode=Mock(side_effect=lambda: state.copy()),
            audit=Mock(side_effect=lambda gate, _: order.append(gate)),
            service_lock=Mock(side_effect=lambda: nullcontext()),
            workload_clear=Mock(side_effect=lambda: order.append("workload")),
            transition=Mock(side_effect=transition),
            arm_ready=Mock(side_effect=lambda _: transition("consumed", "ready")),
            archive=Mock(side_effect=lambda _: order.append("archive")),
        )
        clock = SimpleNamespace(value=0)
        fake_time = SimpleNamespace(
            monotonic=lambda: clock.value,
            sleep=lambda seconds: setattr(clock, "value", clock.value + seconds),
        )
        with (
            patch.object(life, "_recovery_pins", return_value="/release"),
            patch.object(life, "recovery_module", return_value=helper),
            patch.object(life, "_watchdog_failure_offline"),
            patch.object(
                life, "_service_state", side_effect=lambda **_: order.append("stopped")
            ),
            patch.object(life, "guard_ready"),
            patch.object(
                life,
                "tailnet_state",
                side_effect=lambda *_a, **_k: order.append("tailnet"),
            ),
            patch.object(
                life,
                "watchdog_ready",
                side_effect=lambda *_a: order.append("watchdog-health"),
            ),
            patch.object(
                life,
                "_recovery_readiness",
                side_effect=lambda *_a: order.append("ready"),
            ),
            patch.object(
                life, "run", side_effect=lambda *a, **_k: order.append("start-" + a[2])
            ),
            patch.object(life, "time", fake_time),
        ):
            life.recovery_run(*self.pins())
        self.assertGreaterEqual(order.count("tailnet"), 63)
        self.assertEqual(order.count("workload"), 2)
        self.assertLess(
            order.index("consumed"), order.index("start-" + life.WATCHDOG_UNIT.name)
        )
        self.assertLess(order.index("ready"), order.index("start-" + life.UNIT.name))
        self.assertLess(
            order.index("rechecked"), order.index("start-" + life.UNIT.name)
        )
        self.assertLess(order.index("ready"), order.index("archive"))

    def test_controller_denies_lock_contention_before_unit_start(self):
        helper = SimpleNamespace(
            episode=Mock(return_value={"event_id": EVENT_ID}),
            audit=Mock(),
            service_lock=Mock(side_effect=RuntimeError("resolver holds lock")),
        )
        clock = SimpleNamespace(value=0)
        fake_time = SimpleNamespace(
            monotonic=lambda: clock.value,
            sleep=lambda seconds: setattr(clock, "value", clock.value + seconds),
        )
        with (
            patch.object(life, "_recovery_pins", return_value="/release"),
            patch.object(life, "recovery_module", return_value=helper),
            patch.object(life, "_watchdog_failure_offline"),
            patch.object(life, "_service_state"),
            patch.object(life, "guard_ready"),
            patch.object(life, "tailnet_state"),
            patch.object(life, "run") as command,
            patch.object(life, "time", fake_time),
        ):
            with self.assertRaises(RuntimeError):
                life.recovery_run(*self.pins())
            command.assert_not_called()

    def test_controller_post_start_audit_failure_invokes_cleanup(self):
        state = {"event_id": EVENT_ID}

        def audit(gate, _):
            if gate == "verified":
                raise OSError("audit")

        helper = SimpleNamespace(
            episode=Mock(side_effect=lambda: state.copy()),
            audit=Mock(side_effect=audit),
            service_lock=Mock(side_effect=lambda: nullcontext()),
            workload_clear=Mock(),
            transition=Mock(
                side_effect=lambda _old, target: state.update(state=target)
            ),
            arm_ready=Mock(side_effect=lambda _: state.update(state="ready")),
            archive=Mock(),
        )
        clock = SimpleNamespace(value=0)
        fake_time = SimpleNamespace(
            monotonic=lambda: clock.value,
            sleep=lambda seconds: setattr(clock, "value", clock.value + seconds),
        )
        with (
            patch.object(life, "_recovery_pins", return_value="/release"),
            patch.object(life, "recovery_module", return_value=helper),
            patch.object(life, "_watchdog_failure_offline"),
            patch.object(life, "_service_state"),
            patch.object(life, "guard_ready"),
            patch.object(life, "tailnet_state"),
            patch.object(life, "watchdog_ready"),
            patch.object(life, "_recovery_readiness"),
            patch.object(life, "run"),
            patch.object(life, "_recovery_failed") as cleanup,
            patch.object(life, "time", fake_time),
        ):
            with self.assertRaises(OSError):
                life.recovery_run(*self.pins())
            cleanup.assert_called_once()
            helper.archive.assert_not_called()

    def test_systemd_failure_hook_stops_after_controller_kill(self):
        helper = SimpleNamespace(
            EPISODE=SimpleNamespace(exists=lambda: False, is_symlink=lambda: False),
        )
        with (
            patch.dict(life.os.environ, {"SERVICE_RESULT": "signal"}),
            patch.object(life, "_recovery_pins", return_value="/release"),
            patch.object(life, "recovery_module", return_value=helper),
            patch.object(life, "run") as command,
            patch.object(life, "close") as close,
            patch.object(life, "_service_state") as service,
            patch.object(life, "tailnet_state") as tailnet,
            patch.object(life, "emergency_block") as emergency,
        ):
            with self.assertRaises(ValueError):
                life.recovery_stop_post(*self.pins())
            self.assertEqual(command.call_count, 2)
            self.assertEqual(close.call_count, 2)
            self.assertEqual(service.call_count, 2)
            self.assertEqual(tailnet.call_count, 2)
            emergency.assert_called_once()

    def test_early_sample_failure_is_tombstoned_by_systemd_hook(self):
        state = {"event_id": EVENT_ID}
        helper = SimpleNamespace(
            EPISODE=SimpleNamespace(exists=lambda: True, is_symlink=lambda: False),
            episode=Mock(side_effect=lambda: state.copy()),
            transition=Mock(
                side_effect=lambda _old, target, _inv=None: state.update(state=target)
            ),
            audit=Mock(),
        )
        with (
            patch.dict(life.os.environ, {"SERVICE_RESULT": "exit-code"}),
            patch.object(life, "_recovery_pins", return_value="/release"),
            patch.object(life, "recovery_module", return_value=helper),
            patch.object(life, "run"),
            patch.object(life, "close"),
            patch.object(life, "_service_state"),
            patch.object(life, "tailnet_state"),
        ):
            life.recovery_stop_post(*self.pins())
        helper.transition.assert_called_once_with(None, "failed", None)
        self.assertEqual(state["state"], "failed")
        with (
            patch.object(life, "_recovery_pins", return_value="/release"),
            patch.object(life, "recovery_module", return_value=helper),
            patch.object(life, "_watchdog_failure_offline"),
            self.assertRaises(ValueError),
        ):
            life.recovery_run(*self.pins())

    def test_operator_can_abandon_only_exact_failed_event(self):
        helper = SimpleNamespace(
            service_lock=Mock(side_effect=lambda: nullcontext()),
            workload_clear=Mock(),
            episode=Mock(return_value={"event_id": EVENT_ID, "state": "failed"}),
            audit=Mock(),
            abandon=Mock(),
        )

        def status(*argv, **_kwargs):
            field = argv[2].split("=", 1)[1]
            return SimpleNamespace(
                stdout={"ActiveState": "inactive", "ControlPID": "0", "MainPID": "0"}[
                    field
                ]
            )

        with (
            patch.object(life, "_recovery_pins", return_value="/release"),
            patch.object(life, "recovery_module", return_value=helper),
            patch.object(life, "run", side_effect=status),
            patch.object(life, "_service_state"),
            patch.object(life, "guard_ready"),
            patch.object(life, "tailnet_state"),
        ):
            with self.assertRaises(ValueError):
                life.recovery_abandon(*self.pins(), "c" * 32)
            helper.abandon.assert_not_called()
            life.recovery_abandon(*self.pins(), EVENT_ID)
            helper.audit.assert_called_once_with("abandon-intent", EVENT_ID)
            helper.abandon.assert_called_once_with(EVENT_ID)


if __name__ == "__main__":
    unittest.main()
