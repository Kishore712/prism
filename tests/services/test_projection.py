from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from prism.exceptions import ValidationError
from prism.models.capture import RemoteCaptureSource
from prism.models.projection import ProjectionSelection
from prism.providers.chatgpt.shared_link import ChatGPTSharedLinkAdapter
from prism.services.capture import CaptureService
from prism.services.normalization import CaptureNormalizer
from prism.services.projection import ProjectionService
from prism.services.shared_link_capture import SharedLinkCaptureService
from prism.storage import InMemoryCaptureCandidateStore, LocalCaptureStore

from tests.providers.chatgpt.shared_link.helpers import (
    StaticFetcher,
    synthetic_shared_page,
)


VALID_URL = "https://chatgpt.com/share/00000000-0000-4000-8000-000000000000"


class ProjectionServiceTests(unittest.TestCase):
    def _captured(
        self,
        data_dir: Path,
    ) -> tuple[LocalCaptureStore, str]:
        store = LocalCaptureStore(data_dir)
        capture_service = CaptureService(
            adapters={},
            normalizer=CaptureNormalizer(),
            store=store,
        )
        shared_link = SharedLinkCaptureService(
            ChatGPTSharedLinkAdapter(fetcher=StaticFetcher(synthetic_shared_page())),
            InMemoryCaptureCandidateStore(),
            capture_service,
        )
        candidate = shared_link.inspect(RemoteCaptureSource(url=VALID_URL))
        result = shared_link.confirm(candidate.import_id, candidate.preview_hash)
        return store, result.capture_id

    def test_preview_contains_only_explicitly_selected_complete_turns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store, capture_id = self._captured(Path(directory))
            projection = ProjectionService(store)
            review = projection.review(capture_id)

            preview = projection.preview(
                capture_id,
                ProjectionSelection(selected_turn_ids=(review.turns[1].turn_id,)),
            )

            contents = [message.content for message in preview.snapshot.messages]
            self.assertEqual(contents, ["Second question", "Second answer"])
            self.assertEqual(preview.included_turn_count, 1)
            self.assertEqual(preview.included_message_count, 2)
            self.assertNotIn("First question", str(preview.model_dump()))

    def test_preview_is_stable_and_rejects_unknown_or_duplicate_turns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store, capture_id = self._captured(Path(directory))
            projection = ProjectionService(store)
            review = projection.review(capture_id)
            selection = ProjectionSelection(
                selected_turn_ids=(review.turns[0].turn_id,)
            )

            first = projection.preview(capture_id, selection)
            second = projection.preview(capture_id, selection)
            self.assertEqual(first.preview_hash, second.preview_hash)
            self.assertEqual(first.snapshot, second.snapshot)

            with self.assertRaises(ValidationError):
                projection.preview(
                    capture_id,
                    ProjectionSelection(
                        selected_turn_ids=(
                            review.turns[0].turn_id,
                            review.turns[0].turn_id,
                        )
                    ),
                )
            with self.assertRaises(ValidationError):
                projection.preview(
                    capture_id,
                    ProjectionSelection(selected_turn_ids=("turn_unknown",)),
                )
            with self.assertRaises(ValidationError):
                projection.preview(
                    capture_id,
                    ProjectionSelection(
                        selected_turn_ids=(review.turns[0].turn_id,),
                        selected_resource_ids=("res_unknown",),
                    ),
                )


if __name__ == "__main__":
    unittest.main()
