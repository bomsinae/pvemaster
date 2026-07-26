"""Add approval-based customer self-service.

Revision ID: 20260726_0023
Revises: 20260726_0022
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260726_0023"
down_revision: str | None = "20260726_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ssh_public_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(length=80), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_user_id", "organization_id", "fingerprint"),
    )
    op.create_index(
        "ix_ssh_public_keys_owner_active",
        "ssh_public_keys",
        ["owner_user_id", "organization_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_table(
        "security_groups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid()),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("is_global", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
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
            "organization_id IS NOT NULL OR is_global = true",
            name="ck_security_groups_scope",
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "name"),
    )
    op.create_table(
        "organization_service_quotas",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column(
            "max_cpu_cores_per_vm", sa.Integer(), nullable=False, server_default="64"
        ),
        sa.Column(
            "max_memory_bytes_per_vm",
            sa.BigInteger(),
            nullable=False,
            server_default="549755813888",
        ),
        sa.Column(
            "max_disk_bytes_per_vm",
            sa.BigInteger(),
            nullable=False,
            server_default="17592186044416",
        ),
        sa.Column("max_pending_requests", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("organization_id"),
    )
    op.create_table(
        "service_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_type", sa.String(length=40), nullable=False),
        sa.Column("requested_by_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("workload_id", sa.Uuid(), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("impact_snapshot", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("idempotency_key_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("request_fingerprint", sa.LargeBinary(length=32), nullable=False),
        sa.Column("operation_id", sa.Uuid()),
        sa.Column("error_code", sa.String(length=64)),
        sa.Column("result_summary", sa.String(length=500)),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "status IN ('PENDING_APPROVAL','APPROVED','IN_PROGRESS','SUCCEEDED',"
            "'REJECTED','CANCELLED','NEEDS_ATTENTION')",
            name="ck_service_requests_status",
        ),
        sa.ForeignKeyConstraint(
            ["assignment_id"], ["workload_assignments.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["operation_id"], ["operations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workload_id"], ["workloads.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id"),
        sa.UniqueConstraint("requested_by_id", "idempotency_key_hash"),
    )
    op.create_index(
        "ix_service_requests_org_status",
        "service_requests",
        ["organization_id", "status", "requested_at"],
    )
    op.create_index(
        "uq_service_requests_active_type",
        "service_requests",
        ["workload_id", "request_type"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('PENDING_APPROVAL','APPROVED','IN_PROGRESS','NEEDS_ATTENTION')"
        ),
    )
    op.create_table(
        "approval_steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("service_request_id", sa.Uuid(), nullable=False),
        sa.Column("step_order", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("approver_role", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=20)),
        sa.Column("reason", sa.String(length=500)),
        sa.Column("decided_by_id", sa.Uuid()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["decided_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["service_request_id"], ["service_requests.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("service_request_id", "step_order"),
    )
    op.create_table(
        "workload_ssh_public_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workload_id", sa.Uuid(), nullable=False),
        sa.Column("ssh_public_key_id", sa.Uuid(), nullable=False),
        sa.Column(
            "applied_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["ssh_public_key_id"], ["ssh_public_keys.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["workload_id"], ["workloads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workload_id", "ssh_public_key_id"),
    )
    op.create_table(
        "workload_security_groups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workload_id", sa.Uuid(), nullable=False),
        sa.Column("security_group_id", sa.Uuid(), nullable=False),
        sa.Column("applied_by_request_id", sa.Uuid()),
        sa.Column(
            "applied_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["applied_by_request_id"],
            ["service_requests.id"],
            name="fk_workload_security_groups_service_request",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["security_group_id"], ["security_groups.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["workload_id"], ["workloads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workload_id", "security_group_id"),
    )


def downgrade() -> None:
    op.drop_table("workload_security_groups")
    op.drop_table("workload_ssh_public_keys")
    op.drop_table("approval_steps")
    op.drop_index("uq_service_requests_active_type", table_name="service_requests")
    op.drop_index("ix_service_requests_org_status", table_name="service_requests")
    op.drop_table("service_requests")
    op.drop_table("organization_service_quotas")
    op.drop_table("security_groups")
    op.drop_index("ix_ssh_public_keys_owner_active", table_name="ssh_public_keys")
    op.drop_table("ssh_public_keys")
