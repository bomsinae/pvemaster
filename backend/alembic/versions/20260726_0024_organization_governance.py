"""Add organization roles, invitations, quotas, and approval policies.

Revision ID: 20260726_0024
Revises: 20260726_0023
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260726_0024"
down_revision: str | None = "20260726_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organization_members",
        sa.Column(
            "organization_role",
            sa.String(length=24),
            nullable=False,
            server_default="ORG_OPERATOR",
        ),
    )
    op.add_column(
        "organization_members",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
    )
    op.add_column(
        "organization_members",
        sa.Column("expires_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "organization_members",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.add_column(
        "organization_members",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_check_constraint(
        "ck_organization_members_role",
        "organization_members",
        "organization_role IN "
        "('ORG_OWNER','ORG_ADMIN','ORG_OPERATOR','ORG_VIEWER','BILLING_VIEWER')",
    )
    op.create_check_constraint(
        "ck_organization_members_status",
        "organization_members",
        "status IN ('ACTIVE','SUSPENDED')",
    )
    op.create_index(
        "ix_organization_members_active",
        "organization_members",
        ["organization_id", "status", "expires_at"],
    )
    op.execute(
        """
        WITH ranked AS (
          SELECT id,
                 row_number() OVER (
                   PARTITION BY organization_id ORDER BY created_at, id
                 ) AS position
          FROM organization_members
        )
        UPDATE organization_members AS member
        SET organization_role = CASE
          WHEN ranked.position = 1 THEN 'ORG_OWNER'
          ELSE 'ORG_OPERATOR'
        END
        FROM ranked
        WHERE member.id = ranked.id
        """
    )

    op.create_table(
        "organization_invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("organization_role", sa.String(length=24), nullable=False),
        sa.Column("invited_by_id", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("accepted_by_id", sa.Uuid()),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "organization_role IN "
            "('ORG_OWNER','ORG_ADMIN','ORG_OPERATOR','ORG_VIEWER','BILLING_VIEWER')",
            name="ck_organization_invitations_role",
        ),
        sa.ForeignKeyConstraint(["accepted_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["invited_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "ix_organization_invitations_pending",
        "organization_invitations",
        ["organization_id", "accepted_at", "revoked_at", "expires_at"],
    )
    op.create_index(
        "uq_organization_invitations_pending_email",
        "organization_invitations",
        ["organization_id", "email"],
        unique=True,
        postgresql_where=sa.text("accepted_at IS NULL AND revoked_at IS NULL"),
    )

    op.create_table(
        "organization_quotas",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("max_vcpu", sa.Integer(), nullable=False),
        sa.Column("max_memory_bytes", sa.BigInteger(), nullable=False),
        sa.Column("max_disk_bytes", sa.BigInteger(), nullable=False),
        sa.Column("max_vms", sa.Integer(), nullable=False),
        sa.Column("max_ips", sa.Integer(), nullable=False),
        sa.Column("max_backup_bytes", sa.BigInteger(), nullable=False),
        sa.Column("updated_by_id", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "max_vcpu >= 0 AND max_memory_bytes >= 0 AND max_disk_bytes >= 0 "
            "AND max_vms >= 0 AND max_ips >= 0 AND max_backup_bytes >= 0",
            name="ck_organization_quotas_nonnegative",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("organization_id"),
    )

    op.create_table(
        "quota_usage_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("used_vcpu", sa.Integer(), nullable=False),
        sa.Column("used_memory_bytes", sa.BigInteger(), nullable=False),
        sa.Column("used_disk_bytes", sa.BigInteger(), nullable=False),
        sa.Column("used_vms", sa.Integer(), nullable=False),
        sa.Column("used_ips", sa.Integer(), nullable=False),
        sa.Column("used_backup_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_quota_usage_snapshots_organization_captured",
        "quota_usage_snapshots",
        ["organization_id", "captured_at"],
    )

    op.create_table(
        "quota_reservations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("provisioning_request_id", sa.Uuid()),
        sa.Column("service_request_id", sa.Uuid()),
        sa.Column("vcpu", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("memory_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("disk_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("vms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ips", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("backup_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "(provisioning_request_id IS NOT NULL) <> (service_request_id IS NOT NULL)",
            name="ck_quota_reservations_single_request",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','CONSUMED','RELEASED')",
            name="ck_quota_reservations_status",
        ),
        sa.CheckConstraint(
            "vcpu >= 0 AND memory_bytes >= 0 AND disk_bytes >= 0 "
            "AND vms >= 0 AND ips >= 0 AND backup_bytes >= 0",
            name="ck_quota_reservations_nonnegative",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["provisioning_request_id"],
            ["provisioning_requests.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["service_request_id"], ["service_requests.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provisioning_request_id"),
        sa.UniqueConstraint("service_request_id"),
    )
    op.create_index(
        "ix_quota_reservations_organization_active",
        "quota_reservations",
        ["organization_id", "status"],
    )

    op.create_table(
        "approval_policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("request_type", sa.String(length=40), nullable=False),
        sa.Column(
            "requires_approval",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("minimum_role", sa.String(length=24), nullable=False),
        sa.Column("updated_by_id", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "minimum_role IN ('ORG_OWNER','ORG_ADMIN','ORG_OPERATOR')",
            name="ck_approval_policies_minimum_role",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "request_type"),
    )


def downgrade() -> None:
    op.drop_table("approval_policies")
    op.drop_index(
        "ix_quota_reservations_organization_active",
        table_name="quota_reservations",
    )
    op.drop_table("quota_reservations")
    op.drop_index(
        "ix_quota_usage_snapshots_organization_captured",
        table_name="quota_usage_snapshots",
    )
    op.drop_table("quota_usage_snapshots")
    op.drop_table("organization_quotas")
    op.drop_index(
        "uq_organization_invitations_pending_email",
        table_name="organization_invitations",
    )
    op.drop_index(
        "ix_organization_invitations_pending",
        table_name="organization_invitations",
    )
    op.drop_table("organization_invitations")
    op.drop_index(
        "ix_organization_members_active",
        table_name="organization_members",
    )
    op.drop_constraint(
        "ck_organization_members_status",
        "organization_members",
        type_="check",
    )
    op.drop_constraint(
        "ck_organization_members_role",
        "organization_members",
        type_="check",
    )
    op.drop_column("organization_members", "version")
    op.drop_column("organization_members", "updated_at")
    op.drop_column("organization_members", "expires_at")
    op.drop_column("organization_members", "status")
    op.drop_column("organization_members", "organization_role")
