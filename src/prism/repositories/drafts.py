"""Repository for revisioned, durable owner selection state."""

from __future__ import annotations

from uuid import uuid4

import hashlib

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..database.models import (
    CaptureResourceRow,
    CaptureTurnRow,
    DraftAttachmentRow,
    DraftResourceSelectionRow,
    DraftTurnSelectionRow,
    ShareDraftRow,
)
from ..exceptions import DraftChangedError, NotFoundError, ValidationError
from ..models.draft import DraftAttachmentInfo, DraftState, DraftStatus
from ..models.projection import ProjectionAttachment
from ..clock import utc_now


MAX_ATTACHMENTS = 20
MAX_ATTACHMENT_BYTES = 200 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 1024 * 1024


class DraftRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_for_capture(self, capture_id: str) -> DraftState:
        draft_id = f"drf_{uuid4().hex[:26]}"
        now = utc_now()
        draft = ShareDraftRow(
            draft_id=draft_id,
            capture_id=capture_id,
            revision=1,
            status=DraftStatus.EDITING.value,
            created_at=now,
            updated_at=now,
        )
        self._session.add(draft)
        self._session.flush()
        turns = self._session.scalars(
            select(CaptureTurnRow)
            .where(CaptureTurnRow.capture_id == capture_id)
            .order_by(CaptureTurnRow.turn_index)
        ).all()
        resources = self._session.scalars(
            select(CaptureResourceRow)
            .where(CaptureResourceRow.capture_id == capture_id)
            .order_by(CaptureResourceRow.ordinal)
        ).all()
        self._session.add_all(
            DraftTurnSelectionRow(
                draft_id=draft_id,
                capture_id=capture_id,
                turn_id=turn.turn_id,
                included=False,
            )
            for turn in turns
        )
        self._session.add_all(
            DraftResourceSelectionRow(
                draft_id=draft_id,
                capture_id=capture_id,
                resource_id=resource.resource_id,
                included=False,
            )
            for resource in resources
        )
        self._session.flush()
        return self._state(draft)

    def get(self, draft_id: str) -> DraftState:
        row = self._row(draft_id)
        return self._state(row)

    def list(self) -> tuple[DraftState, ...]:
        rows = self._session.scalars(
            select(ShareDraftRow).order_by(ShareDraftRow.updated_at.desc())
        ).all()
        return tuple(self._state(row) for row in rows)

    def replace_selection(
        self,
        draft_id: str,
        expected_revision: int,
        selected_turn_ids: tuple[str, ...],
        selected_resource_ids: tuple[str, ...],
    ) -> DraftState:
        row = self._row(draft_id)
        self._require_editable_revision(row, expected_revision)
        if len(set(selected_turn_ids)) != len(selected_turn_ids):
            raise ValidationError("A turn may be selected only once")
        if len(set(selected_resource_ids)) != len(selected_resource_ids):
            raise ValidationError("A resource may be selected only once")
        turn_rows = self._turn_rows(draft_id)
        resource_rows = self._resource_rows(draft_id)
        valid_turns = {item.turn_id for item in turn_rows}
        valid_resources = {item.resource_id for item in resource_rows}
        if set(selected_turn_ids) - valid_turns:
            raise ValidationError("The selection contains a turn outside this draft")
        if set(selected_resource_ids) - valid_resources:
            raise ValidationError("The selection contains a resource outside this draft")
        if selected_resource_ids:
            raise ValidationError(
                "Reference-only resources cannot enter a snapshot preview"
            )
        selected_turn_set = set(selected_turn_ids)
        selected_resource_set = set(selected_resource_ids)
        result = self._session.execute(
            update(ShareDraftRow)
            .where(
                ShareDraftRow.draft_id == draft_id,
                ShareDraftRow.revision == expected_revision,
                ShareDraftRow.status == DraftStatus.EDITING.value,
            )
            .values(
                revision=expected_revision + 1,
                previewed_revision=None,
                preview_hash=None,
                updated_at=utc_now(),
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise DraftChangedError(
                "The draft changed after it was read; reload it before continuing"
            )
        for item in turn_rows:
            item.included = item.turn_id in selected_turn_set
        for item in resource_rows:
            item.included = item.resource_id in selected_resource_set
        self._session.flush()
        self._session.refresh(row)
        return self._state(row)

    def add_attachment(
        self,
        draft_id: str,
        expected_revision: int,
        display_name: str,
        media_type: str,
        content: str,
    ) -> DraftState:
        row = self._row(draft_id)
        self._require_editable_revision(row, expected_revision)
        encoded = content.encode("utf-8")
        if not content.strip():
            raise ValidationError("An attached resource must not be empty")
        if len(encoded) > MAX_ATTACHMENT_BYTES:
            raise ValidationError(
                f"An attached resource may be at most {MAX_ATTACHMENT_BYTES // 1024} KiB"
            )
        existing = self._session.execute(
            select(func.count(), func.coalesce(func.sum(DraftAttachmentRow.byte_size), 0))
            .where(DraftAttachmentRow.draft_id == draft_id)
        ).one()
        if existing[0] >= MAX_ATTACHMENTS:
            raise ValidationError(f"A draft may have at most {MAX_ATTACHMENTS} attachments")
        if existing[1] + len(encoded) > MAX_TOTAL_ATTACHMENT_BYTES:
            raise ValidationError("The attached resources would exceed the 1 MiB total limit")
        next_ordinal = (
            self._session.scalar(
                select(func.coalesce(func.max(DraftAttachmentRow.ordinal), 0)).where(
                    DraftAttachmentRow.draft_id == draft_id
                )
            )
            or 0
        ) + 1
        self._session.add(
            DraftAttachmentRow(
                attachment_id=f"att_{uuid4().hex[:26]}",
                draft_id=draft_id,
                ordinal=next_ordinal,
                display_name=display_name,
                media_type=media_type,
                content=content,
                content_sha256="sha256:" + hashlib.sha256(encoded).hexdigest(),
                byte_size=len(encoded),
                created_at=utc_now(),
            )
        )
        return self._bump_revision(row, expected_revision)

    def remove_attachment(
        self,
        draft_id: str,
        expected_revision: int,
        attachment_id: str,
    ) -> DraftState:
        row = self._row(draft_id)
        self._require_editable_revision(row, expected_revision)
        result = self._session.execute(
            delete(DraftAttachmentRow).where(
                DraftAttachmentRow.draft_id == draft_id,
                DraftAttachmentRow.attachment_id == attachment_id,
            )
        )
        if result.rowcount != 1:
            raise NotFoundError("The requested attachment does not exist on this draft")
        return self._bump_revision(row, expected_revision)

    def set_title(
        self,
        draft_id: str,
        expected_revision: int,
        title: str | None,
    ) -> DraftState:
        """Override the published title, or clear the override with ``None``."""

        row = self._row(draft_id)
        self._require_editable_revision(row, expected_revision)
        if title is not None:
            title = " ".join(title.split())
            if not title:
                raise ValidationError("Title override must not be blank")
            if len(title) > 500:
                raise ValidationError("Title override must be at most 500 characters")
        row.title_override = title
        return self._bump_revision(row, expected_revision)

    def projection_attachments(self, draft_id: str) -> tuple[ProjectionAttachment, ...]:
        rows = self._session.scalars(
            select(DraftAttachmentRow)
            .where(DraftAttachmentRow.draft_id == draft_id)
            .order_by(DraftAttachmentRow.ordinal)
        ).all()
        return tuple(
            ProjectionAttachment(
                attachment_id=item.attachment_id,
                display_name=item.display_name,
                media_type=item.media_type,
                content=item.content,
            )
            for item in rows
        )

    def _bump_revision(self, row: ShareDraftRow, expected_revision: int) -> DraftState:
        result = self._session.execute(
            update(ShareDraftRow)
            .where(
                ShareDraftRow.draft_id == row.draft_id,
                ShareDraftRow.revision == expected_revision,
                ShareDraftRow.status == DraftStatus.EDITING.value,
            )
            .values(
                revision=expected_revision + 1,
                previewed_revision=None,
                preview_hash=None,
                updated_at=utc_now(),
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise DraftChangedError(
                "The draft changed after it was read; reload it before continuing"
            )
        self._session.flush()
        self._session.refresh(row)
        return self._state(row)

    def record_preview(
        self,
        draft_id: str,
        expected_revision: int,
        preview_hash: str,
    ) -> DraftState:
        row = self._row(draft_id)
        result = self._session.execute(
            update(ShareDraftRow)
            .where(
                ShareDraftRow.draft_id == draft_id,
                ShareDraftRow.revision == expected_revision,
                ShareDraftRow.status == DraftStatus.EDITING.value,
            )
            .values(
                previewed_revision=expected_revision,
                preview_hash=preview_hash,
                updated_at=utc_now(),
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise DraftChangedError(
                "The draft changed after it was read; reload it before continuing"
            )
        self._session.flush()
        self._session.refresh(row)
        return self._state(row)

    def mark_published(self, draft_id: str, expected_revision: int) -> DraftState:
        row = self._row(draft_id)
        if row.revision != expected_revision:
            raise DraftChangedError(
                "The draft changed after it was previewed; reload it before continuing"
            )
        if row.status == DraftStatus.PUBLISHED.value:
            return self._state(row)
        result = self._session.execute(
            update(ShareDraftRow)
            .where(
                ShareDraftRow.draft_id == draft_id,
                ShareDraftRow.revision == expected_revision,
                ShareDraftRow.status == DraftStatus.EDITING.value,
            )
            .values(status=DraftStatus.PUBLISHED.value, updated_at=utc_now())
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise DraftChangedError(
                "The draft changed after it was previewed; reload it before continuing"
            )
        self._session.flush()
        self._session.refresh(row)
        return self._state(row)

    def _row(self, draft_id: str) -> ShareDraftRow:
        row = self._session.get(ShareDraftRow, draft_id)
        if row is None:
            raise NotFoundError("The requested draft does not exist")
        return row

    def _state(self, row: ShareDraftRow) -> DraftState:
        selected_turn_ids = tuple(
            item.turn_id for item in self._turn_rows(row.draft_id) if item.included
        )
        selected_resource_ids = tuple(
            item.resource_id
            for item in self._resource_rows(row.draft_id)
            if item.included
        )
        return DraftState(
            draft_id=row.draft_id,
            capture_id=row.capture_id,
            revision=row.revision,
            status=DraftStatus(row.status),
            selected_turn_ids=selected_turn_ids,
            selected_resource_ids=selected_resource_ids,
            attachments=self._attachment_infos(row.draft_id),
            title_override=row.title_override,
            previewed_revision=row.previewed_revision,
            preview_hash=row.preview_hash,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _attachment_infos(self, draft_id: str) -> tuple[DraftAttachmentInfo, ...]:
        rows = self._session.scalars(
            select(DraftAttachmentRow)
            .where(DraftAttachmentRow.draft_id == draft_id)
            .order_by(DraftAttachmentRow.ordinal)
        ).all()
        return tuple(
            DraftAttachmentInfo(
                attachment_id=item.attachment_id,
                display_name=item.display_name,
                media_type=item.media_type,
                byte_size=item.byte_size,
                content_sha256=item.content_sha256,
            )
            for item in rows
        )

    def _turn_rows(self, draft_id: str) -> list[DraftTurnSelectionRow]:
        return list(
            self._session.scalars(
                select(DraftTurnSelectionRow)
                .join(
                    CaptureTurnRow,
                    (CaptureTurnRow.capture_id == DraftTurnSelectionRow.capture_id)
                    & (CaptureTurnRow.turn_id == DraftTurnSelectionRow.turn_id),
                )
                .where(DraftTurnSelectionRow.draft_id == draft_id)
                .order_by(CaptureTurnRow.turn_index)
            ).all()
        )

    def _resource_rows(self, draft_id: str) -> list[DraftResourceSelectionRow]:
        return list(
            self._session.scalars(
                select(DraftResourceSelectionRow)
                .join(
                    CaptureResourceRow,
                    (
                        CaptureResourceRow.capture_id
                        == DraftResourceSelectionRow.capture_id
                    )
                    & (
                        CaptureResourceRow.resource_id
                        == DraftResourceSelectionRow.resource_id
                    ),
                )
                .where(DraftResourceSelectionRow.draft_id == draft_id)
                .order_by(CaptureResourceRow.ordinal)
            ).all()
        )

    @staticmethod
    def _require_editable_revision(row: ShareDraftRow, expected_revision: int) -> None:
        if row.status != DraftStatus.EDITING.value or row.revision != expected_revision:
            raise DraftChangedError(
                "The draft changed after it was read; reload it before continuing"
            )
