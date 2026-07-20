from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.operation import AdminVmAction, OperationStatus, PowerAction


class PowerActionRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class VmSpecUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cpu_cores: int = Field(ge=1, le=128)
    memory_gib: int = Field(ge=1, le=4096)
    disk_gib: int | None = Field(default=None, ge=1, le=1_048_576)
    version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=500)


class VmDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation: str = Field(min_length=1, max_length=255)
    reason: str | None = Field(default=None, max_length=500)


class JobResponse(BaseModel):
    id: UUID
    job_id: UUID
    vm_id: UUID
    workload_id: UUID
    organization_id: UUID | None
    action: PowerAction | AdminVmAction
    action_mode: str
    status: OperationStatus
    result: dict[str, object]
    error_code: str | None
    error_summary: str | None
    retryable: bool | None
    pve_upid: str | None
    pve_exit_status: str | None
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobAcceptedResponse(JobResponse):
    status: OperationStatus
