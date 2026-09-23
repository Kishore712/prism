"""SQLite engine, migration, integrity, and backup management."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from ..exceptions import DatabaseError


# Alembic's environment context is process-global and not thread-safe, so every
# migration run in this process is serialized (a server thread and an approval
# thread both open the database at startup).
_MIGRATION_LOCK = threading.Lock()


class PrismDatabase:
    """Own the configured SQLite database and its connection policy."""

    def __init__(self, database_path: Path) -> None:
        self.path = database_path.expanduser().resolve()
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            self._prepare_directory()
            self._engine = create_engine(
                f"sqlite+pysqlite:///{self.path}",
                connect_args={"timeout": 5.0},
                future=True,
                pool_pre_ping=True,
                poolclass=NullPool,
            )
            event.listen(self._engine, "connect", self._configure_connection)
            self._session_factory = sessionmaker(
                bind=self._engine,
                class_=Session,
                autoflush=False,
                expire_on_commit=False,
            )
        return self._engine

    def initialize(self) -> None:
        """Apply every checked-in migration and secure the resulting files."""

        with _MIGRATION_LOCK:
            self._migrate()
        self._secure_live_files()

    def _migrate(self) -> None:
        engine = self.engine
        config = Config()
        config.set_main_option(
            "script_location",
            str(Path(__file__).with_name("migrations")),
        )
        config.attributes["connection"] = engine.connect()
        try:
            command.upgrade(config, "head")
        except Exception as exc:
            raise DatabaseError("The Prism database migration failed") from exc
        finally:
            config.attributes["connection"].close()

    def session(self) -> Session:
        if self._session_factory is None:
            _ = self.engine
        assert self._session_factory is not None
        return self._session_factory()

    def quick_check(self) -> bool:
        try:
            with self.engine.connect() as connection:
                result = connection.execute(text("PRAGMA quick_check")).scalar_one()
            return result == "ok"
        except Exception as exc:
            raise DatabaseError("The Prism database integrity check failed") from exc

    def backup(self, backup_directory: Path | None = None) -> Path:
        """Create and validate one consistent backup using SQLite's backup API."""

        self.initialize()
        destination_root = (
            backup_directory.expanduser().resolve()
            if backup_directory is not None
            else self.path.parent / "backups"
        )
        destination_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(destination_root, 0o700)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".prism-backup-",
            suffix=".db.tmp",
            dir=destination_root,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        from datetime import datetime, timezone

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = destination_root / f"prism-{timestamp}.db"
        try:
            source_connection = sqlite3.connect(self.path)
            destination_connection = sqlite3.connect(temporary)
            try:
                source_connection.backup(destination_connection)
                check = destination_connection.execute("PRAGMA quick_check").fetchone()
                if check is None or check[0] != "ok":
                    raise DatabaseError("The created backup failed its integrity check")
            finally:
                destination_connection.close()
                source_connection.close()
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
            os.chmod(target, 0o600)
            return target
        except DatabaseError:
            temporary.unlink(missing_ok=True)
            raise
        except (OSError, sqlite3.Error) as exc:
            temporary.unlink(missing_ok=True)
            raise DatabaseError("The Prism database backup failed") from exc

    def dispose(self) -> None:
        if self._engine is not None:
            self._engine.dispose()

    def _prepare_directory(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)

    def _secure_live_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{self.path}{suffix}")
            if candidate.exists():
                os.chmod(candidate, 0o600)

    @staticmethod
    def _configure_connection(
        dbapi_connection: sqlite3.Connection,
        _connection_record: object,
    ) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA busy_timeout = 5000")
            cursor.execute("PRAGMA synchronous = FULL")
            if sqlite3.sqlite_version_info >= (3, 51, 3):
                cursor.execute("PRAGMA journal_mode = WAL")
            else:
                cursor.execute("PRAGMA journal_mode = DELETE")
        finally:
            cursor.close()
