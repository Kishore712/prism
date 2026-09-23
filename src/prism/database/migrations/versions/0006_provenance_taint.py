"""Add Phase 9's provenance timeline and taint decisions.

Revision ID: 0006_provenance_taint
Revises: 0005_projection_layer
Create Date: 2026-09-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006_provenance_taint"
down_revision = "0005_projection_layer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "capture_tool_events",
        sa.Column("event_id", sa.String(length=100), primary_key=True),
        sa.Column(
            "capture_id",
            sa.String(length=100),
            sa.ForeignKey("captures.capture_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column("resource_label", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
    )
    op.create_index(
        "ix_capture_tool_events_capture",
        "capture_tool_events",
        ["capture_id", "turn_index"],
    )

    op.create_table(
        "draft_tainted_events",
        sa.Column(
            "draft_id",
            sa.String(length=100),
            sa.ForeignKey("share_drafts.draft_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "event_id",
            sa.String(length=100),
            sa.ForeignKey("capture_tool_events.event_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("tainted_at", sa.String(length=40), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("draft_tainted_events")
    op.drop_index("ix_capture_tool_events_capture", table_name="capture_tool_events")
    op.drop_table("capture_tool_events")
