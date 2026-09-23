from __future__ import annotations

import hashlib
import json
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import ValidationError as PydanticValidationError

from ..models.capture import (
    AdapterCapture,
    CaptureMethod,
    CaptureProvenance,
    CapturedMessage,
    CapturedResource,
    CapturedSession,
    MessageRole,
    Platform,
    ResourceAvailability,
    WarningSeverity,
)
from ..exceptions import CaptureValidationError


def _opaque_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:26]}"


def _normalize_text(value: str) -> str:
    return unicodedata.normalize(
        "NFC",
        value.replace("\r\n", "\n").replace("\r", "\n"),
    )


class CaptureNormalizer:
    """Builds the immutable, platform-neutral Phase 2 capture contract."""

    schema_version = "prism.capture.v1"

    def normalize(self, adapter_version: str, capture: AdapterCapture) -> CapturedSession:
        if any(warning.severity is WarningSeverity.BLOCKING for warning in capture.warnings):
            raise CaptureValidationError("A blocking adapter warning prevents capture")
        if not capture.messages:
            raise CaptureValidationError("A canonical capture requires at least one message")

        expected_ordinals = list(range(1, len(capture.messages) + 1))
        if [message.ordinal for message in capture.messages] != expected_ordinals:
            raise CaptureValidationError("Adapter message ordinals must be contiguous and ordered")

        messages_by_turn: dict[int, list[MessageRole]] = defaultdict(list)
        for message in capture.messages:
            messages_by_turn[message.turn_index].append(message.role)
        expected_turns = list(range(1, len(messages_by_turn) + 1))
        if sorted(messages_by_turn) != expected_turns:
            raise CaptureValidationError("Adapter turn indexes must be contiguous")
        if any(
            roles != [MessageRole.USER, MessageRole.ASSISTANT]
            for roles in messages_by_turn.values()
        ):
            raise CaptureValidationError(
                "Each Phase 2 turn must contain one user and one assistant message"
            )

        capture_hash = self.capture_hash(capture)
        turn_ids = {turn_index: _opaque_id("turn") for turn_index in messages_by_turn}
        try:
            return CapturedSession(
                schema_version=self.schema_version,
                capture_id=_opaque_id("cap"),
                source=CaptureProvenance(
                    platform=capture.platform,
                    method=capture.method,
                    adapter_version=adapter_version,
                    source_fingerprint=capture.source_fingerprint,
                    conversation_ref=capture.conversation_ref,
                ),
                title=_normalize_text(capture.title),
                captured_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                messages=tuple(
                    CapturedMessage(
                        message_id=_opaque_id("msg"),
                        turn_id=turn_ids[message.turn_index],
                        ordinal=message.ordinal,
                        role=message.role,
                        content=_normalize_text(message.content),
                    )
                    for message in capture.messages
                ),
                resources=tuple(
                    CapturedResource(
                        resource_id=_opaque_id("res"),
                        display_name=_normalize_text(resource.display_name),
                        media_type=resource.media_type.lower(),
                        availability=ResourceAvailability.REFERENCE_ONLY,
                    )
                    for resource in capture.resources
                ),
                warnings=capture.warnings,
                capture_hash=capture_hash,
            )
        except PydanticValidationError as exc:
            raise CaptureValidationError(
                "Adapter output failed canonical capture validation"
            ) from exc

    @staticmethod
    def capture_hash(capture: AdapterCapture) -> str:
        payload = {
            "method": capture.method.value,
            "messages": [
                {
                    "content": _normalize_text(message.content),
                    "ordinal": message.ordinal,
                    "role": message.role.value,
                    "turn": message.turn_index,
                }
                for message in capture.messages
            ],
            "platform": capture.platform.value,
            "resources": [
                {
                    "availability": ResourceAvailability.REFERENCE_ONLY.value,
                    "display_name": _normalize_text(resource.display_name),
                    "media_type": resource.media_type.lower(),
                }
                for resource in capture.resources
            ],
            "title": _normalize_text(capture.title),
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(b'PrismCanonicalJSON-v1\0' + canonical).hexdigest()}"
