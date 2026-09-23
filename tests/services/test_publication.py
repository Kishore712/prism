from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from prism.database import PrismDatabase, PrismUnitOfWork
from prism.database.models import (
    CaptureMessageRow,
    SnapshotLifecycleRow,
    SnapshotPayloadRow,
    SnapshotRow,
)
from prism.exceptions import (
    DraftChangedError,
    PreviewChangedError,
    PreviewRequiredError,
    SnapshotCollisionError,
)
from prism.models.draft import DraftSelectionRequest, DraftStatus
from prism.models.projection import SnapshotContent, SnapshotMessage
from prism.models.publication import PublicationRequest, SnapshotStatus
from prism.models.capture import MessageRole
from prism.services.drafts import DraftService
from prism.services.projection import ProjectionService
from prism.services.publication import PublicationService
from prism.storage import DatabaseCaptureStore

from tests.database.helpers import canonical_capture
from tests.services.phase5_helpers import prepared_draft


class PublicationServiceTests(unittest.TestCase):
    def test_exact_preview_publishes_and_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database, draft_id, request = prepared_draft(root)
            published = PublicationService(database).publish(request)
            restarted = PublicationService(PrismDatabase(root / "prism.db"))
            restored = restarted.get(published.snapshot_id)
            draft = DraftService(PrismDatabase(root / "prism.db")).review(draft_id).draft

            self.assertTrue(published.created)
            self.assertEqual(draft.status, DraftStatus.PUBLISHED)
            self.assertEqual(restored.summary.status, SnapshotStatus.ACTIVE)
            self.assertIsNotNone(restored.content)
            assert restored.content is not None
            self.assertEqual(
                [message.content for message in restored.content.messages],
                ["First question", "First answer"],
            )
            rendered = json.dumps(restored.model_dump(mode="json"))
            self.assertNotIn("Second question", rendered)
            self.assertNotIn(canonical_capture().source.source_fingerprint, rendered)
            self.assertNotIn(canonical_capture().capture_id, rendered)
            self.assertNotIn(draft_id, rendered)

    def test_identical_retry_reuses_snapshot_and_publication_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, _, request = prepared_draft(Path(directory))
            service = PublicationService(database)
            first = service.publish(request)
            second = service.publish(request)

            self.assertTrue(first.created)
            self.assertFalse(second.created)
            self.assertEqual(second.snapshot_id, first.snapshot_id)
            self.assertEqual(second.publication_id, first.publication_id)
            with closing(sqlite3.connect(database.path)) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM publication_events"
                    ).fetchone()[0],
                    1,
                )

    def test_different_drafts_with_identical_bytes_deduplicate_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database, _, first_request = prepared_draft(root)
            first = PublicationService(database).publish(first_request)
            _, _, second_request = prepared_draft(
                root,
                create_capture=False,
            )
            second = PublicationService(database).publish(second_request)

            self.assertFalse(second.created)
            self.assertEqual(first.snapshot_id, second.snapshot_id)
            self.assertNotEqual(first.publication_id, second.publication_id)
            with closing(sqlite3.connect(database.path)) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM snapshot_payloads").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM publication_events"
                    ).fetchone()[0],
                    2,
                )

    def test_publication_requires_current_recorded_preview(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = PrismDatabase(root / "prism.db")
            stored = DatabaseCaptureStore(database).save(canonical_capture())
            assert stored.draft_id is not None
            review = DraftService(database).review(stored.draft_id)
            selected = DraftService(database).replace_selection(
                stored.draft_id,
                DraftSelectionRequest(
                    expected_revision=1,
                    selected_turn_ids=(review.turns[0].turn.turn_id,),
                ),
            )
            with self.assertRaises(PreviewRequiredError):
                PublicationService(database).publish(
                    PublicationRequest(
                        draft_id=stored.draft_id,
                        expected_revision=selected.revision,
                        expected_preview_hash="sha256:" + "0" * 64,
                    )
                )

    def test_selection_change_invalidates_old_publication_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, draft_id, request = prepared_draft(Path(directory))
            drafts = DraftService(database)
            review = drafts.review(draft_id)
            drafts.replace_selection(
                draft_id,
                DraftSelectionRequest(
                    expected_revision=request.expected_revision,
                    selected_turn_ids=(review.turns[1].turn.turn_id,),
                ),
            )
            with self.assertRaises(DraftChangedError):
                PublicationService(database).publish(request)

    def test_capture_tampering_after_preview_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, _, request = prepared_draft(Path(directory))
            with database.session() as session:
                message = session.query(CaptureMessageRow).order_by(
                    CaptureMessageRow.ordinal
                ).first()
                assert message is not None
                message.content = "tampered after preview"
                session.commit()

            with self.assertRaises(PreviewChangedError):
                PublicationService(database).publish(request)
            with closing(sqlite3.connect(database.path)) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0],
                    0,
                )

    def test_write_failure_rolls_back_every_publication_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, _, request = prepared_draft(Path(directory))
            with (
                patch(
                    "prism.repositories.snapshots.PublicationEventRepository.record",
                    side_effect=RuntimeError("injected failure"),
                ),
                self.assertRaises(RuntimeError),
            ):
                PublicationService(database).publish(request)

            with closing(sqlite3.connect(database.path)) as connection:
                for table in (
                    "snapshots",
                    "snapshot_payloads",
                    "snapshot_lifecycle",
                    "publication_events",
                ):
                    self.assertEqual(
                        connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
                        0,
                    )

    def test_full_hash_collision_with_different_bytes_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database, _, request = prepared_draft(Path(directory))
            wrong = SnapshotContent(
                schema_version="prism.snapshot.v1",
                title="Different content",
                messages=(
                    SnapshotMessage(
                        message_id="pubmsg_wrong_user",
                        turn_id="pubturn_wrong",
                        ordinal=1,
                        role=MessageRole.USER,
                        content="wrong user content",
                    ),
                    SnapshotMessage(
                        message_id="pubmsg_wrong_assistant",
                        turn_id="pubturn_wrong",
                        ordinal=2,
                        role=MessageRole.ASSISTANT,
                        content="wrong assistant content",
                    ),
                ),
            )
            wrong_bytes = ProjectionService.canonical_snapshot_bytes(wrong)
            snapshot_id = "snp_" + request.expected_preview_hash.split(":", 1)[1]
            database.initialize()
            with database.session() as session:
                session.add(
                    SnapshotRow(
                        snapshot_id=snapshot_id,
                        schema_version=wrong.schema_version,
                        content_hash=request.expected_preview_hash,
                        published_at="2026-09-20T00:00:00Z",
                    )
                )
                session.flush()
                session.add(
                    SnapshotPayloadRow(
                        snapshot_id=snapshot_id,
                        content_json=wrong_bytes.decode("utf-8"),
                        byte_size=len(wrong_bytes),
                    )
                )
                session.add(
                    SnapshotLifecycleRow(
                        snapshot_id=snapshot_id,
                        status=SnapshotStatus.ACTIVE.value,
                    )
                )
                session.commit()

            with self.assertRaises(SnapshotCollisionError):
                PublicationService(database).publish(request)


if __name__ == "__main__":
    unittest.main()
