from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from app.models.auth import UserRole

Password = Annotated[SecretStr, Field(min_length=12, max_length=1024)]


def normalize_email(value: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) > 320 or "@" not in normalized or normalized.startswith("@"):
        raise ValueError("email must be a valid address")
    return normalized


class LoginRequest(BaseModel):
    email: str
    password: SecretStr = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class RefreshRequest(BaseModel):
    refresh_token: SecretStr = Field(min_length=40, max_length=512)


class LogoutRequest(RefreshRequest):
    pass


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    display_name: str
    role: UserRole
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime
    version: int
    organization_names: list[str] = Field(default_factory=list)


class UserListResponse(BaseModel):
    items: list[UserResponse]


class UserCreate(BaseModel):
    email: str
    display_name: str = Field(min_length=1, max_length=120)
    role: UserRole
    password: Password

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    role: UserRole | None = None
    is_active: bool | None = None
    version: int | None = Field(default=None, ge=1)


class ChangePasswordRequest(BaseModel):
    current_password: SecretStr = Field(min_length=1, max_length=1024)
    new_password: Password

    @model_validator(mode="after")
    def passwords_must_differ(self) -> Self:
        if self.current_password.get_secret_value() == self.new_password.get_secret_value():
            raise ValueError("new_password must differ from current_password")
        return self


class AdminPasswordResetRequest(BaseModel):
    new_password: Password


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class OrganizationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    is_active: bool | None = None
    version: int = Field(ge=1)

    @model_validator(mode="after")
    def has_change(self) -> Self:
        if self.name is None and self.is_active is None:
            raise ValueError("at least one organization field must be changed")
        return self


class OrganizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    version: int


class OrganizationListResponse(BaseModel):
    items: list[OrganizationResponse]
    total: int = Field(ge=0)
    limit: int | None = Field(default=None, ge=1)
    offset: int = Field(default=0, ge=0)


class OrganizationMemberCreate(BaseModel):
    user_id: UUID


class OrganizationMemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    user_id: UUID
    created_at: datetime


class OrganizationMemberDetailResponse(OrganizationMemberResponse):
    email: str
    display_name: str
    role: UserRole
    is_active: bool


class OrganizationMemberListResponse(BaseModel):
    items: list[OrganizationMemberDetailResponse]
