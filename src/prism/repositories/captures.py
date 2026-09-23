"""Relational repository for immutable canonical captures."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import (
    CaptureMessageRow,
    CaptureResourceRow,
    CaptureRow,
    CaptureTurnRow,
    CaptureWarningRow,
)
from ..exceptions import DatabaseError, NotFoundError
from ..models.capture import (
    CaptureMethod,
    CaptureProvenance,
    CapturedMessage,
    CapturedResource,
    CapturedSession,
    CaptureWarning,
    MessageRole,
    Platform,
    ResourceAvailability,
    WarningSeverity,
)
from ..clock import utc_now


class CaptureRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, capture: CapturedSession) -> None:
        if self._session.get(CaptureRow, capture.capture_id) is not None:
            raise DatabaseError("A capture with this identifier already exists")
        self._session.add(
            CaptureRow(
                capture_id=capture.capture_id,
                schema_version=capture.schema_version,
                platform=capture.source.platform.value,
                method=capture.source.method.value,
                adapter_version=capture.source.adapter_version,
                source_fingerprint=capture.source.source_fingerprint,
                conversation_ref=capture.source.conversation_ref,
                title=capture.title,
                captured_at=capture.captured_at,
                capture_hash=capture.capture_hash,
                created_at=utc_now(),
            )
        )
        self._session.flush()
        turn_ids: list[str] = []
        for message in capture.messages:
            if message.turn_id not in turn_ids:
                turn_ids.append(message.turn_id)
        self._session.add_all(
            CaptureTurnRow(
                turn_id=turn_id,
                capture_id=capture.capture_id,
                turn_index=index,
            )
            for index, turn_id in enumerate(turn_ids, start=1)
        )
        self._session.flush()
        self._session.add_all(
            CaptureMessageRow(
                message_id=message.message_id,
                capture_id=capture.capture_id,
                turn_id=message.turn_id,
                ordinal=message.ordinal,
                role=message.role.value,
                content=message.content,
            )
            for message in capture.messages
        )
        self._session.add_all(
            CaptureResourceRow(
                resource_id=resource.resource_id,
                capture_id=capture.capture_id,
                ordinal=index,
                display_name=resource.display_name,
                media_type=resource.media_type,
                availability=resource.availability.value,
            )
            for index, resource in enumerate(capture.resources, start=1)
        )
        self._session.add_all(
            CaptureWarningRow(
                capture_id=capture.capture_id,
                ordinal=index,
                code=warning.code,
                message=warning.message,
                severity=warning.severity.value,
            )
            for index, warning in enumerate(capture.warnings, start=1)
        )
        self._session.flush()

    def load(self, capture_id: str) -> CapturedSession:
        row = self._session.get(CaptureRow, capture_id)
        if row is None:
            raise NotFoundError("The requested capture does not exist")
        messages = self._session.scalars(
            select(CaptureMessageRow)
            .where(CaptureMessageRow.capture_id == capture_id)
            .order_by(CaptureMessageRow.ordinal)
        ).all()
        resources = self._session.scalars(
            select(CaptureResourceRow)
            .where(CaptureResourceRow.capture_id == capture_id)
            .order_by(CaptureResourceRow.ordinal)
        ).all()
        warnings = self._session.scalars(
            select(CaptureWarningRow)
            .where(CaptureWarningRow.capture_id == capture_id)
            .order_by(CaptureWarningRow.ordinal)
        ).all()
        return CapturedSession(
            schema_version=row.schema_version,
            capture_id=row.capture_id,
            source=CaptureProvenance(
                platform=Platform(row.platform),
                method=CaptureMethod(row.method),
                adapter_version=row.adapter_version,
                source_fingerprint=row.source_fingerprint,
                conversation_ref=row.conversation_ref,
            ),
            title=row.title,
            captured_at=row.captured_at,
            messages=tuple(
                CapturedMessage(
                    message_id=message.message_id,
                    turn_id=message.turn_id,
                    ordinal=message.ordinal,
                    role=MessageRole(message.role),
                    content=message.content,
                )
                for message in messages
            ),
            resources=tuple(
                CapturedResource(
                    resource_id=resource.resource_id,
                    display_name=resource.display_name,
                    media_type=resource.media_type,
                    availability=ResourceAvailability(resource.availability),
                )
                for resource in resources
            ),
            warnings=tuple(
                CaptureWarning(
                    code=warning.code,
                    message=warning.message,
                    severity=WarningSeverity(warning.severity),
                )
                for warning in warnings
            ),
            capture_hash=row.capture_hash,
        )

    def exists(self, capture_id: str) -> bool:
        return self._session.get(CaptureRow, capture_id) is not None
