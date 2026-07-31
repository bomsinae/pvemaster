"""Add backup policies, assignments, and verification records.

Revision ID: 20260726_0020
Revises: 20260726_0019
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260726_0020"
down_revision: str | None = "20260726_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backup_policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("backup_target_id", sa.Uuid(), nullable=False),
        sa.Column("schedule", sa.String(120), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("mode", sa.String(16), nullable=False, server_default="snapshot"),
        sa.Column("retention_reference", sa.String(255)),
        sa.Column("verification_interval_days", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("skip_next_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("mode = 'snapshot'", name="ck_backup_policies_mode"),
        sa.ForeignKeyConstraint(["backup_target_id"], ["backup_targets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "backup_policy_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid()),
        sa.Column("workload_id", sa.Uuid()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "(organization_id IS NOT NULL AND workload_id IS NULL) OR "
            "(organization_id IS NULL AND workload_id IS NOT NULL)",
            name="ck_backup_policy_assignments_scope",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["policy_id"], ["backup_policies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workload_id"], ["workloads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("policy_id", "organization_id"),
        sa.UniqueConstraint("policy_id", "workload_id"),
    )
    op.create_index(
        "ix_backup_policy_assignments_policy",
        "backup_policy_assignments",
        ["policy_id"],
    )
    op.add_column(
        "backup_runs",
        sa.Column("policy_assignment_id", sa.Uuid()),
    )
    op.add_column(
        "backup_runs",
        sa.Column("scheduled_for", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "backup_runs",
        sa.Column("trigger_type", sa.String(16), nullable=False, server_default="MANUAL"),
    )
    op.create_foreign_key(
        "fk_backup_runs_policy_assignment",
        "backup_runs",
        "backup_policy_assignments",
        ["policy_assignment_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_table(
        "backup_verifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("backup_run_id", sa.Uuid(), nullable=False),
        sa.Column("restore_run_id", sa.Uuid()),
        sa.Column("verification_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("snapshot_volume_id", sa.String(1024), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(100)),
        sa.Column("result_summary", sa.Text()),
        sa.Column("requested_by_id", sa.Uuid()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["backup_run_id"], ["backup_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["restore_run_id"], ["restore_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_backup_verifications_run_created",
        "backup_verifications",
        ["backup_run_id", "created_at"],
    )
    op.create_index(
        "ix_backup_verifications_status_due",
        "backup_verifications",
        ["status", "due_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_backup_verifications_status_due", table_name="backup_verifications")
    op.drop_index("ix_backup_verifications_run_created", table_name="backup_verifications")
    op.drop_table("backup_verifications")
    op.drop_constraint("fk_backup_runs_policy_assignment", "backup_runs", type_="foreignkey")
    op.drop_column("backup_runs", "trigger_type")
    op.drop_column("backup_runs", "scheduled_for")
    op.drop_column("backup_runs", "policy_assignment_id")
    op.drop_index(
        "ix_backup_policy_assignments_policy",
        table_name="backup_policy_assignments",
    )
    op.drop_table("backup_policy_assignments")
    op.drop_table("backup_policies")
