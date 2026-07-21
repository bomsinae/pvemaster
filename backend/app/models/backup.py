from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
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
