"""Repositories for Phase 9's owner-only provenance timeline and taint decisions."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clock import utc_now
from ..database.models import CaptureToolEventRow, DraftTaintedEventRow
from ..exceptions import NotFoundError, ValidationError
from ..projection.provenance import TaintedEventRecord, ToolReadEvent, ToolReadEventRecord


class ToolEventRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_all(
        self,
        capture_id: str,
        events: tuple[ToolReadEvent, ...],
    ) -> tuple[ToolReadEventRecord, ...]:
        """Persist a capture's provenance timeline once, at capture time."""

        now = utc_now()
        rows = [
            CaptureToolEventRow(
                event_id=f"tce_{uuid4().hex[:26]}",
                capture_id=capture_id,
                turn_index=event.turn_index,
                tool_name=event.tool_name,
                resource_label=event.resource_label,
                created_at=now,
            )
            for event in events
        ]
        self._session.add_all(rows)
        self._session.flush()
        return tuple(self._record(row) for row in rows)

    def list_for_capture(self, capture_id: str) -> tuple[ToolReadEventRecord, ...]:
        rows = self._session.scalars(
            select(CaptureToolEventRow)
            .where(CaptureToolEventRow.capture_id == capture_id)
            .order_by(CaptureToolEventRow.turn_index)
        ).all()
        return tuple(self._record(row) for row in rows)

    def get(self, event_id: str) -> ToolReadEventRecord:
        row = self._session.get(CaptureToolEventRow, event_id)
        if row is None:
            raise NotFoundError("The requested tool-call event does not exist")
        return self._record(row)

    @staticmethod
    def _record(row: CaptureToolEventRow) -> ToolReadEventRecord:
        return ToolReadEventRecord(
            event_id=row.event_id,
            capture_id=row.capture_id,
            turn_index=row.turn_index,
            tool_name=row.tool_name,
            resource_label=row.resource_label,
            created_at=row.created_at,
        )


class TaintedEventRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def taint(self, draft_id: str, event_id: str, reason: str) -> TaintedEventRecord:
        reason = reason.strip()
        if not reason:
            raise ValidationError("A reason is required to taint a provenance event")
        if self._session.get(CaptureToolEventRow, event_id) is None:
            raise NotFoundError("The requested tool-call event does not exist")
        row = self._session.get(DraftTaintedEventRow, (draft_id, event_id))
        now = utc_now()
        if row is None:
            row = DraftTaintedEventRow(
                draft_id=draft_id, event_id=event_id, reason=reason, tainted_at=now
            )
            self._session.add(row)
        else:
            row.reason = reason
            row.tainted_at = now
        self._session.flush()
        return self._record(row)

    def untaint(self, draft_id: str, event_id: str) -> None:
        row = self._session.get(DraftTaintedEventRow, (draft_id, event_id))
        if row is None:
            raise NotFoundError("No taint decision exists for that event on this draft")
        self._session.delete(row)
        self._session.flush()

    def tainted_event_ids(self, draft_id: str) -> frozenset[str]:
        rows = self._session.scalars(
            select(DraftTaintedEventRow.event_id).where(
                DraftTaintedEventRow.draft_id == draft_id
            )
        ).all()
        return frozenset(rows)

    def list(self, draft_id: str) -> tuple[TaintedEventRecord, ...]:
        rows = self._session.scalars(
            select(DraftTaintedEventRow)
            .where(DraftTaintedEventRow.draft_id == draft_id)
            .order_by(DraftTaintedEventRow.tainted_at)
        ).all()
        return tuple(self._record(row) for row in rows)

    @staticmethod
    def _record(row: DraftTaintedEventRow) -> TaintedEventRecord:
        return TaintedEventRecord(
            draft_id=row.draft_id,
            event_id=row.event_id,
            reason=row.reason,
            tainted_at=row.tainted_at,
        )
