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
from app.models.customer_notifications import (
    CustomerNotificationDelivery,
    CustomerNotificationPreference,
    OrganizationNotificationPolicy,
)
from app.security.passwords import PasswordManager
from app.services.customer_notifications import (
    CustomerNotificationDispatcher,
    queue_customer_notification,
)

pytestmark = pytest.mark.skipif(
    "AUTH_TEST_DATABASE_URL" not in os.environ,
    reason="AUTH_TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


async def _clear(app: FastAPI) -> None:
    async with app.state.db_session_factory() as session:
        await session.execute(text("SET LOCAL app.audit_retention = 'on'"))
        for model in (
            CustomerNotificationDelivery,
            CustomerNotificationPreference,
            OrganizationNotificationPolicy,
            AuditLog,
            OrganizationMember,
            RefreshToken,
            Organization,
            LoginThrottle,
            User,
        ):
            await session.execute(delete(model))
        await session.commit()


async def _login(client: AsyncClient, email: str, password: str) -> dict[str, object]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password, "device_label": "Integration browser"},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_customer_notification_policy_delivery_race_and_session_isolation() -> None:
    settings = Settings(
        _env_file=None,
        database_url=SecretStr(os.environ["AUTH_TEST_DATABASE_URL"]),
        redis_url=SecretStr("redis://localhost:6379/15"),
        app_secret_key=SecretStr(token_urlsafe(32)),
    )
    app = create_app(settings)
    passwords = {"a": token_urlsafe(24), "b": token_urlsafe(24)}
    await _clear(app)
    password_manager = PasswordManager()
    async with app.state.db_session_factory() as session:
        admin = User(
            email="notification-admin@example.test",
            display_name="Notification Admin",
            role=UserRole.SUPER_ADMIN.value,
            password_hash=password_manager.hash(token_urlsafe(24)),
            is_active=True,
        )
        customer_a = User(
            email="notification-a@example.test",
            display_name="Notification A",
            role=UserRole.CUSTOMER.value,
            password_hash=password_manager.hash(passwords["a"]),
            is_active=True,
        )
        customer_b = User(
            email="notification-b@example.test",
            display_name="Notification B",
            role=UserRole.CUSTOMER.value,
            password_hash=password_manager.hash(passwords["b"]),
            is_active=True,
        )
        session.add_all([admin, customer_a, customer_b])
        await session.flush()
        organization = Organization(
            name="Notification Organization",
            is_active=True,
            created_by_id=admin.id,
        )
        session.add(organization)
        await session.flush()
        session.add_all(
            [
                OrganizationMember(
                    organization_id=organization.id,
                    user_id=customer_a.id,
                    added_by_id=admin.id,
                ),
                OrganizationNotificationPolicy(
                    organization_id=organization.id,
                    event_type="MAINTENANCE",
                    email_required=True,
                    created_by_id=admin.id,
                ),
            ]
        )
        await session.commit()

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login_a = await _login(client, customer_a.email, passwords["a"])
            login_b = await _login(client, customer_b.email, passwords["b"])
            headers_a = {"Authorization": f"Bearer {login_a['access_token']}"}
            headers_b = {"Authorization": f"Bearer {login_b['access_token']}"}

            listed = await client.get(
                "/api/v1/customer/notification-preferences",
                headers=headers_a,
            )
            assert listed.status_code == 200
            assert listed.json()["destination"] != customer_a.email
            assert customer_a.email not in listed.text
            assert len(listed.json()["items"]) == 4
            maintenance = next(
                item
                for item in listed.json()["items"]
                if item["event_type"] == "MAINTENANCE"
            )
            assert maintenance["required_by_organization"] is True
            assert maintenance["email_enabled"] is True

            disabled = await client.put(
                "/api/v1/customer/notification-preferences",
                headers=headers_a,
                json={
                    "organization_id": str(organization.id),
                    "event_type": "VM_DOWN",
                    "email_enabled": False,
                    "version": 0,
                },
            )
            assert disabled.status_code == 200
            assert disabled.json()["version"] == 1
            stale_write = await client.put(
                "/api/v1/customer/notification-preferences",
                headers=headers_a,
                json={
                    "organization_id": str(organization.id),
                    "event_type": "VM_DOWN",
                    "email_enabled": True,
                    "version": 0,
                },
            )
            assert stale_write.status_code == 409
            forced_opt_out = await client.put(
                "/api/v1/customer/notification-preferences",
                headers=headers_a,
                json={
                    "organization_id": str(organization.id),
                    "event_type": "MAINTENANCE",
                    "email_enabled": False,
                    "version": 0,
                },
            )
            assert forced_opt_out.status_code == 409

            sessions_b = await client.get("/api/v1/auth/sessions", headers=headers_b)
            family_b = sessions_b.json()["items"][0]["id"]
            cross_customer_revoke = await client.delete(
                f"/api/v1/auth/sessions/{family_b}",
                headers=headers_a,
            )
            assert cross_customer_revoke.status_code == 404

            async with app.state.db_session_factory() as session:
                assert await queue_customer_notification(
                    session,
                    organization_id=organization.id,
                    recipient_user_id=customer_a.id,
                    event_type="VM_DOWN",
                    event_key="vm-down-disabled",
                    subject="disabled",
                    message="disabled",
                ) == 0
                assert await queue_customer_notification(
                    session,
                    organization_id=organization.id,
                    recipient_user_id=customer_a.id,
                    event_type="MAINTENANCE",
                    event_key="forced-maintenance",
                    subject="Maintenance",
                    message="Safe maintenance message",
                ) == 1
                assert await queue_customer_notification(
                    session,
                    organization_id=organization.id,
                    recipient_user_id=customer_a.id,
                    event_type="OPERATION_COMPLETED",
                    event_key="operation-before-opt-out",
                    subject="Operation",
                    message="Safe operation message",
                ) == 1
                await session.commit()

            operation_opt_out = await client.put(
                "/api/v1/customer/notification-preferences",
                headers=headers_a,
                json={
                    "organization_id": str(organization.id),
                    "event_type": "OPERATION_COMPLETED",
                    "email_enabled": False,
                    "version": 0,
                },
            )
            assert operation_opt_out.status_code == 200

            sent: list[tuple[str, str, str]] = []

            async def sender(recipient: str, subject: str, body: str) -> None:
                sent.append((recipient, subject, body))

            async with app.state.db_session_factory() as session:
                delivered = await CustomerNotificationDispatcher(
                    session=session,
                    settings=settings,
                    sender=sender,
                ).deliver_due()
                assert delivered == 1
                statuses = dict(
                    (
                        await session.execute(
                        select(
                            CustomerNotificationDelivery.event_key,
                            CustomerNotificationDelivery.status,
                        )
                    )
                    ).all()
                )
                assert statuses["forced-maintenance"] == "DELIVERED"
                assert statuses["operation-before-opt-out"] == "CANCELLED"
            assert sent == [
                (
                    customer_a.email,
                    "Maintenance",
                    "Safe maintenance message",
                )
            ]

            security_events = await client.get("/api/v1/auth/login-events", headers=headers_a)
            assert security_events.status_code == 200
            assert "password" not in security_events.text.lower()
            assert "token" not in security_events.text.lower()

            second_login_a = await _login(client, customer_a.email, passwords["a"])
            second_headers_a = {
                "Authorization": f"Bearer {second_login_a['access_token']}"
            }
            changed_password = await client.post(
                "/api/v1/auth/change-password",
                headers=headers_a,
                json={
                    "current_password": passwords["a"],
                    "new_password": token_urlsafe(24),
                    "revoke_all_sessions": False,
                },
            )
            assert changed_password.status_code == 204
            assert (await client.get("/api/v1/auth/me", headers=headers_a)).status_code == 200
            assert (
                await client.get("/api/v1/auth/me", headers=second_headers_a)
            ).status_code == 401
    finally:
        await _clear(app)
        await app.state.db_engine.dispose()
