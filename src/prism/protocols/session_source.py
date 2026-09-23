from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..models.session import SourceSnapshot


class SessionSource(Protocol):
    """Boundary for reading one session into a provider-neutral snapshot."""

    def snapshot(self, session_reference: str | Path) -> SourceSnapshot: ...
