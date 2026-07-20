from secrets import token_urlsafe

import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url=SecretStr("postgresql+asyncpg://user@localhost/test"),
        redis_url=SecretStr("redis://localhost:6379/0"),
        app_secret_key=SecretStr(token_urlsafe(32)),
        cors_origins=["http://localhost:3000"],
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)
