"""Synthetic sources used for local development and deterministic tests."""

from .synthetic_session import SyntheticSessionSource
from .synthetic_capture import SyntheticSessionCaptureAdapter

__all__ = ["SyntheticSessionCaptureAdapter", "SyntheticSessionSource"]
