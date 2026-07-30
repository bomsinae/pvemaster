from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.operation import OperationStatus


class BackupStorageCandidate(BaseModel):
    cluster_id: UUID
    cluster_name: str
    storage_id: str
    datastore: str | None = None
    namespace: str | None = None
    available: bool
    enabled_in_pve: bool
    registered_target_id: UUID | None = None


class BackupStorageCandidateListResponse(BaseModel):
    items: list[BackupStorageCandidate]


class BackupTargetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cluster_id: UUID
    storage_id: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9._-]+$")


class BackupTargetUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_enabled: bool
    version: int = Field(ge=1)


class BackupTargetResponse(BaseModel):
    id: UUID
    cluster_id: UUID
    cluster_name: str
    storage_id: str
    datastore: str | None
    namespace: str | None
    is_enabled: bool
    available: bool
    last_checked_at: datetime | None
    created_at: datetime
    updated_at: datetime
    version: int


class BackupTargetListResponse(BaseModel):
    items: list[BackupTargetResponse]


class BackupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backup_target_id: UUID
    mode: Literal["snapshot"] = "snapshot"
    compression: Literal["zstd"] = "zstd"


class BackupRunResponse(BaseModel):
    id: UUID
    operation_id: UUID
    backup_target_id: UUID
    cluster_id: UUID
    cluster_name: str
    storage_id: str
    workload_id: UUID
    workload_name: str | None
    vmid: int
    kind: str
    source_node: str
    organization_id: UUID | None
    organization_name: str | None
    policy_assignment_id: UUID | None
    scheduled_for: datetime | None
    trigger_type: str
    mode: str
    compression: str
    status: OperationStatus
    snapshot_volume_id: str | None
    snapshot_time: datetime | None
    size_bytes: int | None
    transferred_bytes: int | None
    error_code: str | None
    error_summary: str | None
    retryable: bool | None
    pve_exit_status: str | None
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class BackupRunListResponse(BaseModel):
    items: list[BackupRunResponse]


class RestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_node: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9._-]+$")
    target_vmid: int = Field(ge=100, le=999_999_999)
    target_name: str = Field(
        min_length=1,
        max_length=63,
        pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$",
    )


class RestoreRunResponse(BaseModel):
    id: UUID
    operation_id: UUID
    backup_run_id: UUID
    cluster_id: UUID
    cluster_name: str
    source_workload_id: UUID
    source_workload_name: str | None
    kind: str
    snapshot_volume_id: str
    target_node: str
    target_vmid: int
    target_name: str
    status: OperationStatus
    error_code: str | None
    error_summary: str | None
    retryable: bool | None
    pve_exit_status: str | None
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class BackupPolicyAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: UUID | None = None
    workload_id: UUID | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> "BackupPolicyAssignmentRequest":
        if (self.organization_id is None) == (self.workload_id is None):
            raise ValueError("Exactly one policy assignment scope is required.")
        return self


class BackupPolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    backup_target_id: UUID
    schedule: str = Field(min_length=5, max_length=120)
    timezone: str = Field(min_length=1, max_length=64)
    mode: Literal["snapshot"] = "snapshot"
    retention_reference: str | None = Field(default=None, max_length=255)
    verification_interval_days: int = Field(default=90, ge=1, le=365)
    is_enabled: bool = True
    assignments: list[BackupPolicyAssignmentRequest] = Field(min_length=1, max_length=500)


class BackupPolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    backup_target_id: UUID
    schedule: str = Field(min_length=5, max_length=120)
    timezone: str = Field(min_length=1, max_length=64)
    mode: Literal["snapshot"] = "snapshot"
    retention_reference: str | None = Field(default=None, max_length=255)
    verification_interval_days: int = Field(default=90, ge=1, le=365)
    is_enabled: bool
    assignments: list[BackupPolicyAssignmentRequest] = Field(min_length=1, max_length=500)
    version: int = Field(ge=1)


class BackupPolicyAssignmentResponse(BaseModel):
    id: UUID
    organization_id: UUID | None
    organization_name: str | None
    workload_id: UUID | None
    workload_name: str | None


class BackupPolicyResponse(BaseModel):
    id: UUID
    name: str
    backup_target_id: UUID
    backup_target_name: str
    schedule: str
    timezone: str
    mode: str
    retention_reference: str | None
    verification_interval_days: int
    is_enabled: bool
    next_run_at: datetime
    last_dispatched_at: datetime | None
    skip_next_at: datetime | None
    recent_success_at: datetime | None
    consecutive_failures: int
    assignments: list[BackupPolicyAssignmentResponse]
    created_at: datetime
    updated_at: datetime
    version: int


class BackupPolicyListResponse(BaseModel):
    items: list[BackupPolicyResponse]


class BackupPolicyPreviewItem(BaseModel):
    assignment_id: UUID
    organization_id: UUID | None
    workload_id: UUID
    workload_name: str | None
    kind: str
    cluster_id: UUID
    eligible: bool
    reason: str | None
    recent_success_at: datetime | None


class BackupPolicyPreviewResponse(BaseModel):
    policy_id: UUID
    next_run_at: datetime
    items: list[BackupPolicyPreviewItem]


class BackupPolicySkipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)


class BackupMetadataReconcileResponse(BaseModel):
    processed_count: int


class BackupVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verification_type: Literal["METADATA", "RESTORE_DRILL"]
    target_node: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9._-]+$",
    )
    target_vmid: int | None = Field(default=None, ge=100, le=999_999_999)
    target_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=63,
        pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$",
    )

    @model_validator(mode="after")
    def validate_restore_target(self) -> "BackupVerificationRequest":
        target_values = (self.target_node, self.target_vmid, self.target_name)
        if self.verification_type == "RESTORE_DRILL" and any(
            value is None for value in target_values
        ):
            raise ValueError("A restore drill target is required.")
        if self.verification_type == "METADATA" and any(
            value is not None for value in target_values
        ):
            raise ValueError("Metadata verification does not accept a restore target.")
        return self


class BackupVerificationResponse(BaseModel):
    id: UUID
    backup_run_id: UUID
    restore_run_id: UUID | None
    verification_type: str
    status: str
    snapshot_volume_id: str
    due_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    result_summary: str | None
    created_at: datetime


class BackupVerificationListResponse(BaseModel):
    items: list[BackupVerificationResponse]
