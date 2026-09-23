"""Provider-neutral owner review and deterministic snapshot-preview models."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from .capture import (
    Identifier,
    MessageRole,
    NonEmptyText,
    ShortText,
    StrictModel,
)


TurnSelection = Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=100_000)]


class ReviewMessage(StrictModel):
    message_id: Identifier
    role: MessageRole
    content: NonEmptyText


class ReviewTurn(StrictModel):
    turn_id: Identifier
    turn_index: int = Field(ge=1, le=100_000)
    messages: tuple[ReviewMessage, ReviewMessage]


class ReviewResource(StrictModel):
    resource_id: Identifier
    display_name: ShortText
    media_type: ShortText
    availability: str


class ProjectionReview(StrictModel):
    capture_id: Identifier
    title: ShortText
    turns: tuple[ReviewTurn, ...]
    resources: tuple[ReviewResource, ...] = ()


class ProjectionSelection(StrictModel):
    selected_turn_ids: TurnSelection
    selected_resource_ids: tuple[Identifier, ...] = ()


class ProjectionAttachment(StrictModel):
    """Owner-approved text resource offered to the projection builder."""

    attachment_id: Identifier
    display_name: ShortText
    media_type: ShortText
    content: NonEmptyText


class SnapshotMessage(StrictModel):
    message_id: Identifier
    turn_id: Identifier
    ordinal: int = Field(ge=1, le=100_000)
    role: MessageRole
    content: NonEmptyText


class SnapshotResource(StrictModel):
    resource_id: Identifier
    display_name: ShortText
    media_type: ShortText
    content: NonEmptyText


class SnapshotContent(StrictModel):
    schema_version: str = Field(pattern=r"^prism\.snapshot\.v[0-9]+$")
    title: ShortText
    messages: tuple[SnapshotMessage, ...]
    resources: tuple[SnapshotResource, ...] = ()


class SnapshotPreview(StrictModel):
    capture_id: Identifier
    included_turn_count: int = Field(ge=1, le=100_000)
    included_message_count: int = Field(ge=2, le=200_000)
    snapshot: SnapshotContent
    preview_hash: Identifier


class SnapshotReceipt(StrictModel):
    """Signed, offline-verifiable statement of what produced a snapshot.

    Proves internal consistency of the recorded fields against the embedded
    key, not that the key belongs to a particular person — pin the public
    key out of band for that. See ``projection/receipt.py``.
    """

    capture_hash: Identifier
    snapshot_hash: Identifier
    detector_versions: dict[str, str]
    finding_count: int = Field(ge=0)
    override_count: int = Field(ge=0)
    created_at: str = Field(min_length=20, max_length=40)
    public_key_hex: str = Field(min_length=64, max_length=64)
    signature_hex: str = Field(min_length=128, max_length=128)
