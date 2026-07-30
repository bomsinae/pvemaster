from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base


class PowerAction(StrEnum):
    START = "start"
    SHUTDOWN = "shutdown"
    STOP = "stop"
    REBOOT = "reboot"
    RESET = "reset"


class AdminVmAction(StrEnum):
    UPDATE_SPEC = "update_spec"
    DELETE = "delete"


class OperationStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"


TERMINAL_OPERATION_STATUSES = {
    OperationStatus.SUCCEEDED,
    OperationStatus.FAILED,
    OperationStatus.TIMEOUT,
    OperationStatus.CANCELLED,
    OperationStatus.NEEDS_ATTENTION,
}


class Workload(Base):
    __tablename__ = "workloads"
    __table_args__ = (
        UniqueConstraint("cluster_id", "vmid"),
        CheckConstraint("kind IN ('QEMU', 'LXC')", name="ck_workloads_kind"),
        Index("ix_workloads_cluster_present_kind", "cluster_id", "is_present", "kind"),
        Index("ix_workloads_observed_at", "observed_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    cluster_id: Mapped[UUID] = mapped_column(
        ForeignKey("clusters.id", ondelete="RESTRICT"), nullable=False
    )
    vmid: Mapped[int] = mapped_column(Integer, nullable=False)
    node: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default="QEMU")
    name: Mapped[str | None] = mapped_column(String(255))
    power_state: Mapped[str] = mapped_column(String(20), nullable=False, default="UNKNOWN")
    cpu_cores: Mapped[int | None] = mapped_column(Integer)
    memory_bytes: Mapped[int | None] = mapped_column(BigInteger)
    disk_bytes: Mapped[int | None] = mapped_column(BigInteger)
    uptime_seconds: Mapped[int | None] = mapped_column(BigInteger)
    is_template: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_present: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sync_generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    missing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL")
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class WorkloadAssignment(Base):
    __tablename__ = "workload_assignments"
    __table_args__ = (
        Index(
            "uq_workload_assignments_active_workload",
            "workload_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("ix_workload_assignments_organization", "organization_id", "assigned_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workload_id: Mapped[UUID] = mapped_column(
        ForeignKey("workloads.id", ondelete="RESTRICT"), nullable=False
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    assigned_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(500))


class Operation(Base):
    __tablename__ = "operations"
    __table_args__ = (
        UniqueConstraint("requested_by_id", "idempotency_key_hash"),
        Index(
            "uq_operations_active_workload",
            "workload_id",
            unique=True,
            postgresql_where=text("status IN ('QUEUED', 'RUNNING', 'CANCEL_REQUESTED')"),
        ),
        Index("ix_operations_requester_requested", "requested_by_id", "requested_at"),
        Index("ix_operations_cluster_status", "cluster_id", "status"),
        Index("ix_operations_status_heartbeat", "status", "heartbeat_at"),
        CheckConstraint(
            "status IN ("
            "'QUEUED','RUNNING','CANCEL_REQUESTED','SUCCEEDED','FAILED','TIMEOUT',"
            "'CANCELLED','NEEDS_ATTENTION'"
            ")",
            name="ck_operations_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    operation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    retry_of_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("operations.id", ondelete="SET NULL"), unique=True
    )
    requested_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    source_ip: Mapped[str] = mapped_column(String(64), nullable=False)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL")
    )
    cluster_id: Mapped[UUID] = mapped_column(ForeignKey("clusters.id"), nullable=False)
    workload_id: Mapped[UUID] = mapped_column(ForeignKey("workloads.id"), nullable=False)
    idempotency_key_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    request_fingerprint: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    celery_task_id: Mapped[str] = mapped_column(String(255), nullable=False)
    result: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_summary: Mapped[str | None] = mapped_column(Text)
    retryable: Mapped[bool | None] = mapped_column(Boolean)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class PveTask(Base):
    __tablename__ = "pve_tasks"
    __table_args__ = (
        UniqueConstraint("cluster_id", "upid"),
        UniqueConstraint("operation_id", "step_name", "upid"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    operation_id: Mapped[UUID] = mapped_column(
        ForeignKey("operations.id", ondelete="CASCADE"), nullable=False
    )
    cluster_id: Mapped[UUID] = mapped_column(ForeignKey("clusters.id"), nullable=False)
    workload_id: Mapped[UUID] = mapped_column(ForeignKey("workloads.id"), nullable=False)
    step_name: Mapped[str] = mapped_column(String(64), nullable=False)
    upid: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    pve_node: Mapped[str] = mapped_column(String(255), nullable=False)
    pve_exit_status: Mapped[str | None] = mapped_column(String(255))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    poll_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_summary: Mapped[str | None] = mapped_column(Text)


class OperationEvent(Base):
    __tablename__ = "operation_events"
    __table_args__ = (
        CheckConstraint(
            "(operation_id IS NOT NULL) <> (provisioning_request_id IS NOT NULL)",
            name="ck_operation_events_single_target",
        ),
        Index("ix_operation_events_operation_occurred", "operation_id", "occurred_at"),
        Index(
            "ix_operation_events_provisioning_occurred",
            "provisioning_request_id",
            "occurred_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    operation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("operations.id", ondelete="CASCADE")
    )
    provisioning_request_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("provisioning_requests.id", ondelete="CASCADE")
    )
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str | None] = mapped_column(String(24))
    step: Mapped[str | None] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    details: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OperationAssignment(Base):
    __tablename__ = "operation_assignments"
    __table_args__ = (
        CheckConstraint(
            "(operation_id IS NOT NULL) <> (provisioning_request_id IS NOT NULL)",
            name="ck_operation_assignments_single_target",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    operation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("operations.id", ondelete="CASCADE"), unique=True
    )
    provisioning_request_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("provisioning_requests.id", ondelete="CASCADE"), unique=True
    )
    assigned_to_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    assigned_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(String(1000))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
