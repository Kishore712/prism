"""Optional capability: a capture adapter that can report tool-call provenance.

Structural (``Protocol``), not required by ``SourceCaptureAdapter`` — most
adapters have no tool-call log to read (a ChatGPT export or shared link has
no such concept) and simply do not implement this. Callers detect support
with ``isinstance(adapter, ProvenanceCaptureAdapter)`` and treat its absence
as "no provenance available for this source", never as an error.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models.capture import CaptureSelection, CaptureSource
from ..projection.provenance import ToolReadEvent


@runtime_checkable
class ProvenanceCaptureAdapter(Protocol):
    """A ``SourceCaptureAdapter`` that can also report tool-call events."""

    def provenance(
        self,
        source: CaptureSource,
        selection: CaptureSelection,
    ) -> tuple[ToolReadEvent, ...]: ...
