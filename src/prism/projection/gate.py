"""Scan and the release gate: the trusted computing base for G1-G3.

``scan`` is the single place that decides what findings exist for a piece of
content. ``check_gate`` is the single place that decides whether publication
may proceed. Both are pure functions of their inputs, deliberately small, so
they can be called identically before publication (pre-check, findings the
owner reviews) and after building the final bytes (post-condition rescan,
defense in depth — see ``docs/PROJECTION_LAYER_PROPOSAL.md`` §4.6).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models.capture import CapturedSession
from ..models.projection import ProjectionAttachment
from .atoms import build_atoms
from .detectors import Finding, FindingSeverity, run_detectors
from .exclusion import ExclusionVocabulary, detect_exclusion_derived


def scan(
    capture: CapturedSession,
    selected_turn_ids: tuple[str, ...],
    attachments: tuple[ProjectionAttachment, ...],
    *,
    title_override: str | None = None,
) -> tuple[Finding, ...]:
    """Run every detector over the currently-selected content."""

    atoms = build_atoms(
        capture, selected_turn_ids, attachments, title_override=title_override
    )
    findings = list(run_detectors(atoms.included))
    vocabulary = ExclusionVocabulary.build(atoms.excluded)
    for atom in atoms.included:
        findings.extend(detect_exclusion_derived(atom, vocabulary))
    # Deterministic order: callers (CLI, tests) must not depend on dict/set
    # iteration order across runs.
    findings.sort(key=lambda item: (item.atom_kind, item.atom_ref, item.finding_id))
    return tuple(findings)


@dataclass(frozen=True)
class GateResult:
    ok: bool
    blocking: tuple[Finding, ...]  # BLOCK-severity findings with no decision
    overridden: tuple[Finding, ...]  # BLOCK-severity findings the owner allowed
    warnings: tuple[Finding, ...]  # WARN-severity findings (never block)


def check_gate(
    findings: tuple[Finding, ...],
    decided_finding_ids: frozenset[str],
) -> GateResult:
    """Decide whether publication may proceed.

    A BLOCK finding blocks publication unless its exact ``finding_id`` has an
    owner decision on file. WARN findings (currently just exclusion-derived)
    never block; they exist to be seen, not to be a gate.
    """

    blocking = tuple(
        finding
        for finding in findings
        if finding.severity is FindingSeverity.BLOCK
        and finding.finding_id not in decided_finding_ids
    )
    overridden = tuple(
        finding
        for finding in findings
        if finding.severity is FindingSeverity.BLOCK
        and finding.finding_id in decided_finding_ids
    )
    warnings = tuple(
        finding for finding in findings if finding.severity is FindingSeverity.WARN
    )
    return GateResult(
        ok=not blocking, blocking=blocking, overridden=overridden, warnings=warnings
    )
