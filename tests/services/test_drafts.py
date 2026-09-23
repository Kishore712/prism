from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from prism.database import PrismDatabase
from prism.exceptions import DraftChangedError, ValidationError
from prism.models.draft import DraftSelectionRequest
from prism.services.drafts import DraftService
from prism.storage import DatabaseCaptureStore

from tests.database.helpers import canonical_capture


class DraftServiceTests(unittest.TestCase):
    def _created(self, root: Path) -> tuple[Path, str]:
        path = root / "prism.db"
        database = PrismDatabase(path)
        stored = DatabaseCaptureStore(database).save(canonical_capture())
        assert stored.draft_id is not None
        return path, stored.draft_id

    def test_selection_and_preview_survive_process_reconstruction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, draft_id = self._created(Path(directory))
            first = DraftService(PrismDatabase(path))
            review = first.review(draft_id)
            selected_id = review.turns[1].turn.turn_id
            selected = first.replace_selection(
                draft_id,
                DraftSelectionRequest(
                    expected_revision=1,
                    selected_turn_ids=(selected_id,),
                ),
            )

            second = DraftService(PrismDatabase(path))
            restored = second.review(draft_id)
            preview = second.preview(draft_id, selected.revision)
            after_preview = second.review(draft_id).draft

            self.assertEqual(restored.draft.selected_turn_ids, (selected_id,))
            self.assertFalse(restored.turns[0].included)
            self.assertTrue(restored.turns[1].included)
            self.assertEqual(
                [message.content for message in preview.snapshot.messages],
                ["Second question", "Second answer"],
            )
            self.assertEqual(after_preview.previewed_revision, 2)
            self.assertEqual(after_preview.preview_hash, preview.preview_hash)

    def test_selection_update_invalidates_prior_preview(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, draft_id = self._created(Path(directory))
            service = DraftService(PrismDatabase(path))
            review = service.review(draft_id)
            first_id = review.turns[0].turn.turn_id
            second_id = review.turns[1].turn.turn_id
            revision_two = service.replace_selection(
                draft_id,
                DraftSelectionRequest(
                    expected_revision=1,
                    selected_turn_ids=(first_id,),
                ),
            )
            service.preview(draft_id, revision_two.revision)

            revision_three = service.replace_selection(
                draft_id,
                DraftSelectionRequest(
                    expected_revision=2,
                    selected_turn_ids=(second_id,),
                ),
            )

            self.assertEqual(revision_three.revision, 3)
            self.assertIsNone(revision_three.previewed_revision)
            self.assertIsNone(revision_three.preview_hash)
            with self.assertRaises(DraftChangedError):
                service.preview(draft_id, 2)

    def test_stale_or_invalid_update_changes_neither_revision_nor_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, draft_id = self._created(Path(directory))
            service = DraftService(PrismDatabase(path))
            review = service.review(draft_id)
            valid = review.turns[0].turn.turn_id
            service.replace_selection(
                draft_id,
                DraftSelectionRequest(
                    expected_revision=1,
                    selected_turn_ids=(valid,),
                ),
            )

            with self.assertRaises(DraftChangedError):
                service.replace_selection(
                    draft_id,
                    DraftSelectionRequest(
                        expected_revision=1,
                        selected_turn_ids=(review.turns[1].turn.turn_id,),
                    ),
                )
            with self.assertRaises(ValidationError):
                service.replace_selection(
                    draft_id,
                    DraftSelectionRequest(
                        expected_revision=2,
                        selected_turn_ids=("turn_00000000000000000000000000",),
                    ),
                )

            restored = service.review(draft_id).draft
            self.assertEqual(restored.revision, 2)
            self.assertEqual(restored.selected_turn_ids, (valid,))

    def test_empty_allowlist_is_durable_but_cannot_be_previewed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, draft_id = self._created(Path(directory))
            service = DraftService(PrismDatabase(path))
            state = service.replace_selection(
                draft_id,
                DraftSelectionRequest(expected_revision=1),
            )
            self.assertEqual(state.revision, 2)
            self.assertEqual(state.selected_turn_ids, ())
            with self.assertRaises(ValidationError):
                service.preview(draft_id, 2)


if __name__ == "__main__":
    unittest.main()
