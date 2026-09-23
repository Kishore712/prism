"""Provider-neutral contracts for durable owner draft state."""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from .capture import Identifier, ShortText, StrictModel
from .projection import ReviewResource, ReviewTurn, SnapshotContent


class DraftStatus(str, Enum):
    EDITING = "editing"
    PUBLISHED = "published"
    ABANDONED = "abandoned"


class DraftAttachmentInfo(StrictModel):
    """Metadata for an owner-attached resource; content stays owner-side."""

    attachment_id: Identifier
    display_name: ShortText
    media_type: ShortText
    byte_size: int = Field(ge=1)
    content_sha256: Identifier


class DraftState(StrictModel):
    draft_id: Identifier
    capture_id: Identifier
    revision: int = Field(ge=1)
    status: DraftStatus
    selected_turn_ids: tuple[Identifier, ...] = ()
    selected_resource_ids: tuple[Identifier, ...] = ()
    attachments: tuple[DraftAttachmentInfo, ...] = ()
    title_override: ShortText | None = None
    previewed_revision: int | None = Field(default=None, ge=1)
    preview_hash: Identifier | None = None
    created_at: str = Field(min_length=20, max_length=40)
    updated_at: str = Field(min_length=20, max_length=40)


class DraftReviewTurn(StrictModel):
    turn: ReviewTurn
    included: bool


class DraftReviewResource(StrictModel):
    resource: ReviewResource
    included: bool


class DraftReview(StrictModel):
    draft: DraftState
    title: ShortText
    turns: tuple[DraftReviewTurn, ...]
    resources: tuple[DraftReviewResource, ...] = ()


class DraftSelectionRequest(StrictModel):
    expected_revision: int = Field(ge=1)
    selected_turn_ids: tuple[Identifier, ...] = ()
    selected_resource_ids: tuple[Identifier, ...] = ()


class DraftSnapshotPreview(StrictModel):
    draft_id: Identifier
    draft_revision: int = Field(ge=1)
    snapshot: SnapshotContent
    preview_hash: Identifier


class LegacyMigrationResult(StrictModel):
    scanned: int = Field(ge=0)
    imported: int = Field(ge=0)
    already_imported: int = Field(ge=0)
    capture_ids: tuple[Identifier, ...] = ()
