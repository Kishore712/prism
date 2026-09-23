"""Sealed Projection: the leak-defense layer around what gets published.

See ``docs/PROJECTION_LAYER_PROPOSAL.md`` for the design. This package is
additive to the existing allowlist projection (``services/projection.py``):
it never decides *what* the owner selected, only whether the selected content
is safe to release, given deterministic detectors and the owner's own
deselections as a signal.
"""

from .detectors import Finding, FindingClass, FindingSeverity, run_detectors
from .gate import GateResult, check_gate, scan
from .receipt import ReceiptKeyPair, sign_receipt, verify_receipt


# Every detector's ``detector_version`` string, recorded in receipts so a
# future rescan can tell whether it used the same rules.
DETECTOR_VERSIONS = {
    "secret": "1.0",
    "url-secret": "1.0",
    "pii-direct": "1.0",
    "env-identifier": "1.0",
    "exclusion-derived": "1.0",
}

__all__ = [
    "DETECTOR_VERSIONS",
    "Finding",
    "FindingClass",
    "FindingSeverity",
    "GateResult",
    "ReceiptKeyPair",
    "check_gate",
    "run_detectors",
    "scan",
    "sign_receipt",
    "verify_receipt",
]
