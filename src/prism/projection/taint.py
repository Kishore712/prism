"""Phase 9, Tier 1: turn provenance taint decisions into BLOCK findings.

This is the ground-truth tier: once the owner has flagged a specific
tool-call event as a mistake, every currently-included turn from that point
forward in the same capture is a fact, not a guess — "this turn came after a
read you yourself said was sensitive." It is fed through the identical
Phase 8 decision ledger and release gate as a regex-based finding; the only
judgment call left to the owner is whether the content actually needs
review, the same kind of decision the ledger already asks for on a `secret`
finding.

Deliberately excludes the finding's own excerpt from the tainted resource
label: the file path or command that was tainted is not repeated inside the
finding (which the owner already knows, from `draft provenance`) — the
finding's `detail` names the tool and turn, not the resource, to avoid
duplicating owner-sensitive content across more places than necessary.
"""

from __future__ import annotations

import hashlib

from ..models.capture import CapturedSession
from .atoms import turn_index_map
from .detectors import Finding, FindingClass, FindingSeverity
from .provenance import ToolReadEventRecord


def _finding_id(event_id: str, turn_id: str) -> str:
    digest = hashlib.sha256(
        b"PrismProvenanceFinding-v1\0" + event_id.encode() + b"\0" + turn_id.encode()
    ).hexdigest()[:26]
    return f"fnd_{digest}"


def provenance_derived_findings(
    capture: CapturedSession,
    selected_turn_ids: tuple[str, ...],
    tool_events: tuple[ToolReadEventRecord, ...],
    tainted_event_ids: frozenset[str],
) -> tuple[Finding, ...]:
    """BLOCK finding for every included turn at or after a tainted read.

    When more than one event is tainted, each contributes independently —
    a turn can be flagged by more than one event, and each contributes a
    distinct, separately resolvable finding (fingerprinted by
    ``(event_id, turn_id)``), so allowing one tainted source through does not
    silently clear turns that also depend on a different one.
    """

    tainted = [event for event in tool_events if event.event_id in tainted_event_ids]
    if not tainted:
        return ()
    turn_index = turn_index_map(capture)
    findings: list[Finding] = []
    for turn_id in selected_turn_ids:
        index = turn_index.get(turn_id)
        if index is None:
            continue
        for event in tainted:
            if index < event.turn_index:
                continue
            findings.append(
                Finding(
                    finding_id=_finding_id(event.event_id, turn_id),
                    atom_kind="message",
                    atom_ref=turn_id,
                    detector="provenance:chronological",
                    detector_version="1.0",
                    finding_class=FindingClass.PROVENANCE_DERIVED,
                    severity=FindingSeverity.BLOCK,
                    excerpt=f"turn {index} (at or after the flagged {event.tool_name} call at turn {event.turn_index})",
                    detail=(
                        f"This turn comes at or after a {event.tool_name} call the owner "
                        f"flagged as a mistake (turn {event.turn_index}); it may restate "
                        "what was read, even without repeating it verbatim."
                    ),
                )
            )
    findings.sort(key=lambda item: (item.atom_ref, item.finding_id))
    return tuple(findings)
