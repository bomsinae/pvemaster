import base64
import binascii
import ipaddress
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.self_service import ServiceRequestStatus, ServiceRequestType


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SshPublicKeyCreate(StrictModel):
    label: str = Field(min_length=1, max_length=80, pattern=r"^[\w .@+-]+$")
    public_key: str = Field(min_length=32, max_length=4096)

    @field_validator("public_key")
    @classmethod
    def validate_public_key(cls, value: str) -> str:
        if "\n" in value or "\r" in value or "PRIVATE KEY" in value.upper():
            raise ValueError("only a single-line SSH public key is accepted")
        parts = value.strip().split()
        allowed = {"ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256"}
        if len(parts) < 2 or parts[0] not in allowed:
            raise ValueError("unsupported SSH public key format")
        try:
            decoded = base64.b64decode(parts[1], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("invalid SSH public key encoding") from exc
        if len(decoded) < 16 or len(decoded) > 2048:
            raise ValueError("invalid SSH public key length")
        return " ".join(parts[:3])


class SshPublicKeyResponse(BaseModel):
    id: UUID
    label: str
    fingerprint: str
    public_key: str
    created_at: datetime


class SshPublicKeyListResponse(BaseModel):
    items: list[SshPublicKeyResponse]


class SecurityGroupRule(StrictModel):
    direction: Literal["IN", "OUT"]
    action: Literal["ACCEPT", "DROP"]
    protocol: Literal["tcp", "udp", "icmp"]
    source: str | None = Field(default=None, max_length=64)
    destination: str | None = Field(default=None, max_length=64)
    ports: list[int] = Field(default_factory=list, max_length=32)

    @field_validator("source", "destination")
    @classmethod
    def validate_network(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return str(ipaddress.ip_network(value, strict=False))

    @field_validator("ports")
    @classmethod
    def validate_ports(cls, value: list[int]) -> list[int]:
        if any(port < 1 or port > 65535 for port in value):
            raise ValueError("ports must be between 1 and 65535")
        return sorted(set(value))

    @model_validator(mode="after")
    def validate_protocol_ports(self) -> "SecurityGroupRule":
        if self.protocol == "icmp" and self.ports:
            raise ValueError("ICMP rules cannot include ports")
        return self


class SecurityGroupCreate(StrictModel):
    organization_id: UUID | None = None
    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_. -]+$")
    description: str = Field(default="", max_length=300)
    rules: list[SecurityGroupRule] = Field(min_length=1, max_length=64)
    is_global: bool = False

    @model_validator(mode="after")
    def validate_scope(self) -> "SecurityGroupCreate":
        if self.is_global == (self.organization_id is not None):
            raise ValueError("select exactly one global or organization scope")
        return self


class SecurityGroupResponse(BaseModel):
    id: UUID
    organization_id: UUID | None
    name: str
    description: str
    rules: list[SecurityGroupRule]
    is_global: bool
    is_enabled: bool
    version: int


class SecurityGroupListResponse(BaseModel):
    items: list[SecurityGroupResponse]


class ServiceRequestInput(StrictModel):
    ssh_key_id: UUID | None = None
    hostname: str | None = Field(
        default=None,
        min_length=1,
        max_length=63,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9.-]{0,62}$",
    )
    description: str | None = Field(
        default=None,
        max_length=300,
        pattern=r"^[\w .,:;@()+/=-]*$",
    )
    rdns: str | None = Field(
        default=None,
        min_length=1,
        max_length=253,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$",
    )
    security_group_id: UUID | None = None
    backup_run_id: UUID | None = None
    cpu_cores: int | None = Field(default=None, ge=1, le=128)
    memory_bytes: int | None = Field(default=None, ge=268_435_456, le=2_199_023_255_552)
    disk_bytes: int | None = Field(default=None, ge=1_073_741_824, le=70_368_744_177_664)
    confirmation: str | None = Field(default=None, max_length=255)
    reason: str | None = Field(default=None, max_length=500)


class ServiceRequestCreate(StrictModel):
    request_type: ServiceRequestType
    input: ServiceRequestInput


class ServiceRequestPreviewResponse(BaseModel):
    request_type: ServiceRequestType
    requires_approval: bool = True
    requires_step_up: bool
    cancellable_until: Literal["APPROVAL"]
    impacts: list[str]
    current: dict[str, object]
    requested: dict[str, object]


class ApprovalStepResponse(BaseModel):
    order: int
    approver_role: str
    decision: str | None
    reason: str | None
    decided_at: datetime | None


class ServiceRequestResponse(BaseModel):
    id: UUID
    request_type: ServiceRequestType
    vm_id: UUID
    vm_name: str
    organization_name: str
    input: dict[str, object]
    impact: dict[str, object]
    status: ServiceRequestStatus
    operation_id: UUID | None
    error_code: str | None
    result_summary: str | None
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    version: int
    approvals: list[ApprovalStepResponse]


class ServiceRequestListResponse(BaseModel):
    items: list[ServiceRequestResponse]


class ServiceRequestCancel(StrictModel):
    version: int = Field(ge=1)


class ServiceRequestDecision(StrictModel):
    version: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=500)
    approved_input: ServiceRequestInput | None = None


class ServiceRequestExecution(StrictModel):
    version: int = Field(ge=1)
    outcome: Literal["START", "SUCCEEDED", "FAILED"]
    summary: str = Field(min_length=3, max_length=500)
