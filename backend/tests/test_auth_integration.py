import os
from secrets import token_urlsafe

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import delete, select, text

from app.core.config import Settings
from app.main import create_app
from app.models.auth import (
    AuditLog,
    LoginThrottle,
    Organization,
    OrganizationMember,
    RefreshToken,
    User,
    UserRole,
)
from app.security.passwords import PasswordManager

pytestmark = pytest.mark.skipif(
    "AUTH_TEST_DATABASE_URL" not in os.environ,
    reason="AUTH_TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


async def _clear_auth_data(app: FastAPI) -> None:
    async with app.state.db_session_factory() as session:
        await session.execute(text("SET LOCAL app.audit_retention = 'on'"))
        for model in (
            AuditLog,
            OrganizationMember,
            RefreshToken,
            Organization,
            LoginThrottle,
            User,
        ):
            await session.execute(delete(model))
        await session.commit()


async def _seed_users(app: FastAPI, passwords_by_role: dict[UserRole, str]) -> None:
    passwords = PasswordManager()
    async with app.state.db_session_factory() as session:
        session.add_all(
            [
                User(
                    email="super@example.test",
                    display_name="Super",
                    role=UserRole.SUPER_ADMIN.value,
                    password_hash=passwords.hash(passwords_by_role[UserRole.SUPER_ADMIN]),
                    is_active=True,
                ),
                User(
                    email="operator@example.test",
                    display_name="Operator",
                    role=UserRole.OPERATOR.value,
                    password_hash=passwords.hash(passwords_by_role[UserRole.OPERATOR]),
                    is_active=True,
                ),
                User(
                    email="customer@example.test",
                    display_name="Customer",
                    role=UserRole.CUSTOMER.value,
                    password_hash=passwords.hash(passwords_by_role[UserRole.CUSTOMER]),
                    is_active=True,
                ),
                User(
                    email="inactive@example.test",
                    display_name="Inactive",
                    role=UserRole.CUSTOMER.value,
                    password_hash=passwords.hash(passwords_by_role[UserRole.CUSTOMER]),
                    is_active=False,
                ),
            ]
        )
        await session.commit()


async def _login(client: AsyncClient, email: str, password: str) -> dict[str, object]:
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


async def test_authentication_rotation_and_role_matrix() -> None:
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(os.environ["AUTH_TEST_DATABASE_URL"]),
        redis_url=SecretStr("redis://localhost:6379/15"),
        app_secret_key=SecretStr(token_urlsafe(32)),
    )
    app = create_app(settings)
    passwords_by_role = {role: token_urlsafe(24) for role in UserRole}
    await _clear_auth_data(app)
    await _seed_users(app, passwords_by_role)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            customer = await _login(
                client, "customer@example.test", passwords_by_role[UserRole.CUSTOMER]
            )
            customer_headers = {"Authorization": f"Bearer {customer['access_token']}"}
            denied = await client.get("/api/v1/admin/users", headers=customer_headers)
            assert denied.status_code == 403

            invalid = await client.post(
                "/api/v1/auth/login",
                json={"email": "customer@example.test", "password": "wrong"},
            )
            inactive = await client.post(
                "/api/v1/auth/login",
                json={
                    "email": "inactive@example.test",
                    "password": passwords_by_role[UserRole.CUSTOMER],
                },
            )
            assert invalid.status_code == inactive.status_code == 401
            assert invalid.json()["error"]["code"] == inactive.json()["error"]["code"]

            limited = invalid
            for _ in range(settings.login_failure_limit - 1):
                limited = await client.post(
                    "/api/v1/auth/login",
                    json={"email": "customer@example.test", "password": token_urlsafe(24)},
                )
            assert limited.status_code == 429
            assert limited.json()["error"]["code"] == "LOGIN_RATE_LIMITED"

            first_refresh = str(customer["refresh_token"])
            async with app.state.db_session_factory() as session:
                stored_hashes = (await session.scalars(select(RefreshToken.token_hash))).all()
            assert stored_hashes
            assert all(first_refresh.encode() not in item for item in stored_hashes)
            rotated = await client.post(
                "/api/v1/auth/refresh", json={"refresh_token": first_refresh}
            )
            assert rotated.status_code == 200
            assert rotated.json()["refresh_token"] != first_refresh
            reused = await client.post(
                "/api/v1/auth/refresh", json={"refresh_token": first_refresh}
            )
            assert reused.status_code == 401
            assert reused.json()["error"]["code"] == "REFRESH_TOKEN_REUSED"
            revoked_replacement = await client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": rotated.json()["refresh_token"]},
            )
            assert revoked_replacement.status_code == 401

            operator = await _login(
                client, "operator@example.test", passwords_by_role[UserRole.OPERATOR]
            )
            operator_headers = {"Authorization": f"Bearer {operator['access_token']}"}
            assert (
                await client.get("/api/v1/admin/users", headers=operator_headers)
            ).status_code == 200
            operator_create = await client.post(
                "/api/v1/admin/users",
                headers=operator_headers,
                json={
                    "email": "created-by-operator@example.test",
                    "display_name": "Denied",
                    "role": "CUSTOMER",
                    "password": token_urlsafe(24),
                },
            )
            assert operator_create.status_code == 403

            new_operator_password = token_urlsafe(24)
            password_change = await client.post(
                "/api/v1/auth/change-password",
                headers=operator_headers,
                json={
                    "current_password": passwords_by_role[UserRole.OPERATOR],
                    "new_password": new_operator_password,
                },
            )
            assert password_change.status_code == 204
            assert (
                await client.get("/api/v1/auth/me", headers=operator_headers)
            ).status_code == 401
            await _login(client, "operator@example.test", new_operator_password)

            super_admin = await _login(
                client, "super@example.test", passwords_by_role[UserRole.SUPER_ADMIN]
            )
            super_headers = {"Authorization": f"Bearer {super_admin['access_token']}"}
            created_password = token_urlsafe(24)
            super_create = await client.post(
                "/api/v1/admin/users",
                headers=super_headers,
                json={
                    "email": "created@example.test",
                    "display_name": "Created",
                    "role": "CUSTOMER",
                    "password": created_password,
                },
            )
            assert super_create.status_code == 201, super_create.text
            organization = await client.post(
                "/api/v1/admin/organizations",
                headers=super_headers,
                json={"name": "Integration organization"},
            )
            assert organization.status_code == 201
            second_organization = await client.post(
                "/api/v1/admin/organizations",
                headers=super_headers,
                json={"name": "Integration secondary"},
            )
            assert second_organization.status_code == 201
            organization_search = await client.get(
                "/api/v1/admin/organizations",
                headers=super_headers,
                params={"q": "Integration", "limit": 1, "offset": 0},
            )
            assert organization_search.status_code == 200
            assert organization_search.json()["total"] == 2
            assert organization_search.json()["limit"] == 1
            assert organization_search.json()["offset"] == 0
            assert len(organization_search.json()["items"]) == 1
            organization_id_search = await client.get(
                "/api/v1/admin/organizations",
                headers=super_headers,
                params={"q": organization.json()["id"][:8], "limit": 10},
            )
            assert organization_id_search.status_code == 200
            assert [item["id"] for item in organization_id_search.json()["items"]] == [
                organization.json()["id"]
            ]
            invalid_organization_limit = await client.get(
                "/api/v1/admin/organizations",
                headers=super_headers,
                params={"limit": 51},
            )
            assert invalid_organization_limit.status_code == 422
            membership = await client.post(
                f"/api/v1/admin/organizations/{organization.json()['id']}/members",
                headers=super_headers,
                json={"user_id": super_create.json()["id"]},
            )
            assert membership.status_code == 201
            users_response = await client.get("/api/v1/admin/users", headers=super_headers)
            assert users_response.status_code == 200
            users_by_email = {item["email"]: item for item in users_response.json()["items"]}
            assert users_by_email["created@example.test"]["organization_names"] == [
                "Integration organization"
            ]
            assert users_by_email["customer@example.test"]["organization_names"] == []
            created_login = await _login(client, "created@example.test", created_password)
            created_headers = {
                "Authorization": f"Bearer {created_login['access_token']}"
            }
            reset_password = token_urlsafe(24)
            denied_reset = await client.post(
                f"/api/v1/admin/users/{super_create.json()['id']}/reset-password",
                headers=operator_headers,
                json={"new_password": reset_password},
            )
            assert denied_reset.status_code == 403
            password_reset = await client.post(
                f"/api/v1/admin/users/{super_create.json()['id']}/reset-password",
                headers=super_headers,
                json={"new_password": reset_password},
            )
            assert password_reset.status_code == 204
            assert (
                await client.get("/api/v1/auth/me", headers=created_headers)
            ).status_code == 401
            revoked_refresh = await client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": created_login["refresh_token"]},
            )
            assert revoked_refresh.status_code == 401
            old_password_login = await client.post(
                "/api/v1/auth/login",
                json={"email": "created@example.test", "password": created_password},
            )
            assert old_password_login.status_code == 401
            await _login(client, "created@example.test", reset_password)
            deactivated = await client.patch(
                f"/api/v1/admin/users/{super_create.json()['id']}",
                headers=super_headers,
                json={"is_active": False, "version": super_create.json()["version"]},
            )
            assert deactivated.status_code == 200
            inactive_created = await client.post(
                "/api/v1/auth/login",
                json={"email": "created@example.test", "password": created_password},
            )
            assert inactive_created.status_code == 401

            logout = await client.post(
                "/api/v1/auth/logout",
                json={"refresh_token": super_admin["refresh_token"]},
            )
            assert logout.status_code == 204
            assert (await client.get("/api/v1/auth/me", headers=super_headers)).status_code == 401
    finally:
        await _clear_auth_data(app)
        await app.state.db_engine.dispose()
