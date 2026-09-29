"""V8 recovery resolver refuses unpinned or altered service units."""

import hashlib
import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE = ROOT / "scripts/gcp/identity-service-lifecycle.py"
RESOLVER = ROOT / "scripts/gcp/identity-uncertain-resolution-v11.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


resolver = load(RESOLVER, "uncertain_resolution_v11")
life = load(LIFECYCLE, "uncertain_resolution_v11_lifecycle")


class V11RecoveryPinsTests(unittest.TestCase):
    def test_missing_or_malformed_pins_deny_without_host_access(self):
        with patch.object(resolver, "unit_pin", side_effect=AssertionError("host access")):
            for name in ("LIFECYCLE_SHA256", "TRANSFER", "SOURCE"):
                for value in ("", "z" * 64):
                    with self.subTest(pin=name, value=value[:4]), patch.object(
                        resolver, name, value
                    ):
                        with self.assertRaisesRegex(
                            resolver.ResolutionDenied, "not been configured"
                        ):
                            resolver.installed_units_pinned()
                        with self.assertRaisesRegex(
                            resolver.ResolutionDenied, "not been configured"
                        ):
                            resolver.execute("inspect", "a" * 32)

    def test_measured_v8_pins_and_missing_host_helper(self):
        self.assertEqual(
            resolver.LIFECYCLE_SHA256,
            "f342f35e6edce3cb0a7848b50a9a29a9bb8ae5fd7fcdb0e7e660f804de78ec1b",
        )
        self.assertEqual(
            resolver.TRANSFER,
            "85151e8a9d47da8c2c5e60c3af49b44bf47149f81b463888fadaff566003814e",
        )
        self.assertEqual(
            resolver.SOURCE,
            "c0f9d990fa44dac1d04802bee11a4c58bae511a272ef098460489d5412fe2202",
        )
        with (
            patch.object(resolver, "LIFECYCLE", "/missing/prism-lifecycle-helper"),
            self.assertRaisesRegex(
                resolver.ResolutionDenied, "Installed lifecycle helper is unavailable"
            ),
        ):
            resolver.installed_units_pinned()

    def test_v8_units_match_and_changes_fail_closed(self):
        host, ip, run_id = "private.example.ts.net", "100.100.100.100", "a" * 32
        transfer, source = "b" * 64, "c" * 64
        update = "/var/lib/prism/identity-pilot/service-updates/" + transfer + "/installed.json"
        release = "/var/lib/prism/identity-pilot/app-releases/service-" + transfer
        with patch.object(life, "LIFECYCLE", str(LIFECYCLE)):
            service = life.unit_text(
                host, ip, update, release, source, run_id,
                watchdog=True, recovery=True,
            ).splitlines()
            watchdog = life.watchdog_unit_text(
                host, ip, update, release, source, run_id, recovery=True,
            ).splitlines()
            recovery = life.recovery_unit_text(
                host, ip, update, source, run_id,
            ).splitlines()
        units = {
            resolver.SERVICE: service,
            resolver.WATCHDOG: watchdog,
            resolver.RECOVERY: recovery,
        }
        initial_service = list(service)
        initial_recovery = list(recovery)
        with (
            patch.object(resolver, "LIFECYCLE", str(LIFECYCLE)),
            patch.object(resolver, "LIFECYCLE_SHA256", hashlib.sha256(LIFECYCLE.read_bytes()).hexdigest()),
            patch.object(resolver, "TRANSFER", transfer),
            patch.object(resolver, "SOURCE", source),
            patch.object(resolver, "UPDATE", update),
            patch.object(resolver, "RELEASE", release),
            patch.object(resolver, "OWNER_UID", os.getuid()),
            patch.object(resolver, "unit_pin", side_effect=units.__getitem__),
            patch.object(resolver, "systemd", return_value="tailscaled.service " + resolver.WATCHDOG),
        ):
            self.assertEqual(resolver.installed_units_pinned(), (host, ip))
            service[service.index(next(line for line in service if "recovery-claim" in line))] = ""
            with self.assertRaises(resolver.ResolutionDenied):
                resolver.installed_units_pinned()
            service[:] = initial_service
            watchdog.remove("OnFailure=" + resolver.RECOVERY)
            with self.assertRaises(resolver.ResolutionDenied):
                resolver.installed_units_pinned()
            watchdog.append("OnFailure=" + resolver.RECOVERY)
            recovery[recovery.index("Restart=no")] = "Restart=on-failure"
            with self.assertRaises(resolver.ResolutionDenied):
                resolver.installed_units_pinned()
            recovery[recovery.index("Restart=on-failure")] = "Restart=no"
            command_index = next(i for i, line in enumerate(recovery)
                                 if line.startswith("ExecStart="))
            recovery[command_index] += " --unexpected value"
            with self.assertRaisesRegex(resolver.ResolutionDenied, "recovery run command"):
                resolver.installed_units_pinned()
            recovery[:] = initial_recovery
            stop_hook = next(line for line in recovery if line.startswith("ExecStopPost="))
            recovery.remove(stop_hook)
            with self.assertRaises(resolver.ResolutionDenied):
                resolver.installed_units_pinned()
            recovery.append(stop_hook + " --unexpected value")
            with self.assertRaisesRegex(resolver.ResolutionDenied, "recovery stop hook"):
                resolver.installed_units_pinned()


if __name__ == "__main__":
    unittest.main()
