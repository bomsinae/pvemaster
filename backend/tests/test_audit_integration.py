import os
from secrets import token_urlsafe

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import delete, text, update
from sqlalchemy.exc import DBAPIError

from app.core.config import Settings
from app.main import create_app
from app.models.auth import AuditLog, User, UserRole
from app.security.passwords import PasswordManager
from app.services.audit import add_audit_event

pytestmark = pytest.mark.skipif(
    "AUTH_TEST_DATABASE_URL" not in os.environ,
    reason="AUTH_TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


async def test_audit_api_redaction_rbac_and_append_only_trigger() -> None:
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(os.environ["AUTH_TEST_DATABASE_URL"]),
        redis_url=SecretStr("redis://localhost:6379/15"),
        app_secret_key=SecretStr(token_urlsafe(32)),
    )
    app: FastAPI = create_app(settings)
    password = token_urlsafe(24)
    async with app.state.db_session_factory() as session:
        await session.execute(text("SET LOCAL app.audit_retention = 'on'"))
        await session.execute(delete(AuditLog))
        await session.execute(delete(User).where(User.email == "audit-admin@example.test"))
        admin = User(
            email="audit-admin@example.test",
            display_name="Audit Admin",
            role=UserRole.SUPER_ADMIN.value,
            password_hash=PasswordManager().hash(password),
            is_active=True,
        )
        session.add(admin)
        await session.flush()
        add_audit_event(
            session,
            action="AUDIT_REDACTION_TEST",
            outcome="SUCCEEDED",
            request_id="audit-seed",
            actor_user_id=admin.id,
            actor_role=UserRole.SUPER_ADMIN,
            target_type="cluster",
            target_id="cluster-test",
            before={"api_token": "old-value"},
            after={"api_token": "new-value", "enabled": True},
        )
        await session.commit()

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post(
                "/api/v1/auth/login",
                json={"email": "audit-admin@example.test", "password": password},
                headers={"User-Agent": "audit-integration-agent"},
            )
            assert login.status_code == 200
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            response = await client.get(
                "/api/v1/admin/audit-logs?action=AUDIT_REDACTION_TEST",
                headers=headers,
            )
            assert response.status_code == 200
            item = response.json()["items"][0]
            assert item["before"] == {"api_token": "[REDACTED]"}
            assert item["after"] == {"api_token": "[REDACTED]", "enabled": True}
            assert "old-value" not in response.text
            assert "new-value" not in response.text

        async with app.state.db_session_factory() as session:
            with pytest.raises(DBAPIError):
                await session.execute(
                    update(AuditLog)
                    .where(AuditLog.action == "AUDIT_REDACTION_TEST")
                    .values(action="MUTATED")
                )
                await session.commit()
            await session.rollback()
    finally:
        async with app.state.db_session_factory() as session:
            await session.execute(text("SET LOCAL app.audit_retention = 'on'"))
            await session.execute(delete(AuditLog))
            await session.execute(delete(User).where(User.email == "audit-admin@example.test"))
            await session.commit()
        await app.state.redis.aclose()
        await app.state.db_engine.dispose()
