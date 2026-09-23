from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from ..protocols.session_source import SessionSource


def _preview(text: str, limit: int = 100) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


@dataclass(frozen=True)
class MessageInspection:
    role: str
    preview: str


@dataclass(frozen=True)
class TurnInspection:
    turn_id: str
    messages: tuple[MessageInspection, ...]


@dataclass(frozen=True)
class ResourceInspection:
    display_name: str
    media_type: str
    source_path: str


@dataclass(frozen=True)
class SessionInspection:
    title: str
    has_instructions: bool
    completed_turns: tuple[TurnInspection, ...]
    resources: tuple[ResourceInspection, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class SessionInspector:
    """Builds an owner-visible, read-only inventory from a session source."""

    def __init__(self, source: SessionSource) -> None:
        self._source = source

    def inspect(self, session_reference: str | Path) -> SessionInspection:
        snapshot = self._source.snapshot(session_reference)
        ordered_turn_ids = list(dict.fromkeys(message.turn_id for message in snapshot.messages))
        turns = tuple(
            TurnInspection(
                turn_id=turn_id,
                messages=tuple(
                    MessageInspection(message.role, _preview(message.content))
                    for message in snapshot.messages
                    if message.turn_id == turn_id
                ),
            )
            for turn_id in ordered_turn_ids
        )
        resources = tuple(
            ResourceInspection(
                display_name=resource.display_name,
                media_type=resource.media_type,
                source_path=str(resource.source_path),
            )
            for resource in snapshot.resources
        )
        return SessionInspection(
            title=snapshot.title,
            has_instructions=bool(snapshot.instructions),
            completed_turns=turns,
            resources=resources,
        )
