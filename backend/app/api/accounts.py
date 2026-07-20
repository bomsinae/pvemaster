from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import PrincipalDependency, get_db_session
from app.schemas.auth import (
    AdminPasswordResetRequest,
    OrganizationCreate,
    OrganizationListResponse,
    OrganizationMemberCreate,
    OrganizationMemberListResponse,
    OrganizationMemberResponse,
    OrganizationResponse,
    OrganizationUpdate,
    UserCreate,
    UserListResponse,
    UserResponse,
    UserUpdate,
)
from app.security.passwords import PasswordManager
from app.services.accounts import AccountService

router = APIRouter(prefix="/api/v1/admin", tags=["admin-accounts"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


def _service(
    request: Request,
    session: AsyncSession,
    principal: PrincipalDependency,
) -> AccountService:
    return AccountService(
        session=session,
        principal=principal,
        passwords=cast(PasswordManager, request.app.state.password_manager),
        request_id=request.state.request_id,
    )


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> UserResponse:
    return await _service(request, session, principal).create_user(payload)


@router.get("/users", response_model=UserListResponse)
async def list_users(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> UserListResponse:
    return UserListResponse(items=await _service(request, session, principal).list_users())


@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    payload: UserUpdate,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> UserResponse:
    return await _service(request, session, principal).update_user(user_id, payload)


@router.post(
    "/users/{user_id}/reset-password",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def reset_user_password(
    user_id: UUID,
    payload: AdminPasswordResetRequest,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> Response:
    await _service(request, session, principal).reset_password(user_id, payload)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/organizations",
    response_model=OrganizationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_organization(
    payload: OrganizationCreate,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> OrganizationResponse:
    return await _service(request, session, principal).create_organization(payload)


@router.get("/organizations", response_model=OrganizationListResponse)
async def list_organizations(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
    q: str | None = Query(default=None, min_length=1, max_length=160),
    status_filter: Literal["active", "inactive", "all"] = Query(
        default="active", alias="status"
    ),
    sort: Literal["newest", "oldest", "name"] = Query(default="newest"),
    limit: int | None = Query(default=None, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
) -> OrganizationListResponse:
    items, total = await _service(request, session, principal).list_organizations(
        q=q,
        status=status_filter,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return OrganizationListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.patch("/organizations/{organization_id}", response_model=OrganizationResponse)
async def update_organization(
    organization_id: UUID,
    payload: OrganizationUpdate,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> OrganizationResponse:
    return await _service(request, session, principal).update_organization(
        organization_id, payload
    )


@router.delete(
    "/organizations/{organization_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_organization(
    organization_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
    version: int = Query(ge=1),
) -> Response:
    await _service(request, session, principal).delete_organization(
        organization_id, version=version
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/organizations/{organization_id}/members",
    response_model=OrganizationMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_organization_member(
    organization_id: UUID,
    payload: OrganizationMemberCreate,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> OrganizationMemberResponse:
    return await _service(request, session, principal).add_member(organization_id, payload)


@router.get(
    "/organizations/{organization_id}/members",
    response_model=OrganizationMemberListResponse,
)
async def list_organization_members(
    organization_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> OrganizationMemberListResponse:
    return OrganizationMemberListResponse(
        items=await _service(request, session, principal).list_members(organization_id)
    )


@router.delete(
    "/organizations/{organization_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_organization_member(
    organization_id: UUID,
    user_id: UUID,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> Response:
    await _service(request, session, principal).remove_member(organization_id, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
