from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI

from app.core.errors import AppError
from app.models.auth import User, UserRole
from app.security.access import Principal
from app.security.passwords import PasswordManager
from app.services.accounts import AccountService


def _service(role: UserRole = UserRole.SUPER_ADMIN) -> tuple[AccountService, AsyncMock]:
    session = AsyncMock()
    session.add = Mock()
    principal = Principal(
        user_id=uuid4(),
        email=f"{role.value.lower()}@example.test",
        role=role,
        session_epoch=0,
    )
    return AccountService(
        session=session,
        principal=principal,
        passwords=PasswordManager(),
        request_id="user-management",
    ), session


def _user(role: UserRole = UserRole.CUSTOMER) -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid4(),
        email="customer@example.test",
        display_name="Customer",
        role=role.value,
        password_hash="unused",
        is_active=True,
        session_epoch=1,
        created_at=now,
        updated_at=now,
        version=2,
    )


def test_user_delete_route_is_registered(app: FastAPI) -> None:
    methods = app.openapi()["paths"]["/api/v1/admin/users/{user_id}"]
    assert {"patch", "delete"}.issubset(methods)


@pytest.mark.asyncio
async def test_super_admin_logically_deletes_user_and_memberships() -> None:
    service, session = _service()
    user = _user()
    service._get_user = AsyncMock(return_value=user)  # type: ignore[method-assign]

    await service.delete_user(user.id, version=2)

    assert user.is_active is False
    assert user.deleted_at is not None
    assert user.disabled_at is not None
    assert user.email == f"deleted+{user.id}@deleted.invalid"
    assert user.display_name == "삭제된 사용자"
    assert user.role == UserRole.CUSTOMER.value
    assert user.session_epoch == 2
    assert user.version == 3
    assert session.execute.await_count == 2
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_user_cannot_delete_own_account() -> None:
    service, session = _service()

    with pytest.raises(AppError) as caught:
        await service.delete_user(service._principal.user_id, version=1)

    assert caught.value.code == "USER_SELF_DELETE"
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_operator_cannot_delete_user() -> None:
    service, session = _service(UserRole.OPERATOR)

    with pytest.raises(AppError) as caught:
        await service.delete_user(uuid4(), version=1)

    assert caught.value.code == "FORBIDDEN"
    session.commit.assert_not_awaited()
