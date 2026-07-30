from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SyncRequestResponse(BaseModel):
    operation_id: UUID
    status: str


class SyncRunResponse(BaseModel):
    id: UUID
    operation_id: UUID
    cluster_id: UUID
    cluster_name: str
    generation: int
    scope: str
    status: str
    partial_failure: bool
    triggered_by: str
    requested_by_id: UUID | None
    target_workload_id: UUID | None
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    error_code: str | None
    resource_counts: dict[str, object]


class SyncRunListResponse(BaseModel):
    items: list[SyncRunResponse]


class InventoryFreshnessResponse(BaseModel):
    cluster_id: UUID
    cluster_name: str
    last_full_success_at: datetime | None
    stale_after_seconds: int
    is_stale: bool
    stale_reason: str | None
    latest_status: str | None


class InventoryFreshnessListResponse(BaseModel):
    items: list[InventoryFreshnessResponse]


class ReconciliationFindingResponse(BaseModel):
    id: UUID
    kind: str
    severity: str
    status: str
    cluster_id: UUID
    cluster_name: str
    workload_id: UUID | None
    sync_run_id: UUID | None
    target_type: str
    target_id: UUID
    summary: str
    details: dict[str, object]
    first_observed_at: datetime
    last_observed_at: datetime
    acknowledged_by_id: UUID | None
    acknowledged_at: datetime | None
    assigned_to_id: UUID | None
    resolved_by_id: UUID | None
    resolved_at: datetime | None
    resolution_note: str | None


class ReconciliationFindingListResponse(BaseModel):
    items: list[ReconciliationFindingResponse]


class FindingAcknowledgeRequest(BaseModel):
    assigned_to_id: UUID | None = None


class FindingResolveRequest(BaseModel):
    resolution_note: str = Field(min_length=3, max_length=1000)
