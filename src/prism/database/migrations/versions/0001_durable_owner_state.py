"""Create durable owner state.

Revision ID: 0001_durable_owner_state
Revises:
Create Date: 2026-09-19
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_durable_owner_state"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "capture_imports",
        sa.Column("import_id", sa.String(length=100), primary_key=True),
        sa.Column("adapter_version", sa.String(length=100), nullable=False),
        sa.Column("candidate_json", sa.Text(), nullable=False),
        sa.Column("preview_hash", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("expires_at", sa.String(length=40), nullable=False),
    )
    op.create_index(
        "ix_capture_imports_expires_at",
        "capture_imports",
        ["expires_at"],
    )
    op.create_table(
        "captures",
        sa.Column("capture_id", sa.String(length=100), primary_key=True),
        sa.Column("schema_version", sa.String(length=100), nullable=False),
        sa.Column("platform", sa.String(length=50), nullable=False),
        sa.Column("method", sa.String(length=50), nullable=False),
        sa.Column("adapter_version", sa.String(length=100), nullable=False),
        sa.Column("source_fingerprint", sa.String(length=100), nullable=False),
        sa.Column("conversation_ref", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("captured_at", sa.String(length=40), nullable=False),
        sa.Column("capture_hash", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
    )
    op.create_index("ix_captures_capture_hash", "captures", ["capture_hash"])
    op.create_table(
        "capture_turns",
        sa.Column("turn_id", sa.String(length=100), primary_key=True),
        sa.Column(
            "capture_id",
            sa.String(length=100),
            sa.ForeignKey("captures.capture_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.UniqueConstraint("capture_id", "turn_index", name="uq_capture_turn_index"),
        sa.UniqueConstraint("capture_id", "turn_id", name="uq_capture_turn_identity"),
    )
    op.create_table(
        "capture_messages",
        sa.Column("message_id", sa.String(length=100), primary_key=True),
        sa.Column("capture_id", sa.String(length=100), nullable=False),
        sa.Column("turn_id", sa.String(length=100), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["capture_id", "turn_id"],
            ["capture_turns.capture_id", "capture_turns.turn_id"],
            ondelete="CASCADE",
            name="fk_message_capture_turn",
        ),
        sa.UniqueConstraint(
            "capture_id", "ordinal", name="uq_capture_message_ordinal"
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')", name="ck_capture_message_role"
        ),
    )
    op.create_table(
        "capture_resources",
        sa.Column("resource_id", sa.String(length=100), primary_key=True),
        sa.Column(
            "capture_id",
            sa.String(length=100),
            sa.ForeignKey("captures.capture_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(length=500), nullable=False),
        sa.Column("media_type", sa.String(length=500), nullable=False),
        sa.Column("availability", sa.String(length=50), nullable=False),
        sa.UniqueConstraint(
            "capture_id", "resource_id", name="uq_capture_resource_identity"
        ),
        sa.UniqueConstraint(
            "capture_id", "ordinal", name="uq_capture_resource_ordinal"
        ),
    )
    op.create_table(
        "capture_warnings",
        sa.Column("warning_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "capture_id",
            sa.String(length=100),
            sa.ForeignKey("captures.capture_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.UniqueConstraint(
            "capture_id", "ordinal", name="uq_capture_warning_ordinal"
        ),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'blocking')",
            name="ck_capture_warning_severity",
        ),
    )
    op.create_table(
        "share_drafts",
        sa.Column("draft_id", sa.String(length=100), primary_key=True),
        sa.Column(
            "capture_id",
            sa.String(length=100),
            sa.ForeignKey("captures.capture_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("previewed_revision", sa.Integer(), nullable=True),
        sa.Column("preview_hash", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.UniqueConstraint(
            "draft_id", "capture_id", name="uq_draft_capture_identity"
        ),
        sa.CheckConstraint("revision >= 1", name="ck_draft_revision"),
        sa.CheckConstraint(
            "status IN ('editing', 'published', 'abandoned')",
            name="ck_draft_status",
        ),
        sa.CheckConstraint(
            "((previewed_revision IS NULL AND preview_hash IS NULL) OR "
            "(previewed_revision = revision AND preview_hash IS NOT NULL))",
            name="ck_draft_preview_binding",
        ),
    )
    op.create_table(
        "draft_turn_selections",
        sa.Column("draft_id", sa.String(length=100), primary_key=True),
        sa.Column("turn_id", sa.String(length=100), primary_key=True),
        sa.Column("capture_id", sa.String(length=100), nullable=False),
        sa.Column("included", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["draft_id", "capture_id"],
            ["share_drafts.draft_id", "share_drafts.capture_id"],
            ondelete="CASCADE",
            name="fk_turn_selection_draft_capture",
        ),
        sa.ForeignKeyConstraint(
            ["capture_id", "turn_id"],
            ["capture_turns.capture_id", "capture_turns.turn_id"],
            ondelete="CASCADE",
            name="fk_turn_selection_capture_turn",
        ),
        sa.CheckConstraint(
            "included IN (0, 1)",
            name="ck_turn_selection_included",
        ),
    )
    op.create_table(
        "draft_resource_selections",
        sa.Column("draft_id", sa.String(length=100), primary_key=True),
        sa.Column("resource_id", sa.String(length=100), primary_key=True),
        sa.Column("capture_id", sa.String(length=100), nullable=False),
        sa.Column("included", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["draft_id", "capture_id"],
            ["share_drafts.draft_id", "share_drafts.capture_id"],
            ondelete="CASCADE",
            name="fk_resource_selection_draft_capture",
        ),
        sa.ForeignKeyConstraint(
            ["capture_id", "resource_id"],
            ["capture_resources.capture_id", "capture_resources.resource_id"],
            ondelete="CASCADE",
            name="fk_resource_selection_capture_resource",
        ),
        sa.CheckConstraint(
            "included IN (0, 1)",
            name="ck_resource_selection_included",
        ),
    )
    op.create_table(
        "legacy_imports",
        sa.Column("file_fingerprint", sa.String(length=100), primary_key=True),
        sa.Column(
            "capture_id",
            sa.String(length=100),
            sa.ForeignKey("captures.capture_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("imported_at", sa.String(length=40), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("legacy_imports")
    op.drop_table("draft_resource_selections")
    op.drop_table("draft_turn_selections")
    op.drop_table("share_drafts")
    op.drop_table("capture_warnings")
    op.drop_table("capture_resources")
    op.drop_table("capture_messages")
    op.drop_table("capture_turns")
    op.drop_index("ix_captures_capture_hash", table_name="captures")
    op.drop_table("captures")
    op.drop_index("ix_capture_imports_expires_at", table_name="capture_imports")
    op.drop_table("capture_imports")
