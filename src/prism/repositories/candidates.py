"""Durable normalized candidate repository."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..database.models import CaptureImportRow
from ..exceptions import NotFoundError
from ..models.capture import RemoteAdapterCapture, StagedCapture
from ..clock import utc_now


class CaptureCandidateRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def stage(
        self,
        adapter_version: str,
        candidate: RemoteAdapterCapture,
        preview_hash: str,
        *,
        ttl: timedelta = timedelta(minutes=15),
    ) -> StagedCapture:
        self.purge_expired()
        import_id = f"imp_{uuid4().hex[:26]}"
        expires_at = datetime.now(timezone.utc) + ttl
        row = CaptureImportRow(
            import_id=import_id,
            adapter_version=adapter_version,
            candidate_json=candidate.model_dump_json(),
            preview_hash=preview_hash,
            created_at=utc_now(),
            expires_at=expires_at.isoformat().replace("+00:00", "Z"),
        )
        self._session.add(row)
        self._session.flush()
        return self._to_model(row)

    def get(self, import_id: str) -> StagedCapture:
        row = self._session.get(CaptureImportRow, import_id)
        if row is None or self._is_expired(row.expires_at):
            raise NotFoundError("The temporary capture candidate is unavailable")
        return self._to_model(row)

    def delete(self, import_id: str) -> None:
        self._session.execute(
            delete(CaptureImportRow).where(CaptureImportRow.import_id == import_id)
        )

    def purge_expired(self) -> int:
        result = self._session.execute(
            delete(CaptureImportRow).where(CaptureImportRow.expires_at <= utc_now())
        )
        return int(result.rowcount or 0)

    def list_active(self) -> tuple[StagedCapture, ...]:
        rows = self._session.scalars(
            select(CaptureImportRow)
            .where(CaptureImportRow.expires_at > utc_now())
            .order_by(CaptureImportRow.created_at)
        ).all()
        return tuple(self._to_model(row) for row in rows)

    @staticmethod
    def _is_expired(value: str) -> bool:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) <= datetime.now(
            timezone.utc
        )

    @staticmethod
    def _to_model(row: CaptureImportRow) -> StagedCapture:
        candidate = RemoteAdapterCapture.model_validate_json(row.candidate_json)
        return StagedCapture(
            import_id=row.import_id,
            adapter_version=row.adapter_version,
            capture=candidate.capture,
            observations=candidate.observations,
            preview_hash=row.preview_hash,
            expires_at=row.expires_at,
        )
