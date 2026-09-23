from __future__ import annotations

from typing import Protocol

from ..models.capture import (
    AdapterCapture,
    CaptureInventory,
    CaptureSelection,
    CaptureSource,
)


class SourceCaptureAdapter(Protocol):
    """Platform-neutral acquisition boundary for one local source."""

    name: str
    version: str

    def inventory(self, source: CaptureSource) -> CaptureInventory: ...

    def capture(
        self,
        source: CaptureSource,
        selection: CaptureSelection,
    ) -> AdapterCapture: ...
