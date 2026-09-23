"""Provider-neutral contracts for immutable snapshot publication."""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from .capture import Identifier, StrictModel
from .projection import SnapshotContent


class SnapshotStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    PURGED = "purged"


class PublicationRequest(StrictModel):
    draft_id: Identifier
    expected_revision: int = Field(ge=1)
    expected_preview_hash: Identifier


class PublicationResult(StrictModel):
    publication_id: Identifier
    snapshot_id: Identifier
    content_hash: Identifier
    draft_id: Identifier
    draft_revision: int = Field(ge=1)
    published_at: str = Field(min_length=20, max_length=40)
    message_count: int = Field(ge=1)
    resource_count: int = Field(ge=0)
    created: bool
    finding_count: int = Field(ge=0)
    override_count: int = Field(ge=0)


class SnapshotSummary(StrictModel):
    snapshot_id: Identifier
    schema_version: Identifier
    content_hash: Identifier
    published_at: str = Field(min_length=20, max_length=40)
    status: SnapshotStatus
    message_count: int = Field(ge=0)
    resource_count: int = Field(ge=0)
    byte_size: int = Field(ge=0)
    revoked_at: str | None = Field(default=None, min_length=20, max_length=40)
    purged_at: str | None = Field(default=None, min_length=20, max_length=40)


class PublishedSnapshot(StrictModel):
    summary: SnapshotSummary
    content: SnapshotContent | None


class PublicationEvent(StrictModel):
    publication_id: Identifier
    snapshot_id: Identifier
    capture_id: Identifier
    draft_id: Identifier
    draft_revision: int = Field(ge=1)
    preview_hash: Identifier
    published_at: str = Field(min_length=20, max_length=40)
