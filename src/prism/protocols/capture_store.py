from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..models.capture import CapturedSession


@dataclass(frozen=True)
class StoredCapture:
    artifact_path: Path
    draft_id: str | None = None
    draft_revision: int | None = None


class CaptureStore(Protocol):
    """Persistence boundary for one validated canonical capture."""

    def save(self, capture: CapturedSession) -> StoredCapture: ...

    def load(self, capture_id: str) -> CapturedSession: ...
