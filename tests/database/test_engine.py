from __future__ import annotations

import sqlite3
import stat
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from sqlalchemy import text
from alembic import command
from alembic.config import Config

from prism.database import PrismDatabase
from prism.storage import DatabaseCaptureStore

from tests.database.helpers import canonical_capture


class PrismDatabaseTests(unittest.TestCase):
    def test_head_migration_upgrades_phase4_database_without_losing_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prism.db"
            database = PrismDatabase(path)
            config = Config()
            config.set_main_option(
                "script_location",
                str(
                    Path(__file__).resolve().parents[2]
                    / "src"
                    / "prism"
                    / "database"
                    / "migrations"
                ),
            )
            connection = database.engine.connect()
            config.attributes["connection"] = connection
            try:
                command.upgrade(config, "0001_durable_owner_state")
            finally:
                connection.close()
            with database.engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO captures "
                        "(capture_id, schema_version, platform, method, adapter_version, "
                        "source_fingerprint, conversation_ref, title, captured_at, "
                        "capture_hash, created_at) VALUES "
                        "(:capture_id, 'prism.capture.v1', 'chatgpt', 'synthetic', "
                        "'test/1', 'sha256:test', 'conversation', 'Preserved', "
                        "'2026-09-20T00:00:00Z', 'sha256:capture', "
                        "'2026-09-20T00:00:00Z')"
                    ),
                    {"capture_id": "cap_preserved_phase4_state"},
                )

            database.initialize()

            with database.engine.connect() as connection:
                version = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                title = connection.execute(
                    text(
                        "SELECT title FROM captures "
                        "WHERE capture_id = 'cap_preserved_phase4_state'"
                    )
                ).scalar_one()
                phase5_tables = connection.execute(
                    text(
                        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' "
                        "AND name IN ('snapshots', 'shares', 'invitations', 'grants')"
                    )
                ).scalar_one()

            self.assertEqual(version, "0006_provenance_taint")
            self.assertEqual(title, "Preserved")
            self.assertEqual(phase5_tables, 4)

    def test_migration_pragmas_permissions_and_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "owner" / "prism.db"
            database = PrismDatabase(path)
            database.initialize()

            with database.engine.connect() as connection:
                version = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                foreign_keys = connection.execute(
                    text("PRAGMA foreign_keys")
                ).scalar_one()
                synchronous = connection.execute(
                    text("PRAGMA synchronous")
                ).scalar_one()
                journal_mode = connection.execute(
                    text("PRAGMA journal_mode")
                ).scalar_one()

            self.assertEqual(version, "0006_provenance_taint")
            self.assertEqual(foreign_keys, 1)
            self.assertEqual(synchronous, 2)
            self.assertIn(journal_mode, {"wal", "delete"})
            self.assertTrue(database.quick_check())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

            with closing(sqlite3.connect(path)) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
            self.assertTrue(
                {
                    "snapshots",
                    "snapshot_payloads",
                    "publication_events",
                    "shares",
                    "share_versions",
                    "invitations",
                    "grants",
                    "sharing_events",
                    "oauth_clients",
                    "oauth_tickets",
                    "oauth_codes",
                    "oauth_tokens",
                    "draft_finding_decisions",
                    "snapshot_receipts",
                }.issubset(tables)
            )
            self.assertNotIn("recipient_sessions", tables)

    def test_phase7_migration_preserves_phase5_share_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prism.db"
            database = PrismDatabase(path)
            config = Config()
            config.set_main_option(
                "script_location",
                str(
                    Path(__file__).resolve().parents[2]
                    / "src"
                    / "prism"
                    / "database"
                    / "migrations"
                ),
            )
            connection = database.engine.connect()
            config.attributes["connection"] = connection
            try:
                command.upgrade(config, "0002_phase5_publication_sharing")
            finally:
                connection.close()
            with database.engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO shares "
                        "(share_id, name, status, created_at, updated_at) VALUES "
                        "('shr_preserved_phase5_state', 'Preserved share', 'active', "
                        "'2026-09-20T00:00:00Z', '2026-09-20T00:00:00Z')"
                    )
                )

            database.initialize()

            with database.engine.connect() as connection:
                version = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                name = connection.execute(
                    text(
                        "SELECT name FROM shares "
                        "WHERE share_id = 'shr_preserved_phase5_state'"
                    )
                ).scalar_one()
                session_table = connection.execute(
                    text(
                        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' "
                        "AND name = 'recipient_sessions'"
                    )
                ).scalar_one()
                event_columns = {
                    row[1]
                    for row in connection.execute(text("PRAGMA table_info(sharing_events)"))
                }

            self.assertEqual(version, "0006_provenance_taint")
            self.assertEqual(name, "Preserved share")
            self.assertEqual(session_table, 0)
            self.assertNotIn("recipient_session_id", event_columns)
            self.assertIn("detail", event_columns)

    def test_projection_layer_migration_adds_title_override_and_preserves_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prism.db"
            database = PrismDatabase(path)
            config = Config()
            config.set_main_option(
                "script_location",
                str(
                    Path(__file__).resolve().parents[2]
                    / "src"
                    / "prism"
                    / "database"
                    / "migrations"
                ),
            )
            connection = database.engine.connect()
            config.attributes["connection"] = connection
            try:
                command.upgrade(config, "0004_phase7_identity_oauth")
            finally:
                connection.close()
            with database.engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO shares "
                        "(share_id, name, status, created_at, updated_at) VALUES "
                        "('shr_preserved_phase7_state', 'Preserved before 0005', 'active', "
                        "'2026-09-21T00:00:00Z', '2026-09-21T00:00:00Z')"
                    )
                )

            database.initialize()

            with database.engine.connect() as connection:
                version = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                name = connection.execute(
                    text(
                        "SELECT name FROM shares "
                        "WHERE share_id = 'shr_preserved_phase7_state'"
                    )
                ).scalar_one()
                draft_columns = {
                    row[1] for row in connection.execute(text("PRAGMA table_info(share_drafts)"))
                }
                new_tables = {
                    row[0]
                    for row in connection.execute(
                        text(
                            "SELECT name FROM sqlite_master WHERE type = 'table' "
                            "AND name IN ('draft_finding_decisions', 'snapshot_receipts')"
                        )
                    )
                }

            self.assertEqual(version, "0006_provenance_taint")
            self.assertEqual(name, "Preserved before 0005")
            self.assertIn("title_override", draft_columns)
            self.assertEqual(new_tables, {"draft_finding_decisions", "snapshot_receipts"})

    def test_concurrent_initialization_from_several_threads_is_safe(self) -> None:
        import threading

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prism.db"
            PrismDatabase(path).initialize()
            errors: list[BaseException] = []

            def initialize() -> None:
                try:
                    PrismDatabase(path).initialize()
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)

            threads = [threading.Thread(target=initialize) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(errors, [])

    def test_online_backup_contains_committed_state_and_is_restorable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = PrismDatabase(root / "prism.db")
            capture = canonical_capture()
            stored = DatabaseCaptureStore(database).save(capture)

            backup = database.backup()

            self.assertTrue(backup.exists())
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
            with closing(sqlite3.connect(backup)) as connection:
                self.assertEqual(
                    connection.execute("PRAGMA quick_check").fetchone()[0],
                    "ok",
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM captures").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM share_drafts"
                    ).fetchone()[0],
                    1,
                )
            self.assertIsNotNone(stored.draft_id)


if __name__ == "__main__":
    unittest.main()
