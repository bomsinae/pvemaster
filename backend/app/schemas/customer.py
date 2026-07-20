from datetime import datetime
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


class CustomerVmSummary(BaseModel):
    id: UUID
    name: str
    power_state: str
    cpu_cores: int | None
    memory_bytes: int | None
    disk_bytes: int | None
    assigned_ip_addresses: list[str]
    observed_at: datetime


class CustomerVmListResponse(BaseModel):
    items: list[CustomerVmSummary]


class CustomerVmDetailResponse(CustomerVmSummary):
    recent_jobs: list[CustomerJobResponse]
