"""Harden provisioning execution and IP reservation uniqueness.

Revision ID: 20260714_0008
Revises: 20260714_0007
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260714_0008"
down_revision: str | None = "20260714_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("provisioning_requests", sa.Column("runner_id", sa.Uuid()))
    op.add_column(
        "provisioning_requests",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_provisioning_requests_recovery_lease",
        "provisioning_requests",
        ["status", "lease_expires_at"],
    )
    op.create_index(
        "uq_ip_allocations_active_provisioning_request",
        "ip_allocations",
        ["provisioning_request_id"],
        unique=True,
        postgresql_where=sa.text(
            "provisioning_request_id IS NOT NULL "
            "AND status IN ('RESERVED','ASSIGNED','QUARANTINED')"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_ip_allocations_active_provisioning_request",
        table_name="ip_allocations",
    )
    op.drop_index("ix_provisioning_requests_recovery_lease", table_name="provisioning_requests")
    op.drop_column("provisioning_requests", "lease_expires_at")
    op.drop_column("provisioning_requests", "runner_id")
