"""Offline ordering and fail-closed checks for the private service unit."""

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts/gcp/identity-service-lifecycle.py"
)
SPEC = importlib.util.spec_from_file_location("identity_service_lifecycle", SCRIPT)
life = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(life)
GUEST_SCRIPT = SCRIPT.with_name("identity-service-guest.py")
GUEST_SPEC = importlib.util.spec_from_file_location(
    "identity_service_guest_for_lifecycle", GUEST_SCRIPT
)
guest = importlib.util.module_from_spec(GUEST_SPEC)
GUEST_SPEC.loader.exec_module(guest)


class IdentityLifecycleTests(unittest.TestCase):
    def test_render_requires_reviewed_fixture_before_pinning_project(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project = root / "project"
            shutil.copytree(
                Path(__file__).resolve().parents[1] / "examples/document-handoff",
                project,
            )
            for path in project.rglob("*"):
                path.chmod(0o700 if path.is_dir() else 0o600)
            project.chmod(0o700)
            output = root / "service.unit"
            args = SimpleNamespace(
                bind_host="100.100.100.100",
                installed_record=None,
                model_budget_cents=0,
                enable_project=True,
                output=str(output),
            )
            with (
                patch.object(guest, "PROJECT_ROOT", project),
                patch.object(
                    guest, "PROJECT_MANIFEST", project / ".prism-project.json"
                ),
                patch.object(guest, "PROJECT_OWNER", os.getuid()),
                patch.object(life, "UNIT", output),
                patch.object(life.os, "geteuid", return_value=0),
                patch.object(life, "fixed_host", return_value="pilot.example.ts.net"),
                patch.object(
                    life,
                    "installation",
                    return_value=("/fixed/release", "a" * 64, "b" * 32),
                ),
                patch.object(
                    life, "project_inventory", side_effect=guest.project_inventory
                ),
            ):
                self.assertEqual(
                    guest.project_inventory(), life.PROJECT_INVENTORY_SHA256
                )
                (project / "unlisted-canary.txt").write_text("substituted\n")
                with self.assertRaisesRegex(ValueError, "inventory changed"):
                    life.render(args)
                self.assertFalse(output.exists())

    def test_installed_manifest_ledger_and_all_sources_are_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            oneboot = base / "oneboot"
            oneboot.mkdir(mode=0o700)
            bundle, run_id = "a" * 64, "b" * 32
            release = base / "app-releases" / f"{bundle}-{run_id}"
            release.mkdir(mode=0o700, parents=True)
            hashes = {}
            for name in life.SOURCE_FILES:
                source = release / name
                source.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                source.write_bytes(name.encode())
                source.chmod(0o600)
                hashes[name] = hashlib.sha256(name.encode()).hexdigest()

            def write(name, value):
                path = oneboot / name
                path.write_text(json.dumps(value))
                path.chmod(0o600)

            write("installed.json", {"bundle_sha256": bundle, "run_id": run_id})
            write(
                "manifest.json",
                {
                    "kind": "prism_oneboot_app_v1",
                    "bundle_sha256": bundle,
                    "raw_sha256": bundle,
                    "run_id": run_id,
                    "file_sha256": hashes,
                },
            )
            write("ledger.json", {"run_id": run_id, "next_seq": 6, "complete": True})
            with (
                patch.object(life, "BASE", base),
                patch.object(life, "INSTALLED", oneboot / "installed.json"),
                patch.object(life, "OWNER_UID", os.getuid()),
            ):
                self.assertEqual(
                    life.installation(oneboot / "installed.json", bundle, run_id),
                    (str(release), bundle, run_id),
                )
                changed = release / "src/prism/cli.py"
                changed.write_bytes(b"changed")
                with self.assertRaisesRegex(ValueError, "source hash mismatch"):
                    life.installation(oneboot / "installed.json", bundle, run_id)
                changed.write_bytes(b"src/prism/cli.py")
                hashes.pop("src/prism/cli.py")
                write(
                    "manifest.json",
                    {
                        "kind": "prism_oneboot_app_v1",
                        "bundle_sha256": bundle,
                        "raw_sha256": bundle,
                        "run_id": run_id,
                        "file_sha256": hashes,
                    },
                )
                with self.assertRaisesRegex(ValueError, "manifest differs"):
                    life.installation(oneboot / "installed.json", bundle, run_id)
                hashes["src/prism/cli.py"] = hashlib.sha256(
                    b"src/prism/cli.py"
                ).hexdigest()
                write(
                    "manifest.json",
                    {
                        "kind": "prism_oneboot_app_v1",
                        "bundle_sha256": bundle,
                        "raw_sha256": bundle,
                        "run_id": run_id,
                        "file_sha256": hashes,
                    },
                )
                static = release / "src/prism/static"
                static.rename(release / "src/prism/static-real")
                static.symlink_to(release / "src/prism/static-real")
                with self.assertRaisesRegex(ValueError, "installation parent"):
                    life.installation(oneboot / "installed.json", bundle, run_id)

    def test_unit_is_manual_fixed_release_and_restores_on_stop(self):
        unit = life.unit_text(
            "pilot.example.ts.net",
            "100.100.100.100",
            str(life.INSTALLED),
            "/var/lib/prism/identity-pilot/app-releases/" + "a" * 64 + "-" + "b" * 32,
            "a" * 64,
            "b" * 32,
        )
        self.assertIn("BindsTo=tailscaled.service", unit)
        self.assertIn("Restart=no", unit)
        self.assertIn("TimeoutStartSec=360", unit)
        self.assertIn("TimeoutStopSec=240", unit)
        self.assertNotIn("[Install]", unit)
        self.assertIn("--bundle-sha256 " + "a" * 64, unit)
        self.assertIn("--run-id " + "b" * 32, unit)
        self.assertLess(unit.index("ExecStartPre="), unit.index("ExecStart="))
        self.assertLess(unit.index("ExecStart="), unit.index("ExecStartPost="))
        self.assertIn("ExecStopPost=", unit)
        self.assertIn("--bind-host 100.100.100.100", unit)
        self.assertIn("--model-budget-cents 0", unit)
        self.assertNotIn("--project-sha256", unit)

    def test_project_unit_pins_inventory_across_preflight_prepare_serve_and_open(self):
        pinned = "c" * 64
        unit = life.unit_text(
            "pilot.example.ts.net",
            "100.100.100.100",
            str(life.INSTALLED),
            "/fixed/release",
            "a" * 64,
            "b" * 32,
            0,
            pinned,
        )
        self.assertEqual(unit.count("--project-sha256 " + pinned), 4)
        self.assertIn("ExecStopPost=", unit)
        self.assertNotIn("--project-sha256", unit.split("ExecStopPost=", 1)[1])
        with self.assertRaisesRegex(ValueError, "inventory hash"):
            life.unit_text(
                "pilot.example.ts.net",
                "100.100.100.100",
                str(life.INSTALLED),
                "/fixed/release",
                "a" * 64,
                "b" * 32,
                0,
                "bad",
            )

    def test_project_guard_rejects_changed_inventory_before_tailnet_opens(self):
        pinned = "c" * 64
        with (
            patch.object(life, "installation"),
            patch.object(
                life, "project_inventory", side_effect=ValueError("changed")
            ) as inventory,
            patch.object(life, "guard_ready"),
            patch.object(life, "old_timer_absent"),
            patch.object(life, "tailnet_state"),
            patch.object(life, "run") as command,
            patch.object(life, "close") as close,
            self.assertRaisesRegex(ValueError, "changed"),
        ):
            life.open_service(
                "pilot.example.ts.net",
                "100.100.100.100",
                str(life.INSTALLED),
                "a" * 64,
                "b" * 32,
                pinned,
            )
        inventory.assert_called_once_with(pinned)
        command.assert_not_called()
        close.assert_called_once_with("pilot.example.ts.net", "100.100.100.100")

    def test_model_route_is_explicit_and_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            key = Path(temporary) / "openai-model-key"
            key.write_text("sk-" + "x" * 32)
            key.chmod(0o600)
            with (
                patch.object(life, "MODEL_KEY", str(key)),
                patch.object(life, "OWNER_UID", os.getuid()),
            ):
                self.assertEqual(life.model_budget(100), 100)
                self.assertEqual(life.model_budget(1000), 1000)
                unit = life.unit_text(
                    "pilot.example.ts.net",
                    "100.100.100.100",
                    str(life.INSTALLED),
                    "/fixed/release",
                    "a" * 64,
                    "b" * 32,
                    1000,
                )
                self.assertIn("preflight", unit)
                self.assertEqual(unit.count("--model-budget-cents 1000"), 2)
                self.assertNotIn("sk-" + "x" * 32, unit)
                key.chmod(0o644)
                with self.assertRaises(ValueError):
                    life.model_budget(1000)
                for invalid in (-1, 1, 101, 1001, True):
                    with self.assertRaises(ValueError):
                        life.model_budget(invalid)
                key.unlink()
                with self.assertRaises(FileNotFoundError):
                    life.model_budget(1000)

    def test_open_waits_for_selfcheck_before_unshielding(self):
        events = []

        def record_run(*argv, **kwargs):
            events.append(argv)
            return SimpleNamespace(stdout="", returncode=0)

        with (
            patch.object(life, "guard_ready"),
            patch.object(life, "installation"),
            patch.object(life, "old_timer_absent"),
            patch.object(life, "tailnet_state"),
            patch.object(life, "run", side_effect=record_run),
        ):
            life.open_service(
                "pilot.example.ts.net",
                "100.100.100.100",
                str(life.INSTALLED),
                "a" * 64,
                "b" * 32,
            )
        first_check = next(i for i, item in enumerate(events) if "selfcheck" in item)
        unshield = next(
            i
            for i, item in enumerate(events)
            if item == ("tailscale", "set", "--shields-up=false")
        )
        self.assertLess(first_check, unshield)
        self.assertTrue(any("selfcheck" in item for item in events[unshield + 1 :]))

    def test_failed_local_check_restores_shield(self):
        def fail_check(*argv, **kwargs):
            raise subprocess.CalledProcessError(1, argv)

        with (
            self.assertRaises(subprocess.CalledProcessError),
            patch.object(life, "installation"),
            patch.object(life, "guard_ready"),
            patch.object(life, "old_timer_absent"),
            patch.object(life, "tailnet_state"),
            patch.object(life, "run", side_effect=fail_check),
            patch.object(life.time, "sleep"),
            patch.object(life, "close") as close,
        ):
            life.open_service(
                "pilot.example.ts.net",
                "100.100.100.100",
                str(life.INSTALLED),
                "a" * 64,
                "b" * 32,
            )
        close.assert_called_once_with("pilot.example.ts.net", "100.100.100.100")

    def test_active_old_timer_blocks_service_open(self):
        listing = SimpleNamespace(
            stdout=(
                "prism-identify-restore-" + "a" * 32 + ".timer loaded active waiting\n"
            )
        )
        with (
            self.assertRaisesRegex(ValueError, "prior identify restore timer"),
            patch.object(life, "run", return_value=listing),
            patch.object(
                life.subprocess, "run", return_value=SimpleNamespace(returncode=0)
            ),
        ):
            life.old_timer_absent()

    def test_restore_failure_uses_independent_daemon_block(self):
        with (
            patch.object(
                life, "run", side_effect=subprocess.CalledProcessError(1, "restore")
            ),
            patch.object(life, "emergency_block") as blocked,
        ):
            life.close("pilot.example.ts.net", "100.100.100.100")
        blocked.assert_called_once_with()

    def test_emergency_block_kills_before_nonblocking_stop(self):
        events = []

        def command(*argv, **kwargs):
            events.append(argv)
            if argv[:3] == ("tailscale", "debug", "prefs"):
                raise subprocess.CalledProcessError(1, argv)
            if argv[:2] == ("systemctl", "show"):
                return SimpleNamespace(stdout="0\n")
            return SimpleNamespace(stdout="")

        with patch.object(life, "run", side_effect=command):
            life.emergency_block()
        self.assertEqual(events[0][:2], ("systemctl", "kill"))
        self.assertEqual(events[1][:3], ("systemctl", "stop", "--no-block"))


if __name__ == "__main__":
    unittest.main()
