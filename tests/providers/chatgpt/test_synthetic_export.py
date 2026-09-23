from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from prism.exceptions import (
    AmbiguousBranchError,
    ConversationNotFoundError,
    UnsupportedSourceFormatError,
)
from prism.models.capture import CaptureSelection, CaptureSource
from prism.providers.chatgpt import SyntheticChatGPTExportAdapter


class SyntheticChatGPTExportAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sample = (
            Path(__file__).resolve().parents[3]
            / "examples"
            / "chatgpt"
            / "synthetic_export.json"
        )

    def setUp(self) -> None:
        self.adapter = SyntheticChatGPTExportAdapter()
        self.source = CaptureSource(path=self.sample)

    def test_inventory_contains_metadata_but_no_message_content(self) -> None:
        inventory = self.adapter.inventory(self.source)

        self.assertEqual(len(inventory.conversations), 3)
        self.assertEqual(inventory.conversations[0].supported_message_count, 4)
        self.assertEqual(inventory.conversations[2].supported_message_count, 0)
        serialized = inventory.model_dump_json()
        self.assertNotIn("UNSELECTED_PRIVATE_MARKER_7429", serialized)
        self.assertNotIn("regenerated response", serialized)

    def test_capture_uses_current_branch_and_excludes_other_conversations(self) -> None:
        inventory = self.adapter.inventory(self.source)
        selected = inventory.conversations[0]

        capture = self.adapter.capture(
            self.source,
            CaptureSelection(conversation_ref=selected.conversation_ref),
        )

        content = "\n".join(message.content for message in capture.messages)
        self.assertEqual(len(capture.messages), 4)
        self.assertIn("safe file metadata", content)
        self.assertNotIn("regenerated response", content)
        self.assertNotIn("UNSELECTED_PRIVATE_MARKER_7429", content)
        self.assertEqual(len(capture.resources), 1)
        self.assertEqual(capture.resources[0].display_name, "research-overview.md")

    def test_rejects_foreign_conversation_reference(self) -> None:
        with self.assertRaises(ConversationNotFoundError):
            self.adapter.capture(
                self.source,
                CaptureSelection(conversation_ref="convref_000000000000000000000000"),
            )

    def test_same_sample_can_be_read_from_a_zip_export(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "chatgpt-export.zip"
            with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.write(self.sample, "conversations.json")
            source = CaptureSource(path=archive_path)

            inventory = self.adapter.inventory(source)
            capture = self.adapter.capture(
                source,
                CaptureSelection(
                    conversation_ref=inventory.conversations[0].conversation_ref
                ),
            )

            self.assertEqual(capture.title, "Prism shared-agent research")
            self.assertEqual(len(capture.messages), 4)

    def test_unknown_source_schema_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "conversations.json"
            source.write_text(
                json.dumps({"schema_version": "unknown", "conversations": []}),
                encoding="utf-8",
            )

            with self.assertRaises(UnsupportedSourceFormatError):
                self.adapter.inventory(CaptureSource(path=source))

    def test_ambiguous_branch_is_visible_in_inventory_and_blocked_on_capture(self) -> None:
        payload = json.loads(self.sample.read_text(encoding="utf-8"))
        payload["conversations"][0].pop("current_node")
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "ambiguous.json"
            source_path.write_text(json.dumps(payload), encoding="utf-8")
            source = CaptureSource(path=source_path)
            inventory = self.adapter.inventory(source)

            self.assertEqual(inventory.conversations[0].supported_message_count, 0)
            self.assertEqual(inventory.conversations[0].warnings[0].code, "AMBIGUOUS_BRANCH")
            with self.assertRaises(AmbiguousBranchError):
                self.adapter.capture(
                    source,
                    CaptureSelection(
                        conversation_ref=inventory.conversations[0].conversation_ref
                    ),
                )


if __name__ == "__main__":
    unittest.main()
