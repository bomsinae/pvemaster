from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import CIDR, INET
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base


class IpAddressState(StrEnum):
    AVAILABLE = "AVAILABLE"
    RESERVED = "RESERVED"
    ASSIGNED = "ASSIGNED"
    QUARANTINED = "QUARANTINED"
    DISABLED = "DISABLED"


class IpAllocationKind(StrEnum):
    AUTOMATIC = "AUTOMATIC"
    MANUAL = "MANUAL"


class IpAllocationStatus(StrEnum):
    RESERVED = "RESERVED"
    ASSIGNED = "ASSIGNED"
    QUARANTINED = "QUARANTINED"
    RELEASED = "RELEASED"


class IpPool(Base):
    __tablename__ = "ip_pools"
    __table_args__ = (
        CheckConstraint("ip_family IN (4, 6)", name="ck_ip_pools_family"),
        CheckConstraint("vlan_tag IS NULL OR vlan_tag BETWEEN 1 AND 4094", name="ck_ip_pools_vlan"),
        CheckConstraint("quarantine_seconds >= 0", name="ck_ip_pools_quarantine"),
        CheckConstraint(
            "allocation_strategy IN ('SEQUENTIAL', 'RANDOM')",
            name="ck_ip_pools_strategy",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    cluster_id: Mapped[UUID | None] = mapped_column(ForeignKey("clusters.id", ondelete="RESTRICT"))
    cidr: Mapped[str] = mapped_column(CIDR, nullable=False)
    gateway: Mapped[str | None] = mapped_column(INET)
    dns_servers: Mapped[list[str]] = mapped_column(ARRAY(INET), nullable=False, default=list)
    bridge: Mapped[str] = mapped_column(String(64), nullable=False)
    vlan_tag: Mapped[int | None] = mapped_column(Integer)
    ip_family: Mapped[int] = mapped_column(Integer, nullable=False)
    allocation_strategy: Mapped[str] = mapped_column(
        String(20), nullable=False, default="SEQUENTIAL"
    )
    quarantine_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=600)
    next_offset: Mapped[Decimal] = mapped_column(
        Numeric(precision=39, scale=0), nullable=False, default=Decimal(0)
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class IpPoolExclusion(Base):
    __tablename__ = "ip_pool_exclusions"
    __table_args__ = (UniqueConstraint("pool_id", "start_address", "end_address"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    pool_id: Mapped[UUID] = mapped_column(
        ForeignKey("ip_pools.id", ondelete="CASCADE"), nullable=False
    )
    start_address: Mapped[str] = mapped_column(INET, nullable=False)
    end_address: Mapped[str] = mapped_column(INET, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IpAddress(Base):
    __tablename__ = "ip_addresses"
    __table_args__ = (
        UniqueConstraint("pool_id", "address"),
        CheckConstraint(
            "state IN ('AVAILABLE','RESERVED','ASSIGNED','QUARANTINED','DISABLED')",
            name="ck_ip_addresses_state",
        ),
        Index("ix_ip_addresses_pool_state_address", "pool_id", "state", "address"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    pool_id: Mapped[UUID] = mapped_column(
        ForeignKey("ip_pools.id", ondelete="RESTRICT"), nullable=False
    )
    address: Mapped[str] = mapped_column(INET, nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    reserved_for: Mapped[str | None] = mapped_column(String(255))
    quarantined_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_allocated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class IpAllocation(Base):
    __tablename__ = "ip_allocations"
    __table_args__ = (
        CheckConstraint("kind IN ('AUTOMATIC','MANUAL')", name="ck_ip_allocations_kind"),
        CheckConstraint(
            "status IN ('RESERVED','ASSIGNED','QUARANTINED','RELEASED')",
            name="ck_ip_allocations_status",
        ),
        Index(
            "uq_ip_allocations_active_address",
            "ip_address_id",
            unique=True,
            postgresql_where=text("status IN ('RESERVED','ASSIGNED','QUARANTINED')"),
        ),
        Index(
            "uq_ip_allocations_active_provisioning_request",
            "provisioning_request_id",
            unique=True,
            postgresql_where=text(
                "provisioning_request_id IS NOT NULL "
                "AND status IN ('RESERVED','ASSIGNED','QUARANTINED')"
            ),
        ),
        Index("ix_ip_allocations_workload_status", "workload_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    ip_address_id: Mapped[UUID] = mapped_column(
        ForeignKey("ip_addresses.id", ondelete="RESTRICT"), nullable=False
    )
    workload_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workloads.id", ondelete="RESTRICT")
    )
    provisioning_request_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("provisioning_requests.id", ondelete="RESTRICT")
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    allocated_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    allocated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
