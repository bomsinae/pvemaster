import os
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import delete, select, text

from app.core.config import Settings
from app.core.errors import AppError
from app.db import create_engine, create_session_factory
from app.models.alerting import (
    Alert,
    AlertEvent,
    MaintenanceWindow,
    NotificationChannel,
    NotificationDelivery,
    NotificationRule,
)
from app.models.auth import AuditLog, Organization, OrganizationMember, User, UserRole
from app.models.customer_notifications import CustomerNotificationDelivery
from app.schemas.alerting import AlertActionRequest
from app.schemas.observability import OperationalAlert
from app.security.access import Principal
from app.security.notification_config import NotificationConfigCipher
from app.security.webhooks import WebhookEndpointPolicy
from app.services.alerting import AlertingService

pytestmark = pytest.mark.skipif(
    "AUTH_TEST_DATABASE_URL" not in os.environ,
    reason="AUTH_TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


async def _clear(session) -> None:
    await session.execute(text("SET LOCAL app.audit_retention = 'on'"))
    for model in (
        CustomerNotificationDelivery,
        NotificationDelivery,
        NotificationRule,
        AlertEvent,
        NotificationChannel,
        MaintenanceWindow,
        Alert,
        AuditLog,
        OrganizationMember,
        Organization,
        User,
    ):
        await session.execute(delete(model))
    await session.commit()


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url=SecretStr(os.environ["AUTH_TEST_DATABASE_URL"]),
        redis_url=SecretStr(os.environ.get("AUTH_TEST_REDIS_URL", "redis://localhost/15")),
        app_secret_key=SecretStr(token_urlsafe(32)),
    )


async def test_alert_deduplication_reopen_maintenance_and_customer_isolation() -> None:
    settings = _settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    try:
        async with factory() as session:
            await _clear(session)
            admin = User(
                email="alert-admin@example.test",
                display_name="Alert Admin",
                role=UserRole.SUPER_ADMIN.value,
                password_hash="unused",
                is_active=True,
            )
            customer = User(
                email="alert-customer@example.test",
                display_name="Alert Customer",
                role=UserRole.CUSTOMER.value,
                password_hash="unused",
                is_active=True,
            )
            session.add_all([admin, customer])
            await session.flush()
            organization = Organization(
                name="Alert Org",
                created_by_id=admin.id,
                is_active=True,
            )
            session.add(organization)
            await session.flush()
            session.add(
                OrganizationMember(
                    organization_id=organization.id,
                    user_id=customer.id,
                    added_by_id=admin.id,
                )
            )
            await session.commit()
            cipher = NotificationConfigCipher(settings.app_secret_key.get_secret_value())
            service = AlertingService(session=session, settings=settings, cipher=cipher)
            signal = OperationalAlert(
                code="WORKER_DOWN",
                severity="critical",
                resource_type="worker",
                message="No worker heartbeat.",
            )
            assert await service.sync([signal]) == 1
            assert await service.sync([signal]) == 1
            persisted = (await session.scalars(select(Alert))).one()
            assert persisted.occurrence_count == 2
            assert len((await session.scalars(select(AlertEvent))).all()) == 2

            admin_service = AlertingService(
                session=session,
                settings=settings,
                cipher=cipher,
                principal=Principal(
                    user_id=admin.id,
                    email=admin.email,
                    role=UserRole.SUPER_ADMIN,
                    session_epoch=0,
                ),
            )
            resolved = await admin_service.action(
                persisted.id,
                "resolve",
                AlertActionRequest(version=persisted.version, note="worker recovered"),
            )
            assert resolved.status == "RESOLVED"
            await service.sync([signal])
            await session.refresh(persisted)
            assert persisted.status == "OPEN"

            session.add(
                MaintenanceWindow(
                    name="Worker maintenance",
                    target_type="worker",
                    starts_at=now - timedelta(minutes=1),
                    ends_at=now + timedelta(minutes=30),
                    suppress_notifications=True,
                    created_by_id=admin.id,
                )
            )
            await session.commit()
            await service.sync([signal])
            await session.refresh(persisted)
            assert persisted.status == "SILENCED"

            organization_alert = Alert(
                type="VM_STATE_CHANGED",
                severity="INFO",
                fingerprint="org-alert",
                status="OPEN",
                resource_type="workload",
                organization_id=organization.id,
                message="VM state changed.",
                first_seen_at=now,
                last_seen_at=now,
            )
            session.add(organization_alert)
            await session.commit()
            customer_service = AlertingService(
                session=session,
                settings=settings,
                cipher=cipher,
                principal=Principal(
                    user_id=customer.id,
                    email=customer.email,
                    role=UserRole.CUSTOMER,
                    session_epoch=0,
                ),
            )
            customer_alerts = await customer_service.list_alerts(status=None, severity=None)
            assert [item.id for item in customer_alerts.items] == [organization_alert.id]
    finally:
        async with factory() as session:
            await _clear(session)
        await engine.dispose()


async def test_notification_config_encryption_and_webhook_ssrf_policy() -> None:
    cipher = NotificationConfigCipher(token_urlsafe(32))
    channel_id = uuid4()
    encrypted = cipher.encrypt(
        {"url": "https://notify.example.test/hook", "secret": "never-log-this"},
        channel_id=channel_id,
    )
    assert b"never-log-this" not in encrypted.ciphertext
    assert cipher.decrypt(encrypted, channel_id=channel_id)["secret"] == "never-log-this"

    async def private_resolver(_host: str, _port: int) -> list[str]:
        return ["127.0.0.1"]

    policy = WebhookEndpointPolicy(
        allowed_hosts=["notify.example.test"],
        allowed_networks=[],
        resolver=private_resolver,
    )
    with pytest.raises(AppError) as denied:
        await policy.validate("https://notify.example.test/hook")
    assert denied.value.code == "WEBHOOK_ENDPOINT_NOT_ALLOWED"


async def test_webhook_timeout_is_retried_with_stable_delivery_identity() -> None:
    settings = _settings().model_copy(
        update={
            "notification_webhook_allowed_networks": ["1.1.1.0/24"],
            "notification_max_attempts": 3,
        }
    )
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    cipher = NotificationConfigCipher(settings.app_secret_key.get_secret_value())
    delivery_id = uuid4()
    seen_idempotency_keys: list[str] = []

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        seen_idempotency_keys.append(request.headers["Idempotency-Key"])
        raise httpx.ReadTimeout("timeout", request=request)

    try:
        async with factory() as session:
            await _clear(session)
            admin = User(
                email="delivery-admin@example.test",
                display_name="Delivery Admin",
                role=UserRole.SUPER_ADMIN.value,
                password_hash="unused",
                is_active=True,
            )
            session.add(admin)
            await session.flush()
            channel_id = uuid4()
            encrypted = cipher.encrypt(
                {"url": "https://1.1.1.1/hook", "secret": "signing-secret"},
                channel_id=channel_id,
            )
            channel = NotificationChannel(
                id=channel_id,
                name="Webhook",
                type="WEBHOOK",
                is_enabled=True,
                config_ciphertext=encrypted.ciphertext,
                config_nonce=encrypted.nonce,
                key_version=encrypted.key_version,
                created_by_id=admin.id,
            )
            alert = Alert(
                type="WORKER_DOWN",
                severity="CRITICAL",
                fingerprint="delivery-alert",
                status="OPEN",
                resource_type="worker",
                message="Worker unavailable.",
                first_seen_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
            )
            session.add_all([channel, alert])
            await session.flush()
            event = AlertEvent(alert_id=alert.id, event_type="OPEN")
            session.add(event)
            await session.flush()
            delivery = NotificationDelivery(
                id=delivery_id,
                alert_event_id=event.id,
                channel_id=channel.id,
                status="PENDING",
                next_attempt_at=datetime.now(UTC),
            )
            session.add(delivery)
            await session.commit()
            service = AlertingService(
                session=session,
                settings=settings,
                cipher=cipher,
                transport=httpx.MockTransport(timeout_handler),
            )
            assert await service.deliver_due() == 0
            await session.refresh(delivery)
            assert delivery.status == "RETRY"
            assert delivery.last_error_code == "NOTIFICATION_TIMEOUT"
            delivery.next_attempt_at = datetime.now(UTC)
            await session.commit()

            def success_handler(request: httpx.Request) -> httpx.Response:
                seen_idempotency_keys.append(request.headers["Idempotency-Key"])
                return httpx.Response(204, request=request)

            service = AlertingService(
                session=session,
                settings=settings,
                cipher=cipher,
                transport=httpx.MockTransport(success_handler),
            )
            assert await service.deliver_due() == 1
            await session.refresh(delivery)
            assert delivery.status == "DELIVERED"
            assert seen_idempotency_keys == [str(delivery_id), str(delivery_id)]
    finally:
        async with factory() as session:
            await _clear(session)
        await engine.dispose()
