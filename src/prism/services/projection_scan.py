"""Shared scan orchestration: content findings plus Phase 9 provenance findings.

``DraftService.scan()``/``gate()`` and ``PublicationService.publish()``'s
pre-check both need the same thing — the content-based findings from
``projection.scan()`` combined with the Tier 1 provenance-derived findings
for whatever tool-call events the owner has tainted on this draft. Before
this module existed that "fetch tool_events + tainted ids + merge findings"
sequence was duplicated between the two services; this gives it one place.

Deliberately *not* used by ``PublicationService._post_condition_check``: that
rescan re-derives findings straight from the built snapshot's bytes, and
provenance-derived findings are not a property of those bytes — they follow
from which turns are selected and which tool-call events the owner tainted,
both of which are already verified unchanged via the preview-hash check
before the post-condition rescan runs. See
``docs/PROJECTION_LAYER_RESEARCH_3_PROVENANCE.md`` §3.3 and
``docs/phases/phase-09-provenance-and-dependency-graph.md``.
"""

from __future__ import annotations

from ..database import PrismUnitOfWork
from ..models.capture import CapturedSession
from ..models.draft import DraftState
from ..models.projection import ProjectionAttachment
from ..projection import Finding, scan
from ..projection.taint import provenance_derived_findings


def scan_draft(
    unit: PrismUnitOfWork,
    draft: DraftState,
    capture: CapturedSession,
    attachments: tuple[ProjectionAttachment, ...],
) -> tuple[Finding, ...]:
    """Content findings plus Tier 1 provenance findings, in one deterministic order.

    Takes an already-open unit of work and already-loaded draft/capture/
    attachments so callers keep control of their own transaction boundary
    (both existing call sites read several other things in the same
    transaction); this function only adds the two provenance reads and the
    merge.
    """

    content_findings = scan(
        capture,
        draft.selected_turn_ids,
        attachments,
        title_override=draft.title_override,
    )
    tool_events = unit.tool_events.list_for_capture(draft.capture_id)
    if not tool_events:
        return content_findings
    tainted_ids = unit.tainted_events.tainted_event_ids(draft.draft_id)
    if not tainted_ids:
        return content_findings
    provenance_findings = provenance_derived_findings(
        capture, draft.selected_turn_ids, tool_events, tainted_ids
    )
    if not provenance_findings:
        return content_findings
    merged = list(content_findings) + list(provenance_findings)
    merged.sort(key=lambda item: (item.atom_kind, item.atom_ref, item.finding_id))
    return tuple(merged)
