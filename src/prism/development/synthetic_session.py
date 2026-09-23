from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path

from ..exceptions import ValidationError
from ..models.session import SourceMessage, SourceResource, SourceSnapshot


class SyntheticSessionSource:
    """Reads Prism's deterministic synthetic session format for development."""

    schema_version = "prism.synthetic-session.v1"

    def snapshot(self, session_reference: str | Path) -> SourceSnapshot:
        source = Path(session_reference).expanduser().resolve()
        if not source.is_file():
            raise ValidationError(f"Source session not found: {source}")

        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationError(f"Cannot read source session: {exc}") from exc

        if not isinstance(payload, dict):
            raise ValidationError("Synthetic session must be a JSON object")
        if payload.get("schema_version") != self.schema_version:
            raise ValidationError(
                f"Synthetic session must declare {self.schema_version}"
            )

        title_value = payload.get("title")
        if not isinstance(title_value, str) or not title_value.strip():
            raise ValidationError("Source session requires a non-empty title")
        title = title_value.strip()

        instructions_value = payload.get("instructions", "")
        if not isinstance(instructions_value, str):
            raise ValidationError("Source session instructions must be text")

        messages_by_turn: OrderedDict[str, list[SourceMessage]] = OrderedDict()
        raw_messages = payload.get("messages")
        if not isinstance(raw_messages, list):
            raise ValidationError("Synthetic session messages must be a list")
        for raw in raw_messages:
            if not isinstance(raw, dict):
                raise ValidationError("Each message must be a JSON object")
            status = raw.get("status")
            turn_id = raw.get("turn_id")
            role = raw.get("role")
            content = raw.get("content")
            if (
                not isinstance(turn_id, str)
                or not turn_id.strip()
                or not isinstance(role, str)
                or role not in {"user", "assistant", "tool"}
                or not isinstance(content, str)
                or not content.strip()
                or not isinstance(status, str)
                or status not in {"completed", "in_progress", "failed"}
            ):
                raise ValidationError("Each message needs turn_id, valid role, content, and status")
            normalized_turn_id = turn_id.strip()
            messages_by_turn.setdefault(normalized_turn_id, []).append(
                SourceMessage(normalized_turn_id, role, content.strip(), status)
            )

        messages: list[SourceMessage] = []
        for turn_messages in messages_by_turn.values():
            roles = {message.role for message in turn_messages}
            is_complete = all(message.status == "completed" for message in turn_messages)
            if is_complete and "assistant" in roles:
                messages.extend(turn_messages)

        if not messages:
            raise ValidationError("Source session has no completed turns")

        resources: list[SourceResource] = []
        raw_resources = payload.get("resources", [])
        if not isinstance(raw_resources, list):
            raise ValidationError("Synthetic session resources must be a list")
        for raw in raw_resources:
            if not isinstance(raw, dict):
                raise ValidationError("Each resource must be a JSON object")
            display_name = raw.get("display_name")
            relative_path = raw.get("path")
            media_type = raw.get("media_type", "text/plain")
            if (
                not isinstance(display_name, str)
                or not display_name.strip()
                or not isinstance(relative_path, str)
                or not relative_path.strip()
                or not isinstance(media_type, str)
                or not media_type.strip()
            ):
                raise ValidationError(
                    "Each resource needs display_name, path, and a valid media_type"
                )
            display_name = display_name.strip()
            relative_path = relative_path.strip()
            resource_path = (source.parent / relative_path).resolve()
            try:
                resource_path.relative_to(source.parent)
            except ValueError as exc:
                raise ValidationError(
                    f"Resource escapes the source directory: {relative_path}"
                ) from exc
            if not resource_path.is_file():
                raise ValidationError(f"Source resource not found: {relative_path}")
            resources.append(
                SourceResource(
                    display_name=display_name,
                    source_path=resource_path,
                    media_type=media_type.strip(),
                )
            )

        return SourceSnapshot(
            title=title,
            instructions=instructions_value.strip(),
            messages=tuple(messages),
            resources=tuple(resources),
        )
