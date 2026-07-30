from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db import Base


class WorkloadMetric(Base):
    __tablename__ = "workload_metrics"
    __table_args__ = (
        UniqueConstraint(
            "workload_id",
            "organization_id",
            "resolution_seconds",
            "bucket_at",
        ),
        Index(
            "ix_workload_metrics_scope_bucket",
            "organization_id",
            "workload_id",
            "resolution_seconds",
            "bucket_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workload_id: Mapped[UUID] = mapped_column(
        ForeignKey("workloads.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    resolution_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    bucket_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    cpu_avg: Mapped[float | None] = mapped_column(Float)
    cpu_max: Mapped[float | None] = mapped_column(Float)
    memory_used_avg: Mapped[float | None] = mapped_column(Float)
    memory_used_max: Mapped[float | None] = mapped_column(Float)
    disk_read_avg: Mapped[float | None] = mapped_column(Float)
    disk_read_max: Mapped[float | None] = mapped_column(Float)
    disk_write_avg: Mapped[float | None] = mapped_column(Float)
    disk_write_max: Mapped[float | None] = mapped_column(Float)
    network_receive_avg: Mapped[float | None] = mapped_column(Float)
    network_receive_max: Mapped[float | None] = mapped_column(Float)
    network_transmit_avg: Mapped[float | None] = mapped_column(Float)
    network_transmit_max: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
