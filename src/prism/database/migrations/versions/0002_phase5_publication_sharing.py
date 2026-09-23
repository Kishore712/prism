"""Add immutable publication and local sharing state.

Revision ID: 0002_phase5_publication_sharing
Revises: 0001_durable_owner_state
Create Date: 2026-09-20
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_phase5_publication_sharing"
down_revision = "0001_durable_owner_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "snapshots",
        sa.Column("snapshot_id", sa.String(length=100), primary_key=True),
        sa.Column("schema_version", sa.String(length=100), nullable=False),
        sa.Column("content_hash", sa.String(length=100), nullable=False, unique=True),
        sa.Column("published_at", sa.String(length=40), nullable=False),
    )
    op.create_table(
        "snapshot_payloads",
        sa.Column(
            "snapshot_id",
            sa.String(length=100),
            sa.ForeignKey("snapshots.snapshot_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("content_json", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.CheckConstraint("byte_size > 0", name="ck_snapshot_payload_byte_size"),
    )
    op.create_table(
        "snapshot_lifecycle",
        sa.Column(
            "snapshot_id",
            sa.String(length=100),
            sa.ForeignKey("snapshots.snapshot_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("revoked_at", sa.String(length=40), nullable=True),
        sa.Column("purged_at", sa.String(length=40), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'revoked', 'purged')",
            name="ck_snapshot_lifecycle_status",
        ),
    )
    op.create_table(
        "publication_events",
        sa.Column("publication_id", sa.String(length=100), primary_key=True),
        sa.Column(
            "snapshot_id",
            sa.String(length=100),
            sa.ForeignKey("snapshots.snapshot_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "capture_id",
            sa.String(length=100),
            sa.ForeignKey("captures.capture_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "draft_id",
            sa.String(length=100),
            sa.ForeignKey("share_drafts.draft_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("draft_revision", sa.Integer(), nullable=False),
        sa.Column("preview_hash", sa.String(length=100), nullable=False),
        sa.Column("published_at", sa.String(length=40), nullable=False),
        sa.UniqueConstraint(
            "draft_id",
            "draft_revision",
            "preview_hash",
            name="uq_publication_draft_revision_preview",
        ),
        sa.CheckConstraint(
            "draft_revision >= 1", name="ck_publication_draft_revision"
        ),
    )
    op.create_table(
        "shares",
        sa.Column("share_id", sa.String(length=100), primary_key=True),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')", name="ck_share_status"
        ),
    )
    op.create_table(
        "share_versions",
        sa.Column("share_version_id", sa.String(length=100), primary_key=True),
        sa.Column(
            "share_id",
            sa.String(length=100),
            sa.ForeignKey("shares.share_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "snapshot_id",
            sa.String(length=100),
            sa.ForeignKey("snapshots.snapshot_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("revoked_at", sa.String(length=40), nullable=True),
        sa.UniqueConstraint("share_id", "version", name="uq_share_version_number"),
        sa.CheckConstraint("version >= 1", name="ck_share_version_number"),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')", name="ck_share_version_status"
        ),
    )
    op.create_table(
        "invitations",
        sa.Column("invitation_id", sa.String(length=100), primary_key=True),
        sa.Column(
            "share_version_id",
            sa.String(length=100),
            sa.ForeignKey("share_versions.share_version_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=100), nullable=False, unique=True),
        sa.Column("token_hint", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.String(length=40), nullable=False),
        sa.Column("grant_expires_at", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("redeemed_at", sa.String(length=40), nullable=True),
        sa.Column("revoked_at", sa.String(length=40), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'redeemed', 'revoked')",
            name="ck_invitation_status",
        ),
    )
    op.create_index("ix_invitations_expires_at", "invitations", ["expires_at"])
    op.create_table(
        "grants",
        sa.Column("grant_id", sa.String(length=100), primary_key=True),
        sa.Column(
            "invitation_id",
            sa.String(length=100),
            sa.ForeignKey("invitations.invitation_id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "share_version_id",
            sa.String(length=100),
            sa.ForeignKey("share_versions.share_version_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=100), nullable=False, unique=True),
        sa.Column("token_hint", sa.String(length=20), nullable=False),
        sa.Column("scope", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("revoked_at", sa.String(length=40), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')", name="ck_grant_status"
        ),
        sa.CheckConstraint("scope = 'snapshot.read'", name="ck_grant_scope"),
    )
    op.create_index("ix_grants_expires_at", "grants", ["expires_at"])
    op.create_table(
        "sharing_events",
        sa.Column("event_id", sa.String(length=100), primary_key=True),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("snapshot_id", sa.String(length=100), nullable=True),
        sa.Column("share_id", sa.String(length=100), nullable=True),
        sa.Column("share_version_id", sa.String(length=100), nullable=True),
        sa.Column("invitation_id", sa.String(length=100), nullable=True),
        sa.Column("grant_id", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
    )
    op.create_index("ix_sharing_events_created_at", "sharing_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_sharing_events_created_at", table_name="sharing_events")
    op.drop_table("sharing_events")
    op.drop_index("ix_grants_expires_at", table_name="grants")
    op.drop_table("grants")
    op.drop_index("ix_invitations_expires_at", table_name="invitations")
    op.drop_table("invitations")
    op.drop_table("share_versions")
    op.drop_table("shares")
    op.drop_table("publication_events")
    op.drop_table("snapshot_lifecycle")
    op.drop_table("snapshot_payloads")
    op.drop_table("snapshots")
