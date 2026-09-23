"""Opaque capability generation and domain-separated hashing."""

from __future__ import annotations

import hashlib
import secrets


def capability_hash(kind: str, value: str) -> str:
    """Hash one secret in a namespace that cannot collide with another kind."""

    return "sha256:" + hashlib.sha256(
        f"Prism{kind}Token-v1\0".encode("ascii") + value.encode("utf-8")
    ).hexdigest()


def new_capability(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def is_capability(value: str, prefix: str) -> bool:
    return 32 <= len(value) <= 200 and value.startswith(f"{prefix}_")
