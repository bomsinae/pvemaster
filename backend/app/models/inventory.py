from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base


class FindingKind(StrEnum):
    EXTERNAL_DELETE = "EXTERNAL_DELETE"
    NODE_MOVED = "NODE_MOVED"
    SPEC_DRIFT = "SPEC_DRIFT"
    POWER_STATE_DRIFT = "POWER_STATE_DRIFT"


class FindingSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class FindingStatus(StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class InventoryNode(Base):
    __tablename__ = "nodes"
    __table_args__ = (
        UniqueConstraint("cluster_id", "pve_name"),
        Index("ix_nodes_cluster_present", "cluster_id", "is_present"),
        Index("ix_nodes_observed_at", "observed_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    cluster_id: Mapped[UUID] = mapped_column(
        ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False
    )
    pve_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")
    cpu_total: Mapped[int | None] = mapped_column(Integer)
    cpu_usage: Mapped[float | None] = mapped_column(Float)
    memory_total_bytes: Mapped[int | None] = mapped_column(BigInteger)
    memory_used_bytes: Mapped[int | None] = mapped_column(BigInteger)
    uptime_seconds: Mapped[int | None] = mapped_column(BigInteger)
    pve_version: Mapped[str | None] = mapped_column(String(64))
    raw_facts: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sync_generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    is_present: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    missing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class InventoryStorage(Base):
    __tablename__ = "inventory_storages"
    __table_args__ = (
        UniqueConstraint("cluster_id", "natural_key"),
        Index("ix_inventory_storages_cluster_present", "cluster_id", "is_present"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    cluster_id: Mapped[UUID] = mapped_column(
        ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False
    )
    natural_key: Mapped[str] = mapped_column(String(520), nullable=False)
    storage_id: Mapped[str] = mapped_column(String(255), nullable=False)
    node: Mapped[str | None] = mapped_column(String(255))
    storage_type: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")
    total_bytes: Mapped[int | None] = mapped_column(BigInteger)
    used_bytes: Mapped[int | None] = mapped_column(BigInteger)
    available_bytes: Mapped[int | None] = mapped_column(BigInteger)
    shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    content: Mapped[str | None] = mapped_column(String(500))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sync_generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    is_present: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    missing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ReconciliationFinding(Base):
    __tablename__ = "reconciliation_findings"
    __table_args__ = (
        UniqueConstraint("fingerprint"),
        CheckConstraint(
            "severity IN ('INFO','WARNING','CRITICAL')",
            name="ck_reconciliation_findings_severity",
        ),
        CheckConstraint(
            "status IN ('OPEN','ACKNOWLEDGED','RESOLVED')",
            name="ck_reconciliation_findings_status",
        ),
        Index("ix_reconciliation_findings_status_severity", "status", "severity"),
        Index("ix_reconciliation_findings_cluster_observed", "cluster_id", "last_observed_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    cluster_id: Mapped[UUID] = mapped_column(
        ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False
    )
    workload_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workloads.id", ondelete="SET NULL")
    )
    sync_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sync_runs.id", ondelete="SET NULL")
    )
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[UUID] = mapped_column(nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    details: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    first_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acknowledged_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_to_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class WorkloadChangeEvent(Base):
    __tablename__ = "workload_change_events"
    __table_args__ = (
        Index("ix_workload_change_events_workload_observed", "workload_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workload_id: Mapped[UUID] = mapped_column(
        ForeignKey("workloads.id", ondelete="CASCADE"), nullable=False
    )
    sync_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("sync_runs.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    before: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    after: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
