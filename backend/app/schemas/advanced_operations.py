from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AdvancedFeature(StrEnum):
    SNAPSHOT = "SNAPSHOT"
    MIGRATION = "MIGRATION"
    HA = "HA"
    NODE_MAINTENANCE = "NODE_MAINTENANCE"
    BULK = "BULK"
    GUEST_CONFIG = "GUEST_CONFIG"
    FIREWALL_SDN = "FIREWALL_SDN"


class AdvancedFeatureCapability(BaseModel):
    feature: AdvancedFeature
    enabled: bool
    mode: str
    actions: list[str]


class AdvancedCapabilitiesResponse(BaseModel):
    items: list[AdvancedFeatureCapability]


class AdvancedPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature: AdvancedFeature
    action: Annotated[str, Field(min_length=2, max_length=32, pattern=r"^[A-Z0-9_]+$")]
    workload_ids: Annotated[list[UUID], Field(min_length=1, max_length=100)]
    options: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_targets(self) -> "AdvancedPreviewRequest":
        if len(set(self.workload_ids)) != len(self.workload_ids):
            raise ValueError("workload_ids must be unique")
        return self


class AdvancedTargetSnapshot(BaseModel):
    workload_id: UUID
    name: str
    kind: str
    node: str
    power_state: str
    version: int


class AdvancedPreviewResponse(BaseModel):
    feature: AdvancedFeature
    action: str
    enabled: bool
    executable: bool
    targets: list[AdvancedTargetSnapshot]
    warnings: list[str]
    blockers: list[str]
    required_confirmation: str
    step_up_action: str | None
    requested_state: dict[str, object]


class AdvancedOperationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview: AdvancedPreviewRequest
    confirmation: Annotated[str, Field(min_length=1, max_length=255)]


class AdvancedOperationResponse(BaseModel):
    operation_id: UUID
    feature: AdvancedFeature
    action: str
    status: str
    targets: list[AdvancedTargetSnapshot]
    requested_state: dict[str, object]
    observed_state: dict[str, object]
    error_code: str | None


class AdvancedInspectionResponse(BaseModel):
    feature: AdvancedFeature
    scope: str
    workload_id: UUID
    items: list[dict[str, object]]
    related: dict[str, list[dict[str, object]]] = Field(default_factory=dict)
