"""Add durable operation outbox and scheduler state.

Revision ID: 20260724_0015
Revises: 20260721_0014
Create Date: 2026-07-24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260724_0015"
down_revision: str | None = "20260721_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operation_outbox",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error_code", sa.String(length=64)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('PENDING','PUBLISHED')",
            name="ck_operation_outbox_status",
        ),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id", "event_type"),
    )
    op.create_index(
        "ix_operation_outbox_dispatch",
        "operation_outbox",
        ["status", "next_attempt_at", "created_at"],
    )
    op.create_table(
        "scheduler_leases",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_table(
        "maintenance_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("fencing_token", sa.BigInteger()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("processed_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=64)),
        sa.CheckConstraint(
            "status IN ('RUNNING','SUCCEEDED','FAILED','SKIPPED')",
            name="ck_maintenance_runs_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_maintenance_runs_job_started",
        "maintenance_runs",
        ["job_name", "started_at"],
    )
    op.create_table(
        "sync_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("cluster_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("triggered_by", sa.String(length=24), nullable=False),
        sa.Column("requested_by_id", sa.Uuid()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(length=64)),
        sa.Column("resource_counts", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "status IN ('RUNNING','SUCCEEDED','FAILED','SKIPPED')",
            name="ck_sync_runs_status",
        ),
        sa.ForeignKeyConstraint(["cluster_id"], ["clusters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cluster_id", "generation"),
    )
    op.create_index(
        "ix_sync_runs_cluster_started",
        "sync_runs",
        ["cluster_id", "started_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_sync_runs_cluster_started", table_name="sync_runs")
    op.drop_table("sync_runs")
    op.drop_index("ix_maintenance_runs_job_started", table_name="maintenance_runs")
    op.drop_table("maintenance_runs")
    op.drop_table("scheduler_leases")
    op.drop_index("ix_operation_outbox_dispatch", table_name="operation_outbox")
    op.drop_table("operation_outbox")
