from __future__ import annotations

import json
import stat
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from prism.database import PrismDatabase, PrismUnitOfWork
from prism.development import SyntheticSessionCaptureAdapter
from prism.exceptions import UnsupportedContentError
from prism.models.capture import CapturedSession, CaptureSelection, CaptureSource
from prism.providers.chatgpt import SyntheticChatGPTExportAdapter
from prism.providers.claude.claude_code import ClaudeCodeSessionAdapter
from prism.services.capture import CaptureService
from prism.services.normalization import CaptureNormalizer
from prism.storage import LocalCaptureStore
from prism.storage.database import DatabaseCaptureStore


class CaptureServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repository_root = Path(__file__).resolve().parents[2]
        cls.sample = (
            cls.repository_root / "examples" / "chatgpt" / "synthetic_export.json"
        )

    def _service(self, data_dir: Path) -> CaptureService:
        adapter = SyntheticChatGPTExportAdapter()
        return CaptureService(
            adapters={adapter.name: adapter},
            normalizer=CaptureNormalizer(),
            store=LocalCaptureStore(data_dir),
        )

    def test_persists_only_selected_conversation_as_canonical_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            service = self._service(data_dir)
            source = CaptureSource(path=self.sample)
            inventory = service.inventory(source, "synthetic-chatgpt-export")

            result = service.capture(
                source,
                "synthetic-chatgpt-export",
                CaptureSelection(
                    conversation_ref=inventory.conversations[0].conversation_ref
                ),
            )
            artifact = json.loads(result.artifact_path.read_text(encoding="utf-8"))
            serialized = json.dumps(artifact)

            self.assertEqual(artifact["schema_version"], "prism.capture.v1")
            self.assertEqual(artifact["title"], "Prism shared-agent research")
            self.assertEqual(len(artifact["messages"]), 4)
            self.assertEqual(artifact["resources"][0]["availability"], "reference_only")
            self.assertNotIn("UNSELECTED_PRIVATE_MARKER_7429", serialized)
            self.assertNotIn("regenerated response", serialized)
            self.assertNotIn(str(self.sample), serialized)
            self.assertEqual(stat.S_IMODE(result.artifact_path.stat().st_mode), 0o600)
            self.assertEqual(
                stat.S_IMODE(result.artifact_path.parent.stat().st_mode),
                0o700,
            )

    def test_repeated_import_has_same_content_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self._service(Path(directory))
            source = CaptureSource(path=self.sample)
            selected = service.inventory(
                source, "synthetic-chatgpt-export"
            ).conversations[0]
            selection = CaptureSelection(conversation_ref=selected.conversation_ref)

            first = service.capture(source, "synthetic-chatgpt-export", selection)
            second = service.capture(source, "synthetic-chatgpt-export", selection)

            self.assertNotEqual(first.capture_id, second.capture_id)
            self.assertEqual(first.capture_hash, second.capture_hash)

    def test_persisted_contract_is_strict_frozen_and_schema_versioned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = self._service(Path(directory))
            source = CaptureSource(path=self.sample)
            selected = service.inventory(
                source, "synthetic-chatgpt-export"
            ).conversations[0]
            result = service.capture(
                source,
                "synthetic-chatgpt-export",
                CaptureSelection(conversation_ref=selected.conversation_ref),
            )
            capture = CapturedSession.model_validate_json(
                result.artifact_path.read_text(encoding="utf-8")
            )

            self.assertEqual(capture.schema_version, "prism.capture.v1")
            self.assertFalse(CapturedSession.model_json_schema()["additionalProperties"])
            with self.assertRaises(PydanticValidationError):
                capture.title = "mutated"  # type: ignore[misc]

    def test_synthetic_session_uses_same_canonical_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            adapter = SyntheticSessionCaptureAdapter()
            service = CaptureService(
                adapters={adapter.name: adapter},
                normalizer=CaptureNormalizer(),
                store=LocalCaptureStore(data_dir),
            )
            source = CaptureSource(
                path=(
                    self.repository_root
                    / "examples"
                    / "sessions"
                    / "synthetic_session.json"
                )
            )
            selected = service.inventory(source, adapter.name).conversations[0]

            result = service.capture(
                source,
                adapter.name,
                CaptureSelection(conversation_ref=selected.conversation_ref),
            )
            artifact = json.loads(result.artifact_path.read_text(encoding="utf-8"))

            self.assertEqual(artifact["source"]["platform"], "prism")
            self.assertEqual(artifact["source"]["method"], "synthetic")
            self.assertEqual(artifact["schema_version"], "prism.capture.v1")

    def test_blocking_content_creates_no_partial_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            service = self._service(data_dir)
            source = CaptureSource(path=self.sample)
            blocked = service.inventory(
                source, "synthetic-chatgpt-export"
            ).conversations[2]

            with self.assertRaises(UnsupportedContentError):
                service.capture(
                    source,
                    "synthetic-chatgpt-export",
                    CaptureSelection(conversation_ref=blocked.conversation_ref),
                )

            self.assertFalse((data_dir / "captures").exists())


class CaptureServiceProvenanceWiringTests(unittest.TestCase):
    """Phase 9: capture() must persist the provenance timeline when both the
    adapter (ProvenanceCaptureAdapter) and the store (save_tool_events)
    support it -- and must be a silent no-op otherwise (LocalCaptureStore
    above, which has neither, is unaffected by this wiring).
    """

    @staticmethod
    def _session_jsonl(path: Path) -> None:
        records = [
            {"type": "custom-title", "customTitle": "Provenance wiring test"},
            {
                "type": "user",
                "uuid": "u1",
                "message": {"role": "user", "content": "Please check config.yaml for the setting."},
            },
            {
                "type": "assistant",
                "uuid": "a1",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "Read",
                            "input": {"file_path": "/tmp/config.yaml"},
                        },
                        {"type": "text", "text": "The setting is enabled."},
                    ],
                },
            },
        ]
        path.write_text(
            "\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8"
        )

    def test_capture_persists_tool_events_when_adapter_and_store_support_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            session_path = data_dir / "session-1.jsonl"
            self._session_jsonl(session_path)

            database = PrismDatabase(data_dir / "prism.db")
            adapter = ClaudeCodeSessionAdapter()
            service = CaptureService(
                adapters={adapter.name: adapter},
                normalizer=CaptureNormalizer(),
                store=DatabaseCaptureStore(database),
            )
            source = CaptureSource(path=session_path)
            selected = service.inventory(source, adapter.name).conversations[0]

            result = service.capture(
                source, adapter.name, CaptureSelection(conversation_ref=selected.conversation_ref)
            )

            with PrismUnitOfWork(database) as unit:
                events = unit.tool_events.list_for_capture(result.capture_id)

            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].tool_name, "Read")
            self.assertEqual(events[0].resource_label, "/tmp/config.yaml")
            self.assertEqual(events[0].turn_index, 1)

    def test_capture_is_unaffected_when_store_does_not_support_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            session_path = data_dir / "session-1.jsonl"
            self._session_jsonl(session_path)

            adapter = ClaudeCodeSessionAdapter()
            service = CaptureService(
                adapters={adapter.name: adapter},
                normalizer=CaptureNormalizer(),
                store=LocalCaptureStore(data_dir / "store"),
            )
            source = CaptureSource(path=session_path)
            selected = service.inventory(source, adapter.name).conversations[0]

            # Must not raise even though LocalCaptureStore has no
            # save_tool_events method at all.
            result = service.capture(
                source, adapter.name, CaptureSelection(conversation_ref=selected.conversation_ref)
            )
            self.assertTrue(result.capture_id)


if __name__ == "__main__":
    unittest.main()
