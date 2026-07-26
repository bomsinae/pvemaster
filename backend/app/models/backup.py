from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base


class BackupTarget(Base):
    __tablename__ = "backup_targets"
    __table_args__ = (
        UniqueConstraint("cluster_id", "storage_id"),
        Index("ix_backup_targets_cluster_enabled", "cluster_id", "is_enabled"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    cluster_id: Mapped[UUID] = mapped_column(
        ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False
    )
    storage_id: Mapped[str] = mapped_column(String(255), nullable=False)
    datastore: Mapped[str | None] = mapped_column(String(255))
    namespace: Mapped[str | None] = mapped_column(String(255))
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_observed_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    version: Mapped[int] = mapped_column(nullable=False, default=1)


class BackupRun(Base):
    __tablename__ = "backup_runs"
    __table_args__ = (
        UniqueConstraint("operation_id"),
        Index("ix_backup_runs_workload_created", "workload_id", "created_at"),
        Index("ix_backup_runs_target_created", "backup_target_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    operation_id: Mapped[UUID] = mapped_column(
        ForeignKey("operations.id", ondelete="CASCADE"), nullable=False
    )
    backup_target_id: Mapped[UUID] = mapped_column(
        ForeignKey("backup_targets.id", ondelete="RESTRICT"), nullable=False
    )
    workload_id: Mapped[UUID] = mapped_column(
        ForeignKey("workloads.id", ondelete="RESTRICT"), nullable=False
    )
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL")
    )
    policy_assignment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("backup_policy_assignments.id", ondelete="SET NULL")
    )
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False, default="MANUAL")
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="snapshot")
    compression: Mapped[str] = mapped_column(String(16), nullable=False, default="zstd")
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    snapshot_volume_id: Mapped[str | None] = mapped_column(String(1024))
    snapshot_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    transferred_bytes: Mapped[int | None] = mapped_column(BigInteger)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RestoreRun(Base):
    __tablename__ = "restore_runs"
    __table_args__ = (
        UniqueConstraint("operation_id"),
        Index("ix_restore_runs_backup_created", "backup_run_id", "created_at"),
        Index(
            "uq_restore_runs_active_target",
            "cluster_id",
            "target_vmid",
            unique=True,
            postgresql_where=text("status IN ('QUEUED', 'RUNNING')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    operation_id: Mapped[UUID] = mapped_column(
        ForeignKey("operations.id", ondelete="CASCADE"), nullable=False
    )
    backup_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("backup_runs.id", ondelete="RESTRICT"), nullable=False
    )
    cluster_id: Mapped[UUID] = mapped_column(
        ForeignKey("clusters.id", ondelete="RESTRICT"), nullable=False
    )
    source_workload_id: Mapped[UUID] = mapped_column(
        ForeignKey("workloads.id", ondelete="RESTRICT"), nullable=False
    )
    target_node: Mapped[str] = mapped_column(String(255), nullable=False)
    target_vmid: Mapped[int] = mapped_column(Integer, nullable=False)
    target_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BackupPolicy(Base):
    __tablename__ = "backup_policies"
    __table_args__ = (
        UniqueConstraint("name"),
        CheckConstraint("mode = 'snapshot'", name="ck_backup_policies_mode"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    backup_target_id: Mapped[UUID] = mapped_column(
        ForeignKey("backup_targets.id", ondelete="RESTRICT"), nullable=False
    )
    schedule: Mapped[str] = mapped_column(String(120), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="snapshot")
    retention_reference: Mapped[str | None] = mapped_column(String(255))
    verification_interval_days: Mapped[int] = mapped_column(Integer, nullable=False, default=90)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    skip_next_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class BackupPolicyAssignment(Base):
    __tablename__ = "backup_policy_assignments"
    __table_args__ = (
        CheckConstraint(
            "(organization_id IS NOT NULL AND workload_id IS NULL) OR "
            "(organization_id IS NULL AND workload_id IS NOT NULL)",
            name="ck_backup_policy_assignments_scope",
        ),
        UniqueConstraint("policy_id", "organization_id"),
        UniqueConstraint("policy_id", "workload_id"),
        Index("ix_backup_policy_assignments_policy", "policy_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    policy_id: Mapped[UUID] = mapped_column(
        ForeignKey("backup_policies.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    workload_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workloads.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BackupVerification(Base):
    __tablename__ = "backup_verifications"
    __table_args__ = (
        Index("ix_backup_verifications_run_created", "backup_run_id", "created_at"),
        Index("ix_backup_verifications_status_due", "status", "due_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    backup_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("backup_runs.id", ondelete="CASCADE"), nullable=False
    )
    restore_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("restore_runs.id", ondelete="SET NULL")
    )
    verification_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    snapshot_volume_id: Mapped[str] = mapped_column(String(1024), nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    result_summary: Mapped[str | None] = mapped_column(Text)
    requested_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
