"""Repositories for immutable snapshot payloads and publication lineage."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..database.models import (
    PublicationEventRow,
    SnapshotLifecycleRow,
    SnapshotPayloadRow,
    SnapshotRow,
)
from ..exceptions import LifecycleError, NotFoundError, SnapshotCollisionError
from ..models.projection import SnapshotContent
from ..models.publication import (
    PublicationEvent,
    PublishedSnapshot,
    SnapshotStatus,
    SnapshotSummary,
)
from ..clock import utc_now


def _snapshot_id(content_hash: str) -> str:
    algorithm, separator, digest = content_hash.partition(":")
    if algorithm != "sha256" or separator != ":" or len(digest) != 64:
        raise SnapshotCollisionError("The snapshot content hash is not a full SHA-256 value")
    return f"snp_{digest}"


class SnapshotRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_or_reuse(
        self,
        snapshot: SnapshotContent,
        content_hash: str,
        canonical_bytes: bytes,
    ) -> tuple[PublishedSnapshot, bool]:
        snapshot_id = _snapshot_id(content_hash)
        published_at = utc_now()
        result = self._session.execute(
            sqlite_insert(SnapshotRow)
            .values(
                snapshot_id=snapshot_id,
                schema_version=snapshot.schema_version,
                content_hash=content_hash,
                published_at=published_at,
            )
            .on_conflict_do_nothing()
        )
        created = result.rowcount == 1
        if created:
            self._session.add(
                SnapshotPayloadRow(
                    snapshot_id=snapshot_id,
                    content_json=canonical_bytes.decode("utf-8"),
                    byte_size=len(canonical_bytes),
                )
            )
            self._session.add(
                SnapshotLifecycleRow(
                    snapshot_id=snapshot_id,
                    status=SnapshotStatus.ACTIVE.value,
                )
            )
            self._session.flush()
        restored = self.get(snapshot_id)
        if restored.summary.content_hash != content_hash:
            raise SnapshotCollisionError("The derived snapshot identifier already exists")
        if restored.summary.status is not SnapshotStatus.ACTIVE:
            raise LifecycleError("A revoked or purged snapshot cannot be republished")
        if restored.content is None:
            raise SnapshotCollisionError("The matching snapshot payload is unavailable")
        stored_payload = self._session.get(SnapshotPayloadRow, snapshot_id)
        if stored_payload is None or stored_payload.content_json.encode("utf-8") != canonical_bytes:
            raise SnapshotCollisionError(
                "The content hash maps to different canonical snapshot bytes"
            )
        return restored, created

    def get(self, snapshot_id: str) -> PublishedSnapshot:
        row = self._session.get(SnapshotRow, snapshot_id)
        if row is None:
            raise NotFoundError("The requested snapshot does not exist")
        lifecycle = self._session.get(SnapshotLifecycleRow, snapshot_id)
        if lifecycle is None:
            raise SnapshotCollisionError("The snapshot lifecycle record is missing")
        payload = self._session.get(SnapshotPayloadRow, snapshot_id)
        content = (
            SnapshotContent.model_validate_json(payload.content_json)
            if payload is not None
            else None
        )
        return PublishedSnapshot(
            summary=SnapshotSummary(
                snapshot_id=row.snapshot_id,
                schema_version=row.schema_version,
                content_hash=row.content_hash,
                published_at=row.published_at,
                status=SnapshotStatus(lifecycle.status),
                message_count=len(content.messages) if content is not None else 0,
                resource_count=len(content.resources) if content is not None else 0,
                byte_size=payload.byte_size if payload is not None else 0,
                revoked_at=lifecycle.revoked_at,
                purged_at=lifecycle.purged_at,
            ),
            content=content,
        )

    def list(self) -> tuple[SnapshotSummary, ...]:
        rows = self._session.scalars(
            select(SnapshotRow).order_by(SnapshotRow.published_at.desc())
        ).all()
        return tuple(self.get(row.snapshot_id).summary for row in rows)

    def require_active(self, snapshot_id: str) -> PublishedSnapshot:
        snapshot = self.get(snapshot_id)
        if snapshot.summary.status is not SnapshotStatus.ACTIVE or snapshot.content is None:
            raise LifecycleError("The snapshot is not active")
        return snapshot

    def revoke(self, snapshot_id: str, *, purge: bool) -> PublishedSnapshot:
        snapshot = self.get(snapshot_id)
        lifecycle = self._session.get(SnapshotLifecycleRow, snapshot_id)
        assert lifecycle is not None
        now = utc_now()
        if lifecycle.revoked_at is None:
            lifecycle.revoked_at = now
        if purge:
            self._session.execute(
                delete(SnapshotPayloadRow).where(
                    SnapshotPayloadRow.snapshot_id == snapshot_id
                )
            )
            lifecycle.status = SnapshotStatus.PURGED.value
            lifecycle.purged_at = lifecycle.purged_at or now
        elif lifecycle.status == SnapshotStatus.ACTIVE.value:
            lifecycle.status = SnapshotStatus.REVOKED.value
        self._session.flush()
        return self.get(snapshot_id)


class PublicationEventRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def find(
        self,
        draft_id: str,
        draft_revision: int,
        preview_hash: str,
    ) -> PublicationEvent | None:
        row = self._session.scalar(
            select(PublicationEventRow).where(
                PublicationEventRow.draft_id == draft_id,
                PublicationEventRow.draft_revision == draft_revision,
                PublicationEventRow.preview_hash == preview_hash,
            )
        )
        return self._event(row) if row is not None else None

    def record(
        self,
        snapshot_id: str,
        capture_id: str,
        draft_id: str,
        draft_revision: int,
        preview_hash: str,
    ) -> PublicationEvent:
        publication_id = f"pub_{uuid4().hex[:26]}"
        published_at = utc_now()
        self._session.execute(
            sqlite_insert(PublicationEventRow)
            .values(
                publication_id=publication_id,
                snapshot_id=snapshot_id,
                capture_id=capture_id,
                draft_id=draft_id,
                draft_revision=draft_revision,
                preview_hash=preview_hash,
                published_at=published_at,
            )
            .on_conflict_do_nothing(
                index_elements=["draft_id", "draft_revision", "preview_hash"]
            )
        )
        self._session.flush()
        event = self.find(draft_id, draft_revision, preview_hash)
        if event is None:
            raise SnapshotCollisionError("The publication event could not be recorded")
        if event.snapshot_id != snapshot_id or event.capture_id != capture_id:
            raise SnapshotCollisionError(
                "The publication identity maps to different lineage"
            )
        return event

    @staticmethod
    def _event(row: PublicationEventRow) -> PublicationEvent:
        return PublicationEvent(
            publication_id=row.publication_id,
            snapshot_id=row.snapshot_id,
            capture_id=row.capture_id,
            draft_id=row.draft_id,
            draft_revision=row.draft_revision,
            preview_hash=row.preview_hash,
            published_at=row.published_at,
        )
