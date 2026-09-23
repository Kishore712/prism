"""Contracts for local shares, invitations, and recipient grants."""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from .capture import Identifier, ShortText, StrictModel
from .publication import SnapshotSummary


class ShareStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"


class ShareVersionStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"


class InvitationStatus(str, Enum):
    PENDING = "pending"
    REDEEMED = "redeemed"
    REVOKED = "revoked"


class GrantStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"


class GrantApproval(str, Enum):
    """Owner approval of the recipient identity attached to a grant."""

    APPROVED = "approved"
    PENDING = "pending"
    DENIED = "denied"


class ShareRecord(StrictModel):
    share_id: Identifier
    name: ShortText
    status: ShareStatus
    created_at: str = Field(min_length=20, max_length=40)
    updated_at: str = Field(min_length=20, max_length=40)


class ShareVersionRecord(StrictModel):
    share_version_id: Identifier
    share_id: Identifier
    version: int = Field(ge=1)
    snapshot_id: Identifier
    status: ShareVersionStatus
    created_at: str = Field(min_length=20, max_length=40)
    revoked_at: str | None = Field(default=None, min_length=20, max_length=40)


class ShareDetails(StrictModel):
    share: ShareRecord
    versions: tuple[ShareVersionRecord, ...]


class InvitationRecord(StrictModel):
    invitation_id: Identifier
    share_version_id: Identifier
    token_hint: str = Field(min_length=4, max_length=20)
    status: InvitationStatus
    expires_at: str = Field(min_length=20, max_length=40)
    grant_expires_at: str = Field(min_length=20, max_length=40)
    created_at: str = Field(min_length=20, max_length=40)
    redeemed_at: str | None = Field(default=None, min_length=20, max_length=40)
    revoked_at: str | None = Field(default=None, min_length=20, max_length=40)
    require_approval: bool = True
    recipient_hint: str | None = Field(default=None, max_length=200)


class InvitationIssued(StrictModel):
    invitation: InvitationRecord
    invitation_token: str = Field(min_length=32, max_length=200)


class GrantRecord(StrictModel):
    grant_id: Identifier
    invitation_id: Identifier
    share_version_id: Identifier
    token_hint: str = Field(min_length=4, max_length=20)
    scope: str = Field(pattern=r"^snapshot\.read$")
    status: GrantStatus
    expires_at: str = Field(min_length=20, max_length=40)
    created_at: str = Field(min_length=20, max_length=40)
    revoked_at: str | None = Field(default=None, min_length=20, max_length=40)
    approval: GrantApproval = GrantApproval.APPROVED
    principal_id: str | None = Field(default=None, max_length=100)
    recipient_label: str | None = Field(default=None, max_length=200)
    approved_at: str | None = Field(default=None, min_length=20, max_length=40)


class GrantIssued(StrictModel):
    grant: GrantRecord
    grant_token: str = Field(min_length=32, max_length=200)


class GrantAuthorization(StrictModel):
    grant: GrantRecord
    share: ShareRecord
    share_version: ShareVersionRecord
    snapshot: SnapshotSummary


class SharingEventRecord(StrictModel):
    """Metadata-only audit event; never carries content, queries, or secrets."""

    event_id: Identifier
    event_type: str = Field(min_length=1, max_length=50)
    snapshot_id: str | None = None
    share_id: str | None = None
    share_version_id: str | None = None
    invitation_id: str | None = None
    grant_id: str | None = None
    detail: str | None = Field(default=None, max_length=50)
    created_at: str = Field(min_length=20, max_length=40)
