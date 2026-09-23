"""Ground-truth tool-call provenance (Phase 9, Tier 1).

An agent transcript that includes tool calls (Claude Code's JSONL format
does) already records, precisely, which file a `Read`/`Write`/`Edit` touched
and at which point in the conversation. Prism's capture adapters otherwise
discard this entirely, by design — raw tool I/O must never enter the
pipeline that can become shared content. This module is deliberately kept
structurally separate from that pipeline: `ToolReadEvent` never appears in
`AdapterCapture`, `CapturedSession`, or `SnapshotContent`, and nothing here
is reachable from the recipient-facing code paths. It exists only so the
owner can review, at capture time, what the agent touched, and mark a
specific read as a mistake — see `taint.py` for what happens after that.

See `docs/PROJECTION_LAYER_RESEARCH_3_PROVENANCE.md` for the design
rationale and `docs/phases/phase-09-provenance-and-dependency-graph.md` for
what's implemented.
"""

from __future__ import annotations

from pydantic import Field

from ..models.capture import Identifier, ShortText, StrictModel


class ToolReadEvent(StrictModel):
    """One tool call, as extracted directly from a source transcript.

    Ephemeral: produced by a capture adapter's ``provenance()`` method,
    persisted once at capture time (see ``repositories/provenance.py``), and
    never re-derived from adapter output after that. ``resource_label`` is
    owner-sensitive (typically a filesystem path) and must only ever be
    shown in owner-facing, loopback-only surfaces.
    """

    turn_index: int = Field(ge=1, le=100_000)
    tool_name: ShortText
    resource_label: ShortText


class ToolReadEventRecord(StrictModel):
    """A persisted ``ToolReadEvent``, with the identifiers assigned at storage."""

    event_id: Identifier
    capture_id: Identifier
    turn_index: int = Field(ge=1, le=100_000)
    tool_name: ShortText
    resource_label: ShortText
    created_at: str = Field(min_length=20, max_length=40)


class TaintedEventRecord(StrictModel):
    """The owner's decision that one tool-read event was a mistake."""

    draft_id: Identifier
    event_id: Identifier
    reason: ShortText
    tainted_at: str = Field(min_length=20, max_length=40)
