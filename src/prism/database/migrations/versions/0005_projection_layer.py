"""Add the Sealed Projection layer: title override, finding decisions, receipts.

Revision ID: 0005_projection_layer
Revises: 0004_phase7_identity_oauth
Create Date: 2026-09-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_projection_layer"
down_revision = "0004_phase7_identity_oauth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("share_drafts") as batch:
        batch.add_column(sa.Column("title_override", sa.String(length=500), nullable=True))

    op.create_table(
        "draft_finding_decisions",
        sa.Column(
            "draft_id",
            sa.String(length=100),
            sa.ForeignKey("share_drafts.draft_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("finding_id", sa.String(length=100), primary_key=True),
        sa.Column("disposition", sa.String(length=20), nullable=False),
        sa.Column("finding_class", sa.String(length=30), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("decided_at", sa.String(length=40), nullable=False),
        sa.CheckConstraint("disposition = 'allow'", name="ck_finding_disposition"),
    )

    op.create_table(
        "snapshot_receipts",
        sa.Column(
            "snapshot_id",
            sa.String(length=100),
            sa.ForeignKey("snapshots.snapshot_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("capture_hash", sa.String(length=100), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=100), nullable=False),
        sa.Column("detector_versions_json", sa.Text(), nullable=False),
        sa.Column("finding_count", sa.Integer(), nullable=False),
        sa.Column("override_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("public_key_hex", sa.String(length=64), nullable=False),
        sa.Column("signature_hex", sa.String(length=128), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("snapshot_receipts")
    op.drop_table("draft_finding_decisions")
    with op.batch_alter_table("share_drafts") as batch:
        batch.drop_column("title_override")
