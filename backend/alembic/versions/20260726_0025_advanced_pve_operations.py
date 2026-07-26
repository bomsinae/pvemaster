"""Add advanced PVE operation intents.

Revision ID: 20260726_0025
Revises: 20260726_0024
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260726_0025"
down_revision: str | None = "20260726_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "advanced_operation_intents",
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("feature", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("target_snapshot", sa.JSON(), nullable=False),
        sa.Column("options_snapshot", sa.JSON(), nullable=False),
        sa.Column("preview_snapshot", sa.JSON(), nullable=False),
        sa.Column("requested_state", sa.JSON(), nullable=False),
        sa.Column("observed_state", sa.JSON(), nullable=False),
        sa.Column("current_target_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "feature IN ('SNAPSHOT','MIGRATION','HA','NODE_MAINTENANCE',"
            "'BULK','GUEST_CONFIG','FIREWALL_SDN')",
            name="ck_advanced_operation_intents_feature",
        ),
        sa.CheckConstraint(
            "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','NEEDS_ATTENTION')",
            name="ck_advanced_operation_intents_status",
        ),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("operation_id"),
    )
    op.create_index(
        "ix_advanced_intents_feature_status",
        "advanced_operation_intents",
        ["feature", "status"],
    )
    op.create_table(
        "advanced_operation_targets",
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("workload_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["advanced_operation_intents.operation_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["workload_id"], ["workloads.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("operation_id", "workload_id"),
    )
    op.create_index(
        "uq_advanced_operation_targets_active_workload",
        "advanced_operation_targets",
        ["workload_id"],
        unique=True,
        postgresql_where=sa.text("active"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_advanced_operation_targets_active_workload",
        table_name="advanced_operation_targets",
    )
    op.drop_table("advanced_operation_targets")
    op.drop_index(
        "ix_advanced_intents_feature_status",
        table_name="advanced_operation_intents",
    )
    op.drop_table("advanced_operation_intents")
