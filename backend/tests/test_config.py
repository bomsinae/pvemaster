from secrets import token_urlsafe

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.main import create_app


def test_required_settings_must_be_present(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in ("DATABASE_URL", "REDIS_URL", "APP_SECRET_KEY"):
        monkeypatch.delenv(variable, raising=False)

    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)  # type: ignore[call-arg]

    missing = {item["loc"][0] for item in error.value.errors() if item["type"] == "missing"}
    assert missing == {"database_url", "redis_url", "app_secret_key"}


def test_invalid_secret_is_not_in_validation_error() -> None:
    invalid_secret = token_urlsafe(4)

    with pytest.raises(ValidationError) as error:
        Settings(
            _env_file=None,
            database_url=SecretStr("postgresql+asyncpg://user@localhost/database"),
            redis_url=SecretStr("redis://localhost:6379/0"),
            app_secret_key=SecretStr(invalid_secret),
        )

    assert invalid_secret not in str(error.value)


def test_production_app_enables_hsts() -> None:
    settings = Settings(
        _env_file=None,
        database_url=SecretStr("postgresql+asyncpg://user@localhost/database"),
        redis_url=SecretStr("redis://localhost:6379/0"),
        app_secret_key=SecretStr(token_urlsafe(32)),
        environment="production",
    )

    app = create_app(settings)

    middleware = next(
        item for item in app.user_middleware if item.cls.__name__ == "SecurityHeadersMiddleware"
    )
    assert middleware.kwargs["enable_hsts"] is True
