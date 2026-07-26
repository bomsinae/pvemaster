from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from app.models.auth import OrganizationRole
from app.schemas.auth import normalize_email


class OrganizationMembershipResponse(BaseModel):
    id: UUID
    organization_id: UUID
    organization_name: str
    user_id: UUID
    email: str
    display_name: str
    organization_role: OrganizationRole
    status: str
    expires_at: datetime | None
    created_at: datetime
    version: int
    permissions: list[str] = Field(default_factory=list)


class OrganizationRoleUpdate(BaseModel):
    organization_role: OrganizationRole
    status: str | None = Field(default=None, pattern="^(ACTIVE|SUSPENDED)$")
    expires_at: datetime | None = None
    version: int = Field(ge=1)


class OrganizationInvitationCreate(BaseModel):
    email: str
    organization_role: OrganizationRole
    expires_in_hours: int = Field(default=72, ge=1, le=168)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class OrganizationInvitationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    email: str
    organization_role: OrganizationRole
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    accept_token: str | None = None


class OrganizationInvitationAccept(BaseModel):
    token: SecretStr = Field(min_length=32, max_length=512)


class OrganizationQuotaUpdate(BaseModel):
    max_vcpu: int = Field(ge=0, le=100_000)
    max_memory_bytes: int = Field(ge=0, le=9_000_000_000_000_000)
    max_disk_bytes: int = Field(ge=0, le=9_000_000_000_000_000)
    max_vms: int = Field(ge=0, le=100_000)
    max_ips: int = Field(ge=0, le=1_000_000)
    max_backup_bytes: int = Field(ge=0, le=9_000_000_000_000_000)
    version: int | None = Field(default=None, ge=1)


class QuotaValues(BaseModel):
    vcpu: int = Field(ge=0)
    memory_bytes: int = Field(ge=0)
    disk_bytes: int = Field(ge=0)
    vms: int = Field(ge=0)
    ips: int = Field(ge=0)
    backup_bytes: int = Field(ge=0)


class OrganizationQuotaResponse(BaseModel):
    organization_id: UUID
    limits: QuotaValues
    usage: QuotaValues
    reserved: QuotaValues
    remaining: QuotaValues
    version: int
    updated_at: datetime | None
    captured_at: datetime


class ApprovalPolicyUpdate(BaseModel):
    request_type: str = Field(min_length=1, max_length=40, pattern="^[A-Z][A-Z0-9_]+$")
    requires_approval: bool = True
    minimum_role: OrganizationRole
    version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def role_can_approve(self) -> Self:
        if self.minimum_role not in {
            OrganizationRole.ORG_OWNER,
            OrganizationRole.ORG_ADMIN,
            OrganizationRole.ORG_OPERATOR,
        }:
            raise ValueError("minimum_role must be an approving organization role")
        return self


class ApprovalPolicyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    request_type: str
    requires_approval: bool
    minimum_role: OrganizationRole
    updated_at: datetime
    version: int


class OrganizationActivityResponse(BaseModel):
    id: UUID
    created_at: datetime
    action: str
    outcome: str
    actor_user_id: UUID | None
    resource_type: str | None
    resource_id: str | None
    summary: dict[str, object] | None
