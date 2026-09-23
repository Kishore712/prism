"""Database backup, integrity, and legacy-capture migration services."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from ..database import PrismDatabase, PrismUnitOfWork
from ..database.models import LegacyImportRow, ShareDraftRow
from ..exceptions import CaptureReadError, DatabaseError
from ..models.capture import CapturedSession
from ..models.draft import LegacyMigrationResult
from ..clock import utc_now


class LegacyCaptureMigrationService:
    def __init__(self, database: PrismDatabase) -> None:
        self._database = database
        self._database.initialize()

    def migrate(self, legacy_capture_root: Path) -> LegacyMigrationResult:
        root = legacy_capture_root.expanduser().resolve()
        paths = sorted(root.glob("*/capture.json")) if root.exists() else []
        imported_ids: list[str] = []
        imported = 0
        already_imported = 0
        for path in paths:
            try:
                payload = path.read_bytes()
                capture = CapturedSession.model_validate_json(payload)
            except (OSError, PydanticValidationError) as exc:
                raise CaptureReadError(
                    f"Legacy capture '{path.parent.name}' failed validation"
                ) from exc
            fingerprint = "sha256:" + hashlib.sha256(
                b"PrismLegacyCapture-v1\0" + payload
            ).hexdigest()
            try:
                with PrismUnitOfWork(self._database) as unit:
                    assert unit.session is not None
                    existing_import = unit.session.get(LegacyImportRow, fingerprint)
                    if existing_import is not None:
                        already_imported += 1
                        continue
                    if unit.captures.exists(capture.capture_id):
                        if unit.captures.load(capture.capture_id) != capture:
                            raise DatabaseError(
                                "A legacy capture identifier conflicts with different content"
                            )
                        existing_draft = unit.session.scalar(
                            select(ShareDraftRow).where(
                                ShareDraftRow.capture_id == capture.capture_id
                            )
                        )
                        if existing_draft is None:
                            unit.drafts.create_for_capture(capture.capture_id)
                    else:
                        unit.captures.add(capture)
                        unit.drafts.create_for_capture(capture.capture_id)
                    unit.session.add(
                        LegacyImportRow(
                            file_fingerprint=fingerprint,
                            capture_id=capture.capture_id,
                            imported_at=utc_now(),
                        )
                    )
                    unit.commit()
            except SQLAlchemyError as exc:
                raise DatabaseError("A legacy capture could not be imported") from exc
            imported += 1
            imported_ids.append(capture.capture_id)
        return LegacyMigrationResult(
            scanned=len(paths),
            imported=imported,
            already_imported=already_imported,
            capture_ids=tuple(imported_ids),
        )
