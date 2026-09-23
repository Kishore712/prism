"""Provider-neutral session snapshot models."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceMessage:
    turn_id: str
    role: str
    content: str
    status: str


@dataclass(frozen=True)
class SourceResource:
    display_name: str
    source_path: Path
    media_type: str


@dataclass(frozen=True)
class SourceSnapshot:
    title: str
    instructions: str
    messages: tuple[SourceMessage, ...]
    resources: tuple[SourceResource, ...]


@dataclass(frozen=True)
class PublishedShare:
    share_id: str
    share_version_id: str
    version: int
    invite_code: str
    expires_at: str
