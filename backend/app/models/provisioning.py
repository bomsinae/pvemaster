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


class ProvisioningStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class ProvisioningStepStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("cpu_cores > 0", name="ck_products_cpu"),
        CheckConstraint("memory_bytes > 0", name="ck_products_memory"),
        CheckConstraint("disk_bytes > 0", name="ck_products_disk"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    cpu_cores: Mapped[int] = mapped_column(Integer, nullable=False)
    memory_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    disk_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Template(Base):
    __tablename__ = "templates"
    __table_args__ = (UniqueConstraint("source_workload_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    source_workload_id: Mapped[UUID] = mapped_column(
        ForeignKey("workloads.id", ondelete="RESTRICT"), nullable=False
    )
    source_disk: Mapped[str] = mapped_column(String(32), nullable=False, default="scsi0")
    default_storage: Mapped[str] = mapped_column(String(64), nullable=False)
    default_bridge: Mapped[str] = mapped_column(String(64), nullable=False)
    default_vlan_tag: Mapped[int | None] = mapped_column(Integer)
    cloud_init_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    linux_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ProvisioningNode(Base):
    __tablename__ = "provisioning_nodes"
    __table_args__ = (UniqueConstraint("cluster_id", "name"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    cluster_id: Mapped[UUID] = mapped_column(
        ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_maintenance: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    available_memory_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    available_storage_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_selected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ProvisioningRequest(Base):
    __tablename__ = "provisioning_requests"
    __table_args__ = (
        UniqueConstraint("requested_by_id", "idempotency_key_hash"),
        Index(
            "uq_provisioning_active_vmid",
            "target_cluster_id",
            "target_vmid",
            unique=True,
            postgresql_where=text(
                "target_vmid IS NOT NULL "
                "AND status IN ('QUEUED','RUNNING','SUCCEEDED','MANUAL_REVIEW')"
            ),
        ),
        Index("ix_provisioning_requests_recovery_lease", "status", "lease_expires_at"),
        CheckConstraint(
            "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED','MANUAL_REVIEW')",
            name="ck_provisioning_requests_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    retry_of_request_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("provisioning_requests.id", ondelete="SET NULL"), unique=True
    )
    requested_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    idempotency_key_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    request_fingerprint: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    template_id: Mapped[UUID] = mapped_column(ForeignKey("templates.id"), nullable=False)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    target_cluster_id: Mapped[UUID] = mapped_column(ForeignKey("clusters.id"), nullable=False)
    target_node_id: Mapped[UUID | None] = mapped_column(ForeignKey("provisioning_nodes.id"))
    target_vmid: Mapped[int | None] = mapped_column(Integer)
    target_name: Mapped[str] = mapped_column(String(63), nullable=False)
    ip_pool_id: Mapped[UUID] = mapped_column(ForeignKey("ip_pools.id"), nullable=False)
    requested_ip_address: Mapped[str | None] = mapped_column(String(64))
    ip_address_id: Mapped[UUID | None] = mapped_column(ForeignKey("ip_addresses.id"))
    workload_id: Mapped[UUID | None] = mapped_column(ForeignKey("workloads.id"))
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    current_step: Mapped[str] = mapped_column(String(64), nullable=False)
    spec_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    celery_task_id: Mapped[str] = mapped_column(String(255), nullable=False)
    clone_submitted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_summary: Mapped[str | None] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    runner_id: Mapped[UUID | None] = mapped_column()
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ProvisioningStep(Base):
    __tablename__ = "provisioning_steps"
    __table_args__ = (
        UniqueConstraint("provisioning_request_id", "step_order"),
        UniqueConstraint("provisioning_request_id", "step_name"),
        CheckConstraint(
            "status IN ('PENDING','RUNNING','SUCCEEDED','FAILED')",
            name="ck_provisioning_steps_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provisioning_request_id: Mapped[UUID] = mapped_column(
        ForeignKey("provisioning_requests.id", ondelete="CASCADE"), nullable=False
    )
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    step_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pve_upid: Mapped[str | None] = mapped_column(Text)
    safe_result: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
