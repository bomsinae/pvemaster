"""Add transactional IPv4 and IPv6 address management.

Revision ID: 20260714_0005
Revises: 20260714_0004
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260714_0005"
down_revision: str | None = "20260714_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ip_pools",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("cluster_id", sa.Uuid()),
        sa.Column("cidr", postgresql.CIDR(), nullable=False),
        sa.Column("gateway", postgresql.INET()),
        sa.Column("dns_servers", postgresql.ARRAY(postgresql.INET()), nullable=False),
        sa.Column("bridge", sa.String(length=64), nullable=False),
        sa.Column("vlan_tag", sa.Integer()),
        sa.Column("ip_family", sa.Integer(), nullable=False),
        sa.Column("allocation_strategy", sa.String(length=20), nullable=False),
        sa.Column("quarantine_seconds", sa.Integer(), nullable=False),
        sa.Column("next_offset", sa.Numeric(precision=39, scale=0), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("ip_family IN (4, 6)", name="ck_ip_pools_family"),
        sa.CheckConstraint(
            "allocation_strategy IN ('SEQUENTIAL', 'RANDOM')", name="ck_ip_pools_strategy"
        ),
        sa.CheckConstraint("quarantine_seconds >= 0", name="ck_ip_pools_quarantine"),
        sa.CheckConstraint(
            "vlan_tag IS NULL OR vlan_tag BETWEEN 1 AND 4094", name="ck_ip_pools_vlan"
        ),
        sa.ForeignKeyConstraint(["cluster_id"], ["clusters.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "ip_pool_exclusions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pool_id", sa.Uuid(), nullable=False),
        sa.Column("start_address", postgresql.INET(), nullable=False),
        sa.Column("end_address", postgresql.INET(), nullable=False),
        sa.Column("reason", sa.String(length=255)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["pool_id"], ["ip_pools.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pool_id", "start_address", "end_address"),
    )
    op.create_table(
        "ip_addresses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pool_id", sa.Uuid(), nullable=False),
        sa.Column("address", postgresql.INET(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("reserved_for", sa.String(length=255)),
        sa.Column("quarantined_until", sa.DateTime(timezone=True)),
        sa.Column("last_allocated_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "state IN ('AVAILABLE','RESERVED','ASSIGNED','QUARANTINED','DISABLED')",
            name="ck_ip_addresses_state",
        ),
        sa.ForeignKeyConstraint(["pool_id"], ["ip_pools.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pool_id", "address"),
    )
    op.create_index(
        "ix_ip_addresses_pool_state_address",
        "ip_addresses",
        ["pool_id", "state", "address"],
    )
    op.create_table(
        "ip_allocations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ip_address_id", sa.Uuid(), nullable=False),
        sa.Column("workload_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("allocated_by_id", sa.Uuid(), nullable=False),
        sa.Column(
            "allocated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("release_reason", sa.Text()),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("kind IN ('AUTOMATIC','MANUAL')", name="ck_ip_allocations_kind"),
        sa.CheckConstraint(
            "status IN ('RESERVED','ASSIGNED','QUARANTINED','RELEASED')",
            name="ck_ip_allocations_status",
        ),
        sa.ForeignKeyConstraint(["allocated_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["ip_address_id"], ["ip_addresses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workload_id"], ["workloads.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ip_allocations_workload_status",
        "ip_allocations",
        ["workload_id", "status"],
    )
    op.create_index(
        "uq_ip_allocations_active_address",
        "ip_allocations",
        ["ip_address_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('RESERVED','ASSIGNED','QUARANTINED')"),
    )


def downgrade() -> None:
    op.drop_index("uq_ip_allocations_active_address", table_name="ip_allocations")
    op.drop_index("ix_ip_allocations_workload_status", table_name="ip_allocations")
    op.drop_table("ip_allocations")
    op.drop_index("ix_ip_addresses_pool_state_address", table_name="ip_addresses")
    op.drop_table("ip_addresses")
    op.drop_table("ip_pool_exclusions")
    op.drop_table("ip_pools")
