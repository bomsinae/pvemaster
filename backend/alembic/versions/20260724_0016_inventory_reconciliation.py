"""Add inventory generations and reconciliation findings.

Revision ID: 20260724_0016
Revises: 20260724_0015
Create Date: 2026-07-24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260724_0016"
down_revision: str | None = "20260724_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "clusters",
        sa.Column("last_sync_succeeded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "clusters",
        sa.Column(
            "sync_interval_seconds",
            sa.Integer(),
            server_default="60",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_clusters_sync_interval_seconds",
        "clusters",
        "sync_interval_seconds >= 15",
    )

    op.add_column(
        "workloads",
        sa.Column(
            "sync_generation",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "workloads",
        sa.Column("missing_since", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_workloads_cluster_present_kind",
        "workloads",
        ["cluster_id", "is_present", "kind"],
    )
    op.create_index("ix_workloads_observed_at", "workloads", ["observed_at"])

    op.drop_constraint("ck_sync_runs_status", "sync_runs", type_="check")
    op.create_check_constraint(
        "ck_sync_runs_status",
        "sync_runs",
        "status IN ('QUEUED','RUNNING','SUCCEEDED','PARTIAL','FAILED','SKIPPED')",
    )
    op.add_column(
        "sync_runs",
        sa.Column("scope", sa.String(length=16), server_default="FULL", nullable=False),
    )
    op.add_column(
        "sync_runs",
        sa.Column("target_workload_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "sync_runs",
        sa.Column(
            "partial_failure",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        "fk_sync_runs_target_workload_id",
        "sync_runs",
        "workloads",
        ["target_workload_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "uq_sync_runs_active_full_cluster",
        "sync_runs",
        ["cluster_id"],
        unique=True,
        postgresql_where=sa.text("scope = 'FULL' AND status IN ('QUEUED', 'RUNNING')"),
    )
    op.create_index(
        "uq_sync_runs_active_target",
        "sync_runs",
        ["target_workload_id"],
        unique=True,
        postgresql_where=sa.text(
            "target_workload_id IS NOT NULL AND status IN ('QUEUED', 'RUNNING')"
        ),
    )

    op.create_table(
        "nodes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("cluster_id", sa.Uuid(), nullable=False),
        sa.Column("pve_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("cpu_total", sa.Integer()),
        sa.Column("cpu_usage", sa.Float()),
        sa.Column("memory_total_bytes", sa.BigInteger()),
        sa.Column("memory_used_bytes", sa.BigInteger()),
        sa.Column("uptime_seconds", sa.BigInteger()),
        sa.Column("pve_version", sa.String(length=64)),
        sa.Column("raw_facts", sa.JSON(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sync_generation", sa.BigInteger(), nullable=False),
        sa.Column("is_present", sa.Boolean(), nullable=False),
        sa.Column("missing_since", sa.DateTime(timezone=True)),
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
        sa.ForeignKeyConstraint(["cluster_id"], ["clusters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cluster_id", "pve_name"),
    )
    op.create_index("ix_nodes_cluster_present", "nodes", ["cluster_id", "is_present"])
    op.create_index("ix_nodes_observed_at", "nodes", ["observed_at"])

    op.create_table(
        "inventory_storages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("cluster_id", sa.Uuid(), nullable=False),
        sa.Column("natural_key", sa.String(length=520), nullable=False),
        sa.Column("storage_id", sa.String(length=255), nullable=False),
        sa.Column("node", sa.String(length=255)),
        sa.Column("storage_type", sa.String(length=64)),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("total_bytes", sa.BigInteger()),
        sa.Column("used_bytes", sa.BigInteger()),
        sa.Column("available_bytes", sa.BigInteger()),
        sa.Column("shared", sa.Boolean(), nullable=False),
        sa.Column("content", sa.String(length=500)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sync_generation", sa.BigInteger(), nullable=False),
        sa.Column("is_present", sa.Boolean(), nullable=False),
        sa.Column("missing_since", sa.DateTime(timezone=True)),
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
        sa.ForeignKeyConstraint(["cluster_id"], ["clusters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cluster_id", "natural_key"),
    )
    op.create_index(
        "ix_inventory_storages_cluster_present",
        "inventory_storages",
        ["cluster_id", "is_present"],
    )

    op.create_table(
        "reconciliation_findings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("cluster_id", sa.Uuid(), nullable=False),
        sa.Column("workload_id", sa.Uuid()),
        sa.Column("sync_run_id", sa.Uuid()),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("summary", sa.String(length=500), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_by_id", sa.Uuid()),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("assigned_to_id", sa.Uuid()),
        sa.Column("resolved_by_id", sa.Uuid()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolution_note", sa.String(length=1000)),
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
            "severity IN ('INFO','WARNING','CRITICAL')",
            name="ck_reconciliation_findings_severity",
        ),
        sa.CheckConstraint(
            "status IN ('OPEN','ACKNOWLEDGED','RESOLVED')",
            name="ck_reconciliation_findings_status",
        ),
        sa.ForeignKeyConstraint(["cluster_id"], ["clusters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workload_id"], ["workloads.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["sync_run_id"], ["sync_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["acknowledged_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["assigned_to_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolved_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fingerprint"),
    )
    op.create_index(
        "ix_reconciliation_findings_status_severity",
        "reconciliation_findings",
        ["status", "severity"],
    )
    op.create_index(
        "ix_reconciliation_findings_cluster_observed",
        "reconciliation_findings",
        ["cluster_id", "last_observed_at"],
    )

    op.create_table(
        "workload_change_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("workload_id", sa.Uuid(), nullable=False),
        sa.Column("sync_run_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("before", sa.JSON(), nullable=False),
        sa.Column("after", sa.JSON(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workload_id"], ["workloads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sync_run_id"], ["sync_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workload_change_events_workload_observed",
        "workload_change_events",
        ["workload_id", "observed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workload_change_events_workload_observed",
        table_name="workload_change_events",
    )
    op.drop_table("workload_change_events")
    op.drop_index(
        "ix_reconciliation_findings_cluster_observed",
        table_name="reconciliation_findings",
    )
    op.drop_index(
        "ix_reconciliation_findings_status_severity",
        table_name="reconciliation_findings",
    )
    op.drop_table("reconciliation_findings")
    op.drop_index(
        "ix_inventory_storages_cluster_present",
        table_name="inventory_storages",
    )
    op.drop_table("inventory_storages")
    op.drop_index("ix_nodes_observed_at", table_name="nodes")
    op.drop_index("ix_nodes_cluster_present", table_name="nodes")
    op.drop_table("nodes")

    op.drop_index("uq_sync_runs_active_target", table_name="sync_runs")
    op.drop_index("uq_sync_runs_active_full_cluster", table_name="sync_runs")
    op.drop_constraint("fk_sync_runs_target_workload_id", "sync_runs", type_="foreignkey")
    op.drop_column("sync_runs", "partial_failure")
    op.drop_column("sync_runs", "target_workload_id")
    op.drop_column("sync_runs", "scope")
    op.drop_constraint("ck_sync_runs_status", "sync_runs", type_="check")
    op.execute("UPDATE sync_runs SET status = 'FAILED' WHERE status IN ('QUEUED', 'PARTIAL')")
    op.create_check_constraint(
        "ck_sync_runs_status",
        "sync_runs",
        "status IN ('RUNNING','SUCCEEDED','FAILED','SKIPPED')",
    )

    op.drop_index("ix_workloads_observed_at", table_name="workloads")
    op.drop_index("ix_workloads_cluster_present_kind", table_name="workloads")
    op.drop_column("workloads", "missing_since")
    op.drop_column("workloads", "sync_generation")
    op.drop_constraint("ck_clusters_sync_interval_seconds", "clusters", type_="check")
    op.drop_column("clusters", "sync_interval_seconds")
    op.drop_column("clusters", "last_sync_succeeded_at")
