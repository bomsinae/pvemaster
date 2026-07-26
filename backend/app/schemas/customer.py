from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.operation import OperationStatus, PowerAction


class CustomerPowerActionRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
    confirm_forced: bool = False


class CustomerJobResponse(BaseModel):
    id: UUID
    job_id: UUID
    vm_id: UUID
    action: PowerAction
    action_mode: str
    status: OperationStatus
    result: dict[str, object]
    error_code: str | None
    error_summary: str | None
    retryable: bool | None
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class CustomerJobListResponse(BaseModel):
    items: list[CustomerJobResponse]
    total: int
    limit: int
    offset: int


class CustomerVmSummary(BaseModel):
    id: UUID
    name: str
    organization_name: str
    power_state: str
    cpu_cores: int | None
    memory_bytes: int | None
    disk_bytes: int | None
    uptime_seconds: int | None
    assigned_ip_addresses: list[str]
    observed_at: datetime
    is_stale: bool
    stale_reason: str | None


class CustomerVmListResponse(BaseModel):
    items: list[CustomerVmSummary]


class CustomerVmDetailResponse(CustomerVmSummary):
    recent_jobs: list[CustomerJobResponse]
    recent_state_changes: list["CustomerStateChange"]
    recent_backup: "CustomerBackupStatus | None"
    upcoming_maintenance: list["CustomerMaintenance"]


class CustomerStateChange(BaseModel):
    id: int
    change_type: str
    summary: str
    observed_at: datetime


class CustomerBackupStatus(BaseModel):
    status: str
    completed_at: datetime | None
    scheduled_for: datetime | None


class CustomerMaintenance(BaseModel):
    id: UUID
    name: str
    starts_at: datetime
    ends_at: datetime


CustomerMetricRange = Literal["day", "month", "year"]


class CustomerMetricPoint(BaseModel):
    time: datetime
    sample_count: int = Field(ge=1)
    cpu_avg: float | None = Field(default=None, ge=0)
    cpu_max: float | None = Field(default=None, ge=0)
    memory_used_avg: float | None = Field(default=None, ge=0)
    memory_used_max: float | None = Field(default=None, ge=0)
    disk_read_avg: float | None = Field(default=None, ge=0)
    disk_read_max: float | None = Field(default=None, ge=0)
    disk_write_avg: float | None = Field(default=None, ge=0)
    disk_write_max: float | None = Field(default=None, ge=0)
    network_receive_avg: float | None = Field(default=None, ge=0)
    network_receive_max: float | None = Field(default=None, ge=0)
    network_transmit_avg: float | None = Field(default=None, ge=0)
    network_transmit_max: float | None = Field(default=None, ge=0)


class CustomerMetricSeriesResponse(BaseModel):
    vm_id: UUID
    range: CustomerMetricRange
    resolution_seconds: int
    assignment_started_at: datetime
    observed_at: datetime
    partial: bool
    items: list[CustomerMetricPoint]
