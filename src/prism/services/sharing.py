"""Owner-local stable shares, one-time invitations, and scoped grants."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.exc import SQLAlchemyError

from ..database import PrismDatabase, PrismUnitOfWork
from ..exceptions import (
    AuthorizationError,
    DatabaseError,
    LifecycleError,
    NotFoundError,
    ValidationError,
)
from ..models.publication import SnapshotStatus
from ..models.sharing import (
    GrantApproval,
    GrantAuthorization,
    GrantIssued,
    GrantRecord,
    GrantStatus,
    InvitationIssued,
    InvitationRecord,
    InvitationStatus,
    ShareDetails,
    ShareStatus,
    ShareVersionStatus,
    SharingEventRecord,
)
from ..security import capability_hash, is_capability, new_capability
from ..clock import utc_now


def _parse_utc(value: str, field: str) -> datetime:
    if len(value) > 40:
        raise ValidationError(f"{field} must be a bounded ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


class SharingService:
    def __init__(self, database: PrismDatabase) -> None:
        self._database = database
        self._database.initialize()

    def create_share(self, snapshot_id: str, name: str | None = None) -> ShareDetails:
        if name is not None and (not name.strip() or len(name) > 500):
            raise ValidationError("Share name must contain 1 to 500 characters")
        try:
            with PrismUnitOfWork(self._database) as unit:
                snapshot = unit.snapshots.require_active(snapshot_id)
                assert snapshot.content is not None
                share = unit.shares.create(snapshot_id, name or snapshot.content.title)
                version = share.versions[-1]
                unit.sharing_events.record(
                    "share_created",
                    snapshot_id=snapshot_id,
                    share_id=share.share.share_id,
                    share_version_id=version.share_version_id,
                )
                unit.commit()
                return share
        except SQLAlchemyError as exc:
            raise DatabaseError("The share could not be created") from exc

    def add_version(self, share_id: str, snapshot_id: str) -> ShareDetails:
        try:
            with PrismUnitOfWork(self._database) as unit:
                unit.snapshots.require_active(snapshot_id)
                share = unit.shares.add_version(share_id, snapshot_id)
                version = share.versions[-1]
                unit.sharing_events.record(
                    "share_version_added",
                    snapshot_id=snapshot_id,
                    share_id=share_id,
                    share_version_id=version.share_version_id,
                )
                unit.commit()
                return share
        except SQLAlchemyError as exc:
            raise DatabaseError("The share version could not be added") from exc

    def get_share(self, share_id: str) -> ShareDetails:
        try:
            with PrismUnitOfWork(self._database) as unit:
                return unit.shares.get(share_id)
        except SQLAlchemyError as exc:
            raise DatabaseError("The share could not be read") from exc

    def list_shares(self) -> tuple[ShareDetails, ...]:
        try:
            with PrismUnitOfWork(self._database) as unit:
                return unit.shares.list()
        except SQLAlchemyError as exc:
            raise DatabaseError("Shares could not be listed") from exc

    def revoke_share(self, share_id: str) -> ShareDetails:
        try:
            with PrismUnitOfWork(self._database) as unit:
                share = unit.shares.revoke(share_id)
                unit.sharing_events.record("share_revoked", share_id=share_id)
                unit.commit()
                return share
        except SQLAlchemyError as exc:
            raise DatabaseError("The share could not be revoked") from exc

    def revoke_share_version(self, share_id: str, version: int) -> ShareDetails:
        if version < 1:
            raise ValidationError("Share version must be a positive integer")
        try:
            with PrismUnitOfWork(self._database) as unit:
                share = unit.shares.revoke_version(share_id, version)
                revoked = next(item for item in share.versions if item.version == version)
                unit.sharing_events.record(
                    "share_version_revoked",
                    snapshot_id=revoked.snapshot_id,
                    share_id=share_id,
                    share_version_id=revoked.share_version_id,
                )
                unit.commit()
                return share
        except SQLAlchemyError as exc:
            raise DatabaseError("The share version could not be revoked") from exc

    def create_invitation(
        self,
        share_id: str,
        *,
        version: int | None,
        expires_at: str,
        grant_expires_at: str,
        require_approval: bool = True,
        recipient_hint: str | None = None,
    ) -> InvitationIssued:
        if recipient_hint is not None and (
            not recipient_hint.strip() or len(recipient_hint) > 200
        ):
            raise ValidationError("Recipient hint must contain 1 to 200 characters")
        now = datetime.now(timezone.utc)
        invitation_expiry = _parse_utc(expires_at, "expires_at")
        grant_expiry = _parse_utc(grant_expires_at, "grant_expires_at")
        if invitation_expiry <= now:
            raise ValidationError("Invitation expiry must be in the future")
        if grant_expiry <= invitation_expiry:
            raise ValidationError("Grant expiry must be later than invitation expiry")
        token = new_capability("pinv_v1")
        try:
            with PrismUnitOfWork(self._database) as unit:
                share_version = unit.shares.get_version(share_id, version)
                unit.snapshots.require_active(share_version.snapshot_id)
                invitation = unit.invitations.create(
                    share_version.share_version_id,
                    capability_hash("Invitation", token),
                    token[-8:],
                    invitation_expiry.isoformat().replace("+00:00", "Z"),
                    grant_expiry.isoformat().replace("+00:00", "Z"),
                    require_approval=require_approval,
                    recipient_hint=recipient_hint.strip() if recipient_hint else None,
                )
                unit.sharing_events.record(
                    "invitation_created",
                    snapshot_id=share_version.snapshot_id,
                    share_id=share_id,
                    share_version_id=share_version.share_version_id,
                    invitation_id=invitation.invitation_id,
                )
                unit.commit()
                return InvitationIssued(
                    invitation=invitation,
                    invitation_token=token,
                )
        except SQLAlchemyError as exc:
            raise DatabaseError("The invitation could not be created") from exc

    def redeem_invitation(self, invitation_token: str) -> GrantIssued:
        if not is_capability(invitation_token, "pinv_v1"):
            raise AuthorizationError("The invitation is invalid, expired, or already used")
        now = utc_now()
        grant_token = new_capability("pgrt_v1")
        try:
            with PrismUnitOfWork(self._database) as unit:
                invitation = unit.invitations.find_by_hash(
                    capability_hash("Invitation", invitation_token)
                )
                if (
                    invitation is None
                    or invitation.status is not InvitationStatus.PENDING
                    or invitation.expires_at <= now
                    or invitation.grant_expires_at <= now
                ):
                    raise AuthorizationError(
                        "The invitation is invalid, expired, or already used"
                    )
                version = unit.shares.get_version_by_id(invitation.share_version_id)
                share = unit.shares.get(version.share_id)
                if (
                    share.share.status is not ShareStatus.ACTIVE
                    or version.status is not ShareVersionStatus.ACTIVE
                ):
                    raise AuthorizationError(
                        "The invitation is invalid, expired, or already used"
                    )
                unit.snapshots.require_active(version.snapshot_id)
                invitation = unit.invitations.mark_redeemed(
                    invitation.invitation_id,
                    now,
                )
                grant = unit.grants.create(
                    invitation.invitation_id,
                    invitation.share_version_id,
                    capability_hash("Grant", grant_token),
                    grant_token[-8:],
                    invitation.grant_expires_at,
                )
                unit.sharing_events.record(
                    "invitation_redeemed",
                    snapshot_id=version.snapshot_id,
                    share_id=version.share_id,
                    share_version_id=version.share_version_id,
                    invitation_id=invitation.invitation_id,
                    grant_id=grant.grant_id,
                )
                unit.commit()
                return GrantIssued(grant=grant, grant_token=grant_token)
        except SQLAlchemyError as exc:
            raise DatabaseError("The invitation redemption transaction failed") from exc
        except (LifecycleError, NotFoundError) as exc:
            raise AuthorizationError(
                "The invitation is invalid, expired, or already used"
            ) from exc

    def authorize_grant(self, grant_token: str) -> GrantAuthorization:
        if not is_capability(grant_token, "pgrt_v1"):
            raise AuthorizationError("The grant is invalid, expired, or revoked")
        now = utc_now()
        try:
            with PrismUnitOfWork(self._database) as unit:
                grant = unit.grants.find_by_hash(
                    capability_hash("Grant", grant_token)
                )
                if (
                    grant is None
                    or grant.status is not GrantStatus.ACTIVE
                    or grant.approval is not GrantApproval.APPROVED
                    or grant.expires_at <= now
                ):
                    raise AuthorizationError("The grant is invalid, expired, or revoked")
                version = unit.shares.get_version_by_id(grant.share_version_id)
                share = unit.shares.get(version.share_id)
                snapshot = unit.snapshots.get(version.snapshot_id)
                if (
                    share.share.status is not ShareStatus.ACTIVE
                    or version.status is not ShareVersionStatus.ACTIVE
                    or snapshot.summary.status is not SnapshotStatus.ACTIVE
                    or snapshot.content is None
                ):
                    raise AuthorizationError("The grant is invalid, expired, or revoked")
                unit.sharing_events.record(
                    "grant_authorized",
                    snapshot_id=version.snapshot_id,
                    share_id=version.share_id,
                    share_version_id=version.share_version_id,
                    invitation_id=grant.invitation_id,
                    grant_id=grant.grant_id,
                )
                unit.commit()
                return GrantAuthorization(
                    grant=grant,
                    share=share.share,
                    share_version=version,
                    snapshot=snapshot.summary,
                )
        except SQLAlchemyError as exc:
            raise DatabaseError("The grant authorization check failed") from exc
        except (LifecycleError, NotFoundError) as exc:
            raise AuthorizationError("The grant is invalid, expired, or revoked") from exc

    def revoke_invitation(self, invitation_id: str) -> InvitationRecord:
        try:
            with PrismUnitOfWork(self._database) as unit:
                invitation = unit.invitations.revoke(invitation_id)
                unit.sharing_events.record(
                    "invitation_revoked",
                    share_version_id=invitation.share_version_id,
                    invitation_id=invitation.invitation_id,
                )
                unit.commit()
                return invitation
        except SQLAlchemyError as exc:
            raise DatabaseError("The invitation could not be revoked") from exc

    def list_invitations(self) -> tuple[InvitationRecord, ...]:
        try:
            with PrismUnitOfWork(self._database) as unit:
                return unit.invitations.list()
        except SQLAlchemyError as exc:
            raise DatabaseError("Invitations could not be listed") from exc

    def revoke_grant(self, grant_id: str) -> GrantRecord:
        try:
            with PrismUnitOfWork(self._database) as unit:
                grant = unit.grants.revoke(grant_id)
                unit.sharing_events.record(
                    "grant_revoked",
                    share_version_id=grant.share_version_id,
                    invitation_id=grant.invitation_id,
                    grant_id=grant.grant_id,
                )
                unit.commit()
                return grant
        except SQLAlchemyError as exc:
            raise DatabaseError("The grant could not be revoked") from exc

    def list_grants(self) -> tuple[GrantRecord, ...]:
        try:
            with PrismUnitOfWork(self._database) as unit:
                return unit.grants.list()
        except SQLAlchemyError as exc:
            raise DatabaseError("Grants could not be listed") from exc

    def approve_grant(self, grant_id: str) -> GrantRecord:
        """Owner confirms the recipient behind a pending grant."""

        try:
            with PrismUnitOfWork(self._database) as unit:
                grant = unit.grants.approve(grant_id)
                unit.sharing_events.record(
                    "grant_approved",
                    share_version_id=grant.share_version_id,
                    invitation_id=grant.invitation_id,
                    grant_id=grant.grant_id,
                )
                unit.commit()
                return grant
        except SQLAlchemyError as exc:
            raise DatabaseError("The grant could not be approved") from exc

    def deny_grant(self, grant_id: str) -> GrantRecord:
        """Owner rejects the recipient behind a pending grant."""

        try:
            with PrismUnitOfWork(self._database) as unit:
                grant = unit.grants.deny(grant_id)
                unit.sharing_events.record(
                    "grant_denied",
                    share_version_id=grant.share_version_id,
                    invitation_id=grant.invitation_id,
                    grant_id=grant.grant_id,
                )
                unit.commit()
                return grant
        except SQLAlchemyError as exc:
            raise DatabaseError("The grant could not be denied") from exc

    def list_events(
        self,
        *,
        grant_id: str | None = None,
        limit: int = 100,
    ) -> tuple[SharingEventRecord, ...]:
        try:
            with PrismUnitOfWork(self._database) as unit:
                return unit.sharing_events.list(grant_id=grant_id, limit=limit)
        except SQLAlchemyError as exc:
            raise DatabaseError("Audit events could not be listed") from exc
