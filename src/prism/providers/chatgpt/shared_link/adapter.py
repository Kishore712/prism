"""ChatGPT shared-page to provider-neutral capture adapter."""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from typing import Protocol

from ....models.capture import (
    AdapterCapture,
    AdapterMessage,
    CaptureMethod,
    CaptureObservations,
    CaptureWarning,
    MessageRole,
    Platform,
    RemoteAdapterCapture,
    RemoteCaptureSource,
    WarningSeverity,
)
from .decoder import ChatGPTSharedPageDecoder, DecodedShare
from .fetcher import BoundedSharedPageFetcher


class SharedPageFetcher(Protocol):
    def fetch(self, raw_url: str) -> bytes: ...


def _opaque_ref(prefix: str, scope: str, native_value: str) -> str:
    digest = hashlib.sha256(
        b"PrismProviderRef-v1\0"
        + scope.encode("utf-8")
        + b"\0"
        + native_value.encode("utf-8")
    ).hexdigest()[:26]
    return f"{prefix}_{digest}"


class ChatGPTSharedLinkAdapter:
    """Acquire exactly one public shared snapshot with no account credentials."""

    name = "chatgpt-shared-link"
    version = "chatgpt-shared-link/0.1.0"

    def __init__(
        self,
        fetcher: SharedPageFetcher | None = None,
        decoder: ChatGPTSharedPageDecoder | None = None,
    ) -> None:
        self._fetcher = fetcher or BoundedSharedPageFetcher()
        self._decoder = decoder or ChatGPTSharedPageDecoder()

    def inspect(self, source: RemoteCaptureSource) -> RemoteAdapterCapture:
        body = self._fetcher.fetch(source.url.get_secret_value())
        decoded = self._decoder.decode(body)
        return RemoteAdapterCapture(
            capture=self._to_adapter_capture(decoded),
            observations=CaptureObservations(
                provider_node_count=decoded.provider_node_count,
                selected_message_count=len(decoded.messages),
                skipped_by_reason=decoded.skipped_by_reason,
                content_reference_types=decoded.content_reference_types,
            ),
        )

    def _to_adapter_capture(self, decoded: DecodedShare) -> AdapterCapture:
        exchanges: OrderedDict[str, int] = OrderedDict()
        messages: list[AdapterMessage] = []
        for ordinal, message in enumerate(decoded.messages, start=1):
            turn_index = exchanges.setdefault(
                message.native_exchange_id,
                len(exchanges) + 1,
            )
            messages.append(
                AdapterMessage(
                    source_message_id=_opaque_ref(
                        "srcmsg",
                        decoded.content_fingerprint,
                        message.native_message_id,
                    ),
                    turn_index=turn_index,
                    ordinal=ordinal,
                    role=MessageRole(message.role),
                    content=message.content,
                )
            )

        warnings = [
            CaptureWarning(
                code="PUBLIC_LINK_REMAINS_ACTIVE",
                message=(
                    "The native ChatGPT shared link remains public until the owner "
                    "revokes it in ChatGPT."
                ),
                severity=WarningSeverity.WARNING,
            )
        ]
        skipped_count = sum(decoded.skipped_by_reason.values())
        if skipped_count:
            warnings.append(
                CaptureWarning(
                    code="PROVIDER_RECORDS_EXCLUDED",
                    message=(
                        f"{skipped_count} hidden, unsupported, incomplete, or empty "
                        "provider records were excluded by deterministic policy."
                    ),
                    severity=WarningSeverity.INFO,
                )
            )
        reference_count = sum(decoded.content_reference_types.values())
        if reference_count:
            warnings.append(
                CaptureWarning(
                    code="CONTENT_REFERENCES_NOT_FETCHED",
                    message=(
                        f"{reference_count} content references were observed; Prism "
                        "did not follow or download their targets."
                    ),
                    severity=WarningSeverity.WARNING,
                )
            )

        return AdapterCapture(
            platform=Platform.CHATGPT,
            method=CaptureMethod.SHARED_LINK,
            source_fingerprint=decoded.content_fingerprint,
            conversation_ref=_opaque_ref(
                "conv",
                decoded.content_fingerprint,
                decoded.native_conversation_id,
            ),
            title=decoded.title,
            messages=tuple(messages),
            warnings=tuple(warnings),
        )
