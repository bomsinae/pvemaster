"""Add resumable template-based VM provisioning.

Revision ID: 20260714_0006
Revises: 20260714_0005
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260714_0006"
down_revision: str | None = "20260714_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("cpu_cores", sa.Integer(), nullable=False),
        sa.Column("memory_bytes", sa.BigInteger(), nullable=False),
        sa.Column("disk_bytes", sa.BigInteger(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("cpu_cores > 0", name="ck_products_cpu"),
        sa.CheckConstraint("memory_bytes > 0", name="ck_products_memory"),
        sa.CheckConstraint("disk_bytes > 0", name="ck_products_disk"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "templates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("source_workload_id", sa.Uuid(), nullable=False),
        sa.Column("source_disk", sa.String(length=32), nullable=False),
        sa.Column("default_storage", sa.String(length=64), nullable=False),
        sa.Column("default_bridge", sa.String(length=64), nullable=False),
        sa.Column("default_vlan_tag", sa.Integer()),
        sa.Column("cloud_init_enabled", sa.Boolean(), nullable=False),
        sa.Column("linux_only", sa.Boolean(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["source_workload_id"], ["workloads.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("source_workload_id"),
    )
    op.create_table(
        "provisioning_nodes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("cluster_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("is_maintenance", sa.Boolean(), nullable=False),
        sa.Column("available_memory_bytes", sa.BigInteger(), nullable=False),
        sa.Column("available_storage_bytes", sa.BigInteger(), nullable=False),
        sa.Column("last_selected_at", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["cluster_id"], ["clusters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cluster_id", "name"),
    )
    op.create_table(
        "provisioning_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("request_fingerprint", sa.LargeBinary(length=32), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("target_cluster_id", sa.Uuid(), nullable=False),
        sa.Column("target_node_id", sa.Uuid()),
        sa.Column("target_vmid", sa.Integer()),
        sa.Column("target_name", sa.String(length=63), nullable=False),
        sa.Column("ip_pool_id", sa.Uuid(), nullable=False),
        sa.Column("requested_ip_address", sa.String(length=64)),
        sa.Column("ip_address_id", sa.Uuid()),
        sa.Column("workload_id", sa.Uuid()),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("current_step", sa.String(length=64), nullable=False),
        sa.Column("spec_snapshot", sa.JSON(), nullable=False),
        sa.Column("celery_task_id", sa.String(length=255), nullable=False),
        sa.Column("clone_submitted", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=64)),
        sa.Column("error_summary", sa.Text()),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','MANUAL_REVIEW')",
            name="ck_provisioning_requests_status",
        ),
        sa.ForeignKeyConstraint(["ip_address_id"], ["ip_addresses.id"]),
        sa.ForeignKeyConstraint(["ip_pool_id"], ["ip_pools.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["target_cluster_id"], ["clusters.id"]),
        sa.ForeignKeyConstraint(["target_node_id"], ["provisioning_nodes.id"]),
        sa.ForeignKeyConstraint(["template_id"], ["templates.id"]),
        sa.ForeignKeyConstraint(["workload_id"], ["workloads.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("requested_by_id", "idempotency_key_hash"),
    )
    op.create_index(
        "uq_provisioning_active_vmid",
        "provisioning_requests",
        ["target_cluster_id", "target_vmid"],
        unique=True,
        postgresql_where=sa.text(
            "target_vmid IS NOT NULL AND status IN ('QUEUED','RUNNING','SUCCEEDED','MANUAL_REVIEW')"
        ),
    )
    op.create_table(
        "provisioning_steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provisioning_request_id", sa.Uuid(), nullable=False),
        sa.Column("step_order", sa.Integer(), nullable=False),
        sa.Column("step_name", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("pve_upid", sa.Text()),
        sa.Column("safe_result", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=64)),
        sa.Column("error_summary", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('PENDING','RUNNING','SUCCEEDED','FAILED')",
            name="ck_provisioning_steps_status",
        ),
        sa.ForeignKeyConstraint(
            ["provisioning_request_id"], ["provisioning_requests.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provisioning_request_id", "step_name"),
        sa.UniqueConstraint("provisioning_request_id", "step_order"),
    )
    op.alter_column("ip_allocations", "workload_id", existing_type=sa.Uuid(), nullable=True)
    op.add_column("ip_allocations", sa.Column("provisioning_request_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_ip_allocations_provisioning_request_id",
        "ip_allocations",
        "provisioning_requests",
        ["provisioning_request_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_ip_allocations_provisioning_request_id", "ip_allocations", type_="foreignkey"
    )
    op.drop_column("ip_allocations", "provisioning_request_id")
    op.alter_column("ip_allocations", "workload_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_table("provisioning_steps")
    op.drop_index("uq_provisioning_active_vmid", table_name="provisioning_requests")
    op.drop_table("provisioning_requests")
    op.drop_table("provisioning_nodes")
    op.drop_table("templates")
    op.drop_table("products")
