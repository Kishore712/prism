"""Local admission checks for the disruptive, host-only cgroup rehearsal."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/m2-systemd-cgroup-check.py"
SPEC = importlib.util.spec_from_file_location("systemd_cgroup_check", SCRIPT)
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class SystemdCgroupCheckTests(unittest.TestCase):
    def test_unsupported_host_cannot_touch_service_or_network(self):
        with (
            patch.object(CHECK.platform, "system", return_value="Darwin"),
            patch.object(CHECK, "command", side_effect=AssertionError("host mutation")),
            patch.object(
                CHECK, "load_observed", side_effect=AssertionError("release read")
            ),
        ):
            result = CHECK.run_check(Path("/missing"))
        self.assertFalse(result["passed"])
        self.assertEqual(result["error_kind"], "UnsupportedHost")

    def test_wrong_pinned_helper_stops_before_host_use(self):
        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory) / "helper.py"
            helper.write_text("print('wrong')\n", encoding="utf-8")
            with (
                patch.object(CHECK, "OBSERVED", helper),
                self.assertRaises(RuntimeError),
            ):
                CHECK.load_observed()

    def test_live_database_requires_private_regular_file(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "demo.sqlite"
            db.write_bytes(b"not a database")
            db.chmod(0o644)
            with patch.object(CHECK, "DB", db), self.assertRaises(RuntimeError):
                CHECK.live_counts()

    def test_unexpected_tailnet_preferences_block_admission(self):
        status = {
            "BackendState": "Running",
            "Self": {"Online": True, "Tags": ["tag:prism-host"]},
        }
        prefs = {"ShieldsUp": False, "RunSSH": True, "RouteAll": False}
        outputs = iter((status, prefs))

        def fake_command(*_args, **_kwargs):
            class Result:
                stdout = json.dumps(next(outputs)).encode()

            return Result()

        with (
            patch.object(CHECK, "command", side_effect=fake_command),
            self.assertRaises(RuntimeError),
        ):
            CHECK.shield_state()


if __name__ == "__main__":
    unittest.main()
