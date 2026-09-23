"""Deterministic, offline detectors over projection atoms.

No detector calls a network service or a model. Each returns zero or more
:class:`Finding` objects. Detectors are intentionally conservative pattern
matchers, not a promise of complete coverage — see
``docs/PROJECTION_LAYER_PROPOSAL.md`` §1 for why recall is bounded and why
the owner's own exclusions (``exclusion-derived``) are treated as the
strongest signal available, not the regex detectors.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum

from pydantic import Field

from ..models.capture import Identifier, ShortText, StrictModel


class FindingClass(str, Enum):
    SECRET = "secret"
    URL_SECRET = "url-secret"
    PII_DIRECT = "pii-direct"
    ENV_IDENTIFIER = "env-identifier"
    EXCLUSION_DERIVED = "exclusion-derived"
    # Phase 9, Tier 1 — see projection/taint.py. Constructed directly there,
    # not via a regex detector in this module, but declared here so every
    # finding class and its default severity lives in one place.
    PROVENANCE_DERIVED = "provenance-derived"


class FindingSeverity(str, Enum):
    BLOCK = "block"
    WARN = "warn"


# Deliberately stricter than the proposal's table for classes I4 (span
# redaction) does not yet implement: PII and environment identifiers default
# to BLOCK, not "redact", until there is a redaction mechanism to fall back
# to. Only the owner's explicit "allow" decision lets them through.
_DEFAULT_SEVERITY = {
    FindingClass.SECRET: FindingSeverity.BLOCK,
    FindingClass.URL_SECRET: FindingSeverity.BLOCK,
    FindingClass.PII_DIRECT: FindingSeverity.BLOCK,
    FindingClass.ENV_IDENTIFIER: FindingSeverity.BLOCK,
    FindingClass.EXCLUSION_DERIVED: FindingSeverity.WARN,
    # Ground truth ("this turn came after a flagged read"), not a guess —
    # see the module docstring in taint.py for why this is BLOCK.
    FindingClass.PROVENANCE_DERIVED: FindingSeverity.BLOCK,
}


class Finding(StrictModel):
    """One detector hit against one atom of the current selection.

    ``finding_id`` is a deterministic fingerprint of (atom, detector, class,
    normalized match) — not a random id — so an owner decision recorded
    against it survives re-scanning the same content across drafts and
    revisions, without persisting the finding itself.
    """

    finding_id: Identifier
    atom_kind: str = Field(pattern=r"^(title|message|resource)$")
    atom_ref: Identifier
    detector: ShortText
    detector_version: ShortText
    finding_class: FindingClass
    severity: FindingSeverity
    excerpt: str = Field(min_length=1, max_length=200)
    detail: str = Field(min_length=1, max_length=300)


@dataclass(frozen=True)
class Atom:
    """One piece of currently-included content to scan."""

    kind: str  # "title" | "message" | "resource"
    ref: str  # turn_id / message_id / attachment_id, or "title"
    text: str


def _finding_id(detector: str, finding_class: FindingClass, match: str) -> str:
    """Fingerprint a finding by its content, not by which atom held it.

    Deliberately excludes atom kind/ref: the same secret or PII string can
    legitimately land in different atom shapes between the pre-publish scan
    (whole-turn atoms) and the post-condition rescan (per-message atoms
    built from the final snapshot), and an owner's "allow" decision must
    transfer across that difference — see ``services/publication.py``'s
    pre-check/post-condition pair. The tradeoff: two *different* locations
    containing the identical match collapse to one finding, shown once.
    """

    digest = hashlib.sha256(
        b"PrismFinding-v2\0"
        + detector.encode()
        + b"\0"
        + finding_class.value.encode()
        + b"\0"
        + match.casefold().encode("utf-8", "ignore")
    ).hexdigest()[:26]
    return f"fnd_{digest}"


def _excerpt(text: str, start: int, end: int, *, radius: int = 24) -> str:
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    snippet = text[lo:hi]
    if lo > 0:
        snippet = "…" + snippet
    if hi < len(text):
        snippet = snippet + "…"
    return snippet[:200]


def _make(
    atom: Atom,
    detector: str,
    version: str,
    finding_class: FindingClass,
    match: re.Match[str],
    detail: str,
    *,
    severity: FindingSeverity | None = None,
) -> Finding:
    return Finding(
        finding_id=_finding_id(detector, finding_class, match.group(0)),
        atom_kind=atom.kind,
        atom_ref=atom.ref,
        detector=detector,
        detector_version=version,
        finding_class=finding_class,
        severity=severity or _DEFAULT_SEVERITY[finding_class],
        excerpt=_excerpt(atom.text, match.start(), match.end()),
        detail=detail,
    )


# --- secret ------------------------------------------------------------

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key_id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack_token", re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,48}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("stripe_key", re.compile(r"\bsk_(?:live|test)_[0-9A-Za-z]{24,}\b")),
    (
        "private_key_block",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----"),
    ),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    (
        "keyword_assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret|password|passwd|access[_-]?token|"
            r"auth[_-]?token)\b\s*[:=]\s*['\"]?[A-Za-z0-9_\-/+]{12,}['\"]?"
        ),
    ),
)


def detect_secrets(atom: Atom) -> list[Finding]:
    findings = []
    for name, pattern in _SECRET_PATTERNS:
        for match in pattern.finditer(atom.text):
            findings.append(
                _make(
                    atom, f"secret:{name}", "1.0", FindingClass.SECRET, match,
                    f"Matches the {name} pattern",
                )
            )
    return findings


# --- url-secret -----------------------------------------------------------

_URL_CREDENTIAL = re.compile(r"[a-z][a-z0-9+.\-]*://[^/\s]+:[^/\s@]+@[^\s]+", re.IGNORECASE)
_URL_TOKEN_PARAM = re.compile(
    r"[?&](?:token|access_token|api_key|apikey|signature|auth)=[^&\s]{8,}",
    re.IGNORECASE,
)


def detect_url_secrets(atom: Atom) -> list[Finding]:
    findings = []
    for match in _URL_CREDENTIAL.finditer(atom.text):
        findings.append(
            _make(atom, "url-secret:credential", "1.0", FindingClass.URL_SECRET, match,
                  "URL contains embedded credentials")
        )
    for match in _URL_TOKEN_PARAM.finditer(atom.text):
        findings.append(
            _make(atom, "url-secret:token-param", "1.0", FindingClass.URL_SECRET, match,
                  "URL query string carries a token/key/signature parameter")
        )
    return findings


# --- pii-direct -----------------------------------------------------------

_EMAIL = re.compile(r"\b[\w.+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+\b", re.IGNORECASE)
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d\-.\s()]{8,}\d)(?!\w)")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
_CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        digit = int(char)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def detect_pii(atom: Atom) -> list[Finding]:
    findings = []
    for match in _EMAIL.finditer(atom.text):
        findings.append(
            _make(atom, "pii:email", "1.0", FindingClass.PII_DIRECT, match, "Email address")
        )
    for match in _PHONE.finditer(atom.text):
        digits = re.sub(r"\D", "", match.group(0))
        if 8 <= len(digits) <= 15:
            findings.append(
                _make(atom, "pii:phone", "1.0", FindingClass.PII_DIRECT, match,
                      "Phone-number-shaped sequence")
            )
    for match in _IBAN.finditer(atom.text):
        findings.append(
            _make(atom, "pii:iban", "1.0", FindingClass.PII_DIRECT, match, "IBAN-shaped sequence")
        )
    for match in _CARD_CANDIDATE.finditer(atom.text):
        digits = re.sub(r"[ -]", "", match.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            findings.append(
                _make(atom, "pii:card", "1.0", FindingClass.PII_DIRECT, match,
                      "Passes the Luhn check for a payment card number")
            )
    return findings


# --- env-identifier ------------------------------------------------------

_HOME_DIR = re.compile(r"(?:/Users/|/home/|C:\\Users\\)[A-Za-z0-9._\-]{2,64}")
_PRIVATE_IP = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3})\b"
)


def detect_env_identifiers(atom: Atom) -> list[Finding]:
    findings = []
    for match in _HOME_DIR.finditer(atom.text):
        findings.append(
            _make(atom, "env:home_dir", "1.0", FindingClass.ENV_IDENTIFIER, match,
                  "Home-directory path reveals a local username")
        )
    for match in _PRIVATE_IP.finditer(atom.text):
        findings.append(
            _make(atom, "env:private_ip", "1.0", FindingClass.ENV_IDENTIFIER, match,
                  "Private/internal IP address")
        )
    return findings


DETECTORS = (detect_secrets, detect_url_secrets, detect_pii, detect_env_identifiers)


def run_detectors(atoms: list[Atom]) -> tuple[Finding, ...]:
    """Run every regex detector over every included atom.

    Every atom is canonicalized (NFKC, invisible-character stripped, with a
    decoded base64/hex pass appended for the secret detector) before
    matching — see ``canonicalize.py``. A match against a documented
    vendor/RFC placeholder (``denylist.py``) never raises a finding.

    Does not include ``exclusion-derived`` findings — those need the
    excluded atoms too and are computed by :func:`detect_exclusion_derived`.
    """

    from .canonicalize import canonicalize, decode_and_rescan_text
    from .denylist import is_known_benign

    findings: list[Finding] = []
    seen: set[str] = set()
    for atom in atoms:
        clean = canonicalize(atom.text)
        widened = Atom(kind=atom.kind, ref=atom.ref, text=decode_and_rescan_text(clean))
        for detector in DETECTORS:
            for finding in detector(widened):
                if is_known_benign(finding.finding_class.value, finding.excerpt):
                    continue
                if finding.finding_id not in seen:
                    seen.add(finding.finding_id)
                    findings.append(finding)
    return tuple(findings)
