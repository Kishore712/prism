from __future__ import annotations

from typing import Protocol

from ..models.capture import RemoteAdapterCapture, RemoteCaptureSource, StagedCapture


class RemoteCaptureAdapter(Protocol):
    """Acquisition boundary for a single remote, public capture source."""

    name: str
    version: str

    def inspect(self, source: RemoteCaptureSource) -> RemoteAdapterCapture: ...


class TemporaryCaptureStore(Protocol):
    """Ephemeral boundary for a normalized candidate awaiting confirmation."""

    def stage(
        self,
        adapter_version: str,
        candidate: RemoteAdapterCapture,
        preview_hash: str,
    ) -> StagedCapture: ...

    def get(self, import_id: str) -> StagedCapture: ...

    def delete(self, import_id: str) -> None: ...
