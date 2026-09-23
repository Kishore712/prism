"""Repository for owner decisions on projection findings, and receipts.

Findings themselves are never persisted (they are recomputed by scanning);
only the owner's disposition against a finding's stable fingerprint is
durable, keyed per draft so it survives re-scans of identical content.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clock import utc_now
from ..database.models import DraftFindingDecisionRow, SnapshotReceiptRow
from ..exceptions import NotFoundError, ValidationError
from ..models.projection import SnapshotReceipt


class DraftFindingDecisionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def allow(self, draft_id: str, finding_id: str, finding_class: str, reason: str) -> None:
        reason = reason.strip()
        if not reason:
            raise ValidationError("A reason is required to allow a finding")
        row = self._session.get(DraftFindingDecisionRow, (draft_id, finding_id))
        if row is None:
            row = DraftFindingDecisionRow(
                draft_id=draft_id,
                finding_id=finding_id,
                disposition="allow",
                finding_class=finding_class,
                reason=reason,
                decided_at=utc_now(),
            )
            self._session.add(row)
        else:
            row.finding_class = finding_class
            row.reason = reason
            row.decided_at = utc_now()
        self._session.flush()

    def revoke(self, draft_id: str, finding_id: str) -> None:
        row = self._session.get(DraftFindingDecisionRow, (draft_id, finding_id))
        if row is None:
            raise NotFoundError("No decision exists for that finding on this draft")
        self._session.delete(row)
        self._session.flush()

    def decided_ids(self, draft_id: str) -> frozenset[str]:
        rows = self._session.scalars(
            select(DraftFindingDecisionRow.finding_id).where(
                DraftFindingDecisionRow.draft_id == draft_id
            )
        ).all()
        return frozenset(rows)

    def list(self, draft_id: str) -> tuple[DraftFindingDecisionRow, ...]:
        return tuple(
            self._session.scalars(
                select(DraftFindingDecisionRow)
                .where(DraftFindingDecisionRow.draft_id == draft_id)
                .order_by(DraftFindingDecisionRow.decided_at)
            ).all()
        )


class ReceiptRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, snapshot_id: str, receipt: SnapshotReceipt) -> None:
        existing = self._session.get(SnapshotReceiptRow, snapshot_id)
        if existing is not None:
            return  # immutable snapshots are receipted exactly once
        self._session.add(
            SnapshotReceiptRow(
                snapshot_id=snapshot_id,
                capture_hash=receipt.capture_hash,
                snapshot_hash=receipt.snapshot_hash,
                detector_versions_json=json.dumps(receipt.detector_versions, sort_keys=True),
                finding_count=receipt.finding_count,
                override_count=receipt.override_count,
                created_at=receipt.created_at,
                public_key_hex=receipt.public_key_hex,
                signature_hex=receipt.signature_hex,
            )
        )
        self._session.flush()

    def get(self, snapshot_id: str) -> SnapshotReceipt:
        row = self._session.get(SnapshotReceiptRow, snapshot_id)
        if row is None:
            raise NotFoundError("No receipt exists for that snapshot")
        return SnapshotReceipt(
            capture_hash=row.capture_hash,
            snapshot_hash=row.snapshot_hash,
            detector_versions=json.loads(row.detector_versions_json),
            finding_count=row.finding_count,
            override_count=row.override_count,
            created_at=row.created_at,
            public_key_hex=row.public_key_hex,
            signature_hex=row.signature_hex,
        )
