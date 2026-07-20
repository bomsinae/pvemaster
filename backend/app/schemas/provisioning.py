import base64
import binascii
from datetime import datetime
from ipaddress import IPv4Address
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.provisioning import ProvisioningStatus, ProvisioningStepStatus


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProductCreate(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    cpu_cores: int = Field(ge=1, le=128)
    memory_bytes: int = Field(ge=268_435_456, le=2_199_023_255_552)
    disk_bytes: int = Field(ge=1_073_741_824, le=70_368_744_177_664)


class ProductUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    cpu_cores: int | None = Field(default=None, ge=1, le=128)
    memory_bytes: int | None = Field(default=None, ge=268_435_456, le=2_199_023_255_552)
    disk_bytes: int | None = Field(default=None, ge=1_073_741_824, le=70_368_744_177_664)
    is_enabled: bool | None = None


class ProductResponse(BaseModel):
    id: UUID
    name: str
    cpu_cores: int
    memory_bytes: int
    disk_bytes: int
    is_enabled: bool


class ProductListResponse(BaseModel):
    items: list[ProductResponse]


class TemplateCreate(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    source_workload_id: UUID
    source_disk: str = Field(default="scsi0", pattern=r"^(scsi|virtio|sata)[0-9]+$")
    default_storage: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    default_bridge: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    default_vlan_tag: int | None = Field(default=None, ge=1, le=4094)


class TemplateUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    source_workload_id: UUID | None = None
    source_disk: str | None = Field(default=None, pattern=r"^(scsi|virtio|sata)[0-9]+$")
    default_storage: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$"
    )
    default_bridge: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$"
    )
    default_vlan_tag: int | None = Field(default=None, ge=1, le=4094)
    is_enabled: bool | None = None


class TemplateResponse(BaseModel):
    id: UUID
    name: str
    source_workload_id: UUID
    source_disk: str
    default_storage: str
    default_bridge: str
    default_vlan_tag: int | None
    cloud_init_enabled: bool
    linux_only: bool
    is_enabled: bool


class TemplateListResponse(BaseModel):
    items: list[TemplateResponse]


class ProvisioningNodeUpsert(StrictModel):
    cluster_id: UUID
    name: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9_.-]+$")
    is_enabled: bool = True
    is_maintenance: bool = False
    available_memory_bytes: int = Field(ge=0)
    available_storage_bytes: int = Field(ge=0)


class ProvisioningNodeResponse(BaseModel):
    id: UUID
    cluster_id: UUID
    name: str
    is_enabled: bool
    is_maintenance: bool
    available_memory_bytes: int
    available_storage_bytes: int
    last_selected_at: datetime | None


class ProvisioningNodeListResponse(BaseModel):
    items: list[ProvisioningNodeResponse]


class CloudInitRequest(StrictModel):
    username: str = Field(pattern=r"^[a-z_][a-z0-9_-]{0,31}$")
    ssh_public_keys: list[str] = Field(min_length=1, max_length=8)

    @field_validator("ssh_public_keys")
    @classmethod
    def validate_ssh_keys(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        allowed = {"ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256"}
        for value in values:
            if len(value) > 4096 or "\n" in value or "\r" in value:
                raise ValueError("SSH public keys must be single-line and at most 4096 bytes")
            parts = value.strip().split()
            if len(parts) < 2 or parts[0] not in allowed:
                raise ValueError("unsupported SSH public key format")
            try:
                decoded = base64.b64decode(parts[1], validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError("invalid SSH public key encoding") from exc
            if len(decoded) < 16 or len(decoded) > 2048:
                raise ValueError("invalid SSH public key length")
            normalized.append(" ".join(parts[:3]))
        return normalized


class ProvisioningRequestCreate(StrictModel):
    product_id: UUID
    template_id: UUID
    organization_id: UUID
    target_cluster_id: UUID
    target_node_id: UUID | None = None
    target_vmid: int | None = Field(default=None, ge=100, le=999_999_999)
    target_name: str = Field(
        min_length=1,
        max_length=63,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9.-]{0,62}$",
    )
    ip_pool_id: UUID
    ip_address: IPv4Address | None = None
    cloud_init: CloudInitRequest
    start_after_create: bool = True


class ProvisioningStepResponse(BaseModel):
    order: int
    name: str
    status: ProvisioningStepStatus
    attempt_count: int
    pve_upid: str | None
    safe_result: dict[str, object]
    error_code: str | None
    error_summary: str | None
    started_at: datetime | None
    finished_at: datetime | None


class ProvisioningRequestResponse(BaseModel):
    id: UUID
    job_id: UUID
    status: ProvisioningStatus
    current_step: str
    product_id: UUID
    template_id: UUID
    organization_id: UUID
    target_cluster_id: UUID
    target_node_id: UUID | None
    target_vmid: int | None
    target_name: str
    ip_pool_id: UUID
    ip_address: str | None
    workload_id: UUID | None
    error_code: str | None
    error_summary: str | None
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    steps: list[ProvisioningStepResponse]


class ProvisioningRequestListResponse(BaseModel):
    items: list[ProvisioningRequestResponse]
