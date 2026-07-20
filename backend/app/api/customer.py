from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.dependencies import PrincipalDependency, get_db_session
from app.models.operation import PowerAction
from app.schemas.customer import (
    CustomerJobResponse,
    CustomerPowerActionRequest,
    CustomerVmDetailResponse,
    CustomerVmListResponse,
)
from app.services.customer_portal import CustomerOperationPublisher, CustomerPortalService

router = APIRouter(prefix="/api/v1/customer", tags=["customer-portal"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=255)]


def _service(
    request: Request,
    session: AsyncSession,
    principal: PrincipalDependency,
) -> CustomerPortalService:
    return CustomerPortalService(
        session=session,
        settings=cast(Settings, request.app.state.settings),
        principal=principal,
        publisher=cast(CustomerOperationPublisher, request.app.state.operation_publisher),
        request_id=request.state.request_id,
        source_ip=request.client.host if request.client is not None else "unknown",
    )


@router.get("/vms", response_model=CustomerVmListResponse)
async def list_customer_vms(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> CustomerVmListResponse:
    return CustomerVmListResponse(items=await _service(request, session, principal).list_vms())


@router.get("/vms/{vm_id}", response_model=CustomerVmDetailResponse)
async def get_customer_vm(
    vm_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> CustomerVmDetailResponse:
    return await _service(request, session, principal).get_vm(vm_id)


async def _power_action(
    *,
    vm_id: UUID,
    action: PowerAction,
    payload: CustomerPowerActionRequest,
    idempotency_key: str,
    request: Request,
    response: Response,
    session: AsyncSession,
    principal: PrincipalDependency,
) -> CustomerJobResponse:
    job = await _service(request, session, principal).request_power_action(
        vm_id=vm_id,
        action=action,
        idempotency_key=idempotency_key,
        reason=payload.reason,
        confirm_forced=payload.confirm_forced,
    )
    response.headers["Location"] = f"/api/v1/customer/jobs/{job.id}"
    response.headers["Retry-After"] = "2"
    response.headers["Cache-Control"] = "no-store"
    return job


@router.post(
    "/vms/{vm_id}/actions/start",
    response_model=CustomerJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_customer_vm(
    vm_id: UUID,
    payload: CustomerPowerActionRequest,
    idempotency_key: IdempotencyKey,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> CustomerJobResponse:
    return await _power_action(
        vm_id=vm_id,
        action=PowerAction.START,
        payload=payload,
        idempotency_key=idempotency_key,
        request=request,
        response=response,
        session=session,
        principal=principal,
    )


@router.post(
    "/vms/{vm_id}/actions/shutdown",
    response_model=CustomerJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def shutdown_customer_vm(
    vm_id: UUID,
    payload: CustomerPowerActionRequest,
    idempotency_key: IdempotencyKey,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> CustomerJobResponse:
    return await _power_action(
        vm_id=vm_id,
        action=PowerAction.SHUTDOWN,
        payload=payload,
        idempotency_key=idempotency_key,
        request=request,
        response=response,
        session=session,
        principal=principal,
    )


@router.post(
    "/vms/{vm_id}/actions/reboot",
    response_model=CustomerJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reboot_customer_vm(
    vm_id: UUID,
    payload: CustomerPowerActionRequest,
    idempotency_key: IdempotencyKey,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> CustomerJobResponse:
    return await _power_action(
        vm_id=vm_id,
        action=PowerAction.REBOOT,
        payload=payload,
        idempotency_key=idempotency_key,
        request=request,
        response=response,
        session=session,
        principal=principal,
    )


@router.post(
    "/vms/{vm_id}/actions/stop",
    response_model=CustomerJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def stop_customer_vm(
    vm_id: UUID,
    payload: CustomerPowerActionRequest,
    idempotency_key: IdempotencyKey,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> CustomerJobResponse:
    return await _power_action(
        vm_id=vm_id,
        action=PowerAction.STOP,
        payload=payload,
        idempotency_key=idempotency_key,
        request=request,
        response=response,
        session=session,
        principal=principal,
    )


@router.get("/jobs/{job_id}", response_model=CustomerJobResponse)
async def get_customer_job(
    job_id: UUID,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> CustomerJobResponse:
    response.headers["Cache-Control"] = "no-store"
    return await _service(request, session, principal).get_job(job_id)
