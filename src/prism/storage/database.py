"""Database-backed capture store used by production owner workflows."""

from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError

from ..database import PrismDatabase, PrismUnitOfWork
from ..exceptions import DatabaseError
from ..models.capture import CapturedSession
from ..projection.provenance import ToolReadEvent, ToolReadEventRecord
from ..protocols.capture_store import StoredCapture


class DatabaseCaptureStore:
    """Persist a capture and its excluded-by-default draft atomically."""

    def __init__(self, database: PrismDatabase) -> None:
        self._database = database
        self._database.initialize()

    def save(self, capture: CapturedSession) -> StoredCapture:
        try:
            with PrismUnitOfWork(self._database) as unit:
                unit.captures.add(capture)
                draft = unit.drafts.create_for_capture(capture.capture_id)
                unit.commit()
        except SQLAlchemyError as exc:
            raise DatabaseError(
                "The capture and initial draft could not be persisted"
            ) from exc
        return StoredCapture(
            artifact_path=self._database.path,
            draft_id=draft.draft_id,
            draft_revision=draft.revision,
        )

    def load(self, capture_id: str) -> CapturedSession:
        try:
            with PrismUnitOfWork(self._database) as unit:
                return unit.captures.load(capture_id)
        except SQLAlchemyError as exc:
            raise DatabaseError("The capture could not be read") from exc

    def save_tool_events(
        self,
        capture_id: str,
        events: tuple[ToolReadEvent, ...],
    ) -> tuple[ToolReadEventRecord, ...]:
        """Persist a capture's Phase 9 provenance timeline, once, at capture time.

        Not part of the generic ``CaptureStore`` protocol — only meaningful
        for sources that can report tool-call provenance (Claude Code today).
        ``CaptureService`` calls this only when it exists, via duck typing,
        the same pattern used for ``ProvenanceCaptureAdapter`` itself.
        """

        if not events:
            return ()
        try:
            with PrismUnitOfWork(self._database) as unit:
                created = unit.tool_events.add_all(capture_id, events)
                unit.commit()
                return created
        except SQLAlchemyError as exc:
            raise DatabaseError("The provenance timeline could not be persisted") from exc
