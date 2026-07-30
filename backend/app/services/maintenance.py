from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import delete, or_, select, text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models.auth import AuditLog, RefreshToken
from app.models.ipam import IpAddress, IpAddressState, IpAllocation, IpAllocationStatus
from app.models.operation import Operation, OperationEvent, OperationStatus
from app.models.scheduling import (
    MaintenanceRun,
    OperationOutbox,
    OutboxStatus,
    RunStatus,
    SchedulerLease,
    SyncRun,
)
from app.services.outbox import (
    ADVANCED_EVENT,
    BACKUP_EVENT,
    POWER_EVENT,
    RESTORE_EVENT,
    OperationPublisher,
    add_operation_event,
    dispatch_due_events,
)

MaintenanceCallback = Callable[[AsyncSession], Awaitable[int]]


@dataclass(frozen=True)
class LeaseGrant:
    name: str
    owner_id: UUID
    fencing_token: int
    expires_at: datetime


class LeaseFencedError(RuntimeError):
    code = "LEASE_FENCED"


async def acquire_lease(
    session: AsyncSession,
    *,
    name: str,
    owner_id: UUID,
    ttl_seconds: int,
    now: datetime | None = None,
) -> LeaseGrant | None:
    acquired_at = now or datetime.now(UTC)
    advisory = await session.scalar(
        text("SELECT pg_try_advisory_xact_lock(hashtextextended(:name, 0))").bindparams(name=name)
    )
    if not advisory:
        await session.rollback()
        return None
    lease = await session.get(SchedulerLease, name, with_for_update=True)
    if lease is not None and lease.lease_expires_at > acquired_at and lease.owner_id != owner_id:
        await session.rollback()
        return None
    expires_at = acquired_at + timedelta(seconds=ttl_seconds)
    if lease is None:
        lease = SchedulerLease(
            name=name,
            owner_id=owner_id,
            fencing_token=1,
            acquired_at=acquired_at,
            lease_expires_at=expires_at,
        )
        session.add(lease)
    else:
        lease.owner_id = owner_id
        lease.fencing_token += 1
        lease.acquired_at = acquired_at
        lease.lease_expires_at = expires_at
    await session.commit()
    return LeaseGrant(name, owner_id, lease.fencing_token, expires_at)


async def release_lease(
    session: AsyncSession,
    grant: LeaseGrant,
    *,
    now: datetime | None = None,
) -> None:
    released_at = now or datetime.now(UTC)
    await session.execute(
        update(SchedulerLease)
        .where(
            SchedulerLease.name == grant.name,
            SchedulerLease.owner_id == grant.owner_id,
            SchedulerLease.fencing_token == grant.fencing_token,
        )
        .values(lease_expires_at=released_at)
    )
    await session.commit()


async def require_current_lease(
    session: AsyncSession,
    grant: LeaseGrant,
    *,
    now: datetime | None = None,
) -> None:
    checked_at = now or datetime.now(UTC)
    current = await session.scalar(
        select(SchedulerLease.name).where(
            SchedulerLease.name == grant.name,
            SchedulerLease.owner_id == grant.owner_id,
            SchedulerLease.fencing_token == grant.fencing_token,
            SchedulerLease.lease_expires_at > checked_at,
        )
    )
    if current is None:
        raise LeaseFencedError("scheduler lease ownership changed")


async def run_maintenance_job(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    job_name: str,
    callback: MaintenanceCallback,
    owner_id: UUID | None = None,
) -> MaintenanceRun:
    owner = owner_id or uuid4()
    started_at = datetime.now(UTC)
    async with session_factory() as session:
        grant = await acquire_lease(
            session,
            name=job_name,
            owner_id=owner,
            ttl_seconds=settings.scheduler_lease_seconds,
            now=started_at,
        )
        run = MaintenanceRun(
            job_name=job_name,
            status=RunStatus.RUNNING.value if grant is not None else RunStatus.SKIPPED.value,
            owner_id=owner,
            fencing_token=grant.fencing_token if grant is not None else None,
            started_at=started_at,
            finished_at=started_at if grant is None else None,
            processed_count=0,
            error_code="LEASE_HELD" if grant is None else None,
        )
        session.add(run)
        await session.commit()
        if grant is None:
            return run
        try:
            run.processed_count = await callback(session)
        except Exception as exc:
            await session.rollback()
            run = await session.get(MaintenanceRun, run.id, with_for_update=True) or run
            run.status = RunStatus.FAILED.value
            run.error_code = _safe_error_code(exc)
            run.finished_at = datetime.now(UTC)
            await session.commit()
        else:
            run.status = RunStatus.SUCCEEDED.value
            run.finished_at = datetime.now(UTC)
            await session.commit()
        finally:
            await release_lease(session, grant)
        return run


def outbox_dispatch_callback(
    settings: Settings,
    publishers: dict[str, OperationPublisher],
) -> MaintenanceCallback:
    async def callback(session: AsyncSession) -> int:
        published, failed = await dispatch_due_events(session, settings, publishers)
        if failed:
            raise RuntimeError("OUTBOX_PUBLISH_FAILED")
        return published

    return callback


def operation_watchdog_callback(settings: Settings) -> MaintenanceCallback:
    async def callback(session: AsyncSession) -> int:
        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=settings.operation_watchdog_seconds)
        operations = (
            await session.scalars(
                select(Operation)
                .where(
                    or_(
                        (
                            (Operation.status == OperationStatus.QUEUED.value)
                            & (Operation.requested_at <= cutoff)
                        ),
                        (
                            (Operation.status == OperationStatus.RUNNING.value)
                            & or_(
                                Operation.heartbeat_at.is_(None),
                                Operation.heartbeat_at <= cutoff,
                            )
                        ),
                    )
                )
                .with_for_update(skip_locked=True)
            )
        ).all()
        for operation in operations:
            already_detected = await session.scalar(
                select(OperationEvent.id).where(
                    OperationEvent.operation_id == operation.id,
                    OperationEvent.event_type == "STUCK_DETECTED",
                )
            )
            if already_detected is None:
                session.add(
                    OperationEvent(
                        operation_id=operation.id,
                        event_type="STUCK_DETECTED",
                        status=operation.status,
                        message="Worker heartbeat expired; work was queued for safe redelivery",
                        details={"watchdog_redelivery": True},
                        occurred_at=now,
                    )
                )
            event_type = _operation_event_type(operation)
            event = await session.scalar(
                select(OperationOutbox)
                .where(
                    OperationOutbox.operation_id == operation.id,
                    OperationOutbox.event_type == event_type,
                )
                .with_for_update()
            )
            if event is None:
                add_operation_event(session, operation, event_type, now=now)
            else:
                event.status = OutboxStatus.PENDING.value
                event.published_at = None
                event.next_attempt_at = now
                event.last_error_code = "WATCHDOG_REDELIVERY"
        await session.commit()
        return len(operations)

    return callback


async def release_expired_ip_quarantine(session: AsyncSession) -> int:
    now = datetime.now(UTC)
    addresses = (
        await session.scalars(
            select(IpAddress)
            .where(
                IpAddress.state == IpAddressState.QUARANTINED.value,
                IpAddress.quarantined_until.is_not(None),
                IpAddress.quarantined_until <= now,
            )
            .with_for_update(skip_locked=True)
        )
    ).all()
    for address in addresses:
        allocation = await session.scalar(
            select(IpAllocation)
            .where(
                IpAllocation.ip_address_id == address.id,
                IpAllocation.status == IpAllocationStatus.QUARANTINED.value,
            )
            .with_for_update()
        )
        if allocation is not None:
            allocation.status = IpAllocationStatus.RELEASED.value
            allocation.released_at = allocation.released_at or now
            allocation.version += 1
        address.state = IpAddressState.AVAILABLE.value
        address.quarantined_until = None
        address.reserved_for = None
        address.version += 1
    await session.commit()
    return len(addresses)


def retention_callback(settings: Settings) -> MaintenanceCallback:
    async def callback(session: AsyncSession) -> int:
        now = datetime.now(UTC)
        await session.execute(text("SET LOCAL app.audit_retention = 'on'"))
        audit_result = await session.execute(
            delete(AuditLog).where(
                AuditLog.created_at < now - timedelta(days=settings.audit_retention_days)
            )
        )
        refresh_result = await session.execute(
            delete(RefreshToken).where(
                RefreshToken.expires_at
                < now - timedelta(days=settings.refresh_token_retention_days)
            )
        )
        outbox_result = await session.execute(
            delete(OperationOutbox).where(
                OperationOutbox.status == OutboxStatus.PUBLISHED.value,
                OperationOutbox.published_at
                < now - timedelta(days=settings.completed_run_retention_days),
            )
        )
        sync_result = await session.execute(
            delete(SyncRun).where(
                SyncRun.finished_at < now - timedelta(days=settings.sync_run_retention_days)
            )
        )
        run_result = await session.execute(
            delete(MaintenanceRun).where(
                MaintenanceRun.status.in_([RunStatus.SUCCEEDED.value, RunStatus.SKIPPED.value]),
                MaintenanceRun.finished_at
                < now - timedelta(days=settings.completed_run_retention_days),
            )
        )
        await session.commit()
        processed = 0
        for result in (
            audit_result,
            refresh_result,
            outbox_result,
            sync_result,
            run_result,
        ):
            processed += max(0, cast(CursorResult[object], result).rowcount or 0)
        return processed

    return callback


def _operation_event_type(operation: Operation) -> str:
    if operation.operation_type.startswith("ADVANCED_"):
        return ADVANCED_EVENT
    if operation.operation_type == "WORKLOAD_BACKUP":
        return BACKUP_EVENT
    if operation.operation_type == "WORKLOAD_RESTORE":
        return RESTORE_EVENT
    return POWER_EVENT


def _safe_error_code(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code[:64]
    message = str(exc)
    if message in {"OUTBOX_PUBLISH_FAILED"}:
        return message
    return "MAINTENANCE_FAILED"
