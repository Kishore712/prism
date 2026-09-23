"""Replace MCP-session binding with identity-bound OAuth grants.

Revision ID: 0004_phase7_identity_oauth
Revises: 0003_phase6_recipient_mcp
Create Date: 2026-09-21
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_phase7_identity_oauth"
down_revision = "0003_phase6_recipient_mcp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_recipient_sessions_expires_at", table_name="recipient_sessions")
    op.drop_table("recipient_sessions")
    with op.batch_alter_table("sharing_events") as batch:
        batch.drop_column("recipient_session_id")
        batch.add_column(sa.Column("detail", sa.String(length=50), nullable=True))

    with op.batch_alter_table("invitations") as batch:
        batch.add_column(
            sa.Column(
                "require_approval",
                sa.Boolean(),
                nullable=False,
                server_default="1",
            )
        )
        batch.add_column(sa.Column("recipient_hint", sa.String(length=200), nullable=True))

    with op.batch_alter_table("grants") as batch:
        batch.add_column(
            sa.Column(
                "approval",
                sa.String(length=20),
                nullable=False,
                server_default="approved",
            )
        )
        batch.add_column(sa.Column("principal_id", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("recipient_label", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("approved_at", sa.String(length=40), nullable=True))
        batch.create_check_constraint(
            "ck_grant_approval",
            "approval IN ('approved', 'pending', 'denied')",
        )

    op.create_table(
        "oauth_clients",
        sa.Column("client_id", sa.String(length=100), primary_key=True),
        sa.Column("client_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
    )
    op.create_table(
        "oauth_tickets",
        sa.Column("ticket_hash", sa.String(length=100), primary_key=True),
        sa.Column("client_id", sa.String(length=100), nullable=False),
        sa.Column("params_json", sa.Text(), nullable=False),
        sa.Column("grant_id", sa.String(length=100), nullable=True),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consumed", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("expires_at", sa.String(length=40), nullable=False),
    )
    op.create_index("ix_oauth_tickets_expires_at", "oauth_tickets", ["expires_at"])
    op.create_table(
        "oauth_codes",
        sa.Column("code_hash", sa.String(length=100), primary_key=True),
        sa.Column("client_id", sa.String(length=100), nullable=False),
        sa.Column(
            "grant_id",
            sa.String(length=100),
            sa.ForeignKey("grants.grant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("principal_id", sa.String(length=100), nullable=False),
        sa.Column("code_challenge", sa.String(length=200), nullable=False),
        sa.Column("redirect_uri", sa.String(length=2000), nullable=False),
        sa.Column("redirect_uri_provided_explicitly", sa.Boolean(), nullable=False),
        sa.Column("resource", sa.String(length=2000), nullable=True),
        sa.Column("scopes_json", sa.Text(), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("expires_at", sa.String(length=40), nullable=False),
    )
    op.create_index("ix_oauth_codes_expires_at", "oauth_codes", ["expires_at"])
    op.create_table(
        "oauth_tokens",
        sa.Column("token_hash", sa.String(length=100), primary_key=True),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("client_id", sa.String(length=100), nullable=False),
        sa.Column(
            "grant_id",
            sa.String(length=100),
            sa.ForeignKey("grants.grant_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("principal_id", sa.String(length=100), nullable=False),
        sa.Column("resource", sa.String(length=2000), nullable=True),
        sa.Column("scopes_json", sa.Text(), nullable=False),
        sa.Column("family_id", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("expires_at", sa.String(length=40), nullable=False),
        sa.Column("revoked_at", sa.String(length=40), nullable=True),
        sa.CheckConstraint("kind IN ('access', 'refresh')", name="ck_oauth_token_kind"),
    )
    op.create_index("ix_oauth_tokens_grant_id", "oauth_tokens", ["grant_id"])
    op.create_index("ix_oauth_tokens_expires_at", "oauth_tokens", ["expires_at"])

    op.create_table(
        "draft_attachments",
        sa.Column("attachment_id", sa.String(length=100), primary_key=True),
        sa.Column(
            "draft_id",
            sa.String(length=100),
            sa.ForeignKey("share_drafts.draft_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(length=500), nullable=False),
        sa.Column("media_type", sa.String(length=100), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=100), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
    )
    op.create_index(
        "ix_draft_attachments_draft",
        "draft_attachments",
        ["draft_id", "ordinal"],
    )


def downgrade() -> None:
    op.drop_table("draft_attachments")
    op.drop_table("oauth_tokens")
    op.drop_table("oauth_codes")
    op.drop_table("oauth_tickets")
    op.drop_table("oauth_clients")
    with op.batch_alter_table("grants") as batch:
        batch.drop_constraint("ck_grant_approval", type_="check")
        batch.drop_column("approved_at")
        batch.drop_column("recipient_label")
        batch.drop_column("principal_id")
        batch.drop_column("approval")
    with op.batch_alter_table("invitations") as batch:
        batch.drop_column("recipient_hint")
        batch.drop_column("require_approval")
    with op.batch_alter_table("sharing_events") as batch:
        batch.drop_column("detail")
        batch.add_column(
            sa.Column("recipient_session_id", sa.String(length=100), nullable=True)
        )
    op.create_table(
        "recipient_sessions",
        sa.Column("session_id", sa.String(length=100), primary_key=True),
        sa.Column(
            "grant_id",
            sa.String(length=100),
            sa.ForeignKey("grants.grant_id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("binding_hash", sa.String(length=100), nullable=False, unique=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("revoked_at", sa.String(length=40), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_recipient_session_status",
        ),
    )
    op.create_index(
        "ix_recipient_sessions_expires_at",
        "recipient_sessions",
        ["expires_at"],
    )
