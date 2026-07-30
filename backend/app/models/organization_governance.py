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
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base


class OrganizationInvitation(Base):
    __tablename__ = "organization_invitations"
    __table_args__ = (
        CheckConstraint(
            "organization_role IN "
            "('ORG_OWNER','ORG_ADMIN','ORG_OPERATOR','ORG_VIEWER','BILLING_VIEWER')",
            name="ck_organization_invitations_role",
        ),
        Index(
            "ix_organization_invitations_pending",
            "organization_id",
            "accepted_at",
            "revoked_at",
            "expires_at",
        ),
        Index(
            "uq_organization_invitations_pending_email",
            "organization_id",
            "email",
            unique=True,
            postgresql_where=text("accepted_at IS NULL AND revoked_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    organization_role: Mapped[str] = mapped_column(String(24), nullable=False)
    invited_by_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OrganizationQuota(Base):
    __tablename__ = "organization_quotas"
    __table_args__ = (
        CheckConstraint(
            "max_vcpu >= 0 AND max_memory_bytes >= 0 AND max_disk_bytes >= 0 "
            "AND max_vms >= 0 AND max_ips >= 0 AND max_backup_bytes >= 0",
            name="ck_organization_quotas_nonnegative",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    max_vcpu: Mapped[int] = mapped_column(Integer, nullable=False)
    max_memory_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_disk_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_vms: Mapped[int] = mapped_column(Integer, nullable=False)
    max_ips: Mapped[int] = mapped_column(Integer, nullable=False)
    max_backup_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_by_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class QuotaUsageSnapshot(Base):
    __tablename__ = "quota_usage_snapshots"
    __table_args__ = (
        Index(
            "ix_quota_usage_snapshots_organization_captured",
            "organization_id",
            "captured_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    used_vcpu: Mapped[int] = mapped_column(Integer, nullable=False)
    used_memory_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    used_disk_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    used_vms: Mapped[int] = mapped_column(Integer, nullable=False)
    used_ips: Mapped[int] = mapped_column(Integer, nullable=False)
    used_backup_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class QuotaReservation(Base):
    __tablename__ = "quota_reservations"
    __table_args__ = (
        CheckConstraint(
            "(provisioning_request_id IS NOT NULL) <> (service_request_id IS NOT NULL)",
            name="ck_quota_reservations_single_request",
        ),
        CheckConstraint(
            "status IN ('ACTIVE','CONSUMED','RELEASED')",
            name="ck_quota_reservations_status",
        ),
        CheckConstraint(
            "vcpu >= 0 AND memory_bytes >= 0 AND disk_bytes >= 0 "
            "AND vms >= 0 AND ips >= 0 AND backup_bytes >= 0",
            name="ck_quota_reservations_nonnegative",
        ),
        Index(
            "ix_quota_reservations_organization_active",
            "organization_id",
            "status",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    provisioning_request_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("provisioning_requests.id", ondelete="CASCADE"), unique=True
    )
    service_request_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("service_requests.id", ondelete="CASCADE"), unique=True
    )
    vcpu: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    memory_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    disk_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    vms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ips: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    backup_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApprovalPolicy(Base):
    __tablename__ = "approval_policies"
    __table_args__ = (
        UniqueConstraint("organization_id", "request_type"),
        CheckConstraint(
            "minimum_role IN ('ORG_OWNER','ORG_ADMIN','ORG_OPERATOR')",
            name="ck_approval_policies_minimum_role",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    request_type: Mapped[str] = mapped_column(String(40), nullable=False)
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    minimum_role: Mapped[str] = mapped_column(String(24), nullable=False)
    updated_by_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
