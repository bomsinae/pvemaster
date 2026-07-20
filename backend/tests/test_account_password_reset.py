from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.core.errors import AppError
from app.models.auth import User, UserRole
from app.schemas.auth import AdminPasswordResetRequest
from app.security.access import Principal
from app.security.passwords import PasswordManager
from app.services.accounts import AccountService


def _principal(role: UserRole) -> Principal:
    return Principal(
        user_id=uuid4(),
        email=f"{role.value.lower()}@example.test",
        role=role,
        session_epoch=0,
    )


@pytest.mark.asyncio
async def test_super_admin_password_reset_rehashes_and_revokes_sessions() -> None:
    passwords = PasswordManager()
    old_password = "old-password-at-least-12"
    new_password = "new-password-at-least-12"
    user = User(
        id=uuid4(),
        email="customer@example.test",
        display_name="Customer",
        role=UserRole.CUSTOMER.value,
        password_hash=passwords.hash(old_password),
        is_active=True,
        session_epoch=3,
        version=7,
    )
    session = AsyncMock()
    session.add = Mock()
    service = AccountService(
        session=session,
        principal=_principal(UserRole.SUPER_ADMIN),
        passwords=passwords,
        request_id="reset-request",
    )
    service._get_user = AsyncMock(return_value=user)  # type: ignore[method-assign]

    await service.reset_password(
        user.id,
        AdminPasswordResetRequest(new_password=new_password),
    )

    assert passwords.verify(user.password_hash, new_password)
    assert not passwords.verify(user.password_hash, old_password)
    assert user.session_epoch == 4
    assert user.version == 8
    session.execute.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_operator_cannot_reset_user_password() -> None:
    service = AccountService(
        session=AsyncMock(),
        principal=_principal(UserRole.OPERATOR),
        passwords=PasswordManager(),
        request_id="denied-reset-request",
    )

    with pytest.raises(AppError) as caught:
        await service.reset_password(
            uuid4(),
            AdminPasswordResetRequest(new_password="new-password-at-least-12"),
        )

    assert caught.value.status_code == 403
    assert caught.value.code == "FORBIDDEN"
