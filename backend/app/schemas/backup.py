from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

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
