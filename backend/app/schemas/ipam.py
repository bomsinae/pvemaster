from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, IPvAnyAddress

from app.models.ipam import IpAddressState, IpAllocationKind, IpAllocationStatus


class IpRangeRequest(BaseModel):
    start: IPvAnyAddress
    end: IPvAnyAddress
    reason: str | None = Field(default=None, max_length=255)


class IpPoolCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    cluster_id: UUID | None = None
    cidr: str = Field(min_length=3, max_length=64)
    gateway: IPvAnyAddress | None = None
    dns_servers: list[IPvAnyAddress] = Field(default_factory=list, max_length=8)
    bridge: str = Field(min_length=1, max_length=64)
    vlan_tag: int | None = Field(default=None, ge=1, le=4094)
    excluded_ranges: list[IpRangeRequest] = Field(default_factory=list, max_length=128)
    allocation_strategy: Literal["SEQUENTIAL", "RANDOM"] = "SEQUENTIAL"
    quarantine_seconds: int = Field(default=600, ge=0, le=2_592_000)


class IpPoolUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    cluster_id: UUID | None = None
    cidr: str | None = Field(default=None, min_length=3, max_length=64)
    gateway: IPvAnyAddress | None = None
    dns_servers: list[IPvAnyAddress] | None = Field(default=None, max_length=8)
    bridge: str | None = Field(default=None, min_length=1, max_length=64)
    vlan_tag: int | None = Field(default=None, ge=1, le=4094)
    allocation_strategy: Literal["SEQUENTIAL", "RANDOM"] | None = None
    quarantine_seconds: int | None = Field(default=None, ge=0, le=2_592_000)
    version: int = Field(ge=1)


class IpPoolResponse(BaseModel):
    id: UUID
    name: str
    cluster_id: UUID | None
    cidr: str
    prefix_length: int
    gateway: str | None
    dns_servers: list[str]
    bridge: str
    vlan_tag: int | None
    ip_family: int
    allocation_strategy: str
    quarantine_seconds: int
    is_active: bool
    allocated_count: int
    quarantined_count: int
    availability_status: str
    version: int


class IpPoolListResponse(BaseModel):
    items: list[IpPoolResponse]


class IpReservationRequest(BaseModel):
    address: IPvAnyAddress
    reason: str = Field(min_length=1, max_length=255)


class IpAllocationRequest(BaseModel):
    workload_id: UUID
    address: IPvAnyAddress | None = None


class IpAddressResponse(BaseModel):
    id: UUID
    pool_id: UUID
    address: str
    state: IpAddressState
    reserved_for: str | None
    quarantined_until: datetime | None
    workload_id: UUID | None


class IpAddressListResponse(BaseModel):
    items: list[IpAddressResponse]


class IpAllocationResponse(BaseModel):
    id: UUID
    pool_id: UUID
    ip_address_id: UUID
    address: str
    workload_id: UUID
    kind: IpAllocationKind
    status: IpAllocationStatus
    allocated_at: datetime
    released_at: datetime | None
    quarantined_until: datetime | None


class IpReleaseRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
