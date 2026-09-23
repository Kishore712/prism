"""Process-local stores for temporary, owner-only prototype state."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from ..exceptions import NotFoundError, ValidationError
from ..models.capture import RemoteAdapterCapture, StagedCapture


class InMemoryCaptureCandidateStore:
    """Stages candidates without retaining source URLs or provider responses."""

    def __init__(
        self,
        ttl: timedelta = timedelta(minutes=15),
        max_candidates: int = 3,
    ) -> None:
        self._ttl = ttl
        self._max_candidates = max_candidates
        self._items: dict[str, StagedCapture] = {}

    def stage(
        self,
        adapter_version: str,
        candidate: RemoteAdapterCapture,
        preview_hash: str,
    ) -> StagedCapture:
        self._remove_expired()
        if len(self._items) >= self._max_candidates:
            raise ValidationError("Too many temporary capture candidates are active")
        import_id = f"imp_{uuid4().hex[:26]}"
        expires_at = datetime.now(timezone.utc) + self._ttl
        staged = StagedCapture(
            import_id=import_id,
            adapter_version=adapter_version,
            capture=candidate.capture,
            observations=candidate.observations,
            preview_hash=preview_hash,
            expires_at=expires_at.isoformat().replace("+00:00", "Z"),
        )
        self._items[import_id] = staged
        return staged

    def get(self, import_id: str) -> StagedCapture:
        self._remove_expired()
        try:
            return self._items[import_id]
        except KeyError as exc:
            raise NotFoundError("The temporary capture candidate is unavailable") from exc

    def delete(self, import_id: str) -> None:
        self._items.pop(import_id, None)

    def _remove_expired(self) -> None:
        now = datetime.now(timezone.utc)
        expired = [
            import_id
            for import_id, item in self._items.items()
            if datetime.fromisoformat(item.expires_at.replace("Z", "+00:00")) <= now
        ]
        for import_id in expired:
            del self._items[import_id]
