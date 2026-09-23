"""Add durable recipient sessions for the MCP boundary.

Revision ID: 0003_phase6_recipient_mcp
Revises: 0002_phase5_publication_sharing
Create Date: 2026-09-20
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_phase6_recipient_mcp"
down_revision = "0002_phase5_publication_sharing"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
    op.add_column(
        "sharing_events",
        sa.Column("recipient_session_id", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sharing_events", "recipient_session_id")
    op.drop_index(
        "ix_recipient_sessions_expires_at",
        table_name="recipient_sessions",
    )
    op.drop_table("recipient_sessions")
