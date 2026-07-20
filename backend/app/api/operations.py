from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.dependencies import PrincipalDependency, get_db_session
from app.models.operation import AdminVmAction, PowerAction
from app.schemas.operation import (
    JobResponse,
    PowerActionRequest,
    VmDeleteRequest,
    VmSpecUpdateRequest,
)
from app.services.operations import OperationPublisher, OperationService

router = APIRouter(tags=["workload-power-operations"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=255)]


def _source(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def _service(
    request: Request,
    session: AsyncSession,
    principal: PrincipalDependency,
) -> OperationService:
    return OperationService(
        session=session,
        settings=cast(Settings, request.app.state.settings),
        principal=principal,
        publisher=cast(OperationPublisher, request.app.state.operation_publisher),
        request_id=request.state.request_id,
        source_ip=_source(request),
    )


async def _request_action(
    *,
    workload_id: UUID,
    action: PowerAction,
    payload: PowerActionRequest,
    idempotency_key: str,
    request: Request,
    response: Response,
    session: AsyncSession,
    principal: PrincipalDependency,
) -> JobResponse:
    job, _created = await _service(request, session, principal).request_power_action(
        workload_id=workload_id,
        action=action,
        idempotency_key=idempotency_key,
        reason=payload.reason,
    )
    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    response.headers["Retry-After"] = "2"
    response.headers["Cache-Control"] = "no-store"
    return job


@router.post(
    "/api/v1/admin/workloads/{workload_id}/actions/{action}",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_workload_power_action(
    workload_id: UUID,
    action: PowerAction,
    payload: PowerActionRequest,
    idempotency_key: IdempotencyKey,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> JobResponse:
    return await _request_action(
        workload_id=workload_id,
        action=action,
        payload=payload,
        idempotency_key=idempotency_key,
        request=request,
        response=response,
        session=session,
        principal=principal,
    )


@router.post(
    "/api/v1/admin/vms/{vm_id}/actions/start",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    deprecated=True,
)
async def start_vm(
    vm_id: UUID,
    payload: PowerActionRequest,
    idempotency_key: IdempotencyKey,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> JobResponse:
    return await _request_action(
        workload_id=vm_id,
        action=PowerAction.START,
        payload=payload,
        idempotency_key=idempotency_key,
        request=request,
        response=response,
        session=session,
        principal=principal,
    )


@router.post(
    "/api/v1/admin/vms/{vm_id}/actions/shutdown",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    deprecated=True,
)
async def shutdown_vm(
    vm_id: UUID,
    payload: PowerActionRequest,
    idempotency_key: IdempotencyKey,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> JobResponse:
    return await _request_action(
        workload_id=vm_id,
        action=PowerAction.SHUTDOWN,
        payload=payload,
        idempotency_key=idempotency_key,
        request=request,
        response=response,
        session=session,
        principal=principal,
    )


@router.post(
    "/api/v1/admin/vms/{vm_id}/actions/stop",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    deprecated=True,
)
async def stop_vm(
    vm_id: UUID,
    payload: PowerActionRequest,
    idempotency_key: IdempotencyKey,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> JobResponse:
    return await _request_action(
        workload_id=vm_id,
        action=PowerAction.STOP,
        payload=payload,
        idempotency_key=idempotency_key,
        request=request,
        response=response,
        session=session,
        principal=principal,
    )


@router.post(
    "/api/v1/admin/vms/{vm_id}/actions/reboot",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    deprecated=True,
)
async def reboot_vm(
    vm_id: UUID,
    payload: PowerActionRequest,
    idempotency_key: IdempotencyKey,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> JobResponse:
    return await _request_action(
        workload_id=vm_id,
        action=PowerAction.REBOOT,
        payload=payload,
        idempotency_key=idempotency_key,
        request=request,
        response=response,
        session=session,
        principal=principal,
    )


@router.post(
    "/api/v1/admin/vms/{vm_id}/actions/reset",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    deprecated=True,
)
async def reset_vm(
    vm_id: UUID,
    payload: PowerActionRequest,
    idempotency_key: IdempotencyKey,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> JobResponse:
    return await _request_action(
        workload_id=vm_id,
        action=PowerAction.RESET,
        payload=payload,
        idempotency_key=idempotency_key,
        request=request,
        response=response,
        session=session,
        principal=principal,
    )


@router.get("/api/v1/jobs/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: UUID,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> JobResponse:
    response.headers["Cache-Control"] = "no-store"
    return await _service(request, session, principal).get_job(job_id)


@router.patch(
    "/api/v1/admin/vms/{vm_id}/spec",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def update_vm_spec(
    vm_id: UUID,
    payload: VmSpecUpdateRequest,
    idempotency_key: IdempotencyKey,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> JobResponse:
    job, _ = await _service(request, session, principal).request_admin_action(
        vm_id=vm_id,
        action=AdminVmAction.UPDATE_SPEC,
        idempotency_key=idempotency_key,
        payload=payload,
    )
    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    return job


@router.delete(
    "/api/v1/admin/vms/{vm_id}",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def delete_vm(
    vm_id: UUID,
    payload: VmDeleteRequest,
    idempotency_key: IdempotencyKey,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> JobResponse:
    job, _ = await _service(request, session, principal).request_admin_action(
        vm_id=vm_id,
        action=AdminVmAction.DELETE,
        idempotency_key=idempotency_key,
        payload=payload,
    )
    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    return job
