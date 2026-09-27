"""Local admission checks for the live-DB watchdog socket rehearsal."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts/m2-watchdog-socket-jobs-check.py"
)
SPEC = importlib.util.spec_from_file_location("watchdog_socket_jobs_check", SCRIPT)
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class WatchdogSocketJobsCheckTests(unittest.TestCase):
    def test_unsupported_host_stops_before_any_live_read(self):
        with (
            patch.object(CHECK.platform, "system", return_value="Darwin"),
            patch.object(
                CHECK, "pinned_module", side_effect=AssertionError("host read")
            ),
            patch.object(CHECK, "db_read", side_effect=AssertionError("live read")),
        ):
            result = CHECK.run_check(Path("/missing"))
        self.assertFalse(result["passed"])
        self.assertEqual(result["error_kind"], "UnsupportedHost")

    def test_wrong_pinned_helper_does_not_load(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "executed"
            helper = Path(directory) / "helper.py"
            helper.write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
            )
            helper.chmod(0o700)
            with self.assertRaises(RuntimeError):
                CHECK.pinned_module(helper, "0" * 64, "wrong")
            self.assertFalse(marker.exists())

    def test_only_exact_synthetic_owner_chat_is_admitted(self):
        valid = {
            "mode": "verify",
            "files": [
                {"name": name, "sha256": digest}
                for name, digest in CHECK.FIXTURE.items()
            ],
            "action": {"id": "json-check", "profile": "reference-linux"},
        }
        wrong = json.loads(json.dumps(valid))
        wrong["files"][0]["sha256"] = "0" * 64
        malformed = json.loads(json.dumps(valid))
        malformed["files"][0] = None
        rows = [
            {"id": "valid", "manifest": json.dumps(valid)},
            {"id": "wrong", "manifest": json.dumps(wrong)},
            {"id": "malformed", "manifest": json.dumps(malformed)},
        ]

        def read(query, params=()):
            if "FROM owner_chats" in query:
                return rows
            return [{"n": 0 if params == ("valid",) else 6}]

        with patch.object(CHECK, "db_read", side_effect=read):
            self.assertEqual(CHECK.chat_candidates(), ["valid"])

    def test_exhausted_synthetic_chat_is_not_admitted(self):
        manifest = {
            "mode": "verify",
            "files": [
                {"name": name, "sha256": digest}
                for name, digest in CHECK.FIXTURE.items()
            ],
            "action": {"id": "json-check", "profile": "reference-linux"},
        }

        def read(query, _params=()):
            if "FROM owner_chats" in query:
                return [{"id": "used", "manifest": json.dumps(manifest)}]
            return [{"n": 6}]

        with patch.object(CHECK, "db_read", side_effect=read):
            self.assertEqual(CHECK.chat_candidates(), [])


if __name__ == "__main__":
    unittest.main()
