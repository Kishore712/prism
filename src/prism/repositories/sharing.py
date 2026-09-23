"""Repositories for stable shares, one-time invitations, and scoped grants."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..database.models import (
    GrantRow,
    InvitationRow,
    ShareRow,
    ShareVersionRow,
    SharingEventRow,
)
from ..exceptions import AuthorizationError, LifecycleError, NotFoundError
from ..models.sharing import (
    GrantApproval,
    GrantRecord,
    GrantStatus,
    InvitationRecord,
    InvitationStatus,
    ShareDetails,
    ShareRecord,
    ShareStatus,
    ShareVersionRecord,
    ShareVersionStatus,
    SharingEventRecord,
)
from ..clock import utc_now


class ShareRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, snapshot_id: str, name: str) -> ShareDetails:
        now = utc_now()
        share = ShareRow(
            share_id=f"shr_{uuid4().hex[:26]}",
            name=name,
            status=ShareStatus.ACTIVE.value,
            created_at=now,
            updated_at=now,
        )
        self._session.add(share)
        self._session.flush()
        self._add_version_row(share, snapshot_id, version=1, now=now)
        self._session.flush()
        return self.get(share.share_id)

    def add_version(self, share_id: str, snapshot_id: str) -> ShareDetails:
        share = self._row(share_id)
        if share.status != ShareStatus.ACTIVE.value:
            raise LifecycleError("A revoked share cannot receive a new version")
        current = self._session.scalar(
            select(func.max(ShareVersionRow.version)).where(
                ShareVersionRow.share_id == share_id
            )
        )
        version = int(current or 0) + 1
        now = utc_now()
        self._add_version_row(share, snapshot_id, version=version, now=now)
        share.updated_at = now
        self._session.flush()
        return self.get(share_id)

    def get(self, share_id: str) -> ShareDetails:
        share = self._row(share_id)
        versions = self._session.scalars(
            select(ShareVersionRow)
            .where(ShareVersionRow.share_id == share_id)
            .order_by(ShareVersionRow.version)
        ).all()
        return ShareDetails(
            share=self._share(share),
            versions=tuple(self._version(row) for row in versions),
        )

    def list(self) -> tuple[ShareDetails, ...]:
        rows = self._session.scalars(
            select(ShareRow).order_by(ShareRow.updated_at.desc())
        ).all()
        return tuple(self.get(row.share_id) for row in rows)

    def get_version(
        self,
        share_id: str,
        version: int | None = None,
    ) -> ShareVersionRecord:
        share = self._row(share_id)
        if share.status != ShareStatus.ACTIVE.value:
            raise LifecycleError("The share is revoked")
        statement = select(ShareVersionRow).where(
            ShareVersionRow.share_id == share_id,
            ShareVersionRow.status == ShareVersionStatus.ACTIVE.value,
        )
        if version is None:
            statement = statement.order_by(ShareVersionRow.version.desc()).limit(1)
        else:
            statement = statement.where(ShareVersionRow.version == version)
        row = self._session.scalar(statement)
        if row is None:
            raise NotFoundError("The requested active share version does not exist")
        return self._version(row)

    def get_version_by_id(self, share_version_id: str) -> ShareVersionRecord:
        row = self._session.get(ShareVersionRow, share_version_id)
        if row is None:
            raise NotFoundError("The requested share version does not exist")
        return self._version(row)

    def revoke(self, share_id: str) -> ShareDetails:
        share = self._row(share_id)
        now = utc_now()
        share.status = ShareStatus.REVOKED.value
        share.updated_at = now
        version_ids = tuple(
            self._session.scalars(
                select(ShareVersionRow.share_version_id).where(
                    ShareVersionRow.share_id == share_id
                )
            ).all()
        )
        self._revoke_version_ids(version_ids, now)
        self._session.flush()
        return self.get(share_id)

    def revoke_version(self, share_id: str, version: int) -> ShareDetails:
        share = self._row(share_id)
        row = self._session.scalar(
            select(ShareVersionRow).where(
                ShareVersionRow.share_id == share_id,
                ShareVersionRow.version == version,
            )
        )
        if row is None:
            raise NotFoundError("The requested share version does not exist")
        now = utc_now()
        self._revoke_version_ids((row.share_version_id,), now)
        active_count = self._session.scalar(
            select(func.count())
            .select_from(ShareVersionRow)
            .where(
                ShareVersionRow.share_id == share_id,
                ShareVersionRow.status == ShareVersionStatus.ACTIVE.value,
            )
        )
        if active_count == 0:
            share.status = ShareStatus.REVOKED.value
        share.updated_at = now
        self._session.flush()
        return self.get(share_id)

    def revoke_versions_for_snapshot(self, snapshot_id: str) -> tuple[str, ...]:
        rows = self._session.scalars(
            select(ShareVersionRow).where(ShareVersionRow.snapshot_id == snapshot_id)
        ).all()
        version_ids = tuple(row.share_version_id for row in rows)
        share_ids = {row.share_id for row in rows}
        now = utc_now()
        self._revoke_version_ids(version_ids, now)
        for share_id in share_ids:
            active_count = self._session.scalar(
                select(func.count())
                .select_from(ShareVersionRow)
                .where(
                    ShareVersionRow.share_id == share_id,
                    ShareVersionRow.status == ShareVersionStatus.ACTIVE.value,
                )
            )
            if active_count == 0:
                share = self._row(share_id)
                share.status = ShareStatus.REVOKED.value
                share.updated_at = now
        self._session.flush()
        return version_ids

    def _revoke_version_ids(self, version_ids: tuple[str, ...], now: str) -> None:
        if not version_ids:
            return
        self._session.execute(
            update(ShareVersionRow)
            .where(
                ShareVersionRow.share_version_id.in_(version_ids),
                ShareVersionRow.status == ShareVersionStatus.ACTIVE.value,
            )
            .values(status=ShareVersionStatus.REVOKED.value, revoked_at=now)
        )
        self._session.execute(
            update(InvitationRow)
            .where(
                InvitationRow.share_version_id.in_(version_ids),
                InvitationRow.status == InvitationStatus.PENDING.value,
            )
            .values(status=InvitationStatus.REVOKED.value, revoked_at=now)
        )
        self._session.execute(
            update(GrantRow)
            .where(
                GrantRow.share_version_id.in_(version_ids),
                GrantRow.status == GrantStatus.ACTIVE.value,
            )
            .values(status=GrantStatus.REVOKED.value, revoked_at=now)
        )

    def _row(self, share_id: str) -> ShareRow:
        row = self._session.get(ShareRow, share_id)
        if row is None:
            raise NotFoundError("The requested share does not exist")
        return row

    def _add_version_row(
        self,
        share: ShareRow,
        snapshot_id: str,
        *,
        version: int,
        now: str,
    ) -> None:
        self._session.add(
            ShareVersionRow(
                share_version_id=f"shv_{uuid4().hex[:26]}",
                share_id=share.share_id,
                version=version,
                snapshot_id=snapshot_id,
                status=ShareVersionStatus.ACTIVE.value,
                created_at=now,
            )
        )

    @staticmethod
    def _share(row: ShareRow) -> ShareRecord:
        return ShareRecord(
            share_id=row.share_id,
            name=row.name,
            status=ShareStatus(row.status),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _version(row: ShareVersionRow) -> ShareVersionRecord:
        return ShareVersionRecord(
            share_version_id=row.share_version_id,
            share_id=row.share_id,
            version=row.version,
            snapshot_id=row.snapshot_id,
            status=ShareVersionStatus(row.status),
            created_at=row.created_at,
            revoked_at=row.revoked_at,
        )


class InvitationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        share_version_id: str,
        token_hash: str,
        token_hint: str,
        expires_at: str,
        grant_expires_at: str,
        *,
        require_approval: bool = True,
        recipient_hint: str | None = None,
    ) -> InvitationRecord:
        row = InvitationRow(
            invitation_id=f"inv_{uuid4().hex[:26]}",
            share_version_id=share_version_id,
            token_hash=token_hash,
            token_hint=token_hint,
            status=InvitationStatus.PENDING.value,
            expires_at=expires_at,
            grant_expires_at=grant_expires_at,
            created_at=utc_now(),
            require_approval=require_approval,
            recipient_hint=recipient_hint,
        )
        self._session.add(row)
        self._session.flush()
        return self._record(row)

    def get(self, invitation_id: str) -> InvitationRecord:
        row = self._session.get(InvitationRow, invitation_id)
        if row is None:
            raise NotFoundError("The requested invitation does not exist")
        return self._record(row)

    def list(self) -> tuple[InvitationRecord, ...]:
        rows = self._session.scalars(
            select(InvitationRow).order_by(InvitationRow.created_at.desc())
        ).all()
        return tuple(self._record(row) for row in rows)

    def find_by_hash(self, token_hash: str) -> InvitationRecord | None:
        row = self._session.scalar(
            select(InvitationRow).where(InvitationRow.token_hash == token_hash)
        )
        return self._record(row) if row is not None else None

    def mark_redeemed(self, invitation_id: str, now: str) -> InvitationRecord:
        result = self._session.execute(
            update(InvitationRow)
            .where(
                InvitationRow.invitation_id == invitation_id,
                InvitationRow.status == InvitationStatus.PENDING.value,
                InvitationRow.expires_at > now,
            )
            .values(status=InvitationStatus.REDEEMED.value, redeemed_at=now)
        )
        if result.rowcount != 1:
            raise AuthorizationError("The invitation is invalid, expired, or already used")
        self._session.flush()
        return self.get(invitation_id)

    def revoke(self, invitation_id: str) -> InvitationRecord:
        row = self._session.get(InvitationRow, invitation_id)
        if row is None:
            raise NotFoundError("The requested invitation does not exist")
        if row.status == InvitationStatus.REDEEMED.value:
            raise LifecycleError("A redeemed invitation cannot be revoked; revoke its grant")
        if row.status == InvitationStatus.PENDING.value:
            row.status = InvitationStatus.REVOKED.value
            row.revoked_at = utc_now()
            self._session.flush()
        return self._record(row)

    @staticmethod
    def _record(row: InvitationRow) -> InvitationRecord:
        return InvitationRecord(
            invitation_id=row.invitation_id,
            share_version_id=row.share_version_id,
            token_hint=row.token_hint,
            status=InvitationStatus(row.status),
            expires_at=row.expires_at,
            grant_expires_at=row.grant_expires_at,
            created_at=row.created_at,
            redeemed_at=row.redeemed_at,
            revoked_at=row.revoked_at,
            require_approval=bool(row.require_approval),
            recipient_hint=row.recipient_hint,
        )


class GrantRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        invitation_id: str,
        share_version_id: str,
        token_hash: str,
        token_hint: str,
        expires_at: str,
        *,
        approval: GrantApproval = GrantApproval.APPROVED,
        principal_id: str | None = None,
        recipient_label: str | None = None,
    ) -> GrantRecord:
        now = utc_now()
        row = GrantRow(
            grant_id=f"grt_{uuid4().hex[:26]}",
            invitation_id=invitation_id,
            share_version_id=share_version_id,
            token_hash=token_hash,
            token_hint=token_hint,
            scope="snapshot.read",
            status=GrantStatus.ACTIVE.value,
            expires_at=expires_at,
            created_at=now,
            approval=approval.value,
            principal_id=principal_id,
            recipient_label=recipient_label,
            approved_at=now if approval is GrantApproval.APPROVED else None,
        )
        self._session.add(row)
        self._session.flush()
        return self._record(row)

    def get(self, grant_id: str) -> GrantRecord:
        row = self._session.get(GrantRow, grant_id)
        if row is None:
            raise NotFoundError("The requested grant does not exist")
        return self._record(row)

    def list(self) -> tuple[GrantRecord, ...]:
        rows = self._session.scalars(
            select(GrantRow).order_by(GrantRow.created_at.desc())
        ).all()
        return tuple(self._record(row) for row in rows)

    def find_by_hash(self, token_hash: str) -> GrantRecord | None:
        row = self._session.scalar(
            select(GrantRow).where(GrantRow.token_hash == token_hash)
        )
        return self._record(row) if row is not None else None

    def revoke(self, grant_id: str) -> GrantRecord:
        row = self._session.get(GrantRow, grant_id)
        if row is None:
            raise NotFoundError("The requested grant does not exist")
        if row.status == GrantStatus.ACTIVE.value:
            row.status = GrantStatus.REVOKED.value
            row.revoked_at = utc_now()
            self._session.flush()
        return self._record(row)

    def approve(self, grant_id: str) -> GrantRecord:
        row = self._session.get(GrantRow, grant_id)
        if row is None:
            raise NotFoundError("The requested grant does not exist")
        if row.status != GrantStatus.ACTIVE.value:
            raise LifecycleError("A revoked grant cannot be approved")
        if row.approval == GrantApproval.DENIED.value:
            raise LifecycleError("A denied grant cannot be approved")
        if row.approval == GrantApproval.PENDING.value:
            row.approval = GrantApproval.APPROVED.value
            row.approved_at = utc_now()
            self._session.flush()
        return self._record(row)

    def deny(self, grant_id: str) -> GrantRecord:
        row = self._session.get(GrantRow, grant_id)
        if row is None:
            raise NotFoundError("The requested grant does not exist")
        if row.approval == GrantApproval.APPROVED.value:
            raise LifecycleError("An approved grant must be revoked, not denied")
        if row.approval == GrantApproval.PENDING.value:
            now = utc_now()
            row.approval = GrantApproval.DENIED.value
            row.status = GrantStatus.REVOKED.value
            row.revoked_at = now
            self._session.flush()
        return self._record(row)

    @staticmethod
    def _record(row: GrantRow) -> GrantRecord:
        return GrantRecord(
            grant_id=row.grant_id,
            invitation_id=row.invitation_id,
            share_version_id=row.share_version_id,
            token_hint=row.token_hint,
            scope=row.scope,
            status=GrantStatus(row.status),
            expires_at=row.expires_at,
            created_at=row.created_at,
            revoked_at=row.revoked_at,
            approval=GrantApproval(row.approval),
            principal_id=row.principal_id,
            recipient_label=row.recipient_label,
            approved_at=row.approved_at,
        )


class SharingEventRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        event_type: str,
        *,
        snapshot_id: str | None = None,
        share_id: str | None = None,
        share_version_id: str | None = None,
        invitation_id: str | None = None,
        grant_id: str | None = None,
        detail: str | None = None,
    ) -> None:
        self._session.add(
            SharingEventRow(
                event_id=f"evt_{uuid4().hex[:26]}",
                event_type=event_type,
                snapshot_id=snapshot_id,
                share_id=share_id,
                share_version_id=share_version_id,
                invitation_id=invitation_id,
                grant_id=grant_id,
                detail=detail,
                created_at=utc_now(),
            )
        )
        self._session.flush()

    def list(
        self,
        *,
        grant_id: str | None = None,
        limit: int = 100,
    ) -> tuple[SharingEventRecord, ...]:
        query = select(SharingEventRow).order_by(SharingEventRow.created_at.desc())
        if grant_id is not None:
            query = query.where(SharingEventRow.grant_id == grant_id)
        rows = self._session.scalars(query.limit(max(1, min(limit, 1_000)))).all()
        return tuple(
            SharingEventRecord(
                event_id=row.event_id,
                event_type=row.event_type,
                snapshot_id=row.snapshot_id,
                share_id=row.share_id,
                share_version_id=row.share_version_id,
                invitation_id=row.invitation_id,
                grant_id=row.grant_id,
                detail=row.detail,
                created_at=row.created_at,
            )
            for row in rows
        )
