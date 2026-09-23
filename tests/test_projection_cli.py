from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from prism.cli import main
from prism.database import PrismDatabase
from prism.models.capture import AdapterCapture, AdapterMessage, CaptureMethod, MessageRole, Platform
from prism.services.normalization import CaptureNormalizer
from prism.storage import DatabaseCaptureStore


def _turn(i, u, a):
    return [
        AdapterMessage(source_message_id=f"s{i}u", turn_index=i, ordinal=2 * i - 1,
                        role=MessageRole.USER, content=u),
        AdapterMessage(source_message_id=f"s{i}a", turn_index=i, ordinal=2 * i,
                        role=MessageRole.ASSISTANT, content=a),
    ]


class ProjectionCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.data_dir = Path(self._directory.name)
        database = PrismDatabase(self.data_dir / "prism.db")
        capture = CaptureNormalizer().normalize(
            "test-adapter/0.1",
            AdapterCapture(
                platform=Platform.CHATGPT,
                method=CaptureMethod.SYNTHETIC,
                source_fingerprint="sha256:" + "a" * 64,
                conversation_ref="convref_abcdefgh",
                title="Original capture title",
                messages=tuple(_turn(1, "key AKIAJVQVGF6NXQZXKMPS", "noted")),
            ),
        )
        result = DatabaseCaptureStore(database).save(capture)
        assert result.draft_id is not None
        self.draft_id = result.draft_id
        run = main(["draft", "select", self.draft_id, "--turns", "1", "--data-dir", str(self.data_dir)])
        self.assertEqual(run, 0)

    def _run_json(self, *arguments: str) -> object:
        output = io.StringIO()
        with redirect_stdout(output):
            code = main([*arguments, "--data-dir", str(self.data_dir), "--json"])
        self.assertEqual(code, 0, output.getvalue())
        return json.loads(output.getvalue())

    def test_findings_lists_the_blocking_secret(self) -> None:
        body = self._run_json("draft", "findings", self.draft_id)
        self.assertEqual(len(body["findings"]), 1)
        self.assertEqual(body["findings"][0]["finding_class"], "secret")
        self.assertFalse(body["findings"][0]["decided"])

    def test_publish_blocks_then_succeeds_after_resolve(self) -> None:
        blocked = main(
            ["draft", "publish", self.draft_id, "--yes", "--data-dir", str(self.data_dir)]
        )
        self.assertNotEqual(blocked, 0)

        finding_id = self._run_json("draft", "findings", self.draft_id)["findings"][0]["finding_id"]
        code = main(
            ["draft", "resolve", self.draft_id, finding_id, "--reason", "demo key",
             "--data-dir", str(self.data_dir)]
        )
        self.assertEqual(code, 0)

        published = main(
            ["draft", "publish", self.draft_id, "--yes", "--data-dir", str(self.data_dir)]
        )
        self.assertEqual(published, 0)

    def test_unresolve_makes_it_block_again(self) -> None:
        finding_id = self._run_json("draft", "findings", self.draft_id)["findings"][0]["finding_id"]
        main(["draft", "resolve", self.draft_id, finding_id, "--reason", "demo key",
              "--data-dir", str(self.data_dir)])
        self.assertEqual(
            main(["draft", "publish", self.draft_id, "--yes", "--data-dir", str(self.data_dir)]), 0
        )
        # Reload a fresh draft for a second round to test unresolve independent of the first publish.
        main(["draft", "unresolve", self.draft_id, finding_id, "--data-dir", str(self.data_dir)])
        state = self._run_json("draft", "findings", self.draft_id)
        self.assertFalse(state["findings"][0]["decided"])

    def test_resolve_requires_a_reason_argument(self) -> None:
        with self.assertRaises(SystemExit):
            main(["draft", "resolve", self.draft_id, "fnd_doesnotexist00000000000"])

    def test_resolve_rejects_a_finding_id_not_in_the_current_scan(self) -> None:
        code = main(
            ["draft", "resolve", self.draft_id, "fnd_doesnotexist00000000000",
             "--reason", "x", "--data-dir", str(self.data_dir)]
        )
        self.assertNotEqual(code, 0)

    def test_title_set_and_clear_round_trip(self) -> None:
        code = main(
            ["draft", "title", self.draft_id, "--set", "New public title",
             "--data-dir", str(self.data_dir), "--json"]
        )
        self.assertEqual(code, 0)
        code = main(
            ["draft", "title", self.draft_id, "--clear", "--data-dir", str(self.data_dir)]
        )
        self.assertEqual(code, 0)

    def test_title_rejects_both_set_and_clear(self) -> None:
        code = main(
            ["draft", "title", self.draft_id, "--set", "x", "--clear",
             "--data-dir", str(self.data_dir)]
        )
        self.assertNotEqual(code, 0)

    def test_snapshot_receipt_round_trips_after_publish(self) -> None:
        finding_id = self._run_json("draft", "findings", self.draft_id)["findings"][0]["finding_id"]
        main(["draft", "resolve", self.draft_id, finding_id, "--reason", "demo key",
              "--data-dir", str(self.data_dir)])
        main(["draft", "publish", self.draft_id, "--yes", "--data-dir", str(self.data_dir)])
        from prism.services.publication import PublicationService
        snapshot_id = PublicationService(PrismDatabase(self.data_dir / "prism.db")).list()[0].snapshot_id
        body = self._run_json("snapshot", "receipt", snapshot_id)
        self.assertTrue(body["signature_valid"])
        self.assertEqual(body["override_count"], 1)


if __name__ == "__main__":
    unittest.main()
