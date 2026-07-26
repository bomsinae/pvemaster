from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.dependencies import PrincipalDependency, get_db_session
from app.models.auth import MfaMethod
from app.models.self_service import ServiceRequestType
from app.schemas.self_service import (
    SecurityGroupCreate,
    SecurityGroupListResponse,
    SecurityGroupResponse,
    ServiceRequestCancel,
    ServiceRequestCreate,
    ServiceRequestDecision,
    ServiceRequestExecution,
    ServiceRequestListResponse,
    ServiceRequestPreviewResponse,
    ServiceRequestResponse,
    SshPublicKeyCreate,
    SshPublicKeyListResponse,
    SshPublicKeyResponse,
)
from app.security.step_up import require_step_up
from app.services.self_service import AdminSelfService, CustomerSelfService

customer_router = APIRouter(prefix="/api/v1/customer", tags=["customer-self-service"])
admin_router = APIRouter(prefix="/api/v1/admin", tags=["admin-self-service"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=255)]
StepUpToken = Annotated[str | None, Header(alias="X-Step-Up-Token")]


def _customer_service(
    request: Request,
    session: AsyncSession,
    principal: PrincipalDependency,
) -> CustomerSelfService:
    return CustomerSelfService(
        session=session,
        principal=principal,
        request_id=request.state.request_id,
        source_ip=request.client.host if request.client is not None else "unknown",
    )


def _admin_service(
    request: Request,
    session: AsyncSession,
    principal: PrincipalDependency,
) -> AdminSelfService:
    return AdminSelfService(
        session=session,
        principal=principal,
        request_id=request.state.request_id,
        source_ip=request.client.host if request.client is not None else "unknown",
    )


@customer_router.get("/ssh-keys", response_model=SshPublicKeyListResponse)
async def list_customer_ssh_keys(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> SshPublicKeyListResponse:
    return SshPublicKeyListResponse(
        items=await _customer_service(request, session, principal).list_keys()
    )


@customer_router.post(
    "/vms/{vm_id}/ssh-keys",
    response_model=SshPublicKeyResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_customer_ssh_key(
    vm_id: UUID,
    payload: SshPublicKeyCreate,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> SshPublicKeyResponse:
    return await _customer_service(request, session, principal).create_key(vm_id, payload)


@customer_router.delete("/ssh-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_customer_ssh_key(
    key_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> Response:
    await _customer_service(request, session, principal).revoke_key(key_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@customer_router.get(
    "/vms/{vm_id}/security-groups",
    response_model=SecurityGroupListResponse,
)
async def list_customer_security_groups(
    vm_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> SecurityGroupListResponse:
    return SecurityGroupListResponse(
        items=await _customer_service(request, session, principal).list_security_groups(vm_id)
    )


@customer_router.post(
    "/vms/{vm_id}/service-requests/preview",
    response_model=ServiceRequestPreviewResponse,
)
async def preview_customer_service_request(
    vm_id: UUID,
    payload: ServiceRequestCreate,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> ServiceRequestPreviewResponse:
    return await _customer_service(request, session, principal).preview(vm_id, payload)


@customer_router.post(
    "/vms/{vm_id}/service-requests",
    response_model=ServiceRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_customer_service_request(
    vm_id: UUID,
    payload: ServiceRequestCreate,
    idempotency_key: IdempotencyKey,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
    step_up_token: StepUpToken = None,
) -> ServiceRequestResponse:
    if payload.request_type in {
        ServiceRequestType.RESTORE_REQUEST,
        ServiceRequestType.REINSTALL,
    }:
        method_count = await session.scalar(
            select(func.count())
            .select_from(MfaMethod)
            .where(
                MfaMethod.user_id == principal.user_id,
                MfaMethod.disabled_at.is_(None),
            )
        )
        if not method_count:
            raise AppError(
                403,
                "MFA_ENROLLMENT_REQUIRED",
                "Enroll MFA before requesting a reinstall or restore.",
                details={
                    "action": (
                        f"customer_service_request:{payload.request_type.value.lower()}"
                    )
                },
            )
        await require_step_up(
            request=request,
            session=session,
            principal=principal,
            action=f"customer_service_request:{payload.request_type.value.lower()}",
            step_up_token=step_up_token,
        )
    item = await _customer_service(request, session, principal).create_request(
        vm_id, payload, idempotency_key
    )
    response.headers["Location"] = f"/api/v1/customer/service-requests/{item.id}"
    response.headers["Cache-Control"] = "no-store"
    return item


@customer_router.get(
    "/service-requests",
    response_model=ServiceRequestListResponse,
)
async def list_customer_service_requests(
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> ServiceRequestListResponse:
    response.headers["Cache-Control"] = "no-store"
    return ServiceRequestListResponse(
        items=await _customer_service(request, session, principal).list_requests()
    )


@customer_router.get(
    "/service-requests/{request_id}",
    response_model=ServiceRequestResponse,
)
async def get_customer_service_request(
    request_id: UUID,
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> ServiceRequestResponse:
    response.headers["Cache-Control"] = "no-store"
    return await _customer_service(request, session, principal).get_request(request_id)


@customer_router.post(
    "/service-requests/{request_id}/cancel",
    response_model=ServiceRequestResponse,
)
async def cancel_customer_service_request(
    request_id: UUID,
    payload: ServiceRequestCancel,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> ServiceRequestResponse:
    return await _customer_service(request, session, principal).cancel(
        request_id, payload.version
    )


@admin_router.get("/service-requests", response_model=ServiceRequestListResponse)
async def list_admin_service_requests(
    request: Request,
    response: Response,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> ServiceRequestListResponse:
    response.headers["Cache-Control"] = "no-store"
    return ServiceRequestListResponse(
        items=await _admin_service(request, session, principal).list_requests()
    )


@admin_router.get(
    "/service-requests/{request_id}",
    response_model=ServiceRequestResponse,
)
async def get_admin_service_request(
    request_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> ServiceRequestResponse:
    return await _admin_service(request, session, principal).get_request(request_id)


@admin_router.post(
    "/service-requests/{request_id}/approve",
    response_model=ServiceRequestResponse,
)
async def approve_service_request(
    request_id: UUID,
    payload: ServiceRequestDecision,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> ServiceRequestResponse:
    return await _admin_service(request, session, principal).approve(request_id, payload)


@admin_router.post(
    "/service-requests/{request_id}/reject",
    response_model=ServiceRequestResponse,
)
async def reject_service_request(
    request_id: UUID,
    payload: ServiceRequestDecision,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> ServiceRequestResponse:
    return await _admin_service(request, session, principal).reject(request_id, payload)


@admin_router.post(
    "/service-requests/{request_id}/execution",
    response_model=ServiceRequestResponse,
)
async def update_service_request_execution(
    request_id: UUID,
    payload: ServiceRequestExecution,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> ServiceRequestResponse:
    return await _admin_service(request, session, principal).execute(request_id, payload)


@admin_router.get("/security-groups", response_model=SecurityGroupListResponse)
async def list_admin_security_groups(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> SecurityGroupListResponse:
    return SecurityGroupListResponse(
        items=await _admin_service(request, session, principal).list_security_groups()
    )


@admin_router.post(
    "/security-groups",
    response_model=SecurityGroupResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_admin_security_group(
    payload: SecurityGroupCreate,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> SecurityGroupResponse:
    return await _admin_service(request, session, principal).create_security_group(payload)
