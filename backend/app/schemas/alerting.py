from datetime import datetime
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator


class AlertEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    event_type: str
    actor_user_id: UUID | None
    note: str | None
    details: dict[str, object]
    created_at: datetime


class AlertResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    type: str
    severity: str
    status: str
    resource_type: str
    resource_id: str | None
    organization_id: UUID | None
    workload_id: UUID | None
    message: str
    details: dict[str, object]
    occurrence_count: int
    assigned_to_id: UUID | None
    acknowledged_at: datetime | None
    silenced_until: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None
    version: int
    events: list[AlertEventResponse] = Field(default_factory=list)


class AlertListResponse(BaseModel):
    items: list[AlertResponse]
    total: int


class AlertActionRequest(BaseModel):
    note: str | None = Field(default=None, max_length=1000)
    assigned_to_id: UUID | None = None
    silenced_until: datetime | None = None
    version: int = Field(ge=1)


class MaintenanceWindowRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    target_type: str = Field(min_length=1, max_length=50)
    target_id: str | None = Field(default=None, max_length=120)
    organization_id: UUID | None = None
    starts_at: datetime
    ends_at: datetime
    suppress_notifications: bool = True

    @model_validator(mode="after")
    def dates_are_ordered(self) -> "MaintenanceWindowRequest":
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class MaintenanceWindowResponse(MaintenanceWindowRequest):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    created_by_id: UUID
    created_at: datetime


class NotificationChannelRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: str = Field(pattern="^(WEBHOOK|EMAIL)$")
    organization_id: UUID | None = None
    webhook_url: AnyHttpUrl | None = None
    email: str | None = Field(default=None, max_length=320)
    secret: str | None = Field(default=None, max_length=1024)
    is_enabled: bool = True

    @model_validator(mode="after")
    def required_configuration(self) -> "NotificationChannelRequest":
        if self.type == "WEBHOOK" and self.webhook_url is None:
            raise ValueError("webhook_url is required")
        if self.type == "EMAIL" and (self.email is None or "@" not in self.email):
            raise ValueError("a valid email is required")
        return self


class NotificationChannelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    organization_id: UUID | None
    name: str
    type: str
    is_enabled: bool
    configured: bool = True
    created_at: datetime
    updated_at: datetime


class NotificationRuleRequest(BaseModel):
    channel_id: UUID
    organization_id: UUID | None = None
    event_types: list[str] = Field(default_factory=list, max_length=50)
    severities: list[str] = Field(default_factory=list, max_length=10)
    quiet_hours: dict[str, object] = Field(default_factory=dict)
    escalation_minutes: int | None = Field(default=None, ge=1, le=10080)
    is_enabled: bool = True


class NotificationRuleResponse(NotificationRuleRequest):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    created_at: datetime


class NotificationDeliveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    alert_event_id: UUID
    channel_id: UUID
    status: str
    attempt_count: int
    next_attempt_at: datetime
    delivered_at: datetime | None
    last_error_code: str | None
