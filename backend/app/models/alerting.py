from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        UniqueConstraint("fingerprint"),
        Index("ix_alerts_status_severity_seen", "status", "severity", "last_seen_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    type: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN")
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(120))
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    workload_id: Mapped[UUID | None] = mapped_column(ForeignKey("workloads.id", ondelete="CASCADE"))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    assigned_to_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    silenced_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class AlertEvent(Base):
    __tablename__ = "alert_events"
    __table_args__ = (Index("ix_alert_events_alert_created", "alert_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    alert_id: Mapped[UUID] = mapped_column(
        ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    note: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NotificationChannel(Base):
    __tablename__ = "notification_channels"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    config_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    config_nonce: Mapped[bytes] = mapped_column(LargeBinary(12), nullable=False)
    key_version: Mapped[str] = mapped_column(String(20), nullable=False)
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class NotificationRule(Base):
    __tablename__ = "notification_rules"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    channel_id: Mapped[UUID] = mapped_column(
        ForeignKey("notification_channels.id", ondelete="CASCADE"), nullable=False
    )
    event_types: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    severities: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    quiet_hours: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    escalation_minutes: Mapped[int | None] = mapped_column(Integer)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint("alert_event_id", "channel_id"),
        Index("ix_notification_deliveries_due", "status", "next_attempt_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    alert_event_id: Mapped[UUID] = mapped_column(
        ForeignKey("alert_events.id", ondelete="CASCADE"), nullable=False
    )
    channel_id: Mapped[UUID] = mapped_column(
        ForeignKey("notification_channels.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MaintenanceWindow(Base):
    __tablename__ = "maintenance_windows"
    __table_args__ = (Index("ix_maintenance_windows_active", "starts_at", "ends_at"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    target_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(120))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    suppress_notifications: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
