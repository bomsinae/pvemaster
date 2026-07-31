"""Add operation center timeline, ownership, and recovery state.

Revision ID: 20260724_0017
Revises: 20260724_0016
Create Date: 2026-07-24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260724_0017"
down_revision: str | None = "20260724_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("operations", sa.Column("retry_of_id", sa.Uuid(), nullable=True))
    op.add_column("operations", sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "operations",
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE operations SET queued_at = requested_at WHERE queued_at IS NULL")
    op.create_foreign_key(
        "fk_operations_retry_of_id",
        "operations",
        "operations",
        ["retry_of_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint("uq_operations_retry_of_id", "operations", ["retry_of_id"])
    op.create_check_constraint(
        "ck_operations_status",
        "operations",
        "status IN ("
        "'QUEUED','RUNNING','CANCEL_REQUESTED','SUCCEEDED','FAILED','TIMEOUT',"
        "'CANCELLED','NEEDS_ATTENTION'"
        ")",
    )
    op.drop_index("uq_operations_active_workload", table_name="operations")
    op.create_index(
        "uq_operations_active_workload",
        "operations",
        ["workload_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('QUEUED', 'RUNNING', 'CANCEL_REQUESTED')"),
    )
    op.create_index(
        "ix_operations_cluster_status",
        "operations",
        ["cluster_id", "status"],
    )
    op.create_index(
        "ix_operations_status_heartbeat",
        "operations",
        ["status", "heartbeat_at"],
    )

    op.drop_constraint(
        "ck_provisioning_requests_status",
        "provisioning_requests",
        type_="check",
    )
    op.create_check_constraint(
        "ck_provisioning_requests_status",
        "provisioning_requests",
        "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED','MANUAL_REVIEW')",
    )
    op.add_column(
        "provisioning_requests",
        sa.Column("retry_of_request_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_provisioning_requests_retry_of_request_id",
        "provisioning_requests",
        "provisioning_requests",
        ["retry_of_request_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_provisioning_requests_retry_of_request_id",
        "provisioning_requests",
        ["retry_of_request_id"],
    )

    op.create_table(
        "operation_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=True),
        sa.Column("provisioning_request_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=True),
        sa.Column("step", sa.String(length=64), nullable=True),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(operation_id IS NOT NULL) <> (provisioning_request_id IS NOT NULL)",
            name="ck_operation_events_single_target",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["operations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["provisioning_request_id"],
            ["provisioning_requests.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_operation_events_operation_occurred",
        "operation_events",
        ["operation_id", "occurred_at"],
    )
    op.create_index(
        "ix_operation_events_provisioning_occurred",
        "operation_events",
        ["provisioning_request_id", "occurred_at"],
    )
    op.execute(
        """
        INSERT INTO operation_events (
            operation_id, event_type, status, message, details, actor_user_id, occurred_at
        )
        SELECT id, 'IMPORTED', status, 'Existing operation imported into operation center',
               '{}'::jsonb, requested_by_id, requested_at
        FROM operations
        """
    )
    op.execute(
        """
        INSERT INTO operation_events (
            provisioning_request_id, event_type, status, step, message, details,
            actor_user_id, occurred_at
        )
        SELECT id, 'IMPORTED', status, current_step,
               'Existing provisioning request imported into operation center',
               '{}'::jsonb, requested_by_id, requested_at
        FROM provisioning_requests
        """
    )

    op.create_table(
        "operation_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=True),
        sa.Column("provisioning_request_id", sa.Uuid(), nullable=True),
        sa.Column("assigned_to_id", sa.Uuid(), nullable=True),
        sa.Column("assigned_by_id", sa.Uuid(), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by_id", sa.Uuid(), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_id", sa.Uuid(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.String(length=1000), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(operation_id IS NOT NULL) <> (provisioning_request_id IS NOT NULL)",
            name="ck_operation_assignments_single_target",
        ),
        sa.ForeignKeyConstraint(["acknowledged_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["assigned_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["assigned_to_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["provisioning_request_id"],
            ["provisioning_requests.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["resolved_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id"),
        sa.UniqueConstraint("provisioning_request_id"),
    )


def downgrade() -> None:
    op.drop_table("operation_assignments")
    op.drop_index(
        "ix_operation_events_provisioning_occurred",
        table_name="operation_events",
    )
    op.drop_index(
        "ix_operation_events_operation_occurred",
        table_name="operation_events",
    )
    op.drop_table("operation_events")

    op.drop_constraint(
        "uq_provisioning_requests_retry_of_request_id",
        "provisioning_requests",
        type_="unique",
    )
    op.drop_constraint(
        "fk_provisioning_requests_retry_of_request_id",
        "provisioning_requests",
        type_="foreignkey",
    )
    op.drop_column("provisioning_requests", "retry_of_request_id")
    op.drop_constraint(
        "ck_provisioning_requests_status",
        "provisioning_requests",
        type_="check",
    )
    op.execute("UPDATE provisioning_requests SET status = 'FAILED' WHERE status = 'CANCELLED'")
    op.create_check_constraint(
        "ck_provisioning_requests_status",
        "provisioning_requests",
        "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','MANUAL_REVIEW')",
    )

    op.drop_index("ix_operations_status_heartbeat", table_name="operations")
    op.drop_index("ix_operations_cluster_status", table_name="operations")
    op.drop_index("uq_operations_active_workload", table_name="operations")
    op.execute(
        "UPDATE operations SET status = 'FAILED' "
        "WHERE status IN ('CANCEL_REQUESTED','CANCELLED','NEEDS_ATTENTION')"
    )
    op.create_index(
        "uq_operations_active_workload",
        "operations",
        ["workload_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('QUEUED', 'RUNNING')"),
    )
    op.drop_constraint("ck_operations_status", "operations", type_="check")
    op.drop_constraint("uq_operations_retry_of_id", "operations", type_="unique")
    op.drop_constraint("fk_operations_retry_of_id", "operations", type_="foreignkey")
    op.drop_column("operations", "cancel_requested_at")
    op.drop_column("operations", "queued_at")
    op.drop_column("operations", "retry_of_id")
