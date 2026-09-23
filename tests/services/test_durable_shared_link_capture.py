from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from prism.database import PrismDatabase, PrismUnitOfWork
from prism.exceptions import NotFoundError, PreviewChangedError, ValidationError
from prism.models.capture import RemoteCaptureSource
from prism.providers.chatgpt.shared_link import ChatGPTSharedLinkAdapter
from prism.repositories.drafts import DraftRepository
from prism.services.durable_shared_link_capture import DurableSharedLinkCaptureService
from prism.services.normalization import CaptureNormalizer

from tests.database.helpers import VALID_URL
from tests.providers.chatgpt.shared_link.helpers import StaticFetcher, synthetic_shared_page


class DurableSharedLinkCaptureTests(unittest.TestCase):
    def _service(self, database: PrismDatabase) -> DurableSharedLinkCaptureService:
        return DurableSharedLinkCaptureService(
            ChatGPTSharedLinkAdapter(fetcher=StaticFetcher(synthetic_shared_page())),
            CaptureNormalizer(),
            database,
        )

    def test_candidate_survives_service_restart_without_source_url_or_html(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prism.db"
            first = self._service(PrismDatabase(path))
            preview = first.inspect(RemoteCaptureSource(url=VALID_URL))

            second = self._service(PrismDatabase(path))
            resumed = second.resume(preview.import_id)

            self.assertEqual(resumed, preview)
            with closing(sqlite3.connect(path)) as connection:
                dump = "\n".join(connection.iterdump())
            self.assertNotIn(VALID_URL, dump)
            self.assertNotIn("<!doctype html>", dump)
            self.assertNotIn("native-conversation-id", dump)

    def test_confirmation_atomically_creates_capture_draft_and_removes_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = PrismDatabase(Path(directory) / "prism.db")
            service = self._service(database)
            preview = service.inspect(RemoteCaptureSource(url=VALID_URL))

            result = service.confirm(preview.import_id, preview.preview_hash)

            self.assertIsNotNone(result.draft_id)
            self.assertEqual(result.draft_revision, 1)
            with PrismUnitOfWork(database) as unit:
                capture = unit.captures.load(result.capture_id)
                draft = unit.drafts.get(result.draft_id or "")
                with self.assertRaises(NotFoundError):
                    unit.candidates.get(preview.import_id)
            self.assertEqual(len(capture.messages), 4)
            self.assertEqual(draft.capture_id, capture.capture_id)
            self.assertEqual(draft.selected_turn_ids, ())

    def test_stale_hash_keeps_candidate_and_creates_no_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = PrismDatabase(Path(directory) / "prism.db")
            service = self._service(database)
            preview = service.inspect(RemoteCaptureSource(url=VALID_URL))

            with self.assertRaises(PreviewChangedError):
                service.confirm(preview.import_id, "sha256:" + "0" * 64)

            self.assertEqual(service.resume(preview.import_id).import_id, preview.import_id)
            with closing(sqlite3.connect(database.path)) as connection:
                count = connection.execute("SELECT COUNT(*) FROM captures").fetchone()[0]
            self.assertEqual(count, 0)

    def test_failure_after_capture_insert_rolls_back_everything(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = PrismDatabase(Path(directory) / "prism.db")
            service = self._service(database)
            preview = service.inspect(RemoteCaptureSource(url=VALID_URL))

            with patch.object(
                DraftRepository,
                "create_for_capture",
                side_effect=RuntimeError("injected draft failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    service.confirm(preview.import_id, preview.preview_hash)

            self.assertEqual(service.resume(preview.import_id).import_id, preview.import_id)
            with closing(sqlite3.connect(database.path)) as connection:
                counts = tuple(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in ("captures", "share_drafts", "capture_messages")
                )
            self.assertEqual(counts, (0, 0, 0))

    def test_candidate_limit_and_cancel_are_durable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = PrismDatabase(Path(directory) / "prism.db")
            service = self._service(database)
            candidates = [
                service.inspect(RemoteCaptureSource(url=VALID_URL)) for _ in range(3)
            ]
            with self.assertRaises(ValidationError):
                service.inspect(RemoteCaptureSource(url=VALID_URL))

            service.cancel(candidates[0].import_id)
            with self.assertRaises(NotFoundError):
                service.resume(candidates[0].import_id)
            replacement = service.inspect(RemoteCaptureSource(url=VALID_URL))
            self.assertIsNotNone(replacement.import_id)


if __name__ == "__main__":
    unittest.main()
