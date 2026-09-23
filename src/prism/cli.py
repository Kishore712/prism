from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import re
import sys
import threading
import time
from urllib.parse import urlsplit
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from .config import Settings
from .database import PrismDatabase
from .development import SyntheticSessionSource
from .exceptions import PrismError, ValidationError
from .models.capture import (
    CaptureInventory,
    CapturePreview,
    CaptureResult,
    CaptureSelection,
    CaptureSource,
    RemoteCaptureSource,
)
from .models.draft import DraftReview, DraftSelectionRequest
from .models.publication import PublicationRequest
from .models.projection import ProjectionReview, ProjectionSelection
from .providers.chatgpt import ChatGPTSharedLinkAdapter
from .providers.registry import built_in_capture_adapters, capture_adapter_names
from .services.capture import CaptureService
from .services.database_admin import LegacyCaptureMigrationService
from .services.drafts import DraftService
from .services.durable_shared_link_capture import DurableSharedLinkCaptureService
from .services.normalization import CaptureNormalizer
from .services.publication import PublicationService
from .services.session_inspection import SessionInspector
from .services.sharing import SharingService
from .storage import DatabaseCaptureStore
from .interfaces.mcp.server import run_mcp_server
from .webui.app import run_web_ui
from . import recipient_client
from .tunnel import Tunnel


def inspect_session(session_file: Path, as_json: bool) -> int:
    inspection = SessionInspector(SyntheticSessionSource()).inspect(session_file)
    if as_json:
        print(json.dumps(inspection.as_dict(), indent=2))
        return 0

    print(f"Session: {inspection.title}")
    print(f"Share instructions present: {'yes' if inspection.has_instructions else 'no'}")
    print()
    print(f"Completed turns ({len(inspection.completed_turns)}):")
    for turn in inspection.completed_turns:
        print(f"  {turn.turn_id}")
        for message in turn.messages:
            print(f"    {message.role}: {message.preview}")
    print()
    print(f"Associated resources ({len(inspection.resources)}):")
    for resource in inspection.resources:
        print(
            f"  {resource.display_name} [{resource.media_type}]\n"
            f"    {resource.source_path}"
        )
    return 0


def _capture_service(data_dir: Path | None) -> CaptureService:
    capture_root = data_dir.expanduser().resolve() if data_dir else Settings.from_env().data_dir
    database = PrismDatabase(capture_root / "prism.db")
    return CaptureService(
        adapters=built_in_capture_adapters(),
        normalizer=CaptureNormalizer(),
        store=DatabaseCaptureStore(database),
    )


def _capture_root(data_dir: Path | None) -> Path:
    return data_dir.expanduser().resolve() if data_dir else Settings.from_env().data_dir


def _shared_link_services(
    data_dir: Path | None,
) -> tuple[DurableSharedLinkCaptureService, DraftService]:
    database = PrismDatabase(_capture_root(data_dir) / "prism.db")
    return (
        DurableSharedLinkCaptureService(
            adapter=ChatGPTSharedLinkAdapter(),
            normalizer=CaptureNormalizer(),
            database=database,
        ),
        DraftService(database),
    )


def _print_inventory(inventory: CaptureInventory, as_json: bool) -> None:
    if as_json:
        print(json.dumps(inventory.model_dump(mode="json"), indent=2))
        return
    print("Available conversations:")
    for index, conversation in enumerate(inventory.conversations, start=1):
        print(f"  [{index}] {conversation.title}")
        print(f"      reference: {conversation.conversation_ref}")
        print(f"      supported messages: {conversation.supported_message_count}")
        if conversation.updated_at:
            print(f"      updated: {conversation.updated_at}")
        for warning in conversation.warnings:
            print(f"      warning [{warning.code}]: {warning.message}")


def _print_capture_result(result: CaptureResult, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result.model_dump(mode="json"), indent=2))
        return
    print("Capture created:")
    print(f"  capture ID: {result.capture_id}")
    print(f"  content hash: {result.capture_hash}")
    print(f"  messages: {result.message_count}")
    print(f"  resource references: {result.resource_reference_count}")
    if result.draft_id is None:
        print(f"  owner-only artifact: {result.artifact_path}")
    else:
        print(f"  owner-state database: {result.artifact_path}")
        print(f"  draft ID: {result.draft_id}")
        print(f"  draft revision: {result.draft_revision}")
    for warning in result.warnings:
        print(f"  warning [{warning.code}]: {warning.message}")


def capture_list(
    source_path: Path,
    adapter_name: str,
    data_dir: Path | None,
    as_json: bool,
) -> int:
    adapter = built_in_capture_adapters()[adapter_name]
    inventory = adapter.inventory(CaptureSource(path=source_path))
    _print_inventory(inventory, as_json)
    return 0


def capture_import(
    source_path: Path,
    adapter_name: str,
    conversation_ref: str,
    data_dir: Path | None,
    as_json: bool,
) -> int:
    result = _capture_service(data_dir).capture(
        CaptureSource(path=source_path),
        adapter_name,
        CaptureSelection(conversation_ref=conversation_ref),
    )
    _print_capture_result(result, as_json)
    return 0


def capture_select(
    source_path: Path,
    adapter_name: str,
    data_dir: Path | None,
) -> int:
    service = _capture_service(data_dir)
    source = CaptureSource(path=source_path)
    inventory = service.inventory(source, adapter_name)
    _print_inventory(inventory, as_json=False)
    print()
    try:
        raw_choice = input(f"Select a conversation [1-{len(inventory.conversations)}]: ").strip()
        selected_index = int(raw_choice)
    except (EOFError, ValueError) as exc:
        raise ValidationError("Selection must be a conversation number") from exc
    if selected_index < 1 or selected_index > len(inventory.conversations):
        raise ValidationError("Selection is outside the available conversation range")
    selected = inventory.conversations[selected_index - 1]
    print(f"Selected: {selected.title}")
    result = service.capture(
        source,
        adapter_name,
        CaptureSelection(conversation_ref=selected.conversation_ref),
    )
    _print_capture_result(result, as_json=False)
    return 0


def _single_line(value: str, limit: int = 140) -> str:
    collapsed = " ".join(value.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1] + "…"


def _parse_turn_numbers(raw: str, turn_count: int) -> tuple[int, ...]:
    value = raw.strip().lower()
    if value == "all":
        return tuple(range(1, turn_count + 1))
    if value == "none":
        return ()
    if not value:
        return ()
    selected: set[int] = set()
    try:
        for part in value.split(","):
            token = part.strip()
            if not token:
                raise ValueError
            if "-" in token:
                bounds = token.split("-")
                if len(bounds) != 2:
                    raise ValueError
                start, end = (int(bound) for bound in bounds)
                if start > end:
                    raise ValueError
                selected.update(range(start, end + 1))
            else:
                selected.add(int(token))
    except ValueError as exc:
        raise ValidationError(
            "Turn selection must use numbers, comma lists, ranges, or 'all'"
        ) from exc
    if not selected or min(selected) < 1 or max(selected) > turn_count:
        raise ValidationError("Turn selection is outside the available range")
    return tuple(sorted(selected))


def _print_candidate(preview: CapturePreview) -> None:
    print("Candidate capture (temporary normalized state stored locally):")
    print(f"  title: {preview.title}")
    print(f"  turns: {len(preview.messages) // 2}")
    print(f"  messages: {len(preview.messages)}")
    print(f"  expires: {preview.expires_at}")
    print(f"  preview hash: {preview.preview_hash}")
    print()
    for turn_index in range(1, len(preview.messages) // 2 + 1):
        messages = [
            message for message in preview.messages if message.turn_index == turn_index
        ]
        print(f"  Turn {turn_index}")
        for message in messages:
            print(f"    {message.role.value}: {_single_line(message.content)}")
    if preview.observations.skipped_by_reason:
        print()
        print("  Excluded provider records:")
        for reason, count in preview.observations.skipped_by_reason.items():
            print(f"    {reason}: {count}")
    for warning in preview.warnings:
        print(f"  warning [{warning.code}]: {warning.message}")


def _print_full_candidate_turns(
    preview: CapturePreview,
    numbers: tuple[int, ...],
) -> None:
    for turn_number in numbers:
        print()
        print(f"--- Candidate turn {turn_number} (exact normalized text) ---")
        for message in preview.messages:
            if message.turn_index == turn_number:
                print(f"[{message.role.value}]")
                print(message.content)


def _print_projection_inventory(review: ProjectionReview) -> None:
    print("Snapshot selection (everything is excluded until selected):")
    for turn in review.turns:
        print(f"  Turn {turn.turn_index}")
        for message in turn.messages:
            print(f"    {message.role.value}: {_single_line(message.content)}")
    if review.resources:
        print("  Resources are reference-only and cannot be selected in this increment.")


def _print_draft_inventory(review: DraftReview) -> None:
    print(
        f"Durable draft {review.draft.draft_id} "
        f"(revision {review.draft.revision}):"
    )
    for item in review.turns:
        marker = "included" if item.included else "excluded"
        print(f"  Turn {item.turn.turn_index} [{marker}]")
        for message in item.turn.messages:
            print(f"    {message.role.value}: {_single_line(message.content)}")
    if review.resources:
        print(
            "  Resources seen in the source are reference-only; attach a local file "
            "with `prism draft resource add` to share its text."
        )
    if review.draft.attachments:
        print("  Attached resources (included in the snapshot):")
        for item in review.draft.attachments:
            print(
                f"    {item.attachment_id}  {item.display_name} "
                f"[{item.media_type}, {item.byte_size} bytes]"
            )


def _normalize_shared_link_input(raw_value: str) -> str:
    value = raw_value.strip()
    if value.startswith("\x1b[200~"):
        value = value.removeprefix("\x1b[200~")
    if value.endswith("\x1b[201~"):
        value = value.removesuffix("\x1b[201~")
    value = value.translate(
        {
            ord("\u200b"): None,
            ord("\u200c"): None,
            ord("\u200d"): None,
            ord("\ufeff"): None,
        }
    ).strip()
    if len(value) >= 2 and (value[0], value[-1]) in {
        ("<", ">"),
        ('"', '"'),
        ("'", "'"),
    }:
        value = value[1:-1].strip()
    markdown = re.fullmatch(
        r"\[[^\]]+\]\((https://chatgpt\.com/share/[A-Za-z0-9_-]{20,200}/?)\)",
        value,
    )
    if markdown is not None:
        value = markdown.group(1)
    return value


def _read_shared_link(visible_input: bool) -> str:
    if visible_input:
        raw_url = input("ChatGPT shared link (URL only; input visible): ")
    else:
        print(
            "Paste the URL with Command+V, then press Enter. "
            "The pasted text will remain invisible."
        )
        raw_url = getpass.getpass("ChatGPT shared link (input hidden): ")
        print(f"Received {len(raw_url.strip())} characters.")
    value = _normalize_shared_link_input(raw_url)
    if not value:
        raise ValidationError(
            "No URL was received. Retry with --visible-input if hidden paste is unclear."
        )
    return value


def _complete_shared_link_review(
    preview: CapturePreview,
    shared_link_service: object,
    draft_backend: object,
) -> int:
    _print_candidate(preview)

    try:
        raw_inspection = input(
            "\nShow exact candidate text for turns "
            "(for example 1,3-5 or all; Enter to continue): "
        )
    except EOFError as exc:
        shared_link_service.cancel(preview.import_id)  # type: ignore[attr-defined]
        raise ValidationError("Candidate review was cancelled") from exc
    inspection_numbers = _parse_turn_numbers(
        raw_inspection,
        len(preview.messages) // 2,
    )
    _print_full_candidate_turns(preview, inspection_numbers)

    try:
        confirmed = input(
            "\nPersist this complete owner-only capture? [y/N]: "
        ).strip().lower()
    except EOFError as exc:
        shared_link_service.cancel(preview.import_id)  # type: ignore[attr-defined]
        raise ValidationError("Capture confirmation was cancelled") from exc
    if confirmed not in {"y", "yes"}:
        shared_link_service.cancel(preview.import_id)  # type: ignore[attr-defined]
        print("Cancelled. The temporary candidate was discarded.")
        return 0

    result = shared_link_service.confirm(  # type: ignore[attr-defined]
        preview.import_id,
        preview.preview_hash,
    )
    _print_capture_result(result, as_json=False)
    print(
        "  note: this owner-only capture contains the complete candidate; "
        "the next selection controls only the recipient snapshot preview."
    )
    print()

    if result.draft_id is not None:
        review = draft_backend.review(result.draft_id)  # type: ignore[attr-defined]
        _print_draft_inventory(review)
        turn_count = len(review.turns)
    else:
        review = draft_backend.review(result.capture_id)  # type: ignore[attr-defined]
        _print_projection_inventory(review)
        turn_count = len(review.turns)
    try:
        raw_selection = input(
            "\nSelect turns for the snapshot preview "
            "(for example 1,3-5 or all): "
        )
    except EOFError as exc:
        raise ValidationError("Snapshot selection was cancelled") from exc
    selected_numbers = _parse_turn_numbers(raw_selection, turn_count)
    if not selected_numbers:
        raise ValidationError("At least one turn must be selected for a snapshot preview")
    if result.draft_id is not None:
        selected_turn_ids = tuple(
            review.turns[number - 1].turn.turn_id for number in selected_numbers
        )
        draft = draft_backend.replace_selection(  # type: ignore[attr-defined]
            result.draft_id,
            DraftSelectionRequest(
                expected_revision=result.draft_revision or 1,
                selected_turn_ids=selected_turn_ids,
            ),
        )
        snapshot_preview = draft_backend.preview(  # type: ignore[attr-defined]
            result.draft_id,
            draft.revision,
        )
    else:
        selected_turn_ids = tuple(
            review.turns[number - 1].turn_id for number in selected_numbers
        )
        snapshot_preview = draft_backend.preview(  # type: ignore[attr-defined]
            result.capture_id,
            ProjectionSelection(selected_turn_ids=selected_turn_ids),
        )

    print("\nExact recipient-visible snapshot preview:")
    print(json.dumps(snapshot_preview.snapshot.model_dump(mode="json"), indent=2))
    print(f"Preview hash: {snapshot_preview.preview_hash}")
    print(
        "Preview only: no PublishedSnapshot, invitation, tunnel, or recipient "
        "access was created."
    )
    return 0


def shared_link_review(data_dir: Path | None, visible_input: bool = True) -> int:
    shared_link_service, draft_service = _shared_link_services(data_dir)
    raw_url = _read_shared_link(visible_input)
    try:
        source = RemoteCaptureSource(url=raw_url)
    except PydanticValidationError as exc:
        raise ValidationError("The shared-link URL has an invalid length") from exc
    preview = shared_link_service.inspect(source)
    return _complete_shared_link_review(preview, shared_link_service, draft_service)


def shared_link_resume(import_id: str, data_dir: Path | None) -> int:
    shared_link_service, draft_service = _shared_link_services(data_dir)
    preview = shared_link_service.resume(import_id)
    return _complete_shared_link_review(preview, shared_link_service, draft_service)


def _draft_service(data_dir: Path | None) -> DraftService:
    return DraftService(PrismDatabase(_capture_root(data_dir) / "prism.db"))


def _publication_service(data_dir: Path | None) -> PublicationService:
    return PublicationService(PrismDatabase(_capture_root(data_dir) / "prism.db"))


def _sharing_service(data_dir: Path | None) -> SharingService:
    return SharingService(PrismDatabase(_capture_root(data_dir) / "prism.db"))


def drafts_list(data_dir: Path | None, as_json: bool) -> int:
    drafts = _draft_service(data_dir).list()
    if as_json:
        print(json.dumps([item.model_dump(mode="json") for item in drafts], indent=2))
        return 0
    if not drafts:
        print("No durable drafts found.")
        return 0
    for draft in drafts:
        print(
            f"{draft.draft_id}  revision={draft.revision}  "
            f"status={draft.status.value}  selected={len(draft.selected_turn_ids)}"
        )
    return 0


def draft_show(draft_id: str, data_dir: Path | None, as_json: bool) -> int:
    review = _draft_service(data_dir).review(draft_id)
    if as_json:
        print(json.dumps(review.model_dump(mode="json"), indent=2))
    else:
        _print_draft_inventory(review)
        if review.draft.preview_hash:
            print(f"Preview hash: {review.draft.preview_hash}")
    return 0


def draft_select(
    draft_id: str,
    turns: str,
    data_dir: Path | None,
    as_json: bool,
) -> int:
    service = _draft_service(data_dir)
    review = service.review(draft_id)
    numbers = _parse_turn_numbers(turns, len(review.turns))
    selected_turn_ids = tuple(
        review.turns[number - 1].turn.turn_id for number in numbers
    )
    state = service.replace_selection(
        draft_id,
        DraftSelectionRequest(
            expected_revision=review.draft.revision,
            selected_turn_ids=selected_turn_ids,
        ),
    )
    if as_json:
        print(json.dumps(state.model_dump(mode="json"), indent=2))
    else:
        print(
            f"Saved {len(state.selected_turn_ids)} selected turns in "
            f"{state.draft_id} at revision {state.revision}."
        )
    return 0


def draft_preview(draft_id: str, data_dir: Path | None, as_json: bool) -> int:
    service = _draft_service(data_dir)
    state = service.review(draft_id).draft
    preview = service.preview(draft_id, state.revision)
    if as_json:
        print(json.dumps(preview.model_dump(mode="json"), indent=2))
    else:
        print("Exact recipient-visible snapshot preview:")
        print(json.dumps(preview.snapshot.model_dump(mode="json"), indent=2))
        print(f"Draft revision: {preview.draft_revision}")
        print(f"Preview hash: {preview.preview_hash}")
    return 0


def draft_publish(
    draft_id: str,
    expected_revision: int,
    expected_preview_hash: str,
    data_dir: Path | None,
    as_json: bool,
) -> int:
    result = _publication_service(data_dir).publish(
        PublicationRequest(
            draft_id=draft_id,
            expected_revision=expected_revision,
            expected_preview_hash=expected_preview_hash,
        )
    )
    if as_json:
        print(json.dumps(result.model_dump(mode="json"), indent=2))
    else:
        action = "created" if result.created else "reused"
        print(f"Published snapshot ({action} immutable payload):")
        print(f"  snapshot ID: {result.snapshot_id}")
        print(f"  publication ID: {result.publication_id}")
        print(f"  content hash: {result.content_hash}")
        print(f"  draft revision: {result.draft_revision}")
    return 0


def draft_resource_add(
    draft_id: str,
    file_path: Path,
    name: str | None,
    data_dir: Path | None,
    as_json: bool,
) -> int:
    path = file_path.expanduser()
    if not path.is_file():
        raise ValidationError("The resource must be an existing regular file")
    if path.stat().st_size > 200 * 1024:
        raise ValidationError("A resource may be at most 200 KiB")
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError("Only UTF-8 text or Markdown files can be attached") from exc
    service = _draft_service(data_dir)
    revision = service.review(draft_id).draft.revision
    state = service.attach_text_resource(draft_id, revision, name or path.name, content)
    if as_json:
        print(json.dumps(state.model_dump(mode="json"), indent=2))
    else:
        added = state.attachments[-1]
        print(
            f"Attached {added.display_name} ({added.byte_size} bytes) as "
            f"{added.attachment_id}; draft is now revision {state.revision}."
        )
        print("Re-run `prism draft preview` (or publish with --yes) to review it.")
    return 0


def draft_resource_remove(
    draft_id: str,
    attachment_id: str,
    data_dir: Path | None,
    as_json: bool,
) -> int:
    service = _draft_service(data_dir)
    revision = service.review(draft_id).draft.revision
    state = service.remove_attachment(draft_id, revision, attachment_id)
    if as_json:
        print(json.dumps(state.model_dump(mode="json"), indent=2))
    else:
        print(f"Removed {attachment_id}; draft is now revision {state.revision}.")
    return 0


def _print_findings(findings, decided_ids: frozenset[str], *, header: str = "Findings") -> None:
    if not findings:
        print(f"{header}: none.")
        return
    print(f"{header}:")
    for finding in findings:
        status = "allowed" if finding.finding_id in decided_ids else "needs review"
        print(
            f"  [{finding.severity.value:5s}] {finding.finding_id}  "
            f"{finding.finding_class.value} ({status})"
        )
        print(f"      {finding.detail}")
        print(f"      excerpt: {finding.excerpt}")


def draft_findings(draft_id: str, data_dir: Path | None, as_json: bool) -> int:
    drafts = _draft_service(data_dir)
    findings = drafts.scan(draft_id)
    decided = drafts.decided_finding_ids(draft_id)
    if as_json:
        print(
            json.dumps(
                {
                    "findings": [
                        {**finding.model_dump(mode="json"), "decided": finding.finding_id in decided}
                        for finding in findings
                    ]
                },
                indent=2,
            )
        )
        return 0
    _print_findings(findings, decided)
    blocking = [
        f for f in findings if f.severity.value == "block" and f.finding_id not in decided
    ]
    if blocking:
        print(
            f"\n{len(blocking)} finding(s) block publication. Resolve with "
            "`prism draft resolve <draft-id> <finding-id> --reason \"...\"`, "
            "or change the selection/attachments to remove the content."
        )
    return 0


def draft_resolve(
    draft_id: str,
    finding_id: str,
    reason: str,
    data_dir: Path | None,
    as_json: bool,
) -> int:
    drafts = _draft_service(data_dir)
    match = next((f for f in drafts.scan(draft_id) if f.finding_id == finding_id), None)
    if match is None:
        raise ValidationError(
            "That finding is not present in the current scan; re-run "
            "`prism draft findings` for the current IDs."
        )
    drafts.allow_finding(draft_id, finding_id, match.finding_class.value, reason)
    if as_json:
        print(json.dumps({"finding_id": finding_id, "disposition": "allow", "reason": reason}, indent=2))
    else:
        print(f"Allowed {finding_id} ({match.finding_class.value}): {reason}")
    return 0


def draft_unresolve(draft_id: str, finding_id: str, data_dir: Path | None, as_json: bool) -> int:
    drafts = _draft_service(data_dir)
    drafts.revoke_finding_decision(draft_id, finding_id)
    if as_json:
        print(json.dumps({"finding_id": finding_id, "revoked": True}, indent=2))
    else:
        print(f"Removed the decision on {finding_id}; it will block publication again if present.")
    return 0


def draft_title(
    draft_id: str,
    title: str | None,
    clear: bool,
    data_dir: Path | None,
    as_json: bool,
) -> int:
    drafts = _draft_service(data_dir)
    revision = drafts.review(draft_id).draft.revision
    state = drafts.set_title(draft_id, revision, None if clear else title)
    if as_json:
        print(json.dumps(state.model_dump(mode="json"), indent=2))
    else:
        shown = state.title_override or "(source title, no override)"
        print(f"Draft {draft_id} title is now: {shown}")
    return 0


def draft_provenance(draft_id: str, data_dir: Path | None, as_json: bool) -> int:
    drafts = _draft_service(data_dir)
    events = drafts.provenance(draft_id)
    tainted = {record.event_id for record in drafts.tainted_events(draft_id)}
    if as_json:
        print(
            json.dumps(
                {
                    "events": [
                        {**event.model_dump(mode="json"), "tainted": event.event_id in tainted}
                        for event in events
                    ]
                },
                indent=2,
            )
        )
        return 0
    if not events:
        print(f"Draft {draft_id} has no captured tool-call events.")
        return 0
    for event in events:
        mark = " [TAINTED]" if event.event_id in tainted else ""
        print(
            f"  {event.event_id}  turn {event.turn_index}  {event.tool_name}  "
            f"{event.resource_label}{mark}"
        )
    return 0


def draft_taint(
    draft_id: str,
    event_id: str,
    reason: str,
    data_dir: Path | None,
    as_json: bool,
) -> int:
    drafts = _draft_service(data_dir)
    record = drafts.taint(draft_id, event_id, reason)
    if as_json:
        print(json.dumps(record.model_dump(mode="json"), indent=2))
    else:
        print(
            f"Tainted {event_id}: {reason}\n"
            "Every currently-included turn at or after this event now blocks "
            "publication as a provenance-derived finding "
            "(see `prism draft findings`)."
        )
    return 0


def draft_untaint(draft_id: str, event_id: str, data_dir: Path | None, as_json: bool) -> int:
    drafts = _draft_service(data_dir)
    drafts.untaint(draft_id, event_id)
    if as_json:
        print(json.dumps({"event_id": event_id, "untainted": True}, indent=2))
    else:
        print(f"Removed the taint on {event_id}; it no longer blocks publication.")
    return 0


def draft_graph(draft_id: str, data_dir: Path | None, as_json: bool) -> int:
    drafts = _draft_service(data_dir)
    edges = drafts.dependency_graph(draft_id)
    if as_json:
        print(
            json.dumps(
                {
                    "edges": [
                        {
                            "source_ref": edge.source_ref,
                            "target_ref": edge.target_ref,
                            "kind": edge.kind,
                            "confidence": edge.confidence,
                            "evidence": edge.evidence,
                        }
                        for edge in edges
                    ]
                },
                indent=2,
            )
        )
        return 0
    if not edges:
        print(f"Draft {draft_id} has no dependency-graph suggestions right now.")
        return 0
    print(f"{len(edges)} suggestion(s) — for the owner to review, never applied automatically:")
    for edge in edges:
        print(
            f"  {edge.source_ref} -> {edge.target_ref}  [{edge.kind}/{edge.confidence}]  "
            f"{edge.evidence}"
        )
    return 0


def snapshot_receipt(snapshot_id: str, data_dir: Path | None, as_json: bool) -> int:
    service = _publication_service(data_dir)
    receipt = service.get_receipt(snapshot_id)
    valid = service.verify_receipt(receipt)
    if as_json:
        print(json.dumps({**receipt.model_dump(mode="json"), "signature_valid": valid}, indent=2))
        return 0
    print(f"Receipt for {snapshot_id}:")
    print(f"  capture hash:   {receipt.capture_hash}")
    print(f"  snapshot hash:  {receipt.snapshot_hash}")
    print(f"  findings:       {receipt.finding_count} ({receipt.override_count} overridden)")
    print(f"  created:        {receipt.created_at}")
    print(f"  signing key:    {receipt.public_key_hex}")
    print(f"  signature valid: {valid}")
    return 0


def draft_publish_confirm(
    draft_id: str,
    assume_yes: bool,
    data_dir: Path | None,
    as_json: bool,
) -> int:
    """Preview the current revision, ask for confirmation, publish exactly that."""

    drafts = _draft_service(data_dir)
    state = drafts.review(draft_id).draft
    gate = drafts.gate(draft_id)
    if not as_json and (gate.blocking or gate.warnings):
        _print_findings(tuple(gate.blocking) + tuple(gate.warnings), drafts.decided_finding_ids(draft_id))
        print()
    if gate.blocking:
        raise ValidationError(
            f"{len(gate.blocking)} finding(s) must be resolved first "
            "(`prism draft findings`, then `prism draft resolve`)."
        )
    preview = drafts.preview(draft_id, state.revision)
    if not as_json:
        print("Exact recipient-visible snapshot:")
        for message in preview.snapshot.messages:
            print(f"  [{message.ordinal}] {message.role.value}: {_single_line(message.content, 110)}")
        for resource in preview.snapshot.resources:
            print(f"  resource: {resource.display_name} ({len(resource.content)} chars)")
        print(f"  title: {preview.snapshot.title}")
        print(f"  preview hash: {preview.preview_hash}")
    if not assume_yes:
        if not sys.stdin.isatty():
            raise ValidationError("Refusing to publish without --yes on a non-interactive input")
        answer = input("Publish exactly this to a new immutable snapshot? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Not published.")
            return 1
    return draft_publish(
        draft_id,
        state.revision,
        preview.preview_hash,
        data_dir,
        as_json,
    )


def _recent_claude_code_sessions(limit: int = 8) -> list[Path]:
    root = Path("~/.claude/projects").expanduser()
    if not root.is_dir():
        return []
    files = [item for item in root.glob("*/*.jsonl") if item.is_file()]
    return sorted(files, key=lambda item: item.stat().st_mtime, reverse=True)[:limit]


def _draft_from_local_source(
    source: Path | None,
    adapter_name: str,
    data_dir: Path | None,
) -> tuple[str, str]:
    """Capture a local transcript file and return ``(draft_id, title)``."""

    service = _capture_service(data_dir)
    adapter = built_in_capture_adapters()[adapter_name]
    if source is None:
        sessions = _recent_claude_code_sessions()
        if not sessions:
            raise ValidationError("No Claude Code sessions found; pass a transcript path")
        print("Recent Claude Code sessions:")
        for index, path in enumerate(sessions, start=1):
            try:
                summary = adapter.inventory(CaptureSource(path=path)).conversations[0]
                label = f"{summary.title}  ({summary.supported_message_count // 2} turns)"
            except PrismError:
                label = "(no completed turns)"
            print(f"  [{index}] {_single_line(label, 100)}")
        try:
            source = sessions[int(input("Session to share: ").strip()) - 1]
        except (ValueError, IndexError, EOFError) as exc:
            raise ValidationError("Choose a session number from the list") from exc
    inventory = service.inventory(CaptureSource(path=source), adapter_name)
    conversation = inventory.conversations[0]
    result = service.capture(
        CaptureSource(path=source),
        adapter_name,
        CaptureSelection(conversation_ref=conversation.conversation_ref),
    )
    for warning in result.warnings:
        print(f"note: {warning.message}")
    assert result.draft_id is not None
    return result.draft_id, conversation.title


def _draft_from_chatgpt_link(
    data_dir: Path | None,
    hidden_input: bool,
) -> tuple[str, str]:
    """Fetch an owner-created ChatGPT shared link and return ``(draft_id, title)``."""

    shared_link_service, _ = _shared_link_services(data_dir)
    raw_url = _read_shared_link(visible_input=not hidden_input)
    try:
        source = RemoteCaptureSource(url=raw_url)
    except PydanticValidationError as exc:
        raise ValidationError("The shared-link URL has an invalid length") from exc
    print("Fetching the public page (no cookies, no credentials, no scripts run)…")
    preview = shared_link_service.inspect(source)
    _print_candidate(preview)
    answer = input(
        "\nStore this complete conversation on this machine (owner-only) so you can choose "
        "what to share? [y/N] "
    ).strip().lower()
    if answer not in {"y", "yes"}:
        shared_link_service.cancel(preview.import_id)
        raise ValidationError("Cancelled; the temporary candidate was discarded")
    result = shared_link_service.confirm(preview.import_id, preview.preview_hash)
    assert result.draft_id is not None
    return result.draft_id, preview.title


def quickshare(
    source: Path | None,
    adapter_name: str,
    recipient_hint: str | None,
    data_dir: Path | None,
    serve: bool,
    tunnel_provider: str | None,
    chatgpt_link: bool = False,
    hidden_input: bool = False,
    approve_here: bool = False,
) -> int:
    """Guided flow: pick a thread, choose turns and files, publish, invite, serve."""

    if not sys.stdin.isatty():
        raise ValidationError("quickshare is interactive; use the individual commands in scripts")
    if chatgpt_link:
        draft_id, title = _draft_from_chatgpt_link(data_dir, hidden_input)
    else:
        draft_id, title = _draft_from_local_source(source, adapter_name, data_dir)

    drafts = _draft_service(data_dir)
    review = drafts.review(draft_id)
    print()
    print("Everything is excluded until you include it. Turns:")
    _print_draft_inventory(review)
    raw = input("\nTurns to share (e.g. 1,3-5, all, none): ")
    numbers = _parse_turn_numbers(raw, len(review.turns))
    if not numbers:
        print("Nothing selected; nothing shared.")
        return 1
    state = drafts.replace_selection(
        draft_id,
        DraftSelectionRequest(
            expected_revision=review.draft.revision,
            selected_turn_ids=tuple(review.turns[n - 1].turn.turn_id for n in numbers),
        ),
    )
    while True:
        raw_path = input("Attach a text/Markdown file to share (blank to finish): ").strip()
        if not raw_path:
            break
        path = Path(raw_path).expanduser()
        try:
            if not path.is_file() or path.stat().st_size > 200 * 1024:
                raise ValidationError("Not a regular file of at most 200 KiB")
            state = drafts.attach_text_resource(
                draft_id, state.revision, path.name, path.read_text(encoding="utf-8")
            )
            print(f"  attached {path.name}")
        except (PrismError, UnicodeDecodeError, OSError) as exc:
            print(f"  skipped: {exc}")
    if draft_publish_confirm(draft_id, False, data_dir, False) != 0:
        return 1
    snapshot_id = _publication_service(data_dir).list()[0].snapshot_id
    sharing = _sharing_service(data_dir)
    share = sharing.create_share(snapshot_id, title[:200])
    now = datetime.now(timezone.utc)
    hint = recipient_hint or input("Who is this for (shown to you when approving)? ").strip() or None
    invitation = sharing.create_invitation(
        share.share.share_id,
        version=None,
        expires_at=(now + timedelta(hours=24)).isoformat().replace("+00:00", "Z"),
        grant_expires_at=(now + timedelta(days=7)).isoformat().replace("+00:00", "Z"),
        recipient_hint=hint,
    )
    print()
    print(f"Share {share.share.share_id} created.")
    print(f"Invitation code (send it out of band; shown once): {invitation.invitation_token}")
    if chatgpt_link:
        print(
            "Reminder: the ChatGPT shared link is a public bearer URL. Delete it in ChatGPT "
            "(Settings > Data controls > Shared links) now that Prism has captured it."
        )
    print("Recipients need the MCP URL (printed by the server) and this code.")
    print("Approve them with `prism grants watch` (or `prism grant approve <grant-id>`).")
    if serve:
        return mcp_serve(
            data_dir, None, None, None, None, None, None, tunnel_provider, None, approve_here
        )
    print("Now start the server: `prism mcp serve --tunnel ngrok --approve-here`.")
    return 0


def _pending_grant_lines(sharing: SharingService) -> list[tuple[str, str]]:
    """Return ``(grant_id, description)`` for grants waiting on owner approval."""

    hints = {
        item.invitation_id: item.recipient_hint for item in sharing.list_invitations()
    }
    pending = []
    for grant in sharing.list_grants():
        if grant.approval.value != "pending" or grant.status.value != "active":
            continue
        expected = hints.get(grant.invitation_id)
        pending.append(
            (
                grant.grant_id,
                f"'{grant.recipient_label or '?'}' redeemed an invitation"
                + (f" you made for '{expected}'" if expected else "")
                + f" (grant {grant.grant_id})",
            )
        )
    return pending


def grants_watch(
    data_dir: Path | None,
    interval: float = 2.0,
    stop: "threading.Event | None" = None,
    ask=input,
    out=print,
) -> int:
    """Poll for pending grants and ask the owner to approve or deny each."""

    sharing = _sharing_service(data_dir)
    seen: set[str] = set()
    out("Watching for recipients waiting on your approval (Ctrl-C to stop)…")
    try:
        while stop is None or not stop.is_set():
            for grant_id, description in _pending_grant_lines(sharing):
                if grant_id in seen:
                    continue
                seen.add(grant_id)
                out(f"\n>>> {description}")
                answer = ask("    Is this the person you meant? Approve? [y/N] ").strip().lower()
                if answer in {"y", "yes"}:
                    sharing.approve_grant(grant_id)
                    out("    Approved.")
                else:
                    sharing.deny_grant(grant_id)
                    out("    Denied.")
            if stop is not None:
                stop.wait(interval)
            else:
                time.sleep(interval)
    except (KeyboardInterrupt, EOFError):
        out("\nStopped watching.")
    return 0


def snapshots_list(data_dir: Path | None, as_json: bool) -> int:
    snapshots = _publication_service(data_dir).list()
    if as_json:
        print(json.dumps([item.model_dump(mode="json") for item in snapshots], indent=2))
        return 0
    if not snapshots:
        print("No published snapshots found.")
        return 0
    for snapshot in snapshots:
        print(
            f"{snapshot.snapshot_id}  status={snapshot.status.value}  "
            f"messages={snapshot.message_count}  hash={snapshot.content_hash}"
        )
    return 0


def snapshot_show(snapshot_id: str, data_dir: Path | None, as_json: bool) -> int:
    snapshot = _publication_service(data_dir).get(snapshot_id)
    payload = snapshot.model_dump(mode="json")
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(json.dumps(payload, indent=2))
    return 0


def snapshot_revoke(
    snapshot_id: str,
    purge: bool,
    data_dir: Path | None,
    as_json: bool,
) -> int:
    snapshot = _publication_service(data_dir).revoke(snapshot_id, purge=purge)
    if as_json:
        print(json.dumps(snapshot.model_dump(mode="json"), indent=2))
    else:
        verb = "purged" if purge else "revoked"
        print(f"Snapshot {snapshot_id} {verb}; dependent access was revoked.")
    return 0


def shares_list(data_dir: Path | None, as_json: bool) -> int:
    shares = _sharing_service(data_dir).list_shares()
    if as_json:
        print(json.dumps([item.model_dump(mode="json") for item in shares], indent=2))
        return 0
    if not shares:
        print("No shares found.")
        return 0
    for item in shares:
        print(
            f"{item.share.share_id}  status={item.share.status.value}  "
            f"versions={len(item.versions)}  name={item.share.name}"
        )
    return 0


def share_create(
    snapshot_id: str,
    name: str | None,
    data_dir: Path | None,
    as_json: bool,
) -> int:
    share = _sharing_service(data_dir).create_share(snapshot_id, name)
    if as_json:
        print(json.dumps(share.model_dump(mode="json"), indent=2))
    else:
        version = share.versions[-1]
        print(f"Created share {share.share.share_id} at version {version.version}.")
        print(
            "Next: `prism invitation create "
            f"{share.share.share_id}` then `prism mcp serve --public-url <https-url>`."
        )
    return 0


def share_add_version(
    share_id: str,
    snapshot_id: str,
    data_dir: Path | None,
    as_json: bool,
) -> int:
    share = _sharing_service(data_dir).add_version(share_id, snapshot_id)
    if as_json:
        print(json.dumps(share.model_dump(mode="json"), indent=2))
    else:
        print(f"Added version {share.versions[-1].version} to share {share_id}.")
    return 0


def share_show(share_id: str, data_dir: Path | None, as_json: bool) -> int:
    share = _sharing_service(data_dir).get_share(share_id)
    if as_json:
        print(json.dumps(share.model_dump(mode="json"), indent=2))
    else:
        print(json.dumps(share.model_dump(mode="json"), indent=2))
    return 0


def share_revoke(share_id: str, data_dir: Path | None, as_json: bool) -> int:
    share = _sharing_service(data_dir).revoke_share(share_id)
    if as_json:
        print(json.dumps(share.model_dump(mode="json"), indent=2))
    else:
        print(f"Revoked share {share_id} and all of its invitations and grants.")
    return 0


def share_revoke_version(
    share_id: str,
    version: int,
    data_dir: Path | None,
    as_json: bool,
) -> int:
    share = _sharing_service(data_dir).revoke_share_version(share_id, version)
    if as_json:
        print(json.dumps(share.model_dump(mode="json"), indent=2))
    else:
        print(f"Revoked version {version} of share {share_id}.")
    return 0


def invitation_create(
    share_id: str,
    version: int | None,
    expires_hours: int,
    grant_hours: int,
    data_dir: Path | None,
    as_json: bool,
    recipient_hint: str | None = None,
    require_approval: bool = True,
) -> int:
    if expires_hours < 1 or grant_hours <= expires_hours:
        raise ValidationError(
            "Invitation hours must be positive and grant hours must be greater"
        )
    now = datetime.now(timezone.utc)
    invitation = _sharing_service(data_dir).create_invitation(
        share_id,
        version=version,
        expires_at=(now + timedelta(hours=expires_hours)).isoformat().replace("+00:00", "Z"),
        grant_expires_at=(now + timedelta(hours=grant_hours)).isoformat().replace("+00:00", "Z"),
        require_approval=require_approval,
        recipient_hint=recipient_hint,
    )
    if as_json:
        print(json.dumps(invitation.model_dump(mode="json"), indent=2))
    else:
        print(f"Invitation {invitation.invitation.invitation_id} created.")
        print("Copy this one-time code now; Prism stores only its hash:")
        print(invitation.invitation_token)
        print(
            "Give it to the recipient out of band. They enter it in the browser step "
            "when connecting; it never goes through the chat."
        )
        if require_approval:
            print(
                "After they connect, approve them with: prism grant approve <grant-id> "
                "(see `prism grants list`)."
            )
    return 0


def _read_token(provided: str | None, prompt: str) -> str:
    token = provided if provided is not None else getpass.getpass(prompt)
    if not token.strip():
        raise ValidationError("A token is required")
    return token.strip()


def invitation_redeem(
    token: str | None,
    data_dir: Path | None,
    as_json: bool,
) -> int:
    grant = _sharing_service(data_dir).redeem_invitation(
        _read_token(token, "One-time invitation token (input hidden): ")
    )
    if as_json:
        print(json.dumps(grant.model_dump(mode="json"), indent=2))
    else:
        print(f"Created grant {grant.grant.grant_id}.")
        print("Copy this grant token now; Prism stores only its hash:")
        print(grant.grant_token)
    return 0


def invitation_revoke(
    invitation_id: str,
    data_dir: Path | None,
    as_json: bool,
) -> int:
    invitation = _sharing_service(data_dir).revoke_invitation(invitation_id)
    if as_json:
        print(json.dumps(invitation.model_dump(mode="json"), indent=2))
    else:
        print(f"Revoked invitation {invitation_id}.")
    return 0


def invitations_list(data_dir: Path | None, as_json: bool) -> int:
    invitations = _sharing_service(data_dir).list_invitations()
    if as_json:
        print(
            json.dumps(
                [item.model_dump(mode="json") for item in invitations],
                indent=2,
            )
        )
        return 0
    if not invitations:
        print("No invitations found.")
        return 0
    for invitation in invitations:
        print(
            f"{invitation.invitation_id}  status={invitation.status.value}  "
            f"version={invitation.share_version_id}  expires={invitation.expires_at}"
        )
    return 0


def grant_verify(token: str | None, data_dir: Path | None, as_json: bool) -> int:
    authorization = _sharing_service(data_dir).authorize_grant(
        _read_token(token, "Grant token (input hidden): ")
    )
    if as_json:
        print(json.dumps(authorization.model_dump(mode="json"), indent=2))
    else:
        print("Grant is active and pinned to:")
        print(f"  share: {authorization.share.share_id}")
        print(f"  version: {authorization.share_version.version}")
        print(f"  snapshot: {authorization.snapshot.snapshot_id}")
    return 0


def grant_revoke(grant_id: str, data_dir: Path | None, as_json: bool) -> int:
    grant = _sharing_service(data_dir).revoke_grant(grant_id)
    if as_json:
        print(json.dumps(grant.model_dump(mode="json"), indent=2))
    else:
        print(f"Revoked grant {grant_id}.")
    return 0


def grants_list(data_dir: Path | None, as_json: bool) -> int:
    grants = _sharing_service(data_dir).list_grants()
    if as_json:
        print(json.dumps([item.model_dump(mode="json") for item in grants], indent=2))
        return 0
    if not grants:
        print("No grants found.")
        return 0
    for grant in grants:
        print(
            f"{grant.grant_id}  status={grant.status.value}  "
            f"approval={grant.approval.value}  "
            f"recipient={grant.recipient_label or '-'}  "
            f"version={grant.share_version_id}  expires={grant.expires_at}"
        )
    return 0


def grant_approve(grant_id: str, data_dir: Path | None, as_json: bool) -> int:
    grant = _sharing_service(data_dir).approve_grant(grant_id)
    if as_json:
        print(json.dumps(grant.model_dump(mode="json"), indent=2))
    else:
        print(f"Approved grant {grant_id} for {grant.recipient_label or 'recipient'}.")
    return 0


def grant_deny(grant_id: str, data_dir: Path | None, as_json: bool) -> int:
    grant = _sharing_service(data_dir).deny_grant(grant_id)
    if as_json:
        print(json.dumps(grant.model_dump(mode="json"), indent=2))
    else:
        print(f"Denied grant {grant_id}.")
    return 0


def audit_list(
    grant_id: str | None,
    limit: int,
    data_dir: Path | None,
    as_json: bool,
) -> int:
    events = _sharing_service(data_dir).list_events(grant_id=grant_id, limit=limit)
    if as_json:
        print(json.dumps([item.model_dump(mode="json") for item in events], indent=2))
        return 0
    if not events:
        print("No audit events found.")
        return 0
    for event in events:
        print(
            f"{event.created_at}  {event.event_type}"
            f"{'/' + event.detail if event.detail else ''}  "
            f"grant={event.grant_id or '-'}  share={event.share_id or '-'}"
        )
    return 0


def database_init(data_dir: Path | None) -> int:
    database = PrismDatabase(_capture_root(data_dir) / "prism.db")
    database.initialize()
    print(f"Initialized Prism database: {database.path}")
    return 0


def database_check(data_dir: Path | None) -> int:
    database = PrismDatabase(_capture_root(data_dir) / "prism.db")
    database.initialize()
    if not database.quick_check():
        raise ValidationError("The Prism database integrity check did not pass")
    print("Database integrity check: ok")
    return 0


def database_backup(data_dir: Path | None) -> int:
    database = PrismDatabase(_capture_root(data_dir) / "prism.db")
    target = database.backup()
    print(f"Database backup created: {target}")
    return 0


def database_migrate_legacy(
    data_dir: Path | None,
    legacy_root: Path | None,
    as_json: bool,
) -> int:
    root = _capture_root(data_dir)
    result = LegacyCaptureMigrationService(
        PrismDatabase(root / "prism.db")
    ).migrate(legacy_root or root / "captures")
    if as_json:
        print(json.dumps(result.model_dump(mode="json"), indent=2))
    else:
        print(
            f"Legacy captures: scanned={result.scanned}, "
            f"imported={result.imported}, already_imported={result.already_imported}"
        )
    return 0


def ui_serve(data_dir: Path | None, port: int) -> int:
    if port < 1 or port > 65_535:
        raise ValidationError("UI port must be between 1 and 65535")
    database = PrismDatabase(_capture_root(data_dir) / "prism.db")
    database.initialize()
    print(f"Prism owner UI on http://127.0.0.1:{port}", flush=True)
    print(f"  Reading {database.path}", flush=True)
    print("  Loopback only, no login — do not put this behind a tunnel or a public host.", flush=True)
    run_web_ui(data_dir, port=port)
    return 0


def mcp_serve(
    data_dir: Path | None,
    host: str | None,
    port: int | None,
    public_url: str | None,
    allowed_hosts: list[str] | None,
    allowed_origins: list[str] | None,
    owner_label: str | None,
    tunnel_provider: str | None = None,
    tunnel_domain: str | None = None,
    approve_here: bool = False,
) -> int:
    settings = Settings.from_env()
    bind_host = host or settings.host
    bind_port = port or settings.port
    if bind_port < 1 or bind_port > 65_535:
        raise ValidationError("MCP port must be between 1 and 65535")
    if approve_here and not sys.stdin.isatty():
        raise ValidationError("--approve-here needs an interactive terminal")
    tunnel: Tunnel | None = None
    if tunnel_provider:
        if public_url or settings.public_url:
            raise ValidationError("Use either --tunnel or --public-url, not both")
        tunnel = Tunnel(tunnel_provider, bind_port, domain=tunnel_domain)
        print(f"Starting {tunnel_provider} tunnel to 127.0.0.1:{bind_port}…", flush=True)
        public_url = tunnel.start()
        print(
            "  The tunnel provider terminates TLS and can observe recipient traffic.",
            flush=True,
        )
    resolved_public = public_url or settings.public_url
    if resolved_public is not None:
        parsed = urlsplit(resolved_public)
        loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not loopback:
            raise ValidationError("--public-url must use HTTPS unless it is loopback")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValidationError("--public-url must be an origin without a path")
    database = PrismDatabase(_capture_root(data_dir) / "prism.db")
    database.initialize()
    configured_hosts = tuple(allowed_hosts or ()) + settings.mcp_allowed_hosts
    configured_origins = tuple(allowed_origins or ()) + settings.mcp_allowed_origins
    effective_public = (resolved_public or f"http://{bind_host}:{bind_port}").rstrip("/")
    print(f"Prism recipient MCP server (OAuth 2.1) on {bind_host}:{bind_port}", flush=True)
    print(f"  MCP endpoint for the recipient: {effective_public}/mcp", flush=True)
    print(
        "  Recipients connect with that URL, then redeem their invitation in the "
        "browser step. Approve them with `prism grant approve`.",
        flush=True,
    )
    if resolved_public is None:
        print(
            "  No --public-url set: only loopback clients can complete authorization.",
            flush=True,
        )
    stop_watching = threading.Event()
    if approve_here:
        threading.Thread(
            target=grants_watch,
            args=(data_dir, 2.0, stop_watching),
            daemon=True,
        ).start()
    try:
        run_mcp_server(
            database,
            host=bind_host,
            port=bind_port,
            public_url=effective_public,
            allowed_hosts=configured_hosts,
            allowed_origins=configured_origins,
            owner_label=owner_label or settings.owner_label,
            trust_forwarded_for=settings.trust_forwarded_for or tunnel is not None,
        )
    finally:
        stop_watching.set()
        if tunnel is not None:
            tunnel.stop()
    return 0


def _print_recipient_result(tool: str, result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, indent=2))
        return
    provenance = result.get("provenance", {})
    print(
        f"Shared by {provenance.get('shared_by', '?')} — "
        f"\"{provenance.get('share_title', '?')}\" v{provenance.get('share_version', '?')}"
    )
    print(f"  ({provenance.get('notice', '')})")
    if tool == "prism_get_manifest":
        print(
            f"{result['message_count']} messages, {result['resource_count']} resources; "
            f"access expires {result['expires_at']}"
        )
        for message in result["messages"]:
            print(f"  [{message['ordinal']}] {message['role']} {message['message_id']}")
            print(f"      {message['preview']}")
        for resource in result.get("resources", []):
            print(f"  resource {resource['resource_id']}: {resource['display_name']}")
    elif tool == "prism_query_share":
        for block in result["context_blocks"]:
            label = block.get("role") or block.get("display_name") or block["source_type"]
            print(f"--- {label} ({block['source_id']}, score {block['score']}) ---")
            print(block["content"])
        if not result["context_blocks"]:
            print("No matching passages.")
        if result["truncated"]:
            print("(more results exist; refine the query)")
    else:
        print(result.get("content", ""))
        if result.get("next_cursor") is not None:
            print(f"\n[more: rerun with --cursor {result['next_cursor']}]")


def recipient_connect(
    url: str,
    state_dir: Path | None,
    headless: bool,
    name: str | None,
    invitation: str | None,
    no_browser: bool,
    as_json: bool,
) -> int:
    result = asyncio.run(
        recipient_client.connect(
            url,
            state_dir or recipient_client.DEFAULT_STATE_DIR,
            headless=headless,
            display_name=name,
            invitation=invitation,
            open_browser=not no_browser,
            log=lambda message: print(message, file=sys.stderr),
        )
    )
    if not as_json:
        print("Connected.")
    _print_recipient_result("prism_get_manifest", result, as_json)
    return 0


def recipient_call(
    tool: str,
    url: str,
    arguments: dict,
    state_dir: Path | None,
    as_json: bool,
) -> int:
    result = asyncio.run(
        recipient_client.call_tool(
            url,
            state_dir or recipient_client.DEFAULT_STATE_DIR,
            tool,
            arguments,
        )
    )
    _print_recipient_result(tool, result, as_json)
    return 0


def mcp_check(url: str) -> int:
    """Probe an MCP URL the way a connector would, and say what is wrong."""

    import urllib.error
    import urllib.request

    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.path.rstrip("/").endswith("/mcp"):
        raise ValidationError("Pass the full MCP URL, ending in /mcp")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    failures = 0

    def fetch(target: str, data: bytes | None = None):
        request = urllib.request.Request(
            target,
            data=data,
            headers={"Content-Type": "application/json", "ngrok-skip-browser-warning": "1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            body = error.read()
            headers = dict(error.headers)
            error.close()
            return error.code, headers, body
        except (urllib.error.URLError, TimeoutError) as error:
            return 0, {}, str(getattr(error, "reason", error)).encode()

    def report(ok: bool, message: str, hint: str = "") -> None:
        nonlocal failures
        print(f"  [{'ok' if ok else 'FAIL'}] {message}")
        if not ok:
            failures += 1
            if hint:
                print(f"        -> {hint}")

    print(f"Checking {url}")
    status, _, body = fetch(f"{origin}/healthz")
    report(
        status == 200,
        "server reachable (/healthz)",
        f"got {status or 'no response'} {body[:80]!r}; is `prism mcp serve` running and "
        "is the tunnel pointing at its port?",
    )
    status, _, body = fetch(f"{origin}/.well-known/oauth-protected-resource{parsed.path.rstrip('/')}")
    try:
        metadata = json.loads(body)
    except json.JSONDecodeError:
        metadata = {}
    report(
        status == 200 and metadata.get("resource", "").rstrip("/") == url.rstrip("/"),
        "protected-resource metadata names this exact URL",
        f"resource is {metadata.get('resource')!r}; start the server with "
        "--public-url (or --tunnel) set to the URL recipients use.",
    )
    status, _, body = fetch(f"{origin}/.well-known/oauth-authorization-server")
    try:
        server = json.loads(body)
    except json.JSONDecodeError:
        server = {}
    report(
        status == 200 and server.get("issuer", "").rstrip("/") == origin
        and "S256" in server.get("code_challenge_methods_supported", []),
        "authorization server metadata (issuer matches, PKCE S256)",
        f"issuer is {server.get('issuer')!r}",
    )
    report(
        bool(server.get("registration_endpoint")),
        "dynamic client registration advertised",
    )
    status, headers, _ = fetch(url, b'{"jsonrpc":"2.0","id":1,"method":"ping"}')
    challenge = {key.lower(): value for key, value in headers.items()}.get("www-authenticate", "")
    report(
        status == 401 and "resource_metadata" in challenge,
        "unauthenticated /mcp is refused with a discoverable challenge",
        f"got {status}; without a 401 + resource_metadata connectors cannot start OAuth.",
    )
    print("All checks passed." if not failures else f"{failures} check(s) failed.")
    return 0 if not failures else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prism",
        description="Prism owner-hosted shared-agent prototype",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser(
        "inspect-session",
        help="List completed turns and associated resources without publishing them",
    )
    inspect_parser.add_argument("session_file", type=Path)
    inspect_parser.add_argument(
        "--json",
        action="store_true",
        help="Return the normalized inspection as JSON",
    )

    capture_parser = subparsers.add_parser(
        "capture",
        help="Inventory or import an owner-selected conversation",
    )
    capture_subparsers = capture_parser.add_subparsers(dest="capture_command", required=True)

    def add_capture_source_arguments(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument("source", type=Path)
        command_parser.add_argument(
            "--adapter",
            choices=capture_adapter_names(),
            default="synthetic-chatgpt-export",
        )
        command_parser.add_argument(
            "--data-dir",
            type=Path,
            default=None,
            help="Owner-only Prism data directory (defaults to PRISM_DATA_DIR or ./var)",
        )

    list_parser = capture_subparsers.add_parser(
        "list",
        help="List conversation metadata without creating a capture",
    )
    add_capture_source_arguments(list_parser)
    list_parser.add_argument("--json", action="store_true")

    import_parser = capture_subparsers.add_parser(
        "import",
        help="Import exactly one conversation by its inventory reference",
    )
    add_capture_source_arguments(import_parser)
    import_parser.add_argument("--conversation-ref", required=True)
    import_parser.add_argument("--json", action="store_true")

    select_parser = capture_subparsers.add_parser(
        "select",
        help="Interactively select and import one conversation",
    )
    add_capture_source_arguments(select_parser)

    shared_link_parser = subparsers.add_parser(
        "shared-link",
        help="Inspect a public ChatGPT shared link and build a local snapshot preview",
    )
    shared_link_subparsers = shared_link_parser.add_subparsers(
        dest="shared_link_command",
        required=True,
    )
    review_parser = shared_link_subparsers.add_parser(
        "review",
        help="Run staged capture, owner selection, and exact snapshot preview",
    )
    review_parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Owner-only Prism data directory (defaults to PRISM_DATA_DIR or ./var)",
    )
    input_mode = review_parser.add_mutually_exclusive_group()
    input_mode.add_argument(
        "--visible-input",
        dest="visible_input",
        action="store_true",
        default=True,
        help=(
            "Show the pasted URL while entering it (default)"
        ),
    )
    input_mode.add_argument(
        "--hidden-input",
        dest="visible_input",
        action="store_false",
        help="Hide the pasted URL and report only its character count",
    )

    resume_parser = shared_link_subparsers.add_parser(
        "resume",
        help="Resume a durable normalized candidate by import ID",
    )
    resume_parser.add_argument("import_id")
    resume_parser.add_argument("--data-dir", type=Path, default=None)

    drafts_parser = subparsers.add_parser(
        "drafts",
        help="List durable owner drafts",
    )
    drafts_subparsers = drafts_parser.add_subparsers(
        dest="drafts_command",
        required=True,
    )
    drafts_list_parser = drafts_subparsers.add_parser("list")
    drafts_list_parser.add_argument("--data-dir", type=Path, default=None)
    drafts_list_parser.add_argument("--json", action="store_true")

    draft_parser = subparsers.add_parser(
        "draft",
        help="Read, update, preview, or publish one durable owner draft",
    )
    draft_subparsers = draft_parser.add_subparsers(
        dest="draft_command",
        required=True,
    )
    for name in ("show", "preview"):
        command = draft_subparsers.add_parser(name)
        command.add_argument("draft_id")
        command.add_argument("--data-dir", type=Path, default=None)
        command.add_argument("--json", action="store_true")
    draft_select_parser = draft_subparsers.add_parser("select")
    draft_select_parser.add_argument("draft_id")
    draft_select_parser.add_argument(
        "--turns",
        required=True,
        help="Complete allowlist such as 1,3-5, all, or none",
    )
    draft_select_parser.add_argument("--data-dir", type=Path, default=None)
    draft_select_parser.add_argument("--json", action="store_true")
    draft_resource_parser = draft_subparsers.add_parser(
        "resource",
        help="Attach or remove an owner-chosen text/Markdown resource",
    )
    resource_subparsers = draft_resource_parser.add_subparsers(
        dest="resource_command",
        required=True,
    )
    resource_add_parser = resource_subparsers.add_parser("add")
    resource_add_parser.add_argument("draft_id")
    resource_add_parser.add_argument("--file", type=Path, required=True)
    resource_add_parser.add_argument("--name", default=None)
    resource_add_parser.add_argument("--data-dir", type=Path, default=None)
    resource_add_parser.add_argument("--json", action="store_true")
    resource_remove_parser = resource_subparsers.add_parser("remove")
    resource_remove_parser.add_argument("draft_id")
    resource_remove_parser.add_argument("attachment_id")
    resource_remove_parser.add_argument("--data-dir", type=Path, default=None)
    resource_remove_parser.add_argument("--json", action="store_true")
    draft_publish_parser = draft_subparsers.add_parser(
        "publish",
        help=(
            "Publish an immutable snapshot: with --yes (or an interactive confirm) "
            "the current revision is previewed and published; or pass the exact "
            "--expected-revision/--expected-preview-hash"
        ),
    )
    draft_publish_parser.add_argument("draft_id")
    draft_publish_parser.add_argument("--yes", action="store_true")
    draft_publish_parser.add_argument("--expected-revision", type=int, default=None)
    draft_publish_parser.add_argument("--expected-preview-hash", default=None)
    draft_publish_parser.add_argument("--data-dir", type=Path, default=None)
    draft_publish_parser.add_argument("--json", action="store_true")

    draft_findings_parser = draft_subparsers.add_parser(
        "findings",
        help="Run the projection detectors over the draft's current selection",
    )
    draft_findings_parser.add_argument("draft_id")
    draft_findings_parser.add_argument("--data-dir", type=Path, default=None)
    draft_findings_parser.add_argument("--json", action="store_true")

    draft_resolve_parser = draft_subparsers.add_parser(
        "resolve",
        help="Allow one blocking finding through, with a required reason",
    )
    draft_resolve_parser.add_argument("draft_id")
    draft_resolve_parser.add_argument("finding_id")
    draft_resolve_parser.add_argument("--reason", required=True)
    draft_resolve_parser.add_argument("--data-dir", type=Path, default=None)
    draft_resolve_parser.add_argument("--json", action="store_true")

    draft_unresolve_parser = draft_subparsers.add_parser(
        "unresolve",
        help="Remove a prior decision on a finding (it will block publish again)",
    )
    draft_unresolve_parser.add_argument("draft_id")
    draft_unresolve_parser.add_argument("finding_id")
    draft_unresolve_parser.add_argument("--data-dir", type=Path, default=None)
    draft_unresolve_parser.add_argument("--json", action="store_true")

    draft_title_parser = draft_subparsers.add_parser(
        "title",
        help="Override the published title, or clear the override",
    )
    draft_title_parser.add_argument("draft_id")
    draft_title_parser.add_argument("--set", dest="title", default=None)
    draft_title_parser.add_argument(
        "--clear", action="store_true", help="Revert to the source conversation's title"
    )
    draft_title_parser.add_argument("--data-dir", type=Path, default=None)
    draft_title_parser.add_argument("--json", action="store_true")

    draft_provenance_parser = draft_subparsers.add_parser(
        "provenance",
        help="List this draft's captured tool-call events (Phase 9)",
    )
    draft_provenance_parser.add_argument("draft_id")
    draft_provenance_parser.add_argument("--data-dir", type=Path, default=None)
    draft_provenance_parser.add_argument("--json", action="store_true")

    draft_taint_parser = draft_subparsers.add_parser(
        "taint",
        help="Flag a tool-call event as a mistake; blocks downstream included turns",
    )
    draft_taint_parser.add_argument("draft_id")
    draft_taint_parser.add_argument("event_id")
    draft_taint_parser.add_argument("--reason", required=True)
    draft_taint_parser.add_argument("--data-dir", type=Path, default=None)
    draft_taint_parser.add_argument("--json", action="store_true")

    draft_untaint_parser = draft_subparsers.add_parser(
        "untaint",
        help="Remove a taint decision from a tool-call event",
    )
    draft_untaint_parser.add_argument("draft_id")
    draft_untaint_parser.add_argument("event_id")
    draft_untaint_parser.add_argument("--data-dir", type=Path, default=None)
    draft_untaint_parser.add_argument("--json", action="store_true")

    draft_graph_parser = draft_subparsers.add_parser(
        "graph",
        help="Show lexical/cue-phrase/provenance dependency-graph suggestions",
    )
    draft_graph_parser.add_argument("draft_id")
    draft_graph_parser.add_argument("--data-dir", type=Path, default=None)
    draft_graph_parser.add_argument("--json", action="store_true")

    snapshots_parser = subparsers.add_parser(
        "snapshots",
        help="List immutable published snapshots",
    )
    snapshots_subparsers = snapshots_parser.add_subparsers(
        dest="snapshots_command",
        required=True,
    )
    snapshots_list_parser = snapshots_subparsers.add_parser("list")
    snapshots_list_parser.add_argument("--data-dir", type=Path, default=None)
    snapshots_list_parser.add_argument("--json", action="store_true")

    snapshot_parser = subparsers.add_parser(
        "snapshot",
        help="Inspect or revoke one immutable snapshot",
    )
    snapshot_subparsers = snapshot_parser.add_subparsers(
        dest="snapshot_command",
        required=True,
    )
    snapshot_show_parser = snapshot_subparsers.add_parser("show")
    snapshot_show_parser.add_argument("snapshot_id")
    snapshot_show_parser.add_argument("--data-dir", type=Path, default=None)
    snapshot_show_parser.add_argument("--json", action="store_true")
    snapshot_receipt_parser = snapshot_subparsers.add_parser(
        "receipt",
        help="Show the signed projection receipt for a published snapshot",
    )
    snapshot_receipt_parser.add_argument("snapshot_id")
    snapshot_receipt_parser.add_argument("--data-dir", type=Path, default=None)
    snapshot_receipt_parser.add_argument("--json", action="store_true")
    snapshot_revoke_parser = snapshot_subparsers.add_parser("revoke")
    snapshot_revoke_parser.add_argument("snapshot_id")
    snapshot_revoke_parser.add_argument(
        "--purge",
        action="store_true",
        help="Also irreversibly remove the recipient payload bytes",
    )
    snapshot_revoke_parser.add_argument("--data-dir", type=Path, default=None)
    snapshot_revoke_parser.add_argument("--json", action="store_true")

    shares_parser = subparsers.add_parser("shares", help="List stable shares")
    shares_subparsers = shares_parser.add_subparsers(
        dest="shares_command",
        required=True,
    )
    shares_list_parser = shares_subparsers.add_parser("list")
    shares_list_parser.add_argument("--data-dir", type=Path, default=None)
    shares_list_parser.add_argument("--json", action="store_true")

    share_parser = subparsers.add_parser(
        "share",
        help="Create, version, inspect, or revoke a stable share",
    )
    share_subparsers = share_parser.add_subparsers(
        dest="share_command",
        required=True,
    )
    share_create_parser = share_subparsers.add_parser("create")
    share_create_parser.add_argument("snapshot_id")
    share_create_parser.add_argument("--name", default=None)
    share_create_parser.add_argument("--data-dir", type=Path, default=None)
    share_create_parser.add_argument("--json", action="store_true")
    share_version_parser = share_subparsers.add_parser("add-version")
    share_version_parser.add_argument("share_id")
    share_version_parser.add_argument("snapshot_id")
    share_version_parser.add_argument("--data-dir", type=Path, default=None)
    share_version_parser.add_argument("--json", action="store_true")
    for name in ("show", "revoke"):
        command = share_subparsers.add_parser(name)
        command.add_argument("share_id")
        command.add_argument("--data-dir", type=Path, default=None)
        command.add_argument("--json", action="store_true")
    share_revoke_version_parser = share_subparsers.add_parser("revoke-version")
    share_revoke_version_parser.add_argument("share_id")
    share_revoke_version_parser.add_argument("--version", type=int, required=True)
    share_revoke_version_parser.add_argument("--data-dir", type=Path, default=None)
    share_revoke_version_parser.add_argument("--json", action="store_true")

    invitation_parser = subparsers.add_parser(
        "invitation",
        help="Create, redeem, or revoke a one-time invitation",
    )
    invitation_subparsers = invitation_parser.add_subparsers(
        dest="invitation_command",
        required=True,
    )
    invitation_create_parser = invitation_subparsers.add_parser("create")
    invitation_create_parser.add_argument("share_id")
    invitation_create_parser.add_argument("--version", type=int, default=None)
    invitation_create_parser.add_argument("--expires-hours", type=int, default=24)
    invitation_create_parser.add_argument("--grant-hours", type=int, default=168)
    invitation_create_parser.add_argument(
        "--recipient-hint",
        default=None,
        help="Who you expect to redeem this (shown to you when approving)",
    )
    invitation_create_parser.add_argument(
        "--no-approval",
        action="store_true",
        help="Skip owner approval: whoever redeems the code is trusted immediately",
    )
    invitation_create_parser.add_argument("--data-dir", type=Path, default=None)
    invitation_create_parser.add_argument("--json", action="store_true")
    invitation_redeem_parser = invitation_subparsers.add_parser("redeem")
    invitation_redeem_parser.add_argument(
        "--token",
        default=None,
        help="One-time token; omit to enter it without terminal echo",
    )
    invitation_redeem_parser.add_argument("--data-dir", type=Path, default=None)
    invitation_redeem_parser.add_argument("--json", action="store_true")
    invitation_revoke_parser = invitation_subparsers.add_parser("revoke")
    invitation_revoke_parser.add_argument("invitation_id")
    invitation_revoke_parser.add_argument("--data-dir", type=Path, default=None)
    invitation_revoke_parser.add_argument("--json", action="store_true")

    invitations_parser = subparsers.add_parser(
        "invitations",
        help="List durable invitation metadata without token values",
    )
    invitations_subparsers = invitations_parser.add_subparsers(
        dest="invitations_command",
        required=True,
    )
    invitations_list_parser = invitations_subparsers.add_parser("list")
    invitations_list_parser.add_argument("--data-dir", type=Path, default=None)
    invitations_list_parser.add_argument("--json", action="store_true")

    grant_parser = subparsers.add_parser(
        "grant",
        help="Approve, deny, verify, or revoke a recipient grant",
    )
    grant_subparsers = grant_parser.add_subparsers(
        dest="grant_command",
        required=True,
    )
    grant_verify_parser = grant_subparsers.add_parser("verify")
    grant_verify_parser.add_argument(
        "--token",
        default=None,
        help="Grant token; omit to enter it without terminal echo",
    )
    grant_verify_parser.add_argument("--data-dir", type=Path, default=None)
    grant_verify_parser.add_argument("--json", action="store_true")
    for name, help_text in (
        ("approve", "Confirm the recipient behind a pending grant"),
        ("deny", "Reject the recipient behind a pending grant"),
    ):
        decision_parser = grant_subparsers.add_parser(name, help=help_text)
        decision_parser.add_argument("grant_id")
        decision_parser.add_argument("--data-dir", type=Path, default=None)
        decision_parser.add_argument("--json", action="store_true")
    grant_revoke_parser = grant_subparsers.add_parser("revoke")
    grant_revoke_parser.add_argument("grant_id")
    grant_revoke_parser.add_argument("--data-dir", type=Path, default=None)
    grant_revoke_parser.add_argument("--json", action="store_true")

    grants_parser = subparsers.add_parser(
        "grants",
        help="List durable grant metadata without token values",
    )
    grants_subparsers = grants_parser.add_subparsers(
        dest="grants_command",
        required=True,
    )
    grants_watch_parser = grants_subparsers.add_parser(
        "watch",
        help="Wait for recipients and approve or deny each interactively",
    )
    grants_watch_parser.add_argument("--data-dir", type=Path, default=None)
    grants_list_parser = grants_subparsers.add_parser("list")
    grants_list_parser.add_argument("--data-dir", type=Path, default=None)
    grants_list_parser.add_argument("--json", action="store_true")

    database_parser = subparsers.add_parser(
        "db",
        help="Initialize, check, back up, or migrate owner state",
    )
    database_subparsers = database_parser.add_subparsers(
        dest="database_command",
        required=True,
    )
    for name in ("init", "check", "backup"):
        command = database_subparsers.add_parser(name)
        command.add_argument("--data-dir", type=Path, default=None)
    migrate_parser = database_subparsers.add_parser("migrate-legacy")
    migrate_parser.add_argument("--data-dir", type=Path, default=None)
    migrate_parser.add_argument("--legacy-root", type=Path, default=None)
    migrate_parser.add_argument("--json", action="store_true")

    ui_parser = subparsers.add_parser(
        "ui",
        help="Run the owner web UI (loopback only — no login, same trust as this CLI)",
    )
    ui_parser.add_argument("--data-dir", type=Path, default=None)
    ui_parser.add_argument("--port", type=int, default=8788)

    quickshare_parser = subparsers.add_parser(
        "quickshare",
        help="Guided: pick a thread, choose turns and files, publish, and invite",
    )
    quickshare_parser.add_argument(
        "source",
        nargs="?",
        type=Path,
        default=None,
        help="Transcript file (default: choose from recent Claude Code sessions)",
    )
    quickshare_parser.add_argument("--adapter", default="claude-code-session", choices=capture_adapter_names())
    quickshare_parser.add_argument("--recipient-hint", default=None)
    quickshare_parser.add_argument(
        "--chatgpt-link",
        action="store_true",
        help="Capture a ChatGPT shared link (prompted) instead of a local transcript",
    )
    quickshare_parser.add_argument("--hidden-input", action="store_true")
    quickshare_parser.add_argument(
        "--approve-here",
        action="store_true",
        help="With --serve: approve recipients from this terminal",
    )
    quickshare_parser.add_argument("--data-dir", type=Path, default=None)
    quickshare_parser.add_argument(
        "--serve",
        action="store_true",
        help="After inviting, start the MCP server (with --tunnel to get a public URL)",
    )
    quickshare_parser.add_argument("--tunnel", choices=("ngrok", "cloudflared"), default=None)

    recipient_parser = subparsers.add_parser(
        "recipient",
        help="Recipient-side client: connect to a share and read it (any machine)",
    )
    recipient_subparsers = recipient_parser.add_subparsers(
        dest="recipient_command",
        required=True,
    )

    def _recipient_common(command: argparse.ArgumentParser) -> None:
        command.add_argument("url", help="The owner's MCP endpoint, ending in /mcp")
        command.add_argument("--state-dir", type=Path, default=None)
        command.add_argument("--json", action="store_true")

    recipient_connect_parser = recipient_subparsers.add_parser(
        "connect",
        help="Authorize this machine (browser step, or --headless in the terminal)",
    )
    _recipient_common(recipient_connect_parser)
    recipient_connect_parser.add_argument(
        "--headless",
        action="store_true",
        help="Redeem the invitation in the terminal instead of a browser",
    )
    recipient_connect_parser.add_argument("--name", default=None, help="Your display name")
    recipient_connect_parser.add_argument(
        "--invitation",
        default=None,
        help="Invitation code (default: PRISM_INVITATION or a hidden prompt)",
    )
    recipient_connect_parser.add_argument("--no-browser", action="store_true")
    _recipient_common(recipient_subparsers.add_parser("manifest"))
    recipient_query_parser = recipient_subparsers.add_parser("query")
    _recipient_common(recipient_query_parser)
    recipient_query_parser.add_argument("question")
    recipient_query_parser.add_argument("--max-results", type=int, default=5)
    for reader, argument in (("message", "message_id"), ("resource", "resource_id")):
        reader_parser = recipient_subparsers.add_parser(reader)
        _recipient_common(reader_parser)
        reader_parser.add_argument(argument)
        reader_parser.add_argument("--cursor", type=int, default=0)

    audit_parser = subparsers.add_parser(
        "audit",
        help="List metadata-only sharing and recipient-access events",
    )
    audit_subparsers = audit_parser.add_subparsers(dest="audit_command", required=True)
    audit_list_parser = audit_subparsers.add_parser("list")
    audit_list_parser.add_argument("--grant-id", default=None)
    audit_list_parser.add_argument("--limit", type=int, default=100)
    audit_list_parser.add_argument("--data-dir", type=Path, default=None)
    audit_list_parser.add_argument("--json", action="store_true")

    mcp_parser = subparsers.add_parser(
        "mcp",
        help="Run the recipient-only Prism MCP server",
    )
    mcp_subparsers = mcp_parser.add_subparsers(dest="mcp_command", required=True)
    mcp_check_parser = mcp_subparsers.add_parser(
        "check",
        help="Probe an MCP URL (local or tunnel) the way a connector would",
    )
    mcp_check_parser.add_argument("url")
    mcp_serve_parser = mcp_subparsers.add_parser(
        "serve",
        help="Expose the stateless OAuth-protected MCP endpoint on /mcp",
    )
    mcp_serve_parser.add_argument("--data-dir", type=Path, default=None)
    mcp_serve_parser.add_argument("--host", default=None)
    mcp_serve_parser.add_argument("--port", type=int, default=None)
    mcp_serve_parser.add_argument(
        "--public-url",
        default=None,
        help="Public HTTPS origin of this host (the tunnel URL); default is loopback",
    )
    mcp_serve_parser.add_argument(
        "--approve-here",
        action="store_true",
        help="Prompt in this terminal to approve or deny each recipient as they connect",
    )
    mcp_serve_parser.add_argument(
        "--tunnel",
        choices=("ngrok", "cloudflared"),
        default=None,
        help="Start a tunnel client and use its HTTPS URL as the public URL",
    )
    mcp_serve_parser.add_argument(
        "--tunnel-domain",
        default=None,
        help="Reserved ngrok domain to use instead of a random one",
    )
    mcp_serve_parser.add_argument(
        "--owner-label",
        default=None,
        help="Name shown to recipients as the sharer (default: PRISM_OWNER_LABEL)",
    )
    mcp_serve_parser.add_argument(
        "--allowed-host",
        action="append",
        default=None,
        help="Exact public Host header accepted from a tunnel; repeat as needed",
    )
    mcp_serve_parser.add_argument(
        "--allowed-origin",
        action="append",
        default=None,
        help="Exact browser Origin accepted when present; repeat as needed",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "inspect-session":
            return inspect_session(args.session_file, args.json)
        if args.command == "capture" and args.capture_command == "list":
            return capture_list(args.source, args.adapter, args.data_dir, args.json)
        if args.command == "capture" and args.capture_command == "import":
            return capture_import(
                args.source,
                args.adapter,
                args.conversation_ref,
                args.data_dir,
                args.json,
            )
        if args.command == "capture" and args.capture_command == "select":
            return capture_select(args.source, args.adapter, args.data_dir)
        if args.command == "shared-link" and args.shared_link_command == "review":
            return shared_link_review(args.data_dir, args.visible_input)
        if args.command == "shared-link" and args.shared_link_command == "resume":
            return shared_link_resume(args.import_id, args.data_dir)
        if args.command == "drafts" and args.drafts_command == "list":
            return drafts_list(args.data_dir, args.json)
        if args.command == "draft" and args.draft_command == "show":
            return draft_show(args.draft_id, args.data_dir, args.json)
        if args.command == "draft" and args.draft_command == "select":
            return draft_select(
                args.draft_id,
                args.turns,
                args.data_dir,
                args.json,
            )
        if args.command == "draft" and args.draft_command == "preview":
            return draft_preview(args.draft_id, args.data_dir, args.json)
        if args.command == "draft" and args.draft_command == "resource":
            if args.resource_command == "add":
                return draft_resource_add(
                    args.draft_id, args.file, args.name, args.data_dir, args.json
                )
            return draft_resource_remove(
                args.draft_id, args.attachment_id, args.data_dir, args.json
            )
        if args.command == "draft" and args.draft_command == "publish":
            exact = (args.expected_revision, args.expected_preview_hash)
            if args.yes or exact == (None, None):
                return draft_publish_confirm(
                    args.draft_id, args.yes, args.data_dir, args.json
                )
            if None in exact:
                raise ValidationError(
                    "Pass both --expected-revision and --expected-preview-hash, or use --yes"
                )
            return draft_publish(
                args.draft_id,
                args.expected_revision,
                args.expected_preview_hash,
                args.data_dir,
                args.json,
            )
        if args.command == "draft" and args.draft_command == "findings":
            return draft_findings(args.draft_id, args.data_dir, args.json)
        if args.command == "draft" and args.draft_command == "resolve":
            return draft_resolve(
                args.draft_id, args.finding_id, args.reason, args.data_dir, args.json
            )
        if args.command == "draft" and args.draft_command == "unresolve":
            return draft_unresolve(args.draft_id, args.finding_id, args.data_dir, args.json)
        if args.command == "draft" and args.draft_command == "title":
            if args.title is not None and args.clear:
                raise ValidationError("Pass either --set or --clear, not both")
            if args.title is None and not args.clear:
                raise ValidationError("Pass --set <title> or --clear")
            return draft_title(args.draft_id, args.title, args.clear, args.data_dir, args.json)
        if args.command == "draft" and args.draft_command == "provenance":
            return draft_provenance(args.draft_id, args.data_dir, args.json)
        if args.command == "draft" and args.draft_command == "taint":
            return draft_taint(
                args.draft_id, args.event_id, args.reason, args.data_dir, args.json
            )
        if args.command == "draft" and args.draft_command == "untaint":
            return draft_untaint(args.draft_id, args.event_id, args.data_dir, args.json)
        if args.command == "draft" and args.draft_command == "graph":
            return draft_graph(args.draft_id, args.data_dir, args.json)
        if args.command == "snapshots" and args.snapshots_command == "list":
            return snapshots_list(args.data_dir, args.json)
        if args.command == "snapshot" and args.snapshot_command == "show":
            return snapshot_show(args.snapshot_id, args.data_dir, args.json)
        if args.command == "snapshot" and args.snapshot_command == "receipt":
            return snapshot_receipt(args.snapshot_id, args.data_dir, args.json)
        if args.command == "snapshot" and args.snapshot_command == "revoke":
            return snapshot_revoke(
                args.snapshot_id,
                args.purge,
                args.data_dir,
                args.json,
            )
        if args.command == "shares" and args.shares_command == "list":
            return shares_list(args.data_dir, args.json)
        if args.command == "share" and args.share_command == "create":
            return share_create(args.snapshot_id, args.name, args.data_dir, args.json)
        if args.command == "share" and args.share_command == "add-version":
            return share_add_version(
                args.share_id,
                args.snapshot_id,
                args.data_dir,
                args.json,
            )
        if args.command == "share" and args.share_command == "show":
            return share_show(args.share_id, args.data_dir, args.json)
        if args.command == "share" and args.share_command == "revoke":
            return share_revoke(args.share_id, args.data_dir, args.json)
        if args.command == "share" and args.share_command == "revoke-version":
            return share_revoke_version(
                args.share_id,
                args.version,
                args.data_dir,
                args.json,
            )
        if args.command == "invitation" and args.invitation_command == "create":
            return invitation_create(
                args.share_id,
                args.version,
                args.expires_hours,
                args.grant_hours,
                args.data_dir,
                args.json,
                args.recipient_hint,
                not args.no_approval,
            )
        if args.command == "invitation" and args.invitation_command == "redeem":
            return invitation_redeem(args.token, args.data_dir, args.json)
        if args.command == "invitation" and args.invitation_command == "revoke":
            return invitation_revoke(args.invitation_id, args.data_dir, args.json)
        if args.command == "invitations" and args.invitations_command == "list":
            return invitations_list(args.data_dir, args.json)
        if args.command == "grant" and args.grant_command == "verify":
            return grant_verify(args.token, args.data_dir, args.json)
        if args.command == "grant" and args.grant_command == "approve":
            return grant_approve(args.grant_id, args.data_dir, args.json)
        if args.command == "grant" and args.grant_command == "deny":
            return grant_deny(args.grant_id, args.data_dir, args.json)
        if args.command == "quickshare":
            return quickshare(
                args.source,
                args.adapter,
                args.recipient_hint,
                args.data_dir,
                args.serve,
                args.tunnel,
                args.chatgpt_link,
                args.hidden_input,
                args.approve_here,
            )
        if args.command == "recipient":
            if args.recipient_command == "connect":
                return recipient_connect(
                    args.url,
                    args.state_dir,
                    args.headless,
                    args.name,
                    args.invitation,
                    args.no_browser,
                    args.json,
                )
            tool, arguments = {
                "manifest": ("prism_get_manifest", {}),
                "query": (
                    "prism_query_share",
                    {"query": getattr(args, "question", None), "max_results": getattr(args, "max_results", 5)},
                ),
                "message": (
                    "prism_read_message",
                    {"message_id": getattr(args, "message_id", None), "cursor": getattr(args, "cursor", 0)},
                ),
                "resource": (
                    "prism_read_resource",
                    {"resource_id": getattr(args, "resource_id", None), "cursor": getattr(args, "cursor", 0)},
                ),
            }[args.recipient_command]
            return recipient_call(tool, args.url, arguments, args.state_dir, args.json)
        if args.command == "audit" and args.audit_command == "list":
            return audit_list(args.grant_id, args.limit, args.data_dir, args.json)
        if args.command == "grant" and args.grant_command == "revoke":
            return grant_revoke(args.grant_id, args.data_dir, args.json)
        if args.command == "grants" and args.grants_command == "watch":
            return grants_watch(args.data_dir)
        if args.command == "grants" and args.grants_command == "list":
            return grants_list(args.data_dir, args.json)
        if args.command == "db" and args.database_command == "init":
            return database_init(args.data_dir)
        if args.command == "db" and args.database_command == "check":
            return database_check(args.data_dir)
        if args.command == "db" and args.database_command == "backup":
            return database_backup(args.data_dir)
        if args.command == "db" and args.database_command == "migrate-legacy":
            return database_migrate_legacy(
                args.data_dir,
                args.legacy_root,
                args.json,
            )
        if args.command == "ui":
            return ui_serve(args.data_dir, args.port)
        if args.command == "mcp" and args.mcp_command == "check":
            return mcp_check(args.url)
        if args.command == "mcp" and args.mcp_command == "serve":
            return mcp_serve(
                args.data_dir,
                args.host,
                args.port,
                args.public_url,
                args.allowed_host,
                args.allowed_origin,
                args.owner_label,
                args.tunnel,
                args.tunnel_domain,
                args.approve_here,
            )
    except PrismError as exc:
        if getattr(args, "json", False):
            print(json.dumps(exc.as_dict(), indent=2), file=sys.stderr)
        else:
            print(
                f"error [{exc.code}] ({exc.correlation_id}): {exc}",
                file=sys.stderr,
            )
        return exc.exit_code
    except PydanticValidationError:
        error = ValidationError("One or more command arguments failed validation")
        if getattr(args, "json", False):
            print(json.dumps(error.as_dict(), indent=2), file=sys.stderr)
        else:
            print(
                f"error [{error.code}] ({error.correlation_id}): {error}",
                file=sys.stderr,
            )
        return error.exit_code
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
