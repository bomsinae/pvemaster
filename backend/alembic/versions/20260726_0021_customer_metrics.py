"""Add customer workload metrics and uptime.

Revision ID: 20260726_0021
Revises: 20260726_0020
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260726_0021"
down_revision: str | None = "20260726_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("workloads", sa.Column("uptime_seconds", sa.BigInteger()))
    op.create_table(
        "workload_metrics",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workload_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("resolution_seconds", sa.Integer(), nullable=False),
        sa.Column("bucket_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("cpu_avg", sa.Float()),
        sa.Column("cpu_max", sa.Float()),
        sa.Column("memory_used_avg", sa.Float()),
        sa.Column("memory_used_max", sa.Float()),
        sa.Column("disk_read_avg", sa.Float()),
        sa.Column("disk_read_max", sa.Float()),
        sa.Column("disk_write_avg", sa.Float()),
        sa.Column("disk_write_max", sa.Float()),
        sa.Column("network_receive_avg", sa.Float()),
        sa.Column("network_receive_max", sa.Float()),
        sa.Column("network_transmit_avg", sa.Float()),
        sa.Column("network_transmit_max", sa.Float()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workload_id"], ["workloads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workload_id",
            "organization_id",
            "resolution_seconds",
            "bucket_at",
        ),
    )
    op.create_index(
        "ix_workload_metrics_scope_bucket",
        "workload_metrics",
        ["organization_id", "workload_id", "resolution_seconds", "bucket_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_workload_metrics_scope_bucket", table_name="workload_metrics")
    op.drop_table("workload_metrics")
    op.drop_column("workloads", "uptime_seconds")
