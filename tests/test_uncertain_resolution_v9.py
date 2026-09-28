"""Pins for the no-restart uncertain-run resolver release."""

import hashlib
import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE = ROOT / "scripts/gcp/identity-service-lifecycle.py"
V8 = ROOT / "scripts/gcp/identity-uncertain-resolution-v8.py"
V9 = ROOT / "scripts/gcp/identity-uncertain-resolution-v9.py"


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


life = module(LIFECYCLE, "identity_lifecycle_v9_test")
resolution = module(V9, "uncertain_resolution_v9_test")


class UncertainResolutionV9Tests(unittest.TestCase):
    def test_v9_changes_only_watchdog_guard_and_current_release_pins(self):
        old = V8.read_text()
        expected = (
            old.replace(
                'setting(watchdog, "Restart", "on-failure")',
                'setting(watchdog, "Restart", "no")',
            )
            .replace(
                'LIFECYCLE_SHA256 = "79e3db10b3683bc65292f449b88798d4edb8674a6f8ea63c8cd08d8cd64c9c0f"',
                f'LIFECYCLE_SHA256 = "{hashlib.sha256(LIFECYCLE.read_bytes()).hexdigest()}"',
            )
            .replace(
                'TRANSFER = "e11ea7a88a2b029f73e2374d47f271af178ff0604febbe9a47e1b4c9259d4f27"',
                'TRANSFER = "47add41b5778bcd9d2a5f6408df2c8312325474952047a5b12b362005dfce7f2"',
            )
            .replace(
                'SOURCE = "fee499e336567fd4c1f9a564c3fc2983fd520eb18d33e191d5ae29b267e1e16d"',
                'SOURCE = "8213a7cae3bfa6a242e1d5fcd3afece9e3526894c27e633f3f29963b97ccd66f"',
            )
        )
        self.assertEqual(V9.read_text(), expected)

    def test_generated_units_accept_no_restart_and_reject_auto_restart(self):
        host, ip, run_id = "private.example.ts.net", "100.100.100.100", "a" * 32
        with patch.object(life, "LIFECYCLE", str(LIFECYCLE)):
            service = life.unit_text(
                host,
                ip,
                resolution.UPDATE,
                resolution.RELEASE,
                resolution.SOURCE,
                run_id,
                watchdog=True,
            ).splitlines()
            watchdog = life.watchdog_unit_text(
                host,
                ip,
                resolution.UPDATE,
                resolution.RELEASE,
                resolution.SOURCE,
                run_id,
            ).splitlines()
        units = {resolution.SERVICE: service, resolution.WATCHDOG: watchdog}
        with (
            patch.object(resolution, "LIFECYCLE", str(LIFECYCLE)),
            patch.object(resolution, "OWNER_UID", os.getuid()),
            patch.object(resolution, "unit_pin", side_effect=units.__getitem__),
            patch.object(
                resolution,
                "systemd",
                return_value="tailscaled.service " + resolution.WATCHDOG,
            ),
        ):
            self.assertEqual(resolution.installed_units_pinned(), (host, ip))
            watchdog[watchdog.index("Restart=no")] = "Restart=on-failure"
            with self.assertRaisesRegex(
                resolution.ResolutionDenied, "private service guard"
            ):
                resolution.installed_units_pinned()


if __name__ == "__main__":
    unittest.main()
