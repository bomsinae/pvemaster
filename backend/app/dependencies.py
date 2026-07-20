from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.auth import User, UserRole
from app.security.access import Principal
from app.security.tokens import TokenManager


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        yield session


SessionDependency = Annotated[AsyncSession, Depends(get_db_session)]


async def get_current_principal(
    request: Request,
    session: SessionDependency,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    if authorization is None or not authorization.startswith("Bearer "):
        raise AppError(
            status_code=401,
            code="AUTHENTICATION_REQUIRED",
            message="A valid access token is required.",
        )
    raw_token = authorization.removeprefix("Bearer ").strip()
    claims = cast(TokenManager, request.app.state.token_manager).decode_access_token(raw_token)
    user = await session.scalar(select(User).where(User.id == claims.user_id))
    if user is None or not user.is_active or user.session_epoch != claims.session_epoch:
        raise AppError(
            status_code=401,
            code="INVALID_ACCESS_TOKEN",
            message="The access token is invalid or expired.",
        )
    return Principal(
        user_id=user.id,
        email=user.email,
        role=UserRole(user.role),
        session_epoch=user.session_epoch,
    )


PrincipalDependency = Annotated[Principal, Depends(get_current_principal)]
