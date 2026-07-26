import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.db import create_engine, create_session_factory
from app.models.backup import BackupRun, BackupTarget
from app.models.cluster import Cluster, ClusterCredential
from app.models.provisioning import ProvisioningRequest, ProvisioningStatus
from app.models.scheduling import RunStatus, SyncRun
from app.security.credentials import CredentialCipher
from app.security.notification_config import NotificationConfigCipher
from app.services.alerting import AlertingService
from app.services.backup_metadata import BackupMetadataReconciler
from app.services.maintenance import (
    operation_watchdog_callback,
    outbox_dispatch_callback,
    release_expired_ip_quarantine,
    retention_callback,
    run_maintenance_job,
)
from app.services.observability import ObservabilityService
from app.services.outbox import BACKUP_EVENT, POWER_EVENT, RESTORE_EVENT
from app.services.reconciliation import create_sync_run
from app.tasks.backup import enqueue_backup_operation, enqueue_restore_operation
from app.tasks.inventory import enqueue_inventory_sync
from app.tasks.power import enqueue_power_operation
from app.tasks.provisioning import enqueue_provisioning_request
from app.worker import celery_app

Callback = Callable[[AsyncSession], Awaitable[int]]


def _run(job_name: str, callback: Callback) -> int:
    return asyncio.run(_run_async(job_name, callback))


async def _run_async(job_name: str, callback: Callback) -> int:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        run = await run_maintenance_job(
            session_factory,
            settings,
            job_name=job_name,
            callback=callback,
        )
        return run.processed_count
    finally:
        await engine.dispose()


@celery_app.task(name="app.tasks.scheduler.dispatch_operation_outbox")  # type: ignore[untyped-decorator]
def dispatch_operation_outbox() -> int:
    settings = get_settings()
    return _run(
        "operation_outbox_dispatch",
        outbox_dispatch_callback(
            settings,
            {
                POWER_EVENT: enqueue_power_operation,
                BACKUP_EVENT: enqueue_backup_operation,
                RESTORE_EVENT: enqueue_restore_operation,
            },
        ),
    )


@celery_app.task(name="app.tasks.scheduler.watchdog_operations")  # type: ignore[untyped-decorator]
def watchdog_operations() -> int:
    return _run(
        "operation_watchdog",
        operation_watchdog_callback(get_settings()),
    )


@celery_app.task(name="app.tasks.scheduler.watchdog_provisioning")  # type: ignore[untyped-decorator]
def watchdog_provisioning() -> int:
    return _run("provisioning_watchdog", _watchdog_provisioning)


async def _watchdog_provisioning(session: AsyncSession) -> int:
    now = datetime.now(UTC)
    requests = (
        await session.scalars(
            select(ProvisioningRequest).where(
                or_(
                    ProvisioningRequest.status == ProvisioningStatus.QUEUED.value,
                    and_(
                        ProvisioningRequest.status == ProvisioningStatus.RUNNING.value,
                        or_(
                            ProvisioningRequest.runner_id.is_(None),
                            ProvisioningRequest.lease_expires_at.is_(None),
                            ProvisioningRequest.lease_expires_at <= now,
                        ),
                    ),
                )
            )
        )
    ).all()
    for request in requests:
        enqueue_provisioning_request(request.id, request.celery_task_id)
    return len(requests)


@celery_app.task(name="app.tasks.scheduler.dispatch_inventory_sync")  # type: ignore[untyped-decorator]
def dispatch_inventory_sync() -> int:
    return _run("inventory_sync_dispatch", _dispatch_inventory_sync)


async def _dispatch_inventory_sync(session: AsyncSession) -> int:
    now = datetime.now(UTC)
    queued_ids = (
        await session.scalars(select(SyncRun.id).where(SyncRun.status == RunStatus.QUEUED.value))
    ).all()
    for run_id in queued_ids:
        enqueue_inventory_sync(run_id)
    clusters = (await session.scalars(select(Cluster).where(Cluster.is_active.is_(True)))).all()
    dispatched = len(queued_ids)
    for cluster in clusters:
        due = (
            cluster.last_sync_succeeded_at is None
            or cluster.last_sync_succeeded_at
            <= now - timedelta(seconds=cluster.sync_interval_seconds)
        )
        if not due:
            continue
        run, created = await create_sync_run(
            session,
            cluster_id=cluster.id,
            triggered_by="scheduler",
        )
        if created and run.id not in queued_ids:
            enqueue_inventory_sync(run.id)
            dispatched += 1
    return dispatched


@celery_app.task(name="app.tasks.scheduler.release_ip_quarantine")  # type: ignore[untyped-decorator]
def release_ip_quarantine() -> int:
    return _run("ip_quarantine_release", release_expired_ip_quarantine)


@celery_app.task(name="app.tasks.scheduler.run_retention")  # type: ignore[untyped-decorator]
def run_retention() -> int:
    return _run("data_retention", retention_callback(get_settings()))


@celery_app.task(name="app.tasks.scheduler.check_control_plane_state")  # type: ignore[untyped-decorator]
def check_control_plane_state() -> int:
    return _run("control_plane_state_check", _check_control_plane_state)


@celery_app.task(name="app.tasks.scheduler.reconcile_backup_metadata")  # type: ignore[untyped-decorator]
def reconcile_backup_metadata() -> int:
    settings = get_settings()

    async def callback(session: AsyncSession) -> int:
        return await BackupMetadataReconciler(
            session=session,
            settings=settings,
            cipher=CredentialCipher(settings.app_secret_key.get_secret_value()),
        ).reconcile()

    return _run("backup_metadata_reconciliation", callback)


async def _check_control_plane_state(session: AsyncSession) -> int:
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url.get_secret_value())
    try:
        current_alerts = await ObservabilityService(
            session=session,
            redis=redis,
            settings=settings,
        ).evaluate_alerts()
    finally:
        await redis.aclose()
    alerting = AlertingService(
        session=session,
        settings=settings,
        cipher=NotificationConfigCipher(settings.app_secret_key.get_secret_value()),
    )
    changed = await alerting.sync(current_alerts)
    await alerting.deliver_due()
    stale_before = datetime.now(UTC) - timedelta(minutes=5)
    missing_credentials = await session.scalar(
        select(func.count())
        .select_from(Cluster)
        .outerjoin(
            ClusterCredential,
            (ClusterCredential.cluster_id == Cluster.id) & ClusterCredential.is_active.is_(True),
        )
        .where(Cluster.is_active.is_(True), ClusterCredential.id.is_(None))
    )
    stale_targets = await session.scalar(
        select(func.count())
        .select_from(BackupTarget)
        .where(
            BackupTarget.is_enabled.is_(True),
            (
                BackupTarget.last_checked_at.is_(None)
                | (BackupTarget.last_checked_at < stale_before)
            ),
        )
    )
    missing_backup_metadata = await session.scalar(
        select(func.count())
        .select_from(BackupRun)
        .where(
            BackupRun.status == "SUCCEEDED",
            BackupRun.snapshot_volume_id.is_(None),
        )
    )
    latest_success = (
        select(
            SyncRun.cluster_id,
            func.max(SyncRun.finished_at).label("last_success_at"),
        )
        .where(SyncRun.status == RunStatus.SUCCEEDED.value)
        .group_by(SyncRun.cluster_id)
        .subquery()
    )
    stale_syncs = await session.scalar(
        select(func.count())
        .select_from(Cluster)
        .outerjoin(latest_success, latest_success.c.cluster_id == Cluster.id)
        .where(
            Cluster.is_active.is_(True),
            (
                latest_success.c.last_success_at.is_(None)
                | (latest_success.c.last_success_at < stale_before)
            ),
        )
    )
    issues = (
        int(missing_credentials or 0)
        + int(stale_targets or 0)
        + int(missing_backup_metadata or 0)
        + int(stale_syncs or 0)
    )
    if issues:
        raise AppError(
            503,
            "CONTROL_PLANE_STATE_DEGRADED",
            "Scheduled control-plane checks found degraded state.",
        )
    return changed
