"""Non-executing decoder for the observed ChatGPT shared-page payload."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections import Counter, OrderedDict
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

from ....exceptions import UnsupportedContentError, UnsupportedSourceFormatError


ENQUEUE_CALL = "window.__reactRouterContext.streamController.enqueue("
CONVERSATION_KEYS = {
    "conversation_id",
    "current_node",
    "linear_conversation",
    "mapping",
    "title",
}
MAX_FLAT_ITEMS = 250_000
MAX_LINEAR_NODES = 100_000


class _ScriptCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._in_script = False
        self._buffer: list[str] = []
        self.scripts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.lower() == "script":
            self._in_script = True
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._in_script:
            self.scripts.append("".join(self._buffer))
            self._in_script = False
            self._buffer = []


@dataclass(frozen=True)
class DecodedMessage:
    native_message_id: str
    native_exchange_id: str
    role: str
    content: str


@dataclass(frozen=True)
class DecodedShare:
    title: str
    native_conversation_id: str
    messages: tuple[DecodedMessage, ...]
    provider_node_count: int
    skipped_by_reason: dict[str, int]
    content_reference_types: dict[str, int]
    content_fingerprint: str


class _FlatIndexView:
    def __init__(self, values: list[Any]) -> None:
        self._values = values

    def value(self, reference: object) -> Any:
        if type(reference) is int and 0 <= reference < len(self._values):
            return self._values[reference]
        return None

    def key_name(self, encoded: object) -> object:
        if isinstance(encoded, str) and encoded.startswith("_") and encoded[1:].isdigit():
            decoded = self.value(int(encoded[1:]))
            if isinstance(decoded, str):
                return decoded
        return encoded

    def keys(self, object_ref: object) -> tuple[str, ...]:
        value = self.value(object_ref)
        if not isinstance(value, dict):
            return ()
        return tuple(
            key
            for encoded in value
            if isinstance((key := self.key_name(encoded)), str)
        )

    def reference(self, object_ref: object, name: str) -> object | None:
        value = self.value(object_ref)
        if not isinstance(value, dict):
            return None
        for encoded, reference in value.items():
            if self.key_name(encoded) == name:
                return reference
        return None

    def field(self, object_ref: object, name: str) -> Any:
        return self.value(self.reference(object_ref, name))

    def find_conversation(self) -> int:
        matches = [
            index
            for index, value in enumerate(self._values)
            if isinstance(value, dict) and CONVERSATION_KEYS.issubset(self.keys(index))
        ]
        if len(matches) != 1:
            raise UnsupportedSourceFormatError(
                "Expected exactly one supported conversation in the shared page"
            )
        return matches[0]


class ChatGPTSharedPageDecoder:
    """Decode a tested provider shape and fail closed on fidelity ambiguity."""

    def decode(self, body: bytes) -> DecodedShare:
        try:
            html_text = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UnsupportedSourceFormatError(
                "The shared page is not valid UTF-8"
            ) from exc

        flattened = self._decode_flat_payload(html_text)
        view = _FlatIndexView(flattened)
        conversation_ref = view.find_conversation()
        title = view.field(conversation_ref, "title")
        native_conversation_id = view.field(conversation_ref, "conversation_id")
        nodes = view.field(conversation_ref, "linear_conversation")
        if not isinstance(title, str) or not title.strip():
            raise UnsupportedSourceFormatError(
                "The shared conversation has no usable title"
            )
        title = self._normalize_text(title)
        if len(title) > 500:
            raise UnsupportedContentError("The shared conversation title is too long")
        if not isinstance(native_conversation_id, str) or not native_conversation_id:
            raise UnsupportedSourceFormatError(
                "The shared conversation has no stable identifier"
            )
        if not isinstance(nodes, list) or not nodes:
            raise UnsupportedSourceFormatError(
                "The shared conversation has no linear message path"
            )
        if len(nodes) > MAX_LINEAR_NODES:
            raise UnsupportedSourceFormatError(
                "The shared conversation exceeds the supported node limit"
            )

        skipped: Counter[str] = Counter()
        content_reference_types: Counter[str] = Counter()
        selected: list[DecodedMessage] = []

        for node_ref in nodes:
            message_ref = view.reference(node_ref, "message")
            if (
                type(message_ref) is not int
                or message_ref < 0
                or not isinstance(view.value(message_ref), dict)
            ):
                skipped["missing_message"] += 1
                continue
            author_ref = view.reference(message_ref, "author")
            content_ref = view.reference(message_ref, "content")
            metadata_ref = view.reference(message_ref, "metadata")
            role = view.field(author_ref, "role")
            content_type = view.field(content_ref, "content_type")
            status = view.field(message_ref, "status")

            if view.field(metadata_ref, "is_visually_hidden_from_conversation") is True:
                skipped["visually_hidden"] += 1
                continue
            if view.field(metadata_ref, "is_redacted") is True:
                skipped["redacted"] += 1
                continue
            if role not in {"user", "assistant"}:
                skipped["unsupported_role"] += 1
                continue
            if status != "finished_successfully":
                skipped["incomplete"] += 1
                continue
            if content_type != "text":
                skipped["unsupported_content_type"] += 1
                continue

            text = self._decode_text_parts(view.field(content_ref, "parts"), view)
            if text is None:
                skipped["unsupported_text_parts"] += 1
                continue
            if not text:
                skipped["empty_text"] += 1
                continue
            if len(text) > 1_000_000:
                raise UnsupportedContentError(
                    "A shared conversation message exceeds the supported size"
                )

            native_message_id = view.field(message_ref, "id")
            native_exchange_id = (
                view.field(metadata_ref, "turn_exchange_id")
                or view.field(metadata_ref, "working_turn_id")
            )
            if not isinstance(native_message_id, str) or not native_message_id:
                raise UnsupportedSourceFormatError(
                    "A selected message has no stable source identifier"
                )
            if not isinstance(native_exchange_id, str) or not native_exchange_id:
                raise UnsupportedSourceFormatError(
                    "A selected message has no stable exchange identifier"
                )

            references = view.field(metadata_ref, "content_references")
            if isinstance(references, list):
                for reference_ref in references:
                    reference_type = view.field(reference_ref, "type")
                    content_reference_types[str(reference_type)] += 1
            selected.append(
                DecodedMessage(
                    native_message_id=native_message_id,
                    native_exchange_id=native_exchange_id,
                    role=role,
                    content=text,
                )
            )

        if not selected:
            raise UnsupportedContentError(
                "The shared page contains no supported visible messages"
            )
        exchanges: OrderedDict[str, list[str]] = OrderedDict()
        for message in selected:
            exchanges.setdefault(message.native_exchange_id, []).append(message.role)
        if any(roles != ["user", "assistant"] for roles in exchanges.values()):
            raise UnsupportedContentError(
                "The shared conversation contains an incomplete or ambiguous exchange"
            )

        content_bytes = json.dumps(
            {
                "messages": [
                    {"content": message.content, "role": message.role}
                    for message in selected
                ],
                "title": title,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return DecodedShare(
            title=title,
            native_conversation_id=native_conversation_id,
            messages=tuple(selected),
            provider_node_count=len(nodes),
            skipped_by_reason=dict(sorted(skipped.items())),
            content_reference_types=dict(sorted(content_reference_types.items())),
            content_fingerprint=(
                "sha256:" + hashlib.sha256(b"PrismSharedPage-v1\0" + content_bytes).hexdigest()
            ),
        )

    @staticmethod
    def _decode_flat_payload(html_text: str) -> list[Any]:
        collector = _ScriptCollector()
        collector.feed(html_text)
        candidate_scripts = [
            script
            for script in collector.scripts
            if ENQUEUE_CALL in script and "conversation_id" in script
        ]
        if len(candidate_scripts) != 1:
            raise UnsupportedSourceFormatError(
                "The shared page did not contain one recognized data stream"
            )
        script = candidate_scripts[0]
        start = script.index(ENQUEUE_CALL) + len(ENQUEUE_CALL)
        try:
            wire_text, _ = json.JSONDecoder().raw_decode(script[start:])
        except json.JSONDecodeError as exc:
            raise UnsupportedSourceFormatError(
                "The shared-page stream argument is not valid JSON"
            ) from exc
        if not isinstance(wire_text, str):
            raise UnsupportedSourceFormatError(
                "The shared-page stream argument has an unsupported type"
            )
        root_records = [line for line in wire_text.splitlines() if line.startswith("[")]
        if len(root_records) != 1:
            raise UnsupportedSourceFormatError(
                "The shared-page stream has an unsupported record layout"
            )
        try:
            flattened = json.loads(root_records[0])
        except json.JSONDecodeError as exc:
            raise UnsupportedSourceFormatError(
                "The shared-page root record is not valid JSON"
            ) from exc
        if not isinstance(flattened, list) or len(flattened) > MAX_FLAT_ITEMS:
            raise UnsupportedSourceFormatError(
                "The shared-page root record exceeds the supported shape"
            )
        return flattened

    @classmethod
    def _decode_text_parts(
        cls,
        parts: object,
        view: _FlatIndexView,
    ) -> str | None:
        if not isinstance(parts, list):
            return None
        decoded: list[str] = []
        for part_ref in parts:
            part = view.value(part_ref)
            if not isinstance(part, str):
                return None
            decoded.append(part)
        text = cls._normalize_text("\n".join(decoded))
        return text if text.strip() else ""

    @staticmethod
    def _normalize_text(value: str) -> str:
        return unicodedata.normalize(
            "NFC",
            value.replace("\r\n", "\n").replace("\r", "\n"),
        )
