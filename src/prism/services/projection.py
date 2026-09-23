"""Allowlist-only Phase 4A snapshot preview construction."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict

from ..exceptions import ValidationError
from ..models.capture import CapturedMessage, CapturedSession
from ..models.projection import (
    ProjectionAttachment,
    ProjectionReview,
    ProjectionSelection,
    ReviewMessage,
    ReviewResource,
    ReviewTurn,
    SnapshotContent,
    SnapshotMessage,
    SnapshotPreview,
    SnapshotResource,
)
from ..protocols.capture_store import CaptureStore


def _derived_id(prefix: str, capture_hash: str, source_id: str) -> str:
    digest = hashlib.sha256(
        b"PrismSnapshotId-v1\0"
        + capture_hash.encode("utf-8")
        + b"\0"
        + source_id.encode("utf-8")
    ).hexdigest()[:26]
    return f"{prefix}_{digest}"


class ProjectionService:
    """Review one capture and build an exact, non-persisted recipient preview."""

    schema_version = "prism.snapshot.v1"

    def __init__(self, capture_store: CaptureStore) -> None:
        self._capture_store = capture_store

    def review(self, capture_id: str) -> ProjectionReview:
        capture = self._capture_store.load(capture_id)
        return self.review_capture(capture)

    @classmethod
    def review_capture(cls, capture: CapturedSession) -> ProjectionReview:
        grouped = cls._messages_by_turn(capture)
        return ProjectionReview(
            capture_id=capture.capture_id,
            title=capture.title,
            turns=tuple(
                ReviewTurn(
                    turn_id=turn_id,
                    turn_index=index,
                    messages=tuple(
                        ReviewMessage(
                            message_id=message.message_id,
                            role=message.role,
                            content=message.content,
                        )
                        for message in messages
                    ),
                )
                for index, (turn_id, messages) in enumerate(grouped.items(), start=1)
            ),
            resources=tuple(
                ReviewResource(
                    resource_id=resource.resource_id,
                    display_name=resource.display_name,
                    media_type=resource.media_type,
                    availability=resource.availability.value,
                )
                for resource in capture.resources
            ),
        )

    def preview(
        self,
        capture_id: str,
        selection: ProjectionSelection,
    ) -> SnapshotPreview:
        capture = self._capture_store.load(capture_id)
        return self.preview_capture(capture, selection)

    @classmethod
    def preview_capture(
        cls,
        capture: CapturedSession,
        selection: ProjectionSelection,
        attachments: tuple[ProjectionAttachment, ...] = (),
        title_override: str | None = None,
    ) -> SnapshotPreview:
        grouped = cls._messages_by_turn(capture)
        selected_turn_ids = selection.selected_turn_ids
        if len(set(selected_turn_ids)) != len(selected_turn_ids):
            raise ValidationError("A turn may be selected only once")
        unknown_turns = set(selected_turn_ids) - set(grouped)
        if unknown_turns:
            raise ValidationError("The selection contains a turn outside this capture")
        if selection.selected_resource_ids:
            raise ValidationError(
                "Reference-only resources cannot enter a snapshot preview"
            )

        snapshot_messages: list[SnapshotMessage] = []
        selected = set(selected_turn_ids)
        ordinal = 1
        for turn_id, messages in grouped.items():
            if turn_id not in selected:
                continue
            projected_turn_id = _derived_id("pubturn", capture.capture_hash, turn_id)
            for message in messages:
                snapshot_messages.append(
                    SnapshotMessage(
                        message_id=_derived_id(
                            "pubmsg",
                            capture.capture_hash,
                            message.message_id,
                        ),
                        turn_id=projected_turn_id,
                        ordinal=ordinal,
                        role=message.role,
                        content=message.content,
                    )
                )
                ordinal += 1

        if len({item.attachment_id for item in attachments}) != len(attachments):
            raise ValidationError("An attachment may be included only once")
        snapshot_resources = tuple(
            SnapshotResource(
                resource_id=_derived_id(
                    "pubres", capture.capture_hash, item.attachment_id
                ),
                display_name=item.display_name,
                media_type=item.media_type,
                content=item.content,
            )
            for item in attachments
        )
        snapshot = SnapshotContent(
            schema_version=cls.schema_version,
            title=title_override if title_override is not None else capture.title,
            messages=tuple(snapshot_messages),
            resources=snapshot_resources,
        )
        return SnapshotPreview(
            capture_id=capture.capture_id,
            included_turn_count=len(selected_turn_ids),
            included_message_count=len(snapshot_messages),
            snapshot=snapshot,
            preview_hash=cls.snapshot_hash(snapshot),
        )

    @staticmethod
    def _messages_by_turn(
        capture: CapturedSession,
    ) -> OrderedDict[str, tuple[CapturedMessage, CapturedMessage]]:
        grouped_lists: OrderedDict[str, list[CapturedMessage]] = OrderedDict()
        for message in capture.messages:
            grouped_lists.setdefault(message.turn_id, []).append(message)
        grouped: OrderedDict[str, tuple[CapturedMessage, CapturedMessage]] = OrderedDict()
        for turn_id, messages in grouped_lists.items():
            if len(messages) != 2:
                raise ValidationError("A captured turn is not a complete exchange")
            grouped[turn_id] = (messages[0], messages[1])
        return grouped

    @staticmethod
    def canonical_snapshot_bytes(snapshot: SnapshotContent) -> bytes:
        """Return the single canonical byte representation used for publication."""

        return json.dumps(
            snapshot.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    @classmethod
    def snapshot_hash(cls, snapshot: SnapshotContent) -> str:
        canonical = cls.canonical_snapshot_bytes(snapshot)
        return "sha256:" + hashlib.sha256(
            b"PrismSnapshotPreview-v1\0" + canonical
        ).hexdigest()

    _snapshot_hash = snapshot_hash
