from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import PrincipalDependency, get_db_session
from app.schemas.organization_governance import (
    ApprovalPolicyResponse,
    ApprovalPolicyUpdate,
    OrganizationActivityResponse,
    OrganizationInvitationAccept,
    OrganizationInvitationCreate,
    OrganizationInvitationResponse,
    OrganizationMembershipResponse,
    OrganizationQuotaResponse,
    OrganizationQuotaUpdate,
    OrganizationRoleUpdate,
)
from app.services.organization_governance import (
    AdminOrganizationGovernanceService,
    OrganizationGovernanceService,
)

customer_router = APIRouter(
    prefix="/api/v1/customer",
    tags=["customer-organization-governance"],
)
admin_router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin-organization-governance"],
)
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


def _customer_service(
    request: Request,
    session: AsyncSession,
    principal: PrincipalDependency,
) -> OrganizationGovernanceService:
    return OrganizationGovernanceService(
        session=session,
        principal=principal,
        request_id=request.state.request_id,
        source_ip=request.client.host if request.client else None,
    )


def _admin_service(
    request: Request,
    session: AsyncSession,
    principal: PrincipalDependency,
) -> AdminOrganizationGovernanceService:
    return AdminOrganizationGovernanceService(
        session=session,
        principal=principal,
        request_id=request.state.request_id,
        source_ip=request.client.host if request.client else None,
    )


@customer_router.get(
    "/organizations",
    response_model=list[OrganizationMembershipResponse],
)
async def list_my_organizations(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> list[OrganizationMembershipResponse]:
    return await _customer_service(request, session, principal).list_my_organizations()


@customer_router.get(
    "/organizations/{organization_id}/members",
    response_model=list[OrganizationMembershipResponse],
)
async def list_organization_members(
    organization_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> list[OrganizationMembershipResponse]:
    return await _customer_service(request, session, principal).list_members(organization_id)


@customer_router.patch(
    "/organizations/{organization_id}/members/{membership_id}",
    response_model=OrganizationMembershipResponse,
)
async def update_organization_member(
    organization_id: UUID,
    membership_id: UUID,
    payload: OrganizationRoleUpdate,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> OrganizationMembershipResponse:
    return await _customer_service(request, session, principal).update_member(
        organization_id, membership_id, payload
    )


@customer_router.delete(
    "/organizations/{organization_id}/members/{membership_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_organization_member(
    organization_id: UUID,
    membership_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> Response:
    await _customer_service(request, session, principal).remove_member(
        organization_id, membership_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@customer_router.post(
    "/organizations/{organization_id}/invitations",
    response_model=OrganizationInvitationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def invite_organization_member(
    organization_id: UUID,
    payload: OrganizationInvitationCreate,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> OrganizationInvitationResponse:
    return await _customer_service(request, session, principal).invite(organization_id, payload)


@customer_router.get(
    "/organizations/{organization_id}/invitations",
    response_model=list[OrganizationInvitationResponse],
)
async def list_organization_invitations(
    organization_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> list[OrganizationInvitationResponse]:
    return await _customer_service(request, session, principal).list_invitations(organization_id)


@customer_router.delete(
    "/organizations/{organization_id}/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_organization_invitation(
    organization_id: UUID,
    invitation_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> Response:
    await _customer_service(request, session, principal).revoke_invitation(
        organization_id, invitation_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@customer_router.post(
    "/organization-invitations/accept",
    response_model=OrganizationMembershipResponse,
)
async def accept_organization_invitation(
    payload: OrganizationInvitationAccept,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> OrganizationMembershipResponse:
    return await _customer_service(request, session, principal).accept_invitation(
        payload.token.get_secret_value()
    )


@customer_router.get(
    "/organizations/{organization_id}/quota",
    response_model=OrganizationQuotaResponse,
)
async def get_organization_quota(
    organization_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> OrganizationQuotaResponse:
    return await _customer_service(request, session, principal).quota(organization_id)


@customer_router.get(
    "/organizations/{organization_id}/approval-policies",
    response_model=list[ApprovalPolicyResponse],
)
async def list_customer_approval_policies(
    organization_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> list[ApprovalPolicyResponse]:
    return await _customer_service(request, session, principal).approval_policies(organization_id)


@customer_router.get(
    "/organizations/{organization_id}/activity",
    response_model=list[OrganizationActivityResponse],
)
async def list_organization_activity(
    organization_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
    limit: int = Query(default=50, ge=1, le=100),
) -> list[OrganizationActivityResponse]:
    return await _customer_service(request, session, principal).activity(
        organization_id, limit=limit
    )


@admin_router.get(
    "/organizations/{organization_id}/quota",
    response_model=OrganizationQuotaResponse,
)
async def get_admin_organization_quota(
    organization_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> OrganizationQuotaResponse:
    return await _admin_service(request, session, principal).get_quota(organization_id)


@admin_router.put(
    "/organizations/{organization_id}/quota",
    response_model=OrganizationQuotaResponse,
)
async def set_admin_organization_quota(
    organization_id: UUID,
    payload: OrganizationQuotaUpdate,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> OrganizationQuotaResponse:
    return await _admin_service(request, session, principal).set_quota(organization_id, payload)


@admin_router.get(
    "/organizations/{organization_id}/approval-policies",
    response_model=list[ApprovalPolicyResponse],
)
async def list_admin_approval_policies(
    organization_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> list[ApprovalPolicyResponse]:
    return await _admin_service(request, session, principal).list_approval_policies_admin(
        organization_id
    )


@admin_router.put(
    "/organizations/{organization_id}/approval-policies",
    response_model=ApprovalPolicyResponse,
)
async def set_admin_approval_policy(
    organization_id: UUID,
    payload: ApprovalPolicyUpdate,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> ApprovalPolicyResponse:
    return await _admin_service(request, session, principal).set_approval_policy(
        organization_id, payload
    )
