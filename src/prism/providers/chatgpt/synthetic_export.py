from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from ...models.capture import (
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
from ...exceptions import (
    AmbiguousBranchError,
    CaptureValidationError,
    ConversationNotFoundError,
    UnsupportedContentError,
    UnsupportedSourceFormatError,
)
from ...archives import SafeArchiveReader, SourceDocument


class SyntheticChatGPTExportAdapter:
    """Parses the explicitly versioned synthetic ChatGPT-like export sample.

    The shape is intentionally not described as an official ChatGPT export
    contract. A sanitized real export must be added as a separately tested
    source variant before this adapter claims compatibility with it.
    """

    name = "synthetic-chatgpt-export"
    version = "synthetic-chatgpt-export/0.1.0"
    source_schema = "prism.synthetic-chatgpt-export.v1"

    def __init__(self, archive_reader: SafeArchiveReader | None = None) -> None:
        self._archive_reader = archive_reader or SafeArchiveReader()

    def inventory(self, source: CaptureSource) -> CaptureInventory:
        document, conversations = self._load(source)
        try:
            summaries: list[ConversationSummary] = []
            seen_ids: set[str] = set()
            for conversation in conversations:
                native_id = self._required_string(conversation, "id", "conversation")
                if native_id in seen_ids:
                    raise UnsupportedSourceFormatError(
                        "The source contains duplicate conversation IDs"
                    )
                seen_ids.add(native_id)
                title = self._required_string(conversation, "title", "conversation")
                warnings: tuple[CaptureWarning, ...] = ()
                try:
                    messages = self._messages_for(conversation)
                    supported_count = len(messages)
                except (AmbiguousBranchError, UnsupportedContentError) as exc:
                    supported_count = 0
                    warnings = (
                        CaptureWarning(
                            code=exc.code,
                            message="This conversation cannot be captured in Phase 2.",
                            severity=WarningSeverity.BLOCKING,
                        ),
                    )
                summaries.append(
                    ConversationSummary(
                        conversation_ref=self._conversation_ref(
                            document.source_fingerprint,
                            native_id,
                        ),
                        title=title,
                        updated_at=self._optional_timestamp(conversation.get("update_time")),
                        supported_message_count=supported_count,
                        warnings=warnings,
                    )
                )
            return CaptureInventory(
                source_fingerprint=document.source_fingerprint,
                adapter_version=self.version,
                conversations=tuple(summaries),
            )
        except PydanticValidationError as exc:
            raise CaptureValidationError(
                "Conversation metadata exceeds a capture contract limit"
            ) from exc

    def capture(
        self,
        source: CaptureSource,
        selection: CaptureSelection,
    ) -> AdapterCapture:
        document, conversations = self._load(source)
        selected: dict[str, Any] | None = None
        for conversation in conversations:
            native_id = self._required_string(conversation, "id", "conversation")
            if (
                self._conversation_ref(document.source_fingerprint, native_id)
                == selection.conversation_ref
            ):
                selected = conversation
                break
        if selected is None:
            raise ConversationNotFoundError(
                "The conversation reference does not belong to the selected source"
            )

        try:
            messages = self._messages_for(selected)
            return AdapterCapture(
                platform=Platform.CHATGPT,
                method=CaptureMethod.EXPORT,
                source_fingerprint=document.source_fingerprint,
                conversation_ref=selection.conversation_ref,
                title=self._required_string(selected, "title", "conversation"),
                messages=messages,
                resources=self._resources_for(
                    selected,
                    {message.source_message_id for message in messages},
                ),
            )
        except PydanticValidationError as exc:
            raise CaptureValidationError(
                "The selected conversation exceeds a capture contract limit"
            ) from exc

    def _load(self, source: CaptureSource) -> tuple[SourceDocument, list[dict[str, Any]]]:
        document = self._archive_reader.read_document(
            source.path,
            document_name="conversations.json",
            allow_direct_json=True,
        )
        try:
            payload = json.loads(document.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UnsupportedSourceFormatError(
                "The conversations document is not valid UTF-8 JSON"
            ) from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != self.source_schema:
            raise UnsupportedSourceFormatError(
                f"Only the explicit {self.source_schema} source variant is currently supported"
            )
        conversations = payload.get("conversations")
        if not isinstance(conversations, list) or not conversations:
            raise UnsupportedSourceFormatError(
                "The source must contain a non-empty conversations list"
            )
        if not all(isinstance(item, dict) for item in conversations):
            raise UnsupportedSourceFormatError("Every conversation must be a JSON object")
        return document, conversations

    def _messages_for(self, conversation: dict[str, Any]) -> tuple[AdapterMessage, ...]:
        path = self._active_path(conversation)
        messages: list[AdapterMessage] = []
        source_message_ids: set[str] = set()
        turn_index = 0
        awaiting_assistant = False
        for node_id, node in path:
            message = node.get("message")
            if message is None:
                continue
            if not isinstance(message, dict):
                raise UnsupportedContentError("A selected message has an invalid structure")
            status = message.get("status")
            if status not in {"completed", "finished_successfully"}:
                raise UnsupportedContentError("The selected branch contains an incomplete message")
            author = message.get("author")
            if not isinstance(author, dict) or author.get("role") not in {
                "user",
                "assistant",
            }:
                raise UnsupportedContentError(
                    "The selected branch contains an unsupported message role"
                )
            role = MessageRole(author["role"])
            if role is MessageRole.USER:
                if awaiting_assistant:
                    raise UnsupportedContentError(
                        "A user message has no completed assistant response"
                    )
                turn_index += 1
                awaiting_assistant = True
            elif not awaiting_assistant:
                raise UnsupportedContentError("An assistant message does not follow a user message")
            else:
                awaiting_assistant = False

            content = message.get("content")
            if not isinstance(content, dict) or content.get("content_type") != "text":
                raise UnsupportedContentError(
                    "The selected branch contains unsupported visible content"
                )
            parts = content.get("parts")
            if (
                not isinstance(parts, list)
                or not parts
                or not all(isinstance(part, str) for part in parts)
            ):
                raise UnsupportedContentError("A selected text message has invalid content parts")
            text = unicodedata.normalize(
                "NFC",
                "\n".join(parts).replace("\r\n", "\n").replace("\r", "\n"),
            )
            if not text.strip():
                raise UnsupportedContentError("A selected message is empty")
            source_message_id = message.get("id", node_id)
            if not isinstance(source_message_id, str) or not source_message_id.strip():
                raise UnsupportedContentError("A selected message has no stable source identifier")
            if source_message_id in source_message_ids:
                raise UnsupportedContentError("The selected branch contains duplicate message IDs")
            source_message_ids.add(source_message_id)
            try:
                messages.append(
                    AdapterMessage(
                        source_message_id=source_message_id,
                        turn_index=turn_index,
                        ordinal=len(messages) + 1,
                        role=role,
                        content=text,
                    )
                )
            except PydanticValidationError as exc:
                raise CaptureValidationError(
                    "A selected message exceeds a capture contract limit"
                ) from exc

        if not messages or awaiting_assistant:
            raise UnsupportedContentError(
                "The selected branch has no complete user-assistant turns"
            )
        return tuple(messages)

    def _active_path(self, conversation: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        mapping = conversation.get("mapping")
        if not isinstance(mapping, dict) or not mapping:
            raise UnsupportedSourceFormatError("A conversation has no message mapping")
        if not all(
            isinstance(key, str) and isinstance(value, dict)
            for key, value in mapping.items()
        ):
            raise UnsupportedSourceFormatError("A conversation mapping is malformed")
        for node_id, node in mapping.items():
            declared_id = node.get("id", node_id)
            if declared_id != node_id:
                raise UnsupportedSourceFormatError(
                    "A conversation node ID does not match its mapping key"
                )
            children = node.get("children", [])
            if not isinstance(children, list) or not all(
                isinstance(child, str) for child in children
            ):
                raise UnsupportedSourceFormatError("A conversation node has invalid children")
            if any(child not in mapping for child in children):
                raise UnsupportedSourceFormatError("A conversation node references a missing child")
            parent = node.get("parent")
            if parent is not None and (not isinstance(parent, str) or parent not in mapping):
                raise UnsupportedSourceFormatError("A conversation node has an invalid parent")
            if parent is not None and node_id not in mapping[parent].get("children", []):
                raise UnsupportedSourceFormatError("Conversation parent and child links disagree")

        current_node = conversation.get("current_node")
        if current_node is None:
            leaves = [
                node_id
                for node_id, node in mapping.items()
                if node.get("message") is not None and not node.get("children")
            ]
            if len(leaves) != 1:
                raise AmbiguousBranchError("A unique active conversation branch cannot be proven")
            current_node = leaves[0]
        if not isinstance(current_node, str) or current_node not in mapping:
            raise UnsupportedSourceFormatError(
                "The conversation current_node is missing or invalid"
            )

        reverse_path: list[tuple[str, dict[str, Any]]] = []
        visited: set[str] = set()
        node_id: str | None = current_node
        while node_id is not None:
            if node_id in visited:
                raise UnsupportedSourceFormatError("The conversation mapping contains a cycle")
            visited.add(node_id)
            node = mapping.get(node_id)
            if not isinstance(node, dict):
                raise UnsupportedSourceFormatError("The active branch references a missing node")
            reverse_path.append((node_id, node))
            node_id = node.get("parent")
        reverse_path.reverse()
        return reverse_path

    def _resources_for(
        self,
        conversation: dict[str, Any],
        selected_message_ids: set[str],
    ) -> tuple[AdapterResource, ...]:
        raw_resources = conversation.get("resources", [])
        if not isinstance(raw_resources, list):
            raise UnsupportedContentError("Conversation resource references are malformed")
        resources: list[AdapterResource] = []
        for raw in raw_resources:
            if not isinstance(raw, dict):
                raise UnsupportedContentError("A conversation resource reference is malformed")
            message_id = self._required_string(raw, "message_id", "resource")
            if message_id not in selected_message_ids:
                continue
            display_name = self._required_string(raw, "display_name", "resource")
            if "/" in display_name or "\\" in display_name:
                raise UnsupportedContentError(
                    "Resource display names must not contain source paths"
                )
            media_type = self._required_string(raw, "media_type", "resource")
            try:
                resources.append(
                    AdapterResource(display_name=display_name, media_type=media_type)
                )
            except PydanticValidationError as exc:
                raise CaptureValidationError(
                    "A resource reference exceeds a capture contract limit"
                ) from exc
        return tuple(resources)

    @staticmethod
    def _required_string(container: dict[str, Any], key: str, label: str) -> str:
        value = container.get(key)
        if not isinstance(value, str) or not value.strip():
            raise UnsupportedSourceFormatError(f"Each {label} requires a non-empty {key}")
        return value.strip()

    @staticmethod
    def _optional_timestamp(value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise UnsupportedSourceFormatError(
                "Conversation update_time must be a string when present"
            )
        return value.strip()

    @staticmethod
    def _conversation_ref(source_fingerprint: str, native_id: str) -> str:
        material = f"{source_fingerprint}\0{native_id}".encode("utf-8")
        return f"convref_{hashlib.sha256(material).hexdigest()[:24]}"
