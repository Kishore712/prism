"""SQLAlchemy persistence models for durable Prism owner state."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CaptureImportRow(Base):
    __tablename__ = "capture_imports"
    __table_args__ = (Index("ix_capture_imports_expires_at", "expires_at"),)

    import_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    adapter_version: Mapped[str] = mapped_column(String(100), nullable=False)
    candidate_json: Mapped[str] = mapped_column(Text, nullable=False)
    preview_hash: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    expires_at: Mapped[str] = mapped_column(String(40), nullable=False)


class CaptureRow(Base):
    __tablename__ = "captures"
    __table_args__ = (Index("ix_captures_capture_hash", "capture_hash"),)

    capture_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    method: Mapped[str] = mapped_column(String(50), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(100), nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(100), nullable=False)
    conversation_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    captured_at: Mapped[str] = mapped_column(String(40), nullable=False)
    capture_hash: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class CaptureTurnRow(Base):
    __tablename__ = "capture_turns"
    __table_args__ = (
        UniqueConstraint("capture_id", "turn_index", name="uq_capture_turn_index"),
        UniqueConstraint("capture_id", "turn_id", name="uq_capture_turn_identity"),
    )

    turn_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    capture_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("captures.capture_id", ondelete="CASCADE"),
        nullable=False,
    )
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)


class CaptureMessageRow(Base):
    __tablename__ = "capture_messages"
    __table_args__ = (
        UniqueConstraint("capture_id", "ordinal", name="uq_capture_message_ordinal"),
        ForeignKeyConstraint(
            ("capture_id", "turn_id"),
            ("capture_turns.capture_id", "capture_turns.turn_id"),
            ondelete="CASCADE",
            name="fk_message_capture_turn",
        ),
        CheckConstraint("role IN ('user', 'assistant')", name="ck_capture_message_role"),
    )

    message_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    capture_id: Mapped[str] = mapped_column(String(100), nullable=False)
    turn_id: Mapped[str] = mapped_column(String(100), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)


class CaptureResourceRow(Base):
    __tablename__ = "capture_resources"
    __table_args__ = (
        UniqueConstraint("capture_id", "resource_id", name="uq_capture_resource_identity"),
        UniqueConstraint("capture_id", "ordinal", name="uq_capture_resource_ordinal"),
    )

    resource_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    capture_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("captures.capture_id", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    display_name: Mapped[str] = mapped_column(String(500), nullable=False)
    media_type: Mapped[str] = mapped_column(String(500), nullable=False)
    availability: Mapped[str] = mapped_column(String(50), nullable=False)


class CaptureWarningRow(Base):
    __tablename__ = "capture_warnings"
    __table_args__ = (
        UniqueConstraint("capture_id", "ordinal", name="uq_capture_warning_ordinal"),
        CheckConstraint(
            "severity IN ('info', 'warning', 'blocking')",
            name="ck_capture_warning_severity",
        ),
    )

    warning_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    capture_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("captures.capture_id", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)


class ShareDraftRow(Base):
    __tablename__ = "share_drafts"
    __table_args__ = (
        UniqueConstraint("draft_id", "capture_id", name="uq_draft_capture_identity"),
        CheckConstraint("revision >= 1", name="ck_draft_revision"),
        CheckConstraint(
            "status IN ('editing', 'published', 'abandoned')",
            name="ck_draft_status",
        ),
        CheckConstraint(
            "((previewed_revision IS NULL AND preview_hash IS NULL) OR "
            "(previewed_revision = revision AND preview_hash IS NOT NULL))",
            name="ck_draft_preview_binding",
        ),
    )

    draft_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    capture_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("captures.capture_id", ondelete="CASCADE"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="editing")
    previewed_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    preview_hash: Mapped[str | None] = mapped_column(String(100), nullable=True)
    title_override: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class DraftTurnSelectionRow(Base):
    __tablename__ = "draft_turn_selections"
    __table_args__ = (
        ForeignKeyConstraint(
            ("draft_id", "capture_id"),
            ("share_drafts.draft_id", "share_drafts.capture_id"),
            ondelete="CASCADE",
            name="fk_turn_selection_draft_capture",
        ),
        ForeignKeyConstraint(
            ("capture_id", "turn_id"),
            ("capture_turns.capture_id", "capture_turns.turn_id"),
            ondelete="CASCADE",
            name="fk_turn_selection_capture_turn",
        ),
        CheckConstraint("included IN (0, 1)", name="ck_turn_selection_included"),
    )

    draft_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    turn_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    capture_id: Mapped[str] = mapped_column(String(100), nullable=False)
    included: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class DraftResourceSelectionRow(Base):
    __tablename__ = "draft_resource_selections"
    __table_args__ = (
        ForeignKeyConstraint(
            ("draft_id", "capture_id"),
            ("share_drafts.draft_id", "share_drafts.capture_id"),
            ondelete="CASCADE",
            name="fk_resource_selection_draft_capture",
        ),
        ForeignKeyConstraint(
            ("capture_id", "resource_id"),
            ("capture_resources.capture_id", "capture_resources.resource_id"),
            ondelete="CASCADE",
            name="fk_resource_selection_capture_resource",
        ),
        CheckConstraint(
            "included IN (0, 1)",
            name="ck_resource_selection_included",
        ),
    )

    draft_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    resource_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    capture_id: Mapped[str] = mapped_column(String(100), nullable=False)
    included: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class LegacyImportRow(Base):
    __tablename__ = "legacy_imports"

    file_fingerprint: Mapped[str] = mapped_column(String(100), primary_key=True)
    capture_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("captures.capture_id", ondelete="CASCADE"),
        nullable=False,
    )
    imported_at: Mapped[str] = mapped_column(String(40), nullable=False)


class SnapshotRow(Base):
    __tablename__ = "snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    published_at: Mapped[str] = mapped_column(String(40), nullable=False)


class SnapshotPayloadRow(Base):
    __tablename__ = "snapshot_payloads"
    __table_args__ = (
        CheckConstraint("byte_size > 0", name="ck_snapshot_payload_byte_size"),
    )

    snapshot_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("snapshots.snapshot_id", ondelete="CASCADE"),
        primary_key=True,
    )
    content_json: Mapped[str] = mapped_column(Text, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)


class SnapshotLifecycleRow(Base):
    __tablename__ = "snapshot_lifecycle"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'revoked', 'purged')",
            name="ck_snapshot_lifecycle_status",
        ),
    )

    snapshot_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("snapshots.snapshot_id", ondelete="CASCADE"),
        primary_key=True,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    revoked_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    purged_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class PublicationEventRow(Base):
    __tablename__ = "publication_events"
    __table_args__ = (
        UniqueConstraint(
            "draft_id",
            "draft_revision",
            "preview_hash",
            name="uq_publication_draft_revision_preview",
        ),
        CheckConstraint("draft_revision >= 1", name="ck_publication_draft_revision"),
    )

    publication_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("snapshots.snapshot_id", ondelete="RESTRICT"),
        nullable=False,
    )
    capture_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("captures.capture_id", ondelete="RESTRICT"),
        nullable=False,
    )
    draft_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("share_drafts.draft_id", ondelete="RESTRICT"),
        nullable=False,
    )
    draft_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    preview_hash: Mapped[str] = mapped_column(String(100), nullable=False)
    published_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ShareRow(Base):
    __tablename__ = "shares"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'revoked')", name="ck_share_status"),
    )

    share_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ShareVersionRow(Base):
    __tablename__ = "share_versions"
    __table_args__ = (
        UniqueConstraint("share_id", "version", name="uq_share_version_number"),
        CheckConstraint("version >= 1", name="ck_share_version_number"),
        CheckConstraint(
            "status IN ('active', 'revoked')", name="ck_share_version_status"
        ),
    )

    share_version_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    share_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("shares.share_id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("snapshots.snapshot_id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    revoked_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class InvitationRow(Base):
    __tablename__ = "invitations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'redeemed', 'revoked')",
            name="ck_invitation_status",
        ),
        Index("ix_invitations_expires_at", "expires_at"),
    )

    invitation_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    share_version_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("share_versions.share_version_id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    token_hint: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    expires_at: Mapped[str] = mapped_column(String(40), nullable=False)
    grant_expires_at: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    redeemed_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    revoked_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    require_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    recipient_hint: Mapped[str | None] = mapped_column(String(200), nullable=True)


class GrantRow(Base):
    __tablename__ = "grants"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'revoked')", name="ck_grant_status"),
        CheckConstraint("scope = 'snapshot.read'", name="ck_grant_scope"),
        CheckConstraint(
            "approval IN ('approved', 'pending', 'denied')",
            name="ck_grant_approval",
        ),
        Index("ix_grants_expires_at", "expires_at"),
    )

    grant_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    invitation_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("invitations.invitation_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    share_version_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("share_versions.share_version_id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    token_hint: Mapped[str] = mapped_column(String(20), nullable=False)
    scope: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    expires_at: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    revoked_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    approval: Mapped[str] = mapped_column(
        String(20), nullable=False, default="approved", server_default="approved"
    )
    principal_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    recipient_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    approved_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class SharingEventRow(Base):
    __tablename__ = "sharing_events"
    __table_args__ = (Index("ix_sharing_events_created_at", "created_at"),)

    event_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    snapshot_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    share_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    share_version_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    invitation_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    grant_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class OAuthClientRow(Base):
    __tablename__ = "oauth_clients"

    client_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    client_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class OAuthTicketRow(Base):
    """One in-flight authorization request awaiting invitation redemption."""

    __tablename__ = "oauth_tickets"
    __table_args__ = (Index("ix_oauth_tickets_expires_at", "expires_at"),)

    ticket_hash: Mapped[str] = mapped_column(String(100), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(100), nullable=False)
    params_json: Mapped[str] = mapped_column(Text, nullable=False)
    grant_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consumed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    expires_at: Mapped[str] = mapped_column(String(40), nullable=False)


class OAuthCodeRow(Base):
    __tablename__ = "oauth_codes"
    __table_args__ = (Index("ix_oauth_codes_expires_at", "expires_at"),)

    code_hash: Mapped[str] = mapped_column(String(100), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(100), nullable=False)
    grant_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("grants.grant_id", ondelete="CASCADE"),
        nullable=False,
    )
    principal_id: Mapped[str] = mapped_column(String(100), nullable=False)
    code_challenge: Mapped[str] = mapped_column(String(200), nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(2000), nullable=False)
    redirect_uri_provided_explicitly: Mapped[bool] = mapped_column(Boolean, nullable=False)
    resource: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    scopes_json: Mapped[str] = mapped_column(Text, nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    expires_at: Mapped[str] = mapped_column(String(40), nullable=False)


class OAuthTokenRow(Base):
    __tablename__ = "oauth_tokens"
    __table_args__ = (
        CheckConstraint("kind IN ('access', 'refresh')", name="ck_oauth_token_kind"),
        Index("ix_oauth_tokens_grant_id", "grant_id"),
        Index("ix_oauth_tokens_expires_at", "expires_at"),
    )

    token_hash: Mapped[str] = mapped_column(String(100), primary_key=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    client_id: Mapped[str] = mapped_column(String(100), nullable=False)
    grant_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("grants.grant_id", ondelete="CASCADE"),
        nullable=False,
    )
    principal_id: Mapped[str] = mapped_column(String(100), nullable=False)
    resource: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    scopes_json: Mapped[str] = mapped_column(Text, nullable=False)
    family_id: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    expires_at: Mapped[str] = mapped_column(String(40), nullable=False)
    revoked_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class DraftAttachmentRow(Base):
    """Owner-supplied text resource attached to one draft (excluded until attached)."""

    __tablename__ = "draft_attachments"
    __table_args__ = (
        Index("ix_draft_attachments_draft", "draft_id", "ordinal"),
    )

    attachment_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    draft_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("share_drafts.draft_id", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    display_name: Mapped[str] = mapped_column(String(500), nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(100), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class DraftFindingDecisionRow(Base):
    """Owner decision on one projection finding, keyed by its stable fingerprint.

    Not the finding itself (findings are recomputed by scanning, never
    persisted) — only the owner's disposition, so a decision survives
    re-scanning identical content across drafts and revisions.
    """

    __tablename__ = "draft_finding_decisions"
    __table_args__ = (
        CheckConstraint("disposition = 'allow'", name="ck_finding_disposition"),
    )

    draft_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("share_drafts.draft_id", ondelete="CASCADE"),
        primary_key=True,
    )
    finding_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    disposition: Mapped[str] = mapped_column(String(20), nullable=False)
    finding_class: Mapped[str] = mapped_column(String(30), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    decided_at: Mapped[str] = mapped_column(String(40), nullable=False)


class SnapshotReceiptRow(Base):
    __tablename__ = "snapshot_receipts"

    snapshot_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("snapshots.snapshot_id", ondelete="CASCADE"),
        primary_key=True,
    )
    capture_hash: Mapped[str] = mapped_column(String(100), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(100), nullable=False)
    detector_versions_json: Mapped[str] = mapped_column(Text, nullable=False)
    finding_count: Mapped[int] = mapped_column(Integer, nullable=False)
    override_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    public_key_hex: Mapped[str] = mapped_column(String(64), nullable=False)
    signature_hex: Mapped[str] = mapped_column(String(128), nullable=False)


class CaptureToolEventRow(Base):
    """Owner-only tool-call provenance, persisted once at capture time.

    Never referenced by any recipient-facing model or query path; only
    ``repositories/provenance.py`` and Phase 9's taint-derived findings read
    this table.
    """

    __tablename__ = "capture_tool_events"
    __table_args__ = (Index("ix_capture_tool_events_capture", "capture_id", "turn_index"),)

    event_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    capture_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("captures.capture_id", ondelete="CASCADE"),
        nullable=False,
    )
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_label: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class DraftTaintedEventRow(Base):
    """The owner's decision that one tool-read event was a mistake."""

    __tablename__ = "draft_tainted_events"

    draft_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("share_drafts.draft_id", ondelete="CASCADE"),
        primary_key=True,
    )
    event_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("capture_tool_events.event_id", ondelete="CASCADE"),
        primary_key=True,
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    tainted_at: Mapped[str] = mapped_column(String(40), nullable=False)
