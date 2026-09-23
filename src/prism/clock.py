"""Shared UTC clock formatting for persisted Prism timestamps."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def utc_after(seconds: int) -> str:
    """Return a persisted-format UTC timestamp ``seconds`` from now."""

    return (
        (datetime.now(timezone.utc) + timedelta(seconds=seconds))
        .isoformat()
        .replace("+00:00", "Z")
    )


def to_epoch(value: str) -> int:
    """Convert a persisted timestamp to integer epoch seconds."""

    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
