"""Staged, owner-confirmed capture workflow for remote public snapshots."""

from __future__ import annotations

import hashlib
import hmac
import json

from ..exceptions import PreviewChangedError
from ..models.capture import (
    CapturePreview,
    CapturePreviewMessage,
    CapturePreviewSource,
    CaptureResult,
    RemoteCaptureSource,
    StagedCapture,
)
from ..protocols.remote_capture import RemoteCaptureAdapter, TemporaryCaptureStore
from .capture import CaptureService


class SharedLinkCaptureService:
    """Coordinate remote inspection, temporary review, and canonical persistence."""

    schema_version = "prism.capture-preview.v1"

    def __init__(
        self,
        adapter: RemoteCaptureAdapter,
        temporary_store: TemporaryCaptureStore,
        capture_service: CaptureService,
    ) -> None:
        self._adapter = adapter
        self._temporary_store = temporary_store
        self._capture_service = capture_service

    def inspect(self, source: RemoteCaptureSource) -> CapturePreview:
        candidate = self._adapter.inspect(source)
        preview_hash = self._preview_hash(candidate.model_dump(mode="json"))
        staged = self._temporary_store.stage(
            self._adapter.version,
            candidate,
            preview_hash,
        )
        return self._preview(staged)

    def confirm(self, import_id: str, expected_preview_hash: str) -> CaptureResult:
        staged = self._temporary_store.get(import_id)
        if not hmac.compare_digest(staged.preview_hash, expected_preview_hash):
            raise PreviewChangedError(
                "The capture candidate no longer matches the reviewed preview"
            )
        result = self._capture_service.persist(
            staged.adapter_version,
            staged.capture,
        )
        self._temporary_store.delete(import_id)
        return result

    def cancel(self, import_id: str) -> None:
        self._temporary_store.delete(import_id)

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
