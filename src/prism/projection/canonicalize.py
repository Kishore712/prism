"""Text canonicalization run before every detector.

Closes a real gap: NFKC normalization was applied to exclusion-derived
matching (`exclusion.py`) but never to the secret/PII/env-identifier
detectors, so a zero-width character inserted into a pasted key — a common
copy-paste artifact, not necessarily adversarial — silently defeated a
contiguous regex match. NFKC also folds compatibility-equivalent forms
(full-width Latin, ligatures, some typographic variants) to their plain
form, which the raw text would otherwise miss.

**What this does not do**, stated precisely rather than implied: NFKC does
not fold cross-script confusables (a Cyrillic "а", U+0430, next to a Latin
"a", U+0061, are different code points NFKC treats as already-distinct
characters). Defeating that needs Unicode confusables-skeleton matching
(UTS #39), which is not implemented here and is tracked as a known gap, not
silently assumed solved. See `PROJECTION_LAYER_RESEARCH_2.md` §1.2 for the
threat-model reasoning this scope decision follows: the priority is
accidental obfuscation (real, common) over adversarial evasion (a
disproportionate threat for content the owner reviews on their own machine
before anyone else sees it).

Detectors must scan `canonicalize(atom.text)`, not `atom.text` directly.
"""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata


# Zero-width and other invisible-but-not-whitespace characters seen in
# copy-pasted web/document text. Stripped, not just normalized, because they
# carry no visible meaning and only ever break contiguous matches.
_INVISIBLE = re.compile(
    "["
    "​‌‍‎‏"  # zero-width space/joiners/marks
    "⁠﻿"  # word joiner, BOM
    "­"  # soft hyphen
    "]"
)

# A run of base64-alphabet characters long enough to plausibly encode a
# secret. Decoded and appended (not substituted) so detectors see both the
# original text and its decoded form in one pass.
_BASE64_RUN = re.compile(r"[A-Za-z0-9+/]{24,}={0,2}")
_HEX_RUN = re.compile(r"\b[0-9a-fA-F]{32,}\b")


def canonicalize(text: str) -> str:
    """Normalize Unicode and strip invisible characters. Cheap, always applied."""

    normalized = unicodedata.normalize("NFKC", text)
    return _INVISIBLE.sub("", normalized)


def decode_and_rescan_text(text: str) -> str:
    """Return `text` with any plausible base64/hex runs' decoded form appended.

    A secret pasted "for safety" as base64, or copied from a hex-encoded log
    line, will not match a plain-text pattern. This does not replace the
    original text (both forms are scanned) and never mutates what gets
    published — it is purely a wider view for detectors.
    """

    extra: list[str] = []
    for match in _BASE64_RUN.finditer(text):
        candidate = match.group(0)
        try:
            decoded = base64.b64decode(candidate, validate=True)
        except (binascii.Error, ValueError):
            continue
        try:
            extra.append(decoded.decode("utf-8"))
        except UnicodeDecodeError:
            continue
    for match in _HEX_RUN.finditer(text):
        candidate = match.group(0)
        if len(candidate) % 2 != 0:
            continue
        try:
            decoded = bytes.fromhex(candidate)
        except ValueError:
            continue
        try:
            extra.append(decoded.decode("utf-8"))
        except UnicodeDecodeError:
            continue
    if not extra:
        return text
    return text + "\n" + "\n".join(extra)


def prepare_for_scanning(text: str) -> str:
    """The single entry point detectors call: canonicalize, then widen."""

    return decode_and_rescan_text(canonicalize(text))
