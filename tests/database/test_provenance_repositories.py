from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from prism.database import PrismDatabase, PrismUnitOfWork
from prism.exceptions import NotFoundError, ValidationError
from prism.projection.provenance import ToolReadEvent

from tests.database.helpers import canonical_capture


def _events() -> tuple[ToolReadEvent, ...]:
    return (
        ToolReadEvent(turn_index=1, tool_name="Read", resource_label="/tmp/one.txt"),
        ToolReadEvent(turn_index=3, tool_name="Bash", resource_label="cat /tmp/two.txt"),
    )


class ToolEventRepositoryTests(unittest.TestCase):
    def _seeded_capture(self, database):
        capture = canonical_capture()
        with PrismUnitOfWork(database) as unit:
            unit.captures.add(capture)
            draft = unit.drafts.create_for_capture(capture.capture_id)
            unit.commit()
        return capture, draft

    def test_add_all_persists_and_list_for_capture_round_trips_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = PrismDatabase(Path(directory) / "prism.db")
            database.initialize()
            capture, _ = self._seeded_capture(database)

            with PrismUnitOfWork(database) as unit:
                created = unit.tool_events.add_all(capture.capture_id, _events())
                unit.commit()
            self.assertEqual(len(created), 2)
            self.assertTrue(all(record.event_id.startswith("tce_") for record in created))

            with PrismUnitOfWork(database) as unit:
                listed = unit.tool_events.list_for_capture(capture.capture_id)
            self.assertEqual([e.turn_index for e in listed], [1, 3])
            self.assertEqual(listed[0].resource_label, "/tmp/one.txt")
            self.assertEqual(listed[0].capture_id, capture.capture_id)

    def test_list_for_capture_with_no_events_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = PrismDatabase(Path(directory) / "prism.db")
            database.initialize()
            capture, _ = self._seeded_capture(database)
            with PrismUnitOfWork(database) as unit:
                listed = unit.tool_events.list_for_capture(capture.capture_id)
            self.assertEqual(listed, ())

    def test_deleting_the_capture_cascades_to_its_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = PrismDatabase(Path(directory) / "prism.db")
            database.initialize()
            capture, _ = self._seeded_capture(database)
            with PrismUnitOfWork(database) as unit:
                unit.tool_events.add_all(capture.capture_id, _events())
                unit.commit()
            with PrismUnitOfWork(database) as unit:
                assert unit.session is not None
                from prism.database.models import CaptureRow

                unit.session.delete(unit.session.get(CaptureRow, capture.capture_id))
                unit.commit()
            with PrismUnitOfWork(database) as unit:
                self.assertEqual(unit.tool_events.list_for_capture(capture.capture_id), ())

    def test_get_unknown_event_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = PrismDatabase(Path(directory) / "prism.db")
            database.initialize()
            with PrismUnitOfWork(database) as unit:
                with self.assertRaises(NotFoundError):
                    unit.tool_events.get("tce_doesnotexist00000000000")


class TaintedEventRepositoryTests(unittest.TestCase):
    def _seeded(self, database):
        capture = canonical_capture()
        with PrismUnitOfWork(database) as unit:
            unit.captures.add(capture)
            draft = unit.drafts.create_for_capture(capture.capture_id)
            events = unit.tool_events.add_all(capture.capture_id, _events())
            unit.commit()
        return capture, draft, events

    def test_taint_then_untaint_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = PrismDatabase(Path(directory) / "prism.db")
            database.initialize()
            _, draft, events = self._seeded(database)

            with PrismUnitOfWork(database) as unit:
                record = unit.tainted_events.taint(
                    draft.draft_id, events[0].event_id, "wrong file, mistake"
                )
                unit.commit()
            self.assertEqual(record.event_id, events[0].event_id)

            with PrismUnitOfWork(database) as unit:
                ids = unit.tainted_events.tainted_event_ids(draft.draft_id)
                listed = unit.tainted_events.list(draft.draft_id)
            self.assertEqual(ids, frozenset({events[0].event_id}))
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0].reason, "wrong file, mistake")

            with PrismUnitOfWork(database) as unit:
                unit.tainted_events.untaint(draft.draft_id, events[0].event_id)
                unit.commit()
            with PrismUnitOfWork(database) as unit:
                self.assertEqual(unit.tainted_events.tainted_event_ids(draft.draft_id), frozenset())

    def test_taint_requires_a_non_blank_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = PrismDatabase(Path(directory) / "prism.db")
            database.initialize()
            _, draft, events = self._seeded(database)
            with PrismUnitOfWork(database) as unit:
                with self.assertRaises(ValidationError):
                    unit.tainted_events.taint(draft.draft_id, events[0].event_id, "   ")

    def test_taint_unknown_event_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = PrismDatabase(Path(directory) / "prism.db")
            database.initialize()
            _, draft, _ = self._seeded(database)
            with PrismUnitOfWork(database) as unit:
                with self.assertRaises(NotFoundError):
                    unit.tainted_events.taint(
                        draft.draft_id, "tce_doesnotexist00000000000", "reason"
                    )

    def test_retainting_the_same_event_updates_the_reason_not_duplicates_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = PrismDatabase(Path(directory) / "prism.db")
            database.initialize()
            _, draft, events = self._seeded(database)
            with PrismUnitOfWork(database) as unit:
                unit.tainted_events.taint(draft.draft_id, events[0].event_id, "first reason")
                unit.tainted_events.taint(draft.draft_id, events[0].event_id, "revised reason")
                unit.commit()
            with PrismUnitOfWork(database) as unit:
                listed = unit.tainted_events.list(draft.draft_id)
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0].reason, "revised reason")

    def test_untaint_without_a_prior_taint_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = PrismDatabase(Path(directory) / "prism.db")
            database.initialize()
            _, draft, events = self._seeded(database)
            with PrismUnitOfWork(database) as unit:
                with self.assertRaises(NotFoundError):
                    unit.tainted_events.untaint(draft.draft_id, events[0].event_id)

    def test_deleting_the_draft_cascades_to_its_taint_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = PrismDatabase(Path(directory) / "prism.db")
            database.initialize()
            _, draft, events = self._seeded(database)
            with PrismUnitOfWork(database) as unit:
                unit.tainted_events.taint(draft.draft_id, events[0].event_id, "reason")
                unit.commit()
            with PrismUnitOfWork(database) as unit:
                assert unit.session is not None
                from prism.database.models import ShareDraftRow

                unit.session.delete(unit.session.get(ShareDraftRow, draft.draft_id))
                unit.commit()
            with PrismUnitOfWork(database) as unit:
                self.assertEqual(unit.tainted_events.tainted_event_ids(draft.draft_id), frozenset())


if __name__ == "__main__":
    unittest.main()
