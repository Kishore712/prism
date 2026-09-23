from __future__ import annotations

import hashlib
from pathlib import Path

from ..models.capture import (
    AdapterCapture,
    AdapterMessage,
    AdapterResource,
    CaptureMethod,
    CaptureInventory,
    CaptureSelection,
    CaptureSource,
    CaptureWarning,
    ConversationSummary,
    MessageRole,
    Platform,
    WarningSeverity,
)
from ..exceptions import (
    ConversationNotFoundError,
    UnsupportedContentError,
    UnsafeArchiveError,
)
from .synthetic_session import SyntheticSessionSource


class SyntheticSessionCaptureAdapter:
    """Adapts Prism's synthetic development session into capture contracts."""

    name = "synthetic-session"
    version = "synthetic-session/0.1.0"

    def __init__(self, source_reader: SyntheticSessionSource | None = None) -> None:
        self._source_reader = source_reader or SyntheticSessionSource()

    def inventory(self, source: CaptureSource) -> CaptureInventory:
        snapshot = self._source_reader.snapshot(source.path)
        fingerprint = self._fingerprint(source.path)
        warning = self._instruction_warning(snapshot.instructions)
        return CaptureInventory(
            source_fingerprint=fingerprint,
            adapter_version=self.version,
            conversations=(
                ConversationSummary(
                    conversation_ref=self._conversation_ref(fingerprint),
                    title=snapshot.title,
                    supported_message_count=len(snapshot.messages),
                    warnings=(warning,) if warning else (),
                ),
            ),
        )

    def capture(
        self,
        source: CaptureSource,
        selection: CaptureSelection,
    ) -> AdapterCapture:
        snapshot = self._source_reader.snapshot(source.path)
        fingerprint = self._fingerprint(source.path)
        if selection.conversation_ref != self._conversation_ref(fingerprint):
            raise ConversationNotFoundError(
                "The conversation reference does not belong to the selected synthetic session"
            )
        turn_ids: dict[str, int] = {}
        messages: list[AdapterMessage] = []
        for source_message in snapshot.messages:
            if source_message.role not in {"user", "assistant"}:
                raise UnsupportedContentError(
                    "The synthetic session contains a non-text tool message"
                )
            turn_index = turn_ids.setdefault(source_message.turn_id, len(turn_ids) + 1)
            messages.append(
                AdapterMessage(
                    source_message_id=f"synthetic-message-{len(messages) + 1}",
                    turn_index=turn_index,
                    ordinal=len(messages) + 1,
                    role=MessageRole(source_message.role),
                    content=source_message.content,
                )
            )
        warning = self._instruction_warning(snapshot.instructions)
        return AdapterCapture(
            platform=Platform.PRISM,
            method=CaptureMethod.SYNTHETIC,
            source_fingerprint=fingerprint,
            conversation_ref=selection.conversation_ref,
            title=snapshot.title,
            messages=tuple(messages),
            resources=tuple(
                AdapterResource(
                    display_name=resource.display_name,
                    media_type=resource.media_type,
                )
                for resource in snapshot.resources
            ),
            warnings=(warning,) if warning else (),
        )

    @staticmethod
    def _fingerprint(path: Path) -> str:
        try:
            content = path.expanduser().resolve().read_bytes()
        except OSError as exc:
            raise UnsafeArchiveError(
                "The selected synthetic session cannot be fingerprinted"
            ) from exc
        return f"sha256:{hashlib.sha256(content).hexdigest()}"

    @staticmethod
    def _conversation_ref(source_fingerprint: str) -> str:
        digest = hashlib.sha256(
            f"{source_fingerprint}\0synthetic-session".encode()
        ).hexdigest()
        return f"convref_{digest[:24]}"

    @staticmethod
    def _instruction_warning(instructions: str) -> CaptureWarning | None:
        if not instructions:
            return None
        return CaptureWarning(
            code="SOURCE_INSTRUCTIONS_EXCLUDED",
            message="Source instructions are owner-only and were excluded from the capture.",
            severity=WarningSeverity.INFO,
        )
