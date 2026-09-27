"""Local safety gates for the host-only observed watchdog check."""

import importlib.util
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/m2-observed-watchdog-check.py"
SPEC = importlib.util.spec_from_file_location("observed_watchdog_check", SCRIPT)
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class ObservedWatchdogCheckTests(unittest.TestCase):
    def test_source_intermediate_directory_allows_root_owned_0755_only_if_not_writable(
        self,
    ):
        self.assertTrue(
            CHECK.source_directory_is_safe(
                SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
            )
        )
        self.assertFalse(
            CHECK.source_directory_is_safe(
                SimpleNamespace(st_mode=stat.S_IFDIR | 0o775, st_uid=0)
            )
        )
        self.assertFalse(
            CHECK.source_directory_is_safe(
                SimpleNamespace(st_mode=stat.S_IFDIR | 0o777, st_uid=1000)
            )
        )

    @staticmethod
    def fixed_units():
        release = CHECK.RELEASE_ROOT / ("service-" + CHECK.V7_TRANSFER_SHA256)
        marker = (
            CHECK.RELEASE_ROOT.parent
            / "service-updates"
            / CHECK.V7_TRANSFER_SHA256
            / "installed.json"
        )
        lifecycle = (
            "/usr/bin/python3 /usr/local/libexec/prism-identity-service-lifecycle.py "
        )
        helper = "/usr/bin/python3 /usr/local/libexec/prism-identity-service-guest.py "
        common = (
            f"--installed-record {marker} --bundle-sha256 {CHECK.SOURCE_ARCHIVE_SHA256} "
            + "--run-id "
            + "a" * 32
        )
        service = [
            "User=root",
            "BindsTo=tailscaled.service prism-identity-host-watchdog.service",
            "ExecStartPre=" + lifecycle + "prepare " + common,
            "ExecStartPre=" + lifecycle + "watchdog-ready " + common,
            "ExecStartPre=" + helper + "preflight --release " + str(release),
            "ExecStart=" + lifecycle + "serve " + common,
            "ExecStartPost=" + lifecycle + "open " + common,
            "ExecStopPost=" + lifecycle + "close --hostname test",
        ]
        watcher = [
            "User=root",
            "ExecStartPre="
            + lifecycle
            + "watchdog-prepare "
            + common
            + " --release "
            + str(release)
            + " --db /var/lib/prism-identity/service-state/demo.sqlite",
            "ExecStart="
            + lifecycle
            + "watchdog-serve "
            + common
            + " --release "
            + str(release)
            + " --db /var/lib/prism-identity/service-state/demo.sqlite",
            "ExecStopPost=" + lifecycle + "close --hostname test",
        ]
        installed = {
            "source_tar_sha256": CHECK.SOURCE_ARCHIVE_SHA256,
            "transfer_tar_sha256": CHECK.V7_TRANSFER_SHA256,
            "base_bundle_sha256": "b" * 64,
            "base_run_id": "a" * 32,
        }
        return service, watcher, release, marker, installed

    def test_unsupported_host_exits_before_release_or_runtime_access(self):
        with (
            patch.object(CHECK.platform, "system", return_value="Darwin"),
            patch.object(CHECK, "verified_modules", side_effect=AssertionError),
        ):
            report = CHECK.run_check(Path("/nonexistent"))
        self.assertFalse(report["passed"])
        self.assertEqual(report["error_kind"], "UnsupportedHost")
        self.assertFalse(report["checks"]["installed_v7_source_verified"])

    def test_release_must_match_exact_v7_transfer_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = root / "releases" / ("service-" + "a" * 64)
            release.mkdir(parents=True)
            manifest = root / "service-updates" / ("a" * 64) / "manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                '{"kind":"prism_service_update_v6","release_name":"'
                + release.name
                + '","transfer_tar_sha256":"'
                + "a" * 64
                + '","file_sha256":{}}',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                CHECK.verified_modules(release)
        wrong = CHECK.RELEASE_ROOT / ("service-" + "a" * 64)
        with self.assertRaises(ValueError):
            CHECK.verified_modules(wrong)

    def test_inactive_unit_cgroup_must_be_drained(self):
        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "is_dir", return_value=True),
            patch.object(Path, "is_symlink", return_value=False),
            patch.object(Path, "rglob", return_value=[]),
            patch.object(Path, "read_text", return_value="123\n"),
        ):
            self.assertFalse(
                CHECK.cgroup_tree_drained("prism-identity-service.service")
            )
        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "is_dir", return_value=True),
            patch.object(Path, "is_symlink", return_value=False),
            patch.object(Path, "rglob", return_value=[]),
            patch.object(Path, "read_text", return_value=""),
        ):
            self.assertTrue(CHECK.cgroup_tree_drained("prism-identity-service.service"))

    def test_unit_preflight_denies_nonzero_main_pid(self):
        release = CHECK.RELEASE_ROOT / ("service-" + CHECK.V7_TRANSFER_SHA256)
        lifecycle = (
            "/usr/bin/python3 /usr/local/libexec/prism-identity-service-lifecycle.py "
        )
        helper = "/usr/bin/python3 /usr/local/libexec/prism-identity-service-guest.py "
        units = {
            "prism-identity-service.service": (
                "BindsTo=tailscaled.service prism-identity-host-watchdog.service\n"
                + "ExecStartPre="
                + lifecycle
                + "watchdog-ready x\n"
                + "ExecStartPre="
                + helper
                + "preflight --release "
                + str(release)
                + "\n"
                + "ExecStart="
                + lifecycle
                + "serve x\n"
            ),
            "prism-identity-host-watchdog.service": (
                "ExecStartPre="
                + lifecycle
                + "watchdog-prepare --release "
                + str(release)
                + "\n"
                + "ExecStart="
                + lifecycle
                + "watchdog-serve --release "
                + str(release)
                + "\n"
                + "ExecStopPost="
                + lifecycle
                + "close x\n"
            ),
        }

        def read_unit(path, *_args, **_kwargs):
            return units[path.name]

        def systemctl(argv, **_kwargs):
            field = argv[2].removeprefix("--property=")
            values = {
                "LoadState": "loaded",
                "FragmentPath": "/etc/systemd/system/" + argv[-1],
                "DropInPaths": "",
                "NeedDaemonReload": "no",
                "ActiveState": "inactive",
                "ControlGroup": "",
                "MainPID": "7",
                "ControlPID": "0",
            }
            return SimpleNamespace(stdout=values[field].encode())

        with (
            patch.object(
                Path,
                "lstat",
                return_value=SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_uid=0),
            ),
            patch.object(Path, "read_text", autospec=True, side_effect=read_unit),
            patch.object(CHECK.subprocess, "run", side_effect=systemctl),
            patch.object(CHECK, "cgroup_tree_drained", return_value=True),
        ):
            self.assertFalse(CHECK.live_units_inactive(release))

    def test_unit_identity_denies_duplicate_command_and_release_suffix(self):
        service, watcher, release, marker, installed = self.fixed_units()
        self.assertTrue(
            CHECK.unit_identity(service, watcher, release, marker, installed)
        )
        self.assertFalse(
            CHECK.unit_identity(
                service + [service[2]], watcher, release, marker, installed
            )
        )
        wrong_bind = [line.replace("tailscaled.service ", "") for line in service]
        self.assertFalse(
            CHECK.unit_identity(wrong_bind, watcher, release, marker, installed)
        )
        bad = [line.replace(str(release), str(release) + "-suffix") for line in watcher]
        self.assertFalse(CHECK.unit_identity(service, bad, release, marker, installed))

    def test_expiry_requires_still_running_exact_owned_client(self):
        resource = "prism-m23-run-" + "a" * 32
        token = "b" * 32
        client = [CHECK.Client(123, 456, 7)]

        class Runtime:
            def __init__(self, state):
                self.state = state

            def inspect_owned(self, _resource, _token):
                return {"State": self.state}

        with (
            patch.object(CHECK, "client_live", return_value=True),
        ):
            self.assertTrue(
                CHECK.expiry_ready(
                    Runtime({"Status": "running"}), resource, token, client
                )
            )
            self.assertFalse(
                CHECK.expiry_ready(
                    Runtime({"Status": "exited"}), resource, token, client
                )
            )
            self.assertFalse(CHECK.expiry_ready(Runtime({}), resource, token, []))
        with (
            patch.object(CHECK, "client_live", return_value=False),
        ):
            self.assertFalse(
                CHECK.expiry_ready(
                    Runtime({"Status": "running"}), resource, token, client
                )
            )

    def test_worker_loss_grace_must_precede_current_lease_deadline(self):
        active = {"worker_dead_at": 10.0, "deadline": 13.0}
        watcher = type("Watcher", (), {"active": active})()
        self.assertFalse(CHECK.worker_loss_ready(watcher, active, 10.0, 12.5))
        self.assertTrue(CHECK.worker_loss_ready(watcher, active, 10.0, 12.6))
        self.assertFalse(CHECK.worker_loss_ready(watcher, active, 10.0, 13.0))
        watcher.active = None
        self.assertFalse(CHECK.worker_loss_ready(watcher, active, 10.0, 12.6))

    def test_cleanup_reconciles_exact_resource_even_if_client_scan_fails(self):
        class Runtime:
            def __init__(self):
                self.reconciled = 0

            def reconcile(self, *_args, **_kwargs):
                self.reconciled += 1

            def inspect_owned(self, *_args):
                return None

            def all_resources(self):
                return []

        runtime = Runtime()
        with (
            patch.object(CHECK, "LATE_SECONDS", 0),
            patch.object(
                CHECK,
                "discover_exact_clients",
                side_effect=[OSError("scan failed"), []],
            ),
        ):
            self.assertFalse(CHECK.cleanup_exact(runtime, "resource", "token", []))
        self.assertEqual(runtime.reconciled, 1)

    def test_client_signal_uses_validated_pidfd(self):
        client = CHECK.Client(123, 456, 7)
        with (
            patch.object(CHECK, "client_live", return_value=True),
            patch.object(CHECK.signal, "pidfd_send_signal", create=True) as send,
        ):
            self.assertTrue(CHECK.kill_client(client, "resource", "token"))
            send.assert_called_once_with(7, CHECK.signal.SIGKILL, None, 0)
        with (
            patch.object(CHECK, "client_live", return_value=False),
            patch.object(CHECK.signal, "pidfd_send_signal", create=True) as send,
        ):
            self.assertFalse(CHECK.kill_client(client, "resource", "token"))
            send.assert_not_called()

    def test_pidfd_preflight_signals_zero_and_closes_on_failure(self):
        with (
            patch.object(CHECK.os, "pidfd_open", return_value=9, create=True),
            patch.object(CHECK.signal, "pidfd_send_signal", create=True) as send,
            patch.object(CHECK.os, "close") as close,
        ):
            self.assertTrue(CHECK.pidfd_signal_ready())
            send.assert_called_once_with(9, 0, None, 0)
            close.assert_called_once_with(9)
        with (
            patch.object(CHECK.os, "pidfd_open", return_value=9, create=True),
            patch.object(
                CHECK.signal,
                "pidfd_send_signal",
                side_effect=OSError("unsupported"),
                create=True,
            ),
            patch.object(CHECK.os, "close") as close,
        ):
            with self.assertRaises(OSError):
                CHECK.pidfd_signal_ready()
            close.assert_called_once_with(9)

    def test_client_must_name_exact_owned_resource_and_token(self):
        resource = "prism-m23-run-" + "a" * 32
        token = "b" * 32
        argv = [
            "/usr/local/bin/nerdctl",
            "run",
            "--name",
            resource,
            "--label",
            "org.prism.reference-run=" + token,
        ]
        with patch.object(Path, "read_bytes", return_value="\0".join(argv).encode()):
            self.assertTrue(CHECK.exact_client(123, resource, token))
            self.assertFalse(CHECK.exact_client(123, resource, "c" * 32))
            self.assertFalse(CHECK.exact_client(123, resource + "x", token))


if __name__ == "__main__":
    unittest.main()
