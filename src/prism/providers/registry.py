from __future__ import annotations

from ..protocols.capture_source import SourceCaptureAdapter
from .chatgpt import SyntheticChatGPTExportAdapter
from .claude import ClaudeCodeSessionAdapter


def built_in_capture_adapters() -> dict[str, SourceCaptureAdapter]:
    """Return provider adapters enabled in this prototype build."""

    adapters: tuple[SourceCaptureAdapter, ...] = (
        SyntheticChatGPTExportAdapter(),
        ClaudeCodeSessionAdapter(),
    )
    return {adapter.name: adapter for adapter in adapters}


def capture_adapter_names() -> tuple[str, ...]:
    """Return stable CLI-facing names from the same provider registry."""

    return tuple(sorted(built_in_capture_adapters()))
