from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

TokenIdentifier = Annotated[
    str,
    Field(min_length=3, max_length=255, pattern=r"^[^\s=]+@[^\s=!]+![^\s=]+$"),
]


class ClusterCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    api_base_url: AnyHttpUrl
    token_identifier: TokenIdentifier
    token_secret: SecretStr = Field(min_length=1, max_length=1024)
    ca_bundle_pem: str | None = Field(default=None, max_length=262_144)

    @field_validator("api_base_url")
    @classmethod
    def require_https(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.scheme != "https" or value.username or value.password:
            raise ValueError("api_base_url must be an HTTPS URL without user information")
        if value.query or value.fragment:
            raise ValueError("api_base_url must not contain a query or fragment")
        return value


class ClusterUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    api_base_url: AnyHttpUrl | None = None
    token_identifier: TokenIdentifier | None = None
    token_secret: SecretStr | None = Field(default=None, min_length=1, max_length=1024)
    ca_bundle_pem: str | None = Field(default=None, max_length=262_144)
    clear_ca_bundle: bool = False
    version: int | None = Field(default=None, ge=1)

    @field_validator("api_base_url")
    @classmethod
    def require_https(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        if value is None:
            return None
        return ClusterCreate.require_https(value)


class CredentialSummary(BaseModel):
    token_identifier: str
    configured: bool = True
    last_used_at: datetime | None = None


class ClusterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    api_base_url: str
    is_active: bool
    ca_configured: bool
    last_connection_error_code: str | None
    last_connected_at: datetime | None
    credential: CredentialSummary
    created_at: datetime
    updated_at: datetime
    version: int


class ClusterListResponse(BaseModel):
    items: list[ClusterResponse]
    next_cursor: str | None = None


class ClusterRemovalBlock(BaseModel):
    code: str
    count: int = Field(ge=1)


class ClusterRemovalCheckResponse(BaseModel):
    cluster_id: UUID
    can_remove: bool
    blocks: list[ClusterRemovalBlock] = Field(default_factory=list)


class ConnectionTestResponse(BaseModel):
    reachable: bool
    tls_valid: bool
    authenticated: bool
    version: str | None = None
    release: str | None = None
    capabilities: dict[str, bool]


class NodeResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    node: str
    status: str | None = None
    cpu: float | None = None
    maxcpu: int | None = None
    mem: int | None = None
    maxmem: int | None = None
    disk: int | None = None
    maxdisk: int | None = None
    uptime: int | None = None


class NodeResourceOverview(BaseModel):
    node: str
    status: str | None = None
    cpu: float | None = Field(default=None, ge=0)
    maxcpu: int | None = Field(default=None, ge=0)
    memory_used_bytes: int | None = Field(default=None, ge=0)
    memory_total_bytes: int | None = Field(default=None, ge=0)
    disk_used_bytes: int | None = Field(default=None, ge=0)
    disk_total_bytes: int | None = Field(default=None, ge=0)
    load_average: list[float] = Field(default_factory=list, max_length=3)
    uptime_seconds: int | None = Field(default=None, ge=0)


class ClusterResourceOverview(BaseModel):
    cluster_id: UUID
    name: str
    connected: bool
    observed_at: datetime
    error_code: str | None = None
    node_count: int = Field(ge=0)
    guest_count: int = Field(ge=0)
    running_guest_count: int = Field(ge=0)
    qemu_count: int = Field(ge=0)
    lxc_count: int = Field(ge=0)
    storage_count: int = Field(ge=0)
    storage_used_bytes: int = Field(ge=0)
    storage_total_bytes: int = Field(ge=0)
    vm_storage_count: int = Field(ge=0)
    vm_storage_used_bytes: int = Field(ge=0)
    vm_storage_total_bytes: int = Field(ge=0)
    nodes: list[NodeResourceOverview] = Field(default_factory=list)


class ClusterResourceOverviewListResponse(BaseModel):
    items: list[ClusterResourceOverview]


NodeMetricRange = Literal["hour", "six_hours", "day", "week"]


class NodeMetricPoint(BaseModel):
    time: int = Field(gt=0)
    cpu_usage: float | None = Field(default=None, ge=0)
    server_load: float | None = Field(default=None, ge=0)
    memory_used_bytes: int | None = Field(default=None, ge=0)
    memory_total_bytes: int | None = Field(default=None, ge=0)
    network_receive_bps: float | None = Field(default=None, ge=0)
    network_transmit_bps: float | None = Field(default=None, ge=0)
    cpu_pressure_some: float | None = Field(default=None, ge=0)
    io_pressure_some: float | None = Field(default=None, ge=0)
    io_pressure_full: float | None = Field(default=None, ge=0)
    memory_pressure_some: float | None = Field(default=None, ge=0)
    memory_pressure_full: float | None = Field(default=None, ge=0)


class NodeMetricSeriesResponse(BaseModel):
    cluster_id: UUID
    node: str
    range: NodeMetricRange
    observed_at: datetime
    items: list[NodeMetricPoint]


class GuestResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    vmid: int
    node: str | None = None
    type: str
    name: str | None = None
    status: str | None = None
    cpu: float | None = Field(default=None, ge=0)
    maxcpu: int | None = Field(default=None, ge=0, le=65_535)
    mem: int | None = Field(default=None, ge=0, le=9_223_372_036_854_775_807)
    maxmem: int | None = Field(default=None, ge=0, le=9_223_372_036_854_775_807)
    disk: int | None = Field(default=None, ge=0, le=9_223_372_036_854_775_807)
    maxdisk: int | None = Field(default=None, ge=0, le=9_223_372_036_854_775_807)
    uptime: int | None = Field(default=None, ge=0, le=9_223_372_036_854_775_807)
    template: int | bool | None = None


class StorageResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def normalize_cluster_resource_capacity(cls, value: object) -> object:
        """Normalize Proxmox cluster-resource storage capacity field names."""
        if not isinstance(value, dict):
            return value

        data = dict(value)
        total = data.get("total")
        if total is None:
            total = data.get("maxdisk")
            if total is not None:
                data["total"] = total

        used = data.get("used")
        if used is None:
            used = data.get("disk")
            if used is not None:
                data["used"] = used

        if data.get("avail") is None and isinstance(total, int) and isinstance(used, int):
            data["avail"] = max(0, total - used)
        return data

    storage: str
    node: str | None = None
    type: str | None = None
    status: str | None = None
    total: int | None = None
    used: int | None = None
    avail: int | None = None
    shared: int | bool | None = None
    content: str | None = None


class NodeListResponse(BaseModel):
    items: list[NodeResponse]
    next_cursor: str | None = None


class GuestListResponse(BaseModel):
    items: list[GuestResponse]
    next_cursor: str | None = None


class StorageListResponse(BaseModel):
    items: list[StorageResponse]
    next_cursor: str | None = None
