from __future__ import annotations

from collections.abc import Mapping

from ..models.capture import (
    AdapterCapture,
    CaptureInventory,
    CaptureResult,
    CaptureSelection,
    CaptureSource,
)
from ..exceptions import ValidationError
from ..protocols.capture_source import SourceCaptureAdapter
from ..protocols.capture_store import CaptureStore
from ..protocols.provenance_source import ProvenanceCaptureAdapter
from .normalization import CaptureNormalizer


class CaptureService:
    """Coordinates acquisition, normalization, and owner-only persistence."""

    def __init__(
        self,
        adapters: Mapping[str, SourceCaptureAdapter],
        normalizer: CaptureNormalizer,
        store: CaptureStore,
    ) -> None:
        self._adapters = dict(adapters)
        self._normalizer = normalizer
        self._store = store

    def inventory(self, source: CaptureSource, adapter_name: str) -> CaptureInventory:
        return self._adapter(adapter_name).inventory(source)

    def capture(
        self,
        source: CaptureSource,
        adapter_name: str,
        selection: CaptureSelection,
    ) -> CaptureResult:
        adapter = self._adapter(adapter_name)
        adapter_capture = adapter.capture(source, selection)
        result = self.persist(adapter.version, adapter_capture)
        self._persist_provenance(adapter, source, selection, result.capture_id)
        return result

    def persist(
        self,
        adapter_version: str,
        adapter_capture: AdapterCapture,
    ) -> CaptureResult:
        """Persist an already acquired adapter capture through the canonical path."""

        canonical = self._normalizer.normalize(adapter_version, adapter_capture)
        stored = self._store.save(canonical)
        return CaptureResult(
            capture_id=canonical.capture_id,
            capture_hash=canonical.capture_hash,
            message_count=len(canonical.messages),
            resource_reference_count=len(canonical.resources),
            warnings=canonical.warnings,
            artifact_path=stored.artifact_path,
            draft_id=stored.draft_id,
            draft_revision=stored.draft_revision,
        )

    def _persist_provenance(
        self,
        adapter: SourceCaptureAdapter,
        source: CaptureSource,
        selection: CaptureSelection,
        capture_id: str,
    ) -> None:
        """Best-effort Phase 9 provenance capture — optional on both sides.

        Only runs when the adapter supports ``ProvenanceCaptureAdapter`` *and*
        the store supports ``save_tool_events`` (duck-typed, not part of
        ``CaptureStore``): most adapters (ChatGPT) and stores (the legacy
        local JSON store) have neither, and this is a silent no-op for them.
        """

        if not isinstance(adapter, ProvenanceCaptureAdapter):
            return
        save_events = getattr(self._store, "save_tool_events", None)
        if not callable(save_events):
            return
        events = adapter.provenance(source, selection)
        if events:
            save_events(capture_id, events)

    def _adapter(self, name: str) -> SourceCaptureAdapter:
        adapter = self._adapters.get(name)
        if adapter is None:
            supported = ", ".join(sorted(self._adapters))
            raise ValidationError(f"Unknown adapter '{name}'. Supported adapters: {supported}")
        return adapter
