from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from prism.database import PrismDatabase, PrismUnitOfWork
from prism.database.models import DraftTurnSelectionRow

from tests.database.helpers import canonical_capture


class RepositoryTests(unittest.TestCase):
    def test_capture_round_trip_and_default_draft_are_exact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = PrismDatabase(Path(directory) / "prism.db")
            database.initialize()
            capture = canonical_capture()

            with PrismUnitOfWork(database) as unit:
                unit.captures.add(capture)
                draft = unit.drafts.create_for_capture(capture.capture_id)
                unit.commit()

            with PrismUnitOfWork(database) as unit:
                restored = unit.captures.load(capture.capture_id)
                restored_draft = unit.drafts.get(draft.draft_id)

            self.assertEqual(restored, capture)
            self.assertEqual(restored.capture_hash, capture.capture_hash)
            self.assertEqual(restored_draft.revision, 1)
            self.assertEqual(restored_draft.selected_turn_ids, ())
            self.assertIsNone(restored_draft.preview_hash)

    def test_database_rejects_selection_for_a_turn_outside_the_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = PrismDatabase(Path(directory) / "prism.db")
            database.initialize()
            capture = canonical_capture()
            with PrismUnitOfWork(database) as unit:
                unit.captures.add(capture)
                draft = unit.drafts.create_for_capture(capture.capture_id)
                unit.commit()

            with self.assertRaises(IntegrityError):
                with PrismUnitOfWork(database) as unit:
                    assert unit.session is not None
                    unit.session.add(
                        DraftTurnSelectionRow(
                            draft_id=draft.draft_id,
                            capture_id=capture.capture_id,
                            turn_id="turn_00000000000000000000000000",
                            included=True,
                        )
                    )
                    unit.commit()

            with PrismUnitOfWork(database) as unit:
                restored = unit.drafts.get(draft.draft_id)
            self.assertEqual(restored.selected_turn_ids, ())


if __name__ == "__main__":
    unittest.main()
