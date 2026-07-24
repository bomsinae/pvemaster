from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.operation import Operation
from app.models.scheduling import OperationOutbox, OutboxStatus
from app.services.outbox import (
    POWER_EVENT,
    add_operation_event,
    record_publish_failure,
    record_publish_success,
)


def test_worker_routes_and_periodic_schedule_are_split_by_purpose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user@localhost/test")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret-key-that-is-at-least-32-bytes")
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.worker import celery_app

    routes = celery_app.conf.task_routes
    assert routes["app.tasks.power.*"]["queue"] == "operations"
    assert routes["app.tasks.inventory.*"]["queue"] == "inventory"
    assert routes["app.tasks.scheduler.*"]["queue"] == "maintenance"
    assert {item["task"] for item in celery_app.conf.beat_schedule.values()} >= {
        "app.tasks.scheduler.dispatch_operation_outbox",
        "app.tasks.scheduler.watchdog_operations",
        "app.tasks.scheduler.dispatch_inventory_sync",
        "app.tasks.scheduler.release_ip_quarantine",
        "app.tasks.scheduler.run_retention",
    }
    get_settings.cache_clear()


def test_outbox_payload_contains_only_internal_operation_id() -> None:
    session = MagicMock(spec=AsyncSession)
    operation = Operation(id=uuid4())

    event = add_operation_event(cast(AsyncSession, session), operation, POWER_EVENT)

    assert event.payload == {"operation_id": str(operation.id)}
    assert event.status == OutboxStatus.PENDING.value
    session.add.assert_called_once_with(event)


async def test_immediate_publish_updates_only_safe_outbox_state(settings: Settings) -> None:
    session = AsyncMock(spec=AsyncSession)
    event = OperationOutbox(
        operation_id=uuid4(),
        event_type=POWER_EVENT,
        payload={"operation_id": str(uuid4())},
        status=OutboxStatus.PENDING.value,
        attempt_count=0,
        next_attempt_at=datetime.now(UTC),
    )

    await record_publish_success(cast(AsyncSession, session), event)

    assert event.status == OutboxStatus.PUBLISHED.value
    assert event.published_at is not None
    assert event.last_error_code is None
    session.commit.assert_awaited_once()

    event.status = OutboxStatus.PENDING.value
    await record_publish_failure(
        cast(AsyncSession, session),
        event,
        settings,
    )
    assert event.status == OutboxStatus.PENDING.value
    assert event.last_error_code == "BROKER_UNAVAILABLE"
