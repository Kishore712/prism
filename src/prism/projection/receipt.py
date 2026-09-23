"""Signed projection receipts.

A receipt answers, offline and without exposing content: *what exactly
produced this snapshot, and did the owner override any blocking finding?*
See ``docs/PROJECTION_LAYER_PROPOSAL.md`` §4.6. Ed25519 (via ``cryptography``,
already a dependency) rather than a bespoke scheme; the key lives in a
``0600`` file under the owner's data directory and is generated on first use.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from ..models.projection import SnapshotReceipt


_KEY_FILE_NAME = "owner_ed25519.key"


class ReceiptKeyPair:
    """The owner's signing key, loaded from or created in a data directory."""

    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        self._private_key = private_key

    @classmethod
    def load_or_create(cls, data_dir: Path) -> "ReceiptKeyPair":
        data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(data_dir, 0o700)
        path = data_dir / _KEY_FILE_NAME
        if path.exists():
            raw = path.read_bytes()
            return cls(Ed25519PrivateKey.from_private_bytes(raw))
        private_key = Ed25519PrivateKey.generate()
        raw = private_key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
        os.chmod(path, 0o600)
        return cls(private_key)

    @property
    def public_key_hex(self) -> str:
        return self._private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        ).hex()

    def sign(self, payload: bytes) -> bytes:
        return self._private_key.sign(payload)


def _canonical_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def sign_receipt(
    keypair: ReceiptKeyPair,
    *,
    capture_hash: str,
    snapshot_hash: str,
    detector_versions: dict[str, str],
    finding_count: int,
    override_count: int,
    created_at: str,
) -> SnapshotReceipt:
    payload = {
        "capture_hash": capture_hash,
        "snapshot_hash": snapshot_hash,
        "detector_versions": detector_versions,
        "finding_count": finding_count,
        "override_count": override_count,
        "created_at": created_at,
    }
    canonical = _canonical_bytes(payload)
    signature = keypair.sign(canonical)
    return SnapshotReceipt(
        capture_hash=capture_hash,
        snapshot_hash=snapshot_hash,
        detector_versions=detector_versions,
        finding_count=finding_count,
        override_count=override_count,
        created_at=created_at,
        public_key_hex=keypair.public_key_hex,
        signature_hex=signature.hex(),
    )


def verify_receipt(receipt: SnapshotReceipt) -> bool:
    """Verify a receipt against its own embedded public key.

    This proves internal consistency (the signature matches the claimed
    fields and key) — it does not, by itself, prove the key belongs to a
    particular owner. Pin the public key out of band for that.
    """

    payload = {
        "capture_hash": receipt.capture_hash,
        "snapshot_hash": receipt.snapshot_hash,
        "detector_versions": receipt.detector_versions,
        "finding_count": receipt.finding_count,
        "override_count": receipt.override_count,
        "created_at": receipt.created_at,
    }
    try:
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(receipt.public_key_hex))
        public_key.verify(bytes.fromhex(receipt.signature_hex), _canonical_bytes(payload))
    except (InvalidSignature, ValueError):
        return False
    return True
