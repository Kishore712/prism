"""Phase 9: unify lexical, cue-phrase, and provenance signals into one graph.

Three different kinds of evidence that an included atom depends on something
the owner has flagged (excluded, or a tainted tool-read) are computed by
three different modules (`exclusion.py`, `dependency.py`, `taint.py`). This
module gives them one shared, typed shape and one query — "what points at
X?" — instead of three parallel, differently-structured checks. See
`docs/PROJECTION_LAYER_RESEARCH_2.md` §1.1 and
`docs/PROJECTION_LAYER_RESEARCH_3_PROVENANCE.md` §3.3 for why the confidence
levels are kept distinct rather than merged into one score.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..models.capture import CapturedSession
from .atoms import AtomSet, turn_index_map
from .dependency import matched_cue_phrases
from .exclusion import ExclusionVocabulary, _tokens
from .provenance import ToolReadEventRecord

EdgeKind = Literal["lexical", "cue_phrase", "provenance"]
Confidence = Literal["high", "medium", "low"]

_KIND_CONFIDENCE: dict[EdgeKind, Confidence] = {
    "provenance": "high",
    "lexical": "medium",
    "cue_phrase": "low",
}


@dataclass(frozen=True)
class DependencyEdge:
    """One piece of evidence that ``source_ref`` may depend on ``target_ref``.

    ``target_ref`` for a provenance edge is the tool-event id, prefixed
    (``event:<event_id>``), not a capture atom ref — the two ref spaces are
    kept visually distinct on purpose so a caller can't confuse "depends on
    an excluded turn" with "depends on a flagged tool read" by accident.
    """

    source_ref: str
    target_ref: str
    kind: EdgeKind
    confidence: Confidence
    evidence: str


def _event_ref(event_id: str) -> str:
    return f"event:{event_id}"


def build_lexical_edges(atoms: AtomSet) -> tuple[DependencyEdge, ...]:
    """One edge per (included atom, excluded atom) pair sharing a distinctive term.

    Reuses the same vocabulary Phase 8's exclusion-derived detector already
    builds, so this is additive bookkeeping, not a second implementation of
    the token-matching logic.
    """

    vocabulary = ExclusionVocabulary.build(atoms.excluded)
    if not vocabulary:
        return ()
    edges: list[DependencyEdge] = []
    seen: set[tuple[str, str]] = set()
    for atom in atoms.included:
        tokens = _tokens(atom.text) & vocabulary.terms
        for token in tokens:
            for target_ref in vocabulary.sources.get(token, ()):
                pair = (atom.ref, target_ref)
                if pair in seen:
                    continue
                seen.add(pair)
                edges.append(
                    DependencyEdge(
                        source_ref=atom.ref,
                        target_ref=target_ref,
                        kind="lexical",
                        confidence=_KIND_CONFIDENCE["lexical"],
                        evidence=f"shares the term '{token}' with excluded content",
                    )
                )
    edges.sort(key=lambda edge: (edge.source_ref, edge.target_ref))
    return tuple(edges)


def build_cue_phrase_edges(atoms: AtomSet) -> tuple[DependencyEdge, ...]:
    """One edge per included atom containing a backward-reference cue.

    The target is the *nearest preceding excluded atom*, a reasonable guess
    when one exists; with none, the atom is still flagged (target is the
    atom's own ref, i.e. a self-referential "review this" edge with no
    specific excluded target) so the cue is never silently dropped just
    because nothing excluded happens to be nearby.
    """

    edges: list[DependencyEdge] = []
    excluded_message_refs = [atom.ref for atom in atoms.excluded if atom.kind == "message"]
    for atom in atoms.included:
        matches = matched_cue_phrases(atom.text)
        if not matches:
            continue
        target = excluded_message_refs[-1] if excluded_message_refs else atom.ref
        edges.append(
            DependencyEdge(
                source_ref=atom.ref,
                target_ref=target,
                kind="cue_phrase",
                confidence=_KIND_CONFIDENCE["cue_phrase"],
                evidence=f"contains the backward-reference phrase '{matches[0]}'",
            )
        )
    edges.sort(key=lambda edge: edge.source_ref)
    return tuple(edges)


def build_provenance_edges(
    capture: CapturedSession,
    selected_turn_ids: tuple[str, ...],
    tool_events: tuple[ToolReadEventRecord, ...],
    tainted_event_ids: frozenset[str],
) -> tuple[DependencyEdge, ...]:
    """One edge per (included turn, tainted event) pair the turn comes after."""

    tainted = [event for event in tool_events if event.event_id in tainted_event_ids]
    if not tainted:
        return ()
    turn_index = turn_index_map(capture)
    edges: list[DependencyEdge] = []
    for turn_id in selected_turn_ids:
        index = turn_index.get(turn_id)
        if index is None:
            continue
        for event in tainted:
            if index < event.turn_index:
                continue
            edges.append(
                DependencyEdge(
                    source_ref=turn_id,
                    target_ref=_event_ref(event.event_id),
                    kind="provenance",
                    confidence=_KIND_CONFIDENCE["provenance"],
                    evidence=f"at or after the flagged {event.tool_name} call at turn {event.turn_index}",
                )
            )
    edges.sort(key=lambda edge: (edge.source_ref, edge.target_ref))
    return tuple(edges)


def cascading_candidates(
    edges: tuple[DependencyEdge, ...],
    flagged_refs: frozenset[str],
) -> tuple[DependencyEdge, ...]:
    """Every edge whose target is something the owner has flagged.

    A suggestion set for the owner to review — never applied automatically.
    """

    return tuple(edge for edge in edges if edge.target_ref in flagged_refs)
