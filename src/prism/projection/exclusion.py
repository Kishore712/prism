"""Exclusion-derived leak detection.

The owner's own deselection is the strongest available signal about what
they consider sensitive: this module builds a vocabulary from everything the
owner *excluded* (unselected turns, and the original title if the owner
overrode it) and flags any of those distinctive terms reappearing in the
content that *is* about to be published. See
``docs/PROJECTION_LAYER_PROPOSAL.md`` §4.3.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .canonicalize import canonicalize
from .detectors import Atom, Finding, FindingClass, FindingSeverity, _excerpt, _finding_id


_WORD = re.compile(r"[^\W_]+", re.UNICODE)

# A deliberately larger stopword list than the recipient-side retrieval one:
# false positives here would train owners to ignore every warning, so common
# words must not qualify as "distinctive".
_STOPWORDS = frozenset(
    """a about after again all also am an and any are as at be been before
    being below between both but by can could did do does doing down during
    each few for from further had has have having he her here hers herself
    him himself his how i if in into is it its itself just me more most my
    myself no nor not now of off on once only or other our ours ourselves
    out over own same she should so some such than that the their theirs
    them themselves then there these they this those through to too under
    until up very was we were what when where which while who whom why will
    with would you your yours yourself yourselves""".split()
)

_MIN_TOKEN_LENGTH = 5


def _tokens(text: str) -> set[str]:
    normalized = canonicalize(text).casefold()
    return {
        token
        for token in _WORD.findall(normalized)
        if len(token) >= _MIN_TOKEN_LENGTH and token not in _STOPWORDS
    }


@dataclass(frozen=True)
class ExclusionVocabulary:
    """Distinctive terms from everything the owner chose not to share."""

    terms: frozenset[str]
    sources: dict[str, tuple[str, ...]]  # term -> which excluded atom refs it came from

    @classmethod
    def build(cls, excluded_atoms: list[Atom]) -> "ExclusionVocabulary":
        sources: dict[str, list[str]] = {}
        for atom in excluded_atoms:
            for token in _tokens(atom.text):
                sources.setdefault(token, []).append(atom.ref)
        return cls(
            terms=frozenset(sources),
            sources={term: tuple(refs) for term, refs in sources.items()},
        )

    def __bool__(self) -> bool:
        return bool(self.terms)


def detect_exclusion_derived(
    atom: Atom,
    vocabulary: ExclusionVocabulary,
) -> list[Finding]:
    """One finding per (atom, distinctive term) — not per raw occurrence.

    A long included message can legitimately repeat the same offending
    term many times (a section heading and body text both saying
    "research", say); reporting each occurrence separately doesn't add
    information, it just buries genuinely distinct terms — including rare,
    highly specific ones like a name — under noise from common ones. The
    first occurrence's excerpt is representative enough to review; the
    owner can search the source text themselves for the rest.
    """

    if not vocabulary:
        return []
    findings: list[Finding] = []
    seen_tokens: set[str] = set()
    normalized = canonicalize(atom.text).casefold()
    for match in _WORD.finditer(normalized):
        token = match.group(0)
        if len(token) < _MIN_TOKEN_LENGTH or token not in vocabulary.terms:
            continue
        if token in seen_tokens:
            continue
        seen_tokens.add(token)
        source_refs = vocabulary.sources.get(token, ())
        findings.append(
            Finding(
                finding_id=_finding_id("exclusion-derived", FindingClass.EXCLUSION_DERIVED, token),
                atom_kind=atom.kind,
                atom_ref=atom.ref,
                detector="exclusion-derived",
                detector_version="1.0",
                finding_class=FindingClass.EXCLUSION_DERIVED,
                severity=FindingSeverity.WARN,
                excerpt=_excerpt(atom.text, match.start(), match.end()),
                detail=(
                    f"'{token}' also appears in excluded content ("
                    + ", ".join(source_refs[:3])
                    + (", …" if len(source_refs) > 3 else "")
                    + ") — may echo something you chose not to share"
                ),
            )
        )
    return findings
