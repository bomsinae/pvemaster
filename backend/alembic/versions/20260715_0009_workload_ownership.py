"""Add workload ownership history for imported PVE resources.

Revision ID: 20260715_0009
Revises: 20260714_0008
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260715_0009"
down_revision: str | None = "20260714_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workload_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workload_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_by_id", sa.Uuid(), nullable=False),
        sa.Column(
            "assigned_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("revoked_by_id", sa.Uuid()),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoke_reason", sa.String(length=500)),
        sa.ForeignKeyConstraint(["assigned_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["revoked_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["workload_id"], ["workloads.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_workload_assignments_active_workload",
        "workload_assignments",
        ["workload_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "ix_workload_assignments_organization",
        "workload_assignments",
        ["organization_id", "assigned_at"],
    )
    op.execute(
        """
        INSERT INTO workload_assignments (
            id, workload_id, organization_id, assigned_by_id, assigned_at
        )
        SELECT gen_random_uuid(), w.id, w.organization_id, o.created_by_id,
               COALESCE(w.updated_at, w.created_at, now())
        FROM workloads AS w
        JOIN organizations AS o ON o.id = w.organization_id
        WHERE w.organization_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_workload_assignments_organization", table_name="workload_assignments")
    op.drop_index("uq_workload_assignments_active_workload", table_name="workload_assignments")
    op.drop_table("workload_assignments")
