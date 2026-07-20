from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.dependencies import PrincipalDependency, get_db_session
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    TokenResponse,
    UserResponse,
)
from app.security.passwords import PasswordManager
from app.security.tokens import TokenManager
from app.services.accounts import AccountService
from app.services.auth import AuthService

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


def _auth_service(request: Request, session: AsyncSession) -> AuthService:
    return AuthService(
        session=session,
        settings=cast(Settings, request.app.state.settings),
        passwords=cast(PasswordManager, request.app.state.password_manager),
        tokens=cast(TokenManager, request.app.state.token_manager),
    )


def _source(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    session: SessionDependency,
) -> TokenResponse:
    return await _auth_service(request, session).login(
        payload,
        source=_source(request),
        request_id=request.state.request_id,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    session: SessionDependency,
) -> TokenResponse:
    return await _auth_service(request, session).refresh(
        payload.refresh_token.get_secret_value(),
        request_id=request.state.request_id,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: LogoutRequest,
    request: Request,
    session: SessionDependency,
) -> Response:
    await _auth_service(request, session).logout(
        payload.refresh_token.get_secret_value(),
        request_id=request.state.request_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _account_service(
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


@router.get("/me", response_model=UserResponse)
async def me(
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> UserResponse:
    return await _account_service(request, session, principal).me()


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> Response:
    await _account_service(request, session, principal).change_password(payload)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
