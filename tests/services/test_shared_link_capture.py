from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from prism.exceptions import NotFoundError, PreviewChangedError
from prism.models.capture import RemoteCaptureSource
from prism.providers.chatgpt.shared_link import ChatGPTSharedLinkAdapter
from prism.services.capture import CaptureService
from prism.services.normalization import CaptureNormalizer
from prism.services.shared_link_capture import SharedLinkCaptureService
from prism.storage import InMemoryCaptureCandidateStore, LocalCaptureStore

from tests.providers.chatgpt.shared_link.helpers import (
    StaticFetcher,
    synthetic_shared_page,
)


VALID_URL = "https://chatgpt.com/share/00000000-0000-4000-8000-000000000000"


class SharedLinkCaptureServiceTests(unittest.TestCase):
    def _service(
        self,
        data_dir: Path,
    ) -> tuple[SharedLinkCaptureService, InMemoryCaptureCandidateStore]:
        adapter = ChatGPTSharedLinkAdapter(
            fetcher=StaticFetcher(synthetic_shared_page())
        )
        temporary = InMemoryCaptureCandidateStore()
        captures = CaptureService(
            adapters={},
            normalizer=CaptureNormalizer(),
            store=LocalCaptureStore(data_dir),
        )
        return SharedLinkCaptureService(adapter, temporary, captures), temporary

    def test_inspect_is_temporary_and_confirm_persists_exact_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            service, temporary = self._service(data_dir)

            preview = service.inspect(RemoteCaptureSource(url=VALID_URL))
            self.assertFalse((data_dir / "captures").exists())
            self.assertEqual(len(preview.messages), 4)
            self.assertNotIn(
                VALID_URL,
                json.dumps(preview.model_dump(mode="json")),
            )

            result = service.confirm(preview.import_id, preview.preview_hash)
            artifact = json.loads(result.artifact_path.read_text(encoding="utf-8"))
            self.assertEqual(artifact["source"]["method"], "shared_link")
            self.assertEqual(len(artifact["messages"]), 4)
            self.assertNotIn("native-", json.dumps(artifact))
            with self.assertRaises(NotFoundError):
                temporary.get(preview.import_id)

    def test_confirmation_rejects_a_stale_hash_without_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            service, temporary = self._service(data_dir)
            preview = service.inspect(RemoteCaptureSource(url=VALID_URL))

            with self.assertRaises(PreviewChangedError):
                service.confirm(preview.import_id, "sha256:" + "0" * 64)

            self.assertFalse((data_dir / "captures").exists())
            self.assertEqual(temporary.get(preview.import_id).import_id, preview.import_id)


if __name__ == "__main__":
    unittest.main()
