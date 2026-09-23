"""Turn a capture, selection, and attachments into scannable atoms."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from ..models.capture import CapturedMessage, CapturedSession
from ..models.projection import ProjectionAttachment
from .detectors import Atom


def _messages_by_turn(
    capture: CapturedSession,
) -> "OrderedDict[str, tuple[CapturedMessage, CapturedMessage]]":
    grouped_lists: OrderedDict[str, list[CapturedMessage]] = OrderedDict()
    for message in capture.messages:
        grouped_lists.setdefault(message.turn_id, []).append(message)
    return OrderedDict(
        (turn_id, (messages[0], messages[1])) for turn_id, messages in grouped_lists.items()
    )


def turn_index_map(capture: CapturedSession) -> dict[str, int]:
    """Map each ``turn_id`` to its 1-based position in the capture.

    Turn order in ``capture.messages`` is guaranteed contiguous by
    ``CaptureNormalizer``, and a capture adapter (e.g. Claude Code's) assigns
    its own ``turn_index`` the same way — by order of appearance — so this
    reproduces the same numbering a provenance event was recorded against,
    without needing the adapter's numbering to be persisted anywhere.
    """

    return {turn_id: index for index, turn_id in enumerate(_messages_by_turn(capture), start=1)}


@dataclass(frozen=True)
class AtomSet:
    included: list[Atom]
    excluded: list[Atom]


def build_atoms(
    capture: CapturedSession,
    selected_turn_ids: tuple[str, ...],
    attachments: tuple[ProjectionAttachment, ...],
    *,
    title_override: str | None = None,
) -> AtomSet:
    """Split the capture into currently-included and currently-excluded atoms.

    The title is scanned as its own atom: the override if the owner set one,
    otherwise the capture's own title (which is what would be published).
    When the owner overrode the title, the *original* capture title becomes
    an excluded atom, so anything it named can be caught echoing elsewhere.
    """

    grouped = _messages_by_turn(capture)
    selected = set(selected_turn_ids)
    included: list[Atom] = []
    excluded: list[Atom] = []

    published_title = title_override if title_override is not None else capture.title
    included.append(Atom(kind="title", ref="title", text=published_title))
    if title_override is not None and title_override != capture.title:
        excluded.append(Atom(kind="title", ref="title:original", text=capture.title))

    for turn_id, (user_message, assistant_message) in grouped.items():
        text = f"{user_message.content}\n{assistant_message.content}"
        target = included if turn_id in selected else excluded
        target.append(Atom(kind="message", ref=turn_id, text=text))

    for attachment in attachments:
        included.append(Atom(kind="resource", ref=attachment.attachment_id, text=attachment.content))

    return AtomSet(included=included, excluded=excluded)
