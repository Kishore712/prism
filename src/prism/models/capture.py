from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, SecretStr


NonEmptyText = Annotated[str, Field(min_length=1, max_length=1_000_000)]
ShortText = Annotated[str, Field(min_length=1, max_length=500)]
Identifier = Annotated[str, Field(min_length=5, max_length=100, pattern=r"^[A-Za-z0-9_./:-]+$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Platform(str, Enum):
    CHATGPT = "chatgpt"
    CLAUDE = "claude"
    PRISM = "prism"


class CaptureMethod(str, Enum):
    EXPORT = "export"
    SHARED_LINK = "shared_link"
    LOCAL_SESSION = "local_session"
    SYNTHETIC = "synthetic"


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


class ResourceAvailability(str, Enum):
    REFERENCE_ONLY = "reference_only"


class WarningSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


class CaptureWarning(StrictModel):
    code: Identifier
    message: ShortText
    severity: WarningSeverity = WarningSeverity.WARNING


class CaptureSource(StrictModel):
    path: Path


class RemoteCaptureSource(StrictModel):
    """Sensitive transport input that must never be persisted or logged."""

    url: SecretStr = Field(min_length=40, max_length=500)


class CaptureSelection(StrictModel):
    conversation_ref: Identifier


class ConversationSummary(StrictModel):
    conversation_ref: Identifier
    title: ShortText
    updated_at: str | None = Field(default=None, max_length=50)
    supported_message_count: int = Field(ge=0, le=100_000)
    warnings: tuple[CaptureWarning, ...] = ()


class CaptureInventory(StrictModel):
    source_fingerprint: Identifier
    adapter_version: Identifier
    conversations: tuple[ConversationSummary, ...]
    warnings: tuple[CaptureWarning, ...] = ()


class AdapterMessage(StrictModel):
    source_message_id: ShortText
    turn_index: int = Field(ge=1, le=100_000)
    ordinal: int = Field(ge=1, le=100_000)
    role: MessageRole
    content: NonEmptyText


class AdapterResource(StrictModel):
    display_name: ShortText
    media_type: ShortText


class AdapterCapture(StrictModel):
    platform: Platform
    method: CaptureMethod
    source_fingerprint: Identifier
    conversation_ref: Identifier
    title: ShortText
    messages: tuple[AdapterMessage, ...]
    resources: tuple[AdapterResource, ...] = ()
    warnings: tuple[CaptureWarning, ...] = ()


class CaptureObservations(StrictModel):
    """Non-sensitive compatibility measurements shown during owner review."""

    provider_node_count: int = Field(ge=0, le=100_000)
    selected_message_count: int = Field(ge=0, le=100_000)
    skipped_by_reason: dict[str, int] = Field(default_factory=dict)
    content_reference_types: dict[str, int] = Field(default_factory=dict)


class RemoteAdapterCapture(StrictModel):
    capture: AdapterCapture
    observations: CaptureObservations


class StagedCapture(StrictModel):
    import_id: Identifier
    adapter_version: Identifier
    capture: AdapterCapture
    observations: CaptureObservations
    preview_hash: Identifier
    expires_at: str = Field(min_length=20, max_length=40)


class CapturePreviewMessage(StrictModel):
    source_message_ref: ShortText
    turn_index: int = Field(ge=1, le=100_000)
    ordinal: int = Field(ge=1, le=100_000)
    role: MessageRole
    content: NonEmptyText


class CapturePreviewSource(StrictModel):
    platform: Platform
    method: CaptureMethod
    capture_scope: str = Field(pattern=r"^public_shared_snapshot$")


class CapturePreview(StrictModel):
    schema_version: str = Field(pattern=r"^prism\.capture-preview\.v[0-9]+$")
    import_id: Identifier
    source: CapturePreviewSource
    title: ShortText
    messages: tuple[CapturePreviewMessage, ...]
    observations: CaptureObservations
    warnings: tuple[CaptureWarning, ...] = ()
    preview_hash: Identifier
    expires_at: str = Field(min_length=20, max_length=40)


class CaptureProvenance(StrictModel):
    platform: Platform
    method: CaptureMethod
    adapter_version: Identifier
    source_fingerprint: Identifier
    conversation_ref: Identifier


class CapturedMessage(StrictModel):
    message_id: Identifier
    turn_id: Identifier
    ordinal: int = Field(ge=1, le=100_000)
    role: MessageRole
    content: NonEmptyText


class CapturedResource(StrictModel):
    resource_id: Identifier
    display_name: ShortText
    media_type: ShortText
    availability: ResourceAvailability = ResourceAvailability.REFERENCE_ONLY


class CapturedSession(StrictModel):
    schema_version: str = Field(pattern=r"^prism\.capture\.v[0-9]+$")
    capture_id: Identifier
    source: CaptureProvenance
    title: ShortText
    captured_at: str = Field(min_length=20, max_length=40)
    messages: tuple[CapturedMessage, ...]
    resources: tuple[CapturedResource, ...] = ()
    warnings: tuple[CaptureWarning, ...] = ()
    capture_hash: Identifier


class CaptureResult(StrictModel):
    capture_id: Identifier
    capture_hash: Identifier
    message_count: int = Field(ge=1)
    resource_reference_count: int = Field(ge=0)
    warnings: tuple[CaptureWarning, ...] = ()
    artifact_path: Path
    draft_id: Identifier | None = None
    draft_revision: int | None = Field(default=None, ge=1)
