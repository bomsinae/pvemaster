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


class ServiceRequestType(StrEnum):
    SSH_KEY_ADD = "SSH_KEY_ADD"
    SSH_KEY_REPLACE = "SSH_KEY_REPLACE"
    SSH_KEY_DELETE = "SSH_KEY_DELETE"
    METADATA_CHANGE = "METADATA_CHANGE"
    RDNS_CHANGE = "RDNS_CHANGE"
    SECURITY_GROUP_APPLY = "SECURITY_GROUP_APPLY"
    BACKUP_RUN = "BACKUP_RUN"
    RESTORE_REQUEST = "RESTORE_REQUEST"
    RESIZE = "RESIZE"
    REINSTALL = "REINSTALL"


class ServiceRequestStatus(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    IN_PROGRESS = "IN_PROGRESS"
    SUCCEEDED = "SUCCEEDED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"


class SshPublicKey(Base):
    __tablename__ = "ssh_public_keys"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "organization_id", "fingerprint"),
        Index(
            "ix_ssh_public_keys_owner_active",
            "owner_user_id",
            "organization_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SecurityGroup(Base):
    __tablename__ = "security_groups"
    __table_args__ = (
        UniqueConstraint("organization_id", "name"),
        CheckConstraint(
            "organization_id IS NOT NULL OR is_global = true",
            name="ck_security_groups_scope",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    rules: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    is_global: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class WorkloadSshPublicKey(Base):
    __tablename__ = "workload_ssh_public_keys"
    __table_args__ = (UniqueConstraint("workload_id", "ssh_public_key_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workload_id: Mapped[UUID] = mapped_column(
        ForeignKey("workloads.id", ondelete="CASCADE"), nullable=False
    )
    ssh_public_key_id: Mapped[UUID] = mapped_column(
        ForeignKey("ssh_public_keys.id", ondelete="CASCADE"), nullable=False
    )
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WorkloadSecurityGroup(Base):
    __tablename__ = "workload_security_groups"
    __table_args__ = (UniqueConstraint("workload_id", "security_group_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workload_id: Mapped[UUID] = mapped_column(
        ForeignKey("workloads.id", ondelete="CASCADE"), nullable=False
    )
    security_group_id: Mapped[UUID] = mapped_column(
        ForeignKey("security_groups.id", ondelete="CASCADE"), nullable=False
    )
    applied_by_request_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "service_requests.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_workload_security_groups_service_request",
        )
    )
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OrganizationServiceQuota(Base):
    __tablename__ = "organization_service_quotas"

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    max_cpu_cores_per_vm: Mapped[int] = mapped_column(Integer, nullable=False, default=64)
    max_memory_bytes_per_vm: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=549_755_813_888
    )
    max_disk_bytes_per_vm: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=17_592_186_044_416
    )
    max_pending_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ServiceRequest(Base):
    __tablename__ = "service_requests"
    __table_args__ = (
        UniqueConstraint("requested_by_id", "idempotency_key_hash"),
        Index("ix_service_requests_org_status", "organization_id", "status", "requested_at"),
        Index(
            "uq_service_requests_active_type",
            "workload_id",
            "request_type",
            unique=True,
            postgresql_where=text(
                "status IN ('PENDING_APPROVAL','APPROVED','IN_PROGRESS','NEEDS_ATTENTION')"
            ),
        ),
        CheckConstraint(
            "status IN ('PENDING_APPROVAL','APPROVED','IN_PROGRESS','SUCCEEDED',"
            "'REJECTED','CANCELLED','NEEDS_ATTENTION')",
            name="ck_service_requests_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    request_type: Mapped[str] = mapped_column(String(40), nullable=False)
    requested_by_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    workload_id: Mapped[UUID] = mapped_column(
        ForeignKey("workloads.id", ondelete="RESTRICT"), nullable=False
    )
    assignment_id: Mapped[UUID] = mapped_column(
        ForeignKey("workload_assignments.id", ondelete="RESTRICT"), nullable=False
    )
    input_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    impact_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    idempotency_key_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    request_fingerprint: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    operation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("operations.id", ondelete="SET NULL"), unique=True
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    result_summary: Mapped[str | None] = mapped_column(String(500))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ApprovalStep(Base):
    __tablename__ = "approval_steps"
    __table_args__ = (UniqueConstraint("service_request_id", "step_order"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    service_request_id: Mapped[UUID] = mapped_column(
        ForeignKey("service_requests.id", ondelete="CASCADE"), nullable=False
    )
    step_order: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    approver_role: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str | None] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(String(500))
    decided_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
