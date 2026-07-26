from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Header, Request, Response, status

from app.core.config import Settings
from app.dependencies import PrincipalDependency, SessionDependency
from app.schemas.advanced_operations import (
    AdvancedCapabilitiesResponse,
    AdvancedFeature,
    AdvancedInspectionResponse,
    AdvancedOperationCreate,
    AdvancedOperationResponse,
    AdvancedPreviewRequest,
    AdvancedPreviewResponse,
)
from app.security.credentials import CredentialCipher
from app.security.step_up import require_step_up
from app.services.advanced_operations import AdvancedOperationService, AdvancedPublisher

router = APIRouter(prefix="/api/v1/admin/advanced", tags=["admin-advanced-operations"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=255)]
StepUpToken = Annotated[str | None, Header(alias="X-Step-Up-Token")]


def _service(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> AdvancedOperationService:
    return AdvancedOperationService(
        session=session,
        settings=cast(Settings, request.app.state.settings),
        cipher=cast(CredentialCipher, request.app.state.credential_cipher),
        principal=principal,
        publisher=cast(AdvancedPublisher, request.app.state.advanced_operation_publisher),
        request_id=request.state.request_id,
        source_ip=request.client.host if request.client is not None else "unknown",
        transport=request.app.state.proxmox_transport,
    )


@router.get("/capabilities", response_model=AdvancedCapabilitiesResponse)
async def capabilities(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> AdvancedCapabilitiesResponse:
    return _service(request, session, principal).capabilities()


@router.post("/preview", response_model=AdvancedPreviewResponse)
async def preview(
    payload: AdvancedPreviewRequest,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> AdvancedPreviewResponse:
    return await _service(request, session, principal).preview(payload)


@router.get(
    "/workloads/{workload_id}/inspection",
    response_model=AdvancedInspectionResponse,
)
async def inspect(
    workload_id: UUID,
    feature: AdvancedFeature,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> AdvancedInspectionResponse:
    return await _service(request, session, principal).inspect(workload_id, feature)


@router.post(
    "/operations",
    response_model=AdvancedOperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_operation(
    payload: AdvancedOperationCreate,
    idempotency_key: IdempotencyKey,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
    step_up_token: StepUpToken = None,
) -> AdvancedOperationResponse:
    service = _service(request, session, principal)
    operation_preview = await service.preview(payload.preview)
    if operation_preview.step_up_action is not None:
        await require_step_up(
            request=request,
            session=session,
            principal=principal,
            action=operation_preview.step_up_action,
            step_up_token=step_up_token,
        )
    item, _ = await service.create(payload, idempotency_key=idempotency_key)
    response.headers["Location"] = f"/api/v1/admin/advanced/operations/{item.operation_id}"
    return item


@router.get(
    "/operations/{operation_id}",
    response_model=AdvancedOperationResponse,
)
async def get_operation(
    operation_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> AdvancedOperationResponse:
    return await _service(request, session, principal).get(operation_id)
