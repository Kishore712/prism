"""Known-benign patterns that must never raise a finding, scoped per class.

Directly motivated by a real false positive hit while building Phase 8:
`AKIAIOSFODNN7EXAMPLE` — AWS's own documented placeholder access key ID,
published in AWS's IAM user guide precisely so it can appear in technical
writing without being a real credential — tripped the `secret` detector.
Vendors publish these placeholders specifically to be used in examples;
treating every occurrence as a leak trains the owner to stop reading
findings at all (see `PROJECTION_LAYER_RESEARCH_2.md` §1.3 on warning
fatigue). Checked case-insensitively, exact-substring, before a finding is
raised — never after, so it costs nothing extra when it doesn't match.

Entries are scoped to the finding class they are actually a placeholder
for. `@example.com` is a reserved placeholder *email domain* (RFC 2606) —
it says nothing about whether a URL that happens to point at example.com
carries an embedded credential, which is a `url-secret` finding about the
credential, not the domain. An early version of this list applied every
entry globally and silently suppressed real `url-secret` findings whose
target happened to be example.com; that bug is why entries are class-scoped
now, not global substrings.

This list is intentionally short and citation-backed, not a general
false-positive suppressor: an entry here means a specific vendor or RFC
publishes this exact string as a non-secret placeholder, not "this looks
like it's probably fine."
"""

from __future__ import annotations

# class -> (pattern, source). `None` as the class means "any class" — used
# only for identifiers unambiguous enough to be safe everywhere (a specific
# vendor-documented key, not a generic domain or number range).
_KNOWN_BENIGN: tuple[tuple[str | None, str, str], ...] = (
    # AWS's own documented example access key (IAM User Guide) — unambiguous,
    # safe across classes.
    (None, "akiaiosfodnn7example", "AWS IAM User Guide placeholder credential"),
    (None, "wjalrxutnfemi/k7mdeng/bpxrficyexamplekey", "AWS IAM User Guide placeholder secret key"),
    # Stripe's published test-mode example key (Stripe API docs).
    (None, "sk_test_4ec39hqlyjwdarjtt1zdp7dc", "Stripe API docs example test key"),
    # RFC 2606 reserved example domains — a placeholder *email*, not a
    # statement about any other finding class that merely mentions the domain.
    ("pii-direct", "@example.com", "RFC 2606 reserved example domain"),
    ("pii-direct", "@example.org", "RFC 2606 reserved example domain"),
    ("pii-direct", "@example.net", "RFC 2606 reserved example domain"),
    # RFC 3092 canonical placeholder terms, as email local parts.
    ("pii-direct", "foo@example", "RFC 3092 metasyntactic placeholder"),
    ("pii-direct", "bar@example", "RFC 3092 metasyntactic placeholder"),
    # Fictional NANP numbers reserved for film/TV/documentation use
    # (North American Numbering Plan, 555-01XX block) — only meaningful for
    # the phone detector.
    *(
        ("pii-direct", f"555-01{n:02d}", "NANP reserved fictional phone number")
        for n in range(100)
    ),
)


def _entries_for(finding_class: str) -> tuple[tuple[str, str], ...]:
    return tuple(
        (pattern, source)
        for scope, pattern, source in _KNOWN_BENIGN
        if scope is None or scope == finding_class
    )


def is_known_benign(finding_class: str, matched_text: str) -> bool:
    """True if the exact matched text is a documented placeholder for this class."""

    folded = matched_text.casefold()
    return any(pattern in folded for pattern, _ in _entries_for(finding_class))


def benign_reason(finding_class: str, matched_text: str) -> str | None:
    folded = matched_text.casefold()
    for pattern, source in _entries_for(finding_class):
        if pattern in folded:
            return source
    return None
