from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from prism.database import PrismDatabase, PrismUnitOfWork
from prism.exceptions import CaptureReadError
from prism.services.database_admin import LegacyCaptureMigrationService
from prism.storage import LocalCaptureStore

from tests.database.helpers import canonical_capture


class LegacyCaptureMigrationTests(unittest.TestCase):
    def test_migration_is_idempotent_and_preserves_legacy_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capture = canonical_capture()
            artifact = LocalCaptureStore(root).save(capture).artifact_path
            database = PrismDatabase(root / "prism.db")
            migrator = LegacyCaptureMigrationService(database)

            first = migrator.migrate(root / "captures")
            second = migrator.migrate(root / "captures")

            self.assertEqual(first.imported, 1)
            self.assertEqual(first.already_imported, 0)
            self.assertEqual(second.imported, 0)
            self.assertEqual(second.already_imported, 1)
            self.assertTrue(artifact.exists())
            with PrismUnitOfWork(database) as unit:
                restored = unit.captures.load(capture.capture_id)
                drafts = unit.drafts.list()
            self.assertEqual(restored, capture)
            self.assertEqual(len(drafts), 1)

    def test_malformed_later_file_does_not_rollback_prior_valid_import(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capture = canonical_capture()
            LocalCaptureStore(root).save(capture)
            malformed = root / "captures" / "zzz_invalid"
            malformed.mkdir()
            (malformed / "capture.json").write_text("{not-json", encoding="utf-8")
            database = PrismDatabase(root / "prism.db")

            with self.assertRaises(CaptureReadError):
                LegacyCaptureMigrationService(database).migrate(root / "captures")

            with PrismUnitOfWork(database) as unit:
                restored = unit.captures.load(capture.capture_id)
            self.assertEqual(restored.capture_hash, capture.capture_hash)


if __name__ == "__main__":
    unittest.main()
