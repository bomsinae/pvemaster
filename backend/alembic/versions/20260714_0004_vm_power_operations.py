"""Add VM power operations and PVE task tracking.

Revision ID: 20260714_0004
Revises: 20260714_0003
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260714_0004"
down_revision: str | None = "20260714_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workloads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("cluster_id", sa.Uuid(), nullable=False),
        sa.Column("vmid", sa.Integer(), nullable=False),
        sa.Column("node", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=255)),
        sa.Column("power_state", sa.String(length=20), nullable=False),
        sa.Column("is_template", sa.Boolean(), nullable=False),
        sa.Column("is_present", sa.Boolean(), nullable=False),
        sa.Column("organization_id", sa.Uuid()),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("kind IN ('QEMU', 'LXC')", name="ck_workloads_kind"),
        sa.ForeignKeyConstraint(["cluster_id"], ["clusters.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cluster_id", "vmid"),
    )
    op.create_table(
        "operations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation_type", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("requested_by_id", sa.Uuid(), nullable=False),
        sa.Column("source_ip", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.Uuid()),
        sa.Column("cluster_id", sa.Uuid(), nullable=False),
        sa.Column("workload_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("request_fingerprint", sa.LargeBinary(length=32), nullable=False),
        sa.Column("celery_task_id", sa.String(length=255), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=64)),
        sa.Column("error_summary", sa.Text()),
        sa.Column("retryable", sa.Boolean()),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["cluster_id"], ["clusters.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["workload_id"], ["workloads.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("requested_by_id", "idempotency_key_hash"),
    )
    op.create_index(
        "ix_operations_requester_requested",
        "operations",
        ["requested_by_id", "requested_at"],
    )
    op.create_index(
        "uq_operations_active_workload",
        "operations",
        ["workload_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('QUEUED', 'RUNNING')"),
    )
    op.create_table(
        "pve_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("cluster_id", sa.Uuid(), nullable=False),
        sa.Column("workload_id", sa.Uuid(), nullable=False),
        sa.Column("step_name", sa.String(length=64), nullable=False),
        sa.Column("upid", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("pve_node", sa.String(length=255), nullable=False),
        sa.Column("pve_exit_status", sa.String(length=255)),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_polled_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("poll_attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=64)),
        sa.Column("error_summary", sa.Text()),
        sa.ForeignKeyConstraint(["cluster_id"], ["clusters.id"]),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workload_id"], ["workloads.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cluster_id", "upid"),
        sa.UniqueConstraint("operation_id", "step_name", "upid"),
    )
    op.add_column("audit_logs", sa.Column("organization_id", sa.Uuid()))
    op.add_column("audit_logs", sa.Column("workload_id", sa.Uuid()))
    op.add_column("audit_logs", sa.Column("operation_id", sa.Uuid()))
    op.add_column("audit_logs", sa.Column("source_ip", sa.String(length=64)))
    op.add_column("audit_logs", sa.Column("pve_upid", sa.Text()))
    op.create_foreign_key(
        "fk_audit_logs_organization_id",
        "audit_logs",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_audit_logs_workload_id",
        "audit_logs",
        "workloads",
        ["workload_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_audit_logs_operation_id",
        "audit_logs",
        "operations",
        ["operation_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_audit_logs_operation_id", "audit_logs", type_="foreignkey")
    op.drop_constraint("fk_audit_logs_workload_id", "audit_logs", type_="foreignkey")
    op.drop_constraint("fk_audit_logs_organization_id", "audit_logs", type_="foreignkey")
    op.drop_column("audit_logs", "pve_upid")
    op.drop_column("audit_logs", "source_ip")
    op.drop_column("audit_logs", "operation_id")
    op.drop_column("audit_logs", "workload_id")
    op.drop_column("audit_logs", "organization_id")
    op.drop_table("pve_tasks")
    op.drop_index("uq_operations_active_workload", table_name="operations")
    op.drop_index("ix_operations_requester_requested", table_name="operations")
    op.drop_table("operations")
    op.drop_table("workloads")
