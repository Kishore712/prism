"""Phase 9, Tier 2: deterministic cue-phrase (anaphora/deixis) detection.

Low precision by construction — this is a heuristic nudge, not ground truth,
and it stays WARN-severity, never blocking, for the same reason
exclusion-derived was downgraded in Phase 8: measured false-positive rates
matter more than a plausible-sounding rule. See
`PROJECTION_LAYER_RESEARCH_2.md` §1.1 for the origin of this idea and
`tests/projection/test_dependency.py` for the actual measured precision and
recall against a small labeled corpus — a number, not an assumption.
"""

from __future__ import annotations

import re

from .canonicalize import canonicalize


# Phrases that, in isolation, suggest a sentence refers to something outside
# itself without repeating it — "backward reference" cues. Deliberately a
# fixed, auditable list rather than a learned model: every entry is here
# because it is a common way English signals "you already know what I mean",
# and every entry is a known source of false positives (see the corpus).
_CUE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bas (?:i|we) (?:mentioned|said|discussed|noted)\b",
        r"\b(?:like|as) (?:i|we) said\b",
        r"\bthe (?:one|thing|file|document|number|project|deal|person) "
        r"(?:i|we|you) (?:mentioned|discussed|meant|referred to)\b",
        r"\bgoing back to\b",
        r"\breferred to (?:above|earlier|before)\b",
        r"\bthe aforementioned\b",
        r"\b(?:earlier|before|previously) (?:i|we) (?:said|mentioned|noted|discussed)\b",
        r"\bas (?:noted|discussed|mentioned) (?:above|earlier|before)\b",
        r"\bthat (?:number|name|file|document|project|deal|person|detail|thing) "
        r"(?:i|we|you) (?:mentioned|gave|shared|discussed)\b",
    )
)


def has_cue_phrase(text: str) -> bool:
    """True if `text` contains a deterministic backward-reference cue.

    Callers should treat a hit as "worth a second look", never as proof.
    """

    clean = canonicalize(text)
    return any(pattern.search(clean) for pattern in _CUE_PATTERNS)


def matched_cue_phrases(text: str) -> tuple[str, ...]:
    """Every distinct cue phrase matched, for display (`prism draft graph`)."""

    clean = canonicalize(text)
    return tuple(
        match.group(0)
        for pattern in _CUE_PATTERNS
        if (match := pattern.search(clean)) is not None
    )
