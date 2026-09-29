"""Compatibility pins for the local offline-close lifecycle increment."""

import hashlib
import importlib.util
import os
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE = ROOT / "scripts/gcp/identity-service-lifecycle.py"
V9 = ROOT / "scripts/gcp/identity-uncertain-resolution-v9.py"
V10 = ROOT / "scripts/gcp/identity-uncertain-resolution-v10.py"
OBSERVER_V2 = ROOT / "scripts/m2-browser-active-tailnet-check.py"
OBSERVER_V3 = ROOT / "scripts/m2-browser-active-tailnet-check-v3.py"


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


resolver = module(V10, "offline_close_resolver_v10")
observer = module(OBSERVER_V3, "offline_close_observer_v3")
life = module(LIFECYCLE, "offline_close_lifecycle")


class OfflineCloseReleasePinsTests(unittest.TestCase):
    def test_v10_changes_only_lifecycle_pin(self):
        old = V9.read_text()
        expected = old.replace(
            'LIFECYCLE_SHA256 = "542465aedc1316f67edbae398407f0bb80de91bb4d800db547d3791d59b2d143"',
            f'LIFECYCLE_SHA256 = "{hashlib.sha256(LIFECYCLE.read_bytes()).hexdigest()}"',
        )
        self.assertNotEqual(old, expected)
        self.assertEqual(V10.read_text(), expected)

    def test_v3_changes_only_new_lifecycle_and_resolver_pins(self):
        expected = (
            OBSERVER_V2.read_text()
            .replace(observer_v2_lifecycle(), observer.RELEASE_LIFECYCLE_SHA256)
            .replace(
                "prism-identity-uncertain-resolution-v9.py",
                "prism-identity-uncertain-resolution-v10.py",
            )
            .replace(observer_v2_resolver(), observer.RELEASE_RESOLVER_SHA256)
            .replace("v9 resolver", "v10 resolver")
        )
        self.assertEqual(OBSERVER_V3.read_text(), expected)

    def test_v3_pins_actual_files_and_rejects_old_resolver(self):
        self.assertEqual(
            observer.RELEASE_LIFECYCLE_SHA256,
            hashlib.sha256(LIFECYCLE.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            observer.RELEASE_RESOLVER_SHA256,
            hashlib.sha256(V10.read_bytes()).hexdigest(),
        )
        self.assertEqual(resolver.LIFECYCLE_SHA256, observer.RELEASE_LIFECYCLE_SHA256)
        release = Path(resolver.RELEASE)
        observer.configure_resolver_pins(resolver, release)
        old_resolver = module(V9, "offline_close_old_resolver_v9")
        with self.assertRaisesRegex(RuntimeError, "v10 resolver release pins differ"):
            observer.configure_resolver_pins(old_resolver, release)

    def test_v3_adapts_only_verified_active_helper_baseline(self):
        old = module(OBSERVER_V2, "offline_close_observer_v2")
        baseline = types.SimpleNamespace(
            TRANSFER_SHA256=old.OLD_TRANSFER_SHA256,
            SOURCE_SHA256=old.OLD_SOURCE_SHA256,
            WATCHDOG_SHA256=old.OLD_WATCHDOG_SHA256,
            LIFECYCLE_SHA256=old.OLD_LIFECYCLE_SHA256,
            RESOLVER=old.OLD_RESOLVER,
            RESOLVER_SHA256=old.OLD_RESOLVER_SHA256,
        )
        observer.configure_release_pins(baseline, Path(resolver.RELEASE))
        self.assertEqual(baseline.LIFECYCLE_SHA256, observer.RELEASE_LIFECYCLE_SHA256)
        self.assertEqual(baseline.RESOLVER, observer.RELEASE_RESOLVER)
        self.assertEqual(baseline.RESOLVER_SHA256, observer.RELEASE_RESOLVER_SHA256)
        with self.assertRaisesRegex(RuntimeError, "baseline differs"):
            observer.configure_release_pins(baseline, Path(resolver.RELEASE))

    def test_v10_accepts_generated_no_restart_units_and_rejects_restart(self):
        host, ip, run_id = "private.example.ts.net", "100.100.100.100", "a" * 32
        with patch.object(life, "LIFECYCLE", str(LIFECYCLE)):
            service = life.unit_text(
                host,
                ip,
                resolver.UPDATE,
                resolver.RELEASE,
                resolver.SOURCE,
                run_id,
                watchdog=True,
            ).splitlines()
            watchdog = life.watchdog_unit_text(
                host,
                ip,
                resolver.UPDATE,
                resolver.RELEASE,
                resolver.SOURCE,
                run_id,
            ).splitlines()
        units = {resolver.SERVICE: service, resolver.WATCHDOG: watchdog}
        with (
            patch.object(resolver, "LIFECYCLE", str(LIFECYCLE)),
            patch.object(resolver, "OWNER_UID", os.getuid()),
            patch.object(resolver, "unit_pin", side_effect=units.__getitem__),
            patch.object(
                resolver,
                "systemd",
                return_value="tailscaled.service " + resolver.WATCHDOG,
            ),
        ):
            self.assertEqual(resolver.installed_units_pinned(), (host, ip))
            watchdog[watchdog.index("Restart=no")] = "Restart=on-failure"
            with self.assertRaisesRegex(
                resolver.ResolutionDenied, "private service guard"
            ):
                resolver.installed_units_pinned()


def observer_v2_lifecycle():
    return "542465aedc1316f67edbae398407f0bb80de91bb4d800db547d3791d59b2d143"


def observer_v2_resolver():
    return "cd6bcba09e394184bfe2121e24c7a6974ebc39678d33df026199f5140a51be88"
