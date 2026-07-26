import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.operation import Operation
from app.models.scheduling import OperationOutbox, OutboxStatus

OperationPublisher = Callable[[UUID, str], None]
logger = logging.getLogger(__name__)

POWER_EVENT = "POWER_OPERATION_REQUESTED"
BACKUP_EVENT = "BACKUP_OPERATION_REQUESTED"
RESTORE_EVENT = "RESTORE_OPERATION_REQUESTED"
ADVANCED_EVENT = "ADVANCED_OPERATION_REQUESTED"


def add_operation_event(
    session: AsyncSession,
    operation: Operation,
    event_type: str,
    *,
    now: datetime | None = None,
) -> OperationOutbox:
    created_at = now or datetime.now(UTC)
    event = OperationOutbox(
        operation_id=operation.id,
        event_type=event_type,
        payload={"operation_id": str(operation.id)},
        status=OutboxStatus.PENDING.value,
        attempt_count=0,
        next_attempt_at=created_at,
    )
    session.add(event)
    return event


async def record_publish_success(
    session: AsyncSession,
    event: OperationOutbox,
    *,
    now: datetime | None = None,
) -> None:
    event.status = OutboxStatus.PUBLISHED.value
    event.published_at = now or datetime.now(UTC)
    event.last_error_code = None
    event.attempt_count += 1
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        logger.warning(
            "Could not record outbox publish success",
            extra={"operation_id": str(event.operation_id), "event_type": event.event_type},
        )


async def record_publish_failure(
    session: AsyncSession,
    event: OperationOutbox,
    settings: Settings,
    *,
    error_code: str = "BROKER_UNAVAILABLE",
    now: datetime | None = None,
) -> None:
    failed_at = now or datetime.now(UTC)
    event.status = OutboxStatus.PENDING.value
    event.published_at = None
    event.last_error_code = error_code
    event.attempt_count += 1
    delay = min(2 ** min(event.attempt_count - 1, 12), settings.outbox_max_backoff_seconds)
    event.next_attempt_at = failed_at + timedelta(seconds=delay)
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        logger.warning(
            "Could not record outbox publish failure",
            extra={"operation_id": str(event.operation_id), "event_type": event.event_type},
        )


async def dispatch_due_events(
    session: AsyncSession,
    settings: Settings,
    publishers: Mapping[str, OperationPublisher],
    *,
    now: datetime | None = None,
) -> tuple[int, int]:
    dispatched_at = now or datetime.now(UTC)
    events = (
        await session.scalars(
            select(OperationOutbox)
            .where(
                OperationOutbox.status == OutboxStatus.PENDING.value,
                OperationOutbox.next_attempt_at <= dispatched_at,
            )
            .order_by(OperationOutbox.created_at, OperationOutbox.id)
            .limit(settings.outbox_dispatch_batch_size)
            .with_for_update(skip_locked=True)
        )
    ).all()
    published = 0
    failed = 0
    for event in events:
        operation = await session.get(Operation, event.operation_id)
        publisher = publishers.get(event.event_type)
        payload_operation_id = event.payload.get("operation_id")
        if (
            operation is None
            or publisher is None
            or payload_operation_id != str(event.operation_id)
        ):
            event.last_error_code = "OUTBOX_EVENT_INVALID"
            event.attempt_count += 1
            event.next_attempt_at = dispatched_at + timedelta(
                seconds=settings.outbox_max_backoff_seconds
            )
            failed += 1
            continue
        try:
            publisher(operation.id, operation.celery_task_id)
        except Exception:
            event.last_error_code = "BROKER_UNAVAILABLE"
            event.attempt_count += 1
            delay = min(
                2 ** min(event.attempt_count - 1, 12),
                settings.outbox_max_backoff_seconds,
            )
            event.next_attempt_at = dispatched_at + timedelta(seconds=delay)
            failed += 1
        else:
            event.status = OutboxStatus.PUBLISHED.value
            event.published_at = dispatched_at
            event.last_error_code = None
            event.attempt_count += 1
            published += 1
    await session.commit()
    return published, failed
