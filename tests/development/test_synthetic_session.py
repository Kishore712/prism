from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from prism.development import SyntheticSessionSource
from prism.exceptions import ValidationError


class SyntheticSessionSourceTests(unittest.TestCase):
    def test_returns_only_fully_completed_turns(self) -> None:
        source = (
            Path(__file__).resolve().parents[2]
            / "examples"
            / "sessions"
            / "synthetic_session.json"
        )

        snapshot = SyntheticSessionSource().snapshot(source)

        self.assertEqual(snapshot.title, "Synthetic shared-agent research session")
        self.assertEqual({message.turn_id for message in snapshot.messages}, {"turn-1", "turn-2"})
        self.assertEqual(len(snapshot.resources), 2)

    def test_rejects_unknown_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "session.json"
            source.write_text(
                json.dumps({"schema_version": "unknown"}),
                encoding="utf-8",
            )

            with self.assertRaises(ValidationError):
                SyntheticSessionSource().snapshot(source)

    def test_rejects_non_object_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "session.json"
            source.write_text("[]", encoding="utf-8")

            with self.assertRaises(ValidationError):
                SyntheticSessionSource().snapshot(source)

    def test_rejects_resource_outside_source_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root.parent / "outside.txt"
            outside.write_text("not shareable", encoding="utf-8")
            source = root / "session.json"
            source.write_text(
                json.dumps(
                    {
                        "schema_version": "prism.synthetic-session.v1",
                        "title": "Unsafe session",
                        "messages": [
                            {
                                "turn_id": "turn-1",
                                "role": "assistant",
                                "content": "done",
                                "status": "completed",
                            }
                        ],
                        "resources": [
                            {
                                "display_name": "Outside",
                                "path": "../outside.txt",
                                "media_type": "text/plain",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ValidationError):
                SyntheticSessionSource().snapshot(source)


if __name__ == "__main__":
    unittest.main()
