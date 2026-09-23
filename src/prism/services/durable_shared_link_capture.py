"""Transactional shared-link capture workflow for Phase 4B."""

from __future__ import annotations

import hashlib
import hmac
import json

from sqlalchemy.exc import SQLAlchemyError

from ..database import PrismDatabase, PrismUnitOfWork
from ..exceptions import DatabaseError, PreviewChangedError, ValidationError
from ..models.capture import (
    CapturePreview,
    CapturePreviewMessage,
    CapturePreviewSource,
    CaptureResult,
    RemoteCaptureSource,
    StagedCapture,
)
from ..protocols.remote_capture import RemoteCaptureAdapter
from .normalization import CaptureNormalizer


class DurableSharedLinkCaptureService:
    """Persist candidates and atomically confirm capture plus initial draft."""

    schema_version = "prism.capture-preview.v1"

    def __init__(
        self,
        adapter: RemoteCaptureAdapter,
        normalizer: CaptureNormalizer,
        database: PrismDatabase,
        *,
        max_candidates: int = 3,
    ) -> None:
        self._adapter = adapter
        self._normalizer = normalizer
        self._database = database
        self._max_candidates = max_candidates
        self._database.initialize()

    def inspect(self, source: RemoteCaptureSource) -> CapturePreview:
        candidate = self._adapter.inspect(source)
        preview_hash = self._preview_hash(candidate.model_dump(mode="json"))
        try:
            with PrismUnitOfWork(self._database) as unit:
                if len(unit.candidates.list_active()) >= self._max_candidates:
                    raise ValidationError(
                        "Too many temporary capture candidates are active"
                    )
                staged = unit.candidates.stage(
                    self._adapter.version,
                    candidate,
                    preview_hash,
                )
                unit.commit()
        except SQLAlchemyError as exc:
            raise DatabaseError("The capture candidate could not be persisted") from exc
        return self._preview(staged)

    def resume(self, import_id: str) -> CapturePreview:
        with PrismUnitOfWork(self._database) as unit:
            return self._preview(unit.candidates.get(import_id))

    def confirm(self, import_id: str, expected_preview_hash: str) -> CaptureResult:
        try:
            with PrismUnitOfWork(self._database) as unit:
                staged = unit.candidates.get(import_id)
                if not hmac.compare_digest(
                    staged.preview_hash,
                    expected_preview_hash,
                ):
                    raise PreviewChangedError(
                        "The capture candidate no longer matches the reviewed preview"
                    )
                canonical = self._normalizer.normalize(
                    staged.adapter_version,
                    staged.capture,
                )
                unit.captures.add(canonical)
                draft = unit.drafts.create_for_capture(canonical.capture_id)
                unit.candidates.delete(import_id)
                unit.commit()
        except (PreviewChangedError, ValidationError):
            raise
        except SQLAlchemyError as exc:
            raise DatabaseError("The capture could not be confirmed transactionally") from exc
        return CaptureResult(
            capture_id=canonical.capture_id,
            capture_hash=canonical.capture_hash,
            message_count=len(canonical.messages),
            resource_reference_count=len(canonical.resources),
            warnings=canonical.warnings,
            artifact_path=self._database.path,
            draft_id=draft.draft_id,
            draft_revision=draft.revision,
        )

    def cancel(self, import_id: str) -> None:
        with PrismUnitOfWork(self._database) as unit:
            unit.candidates.delete(import_id)
            unit.commit()

    def _preview(self, staged: StagedCapture) -> CapturePreview:
        return CapturePreview(
            schema_version=self.schema_version,
            import_id=staged.import_id,
            source=CapturePreviewSource(
                platform=staged.capture.platform,
                method=staged.capture.method,
                capture_scope="public_shared_snapshot",
            ),
            title=staged.capture.title,
            messages=tuple(
                CapturePreviewMessage(
                    source_message_ref=message.source_message_id,
                    turn_index=message.turn_index,
                    ordinal=message.ordinal,
                    role=message.role,
                    content=message.content,
                )
                for message in staged.capture.messages
            ),
            observations=staged.observations,
            warnings=staged.capture.warnings,
            preview_hash=staged.preview_hash,
            expires_at=staged.expires_at,
        )

    @staticmethod
    def _preview_hash(payload: dict[str, object]) -> str:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(
            b"PrismCapturePreview-v1\0" + canonical
        ).hexdigest()
