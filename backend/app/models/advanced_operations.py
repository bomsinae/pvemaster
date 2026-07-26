from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base


class AdvancedOperationIntent(Base):
    __tablename__ = "advanced_operation_intents"
    __table_args__ = (
        CheckConstraint(
            "feature IN ('SNAPSHOT','MIGRATION','HA','NODE_MAINTENANCE',"
            "'BULK','GUEST_CONFIG','FIREWALL_SDN')",
            name="ck_advanced_operation_intents_feature",
        ),
        CheckConstraint(
            "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','NEEDS_ATTENTION')",
            name="ck_advanced_operation_intents_status",
        ),
        Index("ix_advanced_intents_feature_status", "feature", "status"),
    )

    operation_id: Mapped[UUID] = mapped_column(
        ForeignKey("operations.id", ondelete="CASCADE"), primary_key=True
    )
    feature: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    target_snapshot: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    options_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    preview_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    requested_state: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    observed_state: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    current_target_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AdvancedOperationTarget(Base):
    __tablename__ = "advanced_operation_targets"
    __table_args__ = (
        Index(
            "uq_advanced_operation_targets_active_workload",
            "workload_id",
            unique=True,
            postgresql_where=text("active"),
        ),
    )

    operation_id: Mapped[UUID] = mapped_column(
        ForeignKey("advanced_operation_intents.operation_id", ondelete="CASCADE"),
        primary_key=True,
    )
    workload_id: Mapped[UUID] = mapped_column(
        ForeignKey("workloads.id", ondelete="RESTRICT"), primary_key=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
