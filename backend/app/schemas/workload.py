from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class WorkloadResponse(BaseModel):
    id: UUID
    cluster_id: UUID
    cluster_name: str
    vmid: int
    node: str
    kind: str
    name: str | None
    power_state: str
    cpu_cores: int | None
    memory_bytes: int | None
    disk_bytes: int | None
    is_template: bool
    is_present: bool
    organization_id: UUID | None
    organization_name: str | None
    assigned_ip_addresses: list[str]
    observed_at: datetime
    version: int


class WorkloadListResponse(BaseModel):
    items: list[WorkloadResponse]


class WorkloadImportResponse(BaseModel):
    cluster_id: UUID
    discovered: int
    created: int
    updated: int


class WorkloadAssignRequest(BaseModel):
    organization_id: UUID


class WorkloadUnassignRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class WorkloadAssignmentResponse(BaseModel):
    id: UUID
    workload_id: UUID
    organization_id: UUID
    organization_name: str
    assigned_by_id: UUID
    assigned_at: datetime
    revoked_by_id: UUID | None
    revoked_at: datetime | None
    revoke_reason: str | None


class WorkloadAssignmentListResponse(BaseModel):
    items: list[WorkloadAssignmentResponse]
