from datetime import UTC, datetime, timedelta
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.dependencies import PrincipalDependency, get_db_session
from app.models.operation import OperationStatus, PowerAction
from app.schemas.customer import (
    CustomerJobListResponse,
    CustomerJobResponse,
    CustomerMetricRange,
    CustomerMetricSeriesResponse,
    CustomerNotificationPreferenceListResponse,
    CustomerNotificationPreferenceResponse,
    CustomerNotificationPreferenceUpdate,
    CustomerPowerActionRequest,
    CustomerVmDetailResponse,
    CustomerVmListResponse,
)
from app.security.step_up import require_step_up
from app.services.customer_notifications import CustomerNotificationPreferenceService
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


def _notification_service(
    request: Request,
    session: AsyncSession,
    principal: PrincipalDependency,
) -> CustomerNotificationPreferenceService:
    return CustomerNotificationPreferenceService(
        session=session,
        principal=principal,
        request_id=request.state.request_id,
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
    x_step_up_token: Annotated[str | None, Header(alias="X-Step-Up-Token")] = None,
) -> CustomerJobResponse:
    await require_step_up(
        request=request,
        session=session,
        principal=principal,
        action="FORCED_STOP",
        step_up_token=x_step_up_token,
    )
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


@router.get("/jobs", response_model=CustomerJobListResponse)
async def list_customer_jobs(
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    vm_id: UUID | None = None,
    job_status: Annotated[OperationStatus | None, Query(alias="status")] = None,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
) -> CustomerJobListResponse:
    if any(value is not None and value.tzinfo is None for value in (started_at, ended_at)):
        raise AppError(422, "INVALID_TIME_RANGE", "The time range must include a timezone.")
    if started_at and ended_at and started_at > ended_at:
        raise AppError(422, "INVALID_TIME_RANGE", "The time range is invalid.")
    if started_at and started_at < datetime.now(UTC) - timedelta(days=365):
        raise AppError(422, "TIME_RANGE_TOO_LARGE", "The maximum history range is 365 days.")
    response.headers["Cache-Control"] = "no-store"
    items, total = await _service(request, session, principal).list_jobs(
        limit=limit,
        offset=offset,
        vm_id=vm_id,
        status=job_status,
        started_at=started_at,
        ended_at=ended_at,
    )
    return CustomerJobListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
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


@router.get(
    "/vms/{vm_id}/metrics",
    response_model=CustomerMetricSeriesResponse,
)
async def get_customer_vm_metrics(
    vm_id: UUID,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
    range: CustomerMetricRange = "day",
) -> CustomerMetricSeriesResponse:
    response.headers["Cache-Control"] = "private, max-age=30"
    return await _service(request, session, principal).metrics(vm_id, range)


@router.get(
    "/notification-preferences",
    response_model=CustomerNotificationPreferenceListResponse,
)
async def customer_notification_preferences(
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> CustomerNotificationPreferenceListResponse:
    response.headers["Cache-Control"] = "no-store"
    return await _notification_service(request, session, principal).list()


@router.put(
    "/notification-preferences",
    response_model=CustomerNotificationPreferenceResponse,
)
async def update_customer_notification_preference(
    payload: CustomerNotificationPreferenceUpdate,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> CustomerNotificationPreferenceResponse:
    response.headers["Cache-Control"] = "no-store"
    return await _notification_service(request, session, principal).update(payload)
