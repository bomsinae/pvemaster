from secrets import token_urlsafe
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.core.errors import AppError
from app.models.auth import User, UserRole
from app.security.access import Principal, require_service_role
from app.security.passwords import PasswordManager
from app.security.tokens import TokenManager


def test_passwords_use_argon2id() -> None:
    manager = PasswordManager()
    encoded = manager.hash(token_urlsafe(24))

    assert encoded.startswith("$argon2id$")


def test_access_token_round_trip(settings: Settings) -> None:
    user = User(
        id=uuid4(),
        email="user@example.test",
        display_name="User",
        role=UserRole.CUSTOMER.value,
        password_hash="not-used",
        session_epoch=4,
    )
    manager = TokenManager(settings)

    encoded, expires_in = manager.create_access_token(user)
    claims = manager.decode_access_token(encoded)

    assert claims.user_id == user.id
    assert claims.session_epoch == 4
    assert expires_in == settings.access_token_ttl_seconds


def test_refresh_secret_is_only_represented_by_hash() -> None:
    manager = TokenManager(
        Settings(
            _env_file=None,
            database_url=SecretStr("postgresql+asyncpg://user@localhost/test"),
            redis_url=SecretStr("redis://localhost/0"),
            app_secret_key=SecretStr(token_urlsafe(32)),
        )
    )
    secret = manager.create_refresh_secret()

    assert secret.encode() not in manager.hash_refresh_secret(secret)
    assert len(manager.hash_refresh_secret(secret)) == 32


def test_customer_is_denied_admin_service_role() -> None:
    principal = Principal(
        user_id=uuid4(),
        email="customer@example.test",
        role=UserRole.CUSTOMER,
        session_epoch=0,
    )

    with pytest.raises(AppError) as error:
        require_service_role(principal, UserRole.SUPER_ADMIN, UserRole.OPERATOR)

    assert error.value.status_code == 403
