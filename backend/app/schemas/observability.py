from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor_user_id: UUID | None
    actor_role: str | None
    actor_display_name: str | None = None
    actor_email: str | None = None
    organization_id: UUID | None
    action: str
    resource_type: str | None
    resource_id: str | None
    workload_name: str | None = None
    workload_vmid: int | None = None
    workload_kind: str | None = None
    workload_node: str | None = None
    workload_cluster_name: str | None = None
    source_ip: str | None
    user_agent: str | None
    request_id: str | None
    before: dict[str, object] | None
    after: dict[str, object] | None
    result: str
    error_code: str | None
    created_at: datetime


class AuditLogListResponse(BaseModel):
    items: list[AuditLogResponse]
    total: int
    limit: int
    offset: int


class WorkerStatus(BaseModel):
    available: bool = True
    alive: bool
    workers: list[str]
    stale_after_seconds: int


class QueueStatus(BaseModel):
    available: bool = True
    total: int
    queues: dict[str, int]
    backlog_threshold: int


class WorkloadInventoryStatus(BaseModel):
    total: int = Field(ge=0)
    assigned: int = Field(ge=0)
    unassigned: int = Field(ge=0)


class ClusterConnectionStatus(BaseModel):
    cluster_id: UUID
    name: str
    enabled: bool
    connected: bool
    last_connected_at: datetime | None
    error_code: str | None


class OperationalAlert(BaseModel):
    code: str
    severity: str
    resource_type: str
    resource_id: str | None = None
    message: str
    value: int | None = None
    threshold: int | None = None


class OperationsStatusResponse(BaseModel):
    status: str
    worker: WorkerStatus
    queue: QueueStatus
    workloads: WorkloadInventoryStatus
    clusters: list[ClusterConnectionStatus]
    alerts: list[OperationalAlert] = Field(default_factory=list)
