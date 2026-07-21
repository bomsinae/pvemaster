"""Add PBS workload restore runs.

Revision ID: 20260721_0013
Revises: 20260721_0012
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260721_0013"
down_revision: str | None = "20260721_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "restore_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("backup_run_id", sa.Uuid(), nullable=False),
        sa.Column("cluster_id", sa.Uuid(), nullable=False),
        sa.Column("source_workload_id", sa.Uuid(), nullable=False),
        sa.Column("target_node", sa.String(length=255), nullable=False),
        sa.Column("target_vmid", sa.Integer(), nullable=False),
        sa.Column("target_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["backup_run_id"], ["backup_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["cluster_id"], ["clusters.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_workload_id"], ["workloads.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id"),
    )
    op.create_index(
        "ix_restore_runs_backup_created", "restore_runs", ["backup_run_id", "created_at"]
    )
    op.create_index(
        "uq_restore_runs_active_target",
        "restore_runs",
        ["cluster_id", "target_vmid"],
        unique=True,
        postgresql_where=sa.text("status IN ('QUEUED', 'RUNNING')"),
    )


def downgrade() -> None:
    op.drop_index("uq_restore_runs_active_target", table_name="restore_runs")
    op.drop_index("ix_restore_runs_backup_created", table_name="restore_runs")
    op.drop_table("restore_runs")
