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
    device_label: str | None = Field(default=None, min_length=1, max_length=120)

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


class LoginResponse(BaseModel):
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"
    expires_in: int
    mfa_required: bool = False
    challenge_id: UUID | None = None
    methods: list[str] = Field(default_factory=list)


class MfaMethodResponse(BaseModel):
    id: UUID
    type: str
    name: str
    enrolled_at: datetime
    last_used_at: datetime | None


class MfaMethodListResponse(BaseModel):
    items: list[MfaMethodResponse]
    recovery_codes_remaining: int = Field(ge=0)
    policy_required: bool


class TotpEnrollmentStartResponse(BaseModel):
    method_id: UUID
    secret: str
    provisioning_uri: str


class TotpEnrollmentVerifyRequest(BaseModel):
    method_id: UUID
    code: str = Field(min_length=6, max_length=16)


class MfaCodeRequest(BaseModel):
    code: str = Field(min_length=6, max_length=64)


class MfaEnrollmentCompleteResponse(BaseModel):
    method: MfaMethodResponse
    recovery_codes: list[str]


class WebAuthnStartRequest(BaseModel):
    name: str = Field(default="Security key", min_length=1, max_length=120)


class WebAuthnStartResponse(BaseModel):
    challenge_id: UUID
    options: dict[str, object]


class WebAuthnFinishRequest(BaseModel):
    challenge_id: UUID
    credential: dict[str, object]


class MfaChallengeVerifyRequest(BaseModel):
    challenge_id: UUID
    method_type: str = Field(pattern="^(TOTP|WEBAUTHN|RECOVERY)$")
    code: str | None = Field(default=None, max_length=64)
    credential: dict[str, object] | None = None


class RecoveryCodesResponse(BaseModel):
    codes: list[str]


class StepUpStartRequest(BaseModel):
    action: str = Field(min_length=3, max_length=80, pattern=r"^[A-Z0-9_:.-]+$")


class StepUpStartResponse(BaseModel):
    challenge_id: UUID
    expires_in: int
    methods: list[str]


class StepUpVerifyRequest(MfaChallengeVerifyRequest):
    action: str = Field(min_length=3, max_length=80, pattern=r"^[A-Z0-9_:.-]+$")


class StepUpTokenResponse(BaseModel):
    step_up_token: str
    expires_in: int


class SessionResponse(BaseModel):
    id: UUID
    device_label: str | None
    created_ip: str | None
    user_agent: str | None
    created_at: datetime
    last_seen_at: datetime | None
    expires_at: datetime
    assurance_level: str
    current: bool = False


class SessionListResponse(BaseModel):
    items: list[SessionResponse]


class LoginEventResponse(BaseModel):
    id: UUID
    created_at: datetime
    outcome: str
    source_ip: str | None
    user_agent: str | None
    error_code: str | None


class LoginEventListResponse(BaseModel):
    items: list[LoginEventResponse]


class MfaPolicyResponse(BaseModel):
    admin_required: bool


class MfaPolicyUpdateRequest(BaseModel):
    admin_required: bool


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
    revoke_all_sessions: bool = True

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
