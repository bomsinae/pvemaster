from datetime import datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response, status

from app.core.config import Settings
from app.dependencies import PrincipalDependency, SessionDependency
from app.schemas.operation_center import (
    OperationActionResponse,
    OperationAssignRequest,
    OperationCenterDetailResponse,
    OperationCenterItemResponse,
    OperationCenterListResponse,
    OperationResolveRequest,
    OperationVersionRequest,
)
from app.services.operation_center import OperationCenterService, OperationPublisher
from app.services.outbox import BACKUP_EVENT, POWER_EVENT, RESTORE_EVENT

router = APIRouter(
    prefix="/api/v1/admin/operations",
    tags=["admin-operation-center"],
)


def _service(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> OperationCenterService:
    return OperationCenterService(
        session=session,
        settings=cast(Settings, request.app.state.settings),
        principal=principal,
        publishers={
            POWER_EVENT: cast(OperationPublisher, request.app.state.operation_publisher),
            BACKUP_EVENT: cast(OperationPublisher, request.app.state.backup_publisher),
            RESTORE_EVENT: cast(OperationPublisher, request.app.state.restore_publisher),
        },
        provisioning_publisher=cast(
            OperationPublisher,
            request.app.state.provisioning_publisher,
        ),
        request_id=request.state.request_id,
        source_ip=request.client.host if request.client is not None else "unknown",
    )


@router.get("", response_model=OperationCenterListResponse)
async def list_operations(
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
    operation_status: Annotated[str | None, Query(alias="status")] = None,
    operation_type: str | None = None,
    cluster_id: UUID | None = None,
    organization_id: UUID | None = None,
    actor_id: UUID | None = None,
    error_code: str | None = None,
    requested_from: datetime | None = None,
    requested_to: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OperationCenterListResponse:
    response.headers["Cache-Control"] = "no-store"
    items, total = await _service(request, session, principal).list_operations(
        status=operation_status,
        operation_type=operation_type,
        cluster_id=cluster_id,
        organization_id=organization_id,
        actor_id=actor_id,
        error_code=error_code,
        requested_from=requested_from,
        requested_to=requested_to,
        limit=limit,
        offset=offset,
    )
    return OperationCenterListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{operation_id}", response_model=OperationCenterDetailResponse)
async def get_operation(
    operation_id: UUID,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> OperationCenterDetailResponse:
    response.headers["Cache-Control"] = "no-store"
    return await _service(request, session, principal).get_detail(operation_id)


@router.post("/{operation_id}/cancel", response_model=OperationCenterItemResponse)
async def cancel_operation(
    operation_id: UUID,
    payload: OperationVersionRequest,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> OperationCenterItemResponse:
    response.headers["Cache-Control"] = "no-store"
    return await _service(request, session, principal).cancel(
        operation_id,
        version=payload.version,
    )


@router.post(
    "/{operation_id}/retry",
    response_model=OperationActionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_operation(
    operation_id: UUID,
    payload: OperationVersionRequest,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> OperationActionResponse:
    response.headers["Cache-Control"] = "no-store"
    operation, created_id = await _service(request, session, principal).retry(
        operation_id,
        version=payload.version,
    )
    return OperationActionResponse(
        operation=operation,
        created_operation_id=created_id,
    )


@router.post("/{operation_id}/assign", response_model=OperationCenterItemResponse)
async def assign_operation(
    operation_id: UUID,
    payload: OperationAssignRequest,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> OperationCenterItemResponse:
    response.headers["Cache-Control"] = "no-store"
    return await _service(request, session, principal).assign(
        operation_id,
        assigned_to_id=payload.assigned_to_id,
        version=payload.version,
    )


@router.post("/{operation_id}/acknowledge", response_model=OperationCenterItemResponse)
async def acknowledge_operation(
    operation_id: UUID,
    payload: OperationVersionRequest,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> OperationCenterItemResponse:
    response.headers["Cache-Control"] = "no-store"
    return await _service(request, session, principal).acknowledge(
        operation_id,
        version=payload.version,
    )


@router.post("/{operation_id}/resolve-manually", response_model=OperationCenterItemResponse)
async def resolve_operation(
    operation_id: UUID,
    payload: OperationResolveRequest,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> OperationCenterItemResponse:
    response.headers["Cache-Control"] = "no-store"
    return await _service(request, session, principal).resolve_manually(
        operation_id,
        resolution_note=payload.resolution_note,
        version=payload.version,
    )
