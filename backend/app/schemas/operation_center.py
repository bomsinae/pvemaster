from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

OperationResourceType = Literal["OPERATION", "PROVISIONING"]
OperationCenterAction = Literal[
    "CANCEL",
    "RETRY",
    "ACKNOWLEDGE",
    "ASSIGN",
    "RESOLVE_MANUALLY",
]


class OperationAssignmentResponse(BaseModel):
    assigned_to_id: UUID | None
    assigned_to_name: str | None
    assigned_at: datetime | None
    acknowledged_by_id: UUID | None
    acknowledged_at: datetime | None
    resolved_by_id: UUID | None
    resolved_at: datetime | None
    resolution_note: str | None
    version: int


class OperationCenterItemResponse(BaseModel):
    id: UUID
    resource_type: OperationResourceType
    operation_type: str
    action: str
    status: str
    cluster_id: UUID
    cluster_name: str
    organization_id: UUID | None
    organization_name: str | None
    requested_by_id: UUID
    requested_by_name: str
    workload_id: UUID | None
    workload_name: str | None
    current_step: str | None
    error_code: str | None
    error_summary: str | None
    retryable: bool
    retry_of_id: UUID | None
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    heartbeat_at: datetime | None
    is_stuck: bool
    available_actions: list[OperationCenterAction]
    impact_summary: str
    recommended_action: str
    assignment: OperationAssignmentResponse | None
    version: int


class OperationCenterListResponse(BaseModel):
    items: list[OperationCenterItemResponse]
    total: int
    limit: int
    offset: int


class OperationEventResponse(BaseModel):
    id: int
    event_type: str
    status: str | None
    step: str | None
    message: str
    details: dict[str, object]
    actor_user_id: UUID | None
    occurred_at: datetime


class OperationTaskResponse(BaseModel):
    step_name: str
    status: str
    upid_reference: str
    pve_exit_status: str | None
    poll_attempts: int
    error_code: str | None
    submitted_at: datetime
    last_polled_at: datetime | None
    completed_at: datetime | None


class OperationStepResponse(BaseModel):
    order: int
    name: str
    status: str
    attempt_count: int
    upid_reference: str | None
    error_code: str | None
    started_at: datetime | None
    finished_at: datetime | None


class OperationCenterDetailResponse(OperationCenterItemResponse):
    events: list[OperationEventResponse]
    pve_tasks: list[OperationTaskResponse]
    provisioning_steps: list[OperationStepResponse]
    related_audit_count: int
    related_backup_ids: list[UUID]


class OperationVersionRequest(BaseModel):
    version: int = Field(ge=1)


class OperationAssignRequest(OperationVersionRequest):
    assigned_to_id: UUID


class OperationResolveRequest(OperationVersionRequest):
    resolution_note: str = Field(min_length=3, max_length=1000)


class OperationActionResponse(BaseModel):
    operation: OperationCenterItemResponse
    created_operation_id: UUID | None = None
